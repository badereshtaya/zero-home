"""client_log sender of the integration (1.3.2) against a fake Zero Admin."""
from __future__ import annotations

import logging

import aiohttp
import pytest
from aiohttp import web

from link.clientlog import ClientLog, LogHandler, clean


@pytest.fixture
async def admin():
    seen: list[tuple[dict, str]] = []
    mode = {"status": 200}

    async def api(request: web.Request) -> web.Response:
        seen.append((await request.json(), request.headers.get("X-Zero-Session", "")))
        if mode["status"] != 200:
            return web.Response(status=mode["status"], text="<html>x</html>", content_type="text/html")
        return web.json_response({"ok": True, "stored": len(seen[-1][0]["lines"])})

    app = web.Application(); app.router.add_post("/api.php", api)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as s:
        yield s, f"http://127.0.0.1:{port}/api.php", seen, mode
    await runner.cleanup()


async def test_batch_dedupe_and_token(admin):
    s, url, seen, mode = admin
    log = ClientLog(url, lambda: "dev-token", device="Home Assistant — البيت", device_id="iid", version="1.3.2")
    for _ in range(3):
        log.add("warn", "relay disconnected: timeout")
    log.add("error", "boom", stack="Traceback…")
    assert await log.flush(s) == 2 and log.queue == []
    body, tok = seen[-1]
    assert tok == "dev-token" and body["channel"] == "ha" and body["device_id"] == "iid" and body["version"] == "1.3.2"
    assert body["lines"][0]["ctx"]["repeat"] == 3 and body["lines"][1]["stack"]


async def test_404_quiet_and_429_backoff(admin):
    s, url, seen, mode = admin
    t = [1000.0]
    log = ClientLog(url, lambda: "", device="d", device_id="i", version="v", now=lambda: t[0])
    mode["status"] = 429
    log.add("warn", "a")
    assert await log.flush(s) == 0 and len(log.queue) == 1
    n = len(seen)
    assert await log.flush(s) == 0 and len(seen) == n            # backing off: nothing sent
    t[0] += 601
    mode["status"] = 404
    assert await log.flush(s) == 0 and log.off is True
    log.add("warn", "b")
    assert len(log.queue) == 1                                    # off: nothing more is queued
    assert seen[0][1] == ""                                       # no token → guest


def test_clean_cuts_secrets():
    assert "abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in clean("key abcdefghijklmnopqrstuvwxyz0123456789ABCD here", 500)
    assert "hunter2" not in clean("password=hunter2 failed", 500)
    assert len(clean("x" * 900, 500)) <= 501


def test_handler_warnings_only():
    log = ClientLog("http://x/api.php", lambda: "", device="d", device_id="i", version="v")
    h = LogHandler(log, lambda f: f())
    lg = logging.getLogger("zh_test_handler"); lg.addHandler(h); lg.setLevel(logging.DEBUG)
    try:
        lg.info("hello"); lg.warning("careful %s", 1)
        try:
            raise ValueError("bad")
        except ValueError:
            lg.exception("failed")
    finally:
        lg.removeHandler(h)
    assert [l["level"] for l in log.queue] == ["warn", "error"] and "ValueError" in log.queue[1]["stack"]
