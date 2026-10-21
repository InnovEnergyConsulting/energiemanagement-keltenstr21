"""Leitstand: aus den letzten Werten in InfluxDB die Anzeige berechnen (reine Funktionen, testbar).

Gestaltung nach docs/design/Dashboard-Entwurf.html (ISA-101): Normalbetrieb ohne Farbe, Stufen nur für
Abweichungen. Stufen: ok | hinweis | stoerung | geplant | unbekannt. Fehlende Werte bleiben None, nie 0.
"""
from datetime import datetime

OK, HINWEIS, STOERUNG, GEPLANT, UNBEKANNT = "ok", "hinweis", "stoerung", "geplant", "unbekannt"
RANK = {OK: 0, GEPLANT: 0, UNBEKANNT: 1, HINWEIS: 1, STOERUNG: 2}

# Grenzwerte (Sekunden bzw. Kelvin)
ROOM_SENSOR_HINWEIS_S = 6 * 3600       # Funkfühler senden nur bei Änderung: Pausen > 3 h gemessen (20.10.2026)
ROOM_SENSOR_STOERUNG_S = 24 * 3600
CONTROME_POLL_HINWEIS_S = 3 * 60       # Adapter fragt jede Minute ab
CONTROME_POLL_STOERUNG_S = 10 * 60
PIPELINE_HINWEIS_S = 5 * 60            # jüngster geschriebener Messwert
PIPELINE_STOERUNG_S = 15 * 60
EEBUS_VALUE_HINWEIS_S = 15 * 60        # Adapter liest alle 5 Minuten
BACKUP_HINWEIS_S = 26 * 3600
BACKUP_STOERUNG_S = 50 * 3600
RESTORE_HINWEIS_S = 35 * 86400
IM_SOLL_K = 0.5

MODE_DE = {"auto": "Automatik", "on": "Ein", "off": "Aus", "Heating": "Heizen", "Cooling": "Kühlen"}


def age_s(now: datetime, t: datetime | None) -> float | None:
    return None if t is None else max(0.0, (now - t).total_seconds())


def by_age(age: float | None, hinweis_s: float, stoerung_s: float | None = None) -> str:
    if age is None:
        return UNBEKANNT
    if stoerung_s is not None and age > stoerung_s:
        return STOERUNG
    return HINWEIS if age > hinweis_s else OK


def worst(*levels: str) -> str:
    return max(levels, key=lambda lv: RANK.get(lv, 1)) if levels else OK


def _latest(records, **match):
    """Jüngster Datensatz, auf den alle Merkmale passen."""
    hits = [r for r in records if all(r.get(k) == v for k, v in match.items())]
    return max(hits, key=lambda r: r["time"]) if hits else None


def _val(rec):
    return None if rec is None else rec["value"]


