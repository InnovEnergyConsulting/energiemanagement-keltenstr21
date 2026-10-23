#!/bin/bash
# Baut app/zaehler-pin.apk ohne Gradle/Android Studio: aapt2 (Manifest) -> javac -> d8 (dex) -> zipalign -> apksigner.
# Läuft im Container eclipse-temurin:17-jdk (siehe build-docker.sh); SDK liegt in /sdk (Cache auf dem Host).
set -euo pipefail
SDK=/sdk; BT=$SDK/build-tools/34.0.0; JAR=$SDK/platforms/android-34/android.jar
if [ ! -f "$JAR" ]; then
  apt-get update -qq && apt-get install -y -qq unzip zip >/dev/null
  mkdir -p $SDK/cmdline-tools && cd /tmp
  curl -sSL -o tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
  unzip -q tools.zip -d $SDK/cmdline-tools && mv $SDK/cmdline-tools/cmdline-tools $SDK/cmdline-tools/latest
  yes | $SDK/cmdline-tools/latest/bin/sdkmanager --sdk_root=$SDK --licenses >/dev/null
  $SDK/cmdline-tools/latest/bin/sdkmanager --sdk_root=$SDK "platforms;android-34" "build-tools;34.0.0" >/dev/null
fi
command -v zip >/dev/null || (apt-get update -qq && apt-get install -y -qq zip >/dev/null)
cd /app && rm -rf build && mkdir -p build/classes build/dex out
$BT/aapt2 link -o build/base.apk -I $JAR --manifest AndroidManifest.xml \
  --min-sdk-version 23 --target-sdk-version 34 --version-code 4 --version-name 1.3
javac -source 11 -target 11 -encoding UTF-8 -classpath $JAR -d build/classes $(find src -name '*.java') 2>&1 | grep -v "bootstrap classpath" || true
$BT/d8 --release --min-api 23 --lib $JAR --output build/dex $(find build/classes -name '*.class')
(cd build/dex && zip -q -u ../base.apk classes.dex)
$BT/zipalign -f 4 build/base.apk build/aligned.apk
$BT/apksigner sign --ks /keystore/zaehler-pin.keystore --ks-pass file:/keystore/passwort \
  --out out/zaehler-pin.apk build/aligned.apk
$BT/apksigner verify --print-certs out/zaehler-pin.apk | head -2
ls -l out/zaehler-pin.apk
