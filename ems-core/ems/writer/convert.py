"""MQTT-Nachrichten -> InfluxDB Line Protocol (reine Funktionen, testbar).

Modell (Anforderungen Kap. 7): stabile Merkmale als Tags, Werte/Qualität als Felder.
Zeitstempel ist die Messzeit (UTC); erneut gesendete Werte mit gleicher Messzeit überschreiben
sich in InfluxDB, statt doppelt zu zählen.
"""
import json

from influxdb_client import Point, WritePrecision

from ..common.timeutil import parse_iso

TELEMETRY_TAGS = ("point_id", "asset_id", "name", "medium", "quantity", "role", "unit",
                  "measurement_kind", "circuit_id", "direction", "source", "floor", "room_asset_id")


def _ts(value: str):
    return parse_iso(value)


def telemetry(msg: dict) -> tuple[str, str]:
    p = Point("telemetry")
    for tag in TELEMETRY_TAGS:
        if msg.get(tag) not in (None, ""):
            p = p.tag(tag, str(msg[tag]))
    p = (p.field("value", float(msg["value"]))
          .field("age_s", float(msg.get("age_s", 0.0)))
          .field("mapping_version", int(msg.get("mapping_version", 0)))
          .time(_ts(msg["measured_at"]), WritePrecision.MS))
    return "ems_detail", p.to_line_protocol()


def state(msg: dict) -> tuple[str, str]:
    p = Point("state").tag("asset_id", msg["asset_id"]).tag("source", msg.get("source", ""))
    if msg.get("name"):
        p = p.tag("name", msg["name"])
    fields = 0
    for key, value in msg.items():
        if key in ("asset_id", "source", "name", "received_at", "floor"):
            continue
        if isinstance(value, bool):
            p = p.field(key, value); fields += 1
        elif isinstance(value, (int, float)):
            p = p.field(key, float(value)); fields += 1
        elif value is not None:
            p = p.field(key, str(value)); fields += 1
    if fields == 0:
        return None
    return "ems_events", p.time(_ts(msg["received_at"]), WritePrecision.MS).to_line_protocol()


def availability(component: str, msg: dict) -> tuple[str, str]:
    p = (Point("availability").tag("component", component)
         .field("status", msg.get("status", "unknown"))
         .field("online", 1 if msg.get("status") == "online" else 0)
         .field("reason", msg.get("reason", "")))
    return "ems_events", p.time(_ts(msg["since"]), WritePrecision.MS).to_line_protocol()


def quality(msg: dict, received_iso: str) -> tuple[str, str]:
    p = (Point("quality").tag("source", msg.get("source", "")).tag("kind", msg.get("kind", ""))
         .field("detail", msg.get("detail", "")))
    return "ems_events", p.time(_ts(received_iso), WritePrecision.MS).to_line_protocol()


def raw(msg: dict) -> tuple[str, str]:
    p = (Point("raw").tag("source", msg.get("source", "")).tag("stream", msg.get("stream", ""))
         .field("payload", json.dumps(msg.get("payload"), ensure_ascii=False))
         .field("http_status", int(msg.get("http_status", 0)))
         .field("duration_ms", int(msg.get("duration_ms", 0))))
    return "ems_raw", p.time(_ts(msg["received_at"]), WritePrecision.MS).to_line_protocol()
