"""Kosten rückwirkend berechnen oder neu berechnen (Konzept Kosten/Tarife, Kap. 5).

  python -m ems kosten-rechnung <zählpunkt> <von> <bis> [--trocken]
  z. B. python -m ems kosten-rechnung haushalt 2026-10-21T00:00 2026-10-22T17:00

Verwendet denselben Kostenrechner wie der Normalizer, auf den gespeicherten Mengen (Zählerstände bzw. WP-Leistung).
Bestehende Kosten werden nie überschrieben: Gibt es im Zeitraum schon Kosten, entsteht die nächste
Berechnungsversion (Leitstand nutzt je Stunde die neueste). Danach werden die Stundensummen (ems_agg) erzeugt.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import SYNCHRONOUS

from ..common.config import env, secret
from ..common.timeutil import iso
from ..writer import convert
from .rechner import KostenRechner
from .tarife import Tarife

QUELLEN = {  # Zählpunkt -> (asset_id, quantity, Art)
    "haushalt": ("hausanschluss", "energy_import_total", "zaehler"),
    "einspeisung": ("hausanschluss", "energy_export_total", "zaehler"),
    "waermepumpe": ("vaillant_wp", "active_power", "leistung"),
}


def _t(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _zeit(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo("Europe/Berlin"))


def berechnen(tarife: Tarife, zp: str, punkte: list[tuple[datetime, float]], vor: tuple | None,
              von: datetime, bis: datetime, version: int) -> list[dict]:
    """Reine Funktion (testbar): Mengenpunkte -> Kostenzeilen im Zeitraum, mit gegebener Berechnungsversion."""
    asset, qty, art = QUELLEN[zp]
    recs = []
    r = KostenRechner(tarife, lambda topic, msg, **kw: recs.append(msg) if topic.startswith("ems/v1/kosten/") else None)
    if vor:
        r.seed(zp, {"wert": vor[1], "zeit": iso(vor[0])} if art == "zaehler" else {"watt": vor[1], "zeit": iso(vor[0])})
    for t, v in punkte:
        if art == "zaehler":
            r.zaehlerstand(zp, v, t)
        else:
            r.leistung(zp, v, t)
    out = []
    for rec in recs:
        bis_rec = datetime.fromisoformat(rec["bis"])
        if von < bis_rec <= bis:
            rec["berechnung_version"] = version
            out.append(rec)
    return out


def run(argv: list[str]):
    if len(argv) < 3 or argv[0] not in QUELLEN:
        sys.exit(f"Aufruf: python -m ems kosten-rechnung <{'|'.join(QUELLEN)}> <von> <bis> [--trocken]")
    zp, von, bis, trocken = argv[0], _zeit(argv[1]), _zeit(argv[2]), "--trocken" in argv
    org = env("EMS_INFLUX_ORG", "keltenstr21")
    client = InfluxDBClient(url=env("EMS_INFLUX_URL", "http://influxdb:8086"),
                            token=secret("influx-tokens/kosten_token"), org=org, timeout=60_000)
    q = client.query_api()
    asset, qty, art = QUELLEN[zp]
    flt = f'r._measurement == "telemetry" and r._field == "value" and r.asset_id == "{asset}" and r.quantity == "{qty}"'
    punkte = [(r.get_time(), r.get_value()) for t in q.query(
        f'from(bucket: "ems_detail") |> range(start: {_t(von)}, stop: {_t(bis)}) |> filter(fn: (r) => {flt}) '
        f'|> keep(columns: ["_time", "_value"]) |> sort(columns: ["_time"])') for r in t.records]
    vor = None
    for t in q.query(f'from(bucket: "ems_detail") |> range(start: {_t(von - timedelta(days=2))}, stop: {_t(von)}) '
                     f'|> filter(fn: (r) => {flt}) |> last()'):
        for r in t.records:
            vor = (r.get_time(), r.get_value())
    versionen = [int(r.values.get("berechnung_version") or 1) for t in q.query(
        f'from(bucket: "ems_detail") |> range(start: {_t(von)}, stop: {_t(bis)}) '
        f'|> filter(fn: (r) => r._measurement == "kosten" and r.zaehlpunkt == "{zp}" and r._field == "menge") '
        f'|> group(columns: ["berechnung_version"]) |> last()') for r in t.records]
    version = max(versionen, default=0) + 1
    tarife = Tarife(Path(env("EMS_TARIFE_DB", "/var/lib/ems-tarife/tarife.sqlite")))
    recs = berechnen(tarife, zp, punkte, vor, von, bis, version)
    menge = sum(r["menge"] for r in recs)
    eur = sum(r.get("kosten_eur", 0) for r in recs)
    print(f"{zp}: {len(punkte)} Mengenpunkte, {len(recs)} Kostenzeilen, {menge:.3f} {recs[0]['einheit'] if recs else ''}, "
          f"{eur:.2f} €, Berechnungsversion {version}{' (bestehend: ' + str(sorted(set(versionen))) + ')' if versionen else ''}")
    if trocken or not recs:
        print("Trockenlauf – nichts geschrieben." if trocken else "Nichts zu schreiben.")
        return
    w = client.write_api(write_options=SYNCHRONOUS)
    lines = [convert.kosten(r)[1] for r in recs]
    for i in range(0, len(lines), 2000):
        w.write(bucket="ems_detail", org=org, record=lines[i:i + 2000], write_precision="ms")
    a = (von.astimezone(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    q.query(f'''from(bucket: "ems_detail") |> range(start: {_t(a)}, stop: {_t(bis + timedelta(hours=1))})
  |> filter(fn: (r) => r._measurement == "kosten" and r.zaehlpunkt == "{zp}" and (r._field == "menge" or r._field == "kosten_eur"))
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)
  |> set(key: "agg", value: "sum_1h")
  |> to(bucket: "ems_agg")''')
    print(f"geschrieben: {len(lines)} Zeilen nach ems_detail, Stundensummen nach ems_agg aktualisiert")
