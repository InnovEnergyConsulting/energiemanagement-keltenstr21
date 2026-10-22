"""Controme-Adapter – nur lesend (Phase 2).

Fragt die lokale API des Miniservers ab und veröffentlicht die unveränderte Antwort samt
Empfangszeit als Rohdatum. Fachliche Bedeutung ordnet erst der Normalizer zu.

  ems/v1/raw/controme/temps   alle EMS_CONTROME_INTERVAL_S Sekunden (Start: 60 s)
  ems/v1/raw/controme/outs    wie temps (Ventil-Ausgänge je Raum, 0 = zu / 1 = auf)
  ems/v1/raw/controme/vtr     alle 5 min (Vorlauftemperatur je Regelung; 20.10.2026 noch leer)
  ems/v1/raw/controme/info    stündlich (Softwarestand -> Schema-Änderungen erkennen)
  ems/v1/availability/controme  online/offline inkl. Grund (retained)

Es gibt hier bewusst keinen Code für /set/… – Schreibzugriffe kommen frühestens in Phase 8.
"""
import time

import requests

from ..common import log as logsetup
from ..common.config import env
from ..common.mqtt import Bus
from ..common.timeutil import iso, utcnow

log = logsetup.setup("ems.controme-adapter")


def fetch(session: requests.Session, url: str) -> tuple[dict | list, int, int]:
    t0 = time.monotonic()
    resp = session.get(url, timeout=(5, 15))
    duration_ms = int((time.monotonic() - t0) * 1000)
    resp.raise_for_status()
    return resp.json(), resp.status_code, duration_ms


def run():
    base = env("EMS_CONTROME_URL").rstrip("/")
    house = env("EMS_CONTROME_HOUSE_ID", "1")
    interval = int(env("EMS_CONTROME_INTERVAL_S", "60"))
    streams = {"temps": (f"{base}/get/json/v1/{house}/temps/", interval),
               "outs": (f"{base}/get/json/v1/{house}/outs/", interval),   # Ventil-Ausgänge je Raum
               "vtr": (f"{base}/get/json/v1/{house}/vtr/", 300),     # Vorlauf je Regelung (derzeit leer)
               "info": (f"{base}/get/json/v1/{house}/info/", 3600)}
    bus = Bus("ems-controme-adapter", "controme_adapter", "ems/v1/availability/controme").start()
    session = requests.Session()
    next_due = {name: 0.0 for name in streams}
    failures = 0
    last_status = None
    while True:
        now = time.monotonic()
        for name, (url, every) in streams.items():
            if now < next_due[name]:
                continue
            received = utcnow()
            try:
                payload, status, duration_ms = fetch(session, url)
                bus.publish_json(f"ems/v1/raw/controme/{name}", {
                    "source": "controme", "stream": name, "received_at": iso(received),
                    "http_status": status, "duration_ms": duration_ms, "payload": payload})
                failures = 0
                next_due[name] = now + every
                if last_status != "online":
                    bus.set_availability("online"); last_status = "online"
            except (requests.RequestException, ValueError) as exc:
                failures += 1
                # wachsende Wartezeit bis max. 10 min, damit ein gestörter Miniserver nicht geflutet wird
                backoff = min(every, 10 * 2 ** min(failures, 6), 600)
                next_due[name] = now + backoff
                reason = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("Controme %s nicht erreichbar (%s) – nächster Versuch in %ss", name, reason, backoff)
                if last_status != "offline":
                    bus.set_availability("offline", reason); last_status = "offline"
        time.sleep(1)
