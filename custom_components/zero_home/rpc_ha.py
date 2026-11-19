"""Home Assistant side of the RPC streams: a thin backend over hass internals.

All the rules (exposure, validation, result shapes) live in ``link/rpc.py``.
No Home Assistant token is used or created: calls go straight to
``hass.services.async_call`` with a fresh system context.
"""
from __future__ import annotations

import json
from typing import Any

import voluptuous as vol
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import JSONEncoder
from homeassistant.helpers.service import async_get_all_descriptions

from .link.rpc import CODE_BAD_REQUEST, CODE_HA_ERROR, EntityMeta, RpcError

try:  # 2024.4+
    from homeassistant.helpers import floor_registry as fr
    from homeassistant.helpers import label_registry as lr
except ImportError:  # pragma: no cover - older Home Assistant
    fr = lr = None  # type: ignore[assignment]

try:
    from homeassistant.exceptions import ServiceValidationError
except ImportError:  # pragma: no cover
    ServiceValidationError = HomeAssistantError  # type: ignore[misc,assignment]


def _str_list(values) -> list[str]:
    return sorted(v for v in (values or ()) if isinstance(v, str) and v)


class HaRpcBackend:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    # -- basics -------------------------------------------------------------
    def info(self) -> dict:
        cfg = self.hass.config
        return {"version": HA_VERSION, "location_name": cfg.location_name,
                "time_zone": cfg.time_zone, "unit_system": cfg.units.as_dict()}

    def entity_ids(self) -> list[str]:
        return self.hass.states.async_entity_ids()

    def meta(self, entity_id: str) -> EntityMeta:
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None:
            return EntityMeta(entity_id)
        area = entry.area_id
        if not area and entry.device_id:
            device = dr.async_get(self.hass).async_get(entry.device_id)
            area = device.area_id if device else None
        return EntityMeta(entity_id, area, frozenset(getattr(entry, "labels", ()) or ()))

    def state(self, entity_id: str) -> dict | None:
        st = self.hass.states.get(entity_id)
        if st is None:
            return None
        # the same JSON as REST /api/states (datetimes as ISO strings)
        return json.loads(json.dumps(st.as_dict(), cls=JSONEncoder))

    def last_updated(self, entity_id: str) -> str | None:
        st = self.hass.states.get(entity_id)
        return st.last_updated.isoformat() if st else None

    async def services(self) -> dict[str, dict]:
        descriptions = json.loads(json.dumps(await async_get_all_descriptions(self.hass), cls=JSONEncoder))
        # newer Home Assistant versions leave «name» to the translations: keep the documented shape (PROTOCOL §2.3)
        for svcs in descriptions.values():
            for sid, desc in (svcs or {}).items():
                if isinstance(desc, dict) and not desc.get("name"):
                    desc["name"] = sid.replace("_", " ").capitalize()
        return descriptions

    # -- registries -----------------------------------------------------------
    def registry(self) -> dict:
        hass = self.hass
        areas = [{"area_id": a.id, "name": a.name, "floor_id": getattr(a, "floor_id", None),
                  "aliases": _str_list(a.aliases)}
                 for a in ar.async_get(hass).async_list_areas()]
        floors = []
        labels = []
        if fr is not None:
            floors = [{"floor_id": f.floor_id, "name": f.name, "level": f.level}
                      for f in fr.async_get(hass).async_list_floors()]
            labels = [{"label_id": l.label_id, "name": l.name}
                      for l in lr.async_get(hass).async_list_labels()]
        ent_reg = er.async_get(hass)
        entities: dict[str, dict] = {}
        for st in hass.states.async_all():
            entry = ent_reg.async_get(st.entity_id)
            name = None
            aliases: list[str] = []
            if entry is not None:
                name = entry.name or entry.original_name
                aliases = _str_list(entry.aliases)
            if not name:
                name = st.attributes.get("friendly_name") or st.name
            entities[st.entity_id] = {"name": name, "aliases": aliases}
        return {"areas": areas, "floors": floors, "labels": labels, "entities": entities}

    # -- service calls ----------------------------------------------------------
    async def call(self, domain: str, service: str, entity_ids: list[str],
                   data: dict, return_response: bool) -> Any:
        if not self.hass.services.has_service(domain, service):
            raise RpcError(CODE_BAD_REQUEST, f"Home Assistant has no service {domain}.{service}")
        service_data = dict(data)
        if entity_ids:
            service_data["entity_id"] = list(entity_ids)
        try:
            response = await self.hass.services.async_call(
                domain, service, service_data, blocking=True, context=Context(),
                return_response=return_response)
        except (vol.Invalid, ServiceValidationError, ServiceNotFound) as err:
            raise RpcError(CODE_BAD_REQUEST, str(err) or type(err).__name__) from err
        except HomeAssistantError as err:
            raise RpcError(CODE_HA_ERROR, str(err) or type(err).__name__) from err
        if return_response:
            return json.loads(json.dumps(response, cls=JSONEncoder))
        return None
