"""Kosten/Tarife: Preishistorie, Bepreisung beim Eintreffen, Preiswechsel, Neustart, Speicherform."""
from datetime import datetime, timedelta, timezone

import pytest

from ems.kosten.rechner import KostenRechner
from ems.kosten.tarife import Tarife, grundkosten, kosten_records
from ems.writer import convert

T0 = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def tarife(tmp_path):
    t = Tarife(tmp_path / "tarife.sqlite", schreibend=True)
    t.anlegen({"zaehlpunkt": "haushalt", "gueltig_ab": "2026-10-21T00:00", "arbeitspreis_ct": 33,
               "grundpreis_eur_monat": 0}, "test")
    return t


def test_versions_correction_and_validation(tarife):
    v2 = tarife.anlegen({"zaehlpunkt": "haushalt", "gueltig_ab": "2027-01-01T00:00", "arbeitspreis_ct": 35}, "test")
    assert tarife.preis_zu("haushalt", datetime(2026, 12, 31, 22, 59, tzinfo=timezone.utc))["arbeitspreis_ct"] == 33
    assert tarife.preis_zu("haushalt", datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc))["arbeitspreis_ct"] == 35  # 0 Uhr MEZ
    with pytest.raises(ValueError):           # gleicher Zeitpunkt ohne Korrektur
        tarife.anlegen({"zaehlpunkt": "haushalt", "gueltig_ab": "2027-01-01T00:00", "arbeitspreis_ct": 36}, "test")
    k = tarife.anlegen({"zaehlpunkt": "haushalt", "gueltig_ab": "2027-01-01T00:00", "arbeitspreis_ct": 34.5}, "test",
                       ersetzt=v2["id"])
    assert tarife.preis_zu("haushalt", datetime(2027, 2, 1, tzinfo=timezone.utc))["id"] == k["id"]
    assert len(tarife.versionen("haushalt")) == 3 and len(tarife.gueltige("haushalt")) == 2   # alte bleibt erhalten
    assert [j["aktion"] for j in tarife.journal()] == ["korrektur", "neu", "neu"]
    for bad in ({"zaehlpunkt": "gas", "gueltig_ab": "2027-01-01", "arbeitspreis_ct": 1},
                {"zaehlpunkt": "wasser", "gueltig_ab": "kein Datum", "arbeitspreis_ct": 1},
                {"zaehlpunkt": "wasser", "gueltig_ab": "2027-01-01", "arbeitspreis_ct": -5}):
        with pytest.raises(ValueError):
            tarife.anlegen(bad, "test")
    leser = Tarife(tarife.path)                 # Normalizer: nur lesend
    assert leser.preis_zu("haushalt", T0)["arbeitspreis_ct"] == 33


def test_split_at_price_change(tarife):
    tarife.anlegen({"zaehlpunkt": "haushalt", "gueltig_ab": "2026-10-22T00:00", "arbeitspreis_ct": 40}, "test")
    wechsel = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)                   # 00:00 MESZ
    recs = kosten_records(tarife, "haushalt", 2.0, wechsel - timedelta(hours=1), wechsel + timedelta(hours=1), "gemessen")
    assert [(r["menge"], r["arbeitspreis_ct"], r["kosten_eur"]) for r in recs] == [(1.0, 33, 0.33), (1.0, 40, 0.4)]


def test_no_tariff_keeps_quantity(tmp_path):
    recs = kosten_records(Tarife(tmp_path / "leer.sqlite"), "wasser", 0.5, T0, T0 + timedelta(hours=1), "gemessen")
    assert recs[0]["menge"] == 0.5 and recs[0]["tarif_version"] == 0 and "kosten_eur" not in recs[0]


