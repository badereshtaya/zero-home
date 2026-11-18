"""RPC dispatch and exposure filtering against a fake state provider."""
from __future__ import annotations

import pytest

from link.rpc import EntityMeta, Exposure, RpcDispatcher, RpcError


def st(eid, state="off", lu="2026-11-18T10:00:00+00:00"):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": eid},
            "last_changed": lu, "last_updated": lu, "context": {"id": "c"}}


class FakeBackend:
    def __init__(self):
        self.states = {e: st(e) for e in (
            "light.kitchen", "light.bedroom", "switch.heater", "lock.front",
            "script.good_night", "script.secret", "scene.movie", "sensor.temp")}
        self.metas = {
            "light.kitchen": EntityMeta("light.kitchen", "kitchen", frozenset()),
            "light.bedroom": EntityMeta("light.bedroom", "bedroom", frozenset({"zero"})),
            "switch.heater": EntityMeta("switch.heater", "kitchen", frozenset()),  # area from device
            "lock.front": EntityMeta("lock.front", None, frozenset()),
            "script.good_night": EntityMeta("script.good_night", None, frozenset({"zero"})),
            "script.secret": EntityMeta("script.secret", None, frozenset()),
            "scene.movie": EntityMeta("scene.movie", "living", frozenset()),
            "sensor.temp": EntityMeta("sensor.temp", "kitchen", frozenset()),
        }
        self.calls = []
        self.tick = 0

    def info(self):
        return {"version": "2026.11.2", "location_name": "البيت", "time_zone": "Asia/Hebron",
                "unit_system": {"temperature": "°C"}}

    def entity_ids(self):
        return list(self.states)

    def meta(self, eid):
        return self.metas.get(eid, EntityMeta(eid))

    def state(self, eid):
        return self.states.get(eid)

    def last_updated(self, eid):
        s = self.states.get(eid)
        return s["last_updated"] if s else None

    async def services(self):
        return {
            "light": {"turn_on": {"name": "Turn on", "description": "", "fields": {}},
                      "turn_off": {"name": "Turn off", "description": "", "fields": {}}},
            "switch": {"turn_on": {"name": "Turn on", "description": "", "fields": {}}},
            "lock": {"unlock": {"name": "Unlock", "description": "", "fields": {}}},
            "script": {"turn_on": {"name": "Run", "description": "", "fields": {}},
                       "reload": {"name": "Reload", "description": "", "fields": {}},
                       "good_night": {"name": "Good night", "description": "", "fields": {}},
                       "secret": {"name": "Secret", "description": "", "fields": {}}},
            "homeassistant": {"restart": {"name": "Restart", "description": "", "fields": {}}},
        }

    def registry(self):
        return {"areas": [{"area_id": "kitchen", "name": "المطبخ", "floor_id": "ground", "aliases": ["kitchen"]},
                          {"area_id": "bedroom", "name": "غرفة النوم", "floor_id": "first", "aliases": []},
                          {"area_id": "living", "name": "الصالون", "floor_id": None, "aliases": []}],
                "floors": [{"floor_id": "ground", "name": "الأرضي", "level": 0},
                           {"floor_id": "first", "name": "الأول", "level": 1}],
                "labels": [{"label_id": "zero", "name": "Zero"}, {"label_id": "other", "name": "Other"}],
                "entities": {e: {"name": e.split(".")[1], "aliases": ["a"] if e == "light.kitchen" else []}
                             for e in self.states}}

    async def call(self, domain, service, ids, data, rr):
        self.calls.append((domain, service, ids, data, rr))
        self.tick += 1
        for e in ids:
            self.states[e] = st(e, "on", f"2026-11-18T10:00:{self.tick:02d}+00:00")
        return {"result": "done"} if rr else None


def disp(**opts):
    return RpcDispatcher(FakeBackend(), Exposure.from_options(opts))


SELECT = {"expose_mode": "select", "expose_areas": ["kitchen"], "expose_labels": ["zero"],
          "expose_domains": ["scene"], "expose_entities": ["lock.front"]}


def test_exposure_modes():
    d = disp()
    assert d.exposed_count() == 8
    d = disp(**SELECT)
    assert sorted(d.exposed_ids()) == sorted(["light.kitchen", "switch.heater", "sensor.temp",
                                               "light.bedroom", "script.good_night",
                                               "scene.movie", "lock.front"])
    assert not d.is_exposed("script.secret")
    assert not d.is_exposed("light.nope")
    assert disp(expose_mode="select", expose_domains=["light"]).exposed_count() == 2


