import copy
import json
from pathlib import Path

import yaml

from ems.common.config import mapping_dir
from ems.normalizer import controme
from ems.normalizer.service import Normalizer

FIX = Path(__file__).parent / "fixtures" / "controme"
MAPPING = yaml.safe_load((mapping_dir() / "controme_temps.yaml").read_text(encoding="utf-8"))


def raw(payload, received="2026-10-20T10:10:00.000Z"):
    return {"source": "controme", "stream": "temps", "received_at": received, "payload": payload}


def load():
    return json.loads((FIX / "temps_2026-10-20.json").read_text(encoding="utf-8"))


def test_rooms_and_values():
    tel, states, quality = controme.normalize(raw(load()), MAPPING)
    assert quality == []
    assert len(states) == 15                                    # 8 Räume EG + 7 OG
    temp = {t["asset_id"]: t for t in tel if t["quantity"] == "temperature" and t["role"] == "room_actual"}
    assert temp["controme_raum_2"]["value"] == 20.64            # Küche EG
    assert temp["controme_raum_2"]["unit"] == "degC"
    assert temp["controme_raum_2"]["point_id"] == "haus.heizung.controme_raum_2.room_climate.temperature"
    assert temp["controme_raum_2"]["name"] == "Küche"


def test_missing_values_are_not_zero():
    tel, _, _ = controme.normalize(raw(load()), MAPPING)
    og = [t for t in tel if t["asset_id"] == "controme_raum_14"]   # Küche OG: kein Sensor
    assert all(t["quantity"] != "temperature" for t in og)          # None -> kein Messwert
    assert any(t["quantity"] == "temperature_setpoint" for t in og) # Sollwert gibt es trotzdem
    hum = {t["asset_id"] for t in tel if t["quantity"] == "relative_humidity"}
    assert hum == {"controme_raum_2", "controme_raum_4"}             # nur Küche und Bad EG


def test_measured_time_is_sensor_time_in_utc():
    tel, _, _ = controme.normalize(raw(load()), MAPPING)
    t = next(t for t in tel if t["asset_id"] == "controme_raum_2" and t["quantity"] == "temperature")
    assert t["measured_at"] == "2026-10-20T10:06:00.000Z"          # 12:06 Ortszeit (MESZ) = 10:06 UTC
    assert t["received_at"] == "2026-10-20T10:10:00.000Z"
    assert t["age_s"] == 240.0


def test_return_flow_sensors():
    tel, _, _ = controme.normalize(raw(load()), MAPPING)
    rl = [t for t in tel if t["role"] == "return_flow"]
    assert len(rl) == 9
    assert all(t["medium"] == "heat" and t["asset_id"].startswith("controme_sensor_") for t in rl)
    assert any(t["name"] == "Rücklauf Küche" and t["room_asset_id"] == "controme_raum_2" for t in rl)


def test_unknown_and_missing_keys_reported():
    data = load()
    data[0]["raeume"][0]["neues_feld"] = 1
    del data[1]["raeume"][0]["solltemperatur"]
    _, _, quality = controme.normalize(raw(data), MAPPING)
    kinds = sorted(q["kind"] for q in quality)
    assert kinds == ["schema_error", "unknown_keys"]


def test_normalizer_deduplicates_and_survives_bad_payload():
    sent = []
    n = Normalizer({"controme_temps": MAPPING}, lambda topic, msg: sent.append((topic, msg)))
    payload = json.dumps(raw(load())).encode()
    n.handle("ems/v1/raw/controme/temps", payload)
    first = len([s for s in sent if "/telemetry/" in s[0]])
    n.handle("ems/v1/raw/controme/temps", payload)              # gleiche Daten erneut
    second = len([s for s in sent if "/telemetry/" in s[0]]) - first
    assert first > 20 and second == 0
    n.handle("ems/v1/raw/controme/temps", json.dumps(raw({"kaputt": True})).encode())
    assert any(t.startswith("ems/v1/quality/") and m["kind"] == "schema_error" for t, m in sent)
    n.handle("ems/v1/raw/controme/temps", b"{kein json")
    assert any(m.get("kind") == "invalid_json" for _, m in sent)
