"""Config, reauth and options flows."""
from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_home.const import DEFAULT_ADMIN_API, DOMAIN

URL = DEFAULT_ADMIN_API + "?action=home_link_activate"
OK = {"ok": True, "home_id": "h_3f9a1c2e7b", "agent_token": "f" * 64, "subdomain": "ahmad",
      "public_url": "https://ahmad.home.eshtaya.ps", "relay_ws": "wss://relay.home.eshtaya.ps/agent",
      "zero_web_origin": "https://zeroapp.eshtaya.ps", "owner_name": "Ahmad"}
DATA = {"admin_api": DEFAULT_ADMIN_API, "home_id": "h_3f9a1c2e7b", "agent_token": "f" * 64,
        "relay_ws": "wss://relay.home.eshtaya.ps/agent", "public_url": "https://ahmad.home.eshtaya.ps",
        "subdomain": "ahmad", "zero_web_origin": "https://zeroapp.eshtaya.ps", "owner_name": "Ahmad"}


@pytest.fixture
def no_setup():
    with patch("custom_components.zero_home.async_setup_entry", return_value=True) as m:
        yield m


async def _start(hass, step="code"):
    """The flow opens on a menu (login | code); go to `step`."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    if result["type"] != FlowResultType.MENU:
        return result
    assert result["step_id"] == "user" and set(result["menu_options"]) == {"login", "code"}
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": step})


async def test_flow_ok(hass: HomeAssistant, aioclient_mock, no_setup):
    aioclient_mock.post(URL, json=OK)
    result = await _start(hass)
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "code"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "zh 7k2q 9mwd 4xrt"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    assert result["options"]["expose_mode"] == "all" and result["options"]["remote_ui"] is True
    sent = aioclient_mock.mock_calls[0][2]
    assert sent["code"] == "ZH-7K2Q-9MWD-4XRT" and sent["agent_version"] == "1.0.0"
    assert sent["instance_id"] and sent["ha_version"]


async def test_flow_login_ok(hass: HomeAssistant, aioclient_mock, no_setup):
    aioclient_mock.post(URL, json=OK)
    result = await _start(hass, "login")
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "login"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "ahmad", "password": "s3cret!"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == DATA
    sent = aioclient_mock.mock_calls[0][2]
    assert sent["username"] == "ahmad" and sent["password"] == "s3cret!" and "code" not in sent


async def test_flow_login_wrong(hass: HomeAssistant, aioclient_mock, no_setup):
    aioclient_mock.post(URL, json={"ok": False, "error": "غلط", "code": "auth"}, status=422)
    result = await _start(hass, "login")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"username": "ahmad", "password": "x"})
    assert result["type"] == FlowResultType.FORM and result["errors"] == {"base": "invalid_auth"}


async def test_flow_bad_format(hass: HomeAssistant, aioclient_mock, no_setup):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "hello"})
    assert result["type"] == FlowResultType.FORM and result["errors"] == {"code": "invalid_format"}
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize("answer,status,error", [
    ({"ok": False, "error": "الرمز غير صالح"}, 422, "rejected"),
    ({"ok": False, "error": "مستعمل", "code": "used"}, 422, "code_used"),
    ({"ok": False, "error": "x", "code": "plan"}, 422, "plan"),
])
async def test_flow_rejected(hass: HomeAssistant, aioclient_mock, no_setup, answer, status, error):
    aioclient_mock.post(URL, json=answer, status=status)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ZH-7K2Q-9MWD-4XRT"})
    assert result["type"] == FlowResultType.FORM and result["errors"] == {"base": error}
    assert result["description_placeholders"]["server_message"] == answer["error"]


async def test_flow_unreachable(hass: HomeAssistant, aioclient_mock, no_setup):
    aioclient_mock.post(URL, exc=aiohttp.ClientConnectionError())
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ZH-7K2Q-9MWD-4XRT"})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_single_instance(hass: HomeAssistant, no_setup):
    MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN).add_to_hass(hass)
    result = await _start(hass)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth(hass: HomeAssistant, aioclient_mock, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, data={**DATA, "agent_token": "0" * 64}, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    aioclient_mock.post(URL, json=OK)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id}, data=entry.data)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "ZH-AAAA-BBBB-CCCC"})
    assert result["type"] == FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["agent_token"] == "f" * 64


async def test_options_all(hass: HomeAssistant, no_setup):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"expose_mode": "all", "remote_ui": False, "set_external_url": False})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["remote_ui"] is False and entry.options["expose_mode"] == "all"


async def test_options_select(hass: HomeAssistant, no_setup):
    hass.states.async_set("light.kitchen", "off")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"expose_mode": "select", "remote_ui": True, "set_external_url": False})
    assert result["step_id"] == "select"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "nothing_selected"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"expose_domains": ["light"], "expose_entities": ["light.kitchen"]})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["expose_mode"] == "select"
    assert entry.options["expose_domains"] == ["light"]
    assert entry.options["expose_entities"] == ["light.kitchen"]


async def test_options_external_url_conflict(hass: HomeAssistant, no_setup):
    await hass.config.async_update(external_url="https://mine.example.org")
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"expose_mode": "all", "remote_ui": True, "set_external_url": True})
    assert result["step_id"] == "external_url"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"replace_external_url": True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["replace_external_url"] is True
