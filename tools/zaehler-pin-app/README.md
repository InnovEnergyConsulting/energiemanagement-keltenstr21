# Zähler-PIN – Android-App

Blinkt mit der Handy-Taschenlampe die PIN in den optischen Sensor des Hauptzählers (Iskra MT691) und schaltet
Menüpunkte (`InF on`, `Pin OFF`) schrittweise. Zeiten einstellbar (Vorgaben nach Herstellerbeschreibung:
kurz < 4 s, lang > 5 s, nächste PIN-Stelle nach 3 s Dunkelheit).

- Bauen: `./build-docker.sh` (Docker, eclipse-temurin:17-jdk, Android-SDK im Cache `~/.cache/android-sdk`)
- Ergebnis: `out/zaehler-pin.apk`, Download: https://192.168.0.172:8444/download/zaehler-pin.apk (data/download/)
- Signierschlüssel: `secrets/zaehler-pin-app/` (für Updates derselbe Schlüssel nötig)
- Keine Berechtigungen nötig (setTorchMode ab Android 6), Android 6 oder neuer.
