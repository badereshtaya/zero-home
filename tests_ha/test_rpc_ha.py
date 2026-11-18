"""RPC against real Home Assistant states, services and registries."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zero_home.link.rpc import Exposure, RpcDispatcher, RpcError
from custom_components.zero_home.rpc_ha import HaRpcBackend


@pytest.fixture
async def home(hass: HomeAssistant):
    floor = fr.async_get(hass).async_create("Ground", level=0)
    kitchen = ar.async_get(hass).async_create("Kitchen", floor_id=floor.floor_id, aliases={"المطبخ"})
    bedroom = ar.async_get(hass).async_create("Bedroom")
    label = lr.async_get(hass).async_create("Zero")
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=cfg.entry_id, identifiers={("test", "dev1")}, name="Kettle")
    dr.async_get(hass).async_update_device(device.id, area_id=kitchen.id)
    ent = er.async_get(hass)
    e1 = ent.async_get_or_create("switch", "test", "kettle", device_id=device.id, original_name="Kettle",
                                 suggested_object_id="kettle")
    assert e1.entity_id == "switch.kettle"
    e2 = ent.async_get_or_create("light", "test", "bed", original_name="Bed lamp",
                                 suggested_object_id="bed_lamp")
    assert e2.entity_id == "light.bed_lamp"
    ent.async_update_entity(e2.entity_id, area_id=bedroom.id, labels={label.label_id}, aliases={"ضو النوم"})
    hass.states.async_set("switch.kettle", "off", {"friendly_name": "Kettle"})
    hass.states.async_set("light.bed_lamp", "off", {"friendly_name": "Bed lamp"})
    hass.states.async_set("lock.front", "locked", {"friendly_name": "Front"})
    calls = []

    async def turn_on(call: ServiceCall):
        calls.append(call)
        for eid in call.data["entity_id"]:
            hass.states.async_set(eid, "on", hass.states.get(eid).attributes)

    async def unlock(call: ServiceCall):
        calls.append(call)
        for eid in call.data["entity_id"]:
            hass.states.async_set(eid, "unlocked")

    async def report(call: ServiceCall):
        return {"seen": call.data.get("entity_id")}

    hass.services.async_register("switch", "turn_on", turn_on)
    hass.services.async_register("light", "turn_on", turn_on)
    hass.services.async_register("lock", "unlock", unlock)
    hass.services.async_register("light", "report", report, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register("homeassistant", "restart", unlock)
    return {"kitchen": kitchen.id, "bedroom": bedroom.id, "label": label.label_id,
            "floor": floor.floor_id, "calls": calls}


def _d(hass, **opts):
    return RpcDispatcher(HaRpcBackend(hass), Exposure.from_options(opts))


async def test_exposure_by_device_area_and_label(hass: HomeAssistant, home):
    d = _d(hass, expose_mode="select", expose_areas=[home["kitchen"]])
    assert d.exposed_ids() == ["switch.kettle"]          # area comes from the device
    d = _d(hass, expose_mode="select", expose_labels=[home["label"]])
    assert d.exposed_ids() == ["light.bed_lamp"]
    assert _d(hass).exposed_count() == 3


async def test_states_info_registry(hass: HomeAssistant, home):
    d = _d(hass, expose_mode="select", expose_areas=[home["kitchen"]], expose_labels=[home["label"]])
    info = await d("ha.info", {})
    assert info["exposed"] == 2 and info["version"] and "temperature" in info["unit_system"]
    states = await d("ha.states", {})
    assert {s["entity_id"] for s in states} == {"switch.kettle", "light.bed_lamp"}
    s = states[0]
    assert set(s) >= {"entity_id", "state", "attributes", "last_changed", "last_updated"}
    assert isinstance(s["last_changed"], str)
    with pytest.raises(RpcError) as e:
        await d("ha.state", {"entity_id": "lock.front"})
    assert e.value.code == "not_exposed"
    reg = await d("ha.registry", {})
    areas = {a["area_id"]: a for a in reg["areas"]}
    assert set(areas) == {home["kitchen"], home["bedroom"]}
    assert areas[home["kitchen"]]["floor_id"] == home["floor"] and areas[home["kitchen"]]["aliases"] == ["المطبخ"]
    assert reg["floors"] == [{"floor_id": home["floor"], "name": "Ground", "level": 0}]
    assert reg["labels"] == [{"label_id": home["label"], "name": "Zero"}]
    ents = {x["entity_id"]: x for x in reg["entities"]}
    assert ents["switch.kettle"]["area_id"] == home["kitchen"] and ents["switch.kettle"]["name"] == "Kettle"
    assert ents["light.bed_lamp"]["aliases"] == ["ضو النوم"] and ents["light.bed_lamp"]["labels"] == [home["label"]]


async def test_services_and_calls(hass: HomeAssistant, home):
    d = _d(hass, expose_mode="select", expose_areas=[home["kitchen"]], expose_labels=[home["label"]])
    svcs = {s["domain"]: s["services"] for s in await d("ha.services", {})}
    assert set(svcs) == {"switch", "light"} and "turn_on" in svcs["switch"]
    assert set(svcs["switch"]["turn_on"]) >= {"name", "fields"}

    res = await d("ha.call", {"domain": "switch", "service": "turn_on", "entity_ids": ["switch.kettle"]})
    assert [s["entity_id"] for s in res["changed"]] == ["switch.kettle"] and res["changed"][0]["state"] == "on"
    assert hass.states.get("switch.kettle").state == "on"

    for bad, code in [({"domain": "lock", "service": "unlock", "entity_ids": ["lock.front"]}, "not_exposed"),
                      ({"domain": "homeassistant", "service": "restart"}, "bad_request"),
                      ({"domain": "light", "service": "turn_on", "entity_ids": ["light.bed_lamp"],
                        "data": {"area_id": home["kitchen"]}}, "bad_request"),
                      ({"domain": "light", "service": "nope", "entity_ids": ["light.bed_lamp"]}, "bad_request")]:
        with pytest.raises(RpcError) as e:
            await d("ha.call", bad)
        assert e.value.code == code
    assert hass.states.get("lock.front").state == "locked"
    assert len(home["calls"]) == 1

    res = await d("ha.call", {"domain": "light", "service": "report", "entity_ids": ["light.bed_lamp"],
                              "return_response": True})
    assert res["response"] == {"seen": ["light.bed_lamp"]}
