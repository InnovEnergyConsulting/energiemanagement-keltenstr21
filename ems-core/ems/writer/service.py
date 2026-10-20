"""Writer: speichert normalisierte Daten in InfluxDB, mit dauerhaftem Puffer bei DB-Ausfall.

Abonniert: telemetry, state, quality, availability, raw. Verarbeitet keine Rohdaten fachlich –
raw wird nur 1:1 in ems_raw (7 Tage) zur Fehlersuche abgelegt.
"""
import json
import threading
import time

from influxdb_client import InfluxDBClient, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from influxdb_client.rest import ApiException

from ..common import log as logsetup
from ..common.config import env, secret
from ..common.mqtt import Bus
from ..common.timeutil import iso, utcnow
from . import convert
from .buffer import Buffer

log = logsetup.setup("ems.writer")


class Writer:
    def __init__(self, write_fn, buffer: Buffer, batch_size: int = 500):
        self.write_fn = write_fn            # write_fn(bucket, [lines]) -> wirft bei Fehler
        self.buffer = buffer
        self.batch_size = batch_size
        self.pending: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        self.db_ok = True

    def handle(self, topic: str, payload: bytes):
        try:
            msg = json.loads(payload)
            parts = topic.split("/")
            kind = parts[2]
            if kind == "telemetry":
                item = convert.telemetry(msg)
            elif kind == "state":
                item = convert.state(msg)
            elif kind == "availability":
                item = convert.availability(parts[3], msg)
            elif kind == "quality":
                item = convert.quality(msg, iso(utcnow()))
            elif kind == "raw":
                item = convert.raw(msg)
            else:
                return
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("Nachricht auf %s nicht verwertbar: %s", topic, exc)
            return
        if item:
            with self.lock:
                self.pending.append(item)

    def flush(self):
        with self.lock:
            items, self.pending = self.pending, []
        if self.buffer.count() > 0:
            # erst den Puffer abarbeiten, damit die Reihenfolge erhalten bleibt
            if items:
                self.buffer.add(items)
                items = []
            self._drain_buffer()
            return
        if items and not self._write(items):
            self.buffer.add(items)
            log.warning("InfluxDB nicht erreichbar – %d Zeilen gepuffert (gesamt %d)", len(items), self.buffer.count())

    def _drain_buffer(self):
        while True:
            rows = self.buffer.peek(self.batch_size)
            if not rows:
                if not self.db_ok:
                    log.info("Puffer vollständig nachgeschrieben")
                self.db_ok = True
                return
            if not self._write([(b, l) for _, b, l in rows]):
                return
            self.buffer.delete_upto(rows[-1][0])

    def _write(self, items: list[tuple[str, str]]) -> bool:
        by_bucket: dict[str, list[str]] = {}
        for bucket, line in items:
            by_bucket.setdefault(bucket, []).append(line)
        try:
            for bucket, lines in by_bucket.items():
                self.write_fn(bucket, lines)
            return True
        except ApiException as exc:
            if exc.status in (400, 422):
                # Datenbank lehnt die Daten selbst ab (Format, Aufbewahrung) – erneutes Senden hilft nie
                # und würde den Puffer dauerhaft blockieren -> protokollieren und verwerfen.
                log.error("InfluxDB lehnt %d Zeilen dauerhaft ab (%s): %s", len(items), exc.status,
                          (exc.body or "")[:300])
                return True
            if self.db_ok:
                log.error("Schreiben in InfluxDB fehlgeschlagen: %s", exc.status)
            self.db_ok = False
            return False
        except Exception as exc:  # Netz, 5xx, Auth – alles führt in den Puffer
            if self.db_ok:
                log.error("Schreiben in InfluxDB fehlgeschlagen: %s", exc)
            self.db_ok = False
            return False


def run():
    org = env("EMS_INFLUX_ORG", "keltenstr21")
    client = InfluxDBClient(url=env("EMS_INFLUX_URL", "http://influxdb:8086"),
                            token=secret("influx-tokens/writer_token"), org=org, timeout=10_000)
    api = client.write_api(write_options=SYNCHRONOUS)

    def write_fn(bucket, lines):
        api.write(bucket=bucket, org=org, record=lines, write_precision=WritePrecision.MS)

    buffer = Buffer(env("EMS_BUFFER_PATH", "/var/lib/ems-writer/buffer.sqlite"),
                    int(env("EMS_BUFFER_MAX_ROWS", "500000")))
    writer = Writer(write_fn, buffer)
    if buffer.count():
        log.info("Start mit %d gepufferten Zeilen", buffer.count())
    Bus("ems-writer", "writer", None,
        subscriptions=["ems/v1/telemetry/#", "ems/v1/state/#", "ems/v1/quality/#",
                       "ems/v1/availability/#", "ems/v1/raw/#"],
        on_message=writer.handle).start()
    last_dropped = 0
    while True:
        time.sleep(2)
        writer.flush()
        if buffer.dropped != last_dropped:
            log.error("Puffergrenze erreicht – bisher %d älteste Zeilen verworfen", buffer.dropped)
            last_dropped = buffer.dropped
