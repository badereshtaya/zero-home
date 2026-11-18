"""Diagnostics for Zero Home (the home key is never included)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import AGENT_VERSION, CONF_AGENT_TOKEN

TO_REDACT = {CONF_AGENT_TOKEN, "Authorization", "token"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    rt = getattr(entry, "runtime_data", None)
    status: dict[str, Any] = {}
    if rt is not None:
        status = {
            "agent_version": AGENT_VERSION,
            "connected": rt.connected,
            "ui_ready": rt.ui_ready,
            "exposed": rt.exposed,
            "last_event": rt.last_event,
            "last_error": rt.last_error,
            "last_connected": rt.last_connected,
            "stop_reason": rt.stop_reason,
            "welcome": rt.welcome,
            "local_base_url": rt.local.base_url if rt.local else None,
        }
    return {
        "data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "status": async_redact_data(status, TO_REDACT),
    }
