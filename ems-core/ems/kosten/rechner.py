"""Kostenrechner im Normalizer: Mengen beim Eintreffen bepreisen (Preis zum Messzeitpunkt) und als
ems/v1/kosten/<zählpunkt> veröffentlichen (Writer -> InfluxDB measurement "kosten").

Mengen:
- Zählerstände (Hauptzähler Bezug/Einspeisung): Differenz zum vorigen Stand, Intervall [vorige Zeit, jetzt].
- Leistung (WP aus EEBUS, bis zum eigenen WP-Zähler): Trapez-Integral zwischen zwei Werten; Lücken > MAX_LUECKE_S
  werden nicht überbrückt (z. B. VR 940 aus) – dort entsteht bewusst keine geschätzte Menge.
Der letzte Stand je Zählpunkt liegt retained unter ems/v1/kostenstand/<zählpunkt>, damit ein Neustart des Normalizers
keine Menge verliert.
"""
from datetime import datetime

from ..common.timeutil import iso, parse_iso
from .tarife import Tarife, kosten_records

MAX_LUECKE_S = 15 * 60


class KostenRechner:
    def __init__(self, tarife: Tarife, publish):
        self.tarife = tarife
        self.publish = publish
        self.stand: dict[str, dict] = {}

    def seed(self, zaehlpunkt: str, payload: dict):
        """Gespeicherten Stand nach Neustart übernehmen (nur wenn noch keiner vorliegt)."""
        if zaehlpunkt not in self.stand and isinstance(payload, dict) and "zeit" in payload:
            self.stand[zaehlpunkt] = payload

    def _merken(self, zp: str, stand: dict):
        self.stand[zp] = stand
        self.publish(f"ems/v1/kostenstand/{zp}", stand, retain=True)

    def _buchen(self, zp: str, menge: float, von: datetime, bis: datetime, herkunft: str) -> list[dict]:
        recs = kosten_records(self.tarife, zp, menge, von, bis, herkunft)
        for r in recs:
            self.publish(f"ems/v1/kosten/{zp}", r)
        return recs

    def zaehlerstand(self, zp: str, wert: float, zeit: datetime, herkunft: str = "gemessen") -> list[dict]:
        alt = self.stand.get(zp)
        recs = []
        if alt and "wert" in alt:
            vorher, t0 = float(alt["wert"]), parse_iso(alt["zeit"])
            if wert < vorher or zeit <= t0:
                return []                       # Rücksprung/alte Nachricht: Normalizer meldet Qualität, hier nichts buchen
            if wert > vorher:
                recs = self._buchen(zp, wert - vorher, t0, zeit, herkunft)
        # Stand nur bei Änderung merken: das nächste Intervall beginnt beim letzten Zählerwechsel (bei ganzen kWh
        # verteilt sich die Menge so richtig über die Zeit seit dem letzten Sprung)
        if not alt or "wert" not in alt or wert != float(alt["wert"]):
            self._merken(zp, {"wert": wert, "zeit": iso(zeit)})
        return recs

    def leistung(self, zp: str, watt: float, zeit: datetime, herkunft: str = "geschaetzt") -> list[dict]:
        alt = self.stand.get(zp)
        recs = []
        if alt and "watt" in alt:
            t0 = parse_iso(alt["zeit"])
            dt = (zeit - t0).total_seconds()
            if dt <= 0:
                return []
            if dt <= MAX_LUECKE_S:
                kwh = (float(alt["watt"]) + watt) / 2 * dt / 3_600_000
                if kwh > 0:
                    recs = self._buchen(zp, kwh, t0, zeit, herkunft)
        self._merken(zp, {"watt": watt, "zeit": iso(zeit)})
        return recs
