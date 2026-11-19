"""Zero Home — links this Home Assistant to Zero through the Zero Home relay.

The tunnel itself lives in ``link/`` (no Home Assistant imports); this file
only wires it to Home Assistant: sessions, repairs and entities.

1.3.0: no settings in Home Assistant. The integration signs in to the Zero account (password or QR, config_flow.py),
gets its relay key with that device token (home_link_activate) and reads what Zero may see and whether the web
interface opens from outside from the account page (ha_agent_config, every 60 s). It stays signed in until the
account signs it out: then every call answers 401 → the link stops and Home Assistant asks to sign in again.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.const import EVENT_STATE_CHANGED, Platform
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import instance_id
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from datetime import timedelta

from .const import (
    CONF_ADMIN_API, CONF_DEVICE_TOKEN, CONFIG_REFRESH_S, DEFAULT_ADMIN_API, ISSUE_SIGNED_OUT, RELAY_KEYS,
    AGENT_VERSION, CONF_AGENT_TOKEN, CONF_HOME_ID, CONF_PUBLIC_URL, CONF_RELAY_WS,
    CONF_SUBDOMAIN, CONF_ZERO_WEB_ORIGIN, DEFAULT_OPTIONS, DOMAIN, EXPOSED_DEBOUNCE_S,
    ISSUE_EXTERNAL_URL, ISSUE_PLAN, ISSUE_REVOKED, ISSUE_UI_NOT_READY, ISSUE_UNREACHABLE,
    OPT_REMOTE_UI, OPT_REPLACE_EXTERNAL_URL, OPT_SET_EXTERNAL_URL, RELAY_UNREACHABLE_AFTER_S,
    SIGNAL_STATUS, TRUSTED_PROXY_YAML, UI_CHECK_INTERVAL_S,
)
from .link import account as A
from .link.agent import Agent, AgentConfig
from .link.proxy import LocalHA
from .link.rpc import Exposure, RpcDispatcher
from .rpc_ha import HaRpcBackend

_LOG = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def options_of(entry: ConfigEntry) -> dict:
    """The settings now come from the account page (rt.options); this is only the starting point."""
    rt = getattr(entry, "runtime_data", None)
    if rt is not None and getattr(rt, "options", None):
        return dict(rt.options)
    return dict(DEFAULT_OPTIONS)


def local_base_url(hass: HomeAssistant) -> str:
    """Home Assistant's own HTTP server, as the integration reaches it (§3.1)."""
    port = getattr(hass.http, "server_port", None) or 8123
    use_ssl = bool(getattr(getattr(hass.config, "api", None), "use_ssl", False))
    host = "127.0.0.1"
    hosts = getattr(hass.http, "server_host", None)
    if hosts:
        hosts = [hosts] if isinstance(hosts, str) else list(hosts)
        if not any(h in ("0.0.0.0", "::", "127.0.0.1", "::1", "localhost") for h in hosts):
            host = hosts[0]   # HA listens only on a specific address
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{'https' if use_ssl else 'http'}://{host}:{port}"


async def direct_mode_blocker(hass: HomeAssistant) -> str:
    """'' when direct mode is safe; else why not (shown in the repair issue)."""
    for prov in getattr(hass.auth, "auth_providers", []) or []:
        if getattr(prov, "type", "") == "trusted_networks":
            return "trusted_networks"
    try:
        users = await hass.auth.async_get_users()
    except Exception:  # noqa: BLE001 - unknown = not safe
        return "users"
    names = [u.name or "?" for u in users
             if u.is_active and not u.system_generated and getattr(u, "local_only", False)]
    return ("local_only: " + ", ".join(names)) if names else ""


