# EEBUS-Adapter – Konzept

Stand: 20.10.2026 · Bezug: Anforderungen Kap. 8 („EEBUS bevorzugter lokaler Weg“), Umsetzungskonzept Phase 9,
`Vaillant_Komponenten.md`

---

## 1. Ausgangslage (live geprüft am 20.10.2026)

Im Heimnetz meldet sich per mDNS (`_ship._tcp`) bereits ein EEBUS-Gerät:

| Merkmal | Wert |
|---|---|
| Gerät | **Vaillant VR 940** (Gateway „myVAILLANT connect“), Marke Vaillant, Typ Gateway |
| Adresse | `192.168.0.176`, SHIP-Port **12480**, Pfad `/ship/` |
| SKI (Gerätekennung) | `e559530753064ec42eda028d01bbff585ab1326c` |
| ID | `21263500203000000933179283N5` |
| `register` | `false` – nimmt neue Partner **nicht** selbst an; die Freigabe erfolgt in der myVAILLANT-App |

Hinweis: Verbaut ist der **VR 940** (nicht der in `Vaillant_Komponenten.md` angenommene VR 921). Vaillant und
Energiemanager-Anbieter nennen den VR 940f ausdrücklich als EEBUS-Gateway.

Nebenbefund: 11 Shelly-Geräte melden sich per mDNS (`_shelly._tcp`) – Grundlage für Phase 5.

---

## 2. Was EEBUS hier leisten kann

EEBUS besteht aus **SHIP** (verschlüsselte, gegenseitig per Zertifikat authentisierte WebSocket-Verbindung)
und **SPINE** (Datenmodell: Geräte → Entitäten → Features → Funktionen). Fachlich wird über **Anwendungsfälle
(Use Cases)** kommuniziert; beide Seiten melden, welche sie unterstützen.

| Anwendungsfall | Richtung | Inhalt | Für das EMS | Bibliothek |
|---|---|---|---|---|
| **MPC** – Monitoring of Power Consumption | WP → EMS | Leistung (gesamt/je Phase), Energie, Strom, Spannung, Frequenz | Kernwert „Strom WP“ – ergänzt den eigenen WP-Zähler | `ma/mpc` |
| **MDT** – Monitoring of DHW Temperature | WP → EMS | Warmwassertemperatur | Anzeige/Optimierung Warmwasser | `ma/mdt` |
| **OHPCF** – Optimization of self-consumption by Heat Pump Compressor Flexibility | beide | WP meldet geplante/optionale Leistungsaufnahme; EMS kann sie zeitlich verschieben (PV-Überschuss) | später: Steuerung | `cem/ohpcf` |
| **LPC** – Limitation of Power Consumption | EMS → WP | Leistungsbegrenzung (§ 14a EnWG) | später: Steuerung | `eg/lpc` |
| weitere (Raum-/Außentemperatur, Betriebsmodi …) | WP → EMS | laut Drittanbietern teilweise angeboten | **erst Datenstromanalyse** | ggf. eigene Auswertung |

**Welche Anwendungsfälle der VR 940 tatsächlich anbietet, ist nicht öffentlich dokumentiert.** Genau das ermittelt
Stufe A (Abschnitt 5) aus dem echten Datenstrom.

**Wichtig (Vaillant-Regler VRC 720/3):** Übernimmt ein externer Energiemanager die Regelung, zeigt der Regler
„Status Ext. Energiemanager: Aktiv“ und bietet nur noch reduzierte Funktionen. Deshalb meldet der Adapter in
Stufe A **nur Überwachungs-Anwendungsfälle** (MPC, MDT) an – keine steuernden (OHPCF, LPC).

---

## 3. Technik

| Baustein | Wahl | Begründung |
|---|---|---|
| EEBUS-Stack | **enbility/eebus-go** (MIT, Go; SHIP 1.0.1, SPINE 1.3.0) inkl. ship-go/spine-go | aktiv gepflegt; Basis von evcc, das Vaillant-Wärmepumpen per EEBUS anbindet; enthält MPC, MDT, OHPCF, LPC |
| Version | Entwicklungszweig `dev`, Stand Commit `8583642` (30.09.2026), im Build fest auf diesen Commit gepinnt | OHPCF und MDT gibt es nur dort; kein `latest` |
| Programm | eigener kleiner Go-Dienst `ems-eebus` (statt des Beispielwerkzeugs `remote`) | legt nur die gewünschten Anwendungsfälle an, schreibt Analyse-Dateien, spricht MQTT – nichts darüber hinaus |
| Container | eigener Container `ems-eebus` im Compose-Projekt `ems` (normaler Dienst statt Profil, da seit 20.10.2026 aktiv); kein anderer Dienst hängt von ihm ab | Anforderung: eigener Container, Kern startet auch ohne ihn |

