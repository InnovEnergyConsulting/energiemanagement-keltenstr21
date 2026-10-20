# Energiemanagement Keltenstr. 21 – Umsetzungskonzept

> **Umsetzungsstand 20.10.2026:** Phasen 0–2 umgesetzt und in Betrieb – siehe `Betriebshandbuch.md`.

Stand: 19.10.2026 · Grundlage: `Anforderungen.md` (Architektur- und Anforderungskonzept vom selben Tag).
Dieses Dokument beschreibt, **wie** das System auf dem vorhandenen KI-Server umgesetzt wird. Fachliche
Regeln (Datenmodell, Schaltlogik, Messgrenzen, Sicherheitsprinzipien) gelten unverändert aus den Anforderungen
und werden hier nicht wiederholt.

---

## 1. Was vorab geklärt werden konnte

### Server (offener Punkt 1 – erledigt)

| Merkmal | Wert |
|---|---|
| System | Ubuntu 24.04.3 LTS, Kernel 7.0.0-38, x86_64 |
| Docker / Compose | 29.2.1 / 5.0.2 |
| CPU / RAM | Intel i9-14900K, 32 Threads / 62 GB, davon ~44 GB frei |
| Speicher | **eine** NVMe-SSD 1 TB, 218 GB frei |
| Netz | 192.168.0.172/24, Gateway 192.168.0.1; Anbindung über **Powerline** (Korrektur 20.10.2026 – für die kleinen EMS-Datenmengen unkritisch) |
| Zeit | NTP aktiv und synchron, Zeitzone Europe/Berlin |
| Last | gering (Load ~1,5 bei 32 Threads); Hauptlast sind GPU-Dienste, die das EMS nicht berühren |
| USB-Lesekopf | keiner angeschlossen |
| Vorhanden | kein MQTT-Broker, keine InfluxDB, kein Grafana – Neuaufbau ohne Altlasten |
| Firewall | UFW aktiv; **Docker umgeht UFW** → Ports nur gezielt an 192.168.0.172 binden |

Bewertung: Der Server ist für das EMS um ein Vielfaches überdimensioniert. Geschätzter Bedarf des EMS-Kerns:
< 2 GB RAM, < 5 % CPU, wenige GB Speicher pro Jahr. **Einzige echte Lücke: Es gibt kein zweites Laufwerk für
Sicherungen** (siehe Abschnitt 7).

### Controme (offener Punkt 5 – weitgehend erledigt)

Der Miniserver ist unter `http://192.168.0.120` im LAN erreichbar. Die lokale API **liefert bereits lesend Daten**
(Abfrage am 19.10.2026, nur lesend):

- `GET /get/json/v1/1/info/` → Raspberry Pi 5, Debian 12, Smart-Heat-OS vom 09.02.2026, Zweig **„development“**
- `GET /get/json/v1/1/temps/` → 2 Etagen mit Räumen, Ist-/Sollwerten, nächster Temperaturszene, Offsets

Haus-ID ist `1`. Offen bleibt nur: Ist für **Schreibzugriffe** (Sollwerte setzen) eine Lizenz nötig bzw. schon gebucht?
Wichtig: Zweig „development“ bedeutet häufigere Änderungen – die geforderte Schema-Prüfung nach Updates (Anforderungen
Kap. 6) ist hier besonders wichtig.

### Lage

Keltenstr. 21 liegt in Frankenthal-Eppstein; der Server steht im selben Haus → reiner LAN-Betrieb wie gefordert,
keine Standortkopplung nötig. *(Bitte bestätigen, falls der Server woanders steht.)*

### Zeitfenster Wärmepumpe

Laut Kalender wird die **Wärmepumpe vom 19. bis 23.10.2026 eingebaut**. Das ist die günstigste Gelegenheit für die
Vaillant-Fragen (Abschnitt 8) und für Messtechnik im WP-Stromkreis – Elektriker ist ohnehin vor Ort.

---

## 2. Leitentscheidungen

1. **Eigenes Projekt, eigene Compose-Datei**: `/home/horst/KI-Applikationen/energiemanagement-keltenstr21/`,
   eigenes privates Git-Repo mit automatischem Push (Muster wie `kdenlive-server`), Git ist nur Backup
   (lokal ist maßgeblich).
