"""Leitstand: Abfragen an InfluxDB (nur lesend, eigener Lese-Token)."""
from influxdb_client import InfluxDBClient

TEL_KEYS = ("asset_id", "name", "floor", "quantity", "role", "source", "medium", "room_asset_id", "unit")
STATE_FIELDS = ("scene", "next_scene", "next_change_at", "mode", "dhw_mode", "dhw_overrun_active", "heating_mode",
                "lpc_limit_active", "lpc_limit_w", "failsafeDurationMinimum", "failsafeConsumptionActivePowerLimit")

# Verläufe je Reiter: Flux-Filter + Einordnung für die Anzeige
SERIES = {
    "fb": 'r.source == "controme" and ((r.medium == "room_climate" and (r.role == "room_actual" or '
          'r.role == "setpoint_effective") and r.quantity != "relative_humidity") or r.role == "return_flow" '
          'or (r.medium == "heat" and (r.role == "flow_actual" or r.role == "flow_setpoint")))',
    "wp": 'r.source == "eebus" and ((r.asset_id == "vaillant_wp" and r.quantity == "active_power") or '
          'r.asset_id == "vaillant_warmwasser" or r.asset_id == "vaillant_zone_1" or r.asset_id == "vaillant_aussenfuehler")',
}
SPANNEN = {"24h": ("-24h", "10m"), "7d": ("-7d", "1h"), "30d": ("-30d", "6h")}


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

    def series(self, bereich: str, spanne: str) -> list[dict]:
        start, every = SPANNEN[spanne]
        flux = f'''from(bucket: "ems_detail") |> range(start: {start})
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value")
  |> filter(fn: (r) => {SERIES[bereich]})
  |> group(columns: ["asset_id", "name", "quantity", "role", "unit", "room_asset_id"])
  |> aggregateWindow(every: {every}, fn: mean, createEmpty: false)'''
        out = {}
        for r in self._records(flux):
            key = f'{r.values["asset_id"]}|{r.values["quantity"]}'
            s = out.setdefault(key, {"key": key, "asset_id": r.values["asset_id"], "name": r.values.get("name"),
                                     "quantity": r.values["quantity"], "role": r.values.get("role"),
                                     "unit": r.values.get("unit"), "raum": r.values.get("room_asset_id"),
                                     "punkte": []})
            if r.get_value() is not None:
                s["punkte"].append([int(r.get_time().timestamp() * 1000), round(r.get_value(), 2)])
        return list(out.values())
