# Hauptzähler über WattWächter – Konzept zur Einbindung ins EMS

Stand: 21.10.2026 · Phase 4 („Optischer Zähler“) laut Umsetzungskonzept · Status: **in Betrieb (ohne PIN: nur Zählerstände)**

**Umsetzung 21.10.2026:** Gerät eingerichtet (3.1 komplett, WebPassword Benutzer `admin`, Passwort in `secrets/wattwaechter_web_password`), Topic abweichend vom Entwurf `ems/v1/device/wattwaechter/…` (Normalizer ergänzt die Rohdaten-Hülle), Mapping `tasmota_wattwaechter.yaml` (nur Felder mit `aktiv: true`), Leitstand-Reiter „Strom“ + Statuseintrag „Hausanschluss“. **Prüfschritte erledigt:** 5.1 Montage, 5.2 Skript aktiv, **5.3 Display = übertragener Zählerstand (2999 kWh, vom Nutzer bestätigt)**. Offen: 5.4–5.6 nach PIN-Eingabe.

**Gemessen 21.10.2026:** Der MT691 sendet **jede Sekunde** ein SML-Telegramm (08:33:47,24 → 48,24 → …); ohne PIN enthält es nur 1.8.0 und 2.8.0 in ganzen kWh (Einheit Wh, Skalierung 10³) sowie Hersteller (ISK) und Geräte-ID. Der WattWächter gibt alle 10 s (TelePeriod) an das EMS weiter.

**Übergangslösung bis zur PIN – Ø-Leistung aus kWh-Sprüngen** (Leitstand, `views.power_from_steps`): 1 kWh / Zeit zwischen zwei Sprüngen = exakte mittlere Leistung des Abschnitts (Unschärfe ±10 s je Sprung, < 0,5 % bei 1,5 kW). Seit dem letzten Sprung wird eine Obergrenze (1 kWh / vergangene Zeit) angezeigt. Abschnitte mit Datenlücke > 15 min gelten als unsicher (grau). Berechnung nur in der Anzeige – gespeichert werden weiterhin nur echte Zählerstände.
Rohdaten der Auslesung: `inventory/wattwaechter-auslesung-2026-10-21/` (Status 0, SML-Skript; ohne Passwörter)

---

## 1. Befund vom 21.10.2026 (nur lesend ausgelesen)

| Punkt | Befund | Bewertung |
|---|---|---|
| Gerät | WattWächter (optischer Lesekopf mit WLAN), ESP8285, **Tasmota 15.5.0**, Hersteller-Updatequelle smartcircuits.de | passt zum EMS-Plan (Tasmota = Standardprofil) |
| Netz | 192.168.0.162 (fest), WLAN `ZTE_3RM665`, **Signal −81 bis −83 dBm** (RSSI 34–38 %) | **schwach** – Aussetzer wahrscheinlich, siehe 7 |
| Neustarts | BootCount 9, letzter Start 21.10. 07:48 | nach Einrichtung normal, weiter beobachten |
| Zähler-Skript | SML-Skript für **Iskra MT691** hinterlegt (9600 Baud, Messintervall-Ausgabe), **„Script enable“ aus** | richtig so, solange der Kopf nicht am Zähler sitzt |
| Messwerte | keine (Kopf noch nicht montiert, Nutzerangabe) | erwartet |
| MQTT | nicht eingerichtet (kein Broker eingetragen) | wird für die Einbindung gebraucht |
| Zeitzone | fest `+01:00` → Ortszeit derzeit **1 h falsch** (Sommerzeit bis 25.10.) | einstellen auf `Timezone 99` (Regeln Mitteleuropa) |
| Zugang | Weboberfläche und Befehls-API **ohne Passwort** | jeder im WLAN kann umkonfigurieren → Passwort setzen |
| TelePeriod | 300 s | für Leistungsanzeige zu grob → 10 s |

**Was das Skript vorsieht** (OBIS-Kennzahl → Name im Tasmota-JSON):