2. **Vollständig unabhängig von GPU-Diensten**: keine GPU, keine gemeinsamen Netze mit KI-Diensten, eigene
   Ressourcenlimits. Der Ausfall am 19.10.2026 (Grafiktreiber nach Kernel-Update) hätte das EMS nicht berührt –
   so soll es bleiben.
3. **Fertige Produkte für Infrastruktur, eigene Software nur für die Fachlogik**: Mosquitto, InfluxDB 2, Grafana,
   Caddy, restic sind fertig. Adapter, Normalisierung, Writer, Steuerdienst und Bedienseite sind Eigenentwicklung.
4. **Eine Codebasis für alle Eigenentwicklungen**: Python 3.12, ein Image `ems-core`, mehrere Rollen
   (`meter-adapter`, `controme-adapter`, `normalizer`, `writer`, `controller`, `rest-monitor`) über den Startbefehl.
   Gleiche Bibliotheken, ein Testpaket, ein Build – weniger Pflegeaufwand als sechs Einzelprojekte.
5. **Lesen vor Schreiben**: Bis Phase 8 existiert im System kein schreibender Pfad zu einem Gerät. Die Broker-ACLs
   sperren Befehlstopics technisch, nicht nur per Konvention.
6. **Entwicklung mit aufgezeichneten Daten**: Jede Quelle bekommt Beispiel-Payloads im Repo. Normalisierung und
   Writer werden dagegen getestet, bevor ein echtes Gerät angeschlossen wird.

---

## 3. Einbettung in die bestehende Serverlandschaft

| Thema | Umsetzung |
|---|---|
| Autostart | Neue Gruppe `ems` in `system-startup/startup.sh`, früh im Ablauf (vor den KI-Diensten) |
| Dashboard | Kachel „Energiemanagement“ im System-Dashboard, Link auf die EMS-Startseite |
| Zugangsdaten | Alle Passwörter/Tokens in **Vaultwarden**; auf dem Server als Compose-Secrets (Dateien `chmod 600`, nicht im Git) |
| Port-Übersicht | Neue Ports in `Systemports.md` eintragen |
| Mobil | entfällt – Tailscale am 20.10.2026 entfernt, kein Fernzugang gewünscht |
| Benachrichtigung (optional) | Anforderungen sehen nur LAN-Alarme vor; der vorhandene Telegram-Bot von Lisa könnte später kritische Alarme senden |
| Lisa (optional, später) | Werkzeug „Energie heute?“ über eine lesende API des Steuerdienstes |

### Ports (alle nur an 192.168.0.172 gebunden)

| Port | Dienst | Hinweis |
|---|---|---|
| 8443 | Caddy (HTTPS) → `/grafana`, `/bedienung`, `/status` | einziger Web-Einstieg |
| 8883 | Mosquitto MQTT mit TLS | Standard für Geräte |
| 1883 | Mosquitto ohne TLS | nur für Geräte ohne TLS, eigene Konten + ACL |
| – | InfluxDB, Writer, Controller, Normalizer | **kein** veröffentlichter Port, nur internes Netz |

Port 3000 (Grafana-Standard) ist durch Kdenlive belegt – Grafana läuft daher nur hinter Caddy.

---

## 4. Dienste und Technik im Detail

| Compose-Dienst | Technik | Netze | Ressourcen (Start) |
|---|---|---|---|
| `mosquitto` | eclipse-mosquitto 2.x (Version beim Aufbau festpinnen) | backend, device | 128 MB |
| `influxdb` | InfluxDB **2.7.x** (bewusst nicht 3, siehe Anforderungen) | backend | 1 GB |
| `grafana` | Grafana 12.x, Provisionierung aus dem Repo | frontend, backend | 512 MB |
| `proxy` | Caddy 2, `tls internal` (eigene LAN-CA) | frontend | 64 MB |
| `normalizer` | ems-core | backend | 128 MB |
| `writer` | ems-core, Puffer in SQLite auf eigenem Volume | backend | 128 MB |
| `controller` + `control-ui` | ems-core, FastAPI, Journal in SQLite; Oberfläche serverseitig gerendert (HTMX) | frontend, backend | 256 MB |
| `rest-monitor` | ems-core | backend, device | 64 MB |
| `controme-adapter` | ems-core, 60-s-Polling | backend, device | 64 MB |
| `meter-adapter` | ems-core; Protokoll-Bibliothek nach Zählerprüfung | backend, device (+USB) | 64 MB |
| `backup` | restic + Influx-Backup, per Zeitplan | backend | 256 MB |
| Profil `vaillant` | ebusd und/oder EEBUS-Client | device | nach Prüfung |
| Profil `pv` | pv-adapter | device | später |

