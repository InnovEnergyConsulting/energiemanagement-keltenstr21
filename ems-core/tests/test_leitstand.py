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
