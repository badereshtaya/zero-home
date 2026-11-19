"""Full setup (1.3.0): settings come from the account (ha_agent_config), the link connects to a fake relay,
serves RPC, follows setting changes, stops when the account signs the integration out."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.zero_home.const import DOMAIN
from custom_components.zero_home.link import account as A
from fakes import FakeRelay

TOKEN = "f" * 64
P = "custom_components.zero_home.link.account."


def cfg(mode="domains", items=("light",), remote_ui=True, version="v1"):
    return {"ok": True, "config": {"expose": {"mode": mode, mode: list(items)} if mode != "all" else {"mode": "all"},
                                   "remote_ui": remote_ui},
            "version": version,
            "home": {"allowed": True, "linked": True, "online": True, "subdomain": "ahmad",
                     "public_url": "https://ahmad.home.eshtaya.ps", "status": "active"},
            "account": {"username": "ahmad", "display_name": "Ahmad",
                        "subscription": {"ok": True, "status": "trial", "days_left": 170}},
            "manage_url": "https://zero.eshtaya.ps/account.html#home", "refresh_every": 60}


def creds(relay):
    return {"ok": True, "home_id": "h_test", "agent_token": TOKEN, "subdomain": "ahmad",
            "public_url": "https://ahmad.home.eshtaya.ps", "relay_ws": relay.url,
            "zero_web_origin": "https://zeroapp.eshtaya.ps", "owner_name": "Ahmad"}


async def _entry(hass, relay=None, with_relay=True):
    data = {"admin_api": "https://zeroadmin.eshtaya.ps/api.php", "device_token": "dev-token", "username": "ahmad",
            "display_name": "Ahmad", "user_id": 7}
    if with_relay and relay is not None:
        data.update({k: v for k, v in creds(relay).items() if k != "ok"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=2, data=data)
    entry.add_to_hass(hass)
    return entry


async def test_setup_settings_from_account_and_sign_out(hass: HomeAssistant):
    hass.states.async_set("light.kitchen", "off")
    hass.states.async_set("lock.front", "locked")
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    get_cfg = AsyncMock(return_value=cfg())
    try:
        entry = await _entry(hass, relay, with_relay=False)
        with patch(P + "agent_config", get_cfg), patch(P + "activate", AsyncMock(return_value=creds(relay))) as act, \
                patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=False):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
            # the relay key came with the device token
            assert act.await_args.args[2] == "dev-token" and entry.data["agent_token"] == TOKEN
            hello = relay.hellos[0]
            # the account says: only lights, and the interface opens from outside
            assert hello["exposed"] == 1 and hello["remote_ui"] is True and hello["ui_ready"] is True
            assert hass.config.external_url == "https://ahmad.home.eshtaya.ps"
            rt = entry.runtime_data
            assert rt.ui_mode == "direct" and rt.local.forward_client is False

            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
            assert hass.states.async_all("binary_sensor")[0].state == "on"
            sensors = {s.entity_id: s for s in hass.states.async_all("sensor")}
            assert any(s.state == "https://ahmad.home.eshtaya.ps" for s in sensors.values())
            acc = [s for s in sensors.values() if s.state == "Ahmad"]
            assert acc and acc[0].attributes["subscription"] == "trial"
            assert acc[0].attributes["manage_url"].endswith("#home")

            # RPC only sees what the account allows
            await relay.send({"t": "rpc", "id": 1, "method": "ha.states", "params": {}})
            m = await relay.next(1)
            assert [s["entity_id"] for s in m["result"]] == ["light.kitchen"]

            # the person changes the settings on the site → applied at the next refresh
            get_cfg.return_value = cfg(mode="all", remote_ui=False, version="v2")
            await rt.refresh_config()           # (the 60-s timer calls the same)
            await hass.async_block_till_done()
            assert rt.options["remote_ui"] is False and rt.options["expose_mode"] == "all" and rt.exposed >= 2   # + its own sensors
            await relay.send({"t": "rpc", "id": 2, "method": "ha.state", "params": {"entity_id": "lock.front"}})
            m = await relay.next(2)
            assert m["ok"] is True

            # signed out from the account → the link stops, «sign in again», no reconnect by itself
            get_cfg.side_effect = A.Revoked("device_revoked")
            await rt.refresh_config()
            await hass.async_block_till_done()
            for _ in range(60):
                if ir.async_get(hass).async_get_issue(DOMAIN, "signed_out"):
                    break
                await asyncio.sleep(0.05)
                await hass.async_block_till_done()
            assert ir.async_get(hass).async_get_issue(DOMAIN, "signed_out") is not None
            assert rt.signed_out is True
            flows = hass.config_entries.flow.async_progress()
            assert any(f["context"]["source"] == "reauth" for f in flows)
            assert hass.states.async_all("binary_sensor")[0].state == "off"
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
    finally:
        await relay.stop()


async def test_relay_refuses_key_gets_a_new_one(hass: HomeAssistant):
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    try:
        entry = await _entry(hass, relay)
        with patch(P + "agent_config", AsyncMock(return_value=cfg())), \
                patch(P + "activate", AsyncMock(return_value=creds(relay))) as act, \
                patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=True):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
            assert act.await_count == 0                       # the stored key was enough
            await relay.send({"t": "bye", "reason": "revoked"})
            await relay.ws.close()
            for _ in range(100):
                await hass.async_block_till_done()
                if act.await_count:
                    break
                await asyncio.sleep(0.05)
            assert act.await_count == 1
            assert not any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
    finally:
        await relay.stop()


async def test_signed_out_before_start(hass: HomeAssistant):
    entry = await _entry(hass, None, with_relay=False)
    with patch(P + "agent_config", AsyncMock(side_effect=A.Revoked("device_revoked"))):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_old_entry_asks_to_sign_in(hass: HomeAssistant):
    """A 1.x entry (no device token) migrates and asks for the sign-in once."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=1, options={"remote_ui": True},
                            data={"admin_api": "https://zeroadmin.eshtaya.ps/api.php", "home_id": "h", "agent_token": "k",
                                  "relay_ws": "wss://x/agent", "public_url": "https://a.home.eshtaya.ps", "subdomain": "a"})
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2 and entry.options == {}
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_external_url_not_overwritten(hass: HomeAssistant):
    await hass.config.async_update(external_url="https://mine.example.org")
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    try:
        entry = await _entry(hass, relay)
        with patch(P + "agent_config", AsyncMock(return_value=cfg())), \
                patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=True):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
            assert hass.config.external_url == "https://mine.example.org"
            assert ir.async_get(hass).async_get_issue(DOMAIN, "external_url_conflict") is not None
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
    finally:
        await relay.stop()


