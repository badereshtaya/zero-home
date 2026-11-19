"""The Zero account client (1.3.0, DEVICE_PAIRING_API.md v1) against a fake Zero Admin."""
from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web

from link import account as A


@pytest.fixture
async def admin():
    calls: list[tuple[str, dict, str]] = []
    state = {"poll": ["pending", "approved"], "revoked": False, "cfg_mode": "domains"}

    async def api(request: web.Request) -> web.Response:
        action = request.query.get("action", "")
        body = await request.json()
        token = request.headers.get("X-Zero-Session", "")
        calls.append((action, body, token))
        if token and state["revoked"]:
            return web.json_response({"ok": False, "error": "device_revoked", "code": "device_revoked",
                                      "message": "هالجهاز انطلع من الحساب"}, status=401)
        if action == "login":
            if body.get("password") != "pw":
                return web.json_response({"ok": False, "error": "غلط", "code": "auth"}, status=422)
            if body.get("username") == "twofa":
                return web.json_response({"ok": True, "need_2fa": True})
            return web.json_response({"ok": True, "token": "dev-token", "user": {"id": 7, "username": body["username"]}})
        if action == "pair_start":
            if body.get("device_name") == "rate":
                return web.json_response({"ok": False, "code": "rate"}, status=429)
            return web.json_response({"ok": True, "pair_id": "p_1", "poll_secret": "s" * 48, "code": "K7MP4XQ2",
                                      "code_display": "K7MP-4XQ2", "qr_payload": "https://zeroapp.eshtaya.ps/pair/?c=K7MP4XQ2",
                                      "expires_in": 180, "poll_every": 2})
        if action == "pair_poll":
            if body.get("poll_secret") == "gone":
                return web.json_response({"ok": False, "status": "unknown", "code": "unknown"}, status=410)
            if body.get("poll_secret") == "html":
                return web.Response(status=404, text="<html>not found</html>", content_type="text/html")
            if body.get("poll_secret") != "s" * 48:
                return web.json_response({"ok": False, "code": "unknown"}, status=404)
            st = state["poll"].pop(0)
            if st == "approved":
                return web.json_response({"ok": True, "status": "approved", "device_token": "qr-token",
                                          "user": {"id": 7, "username": "ahmad"}, "login": {"token": "qr-token"}})
            return web.json_response({"ok": True, "status": st, "expires_in": 100, "poll_every": 2})
        if action == "home_link_activate":
            if not token:
                return web.json_response({"ok": False, "code": "auth"}, status=422)
            return web.json_response({"ok": True, "home_id": "h_1", "agent_token": "k" * 64, "subdomain": "ahmad",
                                      "public_url": "https://ahmad.home.eshtaya.ps",
                                      "relay_ws": "wss://relay.home.eshtaya.ps/agent", "zero_web_origin": "https://z", "owner_name": "A"})
        if action == "ha_agent_config":
            m = state["cfg_mode"]
            return web.json_response({"ok": True, "config": {"expose": {"mode": m, m: ["light"]} if m != "all" else {"mode": "all"},
                                                             "remote_ui": True}, "version": "v1", "refresh_every": 60})
        return web.json_response({"ok": False}, status=404)

    app = web.Application()
    app.router.add_post("/api.php", api)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as session:
        yield session, f"http://127.0.0.1:{port}/api.php", calls, state
    await runner.cleanup()


async def test_login(admin):
    session, url, calls, state = admin
    ans = await A.login(session, url, " ahmad ", "pw", device="Home Assistant — البيت", platform="Home Assistant 2026.11", app_version="1.3.0")
    assert ans["token"] == "dev-token"
    action, body, _ = calls[-1]
    assert action == "login" and body["kind"] == "ha_integration" and body["username"] == "ahmad"
    assert body["device"] == "Home Assistant — البيت" and body["app_version"] == "1.3.0"
    with pytest.raises(A.AccountError) as e:
        await A.login(session, url, "ahmad", "bad", device="d", platform="p", app_version="1")
    assert e.value.kind == A.ERR_AUTH
    with pytest.raises(A.AccountError) as e:
        await A.login(session, url, "twofa", "pw", device="d", platform="p", app_version="1")
    assert e.value.kind == A.ERR_2FA


async def test_pairing(admin):
    session, url, calls, state = admin
    p = await A.pair_start(session, url, device_name="Home Assistant", platform="HA", app_version="1.3.0")
    assert p["code_display"] == "K7MP-4XQ2" and calls[-1][1]["kind"] == "ha_integration"
    assert (await A.pair_poll(session, url, p["pair_id"], p["poll_secret"]))["status"] == "pending"
    res = await A.pair_poll(session, url, p["pair_id"], p["poll_secret"])
    assert res["status"] == "approved" and res["device_token"] == "qr-token"
    assert (await A.pair_poll(session, url, "p_1", "wrong"))["status"] == "unknown"
    assert (await A.pair_poll(session, url, "p_1", "gone"))["status"] == "unknown"      # 410 + JSON
    assert (await A.pair_poll(session, url, "p_1", "html"))["status"] == "unknown"      # 404 + an HTML page
    with pytest.raises(A.AccountError) as e:
        await A.pair_start(session, url, device_name="rate", platform="HA", app_version="1")
    assert e.value.kind == A.ERR_RATE


async def test_token_calls_and_revoked(admin):
    session, url, calls, state = admin
    creds = await A.activate(session, url, "dev-token", instance_id="iid", ha_version="2026.11", agent_version="1.3.0")
    assert creds["agent_token"] == "k" * 64
    action, body, token = calls[-1]
    assert token == "dev-token" and set(body) == {"instance_id", "ha_version", "agent_version"}
    cfg = await A.agent_config(session, url, "dev-token")
    assert cfg["config"]["remote_ui"] is True
    state["revoked"] = True
    with pytest.raises(A.Revoked):
        await A.agent_config(session, url, "dev-token")
    with pytest.raises(A.Revoked):
        await A.activate(session, url, "dev-token", instance_id="i", ha_version="v", agent_version="1")


async def test_unreachable():
    async with aiohttp.ClientSession() as session:
        with pytest.raises(A.AccountError) as e:
            await A.agent_config(session, "http://127.0.0.1:1/api.php", "t")
    assert e.value.kind == A.ERR_CONNECT and not isinstance(e.value, A.Revoked)


@pytest.mark.parametrize("expose,mode,key,items", [
    ({"mode": "all"}, "all", None, None),
    ({"mode": "areas", "areas": ["living_room"]}, "select", "expose_areas", ["living_room"]),
    ({"mode": "domains", "domains": ["light", "climate"]}, "select", "expose_domains", ["light", "climate"]),
    ({"mode": "entities", "entities": ["switch.pump"]}, "select", "expose_entities", ["switch.pump"]),
    ({"mode": "areas", "areas": []}, "select", "expose_areas", []),      # chosen but empty → nothing, never «all»
])
def test_options_from_config(expose, mode, key, items):
    o = A.options_from_config({"config": {"expose": expose, "remote_ui": True}})
    assert o["expose_mode"] == mode and o["remote_ui"] is True and o["set_external_url"] is True
    if key:
        assert o[key] == items
    assert A.options_from_config({})["remote_ui"] is False
