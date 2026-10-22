# Kosten und Tarife – Konzept (Erweiterung des EMS)

Stand: 22.10.2026 · Status: **Schritte 1–4 umgesetzt** (Einstellungen, Kostenrechner, Anzeige, Nachberechnung ab 20./21.10.)

**Umsetzung 22.10.2026 – wo was liegt:**
- Preisablage `data/tarife/tarife.sqlite` (Code `ems-core/ems/kosten/tarife.py`), Reiter „Einstellungen“ im Leitstand, ohne Anmeldung.
- Kostenrechner im Normalizer (`ems/kosten/rechner.py`) → `ems/v1/kosten/<zählpunkt>` → InfluxDB `kosten` (ems_detail),
  Stundensummen per Task `ems_kosten_hourly` nach `ems_agg` (Preis als Tag erhalten). Letzter Stand retained unter
  `ems/v1/kostenstand/<zählpunkt>` (Neustart ohne Mengenverlust).
- Nachberechnung: `python -m ems kosten-rechnung <haushalt|waermepumpe|einspeisung> <von> <bis> [--trocken]`
  (Token `secrets/influx-tokens/kosten_token`); schreibt die nächste Berechnungsversion, nie überschreibend.
- Grundpreis wird aus der Preishistorie zeitanteilig berechnet (unabhängig von Messwerten).
- **Hinweis:** Bis zum eigenen WP-Zähler misst der Hauptzähler auch die WP → WP-Kosten sind in den Haushaltskosten
  enthalten (in der Oberfläche gekennzeichnet, nicht addieren).

## 1. Ziel

Jedem Verbrauch wird **beim Eintreffen** der Preis zugeordnet, der **zum Zeitpunkt des Verbrauchs** galt. Gespeichert werden
Menge, Preis und Kosten **gemeinsam** – samt Kennung der Preisversion. So lassen sich auch nach Jahren Perioden
vergleichen (z. B. Januar 2025 ↔ Januar 2026) in kWh, in € und mit der jeweiligen Preisgrundlage, ohne nachzurechnen.

Grundsätze:

1. **Preise werden nie überschrieben**, sondern mit „gültig ab“ fortgeschrieben (Preishistorie).
2. **Kosten werden beim Eintreffen berechnet und festgeschrieben** – mit dem Preis zum Messzeitpunkt (nicht zum
   Empfangszeitpunkt: wichtig, wenn der Writer nach einem Ausfall gepufferte Werte nachliefert).
3. **Keine stillen Korrekturen:** Wird ein Preis nachträglich geändert (z. B. rückwirkende Preiserhöhung), rechnet das EMS
   den betroffenen Zeitraum nur auf ausdrücklichen Auftrag neu; die alte Berechnung bleibt als Version erhalten.
4. **Langfristig gespeichert:** Detaildaten werden nach 400 Tagen gelöscht (`ems_detail`). Kosten müssen deshalb in die
   unbegrenzte Ablage (`ems_agg`) – mit Menge, Preis und Preisversion.

---

## 2. Zählpunkte und Kostenarten

| Zählpunkt | Messgerät | Einheit | Preisbestandteile | Stand |
|---|---|---|---|---|
| **Haushaltsstrom** | Hauptzähler Iskra MT691 (WattWächter) | kWh | Arbeitspreis ct/kWh, Grundpreis €/Monat | Daten fließen |
| **Wärmepumpenstrom** | eigener WP-Zähler (kommt) | kWh | WP-Arbeitspreis ct/kWh, Grundpreis €/Monat, ggf. § 14a-Netzentgeltreduzierung | bis dahin: berechnet aus EEBUS-Leistung (als „geschätzt“ gekennzeichnet) |
| Einspeisung (später, PV) | Hauptzähler 2.8.0 | kWh | Vergütung ct/kWh (Erlös, negativ) | vorbereitet |
| **Wasser** | Wasserzähler (kommt) | m³ | Arbeitspreis ct/m³, Grundpreis €/Monat, optional Abwasser ct/m³ | vorbereitet |

Anmerkung Wasser: Der Wasserpreis wird je **Kubikmeter (m³)** abgerechnet; „ct/qm“ ist vermutlich so gemeint.

**Messgrenze Haushalt:** Hat die WP einen **eigenen Zählpunkt des Netzbetreibers** (eigener Zähler, eigener Tarif), misst
der Hauptzähler nur den Haushalt → Haushaltskosten = Hauptzähler × Haushaltstarif. Hängt der WP-Zähler dagegen **hinter**
dem Hauptzähler (Unterzähler), gilt: Haushalt = Hauptzähler − WP-Zähler. Diese Zuordnung wird als Einstellung mit
„gültig ab“ hinterlegt (Anforderungen Kap. 7: Messgrenzen mit Gültigkeitsdatum), damit ein späterer Umbau die
Vergangenheit nicht verfälscht. **Bitte klären**, sobald der WP-Zähler eingebaut ist.

