"""Leitstand – eigene Weboberfläche des EMS (nur lesend), hinter Caddy unter https://192.168.0.172:8444/
(eigener Port, damit Grafana auf 8443 unverändert parallel läuft).

GET /                    Oberfläche (static/)
GET /api/lage            Kopfzeile + alle drei Reiter (zwischengespeichert, 15 s)
GET /api/verlauf?bereich=fb|wp&spanne=30m|60m|180m|6h|12h|24h|7d|30d
GET /api/raum?id=<asset_id>&spanne=…   Raum-Detailfenster (Ist, Soll, Feuchte, zugeordnete Rückläufe)
GET /api/tarife   POST /api/tarif (neue Preisversion / Korrektur)
GET /api/meldungen?quelle=eebus|controme|wattwaechter   GET /api/meldung?key=…   POST /api/meldung (Bewertung)
GET /healthz
Einziger schreibender Pfad: Bewertung von Meldungen (SQLite). Schaltbefehle kommen erst mit dem Steuerdienst (Phase 8).
"""
import json
import mimetypes
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from ..common.config import env, secret
from ..common.log import setup
from ..common.timeutil import iso, utcnow
from . import meldungen, views
from ..kosten.tarife import ZAEHLPUNKTE, Tarife, grundkosten
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
    def __init__(self, influx: Influx, shelly_path: Path, ablage: "meldungen.Ablage", tarife: Tarife):
        self.influx, self.shelly_path, self.ablage, self.tarife = influx, shelly_path, ablage, tarife
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._wp_tage: dict = {}            # abgeschlossene Tage -> berechnete WP-Energie (ändern sich nicht mehr)

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

    def kosten(self, zp: str, now: datetime) -> dict:
        """Kosten heute / Monat / Jahr: Arbeitspreis aus den beim Eintreffen berechneten Werten, Grundpreis aus der
        Preishistorie (zeitanteilig)."""
        tz = ZoneInfo("Europe/Berlin")
        local = now.astimezone(tz)
        mitternacht = local.replace(hour=0, minute=0, second=0, microsecond=0)
        start = {"heute": mitternacht, "monat": mitternacht.replace(day=1), "jahr": mitternacht.replace(month=1, day=1)}
        stunden = self._cached(f"kosten|{zp}", lambda: self.influx.kosten_stunden(
            zp, start["jahr"].astimezone(timezone.utc), now + timedelta(hours=1)), ttl=60)
        out = {}
        for k, a in start.items():
            s_ = views.kosten_summe(stunden, a.astimezone(timezone.utc), now)
            s_["eur_grund"] = grundkosten(self.tarife, zp, a.astimezone(timezone.utc), now)
            s_["eur"] = round(s_["eur_arbeit"] + s_["eur_grund"], 2)
            out[k] = s_
        aktuell = self.tarife.preis_zu(zp, now)
        out["preis"] = None if not aktuell else {k: aktuell[k] for k in ("id", "arbeitspreis_ct", "grundpreis_eur_monat", "vorlaeufig", "gueltig_ab")}
        return out

    def tarif_uebersicht(self) -> dict:
        zps = {zp: {**meta, "versionen": self.tarife.versionen(zp)} for zp, meta in ZAEHLPUNKTE.items()}
        gueltige = {zp: self.tarife.gueltige(zp) for zp in ZAEHLPUNKTE}
        hinweise = self._cached("kosten_hinweise", lambda: views.neuberechnung_hinweise(self.influx.kosten_gruppen(), gueltige), ttl=60)
        return {"zaehlpunkte": zps, "journal": self.tarife.journal(30), "hinweise": hinweise}

    def _gruppen(self) -> dict:
        return self._cached("meldungen", lambda: meldungen.gruppieren(self.influx.quality("-7d"),
                                                                      self.influx.ausfaelle("-7d")))

    def meldungsliste(self, quelle: str | None) -> list[dict]:
        ann = self.ablage.alle()
        rows = [meldungen.bewerten(g, ann.get(k)) for k, g in self._gruppen().items()
                if quelle is None or g["quelle"] == quelle]
        return meldungen.sortieren(rows)

    def meldung(self, key: str) -> dict | None:
        g = self._gruppen().get(key)
        if g is None:
            return None
        row = meldungen.bewerten(g, self.ablage.alle().get(key))
        row["vorkommen"] = [t.isoformat() for t in sorted(g["vorkommen"], reverse=True)[:50]]
        row["erklaerung"] = meldungen.ERKLAERUNG.get(g["art"], "")
        row["journal"] = self.ablage.journal(key)
        return row

    def _wp_tag(self, d: date, now: datetime, first: datetime) -> dict:
        tz = ZoneInfo("Europe/Berlin")
        if d in self._wp_tage:
            return self._wp_tage[d]
        start = datetime.combine(d, dtime(0), tz).astimezone(timezone.utc)
        end = datetime.combine(d + timedelta(days=1), dtime(0), tz).astimezone(timezone.utc)
        stop = min(end, now)
        ix = self.influx
        power = ix.points_between("vaillant_wp", "active_power", start - timedelta(minutes=20), stop)
        temps = ix.points_between("vaillant_warmwasser", "temperature", start - timedelta(hours=1), stop)
        e = views.wp_energy(power, temps, start, stop)
        moeglich = max(0.0, (stop - max(start, first)).total_seconds() / 3600)
        e.update({"datum": d.isoformat(), "moeglich_h": round(moeglich, 2),
                  "abdeckung_pct": round(100 * e["abdeckung_h"] / moeglich) if moeglich > 0 else None})
        if end <= now:
            self._wp_tage[d] = e
        return e

    def wp_energie(self, now: datetime) -> dict | None:
        """WP-Verbrauch heute/Monat/Jahr + Tageswerte, aus der EEBUS-Leistung berechnet (siehe views.wp_energy)."""
        first = self._cached("wp_first", lambda: self.influx.first_time("vaillant_wp", "active_power"), ttl=3600)
        if first is None:
            return None
        tz = ZoneInfo("Europe/Berlin")
        today = now.astimezone(tz).date()
        d = max(first.astimezone(tz).date(), date(today.year, 1, 1))
        tage = []
        while d <= today:
            tage.append(self._wp_tag(d, now, first))
            d += timedelta(days=1)

        def summe(sel):
            keys = ("heizung", "warmwasser", "gesamt", "abdeckung_h", "moeglich_h", "ladungen")
            out = {k: round(sum(t[k] for t in sel), 3) for k in keys}
            out["abdeckung_pct"] = round(100 * out["abdeckung_h"] / out["moeglich_h"]) if out["moeglich_h"] else None
            return out
        return {"heute": tage[-1], "monat": summe([t for t in tage if t["datum"][:7] == today.isoformat()[:7]]),
                "jahr": summe(tage), "seit": first.isoformat(), "tage": tage[-30:]}

    def lage(self) -> dict:
        now = utcnow()
        ix = self.influx
        latest, states, avail = ix.latest_telemetry(), ix.states(), ix.availability()
        shelly = load_shelly(self.shelly_path)
        fb = views.fb_view(now, latest, states, ix.ventil_mittel(60))
        wp = views.wp_view(now, latest, states, avail, ix.quality("-7d"))
        wp["energie_berechnet"] = self.wp_energie(now)
        raw_last = ix.raw_last()
        local = now.astimezone(ZoneInfo("Europe/Berlin"))
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = {"heute": midnight, "monat": midnight.replace(day=1), "jahr": midnight.replace(month=1, day=1)}
        anfang = {k: ix.counter_first("hausanschluss", "energy_import_total", v.astimezone(timezone.utc))
                  for k, v in starts.items()}
        strom = views.strom_view(now, latest, avail, raw_last, anfang, ix.device_state("wattwaechter"))
        strom["kosten"] = self.kosten("haushalt", now)
        wp["kosten"] = self.kosten("waermepumpe", now)
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
                if url.path == "/api/tarife":
                    return self._json(app.tarif_uebersicht())
                if url.path == "/api/meldungen":
                    q = parse_qs(url.query)
                    return self._json({"meldungen": app.meldungsliste(q.get("quelle", [None])[0])})
                if url.path == "/api/meldung":
                    m = app.meldung(parse_qs(url.query).get("key", [""])[0])
                    return self._json(m) if m else self._json({"fehler": "Meldung nicht gefunden"}, 404)
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

        def _herkunft_ok(self) -> bool:
            # Schutz gegen fremde Seiten: nur JSON und nur von der eigenen Herkunft (Browser setzen Origin bei POST).
            origin = self.headers.get("Origin", "")
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
            return bool(origin) and urlparse(origin).netloc == host

        def do_POST(self):
            # Schreibende Pfade nur für Verwaltungsdaten des Leitstands (Meldungen, Preise) – nie Geräte.
            url = urlparse(self.path)
            try:
                if url.path not in ("/api/meldung", "/api/tarif"):
                    return self._json({"fehler": "nicht gefunden"}, 404)
                if not self._herkunft_ok():
                    return self._json({"fehler": "Herkunft nicht erlaubt"}, 403)
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    return self._json({"fehler": "JSON erwartet"}, 415)
                length = min(int(self.headers.get("Content-Length", "0") or 0), 10_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                von = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                if url.path == "/api/tarif":
                    ersetzt = body.get("ersetzt")
                    neu = app.tarife.anlegen(body, von, ersetzt=int(ersetzt) if ersetzt not in (None, "") else None)
                    log.info("Preis angelegt von %s: %s %s ct ab %s%s", von, neu["zaehlpunkt"], neu["arbeitspreis_ct"],
                             neu["gueltig_ab"], f" (ersetzt {ersetzt})" if ersetzt else "")
                    with app._lock:
                        app._cache.pop("kosten_hinweise", None)
                    return self._json(app.tarif_uebersicht())
                key = str(body.get("key", ""))
                g = app._gruppen().get(key)
                if g is None:
                    return self._json({"fehler": "Meldung nicht gefunden"}, 404)
                app.ablage.aendern(key, body, {"text": g["text"], "quelle": g["quelle"]}, von)
                log.info("Meldung %s geändert von %s: %s", key, von,
                         {k: body[k] for k in ("status", "wichtigkeit") if k in body})
                return self._json(app.meldung(key))
            except ValueError as e:
                return self._json({"fehler": str(e)}, 400)
            except Exception as e:
                log.exception("POST %s fehlgeschlagen", url.path)
                return self._json({"fehler": f"{type(e).__name__}: {e}"[:300]}, 500)

    return Handler


def run():
    influx = Influx(env("EMS_INFLUX_URL", "http://influxdb:8086"), env("EMS_INFLUX_ORG", "keltenstr21"),
                    secret("influx-tokens/leitstand_token"))
    app = Leitstand(influx, Path(env("EMS_SHELLY_INVENTORY", "/app/inventory/shelly.yaml")),
                    meldungen.Ablage(Path(env("EMS_LEITSTAND_DB", "/var/lib/ems-leitstand/meldungen.sqlite"))),
                    Tarife(Path(env("EMS_TARIFE_DB", "/var/lib/ems-tarife/tarife.sqlite")), schreibend=True))
    port = int(env("EMS_LEITSTAND_PORT", "8080"))
    log.info("Leitstand lauscht auf Port %d", port)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(app)).serve_forever()
