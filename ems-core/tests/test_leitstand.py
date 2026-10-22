"""Leitstand: Statuslogik und Reiter-Aufbereitung (ohne Datenbank)."""
from datetime import datetime, timedelta, timezone

from ems.leitstand import views

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)


def rec(minutes_ago, value, **tags):
    return {"time": NOW - timedelta(minutes=minutes_ago), "value": value, **tags}


def room(asset, name, floor, ist=None, soll=20.0, age=2):
    out = [rec(1, soll, source="controme", medium="room_climate", asset_id=asset, name=name, floor=floor,
               quantity="temperature_setpoint", role="setpoint_effective")]
    if ist is not None:
        out.append(rec(age, ist, source="controme", medium="room_climate", asset_id=asset, name=name, floor=floor,
                       quantity="temperature", role="room_actual"))
    return out


LATEST = (room("r1", "Küche", "Erdgeschoss", 20.3)
          + room("r2", "Flur", "Erdgeschoss", 18.0, age=7 * 60)
          + room("r3", "Bad OG", "Obergeschoß")
          + [rec(1, 23.0, source="controme", role="return_flow", asset_id="s1", name="Rücklauf Küche",
                 room_asset_id="r1", quantity="temperature", medium="heat"),
             rec(1, 22.0, source="controme", role="return_flow", asset_id="s2", name="Rücklauf Flur",
                 room_asset_id="r2", quantity="temperature", medium="heat"),
             rec(3, 12.0, source="eebus", asset_id="vaillant_wp", quantity="active_power"),
             rec(3, 54.0, source="eebus", asset_id="vaillant_warmwasser", quantity="temperature"),
             rec(3, 55.0, source="eebus", asset_id="vaillant_warmwasser", quantity="temperature_setpoint")])
STATES = {"r1": {"scene": "FB - EG - Tag", "mode": "Heating"},
          "vaillant_warmwasser": {"dhw_mode": "auto", "dhw_overrun_active": False},
          "vaillant_zone_1": {"heating_mode": "off"}, "vaillant_wp": {"lpc_limit_active": False}}
AVAIL = {"eebus": {"status": "online", "since": NOW - timedelta(hours=1)},
         "controme": {"status": "online", "since": NOW - timedelta(hours=2)},
         "normalizer": {"status": "online", "since": NOW - timedelta(hours=2)}}
QUALITY = [{"time": NOW - timedelta(hours=2), "source": "eebus", "kind": "value_state",
            "detail": "Außentemperatur Vaillant: outsideAirTemperature meldet error"}]


def test_fb_rooms_sorted_kpis_and_stale_sensor():
    fb = views.fb_view(NOW, LATEST, STATES)
    assert [r["name"] for r in fb["raeume"]] == ["Flur", "Küche", "Bad OG"]       # EG vor OG, mit Fühler zuerst, sonst A–Z
    flur, kueche, bad = fb["raeume"]
    assert kueche["abweichung"] == 0.3 and kueche["stufe"] == "ok" and kueche["szene"] == "FB - EG - Tag"
    assert flur["stufe"] == "hinweis"                                              # 7 h ohne Fühlerwert
    assert bad["sensor"] is False and bad["ist"] is None and bad["abweichung"] is None  # fehlend bleibt fehlend
    k = fb["kennzahlen"]
    assert (k["im_soll"], k["mit_fuehler"], k["raeume"]) == (1, 2, 3)
    assert k["groesste_abweichung"]["raum"] == "Flur" and k["groesste_abweichung"]["wert"] == -2.0
    assert k["ruecklauf_mittel"] == 22.5 and k["vorlauf"] is None
    assert fb["ruecklauf"][0]["raum"] in ("Küche", "Flur")
    assert len(fb["hinweise"]) == 1


