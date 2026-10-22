"""Preishistorie (Tarife) und Kostenberechnung – Konzept docs/Kosten_Tarife_Konzept.md.

Grundsätze:
- Preise werden nie überschrieben: neue Preise = neue Version mit "gültig ab"; eine Korrektur ist eine neue Version
  mit demselben "gültig ab", die alte wird als "ersetzt" markiert (bleibt für die Nachvollziehbarkeit erhalten).
- Kosten werden beim Eintreffen mit dem Preis zum MESSZEITPUNKT berechnet und mit Preis + Version gespeichert.
- Ein Intervall über einen Preiswechsel wird anteilig auf beide Versionen aufgeteilt.

Die Ablage ist eine SQLite-Datei: der Leitstand schreibt (Einstellungen), der Normalizer liest (Kostenrechner).
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

ZAEHLPUNKTE = {
    "haushalt": {"name": "Haushaltsstrom", "einheit": "kWh",
                 "messung": "Hauptzähler (WattWächter) – enthält bis zum eigenen WP-Zähler auch die Wärmepumpe"},
    "waermepumpe": {"name": "Wärmepumpenstrom", "einheit": "kWh", "messung": "bis zum WP-Zähler: aus EEBUS-Leistung berechnet"},
    "einspeisung": {"name": "Einspeisung (Vergütung)", "einheit": "kWh", "messung": "Hauptzähler 2.8.0 (später PV)"},
    "wasser": {"name": "Wasser", "einheit": "m³", "messung": "Wasserzähler (noch nicht eingebunden)"},
}
FELDER = ("arbeitspreis_ct", "grundpreis_eur_monat", "mwst_prozent", "anbieter", "notiz", "vorlaeufig")


def iso(dt: datetime) -> str:
    # Millisekunden: zwei Messwerte in derselben Sekunde dürfen nicht denselben Zeitstempel bekommen, sonst
    # überschreibt die zweite Kostenzeile in InfluxDB die erste (gefunden 22.10.2026, EEBUS liefert teils doppelt)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def iso_s(dt: datetime) -> str:
    """Sekundengenau – für "gültig ab" und Erfassungszeit der Preise (einheitliches Format in der Ablage)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Tarife:
    def __init__(self, path: Path, schreibend: bool = False):
        self.path = Path(path)
        self.lock = threading.Lock()
        if schreibend:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS tarif (id INTEGER PRIMARY KEY AUTOINCREMENT, zaehlpunkt TEXT NOT NULL,
                    gueltig_ab TEXT NOT NULL, arbeitspreis_ct REAL NOT NULL, grundpreis_eur_monat REAL NOT NULL DEFAULT 0,
                    mwst_prozent REAL, anbieter TEXT, notiz TEXT, vorlaeufig INTEGER NOT NULL DEFAULT 0,
                    erfasst_am TEXT NOT NULL, erfasst_von TEXT, ersetzt_durch INTEGER);
                CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, aktion TEXT,
                    tarif_id INTEGER, daten TEXT, von TEXT);
            """)
            self.db.commit()
        else:
            self.db = None                      # Leser öffnet je Abfrage (Datei kann neu entstehen/wechseln)

    def _conn(self):
        if self.db is not None:
            return self.db
        if not self.path.exists():
            return None
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)

    def _rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            c = self._conn()
            if c is None:
                return []
            try:
                cur = c.execute(sql, args)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            except sqlite3.OperationalError:
                return []                       # Tabelle noch nicht angelegt
            finally:
                if c is not self.db:
                    c.close()

    # ------------------------------------------------------------------ lesen
    def versionen(self, zaehlpunkt: str | None = None, mit_ersetzten: bool = True) -> list[dict]:
        sql = "SELECT * FROM tarif" + (" WHERE zaehlpunkt=?" if zaehlpunkt else "")
        rows = self._rows(sql, (zaehlpunkt,) if zaehlpunkt else ())
        if not mit_ersetzten:
            rows = [r for r in rows if r["ersetzt_durch"] is None]
        return sorted(rows, key=lambda r: (r["zaehlpunkt"], r["gueltig_ab"], r["id"]))

    def gueltige(self, zaehlpunkt: str) -> list[dict]:
        """Wirksame Versionen (ohne ersetzte) mit berechnetem "gueltig_bis" (Beginn der nächsten)."""
        rows = self.versionen(zaehlpunkt, mit_ersetzten=False)
        for a, b in zip(rows, rows[1:] + [None]):
            a["gueltig_bis"] = b["gueltig_ab"] if b else None
        return rows

    def preis_zu(self, zaehlpunkt: str, t: datetime) -> dict | None:
        cands = [r for r in self.gueltige(zaehlpunkt) if datetime.fromisoformat(r["gueltig_ab"]) <= t]
        return cands[-1] if cands else None

    def journal(self, limit: int = 50) -> list[dict]:
        return self._rows("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ schreiben (nur Leitstand)
    def anlegen(self, daten: dict, von: str, ersetzt: int | None = None) -> dict:
        zp = daten.get("zaehlpunkt")
        if zp not in ZAEHLPUNKTE:
            raise ValueError("Zählpunkt unbekannt")
        try:
            ab = datetime.fromisoformat(str(daten["gueltig_ab"]))
        except (KeyError, ValueError):
            raise ValueError("„gültig ab“ fehlt oder ist kein Datum")
        if ab.tzinfo is None:
            from zoneinfo import ZoneInfo
            ab = ab.replace(tzinfo=ZoneInfo("Europe/Berlin"))
        try:
            preis = float(daten["arbeitspreis_ct"])
            grund = float(daten.get("grundpreis_eur_monat") or 0)
            mwst = None if daten.get("mwst_prozent") in (None, "") else float(daten["mwst_prozent"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("Preis ist keine Zahl")
        if not (0 <= preis <= 10000) or not (0 <= grund <= 10000) or (mwst is not None and not 0 <= mwst <= 100):
            raise ValueError("Preis außerhalb des plausiblen Bereichs")
        neu = {"zaehlpunkt": zp, "gueltig_ab": iso_s(ab), "arbeitspreis_ct": preis, "grundpreis_eur_monat": grund,
               "mwst_prozent": mwst, "anbieter": str(daten.get("anbieter") or "")[:200],
               "notiz": str(daten.get("notiz") or "")[:1000], "vorlaeufig": 1 if daten.get("vorlaeufig") else 0}
        now = iso_s(datetime.now(timezone.utc))
        with self.lock:
            if ersetzt is not None:
                alt = self.db.execute("SELECT zaehlpunkt, gueltig_ab, ersetzt_durch FROM tarif WHERE id=?", (ersetzt,)).fetchone()
                if not alt or alt[2] is not None:
                    raise ValueError("Zu korrigierende Version nicht gefunden oder bereits ersetzt")
                if alt[0] != zp:
                    raise ValueError("Korrektur muss denselben Zählpunkt betreffen")
            else:
                gleich = self.db.execute("SELECT id FROM tarif WHERE zaehlpunkt=? AND gueltig_ab=? AND ersetzt_durch IS NULL",
                                         (zp, neu["gueltig_ab"])).fetchone()
                if gleich:
                    raise ValueError("Für diesen Zeitpunkt gibt es schon einen Preis – bitte „Korrigieren“ verwenden")
            cur = self.db.execute(
                "INSERT INTO tarif (zaehlpunkt, gueltig_ab, arbeitspreis_ct, grundpreis_eur_monat, mwst_prozent, anbieter, "
                "notiz, vorlaeufig, erfasst_am, erfasst_von) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (zp, neu["gueltig_ab"], preis, grund, mwst, neu["anbieter"], neu["notiz"], neu["vorlaeufig"], now, von))
            tid = cur.lastrowid
            if ersetzt is not None:
                self.db.execute("UPDATE tarif SET ersetzt_durch=? WHERE id=?", (tid, ersetzt))
            self.db.execute("INSERT INTO journal (zeit, aktion, tarif_id, daten, von) VALUES (?,?,?,?,?)",
                            (now, "korrektur" if ersetzt else "neu", tid,
                             json.dumps({**neu, "ersetzt": ersetzt}, ensure_ascii=False), von))
            self.db.commit()
        return {**neu, "id": tid, "erfasst_am": now}


# ---------------------------------------------------------------------- Kosten
def kosten_records(tarife: Tarife, zaehlpunkt: str, menge: float, von: datetime, bis: datetime,
                   herkunft: str, berechnung_version: int = 1) -> list[dict]:
    """Menge im Intervall [von, bis] -> Kostenzeilen. Über einen Preiswechsel hinweg anteilig aufgeteilt.
    Ohne gültigen Preis: Zeile mit Menge, aber ohne Kosten (tarif_version 0) – nichts geht verloren."""
    if bis <= von:
        von = bis                                 # Zeitpunkt-Buchung (z. B. Zählerstand ohne Vorgänger-Zeit)
    grenzen = [von]
    for v in tarife.gueltige(zaehlpunkt):
        t = datetime.fromisoformat(v["gueltig_ab"])
        if von < t < bis:
            grenzen.append(t)
    grenzen.append(bis)
    gesamt = (bis - von).total_seconds()
    out = []
    for a, b in zip(grenzen, grenzen[1:]):
        anteil = 1.0 if gesamt <= 0 else (b - a).total_seconds() / gesamt
        if anteil <= 0 and gesamt > 0:
            continue
        m = menge * anteil
        v = tarife.preis_zu(zaehlpunkt, a if gesamt > 0 else b)
        rec = {"zaehlpunkt": zaehlpunkt, "einheit": ZAEHLPUNKTE[zaehlpunkt]["einheit"], "menge": round(m, 6),
               "von": iso(a), "bis": iso(b), "herkunft": herkunft, "berechnung_version": berechnung_version,
               "tarif_version": v["id"] if v else 0}
        if v:
            rec["arbeitspreis_ct"] = v["arbeitspreis_ct"]
            rec["kosten_eur"] = round(m * v["arbeitspreis_ct"] / 100, 6)
            rec["vorlaeufig"] = bool(v["vorlaeufig"])
        out.append(rec)
    return out


def grundkosten(tarife: Tarife, zaehlpunkt: str, von: datetime, bis: datetime) -> float:
    """Grundpreis im Zeitraum (Monatspreis × 12 / 365 je Tag, zeitanteilig) – aus der Preishistorie abgeleitet."""
    eur = 0.0
    for v in tarife.gueltige(zaehlpunkt):
        a = max(von, datetime.fromisoformat(v["gueltig_ab"]))
        b = min(bis, datetime.fromisoformat(v["gueltig_bis"])) if v["gueltig_bis"] else bis
        if b > a:
            eur += v["grundpreis_eur_monat"] * 12 / 365 * (b - a).total_seconds() / 86400
    return round(eur, 4)
