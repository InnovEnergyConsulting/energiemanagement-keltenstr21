#!/bin/bash
# Baut die App im Container; Signierschlüssel liegt in ../../secrets/zaehler-pin-app (nicht im Git).
set -euo pipefail
cd "$(dirname "$0")"
KS=../../secrets/zaehler-pin-app; mkdir -p "$KS" ~/.cache/android-sdk
if [ ! -f "$KS/zaehler-pin.keystore" ]; then
  python3 -c 'import secrets;print(secrets.token_urlsafe(24),end="")' > "$KS/passwort"; chmod 600 "$KS/passwort"
  docker run --rm -u "$(id -u):$(id -g)" -v "$PWD/$KS:/keystore" eclipse-temurin:17-jdk keytool -genkeypair -keystore /keystore/zaehler-pin.keystore \
    -storepass:file /keystore/passwort -keypass:file /keystore/passwort -alias app -keyalg RSA -keysize 3072 \
    -validity 10000 -dname "CN=Zaehler-PIN Keltenstr21" >/dev/null
fi
docker run --rm -v "$PWD:/app" -v ~/.cache/android-sdk:/sdk -v "$PWD/$KS:/keystore:ro" eclipse-temurin:17-jdk bash /app/build.sh
