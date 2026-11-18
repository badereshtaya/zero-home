"""The public address of this Home Assistant through Zero Home."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PUBLIC_URL
from .entity import ZeroHomeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ZeroHomePublicUrl(entry)])


class ZeroHomePublicUrl(ZeroHomeEntity, SensorEntity):
    _attr_icon = "mdi:web"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "public_url")

    @property
    def native_value(self) -> str | None:
        welcome = self.runtime.welcome or {}
        return welcome.get("public_url") or self._entry.data.get(CONF_PUBLIC_URL)
