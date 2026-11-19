"""Sign-in for Zero Home (1.3.0): the Zero account (username + password) or a QR code approved from the account page.

Nothing else is set here: what Zero may see, the web interface from outside and everything about the home are
managed on the account page of Zero's site (the integration reads them with ha_agent_config). There is no options
flow and no sign-out — the integration stays signed in until the account signs it out.
"""
from __future__ import annotations

import asyncio
import io
import secrets
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, __version__ as HA_VERSION
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AGENT_VERSION, CONF_ADMIN_API, CONF_DEVICE_TOKEN, CONF_DISPLAY_NAME, CONF_USER_ID, DEFAULT_ADMIN_API, DOMAIN,
    NAME, QR_PATH,
)
from .link import account as A

PAIR_GIVE_UP_S = 600           # one QR screen keeps renewing its code for at most 10 minutes


def _device(hass) -> tuple[str, str]:
    loc = (hass.config.location_name or "").strip()
    return (f"Home Assistant — {loc}" if loc else "Home Assistant"), f"Home Assistant {HA_VERSION}"


def qr_svg(payload: str) -> bytes:
    import segno   # manifest requirement
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111111", light="#ffffff")
    return buf.getvalue()


class ZeroHomeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None
        self._admin_api = DEFAULT_ADMIN_API
        self._pair: dict | None = None
        self._pair_task: asyncio.Task | None = None
        self._pair_result: dict | None = None
        self._qr_key = ""
        self._error = ""
        self._pair_since = 0.0

    # ── shared ────────────────────────────────────────────────────────────
    async def _signed_in(self, token: str, user: dict) -> ConfigFlowResult:
        data = {CONF_ADMIN_API: self._admin_api, CONF_DEVICE_TOKEN: token,
                CONF_USERNAME: str(user.get("username") or ""), CONF_DISPLAY_NAME: str(user.get("display_name") or ""),
                CONF_USER_ID: user.get("id")}
        if self._reauth_entry is not None:
            # same account only: a different person signing in here would take over this home
            old = self._reauth_entry.data.get(CONF_USERNAME)
            if old and data[CONF_USERNAME] and old.lower() != data[CONF_USERNAME].lower():
                return self.async_abort(reason="wrong_account")
            # the relay key stays; if it was refused it is renewed with the new token at start
            return self.async_update_reload_and_abort(self._reauth_entry, data={**self._reauth_entry.data, **data},
                                                      reason="reauth_successful")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        title = f"{NAME} — {data[CONF_DISPLAY_NAME] or data[CONF_USERNAME]}"
        return self.async_create_entry(title=title, data=data)

    # ── start: account or QR ──────────────────────────────────────────────
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._reauth_entry is None and self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_show_menu(step_id="user", menu_options=["login", "qr"])

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if self._reauth_entry is not None:
            self._admin_api = self._reauth_entry.data.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API
        return self.async_show_menu(step_id="reauth", menu_options=["login", "qr"])

    # ── username + password ────────────────────────────────────────────────
    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"server_message": ""}
        if user_input is not None:
            if self.show_advanced_options and user_input.get(CONF_ADMIN_API):
                self._admin_api = user_input[CONF_ADMIN_API].strip()
            device, platform = _device(self.hass)
            try:
                ans = await A.login(async_get_clientsession(self.hass), self._admin_api, user_input[CONF_USERNAME],
                                    user_input[CONF_PASSWORD], device=device, platform=platform, app_version=AGENT_VERSION)
                return await self._signed_in(str(ans["token"]), ans.get("user") or {"username": user_input[CONF_USERNAME]})
            except A.AccountError as err:
                errors["base"] = err.kind if err.kind != "revoked" else A.ERR_AUTH
                placeholders["server_message"] = err.message
        fields: dict = {
            vol.Required(CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }
        if self.show_advanced_options:
            fields[vol.Optional(CONF_ADMIN_API, default=self._admin_api)] = str
        return self.async_show_form(step_id="login", data_schema=vol.Schema(fields), errors=errors,
                                    description_placeholders=placeholders)

    # ── QR: the account page approves this Home Assistant ─────────────────────
    async def async_step_qr(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._pair_since:
            self._pair_since = time.monotonic()
        if self._pair_task is None:
            device, platform = _device(self.hass)
            try:
                self._pair = await A.pair_start(async_get_clientsession(self.hass), self._admin_api,
                                                device_name=device, platform=platform, app_version=AGENT_VERSION)
            except A.AccountError as err:
                self._error = err.kind
                return self.async_show_progress_done(next_step_id="qr_failed")
            self._show_qr(self._pair)
            self._pair_task = self.hass.async_create_task(self._wait_pair(), eager_start=False)
        if not self._pair_task.done():
            p = self._pair or {}
            return self.async_show_progress(
                step_id="qr", progress_action="wait_qr", progress_task=self._pair_task,
                description_placeholders={"qr": f"{QR_PATH}/{self._qr_key}.svg", "code": str(p.get("code_display") or ""),
                                          "site": "zero.eshtaya.ps/pair", "minutes": str(max(1, int(p.get("expires_in") or 180) // 60))})
        self._pair_task = None
        res = self._pair_result or {}
        if res.get("status") == "renew" and time.monotonic() - self._pair_since < PAIR_GIVE_UP_S:
            # the code expired while the screen was open: a new code, a new QR on the same screen
            self._pair = self._pair_result = None
            return await self.async_step_qr()
        if res.get("status") == "approved":
            return self.async_show_progress_done(next_step_id="qr_ok")
        self._error = str(res.get("status") or "expired")
        return self.async_show_progress_done(next_step_id="qr_failed")

    def _show_qr(self, pair: dict) -> None:
        data = self.hass.data.setdefault(DOMAIN, {})
        if not data.get("qr_view"):
            from .qr_view import ZeroHomeQrView
            self.hass.http.register_view(ZeroHomeQrView())
            data["qr_view"] = True
        store = data.setdefault("qr", {})
        if self._qr_key:
            store.pop(self._qr_key, None)
        self._qr_key = secrets.token_urlsafe(16)
        store[self._qr_key] = qr_svg(str(pair.get("qr_payload") or pair.get("qr_url") or ""))

    async def _wait_pair(self) -> None:
        """Poll until the account approves or refuses; an expired code ends the wait with «renew»."""
        session = async_get_clientsession(self.hass)
        p = self._pair or {}
        while True:
            await asyncio.sleep(max(1.0, float(p.get("poll_every") or 2)))
            try:
                res = await A.pair_poll(session, self._admin_api, str(p.get("pair_id")), str(p.get("poll_secret")))
            except A.AccountError:
                continue                         # a network hiccup: keep trying
            st = res.get("status")
            if st in ("approved", "denied"):
                self._pair_result = res
                return
            if st in ("expired", "claimed", "unknown"):
                self._pair_result = {"status": "renew"}
                return

    def async_remove(self) -> None:
        """The flow was closed: stop polling, drop the QR image."""
        if self._pair_task is not None and not self._pair_task.done():
            self._pair_task.cancel()
        if self._qr_key:
            self.hass.data.get(DOMAIN, {}).get("qr", {}).pop(self._qr_key, None)

    async def async_step_qr_ok(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        res = self._pair_result or {}
        token = str(res.get("device_token") or (res.get("login") or {}).get("token") or "")
        user = res.get("user") or (res.get("login") or {}).get("user") or {}
        if self._qr_key:
            self.hass.data.get(DOMAIN, {}).get("qr", {}).pop(self._qr_key, None)
        if not token:
            self._error = A.ERR_ANSWER
            return await self.async_step_qr_failed()
        return await self._signed_in(token, user)

    async def async_step_qr_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._pair = self._pair_result = None
            self._pair_since = 0.0
            return await self.async_step_qr()
        return self.async_show_form(step_id="qr_failed", data_schema=vol.Schema({}),
                                    errors={"base": self._error if self._error in (
                                        "denied", "expired", A.ERR_RATE, A.ERR_CONNECT, A.ERR_ANSWER) else "expired"})
