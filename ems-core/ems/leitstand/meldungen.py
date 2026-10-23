"""Leitstand: Meldungsverwaltung (Status offen/in Arbeit/erledigt, Wichtigkeit, Notiz, Änderungsjournal).

Meldungen entstehen automatisch aus den Qualitätsmeldungen des Normalizers und den Verbindungsabbrüchen der Adapter.
Gleiche Meldungen (Quelle + Art + Text) werden zusammengefasst; die Bewertung durch den Nutzer liegt in SQLite.
Eine als erledigt markierte Meldung, die danach erneut auftritt, gilt automatisch wieder als offen.
Kein Gerätezugriff: Hier werden nur Verwaltungsdaten des Leitstands geschrieben.
"""
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

STATUS = ("offen", "in_arbeit", "erledigt")
WICHTIGKEIT = ("kritisch", "unkritisch")
KRITISCH_ARTEN = {"verbindung", "schema_error", "counter_decrease", "counter_jump", "out_of_range", "value_state",
                  "clock_skew", "invalid_json"}

VERBINDUNG_TXT = {"eebus": "EEBUS", "controme": "Controme", "wattwaechter": "WattWächter", "normalizer": "Normalizer"}

# Klartext je Art (Detailfenster)
ERKLAERUNG = {
    "verbindung": "Der Adapter hatte keine Verbindung zum Gerät. In dieser Zeit fehlen Messwerte.",
    "value_state": "Das Gerät meldet für diesen Messwert selbst einen Fehlerzustand (z. B. Fühler nicht angeschlossen "
                   "oder defekt). Der Wert wird nicht übernommen.",
    "unknown_entity": "Das Gerät liefert Daten für einen Geräteteil (Entität), der im Mapping nicht beschrieben ist. "
                      "Die Daten werden nur als Rohdaten gespeichert.",
    "unknown_function": "Das Gerät liefert eine Funktion, die das Mapping nicht auswertet. Nur Rohdaten.",
    "unknown_scope": "Eine Messgröße ist im Mapping nicht beschrieben. Nur Rohdaten.",
    "unknown_keys": "Im Telegramm stehen unbekannte Felder – möglicher Hinweis auf eine Firmware-Änderung.",
    "schema_error": "Das Telegramm hat ein unerwartetes Format und konnte nicht ausgewertet werden.",
    "no_mapping": "Für diesen Datenstrom gibt es kein Mapping.",
    "counter_decrease": "Ein Zählerstand ist gesunken – Zählerwechsel oder Fehllesung. Der Wert wurde verworfen.",
    "counter_jump": "Ein Zählerstand ist unplausibel stark gesprungen. Der Wert wurde verworfen.",
    "out_of_range": "Ein Messwert lag außerhalb des physikalisch möglichen Bereichs und wurde verworfen.",
    "clock_skew": "Die Uhr des Geräts weicht deutlich ab; Zeitstempel könnten falsch sein.",
    "invalid_value": "Ein Feld enthielt keinen Zahlenwert.",
    "invalid_json": "Eine Nachricht war kein gültiges JSON.",
}


def key_of(source: str, kind: str, text: str) -> str:
    return hashlib.sha1(f"{source}|{kind}|{text}".encode()).hexdigest()[:16]


def standard_wichtigkeit(kind: str) -> str:
    return "kritisch" if kind in KRITISCH_ARTEN else "unkritisch"


def gruppieren(quality: list[dict], ausfaelle: list[dict]) -> dict[str, dict]:
    """quality: {time, source, kind, detail}; ausfaelle: {time, source, status, reason} (nicht-online-Meldungen)."""
    groups = {}
    events = [(q["time"], q["source"], q["kind"], q["detail"]) for q in quality]
    events += [(a["time"], a["source"], "verbindung",
                f"{VERBINDUNG_TXT.get(a['source'], a['source'])}-Verbindung getrennt"
                + (f" – {a['reason']}" if a.get("reason") else "")) for a in ausfaelle]
    for t, source, kind, text in events:
        k = key_of(source, kind, text)
        g = groups.setdefault(k, {"key": k, "quelle": source, "art": kind, "text": text, "anzahl": 0,
                                  "erstmals": t, "zuletzt": t, "vorkommen": []})
        g["anzahl"] += 1
        g["erstmals"], g["zuletzt"] = min(g["erstmals"], t), max(g["zuletzt"], t)
        g["vorkommen"].append(t)
    return groups


