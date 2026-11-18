"""Unit tests: frames, backoff, activation code, header rewrite, activation call."""
from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web

from link import frames as F
from link.activation import (ERR_AUTH, ERR_CONNECT, ERR_FORMAT, ERR_REJECTED, ERR_USED, ERR_ANSWER,
                             ActivationError, action_url, activate, activate_login, normalize_code)
from link.proxy import frame_ancestors, rewrite_request_headers, rewrite_response_headers


def test_pack_unpack_roundtrip():
    raw = F.pack(7, F.KIND_BODY, b"abc")
    assert raw == b"\x00\x00\x00\x07\x00abc"
    assert F.unpack(raw) == (7, 0, b"abc")
    assert F.unpack(F.pack(2**31 - 1, F.KIND_END)) == (2**31 - 1, 1, b"")
    assert F.unpack(F.pack(9, F.KIND_WS_BINARY, b"\x00\xff")) == (9, 2, b"\x00\xff")
    with pytest.raises(ValueError):
        F.unpack(b"\x00\x00")


def test_backoff_schedule_and_jitter():
    lo = [F.backoff_delay(i, rand=lambda: 0.0) for i in range(8)]
    hi = [F.backoff_delay(i, rand=lambda: 1.0) for i in range(8)]
    mid = [F.backoff_delay(i, rand=lambda: 0.5) for i in range(8)]
    assert mid == [1, 2, 5, 10, 30, 60, 60, 60]
    assert lo == pytest.approx([x * 0.8 for x in mid])
    assert hi == pytest.approx([x * 1.2 for x in mid])


def test_chunks_limit():
    data = bytes(200_000)
    parts = list(F.chunks(data))
    assert all(len(p) <= F.CHUNK_MAX for p in parts) and b"".join(parts) == data


@pytest.mark.parametrize("raw", ["ZH-7K2Q-9MWD-4XRT", "zh-7k2q-9mwd-4xrt", " zh 7k2q 9mwd 4xrt ",
                                 "ZH7K2Q9MWD4XRT", "zh–7k2q–9mwd–4xrt"])
def test_normalize_code_ok(raw):
    assert normalize_code(raw) == "ZH-7K2Q-9MWD-4XRT"


@pytest.mark.parametrize("raw", ["", "ZH-7K2Q-9MWD", "XX-7K2Q-9MWD-4XRT", "ZH-7K2Q-9MWD-4XR!"])
def test_normalize_code_bad(raw):
    with pytest.raises(ActivationError) as e:
        normalize_code(raw)
    assert e.value.kind == ERR_FORMAT


def test_action_url():
    assert action_url("https://a.example.com/api.php") == "https://a.example.com/api.php?action=home_link_activate"
    assert action_url("https://a/api.php?x=1") == "https://a/api.php?x=1&action=home_link_activate"


def test_request_headers_rewrite():
    hdrs = [["Accept", "application/json"], ["Host", "evil"], ["Connection", "keep-alive, X-Drop"],
            ["X-Drop", "1"], ["X-Forwarded-For", "6.6.6.6"], ["Forwarded", "for=6.6.6.6"],
            ["Authorization", "Bearer abc"], ["Transfer-Encoding", "chunked"], ["Upgrade", "h2c"],
            ["Cookie", "x=1"]]
    out = rewrite_request_headers(hdrs, "203.0.113.9", "ahmad.home.eshtaya.ps")
    names = [n.lower() for n, _ in out]
    assert ("Accept", "application/json") in out and ("Authorization", "Bearer abc") in out
    assert ("Cookie", "x=1") in out
    for gone in ("host", "connection", "x-drop", "forwarded", "transfer-encoding", "upgrade"):
        assert gone not in names
    assert [v for n, v in out if n == "X-Forwarded-For"] == ["203.0.113.9"]
    assert ("X-Forwarded-Proto", "https") in out
    assert ("X-Forwarded-Host", "ahmad.home.eshtaya.ps") in out
    ws = rewrite_request_headers([["Sec-WebSocket-Key", "k"], ["Origin", "o"]], "1.1.1.1", "h", websocket=True)
    assert ("Origin", "o") in ws and all(n != "Sec-WebSocket-Key" for n, _ in ws)


