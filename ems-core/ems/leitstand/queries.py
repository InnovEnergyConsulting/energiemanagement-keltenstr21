"""Leitstand: Abfragen an InfluxDB (nur lesend, eigener Lese-Token)."""
import re
from datetime import datetime, timedelta

from influxdb_client import InfluxDBClient

from ..common.timeutil import utcnow

TEL_KEYS = ("asset_id", "name", "floor", "quantity", "role", "source", "medium", "room_asset_id", "unit")
STATE_FIELDS = ("scene", "next_scene", "next_change_at", "mode", "dhw_mode", "dhw_overrun_active", "heating_mode",
                "lpc_limit_active", "lpc_limit_w", "failsafeDurationMinimum", "failsafeConsumptionActivePowerLimit")

# Verläufe je Reiter: Flux-Filter
SERIES = {
    "fb": 'r.source == "controme" and ((r.medium == "room_climate" and (r.role == "room_actual" or '
          'r.role == "setpoint_effective") and r.quantity != "relative_humidity") or r.role == "return_flow" '
          'or (r.medium == "heat" and (r.role == "flow_actual" or r.role == "flow_setpoint")))',
    "strom": 'r.source == "wattwaechter" and r.asset_id == "hausanschluss" and r.role != "diagnosis"',
    "wp": 'r.source == "eebus" and ((r.asset_id == "vaillant_wp" and r.quantity == "active_power") or '
          'r.asset_id == "vaillant_warmwasser" or r.asset_id == "vaillant_zone_1" or r.asset_id == "vaillant_aussenfuehler")',
}
# Zeitspanne -> (Sekunden, Mittelungsfenster). Feiner als die Messrate lohnt nicht (Controme 60 s, EEBUS 5 min).
SPANNEN = {
    "30m": (30 * 60, "1m"), "60m": (60 * 60, "1m"), "180m": (180 * 60, "2m"), "6h": (6 * 3600, "5m"),
    "12h": (12 * 3600, "5m"), "24h": (24 * 3600, "10m"), "7d": (7 * 86400, "30m"), "30d": (30 * 86400, "2h"),
}
# Fühler, die nur bei Änderung senden (Controme-Raumfühler, EEBUS-Benachrichtigungen), gelten bis zu HALTEN_S
# als unverändert: die Oberfläche zeichnet sie als Stufen und setzt den letzten Wert vor Beginn der Spanne fort.
HALTEN_S = 6 * 3600
_ASSET_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def stufig(source: str | None, medium: str | None, role: str | None) -> bool:
    return source == "eebus" or (source == "controme" and medium == "room_climate")


def raum_filter(asset_id: str) -> str:
    """Flux-Filter für das Raum-Detailfenster; asset_id wird streng geprüft (kein Einschleusen in Flux)."""
    if not _ASSET_RE.match(asset_id or ""):
        raise ValueError("ungültige Raum-ID")
    return (f'(r.asset_id == "{asset_id}" and (r.quantity == "temperature" or r.quantity == "temperature_setpoint" '
            f'or r.quantity == "relative_humidity" or r.quantity == "valve_position")) or (r.room_asset_id == "{asset_id}" and r.role == "return_flow")')


