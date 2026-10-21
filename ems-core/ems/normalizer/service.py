"""Normalizer-Dienst: ems/v1/raw/# -> ems/v1/telemetry|state|quality.

Geräte mit eigenem MQTT-Format (Tasmota) senden nach ems/v1/device/<gerät>/...; dafür ergänzt der Normalizer
die Rohdaten-Hülle (-> ems/v1/raw/<gerät>/<strom>, vom Writer gespeichert) und übersetzt direkt.

Liest nie seine eigenen Ausgabetopics (keine Schleife, Anforderungen Kap. 2).
Duplikate (gleicher Messpunkt, gleiche Messzeit, gleicher Wert) werden verworfen – z. B. wenn
ein Funksensor seit dem letzten Abruf nichts Neues gesendet hat.
"""
import json
import time

import yaml

from ..common import log as logsetup
from ..common.config import mapping_dir
from ..common.mqtt import Bus
from ..common.timeutil import utcnow
from . import controme, eebus, tasmota

log = logsetup.setup("ems.normalizer")


# Rohdaten-Ströme, die nur gespeichert (ems_raw), aber nicht normalisiert werden – die fachlichen Werte
# kommen bei EEBUS vollständig über den Strom "spine" (Stufe C, 20.10.2026).
RAW_ONLY = {("eebus", "mpc"), ("eebus", "mdt"), ("eebus", "discovery"), ("eebus", "mdns"),
            ("wattwaechter", "sensor"), ("wattwaechter", "state")}   # schon beim Geräte-Topic übersetzt

# Tasmota-Geräte: Gerätename (Topic) -> Mapping
TASMOTA_DEVICES = {"wattwaechter": "tasmota_wattwaechter"}


class Normalizer:
    def __init__(self, mappings: dict, publish):
        self.mappings = mappings
        self.publish = publish
        self.last: dict[str, tuple[str, float]] = {}
        self.reported_quality: set[str] = set()
        self.eebus_state = eebus.EebusState()
        self.tasmota_state = tasmota.TasmotaState()

    def handle_device(self, topic: str, payload: bytes):
        parts = topic.split("/")            # ems/v1/device/<gerät>/<prefix>/<name>
        if len(parts) != 6 or parts[3] not in TASMOTA_DEVICES:
            return
        device, prefix, name = parts[3], parts[4], parts[5]
        received = utcnow()
        data = tasmota.parse_payload(payload)
        if (prefix, name) == ("tele", "LWT"):
            self.publish(f"ems/v1/availability/{device}", tasmota.availability(str(data), received), retain=True)
        elif (prefix, name) == ("tele", "STATE"):
            self.publish(f"ems/v1/raw/{device}/state", tasmota.envelope(device, "state", data, received))
        elif (prefix, name) == ("tele", "SENSOR"):
            raw = tasmota.envelope(device, "sensor", data, received)
            self.publish(f"ems/v1/raw/{device}/sensor", raw)
            try:
                telemetry, states, quality = tasmota.normalize(raw, self.mappings[TASMOTA_DEVICES[device]],
                                                               self.tasmota_state)
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                self._quality({"source": device, "kind": "schema_error", "detail": f"SENSOR: {type(exc).__name__}: {exc}"[:300]})
                return
            self._emit(device, "sensor", telemetry, states, quality)

    def handle(self, topic: str, payload: bytes):
        if topic.startswith("ems/v1/device/"):
            return self.handle_device(topic, payload)
        parts = topic.split("/")            # ems/v1/raw/<quelle>/<strom>
        if len(parts) != 5:
            return
        source, stream = parts[3], parts[4]
        if (source, stream) in RAW_ONLY:
            return
        try:
            raw = json.loads(payload)
        except ValueError:
            self._quality({"source": source, "kind": "invalid_json", "detail": topic})
            return
        try:
            if source == "controme" and stream == "temps":
                telemetry, states, quality = controme.normalize(raw, self.mappings["controme_temps"])
            elif source == "controme" and stream == "vtr":
                telemetry, states, quality = controme.normalize_vtr(raw, self.mappings["controme_vtr"])
            elif source == "eebus" and stream == "spine":
                telemetry, states, quality = eebus.normalize(raw, self.mappings["eebus_vr940"], self.eebus_state)
            elif source == "controme" and stream == "info":
                states = [{"asset_id": "controme_miniserver", "name": "Controme Miniserver", "source": "controme",
                           "received_at": raw["received_at"], "software_date": raw["payload"].get("sw-date"),
                           "branch": raw["payload"].get("branch"), "hardware": raw["payload"].get("hw")}]
                telemetry, quality = [], []
            else:
                self._quality({"source": source, "kind": "no_mapping", "detail": f"kein Mapping für {topic}"})
                return
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            # unerwartetes Antwortformat (z. B. nach Controme-Update) -> melden statt abstürzen
            self._quality({"source": source, "kind": "schema_error", "detail": f"{stream}: {type(exc).__name__}: {exc}"[:300]})
            return
        self._emit(source, stream, telemetry, states, quality)

    def _emit(self, source, stream, telemetry, states, quality):
        sent = 0
        for t in telemetry:
            key = t["point_id"]
            if self.last.get(key) == (t["measured_at"], t["value"]):
                continue
            self.last[key] = (t["measured_at"], t["value"])
            self.publish(f"ems/v1/telemetry/{t['asset_id']}/{t['quantity']}", t)
            sent += 1
        for s in states:
            self.publish(f"ems/v1/state/{s['asset_id']}", s)
        for q in quality:
            self._quality(q)
        log.debug("%s/%s: %d Messwerte, %d Zustände", source, stream, sent, len(states))

    def _quality(self, q: dict):
        # gleiche Meldung nur einmal pro Programmlauf, sonst flutet ein Schemafehler jede Minute die Datenbank
        key = json.dumps(q, sort_keys=True)
        if key in self.reported_quality:
            return
        self.reported_quality.add(key)
        log.warning("Qualität: %s", q)
        self.publish(f"ems/v1/quality/{q['source']}", q)


def load_mappings() -> dict:
    return {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in mapping_dir().glob("*.yaml")}


def run():
    mappings = load_mappings()
    log.info("Mappings geladen: %s", ", ".join(f"{k} v{v['mapping_version']}" for k, v in mappings.items()))
    holder = {}
    bus = Bus("ems-normalizer", "normalizer", "ems/v1/availability/normalizer",
              subscriptions=["ems/v1/raw/#", "ems/v1/device/#"], on_message=lambda t, p: holder["n"].handle(t, p))
    holder["n"] = Normalizer(mappings, bus.publish_json)
    bus.start()
    while True:
        time.sleep(3600)