**Warum Caddy:** liefert HTTPS im LAN mit eigener Zertifizierungsstelle ohne manuelle Zertifikatspflege.
Das CA-Zertifikat wird einmal auf PC und Handy importiert, danach gibt es keine Browser-Warnungen.

**InfluxDB-Buckets** (entsprechen den Aufbewahrungs-Startannahmen):

| Bucket | Inhalt | Aufbewahrung |
|---|---|---|
| `ems_raw` | Rohpayloads zur Fehlersuche | 7 Tage |
| `ems_detail` | normalisierte Messwerte | 400 Tage |
| `ems_agg` | Stunden-/Tagesaggregate (Influx-Tasks) | unbegrenzt |
| `ems_events` | Befehlsjournal-Spiegel, Verfügbarkeit, Qualität | 5 Jahre |

Grafana erhält ein Token nur mit Leserechten, der Writer nur Schreibrechte auf `ems_raw`/`ems_detail`/`ems_events`.

---

## 5. Repository-Struktur

```
energiemanagement-keltenstr21/
├─ docker-compose.yml          Kern + Profile vaillant / pv
├─ .env.example                ohne Geheimnisse
├─ secrets/                    (nicht im Git) Compose-Secrets
├─ config/
│  ├─ mosquitto/               mosquitto.conf, acl, Zertifikate (ro)
│  ├─ caddy/Caddyfile
│  ├─ grafana/provisioning/    Datenquelle + Dashboards als JSON
│  ├─ influxdb/init/           Buckets, Tokens, Downsampling-Tasks
│  └─ mapping/                 versionierte YAML-Profile je Quelle
├─ ems-core/                   Python-Paket (alle Eigenentwicklungen)
│  ├─ ems/adapters/            meter, controme, (später) vaillant, pv
│  ├─ ems/normalizer/  ems/writer/  ems/controller/  ems/monitor/
│  ├─ tests/fixtures/          aufgezeichnete Beispiel-Payloads je Quelle
│  └─ Dockerfile
├─ inventory/                  Geräteinventar (YAML) + Fotos Typenschilder
├─ scripts/                    backup-to-github.sh, restore-test.sh
└─ docs/                       Anforderungen.md, Umsetzungskonzept.md, Betriebshandbuch.md
```

---

## 6. Phasenplan mit Aufwand

Die Reihenfolge folgt den Anforderungen (Kap. 12), ist aber so geschnitten, dass **sofort** begonnen werden kann –
fehlende Hardware-Informationen blockieren erst ab Phase 3.

