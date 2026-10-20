"""Normalizer-Dienst: ems/v1/raw/# -> ems/v1/telemetry|state|quality.

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
from . import controme

log = logsetup.setup("ems.normalizer")


# Quellen in der Analysephase: Rohdaten werden nur gespeichert (ems_raw), noch nicht normalisiert –
# sonst entstünde für jede Nachricht eine "kein Mapping"-Qualitätsmeldung. (EEBUS: Konzept Stufe A/B)
ANALYSIS_ONLY = {"eebus"}


class Normalizer:
    def __init__(self, mappings: dict, publish):
        self.mappings = mappings
        self.publish = publish
        self.last: dict[str, tuple[str, float]] = {}
        self.reported_quality: set[str] = set()

    def handle(self, topic: str, payload: bytes):
        parts = topic.split("/")            # ems/v1/raw/<quelle>/<strom>
        if len(parts) != 5:
            return
        source, stream = parts[3], parts[4]
        if source in ANALYSIS_ONLY:
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
              subscriptions=["ems/v1/raw/#"], on_message=lambda t, p: holder["n"].handle(t, p))
    holder["n"] = Normalizer(mappings, bus.publish_json)
    bus.start()
    while True:
        time.sleep(3600)