async def test_info_and_states():
    d = disp(**SELECT)
    info = await d("ha.info", None)
    assert info == {"version": "2026.11.2", "location_name": "البيت", "time_zone": "Asia/Hebron",
                    "unit_system": {"temperature": "°C"}, "exposed": 7}
    states = await d("ha.states", {})
    assert len(states) == 7 and "script.secret" not in {s["entity_id"] for s in states}
    assert set(states[0]) >= {"entity_id", "state", "attributes", "last_changed", "last_updated"}
    some = await d("ha.states", {"entity_ids": ["light.kitchen", "script.secret", "nope.x"]})
    assert [s["entity_id"] for s in some] == ["light.kitchen"]
    assert (await d("ha.state", {"entity_id": "light.kitchen"}))["state"] == "off"
    for eid in ("script.secret", "light.unknown"):
        with pytest.raises(RpcError) as e:
            await d("ha.state", {"entity_id": eid})
        assert e.value.code == "not_exposed"
    with pytest.raises(RpcError) as e:
        await d("ha.state", {})
    assert e.value.code == "bad_request"
    with pytest.raises(RpcError) as e:
        await d("ha.nope", {})
    assert e.value.code == "bad_request"


async def test_services_filtered():
    d = disp(**SELECT)
    svcs = await d("ha.services", None)
    domains = [s["domain"] for s in svcs]
    assert "homeassistant" not in domains and "lock" in domains and "light" in domains
    script = next(s for s in svcs if s["domain"] == "script")["services"]
    assert set(script) == {"turn_on", "reload", "good_night"}
    assert set(script["turn_on"]) >= {"name", "fields"}


async def test_registry_filtered():
    d = disp(expose_mode="select", expose_entities=["light.kitchen", "light.bedroom"])
    reg = await d("ha.registry", {})
    assert {a["area_id"] for a in reg["areas"]} == {"kitchen", "bedroom"}
    assert {f["floor_id"] for f in reg["floors"]} == {"ground", "first"}
    assert reg["labels"] == [{"label_id": "zero", "name": "Zero"}]
    ents = {e["entity_id"]: e for e in reg["entities"]}
    assert set(ents) == {"light.kitchen", "light.bedroom"}
    assert ents["light.kitchen"] == {"entity_id": "light.kitchen", "area_id": "kitchen", "labels": [],
                                     "aliases": ["a"], "name": "kitchen"}
    assert ents["light.bedroom"]["labels"] == ["zero"]


async def test_call_rules():
    d = disp(**SELECT)
    res = await d("ha.call", {"domain": "light", "service": "turn_on",
                              "entity_ids": ["light.kitchen"], "data": {"brightness": 100}})
    assert [s["entity_id"] for s in res["changed"]] == ["light.kitchen"]
    assert res["changed"][0]["state"] == "on" and "response" not in res
    assert d.backend.calls[-1] == ("light", "turn_on", ["light.kitchen"], {"brightness": 100}, False)

    with pytest.raises(RpcError) as e:
        await d("ha.call", {"domain": "script", "service": "turn_on", "entity_ids": ["script.secret"]})
    assert e.value.code == "not_exposed"
    with pytest.raises(RpcError) as e:   # mixed: one hidden entity refuses the whole call
        await d("ha.call", {"domain": "light", "service": "turn_on",
                            "entity_ids": ["light.kitchen", "script.secret"]})
    assert e.value.code == "not_exposed"
    with pytest.raises(RpcError) as e:   # no target at all
        await d("ha.call", {"domain": "homeassistant", "service": "restart"})
    assert e.value.code == "bad_request"
    for bad in ({"entity_id": "script.secret"}, {"area_id": "kitchen"}, {"target": {}}):
        with pytest.raises(RpcError) as e:
            await d("ha.call", {"domain": "light", "service": "turn_on",
                                "entity_ids": ["light.kitchen"], "data": bad})
        assert e.value.code == "bad_request"
    with pytest.raises(RpcError) as e:
        await d("ha.call", {"domain": "light", "service": "turn_on", "entity_ids": "x" * 0, "data": []})
    assert e.value.code == "bad_request"
    n = len(d.backend.calls)
    assert n == 1   # none of the refused calls reached Home Assistant

    # scene/script turn_on on exposed ids, and script.<name> as its own service
    await d("ha.call", {"domain": "scene", "service": "turn_on", "entity_ids": ["scene.movie"]})
    await d("ha.call", {"domain": "script", "service": "good_night"})
    with pytest.raises(RpcError) as e:
        await d("ha.call", {"domain": "script", "service": "secret"})
    assert e.value.code == "not_exposed"
    res = await d("ha.call", {"domain": "script", "service": "turn_on",
                              "entity_ids": ["script.good_night"], "return_response": True})
    assert res["response"] == {"result": "done"}
