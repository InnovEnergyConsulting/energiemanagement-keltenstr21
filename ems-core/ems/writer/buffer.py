"""Dauerhafter, begrenzter Puffer für den Fall, dass InfluxDB nicht erreichbar ist (Anforderungen Kap. 11).

Gespeichert wird fertiges Line Protocol je Bucket. Ist die Grenze erreicht, werden die
ältesten Einträge verworfen und gezählt – der Verlust wird gemeldet, nicht verschwiegen.
"""
import sqlite3
import threading


class Buffer:
    def __init__(self, path: str, max_rows: int):
        self.max_rows = max_rows
        self.dropped = 0
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS lines (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "bucket TEXT NOT NULL, line TEXT NOT NULL)")
        self._db.commit()

    def add(self, items: list[tuple[str, str]]):
        with self._lock:
            self._db.executemany("INSERT INTO lines (bucket, line) VALUES (?, ?)", items)
            over = self.count_locked() - self.max_rows
            if over > 0:
                self._db.execute("DELETE FROM lines WHERE id IN (SELECT id FROM lines ORDER BY id LIMIT ?)", (over,))
                self.dropped += over
            self._db.commit()

    def count_locked(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]

    def count(self) -> int:
        with self._lock:
            return self.count_locked()

    def peek(self, limit: int) -> list[tuple[int, str, str]]:
        with self._lock:
            return self._db.execute("SELECT id, bucket, line FROM lines ORDER BY id LIMIT ?", (limit,)).fetchall()

    def delete_upto(self, last_id: int):
        with self._lock:
            self._db.execute("DELETE FROM lines WHERE id <= ?", (last_id,))
            self._db.commit()
