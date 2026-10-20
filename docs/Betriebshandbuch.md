# EMS Keltenstr. 21 – Betriebshandbuch

Stand: 20.10.2026 · Ausbaustufe: **Phasen 0–2 (lesend)** – Grundsystem, Datenkern, Controme-Heizungsdaten.
Konzept: `Umsetzungskonzept.md` · Anforderungen: `Anforderungen.md` · Vaillant: `Vaillant_Komponenten.md`

---

## 1. Zugang

| Was | Adresse | Anmeldung |
|---|---|---|
| Grafana (Auswertung) | https://192.168.0.172:8443/grafana/ | Benutzer `admin`, Passwort in `secrets/grafana_admin_password` |
| Dashboard „Heizung – Räume (Controme)“ | Ordner *Energiemanagement* | – |
| MQTT für Geräte (TLS) | `192.168.0.172:8883`, CA: `zertifikate/EMS-MQTT-CA.crt` | eigenes Konto je Gerät (kommt mit Phase 5) |
| MQTT ohne TLS | `192.168.0.172:1883` | nur für Geräte ohne TLS |
| Kachel im System-Dashboard | „Energiemanagement“ | – |

**Zertifikatswarnung im Browser abschalten (einmalig je Gerät):** `zertifikate/EMS-HTTPS-Stammzertifikat.crt`
(öffentliches Zertifikat, gültig bis 2036) als vertrauenswürdige Stammzertifizierungsstelle importieren –
Windows: Doppelklick → *Zertifikat installieren* → *Lokaler Computer* → *Vertrauenswürdige Stammzertifizierungsstellen*;
Firefox nutzt einen eigenen Speicher (Einstellungen → Zertifikate → Importieren).

**Zugangsdaten** liegen ausschließlich in `secrets/` (chmod 600, nicht im Git). Sicherungskopie der Werte in
Vaultwarden, Sammlung „EMS Keltenstr. 21“. Neu erzeugen: `scripts/init-secrets.sh` (überschreibt nichts Vorhandenes).

---

## 2. Aufbau

```
Controme-Miniserver 192.168.0.120 ──HTTP, nur lesend, 60 s──▶ ems-controme-adapter
                                                                  │ ems/v1/raw/controme/{temps,info}
                                                                  ▼
                                            ems-mosquitto (MQTT, Konten + ACL)
                                       ▲          │                       │
                       ems/v1/telemetry|state     │ raw                   │ alles außer command
                       |quality                   ▼                       ▼
                                          ems-normalizer             ems-writer ──▶ ems-influxdb ──▶ ems-grafana ──▶ ems-proxy (HTTPS 8443)
                                     (Mapping, Einheiten,        (Puffer SQLite bei
                                      UTC, Duplikate, Schema)     DB-Ausfall)
```

| Container | Aufgabe | Speicher |
|---|---|---|
| `ems-mosquitto` | MQTT-Broker 2.0.22 | `data/mosquitto` |
| `ems-influxdb` | InfluxDB 2.7.12, kein Host-Port | `data/influxdb`, `data/influxdb-config` |
| `ems-grafana` | Grafana 12.4.1, Dashboards aus `config/grafana/dashboards` (im Git, in der Oberfläche nicht änderbar) | `data/grafana` |
| `ems-proxy` | Caddy 2.10.2, HTTPS mit eigener LAN-CA | `data/caddy` |
| `ems-controme-adapter` | ems-core, Rolle `controme-adapter` | – |
| `ems-normalizer` | ems-core, Rolle `normalizer`, Mapping `config/mapping/*.yaml` | – |
| `ems-writer` | ems-core, Rolle `writer` | `data/writer/buffer.sqlite` |

**Datenablage (InfluxDB, Organisation `keltenstr21`):**

| Bucket | Inhalt | Aufbewahrung |
|---|---|---|
| `ems_detail` | Messwerte (`telemetry`) | 400 Tage |
| `ems_agg` | Stundenwerte (Task `ems_agg_hourly`: Mittelwert bzw. letzter Zählerstand) | unbegrenzt |
| `ems_events` | Zustände (`state`), Verfügbarkeit (`availability`), Qualität (`quality`) | 5 Jahre |
| `ems_raw` | unveränderte Antworten der Quellen (`raw`) zur Fehlersuche | 7 Tage |

