# Vaillant-Komponenten Keltenstr. 21 – technische Daten und Bedeutung für das EMS

Stand: 20.10.2026 · Wärmepumpe wird diese Woche installiert; Modell der Außeneinheit folgt.
Bereits bekannt: **eigener Stromzähler für die Wärmepumpe**.

---

## 1. Internetmodul sensoNET VR 921

| Merkmal | Wert | Quelle |
|---|---|---|
| Artikelnummer | 0020260962 | Vaillant Belgien, Datenblatt 09/2022 |
| Stromversorgung | Steckernetzteil 110 … 240 V~, 50/60 Hz, 0,5 A; Ausgang max. 2,1 A DC | Installationsanleitung, Technische Daten |
| Schutzart (Netzteil) | IP 20 | Installationsanleitung |
| Internetverbindung | **WLAN** (Kanäle 1–13, WPA2-PSK, IP per DHCP) **oder LAN** | Installationsanleitung / Händler |
| Funk zu Funkreglern (z. B. sensoCOMFORT 720f) | 869,4 – 869,65 MHz, max. 17,5 dBm | Installationsanleitung |
| Verbindung zur Anlage | eBUS (Plug & Play mit aktuellen Vaillant-Geräten) | Händlerbeschreibung |
| Benötigter Regler | sensoCOMFORT oder sensoHOME | Händlerbeschreibung |
| Dienste | App **myVAILLANT** (Cloud), Fernwartung **myVAILLANT Pro** für den Installateur | Händler |
| **EEBUS** | in der Anleitung erwähnt („EEBus und Systeminformation“); Energiemanagement-Anbieter (gridX) nennen VR 920/921/940f als EEBUS-Gateway, **mit LAN-Anschluss und aktueller Firmware** | Anleitung S. 22, gridX-Support |

## 2. Systemregler sensoCOMFORT VRC 720/3 (kabelgebunden, eBUS)

| Merkmal | Wert |
|---|---|
| Bemessungsspannung | 9 … 24 V (über eBUS) |
| Bemessungsstrom | < 50 mA |
| Querschnitt Anschlussleitungen | 0,75 … 1,5 mm² |
| Schutzart / Schutzklasse | IP 20 / III |
| Umgebungstemperatur | 0 … 60 °C, Raumluftfeuchte 35 … 95 % |
| Abmessungen (H × B × T) | 109 × 175 × 26 mm |
| eBUS-Leitung | 2-adrig, **nicht** geschirmt/verdrillt (z. B. NYM, H05VV), gesamt max. 125 m (≥ 0,75 mm² bis 50 m, 1,5 mm² darüber), ≥ 120 mm Abstand zu Netzleitungen |
| Funktionsumfang | witterungsgeführt, bis zu 9 Heizkreise, Kaskaden bis 7 Wärmeerzeuger, Hybrid, Solar, PV, Lüftung; Erweiterung über VR 70 / VR 71, VR 32 als Buskoppler; eingebauter Feuchtesensor |

Quelle: Betriebs- und Installationsanleitung VRC 720/3, Dok.-Nr. 0020334315_05 (Kap. 3.2, 7.9 und Menüs).

### Für das EMS wichtige Funktionen im Regler

| Funktion | Was sie bedeutet |
|---|---|
| **Status Ext. Energiemanager** | Ein externer Energiemanager kann die Regelung übernehmen; der Regler zeigt dann nur noch reduzierte Funktionen. „Externen Energiemanager beenden“ gibt die Regelung zurück. → Vaillant sieht die Steuerung durch ein EMS ausdrücklich vor (vermutlich über EEBUS am VR 921). |
| **Ext. Leistungsreduzierung** | Anzeige, ob ein Signal zur Leistungsreduzierung des Netzbetreibers aktiv ist (Stichwort § 14a EnWG, steuerbare Verbraucher). |
| **EVU-Kontakt** (an der Wärmepumpe) | Sperrsignal von Netzbetreiber oder externem Regler. Einstellbar, was gesperrt wird (WP / Zusatzheizung / beides bzw. Heizen / Kühlen). Logik: Netzbetreiber-Modus *geschlossen = gesperrt*; Modus externer Regler *geschlossen = freigegeben*. Frostschutz hat immer Vorrang. |
| Anschlüsse am WP-Regelungsmodul | **PV** (Schnittstelle zum PV-Wechselrichter), **SG** (Schnittstelle zum Netzbetreiber), Multifunktionseingang/-ausgang (ME/MA) |
| Energiestatistik | Umweltertrag, Solarertrag, Stromverbrauch je Heizen/Warmwasser/Kühlen (Monat/Jahr/Gesamt). **Laut Vaillant nur Schätzwerte, Abweichungen „können erheblich sein“, nicht für Abrechnung geeignet.** → Echte Werte liefert der eigene WP-Zähler. |

---

## 3. Wege, die Anlage ins EMS einzubinden