def bewerten(g: dict, ann: dict | None) -> dict:
    """Gruppe + gespeicherte Bewertung -> Anzeigezeile (inkl. Wiederauftreten nach "erledigt")."""
    ann = ann or {}
    status = ann.get("status") or "offen"
    wieder = False
    if status == "erledigt" and ann.get("status_seit") and g["zuletzt"] > ann["status_seit"]:
        status, wieder = "offen", True
    return {"key": g["key"], "quelle": g["quelle"], "art": g["art"], "text": g["text"], "anzahl": g["anzahl"],
            "erstmals": g["erstmals"].isoformat(), "zuletzt": g["zuletzt"].isoformat(),
            "status": status, "wieder_aufgetreten": wieder,
            "wichtigkeit": ann.get("wichtigkeit") or standard_wichtigkeit(g["art"]),
            "wichtigkeit_geaendert": bool(ann.get("wichtigkeit")),
            "notiz": ann.get("notiz") or ""}


SORT_STATUS = {"offen": 0, "in_arbeit": 1, "erledigt": 2}


def sortieren(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (SORT_STATUS[r["status"]], r["wichtigkeit"] != "kritisch",
                                       -datetime.fromisoformat(r["zuletzt"]).timestamp()))


class Ablage:
    """SQLite: Bewertung je Meldung + Journal aller Änderungen."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meldung (key TEXT PRIMARY KEY, status TEXT, status_seit TEXT,
                    wichtigkeit TEXT, notiz TEXT, text TEXT, quelle TEXT);
                CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, zeit TEXT,
                    feld TEXT, alt TEXT, neu TEXT, von TEXT);
            """)
            self.db.commit()

    def alle(self) -> dict[str, dict]:
        with self.lock:
            rows = self.db.execute("SELECT key, status, status_seit, wichtigkeit, notiz FROM meldung").fetchall()
        return {k: {"status": s, "status_seit": datetime.fromisoformat(ss) if ss else None, "wichtigkeit": w,
                    "notiz": n} for k, s, ss, w, n in rows}

    def journal(self, key: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT zeit, feld, alt, neu, von FROM journal WHERE key=? ORDER BY id DESC LIMIT 50",
                                   (key,)).fetchall()
        return [{"zeit": z, "feld": f, "alt": a, "neu": n, "von": v} for z, f, a, n, v in rows]

    def aendern(self, key: str, aenderung: dict, meta: dict, von: str) -> dict:
        """aenderung: status/wichtigkeit/notiz (geprüft). meta: text, quelle (zur Lesbarkeit der Datenbank)."""
        aenderung = {k: aenderung[k] for k in ("status", "wichtigkeit", "notiz") if k in aenderung}
        if not aenderung:
            raise ValueError("keine Änderung")
        if "status" in aenderung and aenderung["status"] not in STATUS:
            raise ValueError("Status unbekannt")
        if "wichtigkeit" in aenderung and aenderung["wichtigkeit"] not in WICHTIGKEIT:
            raise ValueError("Wichtigkeit unbekannt")
        if "notiz" in aenderung:
            aenderung["notiz"] = str(aenderung["notiz"])[:2000]
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            row = self.db.execute("SELECT status, wichtigkeit, notiz FROM meldung WHERE key=?", (key,)).fetchone()
            alt = dict(zip(("status", "wichtigkeit", "notiz"), row)) if row else {}
            if not row:
                self.db.execute("INSERT INTO meldung (key, text, quelle) VALUES (?, ?, ?)",
                                (key, meta.get("text", ""), meta.get("quelle", "")))
            for feld, neu in aenderung.items():
                if alt.get(feld) == neu and not (feld == "status" and neu == "erledigt"):
                    continue
                # "erledigt" erneut bestätigen (nach Wiederauftreten): Zeitpunkt neu setzen, sonst bliebe die
                # Meldung angezeigt "offen", obwohl gespeichert schon "erledigt" steht (Fehler bis 23.10.2026)
                if feld == "status":
                    self.db.execute("UPDATE meldung SET status=?, status_seit=? WHERE key=?", (neu, now, key))
                else:
                    self.db.execute(f"UPDATE meldung SET {feld}=? WHERE key=?", (neu, key))
                self.db.execute("INSERT INTO journal (key, zeit, feld, alt, neu, von) VALUES (?, ?, ?, ?, ?, ?)",
                                (key, now, feld, alt.get(feld), neu, von))
            self.db.commit()
        return self.alle().get(key, {})
