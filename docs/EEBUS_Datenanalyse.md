# EEBUS – Datenanalyse Vaillant VR 940 (Stufe B)

Konzept: `EEBUS_Adapter_Konzept.md` · Rohdaten: `data/eebus/spine-trace-<Tag>.jsonl`, `data/eebus/discovery-*.json`

---

## 1. Erste Bestandsaufnahme – 20.10.2026, ca. 19:30 (WP frisch installiert)

Kopplung in der myVAILLANT-App bestätigt um 19:20. Auf **44 Leseanfragen** (17 Abos) kamen **44 Antworten, 0 Ablehnungen**.

### Angebotene Anwendungsfälle (Discovery)

| Akteur (Entität) | Anwendungsfall | Art |
|---|---|---|
| ControllableSystem (1 Wärmepumpe) | limitationOfPowerConsumption (LPC, § 14a) | Steuern |
| MonitoredUnit (1) | monitoringOfPowerConsumption (MPC) | Lesen |
| DHWCircuit (4 Warmwasser) | monitoringOfDhwTemperature, monitoringOfDhwSystemFunction | Lesen |
| DHWCircuit (4) | configurationOfDhwTemperature, configurationOfDhwSystemFunction | Steuern |
| HVACRoom (5.1.1) | monitoringOfRoomTemperature, monitoringOfRoomHeatingSystemFunction | Lesen |
| HVACRoom (5.1.1) | configurationOfRoomHeatingTemperature, configurationOfRoomHeatingSystemFunction | Steuern |
| Heizkreis/Zone/Raum (5, 5.1, 5.1.1) | visualizationOfHeatingAreaName (RC1) | Lesen |
| OutdoorTemperatureSensor (6) | monitoringOfOutdoorTemperature | Lesen |
| MonitoringAppliance (2, CEM-Seite des VR 940) | monitoringOfGridConnectionPoint | VR 940 *möchte* Netzanschlussdaten empfangen |
| VisualizationAppliance (3) | visualizationOfAggregatedBatteryData / …PhotovoltaicData | VR 940 *möchte* Batterie-/PV-Daten empfangen |

**Nicht** angeboten: OHPCF (Kompressor-Flexibilität / PV-Überschuss). Für eine spätere PV-Optimierung bleiben
damit LPC, die Konfiguration der Warmwasser-/Raumsollwerte und die SG-Ready-Kontakte.

### Aktuelle Werte

| Entität | Größe | Wert | Status |
|---|---|---|---|
| Wärmepumpe | Wirkleistung gesamt (`acPowerTotal`) | **12 W** (Bereitschaft) | gemessen, normal |
| Wärmepumpe | Leistung je Phase, Ströme, Spannungen, Frequenz, **Energie bezogen/erzeugt** | – | beschrieben, aber **ohne Wert** |
| Wärmepumpe | Leistungsbegrenzung LPC | 0 W, **nicht aktiv**, änderbar; Failsafe 1 000 000 W für 2 h | – |
| Warmwasser | Ist-Temperatur | **55 °C** (zuvor 56 °C) | gemessen, normal |
| Warmwasser | Solltemperatur | 55 °C | – |
| Warmwasser | Betriebsart | **auto** (auto/on/off möglich); Schnellladung (Overrun) inaktiv | – |
| Raum (eine Zone) | Raumtemperatur | **20 °C** | gemessen, normal |
| Raum | Solltemperatur | 21 °C | – |
| Raum | Heizbetrieb | **off** (auto/on/off möglich) | – |
| Außenfühler | Außentemperatur | 0 °C | **valueState = error** |

### Auffälligkeiten für den Installateur

1. **Außentemperaturfühler meldet „error“.** Der VRC 720/3 regelt witterungsgeführt; ohne gültige Außentemperatur
   arbeitet die Heizkurve nicht wie vorgesehen. → Fühler angeschlossen? Richtig zugeordnet?