def test_rechner_counter_power_and_restart(tarife):
    sent = []
    r = KostenRechner(tarife, lambda topic, msg, **kw: sent.append((topic, msg, kw)))
    assert r.zaehlerstand("haushalt", 3028.0, T0) == []                             # erster Stand: nur merken
    assert r.zaehlerstand("haushalt", 3028.0, T0 + timedelta(minutes=5)) == []      # unverändert
    recs = r.zaehlerstand("haushalt", 3029.0, T0 + timedelta(minutes=30))
    assert recs[0]["menge"] == 1.0 and recs[0]["kosten_eur"] == 0.33 and recs[0]["von"] == "2026-10-21T22:00:00.000+00:00"
    assert r.zaehlerstand("haushalt", 3027.0, T0 + timedelta(minutes=40)) == []     # Rücksprung: nichts buchen
    staende = [(m, kw) for t, m, kw in sent if t == "ems/v1/kostenstand/haushalt"]
    stand = staende[-1][0]
    assert all(kw == {"retain": True} for _, kw in staende) and stand["wert"] == 3029.0
    # Neustart: neuer Rechner übernimmt den Stand aus dem Broker
    r2 = KostenRechner(tarife, lambda *a, **k: None)
    r2.seed("haushalt", stand)
    assert r2.zaehlerstand("haushalt", 3030.0, T0 + timedelta(hours=1))[0]["menge"] == 1.0
    # WP-Leistung: Trapez, Lücke > 15 min nicht überbrückt
    tarife.anlegen({"zaehlpunkt": "waermepumpe", "gueltig_ab": "2026-10-21T00:00", "arbeitspreis_ct": 33, "vorlaeufig": True}, "t")
    r.leistung("waermepumpe", 2000, T0)
    wp = r.leistung("waermepumpe", 2000, T0 + timedelta(minutes=6))
    assert wp[0]["menge"] == 0.2 and wp[0]["herkunft"] == "geschaetzt" and wp[0]["vorlaeufig"] is True
    assert r.leistung("waermepumpe", 2000, T0 + timedelta(minutes=30)) == []        # 24 min Lücke


def test_grundkosten_and_line_protocol(tarife):
    tarife.anlegen({"zaehlpunkt": "wasser", "gueltig_ab": "2026-10-21T00:00", "arbeitspreis_ct": 320,
                    "grundpreis_eur_monat": 36.5}, "t")
    assert grundkosten(tarife, "wasser", T0, T0 + timedelta(days=10)) == 12.0         # 36,5 × 12 / 365 × 10
    rec = kosten_records(tarife, "haushalt", 1.0, T0, T0 + timedelta(minutes=30), "gemessen")[0]
    bucket, line = convert.kosten(rec)
    assert bucket == "ems_detail" and line.startswith("kosten,")
    for part in ("zaehlpunkt=haushalt", "arbeitspreis_ct=33", "herkunft=gemessen", "kosten_eur=0.33", "menge=1", "dauer_s=1800"):
        assert part in line


def test_nachrechnung_counter_and_power(tarife):
    from ems.kosten.nachrechnung import berechnen
    m = lambda minutes: T0 + timedelta(minutes=minutes)
    pts = [(m(0), 3028.0), (m(10), 3028.0), (m(40), 3029.0), (m(70), 3030.0)]
    recs = berechnen(tarife, "haushalt", pts, (m(-60), 3027.0), m(-5), m(80), version=2)
    assert [r["menge"] for r in recs] == [1.0, 1.0, 1.0] and all(r["berechnung_version"] == 2 for r in recs)
    assert round(sum(r["kosten_eur"] for r in recs), 2) == 0.99
    tarife.anlegen({"zaehlpunkt": "waermepumpe", "gueltig_ab": "2026-10-21T00:00", "arbeitspreis_ct": 33}, "t")
    wp = berechnen(tarife, "waermepumpe", [(m(5), 1000.0), (m(10), 1000.0)], (m(0), 1000.0), m(0), m(10), 1)
    assert round(sum(r["menge"] for r in wp), 4) == round(1000 * 600 / 3_600_000, 4)


def test_same_second_records_keep_distinct_timestamps(tarife):
    tarife.anlegen({"zaehlpunkt": "waermepumpe", "gueltig_ab": "2026-10-21T00:00", "arbeitspreis_ct": 33}, "t")
    r = KostenRechner(tarife, lambda *a, **k: None)
    r.leistung("waermepumpe", 1000, T0)
    a = r.leistung("waermepumpe", 1000, T0 + timedelta(milliseconds=300))
    b = r.leistung("waermepumpe", 1000, T0 + timedelta(milliseconds=700))
    assert convert.kosten(a[0])[1].split()[-1] != convert.kosten(b[0])[1].split()[-1]