# ── 1.3.1: one linking method per account (ROUND2 §A) ──────────────────────────────────────────
def cfg_state(state, **kw):
    c = cfg(**kw)
    c["home"]["state"] = state
    return c


async def test_account_uses_external_then_switches(hass: HomeAssistant):
    relay = FakeRelay("h_test", TOKEN)
    await relay.start()
    get_cfg = AsyncMock(return_value=cfg_state("method_external"))
    try:
        entry = await _entry(hass, relay, with_relay=False)
        with patch(P + "agent_config", get_cfg), patch(P + "activate", AsyncMock(return_value=creds(relay))) as act, \
                patch("custom_components.zero_home.link.proxy.LocalHA.check_ui_ready", return_value=True):
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            rt = entry.runtime_data
            # no relay, no activate, a read-only note with the account page
            assert rt.agent is None and act.await_count == 0 and rt.home_state == "method_external"
            issue = ir.async_get(hass).async_get_issue(DOMAIN, "home_state")
            assert issue is not None and issue.translation_key == "home_method_external"
            assert issue.translation_placeholders["manage_url"].endswith("#home")
            assert hass.states.async_all("binary_sensor")[0].state == "off"
            # still not ready on the next poll: nothing is retried
            await rt.refresh_config()
            await hass.async_block_till_done()
            assert act.await_count == 0
            # the person switches to Zero Home on the site → linked within the next poll
            get_cfg.return_value = cfg_state("ready")
            await rt.refresh_config()
            await hass.async_block_till_done()
            await asyncio.wait_for(relay.connected.wait(), 10)
            assert act.await_count == 1
            assert ir.async_get(hass).async_get_issue(DOMAIN, "home_state") is None
            rt = entry.runtime_data
            # switched off again → the relay stops and its key is dropped
            get_cfg.return_value = cfg_state("home_disabled")
            await rt.refresh_config()
            await hass.async_block_till_done()
            assert rt.connected is False and entry.data["agent_token"] == ""
            assert ir.async_get(hass).async_get_issue(DOMAIN, "home_state").translation_key == "home_home_disabled"
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
    finally:
        await relay.stop()


async def test_activate_409_is_shown_not_retried(hass: HomeAssistant):
    entry = await _entry(hass, None, with_relay=False)
    err = A.AccountError("home_disabled", "off", 409, {"manage_url": "https://zeroapp.eshtaya.ps/account.html#home-x"})
    with patch(P + "agent_config", AsyncMock(return_value=cfg_state("ready"))), \
            patch(P + "activate", AsyncMock(side_effect=err)) as act:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        rt = entry.runtime_data
        assert act.await_count == 1 and rt.home_state == "home_disabled" and rt.agent is None
        issue = ir.async_get(hass).async_get_issue(DOMAIN, "home_state")
        assert issue.translation_placeholders["manage_url"].endswith("#home-x")
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