2. **Raum-Heizbetrieb steht auf „off“.** Bei frisch installierter WP evtl. gewollt (Inbetriebnahme) – prüfen.
3. Die WP meldet **keine Energiezähler** (kWh) über EEBUS, nur die Momentanleistung → für Energie bleibt der
   **eigene WP-Stromzähler** maßgeblich. Ob Energie-/Phasenwerte im Betrieb nachgeliefert werden, zeigt die Aufzeichnung.

---

## 2. Offene Analysefragen (Stufe B, 1–2 Wochen Laufzeit)

- Aktualisierungsrate der Werte (Notify-Takt) im Heiz- und Warmwasserbetrieb.
- Liefert die WP im Betrieb Werte für Energie, Phasenleistungen, Ströme, Spannungen?
- Plausibilität `acPowerTotal` gegen den eigenen WP-Zähler (Phase 4).
- Bedeutung der Sollwerte 0 und 2 (ohne Wert, Typ noch nicht ausgewertet).
- Verhalten nach Firmware-Updates (Discovery-Vergleich).
- Danach: Mapping `eebus_*.yaml`, Normalizer-Parser, Tests mit Fixtures aus dieser Aufzeichnung, Grafana „Wärmepumpe“ (Stufe C).

---

## 3. Laufende Auswertung

Täglich 06:30 erzeugt `scripts/eebus-analyse.py` einen Bericht unter `data/eebus/analyse-<Datum>.md`
(Zeitraum: letzte 7 Tage). Erster Lauf 20.10.2026: Werte kommen bislang überwiegend als Antwort auf die
5-Minuten-Lesungen; Warmwasser zusätzlich per Benachrichtigung bei Änderung. 15 Messgrößen der WP
(Phasen, Ströme, Spannungen, Energie, Frequenz) sind beschrieben, aber ohne Wert – Prüfung im Heizbetrieb.

**Klärung durch den Nutzer (20.10.2026):** Außenfühler noch nicht angeschlossen; Heizkreis wird am
21.10.2026 in Betrieb genommen; Energie-Messung der WP (eigener Zähler) wird geklärt. IP des VR 940 fest.

---

## 4. Zweite Bestandsaufnahme – 22.10.2026 (Anlage in Betrieb, Auswertung 21.10. 16:00 – 22.10. 07:40)

Strukturänderungen: 21.10. 05:08 Entitäten neu adressiert (WP 1→3, neu 3.1 Verdichter mit OHPCF);
22.10. 05:48 zweiter Heizkreis „Zone 2“ (Entitäten 7 / 7.1 / 7.1.1) hinzugekommen; Entität 6 heißt jetzt `TemperatureSensor`.

Gerät (Entität 3, deviceClassificationManufacturerData): **aroTHERM plus VWL 75/8.1 A 230V**, Seriennummer 8000033711.

### Mit Wert geliefert

| Entität | Größe | Werte | Bereich | Im EMS |
|---|---|---|---|---|
| 3 Wärmepumpe | acPowerTotal (W) | 1088 | 23 – 2127 W | ja (`vaillant_wp.active_power`) |
| 3.1 Verdichter | acPowerTotal (W), Grenzen 0–3390 W, Schritt 100 W | 1100 | 17 – 2122 W | ja (`vaillant_wp_verdichter.active_power`) |
| 3 | Leistungsbegrenzung § 14a: inaktiv, 0 W, änderbar | 190 | – | ja (Zustand) |
| 3 | Failsafe 1 000 000 W für PT2H | 190 | – | ja (Zustand) |
| 3 | Betriebszustand `normalOperation`, Herzschlag alle ~60 s | 1125 | – | Zustand (nicht im Leitstand) |
| 4 Warmwasser | dhwTemperature (°C) | 484 | 39 – 56 °C | ja |
| 4 | Soll (Setpoint 1) | 190 | 55 °C | ja |
| 4 | Betriebsart auto, Schnellladung inaktiv | 190 | – | ja |
| 5.1 | Zonenname „Zone 1“ | – | – | nein |
| 5.1.1 Zone 1 | roomAirTemperature (°C) | 198 | 21 – 21,5 °C | ja |
| 5.1.1 | Soll (Setpoint 1) | 192 | 22 – 24 °C | ja |
| 5.1.1 | Heizbetrieb **on** (Betriebsart 1) | 190 | – | ja |
| 6 Außenfühler | outsideAirTemperature (°C) | 334 | 8,5 – 12 °C | ja (Fühler funktioniert jetzt) |
| 7.1 | Zonenname „Zone 2“ | – | – | nein |
| 7.1.1 Zone 2 | roomAirTemperature: **0 °C, valueState error** | 23 | – | **nein (nicht im Mapping)** |
| 7.1.1 | Soll (Setpoint 1) | 23 | 21 °C | **nein** |
| 7.1.1 | Heizbetrieb on | 23 | – | **nein** |