| Weg | lokal? | lesen | steuern | Aufwand / Risiko |
|---|---|---|---|---|
| **A – Eigener WP-Stromzähler** (optische Schnittstelle) | ja | exakter Stromverbrauch der WP | – | gering; Lesekopf wie beim Hauszähler (Phase 4) |
| **A2 – Wärmemengenzähler** | ja | gemessene Wärmemenge, Leistung, Vor-/Rücklauf, Volumenstrom → zusammen mit A die **Arbeitszahl** | – | Schnittstelle je nach Modell (M-Bus, Impuls, optisch); Details folgen |
| **B – eBUS mitlesen** (eBUS-Adapter + Open-Source-Software *ebusd*) | ja, ohne Cloud | sehr viele Werte (Temperaturen, Modulation, Betriebszustände, teils Energiezähler) | technisch möglich, **nicht empfohlen** (greift in den Herstellerbus ein) | Adapter ca. 50–100 €; Werte sind modell-/firmwareabhängig und müssen je Anlage zugeordnet werden |
| **C – EEBUS über VR 921** | ja (verschlüsselt, Kopplung per Gerätekennung „SKI“) | eingeschränkt (Leistungsdaten je nach Firmware) | **offizieller Weg** für Energiemanager (Leistungsbegrenzung, PV-Überschuss) | hoch: Open-Source-Bausteine existieren (z. B. *eebus-go*, genutzt von evcc), Funktionsumfang mit dieser Anlage erst zu prüfen; VR 921 per **LAN** anschließen |
| **D – myVAILLANT-Cloud** (inoffizielle API, z. B. *mypyllant*) | nein | viele Werte | eingeschränkt | widerspricht dem Grundsatz „lokal“ aus den Anforderungen; Abfrage-Kontingente und Ausfälle berichtet → höchstens Übergangslösung |
| **E – Schaltkontakte** (EVU/SG-Ready über Relais, z. B. Shelly) | ja | Zustand des Kontakts | grob (sperren/freigeben, PV-Anhebung) | gering, robust, herstellerseitig vorgesehen |

**Empfehlung (vorläufig, bis das WP-Modell bekannt ist):** A und A2 fest einplanen (WP-Zähler zugesagt, Wärmemengenzähler vorgesehen).
Für Betriebsdaten B (nur lesend). Steuerung zuerst über E (Kontakte), C als spätere Ausbaustufe.

---

## 4. Konkrete Fragen an den Installateur (diese Woche)

*Wärmemengenzähler: **vorgesehen** (Anforderungen Kap. 8, bestätigt 20.10.2026). Details (Modell, Einbauort, Schnittstelle) kommen zusammen mit den WP-Daten. Damit lassen sich Heizleistung und Arbeitszahl messen statt nur aus Vaillant-Schätzwerten ableiten.*

1. **Eigener WP-Zähler:** Welches Modell, hat er eine **optische Schnittstelle** (D0/SML), ist die **PIN** für erweiterte Daten verfügbar?
2. **VR 921:** Bitte per **LAN-Kabel** statt WLAN anschließen (stabiler; für EEBUS empfohlen).
3. **eBUS:** Sind die eBUS-Klemmen für einen zusätzlichen Lese-Adapter zugänglich? Wo verläuft die eBUS-Leitung?
4. **EVU-Kontakt / SG:** Ist der EVU-Kontakt beschaltet (Netzbetreiber nach § 14a)? Sind die **SG-/PV-Eingänge** am WP-Regelungsmodul frei, und darf dort ein potenzialfreier Kontakt (Relais) angeschlossen werden?
5. **Modelle:** Außeneinheit (aroTHERM …), Hydraulik-/Regelungsmodul (VWZ …), Firmwarestand von VRC 720/3 und VR 921.

---

## Quellen

- Vaillant sensoCOMFORT VRC 720/3, Betriebs- und Installationsanleitung 0020334315_05:
  https://www.heizungsdiscount24.de/pdf/Vaillant-Regelung-sensoCOMFORT-VRC-720-3-Betriebs-und-Installationsanleitung.pdf
- Vaillant sensoNET VR 921, Installationsanleitung (ManualsLib): https://www.manualslib.de/manual/934970/Vaillant-Sensonet-Vr-921.html
- VR 921 Datenblatt Vaillant Belgien: https://vaillant.be/telechargements/documents/fiche-technique/vai-206-ft-vr921-00-2532720.pdf
- VR 921 Händlerbeschreibung: https://ie.gemashop.de/products/internetmodul-sensonet-vr-921
- gridX-Support, Vaillant-Gateways für EEBUS: https://support.gridx.de/hc/de/article_attachments/29167879649298
- Loxforum, VR 921 und EEBUS: https://www.loxforum.com/forum/german/software-konfiguration-programm-und-visualisierung/392313-vaillant-vr921-→-loxone-über-eebus
- mypyllant (myVAILLANT-Cloud): https://github.com/ardyp87/mypyllant-component/blob/main/README.md
- Lokale ebusd-Einbindung: https://community.home-assistant.io/t/vaillant-ebus-local-ebusd-integration-no-cloud-no-mqtt/1018340