def test_wp_outdoor_error_without_value_is_flagged():
    wp = views.wp_view(NOW, LATEST, STATES, AVAIL, QUALITY)
    assert wp["leistung"]["wert"] == 12.0
    assert wp["warmwasser"]["betriebsart"] == "Automatik" and wp["zone"]["heizbetrieb"] == "Aus"
    assert wp["aussen"]["stoerung"] is True and wp["aussen"]["wert"] is None
    assert any("Außentemperaturfühler" in h for h in wp["hinweise"])
    assert wp["energie"] is None


def test_wp_outdoor_value_clears_error():
    latest = LATEST + [rec(4, 8.5, source="eebus", asset_id="vaillant_aussenfuehler", quantity="temperature")]
    wp = views.wp_view(NOW, latest, STATES, AVAIL, QUALITY)
    assert wp["aussen"]["stoerung"] is False and wp["aussen"]["wert"]["wert"] == 8.5
    assert wp["hinweise"] == []


def test_header_levels():
    fb = views.fb_view(NOW, LATEST, STATES)
    wp = views.wp_view(NOW, LATEST, STATES, AVAIL, QUALITY)
    raw = {("controme", "temps"): {"time": NOW - timedelta(seconds=40), "http_status": 200}}
    backups = {"usb": {"time": NOW - timedelta(hours=5), "ok": 1, "detail": "ok"},
               "restore_test": {"time": NOW - timedelta(days=2), "ok": 1}}
    items = {i["id"]: i for i in views.header(NOW, LATEST, AVAIL, raw, backups, fb, wp, 9)}
    assert items["ems"]["stufe"] == "ok"
    assert items["fb"]["stufe"] == "hinweis"          # Flur-Fühler veraltet
    assert items["wp"]["stufe"] == "hinweis"          # Außenfühler
    assert items["shelly"]["stufe"] == "geplant" and items["shelly"]["text"] == "9 erkannt"
    assert items["backup"]["stufe"] == "ok"
    assert views.overall(list(items.values())) == "hinweis"


def test_header_alarms_when_sources_fail():
    fb = views.fb_view(NOW, LATEST, STATES)
    avail = {**AVAIL, "eebus": {"status": "offline", "since": NOW}}
    wp = views.wp_view(NOW, LATEST, STATES, avail, [])
    raw = {("controme", "temps"): {"time": NOW - timedelta(minutes=20), "http_status": 200}}
    backups = {"usb": {"time": NOW - timedelta(days=3), "ok": 1}}
    items = {i["id"]: i for i in views.header(NOW, LATEST, avail, raw, backups, fb, wp, 0)}
    assert items["fb"]["stufe"] == "stoerung"
    assert items["wp"]["stufe"] == "stoerung"
    assert items["backup"]["stufe"] == "stoerung"
    assert views.overall(list(items.values())) == "stoerung"


def test_raum_filter_rejects_injection_and_spans_complete():
    import pytest
    from ems.leitstand.queries import SPANNEN, raum_filter, stufig
    assert 'r.asset_id == "controme_raum_23"' in raum_filter("controme_raum_23")
    for bad in ('x" or true or "', "", "Raum 1", "a" * 65):
        with pytest.raises(ValueError):
            raum_filter(bad)
    assert list(SPANNEN) == ["30m", "60m", "180m", "6h", "12h", "24h", "7d", "30d"]
    assert stufig("controme", "room_climate", "room_actual") and stufig("eebus", "electricity", "consumption")
    assert not stufig("controme", "heat", "return_flow")     # Rücklauf kommt jede Minute -> Lücken zeigen


