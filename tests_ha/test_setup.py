"""Full setup: the entry connects to a fake relay, serves RPC, reacts to bye."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.zero_home.const import DOMAIN
from fakes import FakeRelay

TOKEN = "f" * 64


async def _entry(hass, relay, **options):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        data={"admin_api": "https://zeroadmin.eshtaya.ps/api.php", "home_id": "h_test", "agent_token": TOKEN,
              "relay_ws": relay.url, "public_url": "https://ahmad.home.eshtaya.ps", "subdomain": "ahmad",
              "zero_web_origin": "https://zeroapp.eshtaya.ps", "owner_name": "Ahmad"},
        options={"expose_mode": "all", "remote_ui": True, "set_external_url": True, **options})
    entry.add_to_hass(hass)
    return entry


async def test_setup_rpc_and_revoke(hass: HomeAssistant):
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set("lock.front", "locked")
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    try:
        entry = await _entry(hass, relay, expose_mode="select", expose_domains=["light"])
        with patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=False):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
        hello = relay.hellos[0]
        assert hello["exposed"] == 1 and hello["remote_ui"] is True and hello["instance_id"]
        assert hass.config.external_url == "https://ahmad.home.eshtaya.ps"

        # the connectivity sensor follows the link
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
        states = [s for s in hass.states.async_all("binary_sensor")]
        assert states and states[0].state == "on"
        assert any(s.state == "https://ahmad.home.eshtaya.ps" for s in hass.states.async_all("sensor"))

        # ui_ready false → repair issue with the YAML
        issue = ir.async_get(hass).async_get_issue(DOMAIN, "ui_not_ready")
        assert issue is not None and "trusted_proxies" in issue.translation_placeholders["yaml"]

        # RPC through the link, limited to exposed entities
        await relay.send({"t": "rpc", "id": 1, "method": "ha.states", "params": {}})
        m = await relay.next(1)
        assert [s["entity_id"] for s in m["result"]] == ["light.kitchen"]
        await relay.send({"t": "rpc", "id": 3, "method": "ha.state", "params": {"entity_id": "lock.front"}})
        m = await relay.next(3)
        assert m["ok"] is False and m["code"] == "not_exposed"

        # a new exposed entity changes the count → state message
        hass.states.async_set("light.hall", "off")
        await asyncio.sleep(0)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
        await hass.async_block_till_done()
        msg = await relay.next_control()
        assert msg["t"] == "state" and msg["exposed"] == 2

        # bye revoked → stops, repair issue, reauth flow
        await relay.send({"t": "bye", "reason": "revoked"})
        await relay.ws.close()
        for _ in range(100):
            await hass.async_block_till_done()
            if ir.async_get(hass).async_get_issue(DOMAIN, "link_revoked"):
                break
            await asyncio.sleep(0.05)
        assert ir.async_get(hass).async_get_issue(DOMAIN, "link_revoked") is not None
        flows = hass.config_entries.flow.async_progress()
        assert any(f["context"]["source"] == "reauth" for f in flows)
        assert hass.states.async_all("binary_sensor")[0].state == "off"

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        await relay.stop()


async def test_external_url_not_overwritten(hass: HomeAssistant):
    await hass.config.async_update(external_url="https://mine.example.org")
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    try:
        entry = await _entry(hass, relay)
        with patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=True):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
        assert hass.config.external_url == "https://mine.example.org"
        assert ir.async_get(hass).async_get_issue(DOMAIN, "external_url_conflict") is not None
        assert ir.async_get(hass).async_get_issue(DOMAIN, "ui_not_ready") is None
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
    finally:
        await relay.stop()
