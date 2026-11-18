"""«Zero Home» connectivity sensor: is the link to the relay up?"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ZeroHomeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ZeroHomeConnected(entry)])


class ZeroHomeConnected(ZeroHomeEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = None   # the device's own name: «Zero Home»

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "connected")

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.connected)

    @property
    def extra_state_attributes(self) -> dict:
        rt = self.runtime
        return {"ui_ready": rt.ui_ready, "exposed": rt.exposed,
                "last_error": rt.last_error, "stop_reason": rt.stop_reason}
