#!/usr/bin/env bash
# Erzeugt einmalig alle Zugangsdaten des EMS in ./secrets (chmod 600, nicht im Git).
# Bereits vorhandene Dateien werden NICHT überschrieben – das Skript ist wiederholbar.
#
#   secrets/influx_admin_password   InfluxDB-Admin (nur Notfall/Wartung)
#   secrets/influx_admin_token      InfluxDB-Admin-Token (nur Initialisierung)
#   secrets/grafana_admin_password  Grafana-Admin
#   secrets/mqtt_<konto>            je ein MQTT-Konto pro Dienst
#   secrets/mosquitto/passwd        gehashte MQTT-Passwörter (Besitzer 1883 = mosquitto)
#   secrets/mqtt-tls/               eigene CA + Serverzertifikat für Port 8883
#   secrets/influx-tokens/          von InfluxDB beim ersten Start erzeugte, eingeschränkte Tokens
#
# Werte zusätzlich in Vaultwarden ablegen (Sammlung "EMS Keltenstr. 21").
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
mkdir -p secrets/mosquitto secrets/mqtt-tls secrets/influx-tokens secrets/rclone secrets/eebus

gen() {  # gen <datei> – 32 Zeichen, nur URL-sichere Zeichen
  [ -s "secrets/$1" ] && return 0
  python3 -c 'import secrets;print(secrets.token_urlsafe(24),end="")' > "secrets/$1"
  echo "neu: secrets/$1"
}

gen influx_admin_password
gen influx_admin_token
gen grafana_admin_password
gen wattwaechter_web_password   # Weboberfläche/Befehls-API des WattWächters (Benutzer admin)
gen restic_password            # Schlüssel der Sicherung – OHNE ihn ist jedes Backup wertlos -> Vaultwarden + Papier!
MQTT_USERS="ems_admin controme_adapter normalizer writer eebus_adapter wattwaechter"
for u in $MQTT_USERS; do gen "mqtt_$u"; done

# --- Mosquitto-Passwortdatei (Hashes) mit dem Broker-Image selbst erzeugen
if [ ! -s secrets/mosquitto/passwd ]; then
  args=""
  for u in $MQTT_USERS; do args="$args mosquitto_passwd -b /out/passwd $u $(cat secrets/mqtt_$u);"; done
  docker run --rm -v "$PWD/secrets/mosquitto:/out" eclipse-mosquitto:2.0.22 sh -c \
    "touch /out/passwd && chmod 600 /out/passwd && $args chown 1883:1883 /out/passwd && chmod 600 /out/passwd"
  echo "neu: secrets/mosquitto/passwd"
else
  # neu hinzugekommene Konten nachtragen (bestehende bleiben unverändert)
  for u in $MQTT_USERS; do
    docker run --rm -v "$PWD/secrets/mosquitto:/out" eclipse-mosquitto:2.0.22 sh -c "grep -q '^$u:' /out/passwd" \
      || { docker run --rm -v "$PWD/secrets/mosquitto:/out" eclipse-mosquitto:2.0.22 sh -c \
             "mosquitto_passwd -b /out/passwd $u $(cat secrets/mqtt_$u) && chown 1883:1883 /out/passwd && chmod 600 /out/passwd" 2>/dev/null
           echo "MQTT-Konto ergänzt: $u"; }
  done
fi

# --- TLS für MQTT 8883: eigene kleine CA (10 Jahre) + Serverzertifikat (825 Tage) für 192.168.0.172
if [ ! -s secrets/mqtt-tls/server.crt ]; then
  cd secrets/mqtt-tls
  openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=EMS Keltenstr21 MQTT CA" \
    -keyout ca.key -out ca.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=192.168.0.172" -keyout server.key -out server.csr 2>/dev/null
  printf "subjectAltName=IP:192.168.0.172,DNS:mosquitto,DNS:localhost\nextendedKeyUsage=serverAuth\n" > ext.cnf
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
    -extfile ext.cnf -out server.crt 2>/dev/null
  rm -f server.csr ext.cnf
  cd ../..
  # Broker läuft als UID 1883 und muss den Schlüssel lesen können
  docker run --rm -v "$PWD/secrets/mqtt-tls:/tls" eclipse-mosquitto:2.0.22 sh -c \
    "chown 1883:1883 /tls/server.key /tls/server.crt && chmod 600 /tls/server.key && chmod 644 /tls/server.crt /tls/ca.crt"
  echo "neu: secrets/mqtt-tls (CA + Serverzertifikat)"
fi

# Broker (UID 1883) muss den Ordner betreten können; Schlüsseldateien selbst bleiben 600
chmod 755 secrets/mqtt-tls
# Influx-Tokens schreibt der Influx-Init-Container (UID 1000 = horst) hinein
chmod 700 secrets/influx-tokens
echo "fertig."