def test_response_headers_rewrite():
    raw = [("Content-Type", "text/html"), ("X-Frame-Options", "SAMEORIGIN"),
           ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'"),
           ("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"), ("Transfer-Encoding", "chunked"),
           ("Connection", "close")]
    out = rewrite_response_headers(raw, "https://zeroapp.eshtaya.ps")
    low = [(n.lower(), v) for n, v in out]
    assert ("x-frame-options", "SAMEORIGIN") not in low
    assert not any(n == "x-frame-options" for n, _ in low)
    assert ("set-cookie", "a=1") in low and ("set-cookie", "b=2") in low
    assert not any(n in ("transfer-encoding", "connection") for n, _ in low)
    csp = [v for n, v in low if n == "content-security-policy"]
    assert csp == ["default-src 'self'", "frame-ancestors 'self' https://zeroapp.eshtaya.ps"]
    assert frame_ancestors("") == "frame-ancestors 'self'"


# ---------------------------------------------------------------- activation
@pytest.fixture
async def admin():
    calls = []
    mode = {"m": "ok"}

    async def api(request: web.Request):
        assert request.query.get("action") == "home_link_activate"
        body = await request.json()
        calls.append(body)
        m = mode["m"]
        if m == "ok":
            return web.json_response({"ok": True, "home_id": "h_3f9a1c2e7b", "agent_token": "f" * 64,
                                      "subdomain": "ahmad", "public_url": "https://ahmad.home.eshtaya.ps",
                                      "relay_ws": "wss://relay.home.eshtaya.ps/agent",
                                      "zero_web_origin": "https://zeroapp.eshtaya.ps", "owner_name": "Ahmad"})
        if m == "used":
            return web.json_response({"ok": False, "error": "الرمز مستعمل", "code": "used"}, status=422)
        if m == "rejected":
            return web.json_response({"ok": False, "error": "الرمز غير صالح"}, status=422)
        if m == "partial":
            return web.json_response({"ok": True, "home_id": "h_1"})
        return web.Response(status=500, text="oops")

    app = web.Application()
    app.router.add_post("/api.php", api)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        yield session, f"http://127.0.0.1:{port}/api.php", calls, mode
    await runner.cleanup()


async def test_activate_ok(admin):
    session, url, calls, mode = admin
    ans = await activate(session, url, "zh 7k2q 9mwd 4xrt", instance_id="iid",
                         ha_version="2026.11.2", agent_version="1.0.0")
    assert ans["home_id"] == "h_3f9a1c2e7b"
    assert calls == [{"code": "ZH-7K2Q-9MWD-4XRT", "instance_id": "iid",
                      "ha_version": "2026.11.2", "agent_version": "1.0.0"}]


@pytest.mark.parametrize("m,kind", [("used", ERR_USED), ("rejected", ERR_REJECTED),
                                    ("partial", ERR_ANSWER), ("error", ERR_CONNECT)])
async def test_activate_errors(admin, m, kind):
    session, url, calls, mode = admin
    mode["m"] = m
    with pytest.raises(ActivationError) as e:
        await activate(session, url, "ZH-7K2Q-9MWD-4XRT", instance_id="i", ha_version="v", agent_version="1")
    assert e.value.kind == kind
    if m == "rejected":
        assert e.value.message == "الرمز غير صالح"


async def test_activate_login(admin):
    session, url, calls, mode = admin
    ans = await activate_login(session, url, " ahmad ", "pw", instance_id="iid", ha_version="2026.11.2", agent_version="1.0.0")
    assert ans["home_id"] == "h_3f9a1c2e7b"
    assert calls[-1] == {"username": "ahmad", "password": "pw", "instance_id": "iid",
                         "ha_version": "2026.11.2", "agent_version": "1.0.0"}
    with pytest.raises(ActivationError) as e:
        await activate_login(session, url, "", "pw", instance_id="i", ha_version="v", agent_version="1")
    assert e.value.kind == ERR_AUTH


async def test_activate_unreachable():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ActivationError) as e:
            await activate(session, "http://127.0.0.1:1/api.php", "ZH-7K2Q-9MWD-4XRT",
                           instance_id="i", ha_version="v", agent_version="1", timeout=3)
    assert e.value.kind == ERR_CONNECT
