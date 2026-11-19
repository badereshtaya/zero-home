"""Sign-in flows (1.3.0): account or QR; no options; reauth only with the same account."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_home.const import DEFAULT_ADMIN_API, DOMAIN
from custom_components.zero_home.link import account as A

P = "custom_components.zero_home.link.account."
LOGIN = {"ok": True, "token": "dev-token", "user": {"id": 7, "username": "ahmad", "display_name": "Ahmad"}}
PAIR = {"ok": True, "pair_id": "p_1", "poll_secret": "s" * 48, "code": "K7MP4XQ2", "code_display": "K7MP-4XQ2",
        "qr_url": "https://zeroapp.eshtaya.ps/pair/?c=K7MP4XQ2", "qr_payload": "https://zeroapp.eshtaya.ps/pair/?c=K7MP4XQ2",
        "expires_in": 180, "poll_every": 1}


@pytest.fixture
def no_setup():
    with patch("custom_components.zero_home.async_setup_entry", return_value=True) as m:
        yield m


async def _start(hass, step):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.MENU and set(result["menu_options"]) == {"login", "qr"}
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


async def test_login_ok(hass: HomeAssistant, no_setup):
    with patch(P + "login", AsyncMock(return_value=LOGIN)) as login:
        result = await _start(hass, "login")
        assert result["step_id"] == "login"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "ahmad", "password": "x"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["device_token"] == "dev-token" and result["data"]["username"] == "ahmad"
    assert result["data"]["admin_api"] == DEFAULT_ADMIN_API
    assert "options" not in result or not result["options"]
    kw = login.await_args.kwargs
    assert kw["device"].startswith("Home Assistant") and kw["platform"].startswith("Home Assistant")


@pytest.mark.parametrize("kind", ["invalid_auth", "plan", "rate_limited", "cannot_connect", "need_2fa"])
async def test_login_errors(hass: HomeAssistant, no_setup, kind):
    with patch(P + "login", AsyncMock(side_effect=A.AccountError(kind, "msg"))):
        result = await _start(hass, "login")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "a", "password": "b"})
    assert result["type"] == FlowResultType.FORM and result["errors"] == {"base": kind}


async def test_qr_approved(hass: HomeAssistant, no_setup, hass_client_no_auth):
    polls = AsyncMock(side_effect=[{"status": "pending"},
                                   {"status": "approved", "device_token": "qr-token", "user": LOGIN["user"]}])
    with patch(P + "pair_start", AsyncMock(return_value=PAIR)), patch(P + "pair_poll", polls), \
            patch("custom_components.zero_home.config_flow.asyncio.sleep", AsyncMock()):
        result = await _start(hass, "qr")
        assert result["type"] == FlowResultType.SHOW_PROGRESS
        ph = result["description_placeholders"]
        assert ph["code"] == "K7MP-4XQ2" and ph["qr"].startswith("/api/zero_home/qr/")
        # the QR image is served while the screen shows it
        client = await hass_client_no_auth()
        r = await client.get(ph["qr"])
        assert r.status == 200 and "svg" in r.headers["Content-Type"]
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["device_token"] == "qr-token"
    assert (await (await hass_client_no_auth()).get(ph["qr"])).status == 404


async def test_qr_expired_renews_then_denied(hass: HomeAssistant, no_setup):
    starts = AsyncMock(return_value=PAIR)
    polls = AsyncMock(side_effect=[{"status": "expired"}, {"status": "denied"}])
    with patch(P + "pair_start", starts), patch(P + "pair_poll", polls),             patch("custom_components.zero_home.config_flow.asyncio.sleep", AsyncMock()):
        result = await _start(hass, "qr")
        flow_id = result["flow_id"]
        for _ in range(5):                      # the progress screens advance by themselves
            await hass.async_block_till_done()
            result = await hass.config_entries.flow.async_configure(flow_id)
            if result["type"] == FlowResultType.FORM:
                break
    # expired → a new code was asked on the same screen; then the account refused
    assert starts.await_count == 2
    assert result["step_id"] == "qr_failed" and result["errors"] == {"base": "denied"}


async def test_single_instance(hass: HomeAssistant, no_setup):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=2, data={"device_token": "t"}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "single_instance_allowed"


async def test_reauth_same_account_only(hass: HomeAssistant, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=2,
                            data={"device_token": "old", "username": "ahmad", "home_id": "h", "agent_token": "k"})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass) if hasattr(entry, "start_reauth_flow") else \
        await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_REAUTH,
                                                                   "entry_id": entry.entry_id}, data=entry.data)
    assert result["type"] == FlowResultType.MENU and result["step_id"] == "reauth"
    other = {**LOGIN, "user": {"id": 9, "username": "someone"}}
    with patch(P + "login", AsyncMock(return_value=other)):
        r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "login"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"username": "someone", "password": "x"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "wrong_account"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_REAUTH,
                                                                        "entry_id": entry.entry_id}, data=entry.data)
    with patch(P + "login", AsyncMock(return_value=LOGIN)):
        r = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "login"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"username": "ahmad", "password": "x"})
    assert r["type"] == FlowResultType.ABORT and r["reason"] == "reauth_successful"
    assert entry.data["device_token"] == "dev-token" and entry.data["agent_token"] == "k"
