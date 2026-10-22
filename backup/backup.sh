#!/bin/bash
# EMS-Sicherung (Phase 3). Aufruf:  ems-backup daemon | now | snapshots | check
#
# Stufe 1 – USB-SSD  : restic-Repository /backup/restic-ems (nur wenn /backup/.ems-backup-ziel existiert,
#                      sonst landete die Sicherung unbemerkt auf der System-SSD)
# Stufe 2 – OneDrive : Kopie desselben Repos per rclone, Ende-zu-Ende verschlüsselt (restic),
#                      nur wenn /config/rclone/rclone.conf einen Eintrag [onedrive] hat
# Inhalt: konsistentes InfluxDB-Backup + config/ + secrets/ + Grafana-/Broker-/Caddy-/Writer-Daten.
# Ergebnis jedes Laufs landet in InfluxDB (ems_events, measurement "backup") -> Grafana.
set -uo pipefail

S=/run/ems-secrets
export RESTIC_PASSWORD_FILE=$S/restic_password
export RESTIC_CACHE_DIR=/backup/.restic-cache
export RCLONE_CONFIG=/config/rclone/rclone.conf
REPO_USB=/backup/restic-ems
REPO_CLOUD="rclone:onedrive:${EMS_CLOUD_PATH:-Backups/EMS-Keltenstr21}"
INFLUX="--host http://influxdb:8086 --org keltenstr21 --token $(cat $S/influx_admin_token)"
RUN_AT="${EMS_BACKUP_TIME:-03:15}"
KEEP="--keep-daily 14 --keep-weekly 8 --keep-monthly 24"

log() { echo "$(date -Is) $*"; }

report() {  # report <ziel> <ok 0|1> <dauer_s> <text>
  local detail="${4//\"/\'}"
  influx write $INFLUX --bucket ems_events --precision s \
    "backup,target=$1 ok=$2i,duration_s=$3,detail=\"${detail:0:250}\" $(date +%s)" >/dev/null 2>&1 \
    || log "Hinweis: Ergebnis konnte nicht in InfluxDB vermerkt werden"
}

cloud_configured() { [ -f "$RCLONE_CONFIG" ] && grep -q '^\[onedrive\]' "$RCLONE_CONFIG"; }

backup_usb() {
  local t0=$SECONDS stage=/tmp/stage
  if [ ! -f /backup/.ems-backup-ziel ]; then
    log "FEHLER: USB-Sicherungsziel nicht eingehängt (/backup/.ems-backup-ziel fehlt)"
    report usb 0 0 "USB-Sicherungsziel nicht eingehaengt"; return 1
  fi
  rm -rf "$stage" && mkdir -p "$stage"
  if ! influx backup $INFLUX "$stage/influx" >/tmp/influx-backup.log 2>&1; then
    log "FEHLER: influx backup"; tail -5 /tmp/influx-backup.log
    report usb 0 $((SECONDS-t0)) "influx backup fehlgeschlagen"; return 1
  fi
  restic -r "$REPO_USB" cat config >/dev/null 2>&1 || { log "Lege USB-Repository an"; restic -r "$REPO_USB" init || return 1; }
  if ! restic -r "$REPO_USB" backup --host ems-keltenstr21 --tag ems --one-file-system \
        "$stage" /src/config /src/secrets /src/data-grafana /src/data-mosquitto /src/data-caddy /src/data-writer /src/data-leitstand /src/data-tarife; then
    report usb 0 $((SECONDS-t0)) "restic backup fehlgeschlagen"; rm -rf "$stage"; return 1
  fi
  rm -rf "$stage"
  restic -r "$REPO_USB" forget --host ems-keltenstr21 $KEEP --prune --quiet || log "Warnung: forget/prune"
  # sonntags zusätzlich 5 % der gespeicherten Daten tatsächlich lesen und prüfen
  if [ "$(date +%u)" = 7 ]; then restic -r "$REPO_USB" check --read-data-subset=5% || { report usb 0 $((SECONDS-t0)) "restic check meldet Fehler"; return 1; }; fi
  local size; size=$(restic -r "$REPO_USB" stats --mode raw-data --json 2>/dev/null | sed -n 's/.*"total_size":\([0-9]*\).*/\1/p')
  report usb 1 $((SECONDS-t0)) "ok, Repository ${size:-?} Bytes"
  log "USB-Sicherung fertig in $((SECONDS-t0)) s"
}

backup_cloud() {
  local t0=$SECONDS
  cloud_configured || { log "OneDrive noch nicht eingerichtet – Stufe 2 übersprungen"; return 0; }
  if ! restic -r "$REPO_CLOUD" cat config >/dev/null 2>&1; then
    log "Lege OneDrive-Repository an (gleiche Chunk-Parameter wie USB -> effiziente Kopie)"
    restic -r "$REPO_CLOUD" init --from-repo "$REPO_USB" --from-password-file "$RESTIC_PASSWORD_FILE" \
      --copy-chunker-params || { report onedrive 0 $((SECONDS-t0)) "init fehlgeschlagen"; return 1; }
  fi
  if restic -r "$REPO_CLOUD" copy --from-repo "$REPO_USB" --from-password-file "$RESTIC_PASSWORD_FILE"; then
    restic -r "$REPO_CLOUD" forget --host ems-keltenstr21 $KEEP --prune --quiet || log "Warnung: forget/prune OneDrive"
    report onedrive 1 $((SECONDS-t0)) "ok"
    log "OneDrive-Kopie fertig in $((SECONDS-t0)) s"
  else
    report onedrive 0 $((SECONDS-t0)) "restic copy fehlgeschlagen"; return 1
  fi
}

run_all() { backup_usb && backup_cloud; }

case "${1:-daemon}" in
  now)       run_all ;;
  snapshots) restic -r "$REPO_USB" snapshots ;;
  check)     restic -r "$REPO_USB" check --read-data-subset=10% ;;
  daemon)
    log "Sicherungsdienst gestartet – täglicher Lauf um $RUN_AT (Ortszeit)"
    while true; do
      now=$(date +%s); target=$(date -d "today $RUN_AT" +%s)
      [ "$target" -le "$now" ] && target=$(date -d "tomorrow $RUN_AT" +%s)
      sleep $((target - now))
      run_all || log "Sicherungslauf mit Fehler beendet (siehe oben)"
    done ;;
  *) echo "Aufruf: ems-backup daemon|now|snapshots|check"; exit 2 ;;
esac
