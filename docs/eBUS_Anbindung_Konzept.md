# Lokale eBUS-Anbindung der Vaillant-Anlage – Konzept

Stand: 22.10.2026 · Status: **Konzept, noch nicht umgesetzt** · Bezug: Phase 9 Umsetzungskonzept („Vaillant: EEBUS / eBUS / Kontakte“)

## 1. Ziel

Die Werte, die die myVAILLANT-App zeigt – **elektrischer Verbrauch der Wärmepumpe getrennt nach Heizung und Warmwasser**,
dazu Wärmeertrag, Arbeitszahlen und Betriebsdaten – **lokal und ohne Cloud** ins EMS holen.

## 2. Warum eBUS

| Befund (20.–22.10.2026) | Folge |
|---|---|
| EEBUS (VR 940) liefert nur Momentanwerte; Energie (`acEnergyConsumed`) ist angekündigt, kommt aber nie (rund 26.700 Telegramme geprüft) | Zähler nicht über EEBUS erreichbar |
| VR 940 hat im LAN nur Port 12480 (EEBUS) und mDNS offen | keine lokale Schnittstelle für die App-Werte |
| App-Werte kommen aus der Vaillant-Cloud (Nutzer-Test: WLAN aus, mobile Daten → Werte weiter sichtbar) | Cloud-Abhängigkeit, die das EMS vermeiden soll |
| Die Werte entstehen in Wärmepumpe und Regler und laufen über den **eBUS** (Zweidraht-Anlagenbus) zum VR 940 | **am eBUS mithören = dieselben Werte, lokal** |

## 3. Was über eBUS erwartbar ist