---

## 3. Preishistorie (Tarife)

Je Zählpunkt eine Liste von **Preisversionen**:

| Feld | Beispiel | Bemerkung |
|---|---|---|
| Zählpunkt | Haushaltsstrom | |
| gültig ab | 01.01.2026 00:00 | Ende = Beginn der nächsten Version (keine Lücken, keine Überschneidungen) |
| Arbeitspreis | 32,48 ct/kWh | **brutto** (inkl. MwSt.), wie auf der Rechnung |
| Grundpreis | 13,50 €/Monat | brutto; wird tageweise verteilt |
| MwSt.-Satz | 19 % | nur zur Dokumentation / Nettoauswertung |
| Anbieter / Tarifname | z. B. „Stadtwerke … Wärmestrom“ | Freitext |
| Quelle / Notiz | „Rechnung 2025, S. 2“ | Nachvollziehbarkeit |
| erfasst am / von | automatisch | Journal |

Regeln:
- Neue Preise = **neue Version** mit eigenem „gültig ab“. Bestehende Versionen sind schreibgeschützt, sobald Kosten mit
  ihnen berechnet wurden; Korrekturen erzeugen eine neue Version und (auf Wunsch) eine Neuberechnung (Kap. 5).
- Später erweiterbar um **zeitvariable Preise** (z. B. § 14a Modul 3 / dynamischer Tarif): dann je Version eine
  Preisliste nach Uhrzeit oder Börsenpreis – das Datenmodell sieht dafür einen Preis **je Intervall** vor.

Ablage: kleine SQLite-Datenbank `data/tarife/tarife.sqlite` (wie die Meldungen, mit Änderungsjournal, im täglichen
Backup). Zusätzlich wird jede Preisversion als Ereignis in InfluxDB (`ems_agg`, unbegrenzt) mitgeschrieben – die
Preisgrundlage liegt damit auch neben den Messdaten und überlebt jede Auswertung.

---

## 4. Berechnung beim Eintreffen

```
Zählerstand t1 ──┐
Zählerstand t2 ──┴─► Menge = t2 − t1 (kWh / m³) ─► Preisversion zum Messzeitpunkt ─► Kosten = Menge × Arbeitspreis
                                                                                  └► gespeichert: Menge, Preis, Kosten, Version
```

- **Wo:** im Normalizer als zusätzlicher Schritt („Kostenrechner“) direkt nach der Zählerprüfung. Er liest die Tarife
  nur (Datei schreibgeschützt eingebunden) und lädt sie bei Änderung neu.
- **Menge aus Zählerdifferenzen**, nicht aus Leistung (genauer, lückenrobust). Für die WP bis zum eigenen Zähler aus der
  EEBUS-Leistung (Kennzeichnung `geschaetzt`).
- **Preiswechsel mitten im Intervall** (z. B. 10-s-Telegramm über Mitternacht zum 1.1.): Menge anteilig auf beide
  Versionen aufteilen.
- **Grundpreis:** einmal je Tag als eigener Kostenposten (Monatspreis × 12 / 365), damit Monats- und Jahressummen die
  Rechnung abbilden.
- **Gespeichert je Intervall** (Detail, 400 Tage) **und als Stunden- und Tagessumme** (`ems_agg`, unbegrenzt):

| Feld | Bedeutung |
|---|---|
| zaehlpunkt | haushalt / waermepumpe / einspeisung / wasser |
| menge, einheit | kWh bzw. m³ |
| arbeitspreis | ct je Einheit, der verwendete Preis |
| kosten_arbeit, kosten_grund | € |
| tarif_version | Kennung der Preisversion |
| berechnung_version | 1 = Erstberechnung, 2… = Neuberechnung |
| herkunft | gemessen / geschätzt |

Damit steht in jedem Tageswert auch nach Jahren: *wie viel*, *zu welchem Preis*, *was es gekostet hat* und *woher*.

---

## 5. Nachträgliche Änderungen

| Fall | Verhalten |
|---|---|
| neuer Preis ab heute/zukünftig | neue Version anlegen – wirkt ab „gültig ab“ automatisch |
| Preis vergessen, rückwirkend erfasst | neue Version mit Vergangenheitsdatum → EMS zeigt „Zeitraum X–Y mit altem Preis berechnet – neu berechnen?“ |
| Tippfehler in einer Version | Korrekturversion; Neuberechnung des Zeitraums |
| Neuberechnung | erzeugt `berechnung_version` n+1 für den Zeitraum; Auswertungen nutzen die neueste, die alte bleibt sichtbar (Journal) |

Neu berechnet werden kann nur, solange Mengen vorliegen: Detail 400 Tage, Stunden-/Tagesmengen unbegrenzt (`ems_agg`) –
also auch ältere Zeiträume auf Stundenbasis.

