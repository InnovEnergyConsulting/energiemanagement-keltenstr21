#!/usr/bin/env bash
# Wiederherstellungstest: neuester Snapshot -> Wegwerf-InfluxDB im Projekt "ems-restoretest",
# Vergleich mit dem Produktivsystem, Prüfung der Geheimnisse, danach komplett aufräumen.
# Ergebnis wird in ems_events (measurement "backup", target=restore_test) vermerkt.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -f /mnt/ems-backup/.ems-backup-ziel ] || { echo "USB-Sicherungsziel nicht eingehängt"; exit 1; }
export RESTORE_DIR="/mnt/ems-backup/restoretest-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RESTORE_DIR"/{influx-data,influx-config}
C="docker compose -f restore-test/docker-compose.yml"
OK=1; MSG=()
cleanup() { $C down -v --remove-orphans >/dev/null 2>&1 || true
            docker run --rm -v /mnt/ems-backup:/b alpine:3.20 rm -rf "/b/$(basename "$RESTORE_DIR")" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "1/4 Snapshot wiederherstellen nach $RESTORE_DIR"
$C run --rm restore >/dev/null
SNAP_TIME=$(ls "$RESTORE_DIR/tmp/stage/influx/" | grep -m1 manifest | cut -c1-16)   # 20261020T120318Z
STOP=$(date -u -d "${SNAP_TIME:0:4}-${SNAP_TIME:4:2}-${SNAP_TIME:6:2} ${SNAP_TIME:9:2}:${SNAP_TIME:11:2}:${SNAP_TIME:13:2} UTC - 1 hour" +%Y-%m-%dT%H:%M:%SZ)

echo "2/4 Wegwerf-InfluxDB starten und vollständig wiederherstellen"
$C up -d --wait influxdb-test >/dev/null
$C exec -T influxdb-test influx restore --full --host http://localhost:8086 \
   --token restoretest-wegwerf-token /restore/tmp/stage/influx >/dev/null

echo "3/4 Daten vergleichen (bis $STOP, also 1 h vor dem Snapshot)"
TOKEN=$(cat secrets/influx_admin_token)   # nach --full gelten die gesicherten Zugangsdaten
Q="from(bucket:\"ems_detail\") |> range(start: 2026-01-01T00:00:00Z, stop: $STOP) |> filter(fn:(r)=> r._field==\"value\") |> group() |> count()"
count() { "$@" influx query --org keltenstr21 --token "$TOKEN" --raw "$Q" 2>/dev/null | grep -E "^,,[0-9]" | awk -F, '{print $NF}' | tr -d '\r'; }
PROD=$(count docker exec ems-influxdb); TEST=$(count $C exec -T influxdb-test)
echo "    Messwerte Produktiv: ${PROD:-?}   Wiederhergestellt: ${TEST:-?}"
if [ -z "$PROD" ] || [ "$PROD" != "$TEST" ]; then OK=0; MSG+=("Messwerte weichen ab ($PROD/$TEST)"); fi
for b in ems_events ems_raw ems_agg; do
  $C exec -T influxdb-test influx bucket list --org keltenstr21 --token "$TOKEN" --name $b >/dev/null 2>&1 \
    || { OK=0; MSG+=("Bucket $b fehlt"); }
done

echo "4/4 Geheimnisse und Konfiguration vergleichen"
for f in restic_password influx_admin_token grafana_admin_password mqtt_writer influx-tokens/writer_token; do
  cmp -s "secrets/$f" "$RESTORE_DIR/src/secrets/$f" || { OK=0; MSG+=("secrets/$f weicht ab"); }
done
docker run --rm -v "$RESTORE_DIR/src/secrets:/r:ro" -v "$PWD/secrets:/p:ro" alpine:3.20 sh -c \
  'cmp -s /r/mosquitto/passwd /p/mosquitto/passwd && cmp -s /r/mqtt-tls/server.key /p/mqtt-tls/server.key' \
  || { OK=0; MSG+=("Broker-Dateien weichen ab"); }
diff -rq config "$RESTORE_DIR/src/config" >/dev/null || { OK=0; MSG+=("config/ weicht ab"); }

TEXT="Snapshot $SNAP_TIME: $PROD Messwerte identisch, Buckets + Geheimnisse ok"
[ $OK = 1 ] || TEXT="FEHLER: ${MSG[*]}"
docker exec ems-influxdb influx write --org keltenstr21 --token "$TOKEN" --bucket ems_events --precision s \
  "backup,target=restore_test ok=${OK}i,duration_s=${SECONDS},detail=\"${TEXT:0:250}\" $(date +%s)" >/dev/null
echo; [ $OK = 1 ] && echo "ERFOLG: $TEXT" || { echo "$TEXT"; exit 1; }
