"""WattWächter (Tasmota-SML) -> Telemetrie. Telegramme im Tasmota-Format vom 21.10.2026 (Gerät noch ohne Zählerdaten:
Werte nachgebildet; nach PIN/Inbetriebnahme durch echte Telegramme aus ems_raw ersetzen)."""
import json
from datetime import datetime, timedelta, timezone

import yaml

from ems.common.config import mapping_dir
from ems.common.timeutil import iso
from ems.normalizer import tasmota
from ems.normalizer.service import Normalizer

MAP = yaml.safe_load((mapping_dir() / "tasmota_wattwaechter.yaml").read_text(encoding="utf-8"))
T0 = datetime(2026, 10, 21, 6, 30, tzinfo=timezone.utc)          # = 08:30 Ortszeit (Sommerzeit)


def raw(meter: dict, at=T0, local="2026-10-21T08:30:00"):
    return tasmota.envelope("wattwaechter", "sensor", {"Time": local, "MT691": meter}, at)


def test_only_active_fields_and_placeholders_dropped():
    # ohne PIN: SML meldet für nie gesendete Zeilen 0 -> Power/Phasen sind im Mapping inaktiv
    tel, _, q = tasmota.normalize(raw({"E_in": 18234.0, "E_out": 0, "Power": 0, "Volt_p1": 0}), MAP, tasmota.TasmotaState())
    assert [(t["quantity"], t["value"], t["unit"]) for t in tel] == [("energy_import_total", 18234.0, "kWh")]
    t = tel[0]
    assert t["asset_id"] == "hausanschluss" and t["direction"] == "import" and t["measured_at"] == iso(T0)
    assert t["point_id"] == "haus.strom.hausanschluss.electricity.energy_import_total"
    assert q == []


def test_counter_decrease_and_jump_reported_not_stored():
    st = tasmota.TasmotaState()
    tasmota.normalize(raw({"E_in": 100.0}), MAP, st)
    tel, _, q = tasmota.normalize(raw({"E_in": 99.0}, T0 + timedelta(seconds=10)), MAP, st)
    assert tel == [] and q[0]["kind"] == "counter_decrease"
    tel, _, q = tasmota.normalize(raw({"E_in": 120.0}, T0 + timedelta(seconds=20)), MAP, st)
    assert tel == [] and q[0]["kind"] == "counter_jump"


def test_unchanged_total_throttled_to_5_minutes():
    st = tasmota.TasmotaState()
    n = sum(len(tasmota.normalize(raw({"E_in": 100.0}, T0 + timedelta(seconds=10 * i)), MAP, st)[0]) for i in range(60))
    assert n == 2                       # 0 s und 300 s
    tel, _, _ = tasmota.normalize(raw({"E_in": 100.5}, T0 + timedelta(seconds=610)), MAP, st)
    assert tel[0]["value"] == 100.5     # Änderung sofort


def test_power_limit_and_clock_skew_when_enabled():
    m = json.loads(json.dumps(MAP))
    m["fields"]["Power"]["aktiv"] = True
    tel, _, q = tasmota.normalize(raw({"E_in": 5.0, "Power": 99999}, local="2026-10-21T07:30:00"), m, tasmota.TasmotaState())
    kinds = {x["kind"] for x in q}
    assert "out_of_range" in kinds and "clock_skew" in kinds      # Uhr 1 h falsch (alte Zeitzone +01:00)
    assert [t["quantity"] for t in tel] == ["energy_import_total"]


def test_missing_meter_block_is_schema_error():
    tel, _, q = tasmota.normalize(tasmota.envelope("wattwaechter", "sensor", {"Time": "x"}, T0), MAP, tasmota.TasmotaState())
    assert tel == [] and q[0]["kind"] == "schema_error"


def test_normalizer_device_topics():
    sent = []
    n = Normalizer({"tasmota_wattwaechter": MAP}, lambda topic, msg, **kw: sent.append((topic, msg, kw)))
    n.handle("ems/v1/device/wattwaechter/tele/LWT", b"Online")
    from zoneinfo import ZoneInfo
    now_local = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%dT%H:%M:%S")   # Geräteuhr stimmt
    n.handle("ems/v1/device/wattwaechter/tele/SENSOR", json.dumps({"Time": now_local, "MT691": {"E_in": 18234.5}}).encode())
    n.handle("ems/v1/device/wattwaechter/stat/RESULT", b'{"TelePeriod":10}')          # ignoriert
    n.handle("ems/v1/device/unbekannt/tele/SENSOR", b"{}")                              # ignoriert
    topics = [s[0] for s in sent]
    assert topics == ["ems/v1/availability/wattwaechter", "ems/v1/raw/wattwaechter/sensor",
                      "ems/v1/telemetry/hausanschluss/energy_import_total"]
    assert sent[0][1]["status"] == "online" and sent[0][2] == {"retain": True}
    assert sent[1][1]["payload"]["MT691"]["E_in"] == 18234.5 and "received_at" in sent[1][1]
    n.handle("ems/v1/raw/wattwaechter/sensor", json.dumps(sent[1][1]).encode())         # eigene Rohdaten: nicht doppelt
    assert len(sent) == 3