def _t(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Influx:
    def __init__(self, url: str, org: str, token: str):
        self.client = InfluxDBClient(url=url, token=token, org=org, timeout=15_000)
        self.q = self.client.query_api()

    def _records(self, flux: str):
        for table in self.q.query(flux):
            yield from table.records

    def latest_telemetry(self) -> list[dict]:
        flux = '''from(bucket: "ems_detail") |> range(start: -2d)
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value")
  |> last()'''
        return [{"time": r.get_time(), "value": r.get_value(), **{k: r.values.get(k) for k in TEL_KEYS}}
                for r in self._records(flux)]

    def states(self) -> dict:
        fields = " or ".join(f'r._field == "{f}"' for f in STATE_FIELDS)
        flux = f'''from(bucket: "ems_events") |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "state" and ({fields}))
  |> last()'''
        out = {}
        for r in self._records(flux):
            out.setdefault(r.values["asset_id"], {})[r.get_field()] = r.get_value()
        return out

    def availability(self) -> dict:
        flux = '''from(bucket: "ems_events") |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "availability" and (r._field == "status" or r._field == "reason"))
  |> last()'''
        out = {}
        for r in self._records(flux):
            c = out.setdefault(r.values["component"], {"since": r.get_time()})
            c[r.get_field()] = r.get_value()
        return out

    def raw_last(self) -> dict:
        flux = '''from(bucket: "ems_raw") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "raw" and r._field == "http_status")
  |> last()'''
        return {(r.values["source"], r.values["stream"]): {"time": r.get_time(), "http_status": r.get_value()}
                for r in self._records(flux)}

    def backups(self) -> dict:
        flux = '''from(bucket: "ems_events") |> range(start: -90d)
  |> filter(fn: (r) => r._measurement == "backup")
  |> last()'''
        out = {}
        for r in self._records(flux):
            b = out.setdefault(r.values["target"], {"time": r.get_time()})
            b[r.get_field()] = r.get_value()
        return out

    def quality(self, since: str = "-7d") -> list[dict]:
        flux = f'''from(bucket: "ems_events") |> range(start: {since})
  |> filter(fn: (r) => r._measurement == "quality" and r._field == "detail")
  |> group() |> sort(columns: ["_time"], desc: true) |> limit(n: 2000)'''
        return [{"time": r.get_time(), "source": r.values.get("source"), "kind": r.values.get("kind"),
                 "detail": r.get_value()} for r in self._records(flux)]

    def counter_first(self, asset_id: str, quantity: str, start: datetime) -> dict | None:
        """Erster Zählerstand ab start (für Verbrauch heute/Monat/Jahr als Zählerdifferenz)."""
        flux = f'''from(bucket: "ems_detail") |> range(start: {_t(start)})
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value" and r.asset_id == "{asset_id}" and r.quantity == "{quantity}")
  |> first()'''
        for r in self._records(flux):
            return {"time": r.get_time(), "value": r.get_value()}
        return None

    def counter_points(self, asset_id: str, quantity: str, start: datetime) -> list[tuple[datetime, float]]:
        """Alle gespeicherten Zählerstände ab start (ohne Mittelung) – für Ø-Leistung aus kWh-Sprüngen."""
        flux = f'''from(bucket: "ems_detail") |> range(start: {_t(start)})
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value" and r.asset_id == "{asset_id}" and r.quantity == "{quantity}")
  |> keep(columns: ["_time", "_value"]) |> sort(columns: ["_time"])'''
        return [(r.get_time(), r.get_value()) for r in self._records(flux)]

    def points_between(self, asset_id: str, quantity: str, start: datetime, stop: datetime) -> list[tuple[datetime, float]]:
        flux = f'''from(bucket: "ems_detail") |> range(start: {_t(start)}, stop: {_t(stop)})
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value" and r.asset_id == "{asset_id}" and r.quantity == "{quantity}")
  |> keep(columns: ["_time", "_value"]) |> sort(columns: ["_time"])'''
        return [(r.get_time(), r.get_value()) for r in self._records(flux)]

    def first_time(self, asset_id: str, quantity: str) -> datetime | None:
        flux = f'''from(bucket: "ems_detail") |> range(start: -400d)
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value" and r.asset_id == "{asset_id}" and r.quantity == "{quantity}")
  |> first() |> keep(columns: ["_time"])'''
        for r in self._records(flux):
            return r.get_time()
        return None

    def device_state(self, source: str) -> dict | None:
        """Letzte Tasmota-STATE-Meldung (WLAN-Signal, Laufzeit) aus den Rohdaten."""
        flux = f'''from(bucket: "ems_raw") |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "raw" and r.source == "{source}" and r.stream == "state" and r._field == "payload")
  |> last()'''
        import json
        for r in self._records(flux):
            try:
                p = json.loads(r.get_value())
            except ValueError:
                return None
            wifi = p.get("Wifi", {}) if isinstance(p, dict) else {}
            return {"zeit": r.get_time().isoformat(), "signal_dbm": wifi.get("Signal"), "uptime": p.get("Uptime"),
                    "ssid": wifi.get("SSId")}
        return None

    def kosten_stunden(self, zaehlpunkt: str, start: datetime, stop: datetime) -> list[dict]:
        """Kosten je Stunde und Berechnungsversion. Jünger als 390 Tage aus ems_detail, älter aus ems_agg (Stundensummen,
        unbegrenzt) – so bleiben Vergleiche über Jahre möglich."""
        grenze = utcnow() - timedelta(days=390)
        teile = []
        if start < grenze:
            teile.append(("ems_agg", start, min(stop, grenze), ' and r.agg == "sum_1h"'))
        if stop > grenze:
            teile.append(("ems_detail", max(start, grenze), stop, ""))
        out = []
        for bucket, a, b, extra in teile:
            flux = f'''from(bucket: "{bucket}") |> range(start: {_t(a)}, stop: {_t(b)})
  |> filter(fn: (r) => r._measurement == "kosten" and r.zaehlpunkt == "{zaehlpunkt}" and (r._field == "menge" or r._field == "kosten_eur"){extra})
  |> group(columns: ["berechnung_version", "herkunft", "vorlaeufig", "tarif_version", "_field"])
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)'''
            for r in self._records(flux):
                v = r.values
                out.append({"stunde": r.get_time(), "version": int(v.get("berechnung_version") or 1),
                            "herkunft": v.get("herkunft"), "vorlaeufig": v.get("vorlaeufig") == "ja",
                            "tarif_version": v.get("tarif_version"), "feld": r.get_field(), "wert": r.get_value()})
        return out

    def kosten_gruppen(self) -> list[dict]:
        """Je Zählpunkt / Preisversion / Berechnungsversion: erster und letzter Zeitpunkt (für Neuberechnungs-Hinweise)."""
        out = {}
        for fn in ("first", "last"):
            flux = f'''from(bucket: "ems_detail") |> range(start: -400d)
  |> filter(fn: (r) => r._measurement == "kosten" and r._field == "menge")
  |> group(columns: ["zaehlpunkt", "tarif_version", "berechnung_version"]) |> {fn}()'''
            for r in self._records(flux):
                v = r.values
                k = (v["zaehlpunkt"], v.get("tarif_version"), int(v.get("berechnung_version") or 1))
                out.setdefault(k, {"zaehlpunkt": k[0], "tarif_version": k[1], "berechnung_version": k[2]})[
                    "von" if fn == "first" else "bis"] = r.get_time()
        return list(out.values())

    def ventil_mittel(self, minuten: int = 60) -> dict[str, float]:
        """Öffnungsanteil je Raum über die letzten `minuten` (Mittel der minütlichen Ventilstellung in %)."""
        flux = f'''from(bucket: "ems_detail") |> range(start: -{minuten}m)
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value" and r.quantity == "valve_position")
  |> group(columns: ["asset_id"]) |> mean()'''
        return {r.values["asset_id"]: r.get_value() for r in self._records(flux)}

    def ausfaelle(self, since: str = "-7d") -> list[dict]:
        """Verbindungsabbrüche der Adapter (availability mit Status offline)."""
        flux = f'''from(bucket: "ems_events") |> range(start: {since})
  |> filter(fn: (r) => r._measurement == "availability" and (r._field == "status" or r._field == "reason"))
  |> pivot(rowKey: ["_time", "component"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.status == "offline")
  |> group() |> sort(columns: ["_time"])'''
        return [{"time": r.get_time(), "source": r.values.get("component"), "status": r.values.get("status"),
                 "reason": r.values.get("reason")} for r in self._records(flux)]

    def series(self, flt: str, spanne: str, fn: str = "mean") -> dict:
        secs, every = SPANNEN[spanne]
        now = utcnow()
        start = now - timedelta(seconds=secs)
        group = '["asset_id", "name", "quantity", "role", "unit", "room_asset_id", "source", "medium"]'
        base = f'''  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value")
  |> filter(fn: (r) => {flt})
  |> group(columns: {group})'''
        main = f'''from(bucket: "ems_detail") |> range(start: {_t(start)}, stop: {_t(now)})
{base}
  |> aggregateWindow(every: {every}, fn: {fn}, createEmpty: false)'''
        before = f'''from(bucket: "ems_detail") |> range(start: {_t(start - timedelta(seconds=HALTEN_S))}, stop: {_t(start)})
{base}
  |> last()'''
        out = {}

        def entry(r):
            v = r.values
            key = f'{v["asset_id"]}|{v["quantity"]}'
            return out.setdefault(key, {
                "key": key, "asset_id": v["asset_id"], "name": v.get("name"), "quantity": v["quantity"],
                "role": v.get("role"), "unit": v.get("unit"), "raum": v.get("room_asset_id"),
                "stufig": stufig(v.get("source"), v.get("medium"), v.get("role")), "vorher": None, "punkte": []})

        for r in self._records(main):
            if r.get_value() is not None:
                entry(r)["punkte"].append([int(r.get_time().timestamp() * 1000), round(r.get_value(), 2)])
        for r in self._records(before):
            e = entry(r)
            if e["stufig"] and r.get_value() is not None:
                e["vorher"] = [int(r.get_time().timestamp() * 1000), round(r.get_value(), 2)]
        for e in out.values():
            e["punkte"].sort()
        return {"von": int(start.timestamp() * 1000), "bis": int(now.timestamp() * 1000),
                "fenster": every, "halten_s": HALTEN_S, "reihen": list(out.values())}