class ZeroHomeRuntime:
    """Everything one config entry keeps while it is loaded."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options = dict(DEFAULT_OPTIONS)
        self.account: dict = {}           # ha_agent_config: account, home, manage_url
        self.config_version = ""
        self.refresh_every = CONFIG_REFRESH_S
        self.signed_out = False
        self._last_new_key = 0.0
        self._closed = False
        self.connected = False
        self.ui_ready: bool | None = None
        self.ui_mode = ""                 # "forwarded" | "direct"
        self.ui_blocker = ""              # direct mode not safe: "trusted_networks" | "local_only:<names>"
        self.exposed = 0
        self.welcome: dict | None = None
        self.last_event: str | None = None
        self.last_error: str | None = None
        self.last_connected: float | None = None
        self.stop_reason: str | None = None
        self.instance_id = ""
        self.agent: Agent | None = None
        self.dispatcher: RpcDispatcher | None = None
        self.local: LocalHA | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._unreachable_timer: CALLBACK_TYPE | None = None
        self._exposed_timer: CALLBACK_TYPE | None = None

    # -- state for hello / state messages ---------------------------------
    def agent_state(self) -> dict:
        return {"ha_version": HA_VERSION, "instance_id": self.instance_id,
                "remote_ui": bool(self.options.get(OPT_REMOTE_UI, False)),
                "ui_ready": bool(self.ui_ready), "exposed": self.exposed,
                "location_name": self.hass.config.location_name}

    @callback
    def signal(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_STATUS.format(self.entry.entry_id))

    # -- exposed count ---------------------------------------------------------
    @callback
    def recount(self, *_: Any) -> None:
        if self._exposed_timer is not None:      # called directly while a debounce is pending: drop it
            self._exposed_timer()
        self._exposed_timer = None
        if self.dispatcher is None:
            return
        n = self.dispatcher.exposed_count()
        if n != self.exposed:
            self.exposed = n
            if self.agent:
                self.agent.notify_state()
            self.signal()

    @callback
    def recount_later(self, *_: Any) -> None:
        if self._exposed_timer is None and not self._closed:
            self._exposed_timer = async_call_later(self.hass, EXPOSED_DEBOUNCE_S, self.recount)

    @callback
    def on_state_changed(self, event: Event) -> None:
        # only entities appearing or disappearing change the count
        if event.data.get("old_state") is None or event.data.get("new_state") is None:
            self.recount_later()

    # -- ui_ready (§3.3) ---------------------------------------------------------
    # Newer Home Assistant versions no longer take `http: trusted_proxies` from configuration.yaml (2026-11-19).
    # When it does not trust us, the web interface still works in «direct mode»: no forwarded headers, so every
    # visitor looks like this integration (127.0.0.1). That is only safe when nothing in Home Assistant gives a
    # «home network» visitor more rights: no «trusted networks» login and no user limited to the local network.
    async def check_ui(self, *_: Any) -> None:
        if self.local is None:
            return
        trusted = await self.local.check_ui_ready()
        if trusted is None:
            return
        self.local.forward_client = trusted
        self.ui_mode = "forwarded" if trusted else "direct"
        self.ui_blocker = "" if trusted else await direct_mode_blocker(self.hass)
        ready = trusted or not self.ui_blocker
        if ready != self.ui_ready:
            self.ui_ready = ready
            if self.agent:
                self.agent.notify_state()
            self.signal()
        if ready or not self.options.get(OPT_REMOTE_UI, False):
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UI_NOT_READY)
        else:
            ir.async_create_issue(
                self.hass, DOMAIN, ISSUE_UI_NOT_READY, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_UI_NOT_READY,
                translation_placeholders={"yaml": TRUSTED_PROXY_YAML, "blocker": self.ui_blocker})

    # -- agent events -------------------------------------------------------------
    @callback
    def on_agent_event(self, name: str, info: dict) -> None:
        self.last_event = name
        if name == "connected":
            self.connected = True
            self.welcome = info
            self.last_connected = time.time()
            self.last_error = None
            self._cancel_unreachable()
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNREACHABLE)
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_REVOKED)
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_PLAN)
            self.hass.async_create_task(self.refresh_config())
        elif name == "disconnected":
            self.connected = False
            self.last_error = info.get("error") or info.get("reason")
            if self._unreachable_timer is None:
                self._unreachable_timer = async_call_later(
                    self.hass, RELAY_UNREACHABLE_AFTER_S, self._unreachable)
        elif name == "stopped":
            self.connected = False
            reason = info.get("reason")
            self.stop_reason = reason
            self._cancel_unreachable()
            if reason == "revoked" and not self.signed_out:
                self.hass.async_create_task(self.new_relay_key())
            elif reason == "plan":
                ir.async_create_issue(
                    self.hass, DOMAIN, ISSUE_PLAN, is_fixable=False, is_persistent=False,
                    severity=ir.IssueSeverity.ERROR, translation_key=ISSUE_PLAN)
        self.signal()

    # -- the account (1.3.0) -------------------------------------------------------
    @property
    def admin_api(self) -> str:
        return self.entry.data.get(CONF_ADMIN_API) or DEFAULT_ADMIN_API

    @property
    def token(self) -> str:
        return self.entry.data.get(CONF_DEVICE_TOKEN) or ""

    def apply_config(self, cfg: dict) -> bool:
        """ha_agent_config → options/exposure; True when something that matters changed."""
        self.account = {k: cfg.get(k) for k in ("account", "home", "manage_url")}
        try:
            self.refresh_every = max(30, int(cfg.get("refresh_every") or CONFIG_REFRESH_S))
        except (TypeError, ValueError):
            self.refresh_every = CONFIG_REFRESH_S
        version = str(cfg.get("version") or "")
        new = A.options_from_config(cfg)
        changed = new != self.options or version != self.config_version
        self.config_version = version
        if changed:
            self.options = new
            if self.dispatcher is not None:
                self.dispatcher.exposure = Exposure.from_options(new)
        return changed

    async def refresh_config(self, *_: Any) -> None:
        if self.signed_out:
            return
        try:
            cfg = await A.agent_config(async_get_clientsession(self.hass), self.admin_api, self.token)
        except A.Revoked:
            await self.sign_out()
            return
        except A.AccountError as err:
            _LOG.debug("Zero Home: settings not read (%s)", err)
            return
        if self.apply_config(cfg):
            self.recount()
            await self.check_ui()
            if self.agent:
                self.agent.notify_state()
            await async_apply_external_url(self.hass, self.entry)
        self.signal()

    async def sign_out(self) -> None:
        """The account signed this integration out: stop the link and ask to sign in again (never by itself)."""
        if self.signed_out:
            return
        self.signed_out = True
        self.connected = False
        self.stop_reason = "signed_out"
        if self.agent is not None:
            await self.agent.stop()
        ir.async_create_issue(self.hass, DOMAIN, ISSUE_SIGNED_OUT, is_fixable=False, is_persistent=False,
                              severity=ir.IssueSeverity.ERROR, translation_key=ISSUE_SIGNED_OUT)
        self.entry.async_start_reauth(self.hass)
        self.signal()

    async def new_relay_key(self) -> None:
        """The relay refused the key (revoked / replaced / bad key): ask for a new one with the device token
        (at most once every 10 minutes — a relay that keeps refusing must not make the integration loop)."""
        now = time.time()
        if now - self._last_new_key < 600:
            ir.async_create_issue(self.hass, DOMAIN, ISSUE_REVOKED, is_fixable=False, is_persistent=False,
                                  severity=ir.IssueSeverity.ERROR, translation_key=ISSUE_REVOKED)
            return
        self._last_new_key = now
        try:
            creds = await A.activate(async_get_clientsession(self.hass), self.admin_api, self.token,
                                     instance_id=self.instance_id, ha_version=HA_VERSION, agent_version=AGENT_VERSION)
        except A.Revoked:
            await self.sign_out()
            return
        except A.AccountError as err:
            issue = ISSUE_PLAN if err.kind == A.ERR_PLAN else ISSUE_REVOKED
            ir.async_create_issue(self.hass, DOMAIN, issue, is_fixable=False, is_persistent=False,
                                  severity=ir.IssueSeverity.ERROR, translation_key=issue)
            return
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, **{k: creds.get(k) or "" for k in RELAY_KEYS}})
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    @callback
    def _unreachable(self, *_: Any) -> None:
        self._unreachable_timer = None
        if not self.connected:
            ir.async_create_issue(
                self.hass, DOMAIN, ISSUE_UNREACHABLE, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_UNREACHABLE,
                translation_placeholders={"error": str(self.last_error or "-")})

    @callback
    def _cancel_unreachable(self) -> None:
        if self._unreachable_timer is not None:
            self._unreachable_timer()
            self._unreachable_timer = None

    @callback
    def close(self) -> None:
        self._closed = True
        self._cancel_unreachable()
        if self._exposed_timer is not None:
            self._exposed_timer()
            self._exposed_timer = None
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()


async def async_apply_external_url(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Point Home Assistant's external_url at the public address (option)."""
    opts = options_of(entry)
    public_url = entry.data.get(CONF_PUBLIC_URL)
    if not public_url or not opts.get(OPT_SET_EXTERNAL_URL, False):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_EXTERNAL_URL)
        return
    current = hass.config.external_url
    if current and current.rstrip("/") == public_url.rstrip("/"):
        ir.async_delete_issue(hass, DOMAIN, ISSUE_EXTERNAL_URL)
        return
    if current and not opts.get(OPT_REPLACE_EXTERNAL_URL, False):
        ir.async_create_issue(
            hass, DOMAIN, ISSUE_EXTERNAL_URL, is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.WARNING, translation_key=ISSUE_EXTERNAL_URL,
            translation_placeholders={"current": current, "public_url": public_url})
        return
    await hass.config.async_update(external_url=public_url)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_EXTERNAL_URL)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.x entries (code / password activation, local options) → 1.3.0: the relay key is kept; the integration asks
    to sign in once (account or QR) to get its device token, then everything comes from the account page."""
    if entry.version < 2:
        hass.config_entries.async_update_entry(entry, version=2, options={})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    rt = ZeroHomeRuntime(hass, entry)
    rt.instance_id = await instance_id.async_get(hass)
    entry.runtime_data = rt
    if not rt.token:
        raise ConfigEntryAuthFailed("sign in to the Zero account")
    session = async_get_clientsession(hass)
    try:
        cfg = await A.agent_config(session, rt.admin_api, rt.token)
    except A.Revoked as err:
        raise ConfigEntryAuthFailed("signed out from the account") from err
    except A.AccountError as err:
        raise ConfigEntryNotReady(f"Zero: {err.kind}") from err
    rt.apply_config(cfg)
    ir.async_delete_issue(hass, DOMAIN, ISSUE_SIGNED_OUT)
    if not entry.data.get(CONF_AGENT_TOKEN) or not entry.data.get(CONF_RELAY_WS):
        try:
            creds = await A.activate(session, rt.admin_api, rt.token, instance_id=rt.instance_id,
                                     ha_version=HA_VERSION, agent_version=AGENT_VERSION)
        except A.Revoked as err:
            raise ConfigEntryAuthFailed("signed out from the account") from err
        except A.AccountError as err:
            if err.kind == A.ERR_PLAN:
                ir.async_create_issue(hass, DOMAIN, ISSUE_PLAN, is_fixable=False, is_persistent=False,
                                      severity=ir.IssueSeverity.ERROR, translation_key=ISSUE_PLAN)
            raise ConfigEntryNotReady(f"Zero home link: {err.kind}") from err
        hass.config_entries.async_update_entry(entry, data={**entry.data, **{k: creds.get(k) or "" for k in RELAY_KEYS}})

    public_host = urlparse(entry.data.get(CONF_PUBLIC_URL) or "").hostname or (
        f"{entry.data.get(CONF_SUBDOMAIN)}.home.eshtaya.ps" if entry.data.get(CONF_SUBDOMAIN) else "")
    local_session = async_create_clientsession(
        hass, verify_ssl=False, auto_cleanup=True,
        cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False)
    rt.local = LocalHA(local_session, local_base_url(hass), public_host=public_host,
                       zero_web_origin=entry.data.get(CONF_ZERO_WEB_ORIGIN) or "")
    rt.dispatcher = RpcDispatcher(HaRpcBackend(hass), Exposure.from_options(rt.options))
    rt.exposed = rt.dispatcher.exposed_count()
    rt.agent = Agent(
        AgentConfig(relay_ws=entry.data[CONF_RELAY_WS], home_id=entry.data[CONF_HOME_ID],
                    agent_token=entry.data[CONF_AGENT_TOKEN], agent_version=AGENT_VERSION),
        relay_session=async_get_clientsession(hass), local=rt.local, rpc=rt.dispatcher,
        state=rt.agent_state, on_event=rt.on_agent_event)

    rt._unsubs.append(hass.bus.async_listen(EVENT_STATE_CHANGED, rt.on_state_changed))
    rt._unsubs.append(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, rt.recount_later))
    rt._unsubs.append(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, rt.recount_later))
    rt._unsubs.append(async_track_time_interval(
        hass, rt.check_ui, timedelta(seconds=UI_CHECK_INTERVAL_S)))
    rt._unsubs.append(async_track_time_interval(
        hass, rt.refresh_config, timedelta(seconds=rt.refresh_every)))

    async def _started(_hass: HomeAssistant) -> None:
        rt.recount()
        await rt.check_ui()

    rt._unsubs.append(async_at_started(hass, _started))
    entry.async_on_unload(rt.close)

    await async_apply_external_url(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(hass, rt.agent.run(), f"{DOMAIN} agent")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    rt: ZeroHomeRuntime | None = getattr(entry, "runtime_data", None)
    if rt is not None and rt.agent is not None:
        await rt.agent.stop()     # the background task itself is cancelled by HA
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for issue in (ISSUE_UI_NOT_READY, ISSUE_REVOKED, ISSUE_PLAN, ISSUE_UNREACHABLE, ISSUE_EXTERNAL_URL, ISSUE_SIGNED_OUT):
        ir.async_delete_issue(hass, DOMAIN, issue)
