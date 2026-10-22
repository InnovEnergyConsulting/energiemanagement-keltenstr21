"""Controme temps/ -> normalisierte Messwerte, Zustände und Qualitätsmeldungen (reine Funktion, testbar)."""
import re
from datetime import datetime

from ..common.timeutil import iso, parse_iso, parse_local

CONTROME_TIME_FMT = "%d.%m.%Y %H:%M"


def _num(value):
    """Nur echte Zahlen übernehmen – Texte wie "kein aktueller Wert vorhanden" oder None -> None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _telemetry(spec: dict, prefix: str, asset_id: str, name: str, value: float, measured: datetime,
               received: datetime, mapping_version: int, extra_tags: dict) -> dict:
    return {
        "point_id": f"{prefix}.{asset_id}.{spec['medium']}.{spec['quantity']}",
        "asset_id": asset_id, "name": name,
        "medium": spec["medium"], "quantity": spec["quantity"], "role": spec["role"],
        "unit": spec["unit"], "measurement_kind": spec["measurement_kind"],
        "circuit_id": "unassigned", "direction": "none",
        "value": value,
        "measured_at": iso(measured), "received_at": iso(received),
        "age_s": round(max(0.0, (received - measured).total_seconds()), 1),
        "source": "controme", "mapping_version": mapping_version, **extra_tags,
    }


def normalize(raw: dict, mapping: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Gibt (telemetry, states, quality) zurück. raw = Nachricht von ems/v1/raw/controme/temps."""
    received = parse_iso(raw["received_at"])
    tz = mapping["timezone_device"]
    ver = mapping["mapping_version"]
    room_cfg, sensor_cfg = mapping["room"], mapping["sensor"]
    prefix = room_cfg["point_prefix"]
    telemetry, states, quality = [], [], []

    floors = raw["payload"]
    if not isinstance(floors, list):
        return [], [], [{"source": "controme", "kind": "schema_error", "detail": "temps ist keine Liste"}]

    for floor in floors:
        floor_name = str(floor.get("etagenname", "")).strip()
        for room in floor.get("raeume", []):
            missing = [k for k in room_cfg["required_keys"] if k not in room]
            if missing:
                quality.append({"source": "controme", "kind": "schema_error",
                                "detail": f"Raum {room.get('id')}: Pflichtfelder fehlen {missing}"})
                continue
            unknown = sorted(set(room) - set(room_cfg["known_keys"]))
            if unknown:
                quality.append({"source": "controme", "kind": "unknown_keys",
                                "detail": f"Raum {room['id']}: {unknown}"})
            asset = f"{room_cfg['asset_prefix']}{room['id']}"
            name = str(room["name"]).strip()
            tags = {"floor": floor_name}

            room_sensors = [s for s in room["sensoren"] if s.get("raumtemperatursensor")]
            sensor_time = None
            if room_sensors:
                sensor_time = parse_local(room_sensors[0].get("letzte_uebertragung", ""), CONTROME_TIME_FMT, tz)

            for spec in room_cfg["telemetry"]:
                value = _num(room.get(spec["key"]))
                if value is None:
                    continue                      # fehlender Wert bleibt fehlend – nie 0
                measured = received
                if spec.get("time_from") == "room_sensor":
                    if sensor_time is None:
                        continue                  # Messwert ohne bekannte Messzeit wird nicht übernommen
                    measured = sensor_time
                telemetry.append(_telemetry(spec, prefix, asset, name, value, measured, received, ver, tags))

            state = {"asset_id": asset, "name": name, "floor": floor_name, "source": "controme",
                     "received_at": iso(received), "mapping_version": ver}
            for key, field in room_cfg["state"].items():
                if key in room and room[key] not in ("", None):
                    state[field] = room[key]
            states.append(state)

            for sensor in room["sensoren"]:
                if sensor.get("raumtemperatursensor") and not sensor_cfg["include_room_sensors"]:
                    continue
                unknown_s = sorted(set(sensor) - set(sensor_cfg["known_keys"]))
                if unknown_s:
                    quality.append({"source": "controme", "kind": "unknown_keys",
                                    "detail": f"Sensor {sensor.get('name')}: {unknown_s}"})
                value = _num(sensor.get("wert"))
                measured = parse_local(sensor.get("letzte_uebertragung", ""), CONTROME_TIME_FMT, tz)
                if value is None or measured is None:
                    continue
                s_asset = f"{sensor_cfg['asset_prefix']}{_slug(str(sensor['name']))}"
                s_name = str(sensor.get("beschreibung", sensor["name"])).strip()
                telemetry.append(_telemetry(sensor_cfg["telemetry"], prefix, s_asset, s_name, value, measured,
                                            received, ver, {**tags, "room_asset_id": asset}))
    return telemetry, states, quality