def test_strom_states_and_periods():
    av_on = {"wattwaechter": {"status": "online", "since": NOW - timedelta(hours=1)}}
    # online, aber keine Telegramme -> Hinweis "Lesekopf prüfen"
    st = views.strom_view(NOW, [], av_on, {}, {"heute": None}, None)
    assert st["stufe"] == "hinweis" and "Lesekopf" in st["hinweise"][0]
    # offline -> Störung
    st = views.strom_view(NOW, [], {"wattwaechter": {"status": "offline", "since": NOW}}, {}, {}, None)
    assert st["stufe"] == "stoerung"
    # Daten da -> ok, Verbrauch heute als Zählerdifferenz
    latest = [rec(1, 18240.0, source="wattwaechter", asset_id="hausanschluss", quantity="energy_import_total")]
    raw = {("wattwaechter", "sensor"): {"time": NOW - timedelta(seconds=10), "http_status": 0}}
    st = views.strom_view(NOW, latest, av_on, raw, {"heute": {"time": NOW - timedelta(hours=8), "value": 18234.0}}, None)
    assert st["stufe"] == "ok" and st["perioden"]["heute"]["kwh"] == 6.0 and st["leistung"] is None
    fb = views.fb_view(NOW, LATEST, STATES)
    wp = views.wp_view(NOW, LATEST, STATES, AVAIL, [])
    items = {i["id"]: i for i in views.header(NOW, LATEST, AVAIL, raw, {}, fb, wp, 9, st)}
    assert items["strom"]["stufe"] == "ok" and items["strom"]["text"] == "18.240 kWh"


def test_power_from_kwh_steps():
    m = lambda minutes: NOW - timedelta(minutes=minutes)
    # Start 100 kWh, Sprung auf 101 nach 20 min, auf 102 nach weiteren 40 min (unveränderte Stände alle 5 min)
    pts = [(m(120 - 5 * i), 100.0) for i in range(5)] + [(m(100), 101.0), (m(95), 101.0), (m(90), 101.0)] \
        + [(m(85 - 5 * i), 101.0) for i in range(5)] + [(m(60), 102.0), (m(55), 102.0)]
    est = views.power_from_steps(sorted(pts), NOW)
    assert len(est["abschnitte"]) == 1                     # erster Abschnitt (vor dem 1. Sprung) zählt nicht
    a = est["abschnitte"][0]
    assert a[2] == 1500 and a[3] is True                   # 1 kWh in 40 min = 1,5 kW
    assert est["letzter"]["minuten"] == 40
    assert est["obergrenze_w"] == 1000                     # seit 60 min kein Sprung -> höchstens 1 kW


def test_power_estimate_marks_gap_unsure():
    m = lambda minutes: NOW - timedelta(minutes=minutes)
    pts = [(m(200), 100.0), (m(190), 101.0), (m(185), 101.0), (m(100), 101.0), (m(90), 102.0)]   # 85 min Ausfall
    est = views.power_from_steps(pts, NOW)
    assert est["abschnitte"][0][3] is False


def test_connection_building_up_is_only_a_hint():
    wp2 = views.wp_view(NOW, LATEST, STATES, {**AVAIL, "eebus": {"status": "verbindet", "since": NOW}}, [])
    raw = {("controme", "temps"): {"time": NOW, "http_status": 200}}
    items = {i["id"]: i for i in views.header(NOW, LATEST, AVAIL, raw, {}, views.fb_view(NOW, LATEST, STATES), wp2, 0)}
    assert items["wp"]["stufe"] == "hinweis"      # Verbindungsaufbau ist keine Störung


def test_wp_energy_split_and_gaps():
    m = lambda minutes: NOW - timedelta(minutes=minutes)
    # Heizen mit 500 W, Warmwasserladung 2 kW von min 60 bis 30; Temperatur steigt verzögert ab min 50 um 6 K
    power = [(m(120 - 5 * i), 2000.0 if 30 <= 120 - 5 * i <= 60 else 500.0) for i in range(25)]
    temps = [(m(120), 45.0), (m(55), 45.0), (m(50), 47.0), (m(40), 49.0), (m(30), 51.0), (m(10), 50.5)]
    (start, end), = views.dhw_periods(temps, power)
    assert start == m(60) and end == m(25)                  # Grenzen aus der Leistung, nicht aus der Temperatur
    e = views.wp_energy(power, temps, m(120), NOW)
    assert e["ladungen"] == 1 and abs(e["warmwasser"] - 1.104) < 0.01 and abs(e["gesamt"] - 1.875) < 0.01
    # Lücke 60 min (VR 940 aus) wird nicht überbrückt
    gap = [p for p in power if not (m(90) < p[0] < m(30))]
    e2 = views.wp_energy(gap, [], m(120), NOW)
    assert e2["abdeckung_h"] == 1.0 and e2["warmwasser"] == 0.0


