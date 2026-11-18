"""RPC streams (PROTOCOL.md §2.3): Zero controls the home without any Home
Assistant token, and only ever sees or touches EXPOSED entities.

``RpcDispatcher`` holds every rule (exposure, validation, result shapes);
the backend is a thin adapter over Home Assistant internals (``rpc_ha.py``)
or a fake in the tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

CODE_NOT_EXPOSED = "not_exposed"
CODE_BAD_REQUEST = "bad_request"
CODE_HA_ERROR = "ha_error"
CODE_TIMEOUT = "timeout"

# keys that would widen a service call beyond the listed entities
_TARGET_KEYS = frozenset({"entity_id", "device_id", "area_id", "floor_id", "label_id", "target"})
# the script domain's own services (everything else in it is one script)
_SCRIPT_BUILTIN = frozenset({"turn_on", "turn_off", "toggle", "reload"})

MAX_IDS = 500


class RpcError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EntityMeta:
    """What exposure rules look at for one entity."""
    entity_id: str
    area_id: str | None = None        # own area, else the device's
    labels: frozenset = frozenset()

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


@dataclass
class Exposure:
    """Which entities Zero may see/control (the integration's options).

    ``mode == "all"``: every entity. ``mode == "select"``: an entity is
    exposed when it matches ANY chosen area, domain, label or is listed.
    """
    mode: str = "all"
    areas: frozenset = field(default_factory=frozenset)
    domains: frozenset = field(default_factory=frozenset)
    labels: frozenset = field(default_factory=frozenset)
    entities: frozenset = field(default_factory=frozenset)

    @classmethod
    def from_options(cls, opts: dict | None) -> "Exposure":
        opts = opts or {}
        def fs(key: str) -> frozenset:
            v = opts.get(key) or ()
            if isinstance(v, str):
                v = [v]
            return frozenset(str(x) for x in v if x)
        return cls(mode="select" if opts.get("expose_mode") == "select" else "all",
                   areas=fs("expose_areas"), domains=fs("expose_domains"),
                   labels=fs("expose_labels"), entities=fs("expose_entities"))

    def allows(self, meta: EntityMeta) -> bool:
        if self.mode == "all":
            return True
        return (meta.entity_id in self.entities
                or meta.domain in self.domains
                or (meta.area_id is not None and meta.area_id in self.areas)
                or bool(self.labels & set(meta.labels)))


class RpcBackend(Protocol):
    """What the dispatcher needs from Home Assistant (all synchronous except
    ``services`` and ``call``; everything returned must be JSON-able)."""

    def info(self) -> dict: ...                              # version, location_name, time_zone, unit_system
    def entity_ids(self) -> list[str]: ...                   # every entity that has a state
    def meta(self, entity_id: str) -> EntityMeta: ...
    def state(self, entity_id: str) -> dict | None: ...      # REST /api/states shape
    def last_updated(self, entity_id: str) -> str | None: ...
    async def services(self) -> dict[str, dict]: ...        # {domain: {service: {name, description, fields, target?}}}
    def registry(self) -> dict: ...                          # {"areas", "floors", "labels", "entities": {id: {...}}}
    async def call(self, domain: str, service: str, entity_ids: list[str],
                   data: dict, return_response: bool) -> Any: ...


class RpcDispatcher:
    def __init__(self, backend: RpcBackend, exposure: Exposure) -> None:
        self.backend = backend
        self.exposure = exposure

    # -- exposure -------------------------------------------------------------
    def is_exposed(self, entity_id: str) -> bool:
        if not isinstance(entity_id, str) or "." not in entity_id:
            return False
        if self.backend.state(entity_id) is None:
            return False
        return self.exposure.allows(self.backend.meta(entity_id))

    def exposed_ids(self) -> list[str]:
        return [e for e in self.backend.entity_ids()
                if self.exposure.allows(self.backend.meta(e))]

    def exposed_count(self) -> int:
        return len(self.exposed_ids())

    # -- dispatch -------------------------------------------------------------
    async def __call__(self, method: str, params: Any) -> Any:
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise RpcError(CODE_BAD_REQUEST, "params must be an object")
        handler = {
            "ha.info": self._info, "ha.states": self._states, "ha.state": self._state,
            "ha.services": self._services, "ha.registry": self._registry, "ha.call": self._call,
        }.get(method)
        if handler is None:
            raise RpcError(CODE_BAD_REQUEST, f"unknown method {method!r}")
        return await handler(params)

    async def _info(self, params: dict) -> dict:
        info = dict(self.backend.info())
        info["exposed"] = self.exposed_count()
        return {k: info.get(k) for k in ("version", "location_name", "time_zone", "unit_system", "exposed")}

    async def _states(self, params: dict) -> list:
        ids = params.get("entity_ids")
        if ids is None:
            wanted: Iterable[str] = self.exposed_ids()
        else:
            ids = _id_list(ids)
            wanted = [e for e in dict.fromkeys(ids) if self.is_exposed(e)]
        out = []
        for eid in wanted:
            st = self.backend.state(eid)
            if st is not None:
                out.append(st)
        return out

    async def _state(self, params: dict) -> dict:
        eid = params.get("entity_id")
        if not isinstance(eid, str) or not eid:
            raise RpcError(CODE_BAD_REQUEST, "entity_id is required")
        if not self.is_exposed(eid):
            raise RpcError(CODE_NOT_EXPOSED, f"{eid} is not exposed to Zero")
        return self.backend.state(eid)

    async def _services(self, params: dict) -> list:
        exposed = set(self.exposed_ids())
        domains = sorted({e.split(".", 1)[0] for e in exposed})
        all_services = await self.backend.services()
        out = []
        for domain in domains:
            svcs = all_services.get(domain)
            if not svcs:
                continue
            if domain == "script":
                svcs = {n: d for n, d in svcs.items()
                        if n in _SCRIPT_BUILTIN or f"script.{n}" in exposed}
            out.append({"domain": domain, "services": svcs})
        return out

    async def _registry(self, params: dict) -> dict:
        reg = self.backend.registry()
        exposed = self.exposed_ids()
        ents = reg.get("entities") or {}
        entities = []
        area_ids: set[str] = set()
        label_ids: set[str] = set()
        for eid in exposed:
            row = ents.get(eid) or {}
            meta = self.backend.meta(eid)
            area = meta.area_id
            labels = sorted(meta.labels)
            if area:
                area_ids.add(area)
            label_ids.update(labels)
            entities.append({"entity_id": eid, "area_id": area, "labels": labels,
                             "aliases": sorted(row.get("aliases") or []),
                             "name": row.get("name")})
        areas = [a for a in reg.get("areas") or [] if a.get("area_id") in area_ids]
        floor_ids = {a.get("floor_id") for a in areas if a.get("floor_id")}
        floors = [f for f in reg.get("floors") or [] if f.get("floor_id") in floor_ids]
        labels_out = [l for l in reg.get("labels") or [] if l.get("label_id") in label_ids]
        return {"areas": areas, "floors": floors, "labels": labels_out, "entities": entities}

    async def _call(self, params: dict) -> dict:
        domain = params.get("domain")
        service = params.get("service")
        if not _ident(domain) or not _ident(service):
            raise RpcError(CODE_BAD_REQUEST, "domain and service are required")
        data = params.get("data") or {}
        if not isinstance(data, dict):
            raise RpcError(CODE_BAD_REQUEST, "data must be an object")
        bad = _TARGET_KEYS & set(data)
        if bad:
            raise RpcError(CODE_BAD_REQUEST,
                           f"targets go in entity_ids, not in data ({', '.join(sorted(bad))})")
        ids = list(dict.fromkeys(_id_list(params.get("entity_ids") or [])))
        if not ids:
            # the only target-less call: a script run as its own service (script.<name>)
            if domain == "script" and service not in _SCRIPT_BUILTIN and self.is_exposed(f"script.{service}"):
                pass
            else:
                raise RpcError(CODE_NOT_EXPOSED if domain == "script" else CODE_BAD_REQUEST,
                               "a call needs at least one exposed target entity")
        for eid in ids:
            if not self.is_exposed(eid):
                raise RpcError(CODE_NOT_EXPOSED, f"{eid} is not exposed to Zero")
        # scene.turn_on / script.turn_on are ordinary calls on exposed ids (checked above)
        return_response = bool(params.get("return_response", False))
        watch = self.exposed_ids()
        before = {e: self.backend.last_updated(e) for e in watch}
        response = await self.backend.call(domain, service, ids, dict(data), return_response)
        changed = []
        for eid in watch:
            if self.backend.last_updated(eid) != before.get(eid):
                st = self.backend.state(eid)
                if st is not None:
                    changed.append(st)
        result: dict = {"changed": changed}
        if return_response:
            result["response"] = response
        return result


def _ident(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and len(v) <= 100 and all(
        c.isalnum() or c == "_" for c in v)


def _id_list(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)) or len(v) > MAX_IDS or not all(isinstance(x, str) for x in v):
        raise RpcError(CODE_BAD_REQUEST, "entity_ids must be a list of entity ids")
    return [x.strip().lower() for x in v if x.strip()]