| Phase | Inhalt | Voraussetzung | Aufwand | Abnahme |
|---|---|---|---|---|
| **0** | Repo, Compose-Kern (Mosquitto, InfluxDB, Grafana, Caddy), Secrets, ACLs, Autostart, Dashboard-Kachel | keine | 1 Tag | Neustart des Servers übersteht alles; HTTPS im LAN |
| **1** | ems-core-Grundgerüst: Normalizer, Writer mit Puffer, Mapping-Format, Tests mit Beispiel-Payloads | keine | 1,5 Tage | DB-Ausfall wird gepuffert und nachgeholt; Duplikate erkannt |
| **2** | **Controme lesend** (Räume, Ist/Soll, Szenen, Offsets) + erstes Grafana-Dashboard „Heizung/Räume“ | keine – API antwortet bereits | 1 Tag | Werte = Anzeige im Smart-Heat-OS; Datenalter sichtbar |
| **3** | Backup: restic → USB-SSD (täglich) + verschlüsselte Kopie nach OneDrive; Wiederherstellungstest in getrenntem Compose-Projekt | USB-SSD formatiert + eingebunden (sudo), OneDrive-Freigabe (einmalig) | 0,5–1 Tag | Wiederherstellung erfolgreich getestet |
| **4** | Optischer Zähler: Modell bestimmen, Lesekopf, meter-adapter | Zählerdaten + Lesekopf | 0,5–1 Tag | Zählerstand = Display; Einheit/Vorzeichen geprüft |
| **5** | Shelly/Tasmota lesend, je Modell ein Profil | Geräteinventar | 0,5–1 Tag | Energieeinheit und Reset-Verhalten geprüft |
| **6** | Dashboards Strom (Tag/Monat/Jahr), Betrieb, REST-Monitor | 4 + 5 | 1 Tag | keine Doppelzählung; Lücken sichtbar |
| **7** | Messgrenzen Haus/WP/Gartenhaus festlegen (mit Gültigkeitsdatum) | Entscheidung Nutzer | 0,5 Tage | historisch korrekte Zuordnung |
| **8** | Steuerdienst + Bedienseite, **ein** unkritischer Verbraucher | 7, Rollen festgelegt | 2–3 Tage | Rückmeldung, Timeout, Verriegelung, Journal |
| **9** | Vaillant nach Modellfestlegung (EEBUS / eBUS / Kontakte) | Abschnitt 8 | 1–3 Tage | Schnittstellen + Wärmebilanz plausibel |
| **10–11** | Automatik erst protokollierend, dann begrenzt; später PV | 8, 9 | je nach Umfang | Ausfallverhalten bestätigt |

**Summe bis zu einem nutzbaren lesenden System (Phasen 0–6): ca. 6–8 Arbeitstage**, davon 3,5 Tage sofort startbar.

---

## 7. Entscheidungen (getroffen am 19.10.2026)

| Frage | Entscheidung | Folge für die Umsetzung |
|---|---|---|
| Backup-Ziel | **Zweistufig: lokale USB-SSD + verschlüsselte Kopie in OneDrive** | **Stufe 1 – USB-SSD am Server** (Verbatim Vi550 S3, 512 GB, SSD, USB 3 mit 5 Gbit/s, erkannt am 19.10.2026): tägliches restic-Backup, schnelle Wiederherstellung ohne Internet. Die Platte ist derzeit eine **ehemalige Windows-Systemplatte** (NTFS mit System-, EFI- und Wiederherstellungspartition, nicht eingebunden) – vor der Nutzung **neu formatieren als ext4** (löscht den Inhalt; vorher klären, ob darauf noch etwas gebraucht wird). Einbindung fest per UUID mit `nofail`, damit der Server auch ohne angesteckte Platte startet; fehlt sie, meldet der Backup-Dienst das, das EMS läuft weiter. **Stufe 2 – OneDrive**: restic-Kopie mit rclone-Backend nach `Backups/EMS-Keltenstr21`, **Ende-zu-Ende verschlüsselt**, schützt vor Brand/Diebstahl/Defekt im Haus. Restic-Schlüssel in Vaultwarden; einmalige OneDrive-Freigabe im Browser (`rclone authorize`). Der alte TrailView-Synchronisierer `onedrive-sync` wird **nicht** wiederverwendet (Zwei-Wege-Sync). Die USB-SSD kann zusätzlich Sicherungen anderer Server-Projekte aufnehmen (heute gibt es für den gesamten Server kein zweites Laufwerk). |
| Netz | **LAN und WLAN im Heimnetz**; Controme-Miniserver über Powerline, Shellys über WLAN | WLAN-Shellys und Tasmota-Steckdosen sind ab Phase 5 sofort einbindbar – keine Ersatzbeschaffung. Gen2+-Shellys per MQTT-TLS (8883), Gen1/Tasmota ohne TLS über 1883 mit eigenem Konto je Gerät. Powerline/WLAN haben gelegentlich Aussetzer → Datenalter-Überwachung und Wiederverbindung sind Pflicht, Lücken werden angezeigt statt überbrückt. |
| Controme | **EMS darf bei Bedarf steuernd eingreifen** | Phase 8 umfasst auch Controme-Sollwerte (`set/json/v1/1/ziel/<raum>/` mit Ablaufzeit `duration`, damit der Miniserver bei EMS-Ausfall selbst zurückfällt). Vorher prüfen: Ist die API-Lizenz für Schreibzugriffe gebucht? Falls nicht: Buchung im Controme-Shop mit MAC-Adresse. Lesen funktioniert bereits ohne weitere Freischaltung. |
| Standort | Server steht im Haus Keltenstr. 21 | Reiner LAN-Betrieb, keine Standortkopplung. |
| Repo | Vorschlag `InnovEnergyConsulting/energiemanagement-keltenstr21` (privat) | wird in Phase 0 angelegt, sofern kein anderer Name gewünscht |