| OBIS | Tasmota-Name | Bedeutung | Einheit, Nachkommastellen |
|---|---|---|---|
| 1-0:1.8.0 | `E_in` | Bezug gesamt | kWh, 3 |
| 1-0:2.8.0 | `E_out` | Einspeisung gesamt | kWh, 3 |
| 1-0:16.7.0 | `Power` | Wirkleistung gesamt (Vorzeichen: + Bezug / − Einspeisung, **zu prüfen**) | W |
| 1-0:36/56/76.7.0 | `power_L1..L3` | Wirkleistung je Phase | W |
| 1-0:31/51/71.7.0 | `Curr_p1..p3` | Strom je Phase | A, 2 |
| 1-0:32/52/72.7.0 | `Volt_p1..p3` | Spannung je Phase | V, 1 |
| 1-0:81.7.x | `angle_*` | Phasenwinkel U/U und I/U | ° |
| 1-0:14.7.0 | `Freq` | Netzfrequenz | Hz, 1 |

Ob der MT691 alle diese Werte tatsächlich sendet, ist **nicht** aus dem Skript ableitbar (Anforderungen Kap. 4).
Bei modernen Messeinrichtungen wie dem MT691 gilt meist: **ohne PIN** nur Zählerstände (oft ohne Nachkommastellen),
**mit PIN** (vom Netzbetreiber, Eingabe per Taschenlampe am Zähler, Info-Anzeige „InF on“) der erweiterte Datensatz mit
Momentanleistung. → PIN-Brief bereitlegen.

---

## 2. Messgrenze

Der WattWächter sitzt am Zähler der **gesamten Stromversorgung des Hauses** (Netzanschlusspunkt).

- Er misst **Netzbezug und Einspeisung**, nicht direkt den Hausverbrauch. Solange keine PV/Batterie existiert, gilt
  Hausverbrauch = Netzbezug. Mit PV: `Hausverbrauch = PV + Bezug + Batterieentladung − Einspeisung − Batterieladung`.
- **Offen – bitte klären:** Der **eigene WP-Zähler** ist entweder ein eigener Zählpunkt des Netzbetreibers
  (WP-Tarif / § 14a, dann ist die WP **nicht** im Hauptzähler enthalten) **oder** ein Unterzähler hinter dem Hauptzähler
  (dann **ist** sie enthalten und darf nicht addiert werden). Davon hängt jede spätere Haus-/WP-Bilanz ab.
- Shelly-Pro-1PM-Messungen (.197, .148, .124) sind Unterzähler und werden nie zum Hauptzähler addiert.
- Die Zuordnung erhält ein Gültigkeitsdatum (Anforderungen Kap. 7), damit spätere Umbauten (PV) die Historie nicht verfälschen.

---

## 3. Weg der Daten – Varianten

| | **A: MQTT (Empfehlung)** | B: HTTP-Abfrage durch einen Adapter |
|---|---|---|
| Ablauf | Tasmota sendet selbst alle 10 s `SENSOR`-Nachricht an Mosquitto; Normalizer übersetzt | neuer `meter-adapter` fragt alle 10 s `/cm?cmnd=Status 10` ab |
| Neuer Container | keiner (nur Mapping + Normalizer-Parser) | ja |
| Ausfallerkennung | Tasmota-„Letzter Wille“ (LWT Online/Offline) + Datenalter | Zeitüberschreitung im Adapter |
| Last auf dem ESP bei schwachem WLAN | gering (eine Nachricht je Intervall, hält Verbindung) | höher (HTTP-Verbindungsaufbau je Abfrage) |
| Gerätekonfiguration nötig | ja (Broker, Konto, Topic) | nein (nur Skript aktivieren) |
| Passt zum EMS-Grundsatz | ja: „Gen1/Tasmota ohne TLS über 1883, eigenes Konto je Gerät“ (Umsetzungskonzept Kap. 7) | Sonderweg |

