"""Zero Home status (read-only): the home's public address and the signed-in Zero account."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import CONF_USERNAME

from .const import CONF_DISPLAY_NAME, CONF_PUBLIC_URL
from .entity import ZeroHomeEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ZeroHomePublicUrl(entry), ZeroHomeAccount(entry)])


class ZeroHomePublicUrl(ZeroHomeEntity, SensorEntity):
    _attr_icon = "mdi:web"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "public_url")

    @property
    def native_value(self) -> str | None:
        home = (self.runtime.account or {}).get("home") or {}
        if home.get("public_url") and home.get("linked", True):
            return home["public_url"]
        welcome = self.runtime.welcome or {}
        return welcome.get("public_url") or self._entry.data.get(CONF_PUBLIC_URL)


class ZeroHomeAccount(ZeroHomeEntity, SensorEntity):
    """Who is signed in, the subscription, and where the settings are (the account page)."""
    _attr_icon = "mdi:account-circle"

    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__(entry, "account")

    @property
    def native_value(self) -> str | None:
        acc = (self.runtime.account or {}).get("account") or {}
        return acc.get("display_name") or acc.get("username") or self._entry.data.get(CONF_DISPLAY_NAME) \
            or self._entry.data.get(CONF_USERNAME)

    @property
    def extra_state_attributes(self) -> dict:
        info = self.runtime.account or {}
        acc = info.get("account") or {}
        sub = acc.get("subscription") or {}
        plan = sub.get("plan") or {}
        return {"username": acc.get("username") or self._entry.data.get(CONF_USERNAME),
                "subscription": sub.get("status"), "days_left": sub.get("days_left"), "ends_at": sub.get("ends_at"),
                "plan": plan.get("name") if isinstance(plan, dict) else plan,
                "home_status": (info.get("home") or {}).get("status"),
                "signed_in": not self.runtime.signed_out,
                "manage_url": info.get("manage_url")}
