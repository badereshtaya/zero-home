"""Config, reauth and options flows for Zero Home."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, __version__ as HA_VERSION
from homeassistant.core import callback
from homeassistant.helpers import instance_id, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AGENT_VERSION, CONF_ADMIN_API, CONF_AGENT_TOKEN, CONF_CODE, CONF_HOME_ID, CONF_OWNER_NAME,
    CONF_PUBLIC_URL, CONF_RELAY_WS, CONF_SUBDOMAIN, CONF_ZERO_WEB_ORIGIN, DEFAULT_ADMIN_API,
    DEFAULT_OPTIONS, DOMAIN, MODE_ALL, MODE_SELECT, NAME, OPT_EXPOSE_AREAS, OPT_EXPOSE_DOMAINS,
    OPT_EXPOSE_ENTITIES, OPT_EXPOSE_LABELS, OPT_EXPOSE_MODE, OPT_REMOTE_UI,
    OPT_REPLACE_EXTERNAL_URL, OPT_SET_EXTERNAL_URL,
)
from .link.activation import ERR_FORMAT, ActivationError, activate, activate_login, normalize_code


def _entry_data(answer: dict, admin_api: str) -> dict:
    return {
        CONF_ADMIN_API: admin_api,
        CONF_HOME_ID: answer["home_id"],
        CONF_AGENT_TOKEN: answer["agent_token"],
        CONF_RELAY_WS: answer["relay_ws"],
        CONF_PUBLIC_URL: answer["public_url"],
        CONF_SUBDOMAIN: answer["subdomain"],
        CONF_ZERO_WEB_ORIGIN: answer.get("zero_web_origin") or "",
        CONF_OWNER_NAME: answer.get("owner_name") or "",
    }


class ZeroHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two ways to link: the Zero account itself (username + password — the address is the
    account's username) or an activation code from the account page."""
    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def _run(self, call) -> tuple[dict | None, dict, dict]:
        """(answer, errors, placeholders) of one activation attempt."""
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        try:
            answer = await call(async_get_clientsession(self.hass), instance_id=await instance_id.async_get(self.hass),
                                ha_version=HA_VERSION, agent_version=AGENT_VERSION)
            return answer, errors, placeholders
        except ActivationError as err:
            errors[CONF_CODE if err.kind == ERR_FORMAT else "base"] = err.kind
            placeholders["server_message"] = err.message or ""
            return None, errors, placeholders

    async def _activate(self, code: str, admin_api: str) -> tuple[dict | None, dict, dict]:
        try:
            normalize_code(code)
        except ActivationError as err:
            return None, {CONF_CODE: err.kind}, {"server_message": ""}
        return await self._run(lambda session, **kw: activate(session, admin_api, code, **kw))

    async def _login(self, username: str, password: str, admin_api: str) -> tuple[dict | None, dict, dict]:
        return await self._run(lambda session, **kw: activate_login(session, admin_api, username, password, **kw))

    def _code_schema(self, user_input: dict | None, *, with_api: bool) -> vol.Schema:
        user_input = user_input or {}
        fields: dict = {vol.Required(CONF_CODE, default=user_input.get(CONF_CODE, "")): str}
        if with_api:
            fields[vol.Optional(CONF_ADMIN_API, default=user_input.get(CONF_ADMIN_API, DEFAULT_ADMIN_API))] = str
        return vol.Schema(fields)

    def _login_schema(self, user_input: dict | None, *, with_api: bool) -> vol.Schema:
        user_input = user_input or {}
        fields: dict = {
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }
        if with_api:
            fields[vol.Optional(CONF_ADMIN_API, default=user_input.get(CONF_ADMIN_API, DEFAULT_ADMIN_API))] = str
        return vol.Schema(fields)

    async def _created(self, answer: dict, admin_api: str) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        title = f"{NAME} — {answer.get('subdomain')}"
        return self.async_create_entry(title=title, data=_entry_data(answer, admin_api), options=dict(DEFAULT_OPTIONS))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_show_menu(step_id="user", menu_options=["login", "code"])

    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None:
            admin_api = (user_input.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API).strip()
            answer, errors, placeholders = await self._login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], admin_api)
            if answer is not None:
                return await self._created(answer, admin_api)
        return self.async_show_form(step_id="login", data_schema=self._login_schema(user_input, with_api=self.show_advanced_options),
                                    errors=errors, description_placeholders=placeholders)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None:
            admin_api = (user_input.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API).strip()
            answer, errors, placeholders = await self._activate(user_input[CONF_CODE], admin_api)
            if answer is not None:
                return await self._created(answer, admin_api)
        return self.async_show_form(step_id="code", data_schema=self._code_schema(user_input, with_api=self.show_advanced_options),
                                    errors=errors, description_placeholders=placeholders)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Link again with the Zero account (the usual way); an activation code still works (reauth_code)."""
        entry = self._reauth_entry
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None and entry is not None:
            admin_api = entry.data.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API
            if user_input.get(CONF_CODE):
                answer, errors, placeholders = await self._activate(user_input[CONF_CODE], admin_api)
            else:
                answer, errors, placeholders = await self._login(user_input.get(CONF_USERNAME, ""), user_input.get(CONF_PASSWORD, ""), admin_api)
            if answer is not None:
                return self.async_update_reload_and_abort(entry, data=_entry_data(answer, admin_api), reason="reauth_successful")
        user_input = user_input or {}
        schema = vol.Schema({
            vol.Optional(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
            vol.Optional(CONF_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Optional(CONF_CODE, default=user_input.get(CONF_CODE, "")): str,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors, description_placeholders=placeholders)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "ZeroHomeOptionsFlow":
        return ZeroHomeOptionsFlow(config_entry)


class ZeroHomeOptionsFlow(OptionsFlow):
    """What Zero may see/control, the web interface, and external_url."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry       # not self.config_entry: works on old and new HA
        self._opts: dict[str, Any] = {**DEFAULT_OPTIONS, **(config_entry.options or {})}

    def _domains(self) -> list[str]:
        present = {eid.split(".", 1)[0] for eid in self.hass.states.async_entity_ids()}
        return sorted(present | set(self._opts.get(OPT_EXPOSE_DOMAINS) or []))

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts.update(user_input)
            if user_input[OPT_EXPOSE_MODE] == MODE_SELECT:
                return await self.async_step_select()
            return await self._after_exposure()
        schema = vol.Schema({
            vol.Required(OPT_EXPOSE_MODE, default=self._opts[OPT_EXPOSE_MODE]):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=[MODE_ALL, MODE_SELECT], translation_key=OPT_EXPOSE_MODE,
                    mode=selector.SelectSelectorMode.LIST)),
            vol.Required(OPT_REMOTE_UI, default=bool(self._opts[OPT_REMOTE_UI])): bool,
            vol.Required(OPT_SET_EXTERNAL_URL, default=bool(self._opts[OPT_SET_EXTERNAL_URL])): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (OPT_EXPOSE_AREAS, OPT_EXPOSE_DOMAINS, OPT_EXPOSE_LABELS, OPT_EXPOSE_ENTITIES):
                self._opts[key] = list(user_input.get(key) or [])
            if not any(self._opts[k] for k in (OPT_EXPOSE_AREAS, OPT_EXPOSE_DOMAINS,
                                                OPT_EXPOSE_LABELS, OPT_EXPOSE_ENTITIES)):
                errors["base"] = "nothing_selected"
            else:
                return await self._after_exposure()
        fields: dict = {
            vol.Optional(OPT_EXPOSE_AREAS, default=self._opts.get(OPT_EXPOSE_AREAS) or []):
                selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
            vol.Optional(OPT_EXPOSE_DOMAINS, default=self._opts.get(OPT_EXPOSE_DOMAINS) or []):
                selector.SelectSelector(selector.SelectSelectorConfig(
                    options=self._domains(), multiple=True, custom_value=True,
                    mode=selector.SelectSelectorMode.DROPDOWN)),
        }
        if hasattr(selector, "LabelSelector"):
            fields[vol.Optional(OPT_EXPOSE_LABELS, default=self._opts.get(OPT_EXPOSE_LABELS) or [])] = \
                selector.LabelSelector(selector.LabelSelectorConfig(multiple=True))
        fields[vol.Optional(OPT_EXPOSE_ENTITIES, default=self._opts.get(OPT_EXPOSE_ENTITIES) or [])] = \
            selector.EntitySelector(selector.EntitySelectorConfig(multiple=True))
        return self.async_show_form(step_id="select", data_schema=vol.Schema(fields), errors=errors)

    async def _after_exposure(self) -> ConfigFlowResult:
        public_url = self._entry.data.get(CONF_PUBLIC_URL) or ""
        current = self.hass.config.external_url
        if (self._opts.get(OPT_SET_EXTERNAL_URL) and current and public_url
                and current.rstrip("/") != public_url.rstrip("/")):
            return await self.async_step_external_url()
        return self._finish()

    async def async_step_external_url(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts[OPT_REPLACE_EXTERNAL_URL] = bool(user_input.get(OPT_REPLACE_EXTERNAL_URL))
            if not self._opts[OPT_REPLACE_EXTERNAL_URL]:
                self._opts[OPT_SET_EXTERNAL_URL] = False
            return self._finish()
        return self.async_show_form(
            step_id="external_url",
            data_schema=vol.Schema({vol.Required(OPT_REPLACE_EXTERNAL_URL,
                                                 default=bool(self._opts.get(OPT_REPLACE_EXTERNAL_URL))): bool}),
            description_placeholders={"current": self.hass.config.external_url or "",
                                      "public_url": self._entry.data.get(CONF_PUBLIC_URL) or ""})

    @callback
    def _finish(self) -> ConfigFlowResult:
        if self._opts.get(OPT_EXPOSE_MODE) != MODE_SELECT:
            self._opts[OPT_EXPOSE_MODE] = MODE_ALL
        return self.async_create_entry(title="", data=dict(self._opts))