# --------------------------------------------------------------------------------- Reiter 1: Fußbodenheizung
def fb_view(now: datetime, latest: list[dict], states: dict) -> dict:
    """latest: letzte Telemetrie je Messpunkt; states: {asset_id: {feld: wert}}."""
    rooms = {}
    for r in latest:
        if r.get("source") != "controme" or r.get("medium") != "room_climate":
            continue
        room = rooms.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "name": r.get("name"),
                                                "floor": r.get("floor") or "–"})
        room[r["quantity"]] = r["value"]
        if r["quantity"] == "temperature":
            room["temp_time"] = r["time"]

    rows = []
    for room in rooms.values():
        ist, soll = room.get("temperature"), room.get("temperature_setpoint")
        age = age_s(now, room.get("temp_time"))
        st = states.get(room["asset_id"], {})
        rows.append({
            "asset_id": room["asset_id"], "name": room["name"], "floor": room["floor"],
            "ist": ist, "soll": soll, "soll_naechste": room.get("temperature_setpoint_next"),
            "abweichung": None if ist is None or soll is None else round(ist - soll, 2),
            "offset": room.get("temperature_offset"), "feuchte": room.get("relative_humidity"),
            "szene": st.get("scene"), "naechste_szene": st.get("next_scene"), "wechsel_um": st.get("next_change_at"),
            "betriebsart": MODE_DE.get(st.get("mode"), st.get("mode")),
            "sensor": ist is not None, "alter_s": age,
            "stufe": by_age(age, ROOM_SENSOR_HINWEIS_S, ROOM_SENSOR_STOERUNG_S) if ist is not None else OK,
        })
    floor_order = lambda f: (0 if f.lower().startswith("erd") else 1 if f.lower().startswith("ober") else 2, f)
    rows.sort(key=lambda x: (floor_order(x["floor"]), not x["sensor"], x["name"] or ""))

    measured = [x for x in rows if x["abweichung"] is not None and x["stufe"] != STOERUNG]
    im_soll = sum(1 for x in measured if abs(x["abweichung"]) <= IM_SOLL_K)
    groesste = max(measured, key=lambda x: abs(x["abweichung"])) if measured else None

    returns = []
    for r in latest:
        if r.get("source") == "controme" and r.get("role") == "return_flow":
            room_name = rooms.get(r.get("room_asset_id"), {}).get("name")
            returns.append({"asset_id": r["asset_id"], "name": r.get("name"), "raum": room_name,
                            "wert": r["value"], "alter_s": age_s(now, r["time"]),
                            "stufe": by_age(age_s(now, r["time"]), CONTROME_POLL_STOERUNG_S)})
    returns.sort(key=lambda x: x["name"] or "")
    fresh = [x["wert"] for x in returns if x["stufe"] == OK]

    flow = _latest(latest, source="controme", medium="heat", role="flow_actual")
    flow_soll = _latest(latest, source="controme", medium="heat", role="flow_setpoint")

    hinweise = [f"{x['name']}: Fühler meldet seit {x['alter_s'] / 3600:.0f} h nichts"
                for x in rows if x["stufe"] in (HINWEIS, STOERUNG)]
    return {
        "kennzahlen": {
            "im_soll": im_soll, "mit_fuehler": len(measured), "raeume": len(rows),
            "groesste_abweichung": None if groesste is None else
                {"raum": groesste["name"], "wert": groesste["abweichung"], "ist": groesste["ist"], "soll": groesste["soll"]},
            "ruecklauf_mittel": round(sum(fresh) / len(fresh), 1) if fresh else None,
            "ruecklauf_min": min(fresh) if fresh else None, "ruecklauf_max": max(fresh) if fresh else None,
            "ruecklauf_kreise": len(returns),
            "vorlauf": _val(flow), "vorlauf_soll": _val(flow_soll),
            "vorlauf_alter_s": age_s(now, flow["time"]) if flow else None,
        },
        "raeume": rows, "ruecklauf": returns, "hinweise": hinweise,
    }


# --------------------------------------------------------------------------------- Reiter 2: Wärmepumpe
def wp_view(now: datetime, latest: list[dict], states: dict, availability: dict, quality: list[dict]) -> dict:
    """quality: Qualitätsmeldungen der letzten Tage. Der Normalizer meldet jeden Fehler nur einmal je Laufzeit –
    eine Fühlerstörung gilt daher, solange danach kein gültiger Wert kam."""
    def tel(asset, quantity):
        rec = _latest(latest, source="eebus", asset_id=asset, quantity=quantity)
        return None if rec is None else {"wert": rec["value"], "alter_s": age_s(now, rec["time"]),
                                         "zeit": rec["time"].isoformat()}

    wp, ww, zone = states.get("vaillant_wp", {}), states.get("vaillant_warmwasser", {}), states.get("vaillant_zone_1", {})
    av = availability.get("eebus", {})
    online = av.get("status") == "online"

    outdoor = tel("vaillant_aussenfuehler", "temperature")
    outdoor_error = next((q for q in quality if q["source"] == "eebus" and q["kind"] == "value_state"
                          and "Außen" in q["detail"]), None)
    if outdoor is not None and outdoor["alter_s"] > 3600:
        outdoor = None
    aussen_stoerung = outdoor is None and outdoor_error is not None

    power = tel("vaillant_wp", "active_power")
    energy = tel("vaillant_wp", "energy_consumption_total")

    hinweise = []
    if not av:
        hinweise.append("EEBUS-Adapter hat noch keinen Zustand gemeldet")
    elif not online:
        hinweise.append(f"EEBUS-Verbindung: {av.get('status')}" + (f" ({av['reason']})" if av.get("reason") else ""))
    if aussen_stoerung:
        hinweise.append("Außentemperaturfühler meldet „error“ (nicht angeschlossen?)")
    if online and power is not None and power["alter_s"] > EEBUS_VALUE_HINWEIS_S:
        hinweise.append(f"Leistung seit {int(power['alter_s'] // 60)} min nicht aktualisiert")

    # Meldungen: gleiche Texte zusammenfassen (der Adapter liest alle 5 Minuten neu)
    grouped = {}
    for q in quality:
        if q["source"] != "eebus":
            continue
        g = grouped.setdefault(q["detail"], {"text": q["detail"], "art": q["kind"], "anzahl": 0,
                                             "erstmals": q["time"], "zuletzt": q["time"]})
        g["anzahl"] += 1
        g["erstmals"], g["zuletzt"] = min(g["erstmals"], q["time"]), max(g["zuletzt"], q["time"])
    meldungen = sorted(grouped.values(), key=lambda g: g["zuletzt"], reverse=True)
    if av.get("since"):
        meldungen.append({"text": f"EEBUS-Verbindung {av.get('status')}", "art": "verbindung", "anzahl": 1,
                          "erstmals": av["since"], "zuletzt": av["since"]})
        meldungen.sort(key=lambda g: g["zuletzt"], reverse=True)
    for g in meldungen:
        g["erstmals"], g["zuletzt"] = g["erstmals"].isoformat(), g["zuletzt"].isoformat()

    return {
        "verbindung": {"status": av.get("status"), "seit": av["since"].isoformat() if av.get("since") else None,
                       "grund": av.get("reason") or None},
        "leistung": power,
        "warmwasser": {"ist": tel("vaillant_warmwasser", "temperature"),
                       "soll": tel("vaillant_warmwasser", "temperature_setpoint"),
                       "betriebsart": MODE_DE.get(ww.get("dhw_mode"), ww.get("dhw_mode")),
                       "schnellladung": ww.get("dhw_overrun_active")},
        "zone": {"ist": tel("vaillant_zone_1", "temperature"), "soll": tel("vaillant_zone_1", "temperature_setpoint"),
                 "heizbetrieb": MODE_DE.get(zone.get("heating_mode"), zone.get("heating_mode"))},
        "aussen": {"wert": outdoor, "stoerung": aussen_stoerung},
        "lpc": {"aktiv": wp.get("lpc_limit_active"), "grenze_w": wp.get("lpc_limit_w"),
                "failsafe_dauer": wp.get("failsafeDurationMinimum"),
                "failsafe_w": wp.get("failsafeConsumptionActivePowerLimit")},
        "energie": energy,
        "hinweise": hinweise, "meldungen": meldungen[:12],
    }


