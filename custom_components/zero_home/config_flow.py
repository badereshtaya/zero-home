"""Config, reauth and options flows for Zero Home — a short wizard (1.2.0, 2026-11-19).

Set-up:   welcome → sign in (Zero account, or a ZH- code) → what Zero may see → the web interface from
          outside (off unless the person turns it on) → summary.
Options:  the same three choices again (what Zero sees → outside → summary with the live status).
"""
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
    OPT_EXPOSE_ENTITIES, OPT_EXPOSE_LABELS, OPT_EXPOSE_MODE, OPT_EXPOSE_UI, OPT_REMOTE_UI,
    OPT_REPLACE_EXTERNAL_URL, OPT_SET_EXTERNAL_URL,
)
from .link.activation import ERR_FORMAT, ActivationError, activate, activate_login, normalize_code

# what Zero may see — the wizard's four simple choices (stored as expose_mode all|select + the lists)
SEE_ALL, SEE_ROOMS, SEE_TYPES, SEE_CUSTOM = "all", "rooms", "types", "custom"
SEE_CHOICES = [SEE_ALL, SEE_ROOMS, SEE_TYPES, SEE_CUSTOM]
# the device types offered first in «by type» (anything present in this home is offered too)
COMMON_DOMAINS = ["light", "switch", "climate", "fan", "cover", "media_player", "lock", "scene", "script",
                  "sensor", "binary_sensor", "vacuum", "water_heater", "humidifier", "alarm_control_panel"]
ADVANCED_EXPOSURE = [OPT_EXPOSE_AREAS, OPT_EXPOSE_DOMAINS, OPT_EXPOSE_LABELS, OPT_EXPOSE_ENTITIES]


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