def test_dhw_periods_ignore_small_fluctuation():
    m = lambda minutes: NOW - timedelta(minutes=minutes)
    temps = [(m(60), 50.0), (m(50), 51.0), (m(40), 50.5), (m(30), 51.5)]       # nur 1,5 K -> keine Ladung
    assert views.dhw_periods(temps) == []


def test_meldungen_status_reopen_and_storage(tmp_path):
    from ems.leitstand import meldungen as ml
    q = [{"time": NOW - timedelta(hours=5), "source": "eebus", "kind": "unknown_entity", "detail": "Entität 9 nicht im Mapping"},
         {"time": NOW - timedelta(hours=1), "source": "eebus", "kind": "unknown_entity", "detail": "Entität 9 nicht im Mapping"},
         {"time": NOW - timedelta(hours=2), "source": "eebus", "kind": "value_state", "detail": "Außen: error"}]
    aus = [{"time": NOW - timedelta(hours=3), "source": "eebus", "status": "offline", "reason": "VR 940 nicht erreichbar"}]
    g = ml.gruppieren(q, aus)
    assert len(g) == 3
    rows = ml.sortieren([ml.bewerten(x, None) for x in g.values()])
    assert [r["wichtigkeit"] for r in rows] == ["kritisch", "kritisch", "unkritisch"]     # kritisch zuerst
    assert all(r["status"] == "offen" for r in rows)
    ab = ml.Ablage(tmp_path / "m.sqlite")
    key = next(r["key"] for r in rows if r["art"] == "unknown_entity")
    ab.aendern(key, {"status": "erledigt", "wichtigkeit": "kritisch", "notiz": "geprüft", "boese": "x"}, {}, "test")
    ann = ab.alle()[key]
    assert ann["status"] == "erledigt" and ann["wichtigkeit"] == "kritisch" and ann["notiz"] == "geprüft"
    assert {j["feld"] for j in ab.journal(key)} == {"status", "wichtigkeit", "notiz"}
    # danach erneut aufgetreten -> wieder offen
    g[key]["zuletzt"] = datetime.now(timezone.utc) + timedelta(minutes=1)
    r = ml.bewerten(g[key], ab.alle()[key])
    assert r["status"] == "offen" and r["wieder_aufgetreten"]
    import pytest
    with pytest.raises(ValueError):
        ab.aendern(key, {"status": "gelöscht"}, {}, "test")


def test_fb_ventil_column():
    latest = LATEST + [rec(1, 100.0, source="controme", medium="room_climate", asset_id="r1", name="Küche",
                           floor="Erdgeschoss", quantity="valve_position", role="valve_position")]
    fb = views.fb_view(NOW, latest, STATES, {"r1": 37.4})
    by = {r["asset_id"]: r for r in fb["raeume"]}
    assert by["r1"]["ventil"] == 100.0 and by["r1"]["ventil_60min"] == 37 and by["r1"]["ventil_vorhanden"]
    assert by["r2"]["ventil"] is None and not by["r2"]["ventil_vorhanden"]       # kein Ausgang -> nicht 0


def test_rooms_without_valve_are_measurement_only():
    latest = LATEST + [rec(1, 0.0, source="controme", medium="room_climate", asset_id="r1", name="Küche",
                           floor="Erdgeschoss", quantity="valve_position", role="valve_position")]
    fb = views.fb_view(NOW, latest, STATES, {"r1": 0.0})
    by = {r["asset_id"]: r for r in fb["raeume"]}
    assert by["r2"]["nur_messung"] and not by["r1"]["nur_messung"]          # Flur ohne Ventil = nur Messung
    k = fb["kennzahlen"]
    assert k["mit_fuehler"] == 1 and k["groesste_abweichung"]["raum"] == "Küche" and k["nur_messung"] == 1
