#!/bin/bash
# Läuft genau einmal beim ersten Start von InfluxDB (nach "influx setup").
# Legt Buckets an (Umsetzungskonzept Kap. 4), erzeugt eingeschränkte Tokens und die
# stündliche Verdichtung. Tokens landen in /run/influx-tokens (= secrets/influx-tokens).
set -euo pipefail
ORG="${DOCKER_INFLUXDB_INIT_ORG}"

bucket() {  # bucket <name> <aufbewahrung>
  influx bucket list --org "$ORG" --name "$1" --hide-headers >/dev/null 2>&1 \
    || influx bucket create --org "$ORG" --name "$1" --retention "$2"
}
bucket ems_raw    7d     # Rohpayloads zur Fehlersuche
bucket ems_agg    0      # Stunden-/Tagesaggregate, unbegrenzt
bucket ems_events 1825d  # Verfügbarkeit, Qualität, Zustände, später Befehlsjournal
# ems_detail (400 d) legt das Setup selbst an

id() { influx bucket list --org "$ORG" --name "$1" --hide-headers | cut -f1; }
RAW=$(id ems_raw); DET=$(id ems_detail); AGG=$(id ems_agg); EVT=$(id ems_events)

token() {  # token <beschreibung> <datei> <rechte…>
  local desc="$1" file="$2"; shift 2
  [ -s "/run/influx-tokens/$file" ] && return 0
  influx auth create --org "$ORG" --description "$desc" "$@" --hide-headers \
    | cut -f3 | tr -d '\n' > "/run/influx-tokens/$file"
  chmod 600 "/run/influx-tokens/$file"
}
# Schreibdienst: nur schreiben, nur in seine Buckets
token "ems-writer" writer_token \
  --write-bucket "$RAW" --write-bucket "$DET" --write-bucket "$EVT"
# Grafana: nur lesen
token "ems-grafana-read" grafana_token \
  --read-bucket "$RAW" --read-bucket "$DET" --read-bucket "$AGG" --read-bucket "$EVT"
# Leitstand: nur lesen (inkl. ems_agg für Kostenvergleiche über die 400-Tage-Grenze hinaus)
token "ems-leitstand-read" leitstand_token \
  --read-bucket "$RAW" --read-bucket "$DET" --read-bucket "$EVT" --read-bucket "$AGG"
# Kosten-Nachberechnung (python -m ems kosten-rechnung): liest Mengen, schreibt Kosten + Stundensummen
token "ems-kosten" kosten_token \
  --read-bucket "$DET" --read-bucket "$AGG" --write-bucket "$DET" --write-bucket "$AGG"

# Stündliche Verdichtung ems_detail -> ems_agg:
# Momentanwerte (Temperatur, Leistung …) als Mittelwert, Zählerstände (*_total) als letzter Wert.
cat > /tmp/agg.flux <<'FLUX'
option task = {name: "ems_agg_hourly", every: 1h, offset: 5m}

data = from(bucket: "ems_detail")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "telemetry" and r._field == "value")

data
  |> filter(fn: (r) => not (r.quantity =~ /_total$/))
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> set(key: "agg", value: "mean_1h")
  |> to(bucket: "ems_agg")

data
  |> filter(fn: (r) => r.quantity =~ /_total$/)
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
  |> set(key: "agg", value: "last_1h")
  |> to(bucket: "ems_agg")
FLUX
influx task create --org "$ORG" -f /tmp/agg.flux >/dev/null

# Kosten je Stunde -> ems_agg (Konzept docs/Kosten_Tarife_Konzept.md)
cat > /tmp/kosten.flux <<'FLUX'
option task = {name: "ems_kosten_hourly", every: 1h, offset: 7m}

from(bucket: "ems_detail")
  |> range(start: -3h)
  |> filter(fn: (r) => r._measurement == "kosten" and (r._field == "menge" or r._field == "kosten_eur"))
  |> aggregateWindow(every: 1h, fn: sum, createEmpty: false)
  |> set(key: "agg", value: "sum_1h")
  |> to(bucket: "ems_agg")
FLUX
influx task create --org "$ORG" -f /tmp/kosten.flux >/dev/null
echo "EMS-Init: Buckets, Tokens und Verdichtung angelegt."