class _Wizard:
    """The steps shared by set-up and options: what Zero sees, the interface from outside."""

    hass: Any
    _opts: dict[str, Any]

    def _domains(self) -> list[str]:
        present = {eid.split(".", 1)[0] for eid in self.hass.states.async_entity_ids()}
        first = [d for d in COMMON_DOMAINS if d in present]
        rest = sorted((present | set(self._opts.get(OPT_EXPOSE_DOMAINS) or [])) - set(first))
        return first + rest

    def _see_now(self) -> str:
        if self._opts.get(OPT_EXPOSE_MODE) != MODE_SELECT:
            return SEE_ALL
        ui = self._opts.get(OPT_EXPOSE_UI)
        return ui if ui in SEE_CHOICES else SEE_CUSTOM

    def _see_schema(self) -> vol.Schema:
        return vol.Schema({vol.Required("see", default=self._see_now()): selector.SelectSelector(
            selector.SelectSelectorConfig(options=SEE_CHOICES, translation_key="see",
                                          mode=selector.SelectSelectorMode.LIST))})

    def _set_see(self, choice: str, **lists: list) -> None:
        for k in ADVANCED_EXPOSURE:
            self._opts[k] = list(lists.get(k) or [])
        self._opts[OPT_EXPOSE_UI] = choice
        self._opts[OPT_EXPOSE_MODE] = MODE_ALL if choice == SEE_ALL else MODE_SELECT

    async def _see_step(self, step_id: str, user_input: dict | None):
        if user_input is not None:
            choice = user_input.get("see", SEE_ALL)
            if choice == SEE_ALL:
                self._set_see(SEE_ALL)
                return await self.async_step_outside()
            return await getattr(self, f"async_step_see_{choice}")()
        return self.async_show_form(step_id=step_id, data_schema=self._see_schema(), last_step=False)

    async def async_step_see_rooms(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            areas = list(user_input.get(OPT_EXPOSE_AREAS) or [])
            if areas:
                self._set_see(SEE_ROOMS, **{OPT_EXPOSE_AREAS: areas})
                return await self.async_step_outside()
            errors["base"] = "nothing_selected"
        schema = vol.Schema({vol.Optional(OPT_EXPOSE_AREAS, default=self._opts.get(OPT_EXPOSE_AREAS) or []):
                             selector.AreaSelector(selector.AreaSelectorConfig(multiple=True))})
        return self.async_show_form(step_id="see_rooms", data_schema=schema, errors=errors, last_step=False)

    async def async_step_see_types(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            doms = list(user_input.get(OPT_EXPOSE_DOMAINS) or [])
            if doms:
                self._set_see(SEE_TYPES, **{OPT_EXPOSE_DOMAINS: doms})
                return await self.async_step_outside()
            errors["base"] = "nothing_selected"
        default = self._opts.get(OPT_EXPOSE_DOMAINS) or [d for d in ("light", "switch", "climate", "cover", "fan")
                                                          if d in self._domains()]
        schema = vol.Schema({vol.Optional(OPT_EXPOSE_DOMAINS, default=default): selector.SelectSelector(
            selector.SelectSelectorConfig(options=self._domains(), multiple=True, custom_value=True,
                                          mode=selector.SelectSelectorMode.DROPDOWN))})
        return self.async_show_form(step_id="see_types", data_schema=schema, errors=errors, last_step=False)

    async def async_step_see_custom(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            lists = {k: list(user_input.get(k) or []) for k in ADVANCED_EXPOSURE}
            if any(lists.values()):
                self._set_see(SEE_CUSTOM, **lists)
                return await self.async_step_outside()
            errors["base"] = "nothing_selected"
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
        return self.async_show_form(step_id="see_custom", data_schema=vol.Schema(fields), errors=errors, last_step=False)

    # the web interface from outside: an explicit choice, OFF unless the person turns it on
    async def async_step_outside(self, user_input: dict | None = None):
        if user_input is not None:
            on = bool(user_input.get(OPT_REMOTE_UI))
            self._opts[OPT_REMOTE_UI] = on
            # the official apps use the public address only when the interface is open from outside
            self._opts[OPT_SET_EXTERNAL_URL] = on
            if not on:
                self._opts[OPT_REPLACE_EXTERNAL_URL] = False
            return await self.async_step_done()
        schema = vol.Schema({vol.Required(OPT_REMOTE_UI, default=bool(self._opts.get(OPT_REMOTE_UI, False))): bool})
        return self.async_show_form(step_id="outside", data_schema=schema, last_step=False,
                                    description_placeholders={"public_url": self._public_url() or "—"})

    def _public_url(self) -> str:
        raise NotImplementedError

    def _summary(self) -> dict[str, str]:
        see = self._see_now()
        parts = {SEE_ROOMS: self._opts.get(OPT_EXPOSE_AREAS), SEE_TYPES: self._opts.get(OPT_EXPOSE_DOMAINS)}
        detail = ", ".join(str(x) for x in (parts.get(see) or []))
        return {"public_url": self._public_url() or "—", "see": see, "see_detail": detail or "—",
                "outside": "on" if self._opts.get(OPT_REMOTE_UI) else "off"}


class ZeroHomeConfigFlow(_Wizard, ConfigFlow, domain=DOMAIN):
    """Two ways to link: the Zero account itself (username + password — the address is the account's username)
    or an activation code from the account page."""
    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None
        self._answer: dict | None = None
        self._admin_api = DEFAULT_ADMIN_API
        self._opts = dict(DEFAULT_OPTIONS)

    def _public_url(self) -> str:
        return (self._answer or {}).get("public_url") or ""

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

    async def _linked(self, answer: dict, admin_api: str) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        self._answer, self._admin_api = answer, admin_api
        return await self.async_step_see()

    # 1. welcome
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return await self.async_step_method()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}), last_step=False)

    # 2. sign in
    async def async_step_method(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="method", menu_options=["login", "code"])

    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None:
            admin_api = (user_input.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API).strip()
            answer, errors, placeholders = await self._login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], admin_api)
            if answer is not None:
                return await self._linked(answer, admin_api)
        return self.async_show_form(step_id="login", data_schema=self._login_schema(user_input, with_api=self.show_advanced_options),
                                    errors=errors, description_placeholders=placeholders, last_step=False)

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None:
            admin_api = (user_input.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API).strip()
            answer, errors, placeholders = await self._activate(user_input[CONF_CODE], admin_api)
            if answer is not None:
                return await self._linked(answer, admin_api)
        return self.async_show_form(step_id="code", data_schema=self._code_schema(user_input, with_api=self.show_advanced_options),
                                    errors=errors, description_placeholders=placeholders, last_step=False)

    # 3. what Zero may see (+ see_rooms / see_types / see_custom), 4. outside (in _Wizard)
    async def async_step_see(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._see_step("see", user_input)

    # 5. summary
    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None and self._answer is not None:
            title = f"{NAME} — {self._answer.get('subdomain')}"
            return self.async_create_entry(title=title, data=_entry_data(self._answer, self._admin_api), options=dict(self._opts))
        return self.async_show_form(step_id="done", data_schema=vol.Schema({}), description_placeholders=self._summary())

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Link again with the Zero account (the usual way); an activation code still works."""
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


class ZeroHomeOptionsFlow(_Wizard, OptionsFlow):
    """The same simple steps: what Zero sees → the interface from outside → summary (with the live status)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry       # not self.config_entry: works on old and new HA
        self._opts: dict[str, Any] = {**DEFAULT_OPTIONS, **(config_entry.options or {})}

    def _public_url(self) -> str:
        return self._entry.data.get(CONF_PUBLIC_URL) or ""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._see_step("init", user_input)

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            public_url = self._public_url()
            current = self.hass.config.external_url
            if (self._opts.get(OPT_SET_EXTERNAL_URL) and current and public_url
                    and current.rstrip("/") != public_url.rstrip("/")):
                return await self.async_step_external_url()
            return self.async_create_entry(title="", data=dict(self._opts))
        rt = getattr(self._entry, "runtime_data", None)
        ph = self._summary()
        ph["status"] = "online" if getattr(rt, "connected", False) else "offline"
        ph["ui_mode"] = getattr(rt, "ui_mode", "") or "—"
        return self.async_show_form(step_id="done", data_schema=vol.Schema({}), description_placeholders=ph)

    async def async_step_external_url(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._opts[OPT_REPLACE_EXTERNAL_URL] = bool(user_input.get(OPT_REPLACE_EXTERNAL_URL))
            if not self._opts[OPT_REPLACE_EXTERNAL_URL]:
                self._opts[OPT_SET_EXTERNAL_URL] = False
            return self.async_create_entry(title="", data=dict(self._opts))
        return self.async_show_form(
            step_id="external_url",
            data_schema=vol.Schema({vol.Required(OPT_REPLACE_EXTERNAL_URL,
                                                 default=bool(self._opts.get(OPT_REPLACE_EXTERNAL_URL))): bool}),
            description_placeholders={"current": self.hass.config.external_url or "",
                                      "public_url": self._public_url()})