---

## 6. Bereich „Einstellungen“ im Leitstand

Neuer Reiter **„Einstellungen“** (rechts, nach „Steuerung Shelly“):

1. **Tarife** – je Zählpunkt eine Tabelle der Preisversionen (gültig ab, Arbeitspreis, Grundpreis, Anbieter, Notiz,
   erfasst am). Knopf „Neuer Preis ab …“ mit Formular; Prüfung auf Lücken/Überschneidungen; Hinweis, wenn eine Version
   bereits verwendet wurde.
2. **Messgrenzen** – Zuordnung Hauptzähler/WP-Zähler (eigener Zählpunkt oder Unterzähler) mit „gültig ab“.
3. **Neuberechnung** – offene Hinweise „Zeitraum mit veraltetem Preis berechnet“ und Knopf zum Neuberechnen.
4. **Änderungsjournal** – wer hat wann welchen Preis angelegt/geändert.

**Schutz:** Preise bestimmen alle Kostenauswertungen. Der Leitstand ist bisher ohne Anmeldung erreichbar
(Passwortschutz der LAN-Dienste wurde zurückgestellt). Für den Bereich Einstellungen schlage ich eine **einfache
Anmeldung** vor (Passwort über Caddy, nur für `/einstellungen` und die zugehörigen Schreibzugriffe) – Anzeige bleibt frei.

---

## 7. Anzeige in den Reitern

- **Regelung WP:** Verbrauch **und Kosten** heute / Monat / Jahr (getrennt Heizung / Warmwasser), Ø ct/kWh im Zeitraum,
  Kennzeichnung „geschätzt“, solange der WP-Zähler fehlt.
- **Strom:** Haushaltsverbrauch und -kosten heute / Monat / Jahr, Tagesbalken in kWh oder € umschaltbar.
- **Periodenvergleich** (später eigener Bereich „Auswertung“): zwei Zeiträume frei wählen → kWh, €, Ø-Preis, Preisgrundlage
  (welche Tarifversionen galten), Witterung (Gradtage aus der Außentemperatur) – Grundlage für vergleichende Analysen.

---

## 8. Umsetzung in Schritten

| Schritt | Inhalt | Aufwand |
|---|---|---|
| 1 | Tarif-Ablage (SQLite + Journal), Bereich „Einstellungen/Tarife“, Anmeldung für Einstellungen | 0,5–1 Tag |
| 2 | Kostenrechner im Normalizer (Haushalt aus Zählerdifferenz, WP aus EEBUS-Schätzung), Stunden-/Tagessummen nach `ems_agg` | 1 Tag |
| 3 | Kostenanzeige in „Regelung WP“ und „Strom“ | 0,5 Tag |
| 4 | Rückwirkende Erstberechnung ab Aufzeichnungsbeginn (21.10.2026) mit den erfassten Preisen | 0,5 Tag |
| 5 | WP-Zähler einbinden, Messgrenze festlegen, „geschätzt“ ablösen | nach Einbau |
| 6 | Wasserzähler, Periodenvergleich | später |

---

## 9. Von dir benötigt

1. **Aktuelle Preise** (laut Vertrag/Rechnung): Haushaltsstrom und WP-Strom – jeweils Arbeitspreis brutto ct/kWh,
   Grundpreis €/Monat, gültig ab. Falls bekannt auch die Vorjahrespreise (dann sind Vergleiche mit 2025 vorbereitet,
   sobald Verbrauchsdaten dazu vorliegen).
2. **WP-Tarif:** eigener Zählpunkt mit eigenem Tarif? § 14a-Modul (pauschale Netzentgeltreduzierung oder reduzierter
   Arbeitspreis)?
3. **Wasser:** Arbeitspreis ct/m³, Grundpreis, Abwasser ct/m³ (falls über den Wasserverbrauch abgerechnet).
4. **Anmeldung für Einstellungen:** einverstanden?

---

## 10. Angaben des Nutzers (22.10.2026)

| Zählpunkt | Arbeitspreis | Grundpreis | gültig ab | Bemerkung |
|---|---|---|---|---|
| Haushaltsstrom | **33 ct/kWh** | offen | offen | |
| WP-Strom | **noch unbekannt** | offen | offen | Vorschlag: vorläufig 33 ct/kWh, als „vorläufig“ markiert; bei Bekanntwerden Korrekturversion + Neuberechnung |
| Wasser | **32 ct/m³** (Angabe so übernommen – bitte bestätigen, üblich sind 2–4 €/m³) | offen | offen | Zähler noch nicht eingebunden |

**Anmeldung für „Einstellungen“:** vom Nutzer abgelehnt (22.10.2026) – Einstellungen ohne Login, wie der übrige Leitstand; Schutz nur über Herkunftsprüfung (JSON, gleiche Origin) und Änderungsjournal.