**Empfehlung A.** TLS ist auf dem ESP8285 mit Standard-Tasmota nicht verfügbar; die Absicherung erfolgt wie geplant über
eigenes Konto + ACL im LAN.

### 3.1 Einstellungen am WattWächter (erst nach deiner Freigabe, siehe 8)

```
Timezone 99                                   # Sommer-/Winterzeit automatisch (Regeln stehen bereits richtig)
MqttHost 192.168.0.172 ; MqttPort 1883
MqttUser wattwaechter ; MqttPassword <aus secrets/mqtt_wattwaechter>
Topic wattwaechter
FullTopic ems/v1/raw/%topic%/%prefix%/       # -> ems/v1/raw/wattwaechter/tele/SENSOR
TelePeriod 10
WebPassword <neu, in Vaultwarden>             # schützt Weboberfläche + Befehls-API
Script enable (Häkchen)                       # erst wenn der Kopf am Zähler sitzt
```

### 3.2 Broker (Mosquitto)

- Neues Konto `wattwaechter` (Passwort von `scripts/init-secrets.sh` erzeugt).
- ACL: **schreiben** `ems/v1/raw/wattwaechter/tele/#` und `.../stat/#`, **kein Lesen** von `.../cmnd/#`
  → das EMS und Dritte im Broker können dem Gerät keine Befehle schicken (Grundsatz „Lesen vor Schreiben“).

### 3.3 Normalizer und Datenmodell

Neues Mapping `config/mapping/tasmota_wattwaechter.yaml` (versioniert, mit Tests aus echten Telegrammen):

| Tasmota-Feld | `quantity` | `role` / `direction` | Einheit EMS |
|---|---|---|---|
| `E_in` | `energy_import_total` | grid / import | kWh |
| `E_out` | `energy_export_total` | grid / export | kWh |
| `Power` | `active_power` (mit Vorzeichen) | grid / import(+) export(−) | W |
| `power_L1..3` | `active_power_l1..l3` | grid | W |
| `Curr_p1..3` | `current_l1..l3` | grid | A |
| `Volt_p1..3` | `voltage_l1..l3` | grid | V |
| `Freq` | `frequency` | grid | Hz |
| `angle_*` | `phase_angle_*` | Diagnose | ° (nur Rohdaten + Detail, kein Dashboard) |

- Asset `hausanschluss`, Name „Hausanschluss (Hauptzähler)“, `circuit_id: grid_connection`, `measurement_kind: measured`.
- **Zeitstempel:** Tasmota schreibt Ortszeit ohne Zeitzone ins JSON. Gespeichert wird die **Empfangszeit (UTC)**;
  die Gerätezeit dient nur der Plausibilitätsprüfung (Abweichung > 2 min → Qualitätsmeldung).
- **Plausibilität** (Qualitätsmeldung statt Speichern):
  - Zählerstand fällt → Zählerwechsel/Reset, wird gemeldet und getrennt geführt;
  - Sprung > 5 kWh innerhalb eines Intervalls → verworfen;
  - |Leistung| > 45 kW (3 × 63 A × 230 V) → verworfen;
  - fehlende Felder bleiben fehlend, nie 0.
- Verfügbarkeit: `tele/LWT` → `ems/v1/availability/wattwaechter`.
- Stündliche Verdichtung läuft bereits: `*_total` als letzter Wert, Leistung als Mittel – keine Änderung nötig.
- Datenmenge: ~15 Werte alle 10 s ≈ 130 000 Punkte/Tag – für InfluxDB unkritisch.

---

## 4. Leitstand

Vorschlag (bitte entscheiden):

1. **Statuszeile:** neuer Eintrag „Hausanschluss“ mit aktueller Leistung (z. B. „Bezug 1,8 kW“), Datenalter, Störung bei
   Offline oder Werten älter als 2 min.
