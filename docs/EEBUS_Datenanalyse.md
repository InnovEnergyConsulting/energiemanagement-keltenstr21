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