# --------------------------------------------------------------------------------- Kopfzeile
def header(now: datetime, latest: list[dict], availability: dict, raw_last: dict, backups: dict,
           fb: dict, wp: dict, shelly_count: int, strom: dict | None = None) -> list[dict]:
    newest = max((r["time"] for r in latest), default=None)
    pipe_age = age_s(now, newest)
    norm = availability.get("normalizer", {})
    ems_level = by_age(pipe_age, PIPELINE_HINWEIS_S, PIPELINE_STOERUNG_S)
    if norm and norm.get("status") != "online":
        ems_level = worst(ems_level, STOERUNG)
    items = [{"id": "ems", "label": "Datenfluss", "stufe": ems_level, "alter_s": pipe_age,
              "text": "Messwerte kommen an" if ems_level == OK else "Messwerte stocken",
              "titel": "Jüngster gespeicherter Messwert; Normalizer " + (norm.get("status") or "ohne Meldung")}]

    poll = raw_last.get(("controme", "temps"))
    c_av = availability.get("controme", {})
    c_level = by_age(age_s(now, poll["time"]) if poll else None, CONTROME_POLL_HINWEIS_S, CONTROME_POLL_STOERUNG_S)
    if c_av and c_av.get("status") != "online" or (poll and poll.get("http_status") not in (None, 200)):
        c_level = STOERUNG
    room_hints = len(fb["hinweise"])
    if c_level == OK and room_hints:
        c_level = HINWEIS
    items.append({"id": "fb", "label": "Fußbodenheizung", "stufe": c_level,
                  "alter_s": age_s(now, poll["time"]) if poll else None,
                  "text": f"{room_hints} Hinweis" + ("e" if room_hints != 1 else "") if room_hints and c_level == HINWEIS
                          else None,
                  "titel": "Controme-Miniserver: letzter Abruf" + ("" if not room_hints else "; " + "; ".join(fb["hinweise"]))})

    online = wp["verbindung"]["status"] == "online"
    wp_level = STOERUNG if wp["verbindung"]["status"] not in ("online", None) else (UNBEKANNT if not online else OK)
    if online and wp["hinweise"]:
        wp_level = HINWEIS
    n = len(wp["hinweise"])
    items.append({"id": "wp", "label": "Wärmepumpe", "stufe": wp_level, "alter_s": None,
                  "text": (f"{n} Hinweis" + ("e" if n != 1 else "")) if n else "verbunden",
                  "titel": "; ".join(wp["hinweise"]) or "EEBUS-Verbindung zum VR 940 steht"})

    if strom is not None:
        p, e = strom["leistung"], strom["bezug"]
        if strom["stufe"] == OK and p is not None:
            text = ("Bezug " if p["wert"] >= 0 else "Einspeisung ") + f"{abs(p['wert']) / 1000:.1f} kW".replace(".", ",")
        elif strom["stufe"] == OK and e is not None:
            text = f"{e['wert']:,.0f} kWh".replace(",", ".")
        else:
            text = "keine Zählerdaten" if strom["stufe"] == HINWEIS else (strom["verbindung"]["status"] or "–")
        items.append({"id": "strom", "label": "Hausanschluss", "stufe": strom["stufe"], "alter_s": None, "text": text,
                      "titel": "; ".join(strom["hinweise"]) or "Hauptzähler (WattWächter) liefert Daten"})

    items.append({"id": "shelly", "label": "Shelly", "stufe": GEPLANT, "alter_s": None,
                  "text": f"{shelly_count} erkannt", "titel": "Im Netz gefunden, Einbindung ab Phase 5"})

    usb, rt = backups.get("usb"), backups.get("restore_test")
    usb_age = age_s(now, usb["time"]) if usb else None
    b_level = by_age(usb_age, BACKUP_HINWEIS_S, BACKUP_STOERUNG_S)
    if usb and not usb.get("ok"):
        b_level = STOERUNG
    if rt and (not rt.get("ok") or age_s(now, rt["time"]) > RESTORE_HINWEIS_S):
        b_level = worst(b_level, HINWEIS)
    items.append({"id": "backup", "label": "Sicherung", "stufe": b_level, "alter_s": usb_age,
                  "zeit": usb["time"].isoformat() if usb else None,
                  "titel": (f"USB-SSD: {usb.get('detail')}" if usb else "Noch keine Sicherung gemeldet")
                           + (f"; Wiederherstellungstest {rt['time']:%d.%m.%Y}: " + ("OK" if rt.get("ok") else "FEHLER")
                              if rt else "")})
    return items