Quelle: ebusd-Konfiguration für Vaillant, Regler (`15.720`) und Wärmepumpenmodul (`08.hmu`) –
[ebus.github.io](https://ebus.github.io/en/vaillant/08.hmu.csv). **Welche Werte die konkrete Anlage
(aroTHERM plus VWL 75/8.1 A, sensoCOMFORT VRC 720/3) tatsächlich beantwortet, zeigt erst der Scan bei der Inbetriebnahme.**

| Bereich | ebusd-Name (Beispiele) | Bedeutung | Nutzen im EMS |
|---|---|---|---|
| Regler | `PrEnergySumHc`, `PrEnergySumHwc`, `PrEnergySum` | elektrische Energie Heizung / Warmwasser / gesamt (Jahr) | **genau die App-Werte**, ersetzt die EEBUS-Schätzung |
| Regler | `PrEnergySumHcThisMonth` / `…LastMonth`, `PrEnergySumHwc…` | dasselbe je Monat | Abgleich, Monatsauswertung |
| WP-Modul | `ConsumptionTotal`, `ConsumptionThisYear1…12` | Stromverbrauch gesamt / je Monat | Plausibilisierung |
| WP-Modul | `YieldHc`, `YieldHwc`, `YieldTotal`, `YieldThisYear1…12` | **Wärmeertrag** (von der WP berechnet) | Wärmemenge, Arbeitszahl |
| WP-Modul | `CopHc`, `CopHwc`, `CopHcMonth`, `CopHwcMonth` | Arbeitszahl Heizung / Warmwasser | Effizienzbewertung |
| WP-Modul | `CurrentConsumedPower`, `CurrentYieldPower` | aktuelle Leistungsaufnahme / Wärmeleistung | Live-Anzeige |
| WP-Modul | `FlowTemp`, `TargetFlowTemp`, `BuildingCircuitFlow`, `FlowPressure` | Vorlauf Ist/Soll, Volumenstrom, Anlagendruck | Hydraulik, Störungserkennung |
| WP-Modul | `CompressorHours`, `CompressorStarts`, `CurrentCompressorUtil`, `State` | Laufzeit, Starts (Takten), Auslastung, Zustand | Taktüberwachung, Wartung |

Hinweis: Die Energie- und Wärmewerte rechnet die Wärmepumpe **selbst** – sie sind nicht geeicht. Maßgeblich für Kosten
bleibt ein geeichter Zähler (Kap. 6).

## 4. Hardware

**Empfehlung: eBUS Adapter Shield C6** (Nachfolger des v5; Entwickler von ebusd, Vertrieb über Elecrow).

| Eigenschaft | Wert |
|---|---|
| Anbindung | WLAN (ESP32-C6) oder USB; LAN nur mit Zusatzmodul USR-ES1 |
| Stromversorgung | 5 V über USB-C (< 120 mA), eigenes Netzteil |
| eBUS | **galvanisch getrennt**, entnimmt dem Bus **keinen Strom** (Klasse 0), Anschluss verpolungssicher (2-polig) |
| Software | ebusd „enhanced protocol“ |
| Preis | ca. 26,50 USD zzgl. Versand, Gehäuse und Netzteil optional (Stand Recherche: zeitweise nicht lieferbar) |

Quellen: [Adapter C6](https://elecrow.com/ebus-adapter-shield-c6.html), [Adapter v5](https://www.elecrow.com/ebus-adapter-shield-v5.html),
Dokumentation adapter.ebusd.eu.

**Anschluss:** zweiadrig parallel an die eBUS-Klemmen – sinnvoll dort, wo auch der VR 940 angeschlossen ist (Inneneinheit
bzw. Regler-Anschluss). **Durch den Installateur oder mit seiner Freigabe** (Gewährleistung). Der Adapter hört nur mit;
er wird so konfiguriert, dass er nicht sendet (Kap. 5).

**WLAN oder LAN:** Am Zählerschrank war das WLAN anfangs schwach (−81 dBm). Am Aufstellort der Inneneinheit vorher das
Signal prüfen; bei schwachem WLAN das LAN-Modul verwenden.

## 5. Software und Sicherheit

```
eBUS ──► Adapter (WLAN/LAN) ──► ebusd (Container, nur lesen) ──MQTT──► ems/v1/device/ebusd/… ──► Normalizer ──► InfluxDB
```

- **ebusd** als Container `ems-ebusd` (offizielles Image `john30/ebusd`, Version beim Aufbau festpinnen), verbindet sich
  über das Netz mit dem Adapter.
- **Nur lesen, doppelt abgesichert:**
  1. ebusd mit `--readonly` (schreibt nie auf den Bus);
  2. eigenes MQTT-Konto `ebusd` mit Schreibrecht nur auf `ems/v1/device/ebusd/#` und **ohne Leserecht** – ebusd kann
     damit keine `/set`-Befehle empfangen (ebusd würde Schreibbefehle sonst über MQTT annehmen).
- **Abfrage:** Energiezähler und Arbeitszahlen alle 15 min, Leistung/Temperaturen/Zustand jede Minute (ebusd-Abfrageliste).
  Die Buslast ist gering; Regler und Wärmepumpe senden ohnehin zyklisch.
- **Normalizer:** neues Mapping `ebusd_vaillant.yaml` (versioniert, mit Tests aus echten Telegrammen), Zählerstände als
  `*_total` (Rücksprung-/Sprungprüfung wie beim Hauptzähler), Arbeitszahlen und Zustände als `state`.
- **Grundsatz „Lesen vor Schreiben“** bleibt gewahrt: Steuern über eBUS ist ausdrücklich nicht Teil dieses Konzepts.

## 6. Zusammenspiel mit Kosten und Leitstand

| Quelle für WP-Strom | Genauigkeit | Rolle |
|---|---|---|
| eigener WP-Zähler (geeicht, kommt) | geeicht | **maßgeblich für Kosten** |
| eBUS-Zähler Heizung/Warmwasser | geräteintern | **Aufteilung** Heizung/Warmwasser, Gegenprobe |
| EEBUS-Leistung (heute) | Schätzung (±3 % zur App) | Rückfallebene |

Kostenrechner: Herkunft `geraet` (eBUS) ersetzt `geschaetzt` (EEBUS) ab dem Tag der Inbetriebnahme; ab dem WP-Zähler
wird nach dessen Werten abgerechnet und mit eBUS nur noch aufgeteilt.

Leitstand „Regelung WP“: Verbrauch Heizung/Warmwasser aus eBUS, **Wärmeertrag und Arbeitszahl** (Heizen/Warmwasser,
Monat/Jahr), Vorlauf Ist/Soll, Volumenstrom, Verdichterstarts je Tag (Takten sichtbar).

**Anlagenschaltbild (Idee 24.10.2026):** Mit eBUS lohnt sich im Reiter „Regelung WP“ ein vereinfachtes Hydraulikschema
(Stil wie der übrige Leitstand: grau im Normalbetrieb, Farbe nur bei Abweichung), auf dem die Messstellen live stehen:

| Anlagenteil | Werte im Schema (Quelle) |
|---|---|
| Außeneinheit aroTHERM plus | Verdichter an/aus, Auslastung, Starts heute, Leistung elektrisch/thermisch, Außentemperatur (eBUS/EEBUS) |
| Hydraulik Inneneinheit | Vorlauf Ist/Soll, Rücklauf, Volumenstrom, Anlagendruck, Umschaltung Heizen/Warmwasser (eBUS) |
| Warmwasserspeicher | Ist/Soll, Ladung aktiv (EEBUS/eBUS) |
| Heizkreis → FB-Verteiler | Vorlauf, Rücklauf je Kreis, Ventilöffnung je Raum (Controme, vorhanden) |
| Hausanschluss / WP-Zähler | Netzbezug, WP-Verbrauch (WattWächter, künftiger WP-Zähler) |

Fließrichtung und Pumpe nur hervorgehoben, wenn tatsächlich Volumenstrom gemessen wird; Klick auf eine Messstelle öffnet
deren Verlauf. Welche Werte die Anlage wirklich liefert (z. B. Stellung des Umschaltventils), klärt erst der Scan (Kap. 7,
Schritt 3) – das Schema wird danach entworfen, nicht vorher.

## 7. Inbetriebnahme

1. Adapter beschaffen (C6, Netzteil, ggf. Gehäuse/LAN-Modul); WLAN am Aufstellort prüfen.
2. Adapter einrichten (WLAN, feste IP in der FRITZ!Box), Installateur schließt an den eBUS an.
3. Container `ems-ebusd` mit `--readonly`, Scan der Busteilnehmer → tatsächliche Geräte/Werte dokumentieren.
4. Werte mit Regler-Menü „Information“ bzw. App vergleichen (Heizung/Warmwasser kWh).
5. Mapping + Tests, Normalizer, Leitstand, Kostenrechner umstellen.
6. Betriebstest: Adapter stromlos → Leitstand zeigt Störung, EMS rechnet mit EEBUS-Schätzung weiter.

**Aufwand:** ca. 1–1,5 Arbeitstage nach Anschluss (Software), Hardware ca. 30–50 € inkl. Netzteil.

## 8. Alternativen

| Weg | Bewertung |
|---|---|
| **Vaillant Modbus-Gateway** (über Fachhandel, mit Registerdokumentation) | offiziell dokumentiert, aber teurer und Einbau durch Fachbetrieb |
| Vaillant-Cloud über inoffizielle Schnittstelle | keine Hardware, aber Internet-/Vaillant-Abhängigkeit, kann jederzeit brechen |
| nur WP-Zähler | geeicht, aber ohne Aufteilung Heizung/Warmwasser und ohne Wärmeertrag/Arbeitszahl |

## 9. Entscheidungen von dir

1. **Adapter beschaffen** (C6 empfohlen) – ja/nein?
2. **Anschluss durch den Installateur** – fragst du ihn (Ort: eBUS-Klemmen an Inneneinheit/VR 940)?
3. **WLAN oder LAN** am Aufstellort?