### Beschrieben, aber ohne Wert

- WP (3) und Verdichter (3.1): **acEnergyConsumed / acEnergyProduced (Wh)**, Ströme L1–L3, Leistung je Phase, Spannungen (je Phase zwei Messungen – vermutlich Leiter-N und Leiter-Leiter), Frequenz.
  → **Die Energiewerte der App (z. B. 14,3 kWh, Heizung/Warmwasser getrennt) kommen nicht über EEBUS**, sondern über die Vaillant-Cloud.
- Setpoints 0 und 2 (Warmwasser, beide Zonen): nur Platzhalter ohne Typ/Wert; Setpoint 2 gehört zur Betriebsart „off“ (Relation).
- Entität 1 (CEM-Seite des VR 940): Kennwert `contractualConsumptionNominalMax` (W) ohne Wert.
- 3.1 `smartEnergyManagementPsData` (OHPCF-Leistungsprofil): wird vom Adapter bewusst nicht gelesen (Steuer-Anwendungsfall).

---

## 5. WP-Energie aus der EEBUS-Leistung (umgesetzt 22.10.2026)

Der VR 940 liefert über EEBUS **keine Energiezähler** – auch nicht nach Aktivieren des „EEBUS-Energiemanagers“ in
myVAILLANT (22.10., geprüft inkl. Neuverbindung). Die App-Werte kommen über eBUS → Vaillant-Cloud.

**Berechnung im Leitstand** (`ems-core/ems/leitstand/views.py`: `wp_energy`, `dhw_periods`):
- Energie = Trapez-Integral von `acPowerTotal` (Entität 3); Lücken > 15 min werden nicht überbrückt (Abdeckung in % angezeigt).
- Warmwasserladung = Anstieg der Warmwassertemperatur ≥ 2 K. Die **Grenzen kommen aus der Leistung**: Beginn = Sprung auf
  ≥ 50 % der Ladeleistung vor dem Anstieg, Ende = Abfall darunter. Grund: Der Fühler reagiert 5–15 min verzögert
  (21.10.: Leistung springt 11:28, Temperatur steigt ab 11:38).
- Abgeschlossene Tage werden einmal berechnet und im Speicher gehalten.

**Abgleich mit myVAILLANT** (Ansicht „Jahr“; die App enthält den laufenden Tag nicht):

| bis 21.10.2026 24:00 | App | Berechnung | Abweichung |
|---|---|---|---|
| Heizung | 6,2 kWh | 6,09 kWh | −2 % |
| Warmwasser | 8,2 kWh | 8,68 kWh | +6 % |
| Summe | 14,3 kWh | 14,77 kWh | +3 % |

Eine erste Fassung (Ladung nur aus dem Temperaturanstieg) ergab 6,27 / 8,49 kWh; sie rechnete aber flache Phasen
vor einer Ladung mit, was der Unit-Test aufdeckte. Maßgeblich wird später der eigene WP-Zähler.