2. **Neuer Reiter „Strom“** (vor „Steuerung Shelly“):
   - Lage: aktuelle Leistung (Bezug/Einspeisung), heute/Monat/Jahr Bezug in kWh, Phasenlast L1–L3 als Balken
     (Schieflast sichtbar), Spannung/Frequenz nur als Hinweis bei Abweichung (±10 % bzw. ±0,2 Hz);
   - Verlauf mit denselben 8 Zeitspannen wie die anderen Reiter;
   - Tages-/Monatswerte aus Zählerdifferenzen (nicht aus aufsummierter Leistung).
3. Später (Phase 6/7): Gegenüberstellung Hauptzähler ↔ WP-Zähler ↔ Shelly-Unterzähler, erst wenn die Messgrenzen festgelegt sind.

---

## 5. Inbetriebnahme – Prüfschritte (wenn der Kopf montiert ist)

1. Lesekopf mittig auf die optische Schnittstelle (Magnet), Kabel zugentlastet.
2. „Script enable“ setzen → Tasmota-Hauptseite zeigt Werte? Wenn nicht: Baudrate/Telegramm prüfen (`sensor53 d1` Rohdump).
3. **Zählerstand im Display = `E_in`** (Einheit, Nachkommastellen).
4. PIN eingeben (falls vorhanden) → erscheinen `Power`, Phasenwerte, Nachkommastellen?
5. **Vorzeichen prüfen:** großen Verbraucher (Wasserkocher ~2 kW) einschalten → `Power` steigt um ~2000 W positiv.
6. Welche der 15 Skriptwerte kommen wirklich? Nicht gelieferte Zeilen aus dem Skript entfernen (Anforderungen Kap. 4).
7. 24 h Telegramme als Test-Fixture ins Repo (`ems-core/tests/fixtures/tasmota/`), dann Mapping + Tests, dann MQTT einschalten.
8. Betriebstest: Kopf abziehen → Leitstand zeigt „Störung/Datenalter“ statt 0 W; WLAN-Ausfall → Lücke statt Linie.

---

## 6. Aufwand

| Schritt | Aufwand |
|---|---|
| Broker-Konto + ACL, Gerätekonfiguration | 0,5 h |
| Mapping + Normalizer-Parser + Tests mit echten Telegrammen | 2–3 h |
| Leitstand: Statuszeile + Reiter „Strom“ | 3–4 h |
| Betriebstests (Ausfall, Vorzeichen, Display-Vergleich) | 1 h |
| **Summe** | **ca. 1 Arbeitstag**, davon alles außer Schritt 5.3–5.7 schon vor Montage vorbereitbar |

---

## 7. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Schwaches WLAN am Zählerschrank (−81 dBm) | WLAN-Repeater/Access-Point näher am Zählerschrank; bis dahin Datenalter-Überwachung, Lücken sichtbar; Energie über Zählerdifferenzen ist lückenrobust |
| Ohne PIN keine Leistung | PIN beim Netzbetreiber anfordern (kostenlos); bis dahin nur Zählerstände |
| Gerät ohne Passwort | WebPassword setzen; ACL verhindert Befehle über MQTT |
| Hersteller-OTA (smartcircuits.de) könnte Skript/Einstellungen ändern | Updates nur bewusst; Konfiguration (Skript + Befehle oben) liegt im Repo zur Wiederherstellung |
| Falsches Vorzeichen bei späterer PV | Prüfschritt 5.5, Mapping-Version erhöhen, Gültigkeitsdatum |

---

## 8. Entscheidungen / Informationen von dir

1. **Freigabe** für die Gerätekonfiguration aus 3.1 (Zeitzone, MQTT, TelePeriod, Passwort) – kann sofort erfolgen,
   „Script enable“ erst nach Montage.
2. **PIN** für den Zähler vorhanden? Sonst beim Netzbetreiber anfordern.
3. **WP-Zähler:** eigener Zählpunkt oder Unterzähler hinter dem Hauptzähler?
4. **Leitstand:** neuer Reiter „Strom“ wie in 4 vorgeschlagen?
5. **WLAN am Zählerschrank** verbessern (Repeater) – ja/nein?