def normalize_vtr(raw: dict, mapping: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Controme vtr/ (Vorlauftemperatur je Regelung). Leere Antwort {} = keine Regelung eingerichtet -> nichts."""
    received = parse_iso(raw["received_at"])
    payload = raw["payload"]
    if not isinstance(payload, dict):
        return [], [], [{"source": "controme", "kind": "schema_error", "detail": "vtr ist kein Objekt"}]
    telemetry, quality = [], []
    for reg_id, reg in payload.items():
        if not isinstance(reg, dict):
            quality.append({"source": "controme", "kind": "schema_error", "detail": f"vtr {reg_id}: kein Objekt"})
            continue
        unknown = sorted(set(reg) - set(mapping["known_keys"]))
        if unknown:
            quality.append({"source": "controme", "kind": "unknown_keys", "detail": f"vtr {reg_id}: {unknown}"})
        asset = f"{mapping['asset_prefix']}{_slug(str(reg_id))}"
        name = str(reg.get("name") or f"Vorlauf {reg_id}").strip()
        for item in mapping["telemetry"]:
            value = _num(reg.get(item["key"]))
            if value is None:
                continue
            spec = {**mapping["defaults"], **item}
            telemetry.append(_telemetry(spec, mapping["point_prefix"], asset, name, value, received, received,
                                        mapping["mapping_version"], {}))
    return telemetry, [], quality


def normalize_outs(raw: dict, mapping: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Controme outs/ -> Ventilstellung je Raum in % (Mittel der Ausgänge). Räume ohne Ausgang: kein Wert."""
    received = parse_iso(raw["received_at"])
    floors = raw["payload"]
    if not isinstance(floors, list):
        return [], [], [{"source": "controme", "kind": "schema_error", "detail": "outs ist keine Liste"}]
    telemetry, quality = [], []
    spec = {k: mapping[k] for k in ("quantity", "role", "medium", "unit", "measurement_kind")}
    for floor in floors:
        floor_name = str(floor.get("etagenname", "")).strip()
        for room in floor.get("raeume", []):
            unknown = sorted(set(room) - set(mapping["known_keys"]))
            if unknown:
                quality.append({"source": "controme", "kind": "unknown_keys", "detail": f"outs Raum {room.get('id')}: {unknown}"})
            outs = room.get("ausgang") or {}
            if not isinstance(outs, dict) or not outs:
                continue
            vals = [_num(v) for v in outs.values()]
            if any(v is None for v in vals):
                quality.append({"source": "controme", "kind": "invalid_value", "detail": f"outs Raum {room.get('id')}: {outs}"})
                continue
            pct = [v * 100 if v <= 1 else v for v in vals]
            asset_id = f"{mapping['asset_prefix']}{room['id']}"
            t = _telemetry(spec, mapping["point_prefix"], asset_id, str(room.get("name", "")).strip(),
                           round(sum(pct) / len(pct), 1), received, received, mapping["mapping_version"],
                           {"floor": floor_name})
            telemetry.append(t)
    return telemetry, [], quality