def overall(items: list[dict]) -> str:
    return worst(*[i["stufe"] for i in items if i["stufe"] != GEPLANT])


# --------------------------------------------------------------------------------- Reiter "Strom" (Hauptzähler)
STROM_WERT_HINWEIS_S = 15 * 60         # Zählerstand kommt mind. alle 5 min (auch unverändert)
STROM_TELEGRAMM_STOERUNG_S = 5 * 60    # WattWächter sendet alle 10 s


def strom_view(now: datetime, latest: list[dict], availability: dict, raw_last: dict,
               anfang: dict, geraet: dict | None) -> dict:
    """anfang: {"heute"|"monat"|"jahr": {"time", "value"} | None} = erster Bezugszählerstand im Zeitraum."""
    def tel(quantity):
        rec = _latest(latest, source="wattwaechter", asset_id="hausanschluss", quantity=quantity)
        return None if rec is None else {"wert": rec["value"], "alter_s": age_s(now, rec["time"]), "zeit": rec["time"].isoformat()}

    e_in, e_out, power = tel("energy_import_total"), tel("energy_export_total"), tel("active_power")
    perioden = {}
    for name, first in anfang.items():
        if e_in is None or first is None:
            perioden[name] = None
            continue
        perioden[name] = {"kwh": round(e_in["wert"] - first["value"], 3), "seit": first["time"].isoformat()}
    phasen = {f"l{i}": {"leistung": tel(f"active_power_l{i}"), "strom": tel(f"current_l{i}"), "spannung": tel(f"voltage_l{i}")}
              for i in (1, 2, 3)}
    av = availability.get("wattwaechter", {})
    tele = raw_last.get(("wattwaechter", "sensor"))
    tele_age = age_s(now, tele["time"]) if tele else None

    hinweise = []
    if not av:
        stufe = UNBEKANNT
        hinweise.append("WattWächter hat sich noch nicht am EMS gemeldet")
    elif av.get("status") != "online":
        stufe = STOERUNG
        hinweise.append("WattWächter offline (WLAN/Strom?)")
    elif tele is None or tele_age > STROM_TELEGRAMM_STOERUNG_S:
        stufe = HINWEIS
        hinweise.append("Keine Zählerdaten – Lesekopf prüfen (Sitz/Ausrichtung am Zähler)")
    elif e_in is None or e_in["alter_s"] > STROM_WERT_HINWEIS_S:
        stufe = HINWEIS
        hinweise.append("Telegramme kommen, aber kein gültiger Zählerstand")
    else:
        stufe = OK
    return {"stufe": stufe, "hinweise": hinweise, "verbindung": {"status": av.get("status"),
            "seit": av["since"].isoformat() if av.get("since") else None},
            "telegramm_alter_s": tele_age, "bezug": e_in, "einspeisung": e_out, "leistung": power,
            "perioden": perioden, "phasen": phasen, "geraet": geraet or {}}