### Netzwerk – die eine Sonderlösung

EEBUS braucht **mDNS (Multicast)** zum Finden und eine **direkte TCP-Verbindung** in beide Richtungen. Über
Docker-Bridge-Netze funktioniert Multicast nicht. Deshalb – wie in den Anforderungen vorgesehen („Netzwerk-
sonderlösung ausschließlich beim betreffenden Adapter“) – läuft **nur dieser Container mit `network_mode: host`**:

- eigener SHIP-Port **4712/tcp** (frei geprüft), gebunden an 192.168.0.172
- MQTT zum Broker über `192.168.0.172:1883` mit eigenem Konto `eebus_adapter`
- **UFW:** eingehende Verbindungen nur vom VR 940 zulassen (einmalig, sudo):
  `sudo ufw allow from 192.168.0.176 to any port 4712 proto tcp comment 'EEBUS VR940'`

### Vertrauen und Kopplung (SHIP)

1. Der Adapter erzeugt beim ersten Start ein **eigenes Zertifikat** (dauerhaft in `secrets/eebus/`, Sicherung über
   das Backup) und damit eine **eigene SKI**.
2. Der Adapter vertraut **ausschließlich** der SKI des VR 940 (fest konfiguriert, nicht automatisch jedem Gerät).
3. In der **myVAILLANT-App** → Optionen → Netzwerkeinstellungen → **EEBUS** den neuen Partner bestätigen und die dort
   angezeigte SKI mit der SKI des Adapters vergleichen (steht im Log und in `data/eebus/eigene-ski.txt`).
4. Erst danach steht die verschlüsselte Verbindung; ohne App-Bestätigung bleibt sie im Zustand „wartet auf Vertrauen“.

---

## 4. Datenfluss im EMS

```
VR 940 ──SHIP/SPINE (TLS, 12480/4712)──▶ ems-eebus ──MQTT──▶ ems/v1/raw/eebus/<strom>   ──▶ writer ──▶ ems_raw (7 Tage)
                                              │                                        └─▶ normalizer (ab Stufe B) ──▶ telemetry
                                              └─▶ data/eebus/  discovery-*.json, spine-trace-*.jsonl  (Analyse-Dateien)
```

| Topic | Inhalt |
|---|---|
| `ems/v1/raw/eebus/discovery` | Gerätebeschreibung des VR 940: Entitäten, Features, Funktionen, angebotene Anwendungsfälle |
| `ems/v1/raw/eebus/mpc` | Leistung, Energie, Ströme, Spannungen, Frequenz (sobald gemeldet) |
| `ems/v1/raw/eebus/mdt` | Warmwassertemperatur (sobald gemeldet) |
| `ems/v1/raw/eebus/usecases` | Ereignis „Anwendungsfall unterstützt/geändert“ |
| `ems/v1/availability/eebus` | Verbindungszustand inkl. Kopplungsstatus |

Schreibrechte: Konto `eebus_adapter` darf nur `ems/v1/raw/eebus/#` und `ems/v1/availability/eebus` – **keine**
Befehlstopics (Grundsatz „Lesen vor Schreiben“ bleibt technisch durchgesetzt).

---

## 5. Stufenplan

| Stufe | Inhalt | Ergebnis / Abnahme | Voraussetzung |
|---|---|---|---|
| **A – Anbinden & mitlesen** | Container, Zertifikat, Kopplung, MPC+MDT anmelden, **vollständige Aufzeichnung**: Discovery-Dump und jede SPINE-Nachricht (JSONL, rotierend, 30 Tage) | Verbindung steht; Liste der vom VR 940 angebotenen Anwendungsfälle und Features liegt vor | App-Bestätigung, UFW-Regel |
| **B – Analyse** | 1–2 Wochen Datenstrom auswerten: welche Werte, Einheiten, Aktualisierungsrate, Plausibilität gegen eigenen WP-Zähler/Controme | Analysebericht `docs/EEBUS_Datenanalyse.md`; Fixtures für Tests | Stufe A läuft, WP im Betrieb |
| **C – Integration (lesend)** | Mapping-YAML `eebus_*.yaml`, Normalizer-Parser, Tests, Grafana-Panels „Wärmepumpe“ | Werte in `ems_detail` mit Messzeit, Einheit, Qualität | Stufe B |
| **D – Steuerung** (eigene Freigabe) | OHPCF (PV-Überschuss) bzw. LPC (§ 14a) über den Steuerdienst mit Journal, Timeout, Rückfall | nur nach Phase 8 (Steuerdienst) und ausdrücklicher Freigabe; Verhalten des VRC 720/3 („Ext. Energiemanager“) vorher geklärt | Phase 8 |

