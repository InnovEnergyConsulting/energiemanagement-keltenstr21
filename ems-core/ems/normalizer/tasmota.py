"""Tasmota-Geräte (WattWächter am Hauptzähler) -> Rohdaten-Hülle, normalisierte Messwerte, Verfügbarkeit.

Tasmota sendet sein eigenes Format nach ems/v1/device/<gerät>/<prefix>/<name>:
  tele/SENSOR  {"Time": "2026-10-21T08:15:00", "MT691": {"E_in": 12345.678, ...}}   (Ortszeit ohne Zone)
  tele/LWT     Online | Offline   (retained, "letzter Wille")
Der Normalizer ergänzt die EMS-Hülle (Empfangszeit), damit der Writer die Rohdaten wie bei den Adaptern ablegt.
"""
import json
from datetime import datetime

from ..common.timeutil import iso, parse_iso, parse_local


def envelope(device: str, stream: str, payload, received: datetime) -> dict:
    return {"source": device, "stream": stream, "received_at": iso(received), "payload": payload,
            "http_status": 0, "duration_ms": 0}


def availability(lwt: str, received: datetime) -> dict:
    online = lwt.strip().lower() == "online"
    return {"status": "online" if online else "offline", "since": iso(received),
            "reason": "" if online else "Tasmota meldet Offline (letzter Wille)"}


class TasmotaState:
    """Letzte Zählerstände je Messpunkt – für Rücksprung- und Sprungprüfung."""

    def __init__(self):
        self.last_total: dict[str, float] = {}
        self.last_emit: dict[str, datetime] = {}


def normalize(raw: dict, mapping: dict, st: TasmotaState) -> tuple[list[dict], list[dict], list[dict]]:
    received = parse_iso(raw["received_at"])
    payload = raw["payload"]
    src = mapping["source"]
    meter = payload.get(mapping["meter_key"])
    if not isinstance(meter, dict):
        return [], [], [{"source": src, "kind": "schema_error",
                         "detail": f"Zählerblock {mapping['meter_key']} fehlt im Telegramm"}]
    quality = []
    device_time = parse_local(payload.get("Time", ""), "%Y-%m-%dT%H:%M:%S", mapping["timezone_device"])
    if device_time is not None and abs((device_time - received).total_seconds()) > mapping["max_clock_skew_s"]:
        quality.append({"source": src, "kind": "clock_skew",
                        "detail": f"Geräteuhr weicht {int((device_time - received).total_seconds())} s ab"})
    unknown = sorted(set(meter) - set(mapping["fields"]))
    if unknown:
        quality.append({"source": src, "kind": "unknown_keys", "detail": f"unbekannte Felder {unknown}"})

    asset, lim, ver = mapping["asset"], mapping["limits"], mapping["mapping_version"]
    telemetry = []
    for key, spec in mapping["fields"].items():
        if not spec.get("aktiv") or key not in meter:
            continue
        value = meter[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            quality.append({"source": src, "kind": "invalid_value", "detail": f"{key}={value!r}"})
            continue
        value = float(value)
        point_id = f"{mapping['point_prefix']}.{asset['asset_id']}.electricity.{spec['quantity']}"
        if spec.get("zaehler"):
            if value <= 0:
                continue                      # SML-Platzhalter vor dem ersten Telegramm, kein echter Stand
            prev = st.last_total.get(point_id)
            if prev is not None and value < prev:
                quality.append({"source": src, "kind": "counter_decrease",
                                "detail": f"{key} fällt von {prev} auf {value} kWh (Zählerwechsel/Fehllesung?)"})
                continue
            if prev is not None and value - prev > lim["energy_jump_kwh"]:
                quality.append({"source": src, "kind": "counter_jump",
                                "detail": f"{key} springt um {value - prev:.3f} kWh"})
                continue
            st.last_total[point_id] = value
            last = st.last_emit.get(point_id)
            if prev == value and last is not None and (received - last).total_seconds() < mapping["unchanged_total_every_s"]:
                continue                      # unveränderter Zählerstand: nur alle paar Minuten speichern
            st.last_emit[point_id] = received
        elif spec["unit"] == "W" and abs(value) > lim["power_abs_w"]:
            quality.append({"source": src, "kind": "out_of_range", "detail": f"{key}={value} W"})
            continue
        telemetry.append({
            "point_id": point_id, "asset_id": asset["asset_id"], "name": asset["name"],
            "medium": "electricity", "quantity": spec["quantity"], "role": spec["role"], "unit": spec["unit"],
            "measurement_kind": "measured", "circuit_id": asset["circuit_id"], "direction": spec["direction"],
            "value": value, "measured_at": iso(received), "received_at": iso(received), "age_s": 0.0,
            "source": src, "mapping_version": ver})
    return telemetry, [], quality


def parse_payload(payload: bytes):
    """Tasmota-Nutzdaten: JSON (SENSOR/STATE) oder Klartext (LWT)."""
    text = payload.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return text