## 8. Fragen an Installateur / Elektriker während des WP-Einbaus (diese Woche)

**Wärmepumpe / Vaillant**
- Genaue Modelle: Außeneinheit (z. B. aroTHERM …), Innen-/Hydraulikmodul, Regler (z. B. sensoCOMFORT), Internetmodul (z. B. VR 921)?
- Ist **EEBUS** aktivierbar? Ist ein **eBUS**-Anschluss frei zugänglich (für späteres Auslesen)?
- **SG-Ready / PV-Ready**-Kontakte vorhanden und beschaltbar?
- **Wärmemengenzähler: vorgesehen** (Anforderungen Kap. 8). Modell, Einbauort, Schnittstelle (z. B. M-Bus, Impuls, optisch) und Messgrenze folgen mit den WP-Detaildaten (Stand 20.10.2026).
- Heizstab vorhanden? Getrennt messbar?

**Elektrik**
- **Eigener WP-Zähler: ja** (bestätigt 20.10.2026) – Modell, optische Schnittstelle, PIN?
- Kann im WP-Stromkreis ein **Hutschienen-Messgerät** (z. B. Shelly Pro 3EM) vorgesehen werden – **nur messen, nicht schalten**?
- Platz im Verteiler und Netzwerkkabel zum Verteiler (für Ethernet-Messgeräte bzw. Zähler-Lesekopf-Gateway)?

---

## 9. Geräteinventar – was ich von dir brauche

Ablage: `inventory/` im Repo (Fotos genügen, ich übertrage sie ins YAML).

| Quelle | Was | Wo zu finden |
|---|---|---|
| Stromzähler | Foto Typenschild + Display, ggf. PIN-Brief vom Netzbetreiber | Zählerschrank |
| Shelly/Tasmota | Modellname, Firmware-Version, IP | Shelly-App bzw. Geräte-Webseite → Geräteinformationen |
| Controme | Lizenz für Schreibzugriff vorhanden? MAC-Adresse | Smart-Heat-OS → Einstellungen / Systeminformation |
| Vaillant | Antworten aus Abschnitt 8 | Installateur, Typenschilder |
| Netzwerk | Gibt es am Router feste IP-Zuordnungen (DHCP-Reservierung)? Router-Modell | Router-Oberfläche 192.168.0.1 |

---

## 10. Risiken und wie sie abgefangen werden

| Risiko | Gegenmaßnahme |
|---|---|
| Controme „development“-Zweig ändert API | Referenzantworten im Repo, Schema-Prüfung im REST-Monitor, Adapter meldet Abweichung statt falscher Werte |
| Server-Ausfall (z. B. Kernel/Treiber wie am 19.10.) | EMS ohne GPU-Abhängigkeit; Heizung regelt lokal weiter; Writer-Puffer; Autostart-Gruppe früh |
| Einzige SSD im Server | Lokale USB-SSD + verschlüsselte OneDrive-Kopie (Abschnitt 7), monatlicher Wiederherstellungstest |
| Falsche Bilanzen durch unklare Messgrenzen | Bis Phase 7 keine Haus-/WP-/Gartenhaus-Bilanz, nur Einzelmesspunkte |
| Versehentliches Schalten | bis Phase 8 kein schreibender Pfad, Broker-ACL sperrt Befehlstopics |
| Docker umgeht UFW | Ports ausschließlich an 192.168.0.172 binden, InfluxDB ohne Port |

---

## 11. Vorschlag für den Start

Sofort und ohne weitere Hardware-Informationen machbar: **Phasen 0, 1 und 2** – Grundsystem, Datenkern und die
Controme-Raumdaten in Grafana. Damit gibt es nach ca. 3–4 Arbeitstagen ein laufendes System mit echten Heizungsdaten,
in das Zähler, Shellys und Vaillant anschließend nur noch „eingesteckt“ werden.
