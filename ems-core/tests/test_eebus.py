import json
from pathlib import Path

import yaml

from ems.common.config import mapping_dir
from ems.normalizer import eebus
from ems.normalizer.service import Normalizer

FIX = Path(__file__).parent / "fixtures" / "eebus" / "vr940_2026-10-20.jsonl"
MAP = yaml.safe_load((mapping_dir() / "eebus_vr940.yaml").read_text(encoding="utf-8"))


def messages():
    return [json.loads(l) for l in FIX.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_all():
    st, tel, states, qual = eebus.EebusState(), [], [], []
    for m in messages():
        t, s, q = eebus.normalize({"received_at": m["received_at"], "payload": m["payload"]}, MAP, st)
        tel += t; states += s; qual += q
    return tel, states, qual


def latest(tel, asset, quantity):
    vals = [t for t in tel if t["asset_id"] == asset and t["quantity"] == quantity]
    return vals[-1] if vals else None


def test_flat_spine_structure():
    assert eebus.flat([{"a": 1}, {"b": [[{"x": 1}], [{"x": 2}]]}]) == {"a": 1, "b": [{"x": 1}, {"x": 2}]}
    assert eebus.scaled({"number": 552, "scale": -1}) == 55.2
    assert eebus.scaled({"number": None}) is None


def test_real_values_from_vr940():
    tel, _, _ = run_all()
    wp = latest(tel, "vaillant_wp", "active_power")
    assert wp["value"] == 12.0 and wp["unit"] == "W" and wp["medium"] == "electricity"
    assert wp["circuit_id"] == "heat_pump"
    assert latest(tel, "vaillant_warmwasser", "temperature")["value"] == 55.0
    assert latest(tel, "vaillant_warmwasser", "temperature_setpoint")["value"] == 55.0
    assert latest(tel, "vaillant_zone_1", "temperature")["value"] == 20.0
    assert latest(tel, "vaillant_zone_1", "temperature_setpoint")["value"] == 21.0


def test_values_without_number_are_not_zero():
    tel, _, _ = run_all()
    # Phasenleistungen, Ströme, Spannungen, Energie sind beschrieben, aber ohne Wert -> kein Messwert
    assert not [t for t in tel if t["asset_id"] == "vaillant_wp" and t["quantity"] != "active_power"]


def test_outdoor_sensor_error_is_quality_not_value():
    tel, _, qual = run_all()
    assert latest(tel, "vaillant_aussenfuehler", "temperature") is None
    assert any(q["kind"] == "value_state" and "outsideAirTemperature" in q["detail"] for q in qual)


def test_states_modes_and_lpc():
    _, states, _ = run_all()
    dhw = [s for s in states if s["asset_id"] == "vaillant_warmwasser" and s["kind"] == "hvac"][-1]
    zone = [s for s in states if s["asset_id"] == "vaillant_zone_1" and s["kind"] == "hvac"][-1]
    assert dhw["dhw_mode"] == "auto" and dhw["dhw_overrun_active"] is False
    assert zone["heating_mode"] == "off"
    lpc = [s for s in states if s["kind"] == "load_control"][-1]
    assert lpc["lpc_limit_active"] is False and lpc["lpc_scope"] == "activePowerLimit"
    cfg = [s for s in states if s["kind"] == "device_configuration"][-1]
    assert cfg["failsafeDurationMinimum"] == "PT2H"


def test_only_known_functions():
    _, _, qual = run_all()
    assert not [q for q in qual if q["kind"] in ("unknown_function", "unknown_entity", "unknown_scope")], qual


def test_normalizer_integration_publishes_telemetry():
    sent = []
    n = Normalizer({"eebus_vr940": MAP}, lambda topic, msg: sent.append((topic, msg)))
    for m in messages():
        raw = {"source": "eebus", "stream": "spine", "received_at": m["received_at"], "payload": m["payload"]}
        n.handle("ems/v1/raw/eebus/spine", json.dumps(raw).encode())
    topics = {t for t, _ in sent}
    assert "ems/v1/telemetry/vaillant_wp/active_power" in topics
    assert "ems/v1/telemetry/vaillant_warmwasser/temperature" in topics
    assert "ems/v1/state/vaillant_zone_1" in topics
    n.handle("ems/v1/raw/eebus/mdt", b'{"x":1}')            # Rohdaten-Strom: still ignoriert
    assert not any(m.get("kind") == "no_mapping" for _, m in sent)


def test_ignored_entity_yields_nothing():
    from ems.normalizer import eebus as eb
    m = dict(MAP, ignore_entities=["7.1.1"])
    raw = {"received_at": "2026-10-22T06:00:00.000Z", "payload": {"data": [{"payload": {"datagram": [
        {"header": [{"addressSource": [{"entity": [7, 1, 1]}]}, {"cmdClassifier": "notify"}]},
        {"payload": [{"cmd": [[{"measurementListData": [{"measurementData": [[{"measurementId": 0}, {"value": [{"number": 0}]}, {"valueState": "error"}]]}]}]]}]}]}}]}}
    assert eb.normalize(raw, m, eb.EebusState()) == ([], [], [])
