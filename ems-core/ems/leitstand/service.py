"""Leitstand – eigene Weboberfläche des EMS (nur lesend), hinter Caddy unter https://192.168.0.172:8444/
(eigener Port, damit Grafana auf 8443 unverändert parallel läuft).

GET /                    Oberfläche (static/)
GET /api/lage            Kopfzeile + alle drei Reiter (zwischengespeichert, 15 s)
GET /api/verlauf?bereich=fb|wp&spanne=30m|60m|180m|6h|12h|24h|7d|30d
GET /api/raum?id=<asset_id>&spanne=…   Raum-Detailfenster (Ist, Soll, Feuchte, zugeordnete Rückläufe)
GET /healthz
Kein schreibender Pfad: Schaltbefehle kommen erst mit dem Steuerdienst (Phase 8).
"""
import json
import mimetypes
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..common.config import env, secret
from ..common.log import setup
from ..common.timeutil import iso, utcnow
from . import views
from .queries import SERIES, SPANNEN, Influx, raum_filter

log = setup("leitstand")
STATIC = Path(__file__).parent / "static"
CACHE_S = 15


def _json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(type(o))


def load_shelly(path: Path) -> list[dict]:
    try:
        return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("geraete", [])
    except FileNotFoundError:
        return []


class Leitstand:
    def __init__(self, influx: Influx, shelly_path: Path):
        self.influx, self.shelly_path = influx, shelly_path
        self._lock = threading.Lock()
        self._cache: dict = {}

    def _cached(self, key, fn, ttl=CACHE_S):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def strom_series(self, spanne: str) -> dict:
        """Zählerstände als letzter Wert je Fenster (für Differenzen), Leistung/Phasen als Mittel."""
        f = SERIES["strom"]
        totals = self.influx.series(f + ' and r.quantity =~ /_total$/', spanne, fn="last")
        others = self.influx.series(f + ' and not (r.quantity =~ /_total$/)', spanne)
        totals["reihen"] += others["reihen"]
        # Ø-Leistung aus kWh-Sprüngen (geschätzt); Vorlauf von 2 Tagen, damit der erste Abschnitt einen Beginn hat
        von = datetime.fromtimestamp(totals["von"] / 1000, timezone.utc)
        pts = self.influx.counter_points("hausanschluss", "energy_import_total", von - timedelta(days=2))
        est = views.power_from_steps(pts, datetime.fromtimestamp(totals["bis"] / 1000, timezone.utc))
        totals["schaetzung"] = [[int(a.timestamp() * 1000), int(b.timestamp() * 1000), w, ok]
                                for a, b, w, ok in est["abschnitte"] if b.timestamp() * 1000 >= totals["von"]]
        return totals

    def lage(self) -> dict:
        now = utcnow()
        ix = self.influx
        latest, states, avail = ix.latest_telemetry(), ix.states(), ix.availability()
        shelly = load_shelly(self.shelly_path)
        fb = views.fb_view(now, latest, states)
        wp = views.wp_view(now, latest, states, avail, ix.quality("-7d"))
        raw_last = ix.raw_last()
        local = now.astimezone(ZoneInfo("Europe/Berlin"))
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = {"heute": midnight, "monat": midnight.replace(day=1), "jahr": midnight.replace(month=1, day=1)}
        anfang = {k: ix.counter_first("hausanschluss", "energy_import_total", v.astimezone(timezone.utc))
                  for k, v in starts.items()}
        strom = views.strom_view(now, latest, avail, raw_last, anfang, ix.device_state("wattwaechter"))
        est = views.power_from_steps(ix.counter_points("hausanschluss", "energy_import_total", now - timedelta(days=2)), now)
        strom["schaetzung"] = {k: v for k, v in est.items() if k != "abschnitte"}
        kopf = views.header(now, latest, avail, raw_last, ix.backups(), fb, wp, len(shelly), strom)
        return {"stand": iso(now), "gesamt": views.overall(kopf), "kopf": kopf, "fb": fb, "wp": wp,
                "strom": strom, "shelly": {"geraete": shelly}}


def make_handler(app: Leitstand):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ems-leitstand"
        sys_version = ""

        def log_message(self, fmt, *args):      # Zugriffe protokolliert Caddy
            pass

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code=200):
            self._send(code, json.dumps(data, default=_json_default, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            try:
                if url.path == "/healthz":
                    return self._send(200, b"ok", "text/plain")
                if url.path == "/api/lage":
                    return self._json(app._cached("lage", app.lage))
                if url.path in ("/api/verlauf", "/api/raum"):
                    q = parse_qs(url.query)
                    spanne = q.get("spanne", ["24h"])[0]
                    if spanne not in SPANNEN:
                        return self._json({"fehler": "spanne unbekannt"}, 400)
                    if url.path == "/api/verlauf":
                        bereich = q.get("bereich", ["fb"])[0]
                        if bereich not in SERIES:
                            return self._json({"fehler": "bereich unbekannt"}, 400)
                        key, flt = f"v|{bereich}|{spanne}", SERIES[bereich]
                    else:
                        raum = q.get("id", [""])[0]
                        try:
                            flt = raum_filter(raum)
                        except ValueError as e:
                            return self._json({"fehler": str(e)}, 400)
                        key = f"r|{raum}|{spanne}"
                    ttl = 20 if SPANNEN[spanne][0] <= 3 * 3600 else 60
                    if key.startswith("v|strom|"):
                        return self._json(app._cached(key, lambda: app.strom_series(spanne), ttl=ttl))
                    return self._json(app._cached(key, lambda: app.influx.series(flt, spanne), ttl=ttl))
                name = "index.html" if url.path in ("/", "/index.html") else url.path.lstrip("/")
                file = (STATIC / name).resolve()
                if STATIC.resolve() not in file.parents or not file.is_file():
                    return self._send(404, b"nicht gefunden", "text/plain; charset=utf-8")
                ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self._send(200, file.read_bytes(), ctype, "no-cache")
            except Exception as e:  # Datenbank nicht erreichbar o. Ä. -> Oberfläche zeigt Störung
                log.exception("Anfrage %s fehlgeschlagen", url.path)
                return self._json({"fehler": f"{type(e).__name__}: {e}"[:300]}, 503)

    return Handler


def run():
    influx = Influx(env("EMS_INFLUX_URL", "http://influxdb:8086"), env("EMS_INFLUX_ORG", "keltenstr21"),
                    secret("influx-tokens/leitstand_token"))
    app = Leitstand(influx, Path(env("EMS_SHELLY_INVENTORY", "/app/inventory/shelly.yaml")))
    port = int(env("EMS_LEITSTAND_PORT", "8080"))
    log.info("Leitstand lauscht auf Port %d", port)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(app)).serve_forever()
