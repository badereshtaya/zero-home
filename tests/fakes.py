"""A fake Zero Home relay (PROTOCOL.md §2) and a fake local Home Assistant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time

import aiohttp
from aiohttp import web

from link import frames as F

BIG_PATTERN = bytes(range(256))


def pattern(n: int) -> bytes:
    reps = n // len(BIG_PATTERN) + 1
    return (BIG_PATTERN * reps)[:n]


# ---------------------------------------------------------------- fake HA
class FakeHA:
    """REST-ish routes, /api/websocket echo, a framed page, a slow stream.

    ``strict = True`` behaves like Home Assistant without ``use_x_forwarded_for``:
    any request carrying X-Forwarded-For gets 400.
    """

    def __init__(self) -> None:
        self.strict = False
        self.slow_ended = asyncio.Event()
        self.seen: list[dict] = []
        self.runner: web.AppRunner | None = None
        self.port = 0

    @web.middleware
    async def _forwarded(self, request: web.Request, handler):
        if self.strict and "X-Forwarded-For" in request.headers:
            return web.Response(status=400, text="reverse proxy not configured")
        return await handler(request)

    async def start(self) -> None:
        app = web.Application(middlewares=[self._forwarded], client_max_size=200 * 1024 * 1024)
        app.router.add_route("*", "/api/echo", self.echo)
        app.router.add_get("/api/big", self.big)
        app.router.add_get("/framed", self.framed)
        app.router.add_get("/api/slow", self.slow)
        app.router.add_get("/api/websocket", self.websocket)
        app.router.add_get("/redirect", self.redirect)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def echo(self, request: web.Request) -> web.Response:
        h = hashlib.sha256()
        n = 0
        async for chunk in request.content.iter_any():
            h.update(chunk)
            n += len(chunk)
        info = {"method": request.method, "path_qs": request.path_qs, "len": n, "sha256": h.hexdigest(),
                "xff": request.headers.getall("X-Forwarded-For", []),
                "xfp": request.headers.get("X-Forwarded-Proto"),
                "xfh": request.headers.get("X-Forwarded-Host"),
                "host": request.headers.get("Host"),
                "auth": request.headers.get("Authorization"),
                "connection": request.headers.get("Connection"),
                "x_custom": request.headers.get("X-Custom")}
        self.seen.append(info)
        return web.json_response(info)

    async def big(self, request: web.Request) -> web.StreamResponse:
        n = int(request.query.get("n", "5242880"))
        resp = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
        await resp.prepare(request)
        data = pattern(n)
        for i in range(0, n, 100_000):
            await resp.write(data[i:i + 100_000])
        await resp.write_eof()
        return resp

    async def framed(self, request: web.Request) -> web.Response:
        resp = web.Response(text="<html>hi</html>", content_type="text/html")
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        resp.headers.add("Set-Cookie", "a=1; Path=/")
        resp.headers.add("Set-Cookie", "b=2; Path=/")
        return resp

    async def redirect(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/lovelace")

    async def slow(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse()
        await resp.prepare(request)
        try:
            for _ in range(600):
                await resp.write(b"x" * 1000)
                await asyncio.sleep(0.05)
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            pass
        finally:
            self.slow_ended.set()
        return resp

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(protocols=("zh-test",), max_msg_size=0)
        await ws.prepare(request)
        self.seen.append({"ws_xff": request.headers.getall("X-Forwarded-For", [])})
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                if msg.data == "close":
                    await ws.close(code=4001)
                    break
                await ws.send_str("echo:" + msg.data)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                await ws.send_bytes(bytes(reversed(msg.data)))
        return ws


# ---------------------------------------------------------------- fake relay
class FakeRelay:
    """Speaks PROTOCOL §2 to one agent at a time."""

    def __init__(self, home_id: str = "h_test", token: str = "a" * 64) -> None:
        self.expected = f"Bearer {home_id}.{token}"
        self.reject_status: int | None = None
        self.attempts: list[float] = []
        self.hellos: list[dict] = []
        self.agent_headers: list[dict] = []
        self.ws: web.WebSocketResponse | None = None
        self.connected = asyncio.Event()
        self.disconnected = asyncio.Event()
        self.control: asyncio.Queue = asyncio.Queue()        # messages without a stream id
        self._streams: dict[int, asyncio.Queue] = {}
        self._next = -1
        self.runner: web.AppRunner | None = None
        self.port = 0
        self.welcome = {"t": "welcome", "subdomain": "ahmad",
                        "public_url": "https://ahmad.home.eshtaya.ps",
                        "limits": {"streams": 64, "body_max": 104857600,
                                   "bytes_month": 0, "used_month": 0}}

    async def start(self, port: int = 0) -> None:
        app = web.Application()
        app.router.add_get("/agent", self.handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/agent"

    async def stop(self) -> None:
        if self.ws is not None and not self.ws.closed:
            await self.ws.close(code=1001)
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def handler(self, request: web.Request):
        self.attempts.append(time.monotonic())
        if self.reject_status:
            return web.Response(status=self.reject_status)
        if request.headers.get("Authorization") != self.expected:
            return web.Response(status=401)
        ws = web.WebSocketResponse(autoping=True, max_msg_size=0)
        await ws.prepare(request)
        self.agent_headers.append(dict(request.headers))
        first = await ws.receive()
        hello = json.loads(first.data)
        self.hellos.append(hello)
        await ws.send_str(json.dumps(self.welcome))
        self.ws = ws
        self.disconnected.clear()
        self.connected.set()
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    sid = m.get("id")
                    if isinstance(sid, int):
                        self.stream(sid).put_nowait(m)
                    else:
                        self.control.put_nowait(m)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    sid, kind, payload = F.unpack(msg.data)
                    self.stream(sid).put_nowait(("bin", kind, payload))
        finally:
            if self.ws is ws:
                self.ws = None
            self.connected.clear()
            self.disconnected.set()
        return ws

    # -- helpers ----------------------------------------------------------
    def stream(self, sid: int) -> asyncio.Queue:
        return self._streams.setdefault(sid, asyncio.Queue())

    def new_id(self) -> int:
        self._next += 2
        return self._next

    async def send(self, msg: dict) -> None:
        await self.ws.send_str(json.dumps(msg))

    async def send_frame(self, sid: int, kind: int, payload: bytes = b"") -> None:
        await self.ws.send_bytes(F.pack(sid, kind, payload))

    async def next(self, sid: int, timeout: float = 5.0):
        return await asyncio.wait_for(self.stream(sid).get(), timeout)

    async def next_control(self, timeout: float = 5.0) -> dict:
        return await asyncio.wait_for(self.control.get(), timeout)

    async def http(self, method: str, path: str, headers=(), body: bytes | None = None,
                   client_ip: str = "203.0.113.9", timeout: float = 20.0):
        """One HTTP stream; returns (res message, body bytes, largest chunk)."""
        sid = self.new_id()
        await self.send({"t": "req", "id": sid, "method": method, "path": path,
                         "headers": [list(h) for h in headers], "client_ip": client_ip,
                         "body": body is not None})
        if body is not None:
            for chunk in F.chunks(body):
                await self.send_frame(sid, F.KIND_BODY, chunk)
            await self.send_frame(sid, F.KIND_END)
        res = await self.next(sid, timeout)
        assert isinstance(res, dict) and res["t"] == "res", res
        out = bytearray()
        largest = 0
        while True:
            item = await self.next(sid, timeout)
            assert isinstance(item, tuple), item
            _, kind, payload = item
            if kind == F.KIND_END:
                break
            assert kind == F.KIND_BODY
            largest = max(largest, len(payload))
            out += payload
        return res, bytes(out), largest
