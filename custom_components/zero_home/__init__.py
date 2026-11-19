"""Zero Home — links this Home Assistant to Zero through the Zero Home relay.

The tunnel itself lives in ``link/`` (no Home Assistant imports); this file
only wires it to Home Assistant: sessions, options, repairs and entities.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp
from homeassistant.config_entries import ConfigEntry
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
    AGENT_VERSION, CONF_AGENT_TOKEN, CONF_HOME_ID, CONF_PUBLIC_URL, CONF_RELAY_WS,
    CONF_SUBDOMAIN, CONF_ZERO_WEB_ORIGIN, DEFAULT_OPTIONS, DOMAIN, EXPOSED_DEBOUNCE_S,
    ISSUE_EXTERNAL_URL, ISSUE_PLAN, ISSUE_REVOKED, ISSUE_UI_NOT_READY, ISSUE_UNREACHABLE,
    OPT_REMOTE_UI, OPT_REPLACE_EXTERNAL_URL, OPT_SET_EXTERNAL_URL, RELAY_UNREACHABLE_AFTER_S,
    SIGNAL_STATUS, TRUSTED_PROXY_YAML, UI_CHECK_INTERVAL_S,
)
from .link.agent import Agent, AgentConfig
from .link.proxy import LocalHA
from .link.rpc import Exposure, RpcDispatcher
from .rpc_ha import HaRpcBackend

_LOG = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


def options_of(entry: ConfigEntry) -> dict:
    return {**DEFAULT_OPTIONS, **(entry.options or {})}


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
        self.options = options_of(entry)
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
        if self._exposed_timer is None:
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
            if reason in ("revoked", "plan"):
                issue = ISSUE_REVOKED if reason == "revoked" else ISSUE_PLAN
                ir.async_create_issue(
                    self.hass, DOMAIN, issue, is_fixable=False, is_persistent=False,
                    severity=ir.IssueSeverity.ERROR, translation_key=issue)
                self.entry.async_start_reauth(self.hass)
        self.signal()

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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    rt = ZeroHomeRuntime(hass, entry)
    rt.instance_id = await instance_id.async_get(hass)
    entry.runtime_data = rt

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

    async def _started(_hass: HomeAssistant) -> None:
        rt.recount()
        await rt.check_ui()

    rt._unsubs.append(async_at_started(hass, _started))
    entry.async_on_unload(rt.close)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await async_apply_external_url(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_create_background_task(hass, rt.agent.run(), f"{DOMAIN} agent")
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    rt: ZeroHomeRuntime | None = getattr(entry, "runtime_data", None)
    if rt is not None and rt.agent is not None:
        await rt.agent.stop()     # the background task itself is cancelled by HA
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for issue in (ISSUE_UI_NOT_READY, ISSUE_REVOKED, ISSUE_PLAN, ISSUE_UNREACHABLE, ISSUE_EXTERNAL_URL):
        ir.async_delete_issue(hass, DOMAIN, issue)
