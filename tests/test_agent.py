"""End-to-end: fake relay ⇄ agent ⇄ fake local Home Assistant."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from types import SimpleNamespace

import aiohttp
import pytest

from fakes import FakeHA, FakeRelay, pattern
from link import frames as F
from link.agent import Agent, AgentConfig
from link.proxy import LocalHA
from link.rpc import RpcError

HOME, TOKEN = "h_test", "a" * 64
MB5 = 5 * 1024 * 1024


async def _make(*, relay: FakeRelay | None = None, rpc=None, cfg_kw=None, start=True, ha_url=None):
    ha = FakeHA()
    await ha.start()
    relay = relay or FakeRelay(HOME, TOKEN)
    if relay.runner is None:
        await relay.start()
    local_session = aiohttp.ClientSession(auto_decompress=False, cookie_jar=aiohttp.DummyCookieJar())
    relay_session = aiohttp.ClientSession()
    local = LocalHA(local_session, ha_url or ha.url, public_host="ahmad.home.eshtaya.ps",
                    zero_web_origin="https://zeroapp.eshtaya.ps")
    state = {"ha_version": "2026.11.2", "instance_id": "iid-1", "remote_ui": True,
             "ui_ready": True, "exposed": 37, "location_name": "البيت"}
    events: list[tuple[str, dict]] = []
    delays: list[int] = []

    def backoff(attempt):
        delays.append(attempt)
        return 0.05 * (attempt + 1)

    async def default_rpc(method, params):
        if method == "echo":
            return {"params": params}
        if method == "slow":
            await asyncio.sleep(5)
        if method == "refuse":
            raise RpcError("not_exposed", "nope")
        raise RuntimeError("boom")

    kw = {"backoff": backoff}
    kw.update(cfg_kw or {})
    agent = Agent(AgentConfig(relay_ws=relay.url, home_id=HOME, agent_token=TOKEN, **kw),
                  relay_session=relay_session, local=local, rpc=rpc or default_rpc,
                  state=lambda: state, on_event=lambda n, i: events.append((n, i)))
    task = asyncio.create_task(agent.run()) if start else None
    env = SimpleNamespace(ha=ha, relay=relay, agent=agent, task=task, state=state, events=events,
                          delays=delays, local=local, sessions=(local_session, relay_session))
    if start:
        await asyncio.wait_for(relay.connected.wait(), 5)
    return env


async def _close(env):
    await env.agent.stop()
    if env.task is not None:
        await asyncio.wait_for(env.task, 5)
    for s in env.sessions:
        await s.close()
    await env.relay.stop()
    await env.ha.stop()


@pytest.fixture
async def env():
    e = await _make()
    yield e
    await _close(e)


# ---------------------------------------------------------------- handshake
async def test_handshake(env):
    r = env.relay
    assert r.hellos[0] == {"t": "hello", "agent_version": "1.0.0", "ha_version": "2026.11.2",
                           "instance_id": "iid-1", "remote_ui": True, "ui_ready": True,
                           "exposed": 37, "location_name": "البيت"}
    hdr = r.agent_headers[0]
    assert hdr["Authorization"] == f"Bearer {HOME}.{TOKEN}"
    assert hdr["X-Zero-Agent"] == "zero_home/1.0.0"
    await asyncio.sleep(0.05)
    assert env.agent.connected and env.agent.welcome["subdomain"] == "ahmad"
    assert ("connected", r.welcome) in env.events


async def test_state_updates(env):
    env.state["exposed"] = 40
    env.agent.notify_state()
    msg = await env.relay.next_control()
    assert msg == {"t": "state", "remote_ui": True, "ui_ready": True, "exposed": 40}
    env.agent.notify_state()          # nothing changed → nothing sent
    env.state["ui_ready"] = False
    env.agent.notify_state()
    msg = await env.relay.next_control()
    assert msg["ui_ready"] is False and msg["exposed"] == 40
    assert env.relay.control.empty()


# ---------------------------------------------------------------- HTTP
async def test_http_get_headers_and_xff(env):
    res, body, _ = await env.relay.http(
        "GET", "/api/echo?x=1",
        headers=[["Accept", "application/json"], ["Authorization", "Bearer t"],
                 ["X-Forwarded-For", "6.6.6.6"], ["X-Custom", "yes"]])
    assert res["status"] == 200
    info = json.loads(body)
    assert info["path_qs"] == "/api/echo?x=1"
    assert info["xff"] == ["203.0.113.9"]          # the client's spoofed value is gone
    assert info["xfp"] == "https" and info["xfh"] == "ahmad.home.eshtaya.ps"
    assert info["auth"] == "Bearer t" and info["x_custom"] == "yes"
    names = [n.lower() for n, _ in res["headers"]]
    assert "content-security-policy" in names


async def test_http_frame_header_rewrite(env):
    res, body, _ = await env.relay.http("GET", "/framed")
    assert body == b"<html>hi</html>"
    hdrs = [(n.lower(), v) for n, v in res["headers"]]
    assert not any(n == "x-frame-options" for n, _ in hdrs)
    assert [v for n, v in hdrs if n == "content-security-policy"] == [
        "default-src 'self'", "frame-ancestors 'self' https://zeroapp.eshtaya.ps"]
    assert [v for n, v in hdrs if n == "set-cookie"] == ["a=1; Path=/", "b=2; Path=/"]


async def test_http_redirect_not_followed(env):
    res, _, _ = await env.relay.http("GET", "/redirect")
    assert res["status"] == 302
    assert ("location", "/lovelace") in [(n.lower(), v) for n, v in res["headers"]]


async def test_http_download_5mb(env):
    t0 = time.monotonic()
    res, body, largest = await env.relay.http("GET", f"/api/big?n={MB5}")
    assert res["status"] == 200 and len(body) == MB5 and body == pattern(MB5)
    assert 0 < largest <= F.CHUNK_MAX
    assert time.monotonic() - t0 < 20


async def test_http_upload_5mb(env):
    data = os.urandom(MB5)
    res, body, _ = await env.relay.http("POST", "/api/echo", headers=[["Content-Type", "application/octet-stream"]],
                                        body=data)
    info = json.loads(body)
    assert res["status"] == 200 and info["len"] == MB5
    assert info["sha256"] == hashlib.sha256(data).hexdigest()


async def test_http_empty_body_still_ends(env):
    res, body, _ = await env.relay.http("HEAD", "/api/echo")
    assert res["status"] == 200 and body == b""


async def test_parallel_streams(env):
    results = await asyncio.gather(*[env.relay.http("GET", f"/api/big?n={200_000 + i}") for i in range(8)])
    for i, (res, body, _) in enumerate(results):
        assert res["status"] == 200 and len(body) == 200_000 + i


async def test_cancel_by_relay(env):
    r = env.relay
    sid = r.new_id()
    await r.send({"t": "req", "id": sid, "method": "GET", "path": "/api/slow", "headers": [],
                  "client_ip": "203.0.113.9", "body": False})
    res = await r.next(sid)
    assert res["t"] == "res" and res["status"] == 200
    first = await r.next(sid)
    assert first[1] == F.KIND_BODY
    await r.send({"t": "cancel", "id": sid})
    await asyncio.wait_for(env.ha.slow_ended.wait(), 5)     # local request aborted
    await asyncio.sleep(0.1)
    assert sid not in env.agent._streams


async def test_bad_path_and_ha_down():
    e = await _make(ha_url="http://127.0.0.1:1")
    try:
        res, body, _ = await e.relay.http("GET", "/api/echo")
        assert res["status"] == 502 and "error" in res and body == b""
        res, _, _ = await e.relay.http("GET", "//evil.example.com/")
        assert res["status"] == 400
    finally:
        await _close(e)


async def test_remote_ui_off(env):
    env.state["remote_ui"] = False
    res, body, _ = await env.relay.http("GET", "/api/echo")
    assert res["status"] == 403 and body == b""
    sid = env.relay.new_id()
    await env.relay.send({"t": "ws_open", "id": sid, "path": "/api/websocket", "headers": [],
                          "client_ip": "1.2.3.4", "protocols": []})
    assert (await env.relay.next(sid)) == {"t": "ws_fail", "id": sid, "status": 403}
    assert env.ha.seen == []     # nothing reached Home Assistant


# ---------------------------------------------------------------- WebSocket
async def test_websocket_text_binary_close(env):
    r = env.relay
    sid = r.new_id()
    await r.send({"t": "ws_open", "id": sid, "path": "/api/websocket",
                  "headers": [["Origin", "https://ahmad.home.eshtaya.ps"], ["Sec-WebSocket-Key", "zzz"]],
                  "client_ip": "198.51.100.4", "protocols": ["zh-test"]})
    ok = await r.next(sid)
    assert ok == {"t": "ws_ok", "id": sid, "protocol": "zh-test"}
    await r.send({"t": "ws_msg", "id": sid, "data": "hello"})
    assert (await r.next(sid)) == {"t": "ws_msg", "id": sid, "data": "echo:hello"}
    await r.send_frame(sid, F.KIND_WS_BINARY, b"\x01\x02\x03")
    assert (await r.next(sid)) == ("bin", F.KIND_WS_BINARY, b"\x03\x02\x01")
    big = "x" * 300_000
    await r.send({"t": "ws_msg", "id": sid, "data": big})
    assert (await r.next(sid))["data"] == "echo:" + big
    await r.send({"t": "ws_msg", "id": sid, "data": "close"})          # local side closes
    assert (await r.next(sid)) == {"t": "ws_close", "id": sid, "code": 4001}
    assert {"ws_xff": ["198.51.100.4"]} in env.ha.seen


async def test_websocket_closed_by_relay(env):
    r = env.relay
    sid = r.new_id()
    await r.send({"t": "ws_open", "id": sid, "path": "/api/websocket", "headers": [],
                  "client_ip": "1.2.3.4", "protocols": []})
    assert (await r.next(sid))["t"] == "ws_ok"
    await r.send({"t": "ws_close", "id": sid, "code": 1000})
    for _ in range(50):
        if sid not in env.agent._streams:
            break
        await asyncio.sleep(0.02)
    assert sid not in env.agent._streams
    assert r.stream(sid).empty()            # no echo of the close


async def test_websocket_fail(env):
    sid = env.relay.new_id()
    await env.relay.send({"t": "ws_open", "id": sid, "path": "/nope", "headers": [],
                          "client_ip": "1.2.3.4", "protocols": []})
    msg = await env.relay.next(sid)
    assert msg["t"] == "ws_fail" and msg["status"] == 404


# ---------------------------------------------------------------- RPC
async def test_rpc(env):
    r = env.relay
    await r.send({"t": "rpc", "id": 11, "method": "echo", "params": {"a": 1}})
    assert (await r.next(11)) == {"t": "rpc_res", "id": 11, "ok": True, "result": {"params": {"a": 1}}}
    await r.send({"t": "rpc", "id": 13, "method": "refuse", "params": {}})
    assert (await r.next(13)) == {"t": "rpc_res", "id": 13, "ok": False, "error": "nope", "code": "not_exposed"}
    await r.send({"t": "rpc", "id": 15, "method": "other", "params": {}})
    m = await r.next(15)
    assert m["ok"] is False and m["code"] == "ha_error" and m["error"] == "boom"


async def test_rpc_timeout():
    e = await _make(cfg_kw={"rpc_timeout": 0.2})
    try:
        await e.relay.send({"t": "rpc", "id": 21, "method": "slow", "params": {}})
        m = await e.relay.next(21)
        assert m["ok"] is False and m["code"] == "timeout"
    finally:
        await _close(e)


async def test_rpc_with_dispatcher():
    from test_rpc import FakeBackend
    from link.rpc import Exposure, RpcDispatcher
    d = RpcDispatcher(FakeBackend(), Exposure.from_options({"expose_mode": "select",
                                                             "expose_domains": ["light"]}))
    e = await _make(rpc=d)
    try:
        await e.relay.send({"t": "rpc", "id": 31, "method": "ha.call",
                            "params": {"domain": "lock", "service": "unlock", "entity_ids": ["lock.front"]}})
        m = await e.relay.next(31)
        assert m["ok"] is False and m["code"] == "not_exposed"
        await e.relay.send({"t": "rpc", "id": 33, "method": "ha.states", "params": {}})
        m = await e.relay.next(33)
        assert sorted(s["entity_id"] for s in m["result"]) == ["light.bedroom", "light.kitchen"]
    finally:
        await _close(e)


# ---------------------------------------------------------------- lifecycle
async def test_relay_restart_reconnects_with_backoff():
    e = await _make()
    try:
        port = e.relay.port
        await e.relay.stop()                      # relay goes away
        # agent retries with the growing schedule (a refused connect is slow on Windows)
        for _ in range(300):
            if len(e.delays) >= 3:
                break
            await asyncio.sleep(0.05)
        assert e.delays[:3] == [0, 1, 2]
        assert len(e.relay.attempts) == 1          # nothing reached the stopped relay
        assert any(n == "disconnected" for n, _ in e.events)
        e.relay.connected.clear()
        await e.relay.start(port)                 # relay is back on the same port
        await asyncio.wait_for(e.relay.connected.wait(), 5)
        assert len(e.relay.hellos) == 2
        # after a welcome the schedule starts again from the first step
        n = len(e.delays)
        await e.relay.send({"t": "bye", "reason": "shutdown"})
        await e.relay.ws.close()
        e.relay.connected.clear()
        await asyncio.wait_for(e.relay.connected.wait(), 5)
        assert e.delays[n] == 0
        assert len(e.relay.hellos) == 3
    finally:
        await _close(e)


@pytest.mark.parametrize("reason", ["revoked", "plan"])
async def test_bye_final_stops(reason):
    e = await _make()
    try:
        await e.relay.send({"t": "bye", "reason": reason})
        await e.relay.ws.close()
        assert await asyncio.wait_for(e.task, 5) == reason
        attempts = len(e.relay.attempts)
        await asyncio.sleep(0.4)
        assert len(e.relay.attempts) == attempts == 1     # no reconnect
        assert e.events[-1] == ("stopped", {"reason": reason, "error": None})
    finally:
        await _close(e)


@pytest.mark.parametrize("status,reason", [(401, "revoked"), (402, "plan")])
async def test_handshake_rejected_stops(status, reason):
    relay = FakeRelay(HOME, TOKEN)
    relay.reject_status = status
    await relay.start()
    e = await _make(relay=relay, start=False)
    try:
        e.task = asyncio.create_task(e.agent.run())
        assert await asyncio.wait_for(e.task, 5) == reason
        assert len(relay.attempts) == 1
    finally:
        await _close(e)


async def test_handshake_server_error_retries():
    relay = FakeRelay(HOME, TOKEN)
    relay.reject_status = 503
    await relay.start()
    e = await _make(relay=relay, start=False)
    try:
        e.task = asyncio.create_task(e.agent.run())
        await asyncio.sleep(0.5)
        assert len(relay.attempts) >= 3
        relay.reject_status = None
        await asyncio.wait_for(relay.connected.wait(), 5)
    finally:
        await _close(e)


async def test_silent_relay_is_dead():
    e = await _make(cfg_kw={"dead_after": 0.4, "ping_interval": 30})
    try:
        # the relay answers nothing (its pongs only answer pings, none are sent)
        await asyncio.sleep(1.0)
        assert len(e.relay.hellos) >= 2
        assert any(n == "disconnected" for n, _ in e.events)
    finally:
        await _close(e)


async def test_pings_keep_alive():
    e = await _make(cfg_kw={"dead_after": 0.5, "ping_interval": 0.1})
    try:
        await asyncio.sleep(1.5)
        assert len(e.relay.hellos) == 1 and e.agent.connected
    finally:
        await _close(e)


async def test_stop_while_connected(env):
    await env.agent.stop()
    assert await asyncio.wait_for(env.task, 5) == "stopped"
    await asyncio.wait_for(env.relay.disconnected.wait(), 5)


# ---------------------------------------------------------------- ui_ready
async def test_ui_ready_check(env):
    assert await env.local.check_ui_ready() is True
    env.ha.strict = True
    assert await env.local.check_ui_ready() is False
    async with aiohttp.ClientSession() as s:
        assert await LocalHA(s, "http://127.0.0.1:1").check_ui_ready() is None


async def test_strict_ha_answers_400_through_link(env):
    env.ha.strict = True
    res, _, _ = await env.relay.http("GET", "/api/echo")
    assert res["status"] == 400
