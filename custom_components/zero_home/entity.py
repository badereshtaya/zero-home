"""Shared base for the Zero Home entities."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import AGENT_VERSION, DOMAIN, MANAGE_URL, NAME, SIGNAL_STATUS


class ZeroHomeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=NAME, manufacturer="Zero",
            model="Zero Home Link", sw_version=AGENT_VERSION,
            entry_type=DeviceEntryType.SERVICE,
            # every setting lives on the account page of Zero's site
            configuration_url=MANAGE_URL)

    @property
    def runtime(self):
        return self._entry.runtime_data

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_STATUS.format(self._entry.entry_id), self.async_write_ha_state))