Messwerte tragen die **Messzeit** (bei Controme: letzte Übertragung des Sensors) als Zeitstempel und das
**Datenalter** (`age_s`) als Feld. Fehlende Werte (z. B. Räume im OG ohne Sensor) werden **nicht** als 0 gespeichert.

---

## 3. Bedienung

```bash
cd /home/horst/KI-Applikationen/energiemanagement-keltenstr21
docker compose ps                       # Zustand
docker compose logs -f writer           # Logs eines Dienstes
docker compose up -d --build            # nach Code-/Konfigurationsänderung (Tests laufen im Build)
docker compose restart grafana          # nach Änderung eines Dashboards
../system-startup/startup.sh ems        # Start über das Autostart-Skript (läuft auch bei "all" früh mit)
```

Alle Dienste haben `restart: unless-stopped` und starten nach einem Server-Neustart selbst.

**Mitlesen auf dem Broker (Fehlersuche, nur lesend):**
```bash
docker exec ems-mosquitto mosquitto_sub -h localhost -u ems_admin -P "$(cat secrets/mqtt_ems_admin)" -t 'ems/v1/#' -v
```

---

## 4. Sicherheitsregeln (technisch durchgesetzt)

- **Keine Schreibzugriffe auf Geräte**: Es gibt keinen Code für Controme-`/set/…`, und **kein MQTT-Konto** darf auf
  `ems/v1/command/#` schreiben (am 20.10.2026 geprüft: 0 Nachrichten kamen an). Freigabe erst in Phase 8.
- Anonyme MQTT-Verbindungen werden abgewiesen; jedes Konto hat nur seine Topics (`config/mosquitto/acl`).
- Ports nur an `192.168.0.172`; InfluxDB hat keinen Host-Port; das Backend-Netz hat keinen Internetzugang;
  Grafana lädt keine Plugins nach.
- Grafana liest mit einem reinen **Lese-Token**, der Writer schreibt mit einem Token ohne Leserechte.

---

## 5. Verhalten bei Störungen (am 20.10.2026 getestet)

| Störung | Verhalten |
|---|---|
| InfluxDB weg | Writer puffert in SQLite (max. 500 000 Zeilen, danach werden die ältesten verworfen und das im Log gemeldet); nach Wiederkehr wird in Originalreihenfolge nachgeschrieben. Test: 2 min Ausfall, 162 Zeilen gepuffert, vollständig nachgeschrieben. |
| Controme nicht erreichbar | Adapter meldet `offline` (Dashboard „Controme erreichbar“), wiederholt mit wachsender Wartezeit (max. 60 s für temps); Daten fehlen für diese Zeit (kein Erfinden). |
| Broker weg | Dienste verbinden sich selbst neu (1 s … 120 s). Werte aus der Ausfallzeit fehlen. |
| Controme-Update ändert das Antwortformat | Normalizer meldet unbekannte/fehlende Felder als Qualitätsmeldung (Dashboard), stürzt nicht ab. |
| Datenbank lehnt Daten dauerhaft ab (Format) | Writer protokolliert und verwirft diese Zeilen, damit der Puffer nicht blockiert. |

---

## 6. Entwicklung

- Code: `ems-core/ems/…` (Python 3.12), Tests: `ems-core/tests` mit aufgezeichneten Controme-Antworten
  (`tests/fixtures/controme/`). Der Image-Build bricht ab, wenn ein Test fehlschlägt (11 Tests, Stand 20.10.2026).
- Neue Quelle anbinden: Adapter veröffentlicht nach `ems/v1/raw/<quelle>/<strom>`, Mapping-YAML in
  `config/mapping/`, Parser im Normalizer, ACL-Eintrag, Beispiel-Payload als Test-Fixture.

---

## 7. Offene Punkte / nächste Phasen

| Phase | Inhalt | Wartet auf |
|---|---|---|
| 3 | Backup: restic → USB-SSD + verschlüsselt nach OneDrive | Formatierung der USB-SSD (sudo, Inhalt vorher klären), OneDrive-Freigabe |
| 4 | Optischer Zähler (Haus) und **eigener WP-Zähler** | Zählermodelle, Lesekopf |
| 5 | Shelly/Tasmota lesend | Geräteinventar |
| 9 | Vaillant (VR 921 / VRC 720/3, Wärmemengenzähler) | WP-Detaildaten |
| – | GitHub-Repo `InnovEnergyConsulting/energiemanagement-keltenstr21` + Auto-Backup | Repo anlegen bzw. `gh auth login` (Anmeldung abgelaufen) |