---

## 6. Sicherheit und Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| EMS übernimmt ungewollt die Regelung | Stufe A–C nur Überwachungs-Anwendungsfälle; steuernde erst in Stufe D mit Freigabe |
| Host-Netzwerk des Containers | nur dieser Container; läuft ohne Root, ohne Docker-Socket, nur lesende Mounts außer `data/eebus`; UFW lässt Port 4712 nur vom VR 940 zu |
| Fremdes Gerät koppelt sich | Vertrauen ausschließlich zur festen SKI des VR 940; `register=false` auf Vaillant-Seite |
| Entwicklungsstand der Bibliothek (dev-Zweig) | fest gepinnter Commit; Aktualisierung nur bewusst; Aufzeichnung macht Verhaltensänderungen sichtbar |
| Vaillant ändert Firmware/Anwendungsfälle | Discovery wird bei jeder Verbindung neu aufgezeichnet und versioniert verglichen → Qualitätsmeldung |
| Zertifikat verloren → neue SKI → neu koppeln | Zertifikat in `secrets/eebus/`, Teil der täglichen Sicherung |
| Doppelte Energiemanager (z. B. später evcc) | nur **ein** steuernder Manager (Anforderungen Kap. 8); Monitoring parallel unkritisch |

---

## 7. Was vom Nutzer gebraucht wird

1. **UFW-Regel** (sudo, siehe Abschnitt 3).
2. **Kopplung in der myVAILLANT-App** bestätigen und SKI vergleichen (nach dem ersten Start des Adapters).
3. Für Stufe B: WP im Normalbetrieb, eigener WP-Zähler zum Vergleich (falls schon auslesbar).

## Quellen

- enbility/eebus-go: https://github.com/enbility/eebus-go (Use Cases: `usecases/README.md`)
- evcc – Vaillant EEBus: https://docs.evcc.io/de/heating/vaillant-eebus
- evcc – Vaillant aroTHERM plus (EEBus): https://docs.evcc.io/en/heating/vaillant-arotherm-plus-eebus
- gridX – Vaillant aroTHERM und weitere Wärmepumpen (Kopplung, Voraussetzungen): https://support.gridx.de/en/articles/640581-vaillant-arotherm-and-other-heat-pumps
- Home Assistant Community – MyVaillant App vs. EEBUS (Erfahrungsbericht): https://community.home-assistant.io/t/myvaillant-app-vs-eebus-evcc/1014402


---

## 8. Umsetzungsstand

**20.10.2026 – Stufe A gebaut und gestartet:**
- Container `ems-eebus` (Go 1.24, eebus-go Commit 8583642, ship-go/spine-go laut go.sum), `network_mode: host`, UID 1000.
- Eigene SKI: `f366108a61d2d3c3ea00c48227c143f49c3e28bd` (auch in `data/eebus/eigene-ski.txt`), Zertifikat in `secrets/eebus/`.
- SHIP-Verbindung zum VR 940 wird aufgebaut; der VR 940 antwortet `connectionHello phase=pending` →
  **wartet auf Bestätigung in der myVAILLANT-App**. Danach folgen SPINE-Discovery und MPC/MDT-Daten.
- Aufzeichnung läuft: `data/eebus/spine-trace-<Tag>.jsonl` (30 Tage), `discovery-*.json` nach erfolgreicher Kopplung.
- MQTT-Konto `eebus_adapter`, ACL nur `ems/v1/raw/eebus/#` + `ems/v1/availability/eebus`; Normalizer ignoriert
  `eebus` in der Analysephase (keine „kein Mapping“-Meldungen).
- `*.local` wird im Container nicht per mDNS aufgelöst → feste Zuordnung `extra_hosts` auf 192.168.0.176;
  **DHCP-Reservierung für den VR 940 in der FritzBox anlegen**, sonst bricht die Zuordnung bei IP-Wechsel.

**20.10.2026, 19:20 – Kopplung bestätigt, Stufe A vollständig:** Verbindung steht (auch nach Neustart sofort wieder).
Zusätzlich zu MPC/MDT liest und abonniert der Adapter alle lesbaren Features (Measurement, Setpoint, HVAC,
DeviceDiagnosis, DeviceClassification, ElectricalConnection, LoadControl, DeviceConfiguration) – **nur lesend**,
ohne Binding und ohne weitere Anwendungsfälle. Ergebnis und erste Werte: `EEBUS_Datenanalyse.md`.
