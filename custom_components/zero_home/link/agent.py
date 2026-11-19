"""The agent: one outbound WebSocket to the Zero Home relay (PROTOCOL.md §2).

    agent = Agent(AgentConfig(...), relay_session=..., local=LocalHA(...),
                  rpc=dispatcher, state=lambda: {...}, on_event=callback)
    await agent.run()        # returns when stopped (bye revoked/plan, 401/402, stop())

``state()`` returns the hello fields (§2 handshake): ``ha_version``,
``instance_id``, ``remote_ui``, ``ui_ready``, ``exposed``, ``location_name``.
Call ``notify_state()`` whenever one of ``remote_ui``/``ui_ready``/``exposed``
may have changed; a ``state`` message is sent only when something did.

``on_event(name, info)`` is called (synchronously, in the loop) with:
``connecting``, ``connected`` (the welcome), ``disconnected``
(reason/error/retry_in) and ``stopped`` (reason: revoked | plan | stopped).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

from . import frames as F
from .proxy import BadPath, LocalHA, Refused
from .rpc import CODE_HA_ERROR, CODE_TIMEOUT, RpcError

_LOG = logging.getLogger(__name__)

STATE_KEYS = ("remote_ui", "ui_ready", "exposed")


class _RelayGone(Exception):
    """The relay connection is closed; nothing more can be sent."""


@dataclass
class AgentConfig:
    relay_ws: str
    home_id: str
    agent_token: str
    agent_version: str = "1.0.0"
    ping_interval: float = F.PING_INTERVAL
    dead_after: float = F.DEAD_AFTER
    rpc_timeout: float = F.RPC_TIMEOUT
    backoff: Callable[[int], float] = field(default=F.backoff_delay)


class _HttpStream:
    __slots__ = ("sid", "body", "task")

    def __init__(self, sid: int) -> None:
        self.sid = sid
        self.body: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.task: asyncio.Task | None = None


class _WsStream:
    __slots__ = ("sid", "out", "task", "closed_by_relay")

    def __init__(self, sid: int) -> None:
        self.sid = sid
        self.out: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.closed_by_relay = False


def _close_code(code: Any) -> int:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return 1000
    if code in (1005, 1006, 1015) or not 1000 <= code <= 4999:
        return 1000
    return code


class Agent:
    def __init__(self, cfg: AgentConfig, *, relay_session: aiohttp.ClientSession,
                 local: LocalHA, rpc: Callable[[str, Any], Awaitable[Any]],
                 state: Callable[[], dict],
                 on_event: Callable[[str, dict], None] | None = None) -> None:
        self.cfg = cfg
        self.relay_session = relay_session
        self.local = local
        self.rpc = rpc
        self.state = state
        self.on_event = on_event or (lambda name, info: None)

        self.connected = False
        self.welcome: dict | None = None
        self.last_bye: str | None = None
        self.last_error: str | None = None
        self.stop_reason: str | None = None

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._streams: dict[int, _HttpStream | _WsStream] = {}
        self._rpcs: dict[int, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._sent_state: dict = {}
        self._stopping = False
        self._stop_evt = asyncio.Event()

    # ------------------------------------------------------------------ public
    async def run(self) -> str:
        """Connect, serve, reconnect with backoff; returns the stop reason."""
        attempt = 0
        while not self._stopping:
            reason: str | None = None
            error: str | None = None
            welcomed = False
            self._emit("connecting", {"attempt": attempt})
            try:
                reason, welcomed = await self._session_once()
            except aiohttp.WSServerHandshakeError as err:
                if err.status == 401:
                    reason = "revoked"
                elif err.status == 402:
                    reason = "plan"
                error = f"relay answered {err.status}"
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as err:
                error = f"{type(err).__name__}: {err}" if str(err) else type(err).__name__
            except asyncio.CancelledError:
                self._stopping = True
                self.stop_reason = self.stop_reason or "stopped"
                raise
            except Exception as err:  # noqa: BLE001 - keep the agent alive
                _LOG.exception("Zero Home agent: unexpected error")
                error = f"{type(err).__name__}: {err}"
            self.last_error = error
            if self._stopping:
                break
            if reason in F.FINAL_BYE:
                self.stop_reason = reason
                self._emit("stopped", {"reason": reason, "error": error})
                return reason
            if welcomed:
                attempt = 0
            delay = max(0.0, float(self.cfg.backoff(attempt)))
            attempt += 1
            self._emit("disconnected", {"reason": reason, "error": error, "retry_in": delay})
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop_evt.wait(), delay)
        self.stop_reason = self.stop_reason or "stopped"
        self._emit("stopped", {"reason": self.stop_reason, "error": None})
        return self.stop_reason

    async def stop(self) -> None:
        self._stopping = True
        self.stop_reason = self.stop_reason or "stopped"
        self._stop_evt.set()
        ws = self._ws
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()

    def notify_state(self) -> None:
        """Send a ``state`` message if remote_ui/ui_ready/exposed changed."""
        if not self.connected:
            return
        cur = self._state_now()
        if cur != self._sent_state:
            self._sent_state = cur
            self._spawn(self._send_quiet({"t": "state", **cur}))

    # ------------------------------------------------------------ connection
    def _state_now(self) -> dict:
        st = self.state() or {}
        return {"remote_ui": bool(st.get("remote_ui", True)),
                "ui_ready": bool(st.get("ui_ready", False)),
                "exposed": int(st.get("exposed") or 0)}

    async def _session_once(self) -> tuple[str | None, bool]:
        headers = {
            "Authorization": f"Bearer {self.cfg.home_id}.{self.cfg.agent_token}",
            "X-Zero-Agent": f"zero_home/{self.cfg.agent_version}",
        }
        ws = await self.relay_session.ws_connect(
            self.cfg.relay_ws, headers=headers, autoping=False, heartbeat=None,
            max_msg_size=0, timeout=aiohttp.ClientWSTimeout(ws_close=5.0)
            if hasattr(aiohttp, "ClientWSTimeout") else 5.0)
        self._ws = ws
        self.last_bye = None
        welcomed = False
        pinger = asyncio.create_task(self._pinger(ws))
        try:
            st = self.state() or {}
            hello = {"t": "hello", "agent_version": self.cfg.agent_version,
                     "ha_version": st.get("ha_version"), "instance_id": st.get("instance_id"),
                     **self._state_now(), "location_name": st.get("location_name")}
            self._sent_state = {k: hello[k] for k in STATE_KEYS}
            await self._send_text(F.dumps(hello))
            while True:
                if self._stopping:
                    break
                try:
                    msg = await ws.receive(timeout=self.cfg.dead_after)
                except asyncio.TimeoutError:
                    self.last_error = "no traffic from the relay"
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        ctl = F.loads(msg.data)
                    except ValueError:
                        continue
                    if ctl["t"] == "welcome":
                        welcomed = True
                        self.welcome = ctl
                        self.connected = True
                        self._emit("connected", ctl)
                        # anything that changed while connecting
                        self.notify_state()
                    else:
                        self._on_control(ctl)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    self._on_binary(msg.data)
                elif msg.type == aiohttp.WSMsgType.PING:
                    with contextlib.suppress(Exception):
                        await ws.pong(msg.data)
                elif msg.type == aiohttp.WSMsgType.PONG:
                    pass
                else:  # CLOSE / CLOSING / CLOSED / ERROR
                    break
        finally:
            self.connected = False
            pinger.cancel()
            self._ws = None
            await self._abort_all()
            with contextlib.suppress(Exception):
                await ws.close()
        return self.last_bye, welcomed

    async def _pinger(self, ws) -> None:
        with contextlib.suppress(Exception):
            while not ws.closed:
                await asyncio.sleep(self.cfg.ping_interval)
                await ws.ping()

    async def _abort_all(self) -> None:
        tasks = [s.task for s in self._streams.values() if s.task] + list(self._rpcs.values())
        tasks += list(self._tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
        self._streams.clear()
        self._rpcs.clear()
        self._tasks.clear()

    def _emit(self, name: str, info: dict) -> None:
        try:
            self.on_event(name, info)
        except Exception:  # noqa: BLE001
            _LOG.exception("Zero Home agent: event handler failed")

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    # ---------------------------------------------------------------- sending
    async def _send_text(self, text: str) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise _RelayGone()
        async with self._send_lock:
            try:
                await ws.send_str(text)
            except (ConnectionError, RuntimeError, aiohttp.ClientError) as err:
                raise _RelayGone() from err

    async def _send(self, msg: dict) -> None:
        await self._send_text(F.dumps(msg))

    async def _send_quiet(self, msg: dict) -> None:
        with contextlib.suppress(_RelayGone):
            await self._send(msg)

    async def _send_frame(self, sid: int, kind: int, payload: bytes = b"") -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise _RelayGone()
        async with self._send_lock:
            try:
                await ws.send_bytes(F.pack(sid, kind, payload))
            except (ConnectionError, RuntimeError, aiohttp.ClientError) as err:
                raise _RelayGone() from err

    # --------------------------------------------------------------- receiving
    def _on_control(self, ctl: dict) -> None:
        t = ctl["t"]
        sid = ctl.get("id")
        if t == "bye":
            self.last_bye = str(ctl.get("reason") or "")
            return
        if t in ("req", "ws_open", "rpc", "cancel", "ws_msg", "ws_close") and not isinstance(sid, int):
            return
        if t == "req":
            self._drop_stream(sid)
            st = _HttpStream(sid)
            self._streams[sid] = st
            if not ctl.get("body"):
                st.body.put_nowait(None)
            st.task = asyncio.create_task(self._http(st, ctl))
        elif t == "ws_open":
            self._drop_stream(sid)
            ws = _WsStream(sid)
            self._streams[sid] = ws
            ws.task = asyncio.create_task(self._websocket(ws, ctl))
        elif t == "ws_msg":
            st = self._streams.get(sid)
            if isinstance(st, _WsStream):
                st.out.put_nowait(("text", str(ctl.get("data", ""))))
        elif t == "ws_close":
            st = self._streams.get(sid)
            if isinstance(st, _WsStream):
                st.closed_by_relay = True
                st.out.put_nowait(("close", _close_code(ctl.get("code", 1000))))
        elif t == "cancel":
            self._drop_stream(sid)
            task = self._rpcs.pop(sid, None)
            if task:
                task.cancel()
        elif t == "rpc":
            old = self._rpcs.pop(sid, None)
            if old:
                old.cancel()
            self._rpcs[sid] = asyncio.create_task(self._rpc(sid, ctl))
        # welcome handled by the loop; unknown types are ignored (forward compatible)

    def _on_binary(self, data: bytes) -> None:
        try:
            sid, kind, payload = F.unpack(data)
        except ValueError:
            return
        st = self._streams.get(sid)
        if st is None:
            return
        if isinstance(st, _HttpStream):
            if kind == F.KIND_BODY:
                if payload:
                    st.body.put_nowait(payload)
            elif kind == F.KIND_END:
                st.body.put_nowait(None)
        elif kind == F.KIND_WS_BINARY:
            st.out.put_nowait(("binary", payload))

    def _drop_stream(self, sid: int) -> None:
        st = self._streams.pop(sid, None)
        if st is not None and st.task is not None:
            st.task.cancel()

    def _remote_ui(self) -> bool:
        return bool((self.state() or {}).get("remote_ui", True))

    # ------------------------------------------------------------- HTTP stream
    async def _http(self, st: _HttpStream, ctl: dict) -> None:
        sid = st.sid
        resp = None
        sent_res = False
        clean = False

        async def body():
            while True:
                chunk = await st.body.get()
                if chunk is None:
                    return
                yield chunk

        try:
            if not self._remote_ui():
                await self._send({"t": "res", "id": sid, "status": 403, "headers": [],
                                  "error": "remote_ui is off"})
                await self._send_frame(sid, F.KIND_END)
                clean = True
                return
            has_body = bool(ctl.get("body"))
            try:
                resp = await self.local.request(
                    str(ctl.get("method") or "GET"), str(ctl.get("path") or "/"),
                    ctl.get("headers") or [], str(ctl.get("client_ip") or ""),
                    body() if has_body else None)
            except BadPath as err:
                await self._send({"t": "res", "id": sid, "status": 400, "headers": [], "error": str(err)})
                await self._send_frame(sid, F.KIND_END)
                clean = True
                return
            except Refused as err:
                await self._send({"t": "res", "id": sid, "status": err.status, "headers": [], "error": str(err)})
                await self._send_frame(sid, F.KIND_END)
                clean = True
                return
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError) as err:
                await self._send({"t": "res", "id": sid, "status": 502, "headers": [],
                                  "error": f"Home Assistant: {type(err).__name__}"})
                await self._send_frame(sid, F.KIND_END)
                clean = True
                return
            await self._send({"t": "res", "id": sid, "status": resp.status,
                              "headers": self.local.response_headers(resp)})
            sent_res = True
            while True:
                chunk = await resp.content.read(F.CHUNK_MAX)
                if not chunk:
                    break
                await self._send_frame(sid, F.KIND_BODY, chunk)
            await self._send_frame(sid, F.KIND_END)
            clean = True
        except _RelayGone:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - the local answer broke mid-way
            _LOG.debug("Zero Home stream %s failed: %s", sid, err)
            if sent_res:
                await self._send_quiet({"t": "cancel", "id": sid})
            else:
                with contextlib.suppress(_RelayGone):
                    await self._send({"t": "res", "id": sid, "status": 502, "headers": [],
                                      "error": f"{type(err).__name__}"})
                    await self._send_frame(sid, F.KIND_END)
        finally:
            if resp is not None:
                if clean:
                    resp.release()
                else:
                    resp.close()
            if self._streams.get(sid) is st:
                del self._streams[sid]

    # -------------------------------------------------------- WebSocket stream
    async def _websocket(self, st: _WsStream, ctl: dict) -> None:
        sid = st.sid
        lws = None
        writer = None
        try:
            if not self._remote_ui():
                await self._send({"t": "ws_fail", "id": sid, "status": 403})
                return
            try:
                lws = await self.local.websocket(
                    str(ctl.get("path") or "/api/websocket"), ctl.get("headers") or [],
                    str(ctl.get("client_ip") or ""), ctl.get("protocols") or [])
            except aiohttp.WSServerHandshakeError as err:
                await self._send({"t": "ws_fail", "id": sid, "status": err.status or 502})
                return
            except BadPath:
                await self._send({"t": "ws_fail", "id": sid, "status": 400})
                return
            except Refused as err:
                await self._send({"t": "ws_fail", "id": sid, "status": err.status})
                return
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError):
                await self._send({"t": "ws_fail", "id": sid, "status": 502})
                return
            await self._send({"t": "ws_ok", "id": sid, "protocol": lws.protocol})
            writer = asyncio.create_task(self._ws_writer(st, lws))
            async for msg in lws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._send({"t": "ws_msg", "id": sid, "data": msg.data})
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await self._send_frame(sid, F.KIND_WS_BINARY, msg.data)
                else:
                    break
            if not st.closed_by_relay:
                await self._send({"t": "ws_close", "id": sid, "code": _close_code(lws.close_code or 1000)})
        except _RelayGone:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOG.debug("Zero Home ws stream %s failed: %s", sid, err)
            if not st.closed_by_relay:
                await self._send_quiet({"t": "ws_close", "id": sid, "code": 1011})
        finally:
            if writer is not None:
                writer.cancel()
            if lws is not None and not lws.closed:
                with contextlib.suppress(Exception):
                    await lws.close()
            if self._streams.get(sid) is st:
                del self._streams[sid]

    async def _ws_writer(self, st: _WsStream, lws) -> None:
        while True:
            kind, data = await st.out.get()
            if kind == "text":
                await lws.send_str(data)
            elif kind == "binary":
                await lws.send_bytes(data)
            else:
                await lws.close(code=data)
                return

    # ------------------------------------------------------------------ RPC
    async def _rpc(self, sid: int, ctl: dict) -> None:
        try:
            try:
                result = await asyncio.wait_for(
                    self.rpc(str(ctl.get("method") or ""), ctl.get("params")),
                    self.cfg.rpc_timeout)
                reply = {"t": "rpc_res", "id": sid, "ok": True, "result": result}
            except RpcError as err:
                reply = {"t": "rpc_res", "id": sid, "ok": False, "error": err.message, "code": err.code}
            except asyncio.TimeoutError:
                reply = {"t": "rpc_res", "id": sid, "ok": False,
                         "error": "Home Assistant did not answer in time", "code": CODE_TIMEOUT}
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                reply = {"t": "rpc_res", "id": sid, "ok": False,
                         "error": str(err) or type(err).__name__, "code": CODE_HA_ERROR}
            await self._send_quiet(reply)
        finally:
            if self._rpcs.get(sid) is asyncio.current_task():
                del self._rpcs[sid]
