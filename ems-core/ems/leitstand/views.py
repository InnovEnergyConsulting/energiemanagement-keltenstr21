"""Leitstand: aus den letzten Werten in InfluxDB die Anzeige berechnen (reine Funktionen, testbar).

Gestaltung nach docs/design/Dashboard-Entwurf.html (ISA-101): Normalbetrieb ohne Farbe, Stufen nur für
Abweichungen. Stufen: ok | hinweis | stoerung | geplant | unbekannt. Fehlende Werte bleiben None, nie 0.
"""
from datetime import datetime, timedelta

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

STATUS_DE = {"online": "verbunden", "offline": "getrennt", "verbindet": "Verbindungsaufbau", "pairing": "Kopplung läuft"}
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
VENTIL_VERALTET_S = 10 * 60          # Ausgänge werden jede Minute abgefragt


def fb_view(now: datetime, latest: list[dict], states: dict, ventil_mittel: dict | None = None) -> dict:
    """latest: letzte Telemetrie je Messpunkt; states: {asset_id: {feld: wert}};
    ventil_mittel: Öffnungsanteil je Raum der letzten 60 min in % (thermische Stellantriebe: Anteil der Zeit offen)."""
    rooms = {}
    for r in latest:
        if r.get("source") != "controme" or r.get("medium") != "room_climate":
            continue
        room = rooms.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "name": r.get("name"),
                                                "floor": r.get("floor") or "–"})
        room[r["quantity"]] = r["value"]
        if r["quantity"] == "temperature":
            room["temp_time"] = r["time"]
        if r["quantity"] == "valve_position":
            room["valve_time"] = r["time"]

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
            "ventil": room.get("valve_position") if room.get("valve_time") and age_s(now, room["valve_time"]) <= VENTIL_VERALTET_S else None,
            "ventil_60min": None if (ventil_mittel or {}).get(room["asset_id"]) is None else round(ventil_mittel[room["asset_id"]]),
            "ventil_vorhanden": "valve_time" in room,
            "stufe": by_age(age, ROOM_SENSOR_HINWEIS_S, ROOM_SENSOR_STOERUNG_S) if ist is not None else OK,
        })
    floor_order = lambda f: (0 if f.lower().startswith("erd") else 1 if f.lower().startswith("ober") else 2, f)
    rows.sort(key=lambda x: (floor_order(x["floor"]), not x["sensor"], x["name"] or ""))

    # Räume ohne Ventil-Ausgang (z. B. Gästetoilette, Eingang: keine Fußbodenheizung) werden nur gemessen, nicht
    # geregelt -> zählen nicht zu Sollbereich/Abweichung. Ohne outs-Daten (z. B. kurz nach Start) zählen alle.
    geregelt = any(x["ventil_vorhanden"] for x in rows)
    for x in rows:
        x["nur_messung"] = geregelt and not x["ventil_vorhanden"]
    measured = [x for x in rows if x["abweichung"] is not None and x["stufe"] != STOERUNG and not x["nur_messung"]]
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
            "nur_messung": sum(1 for x in rows if x["nur_messung"] and x["sensor"]),
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
        hinweise.append("EEBUS-Verbindung: " + STATUS_DE.get(av.get("status"), av.get("status") or "?")
                        + (f" ({av['reason']})" if av.get("reason") else ""))
    if aussen_stoerung:
        hinweise.append("Außentemperaturfühler meldet „error“ (nicht angeschlossen?)")
    if online and power is not None and power["alter_s"] > EEBUS_VALUE_HINWEIS_S:
        hinweise.append(f"Leistung seit {int(power['alter_s'] // 60)} min nicht aktualisiert")

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
        "hinweise": hinweise,
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
    status = wp["verbindung"]["status"]
    wp_level = OK if online else UNBEKANNT if status is None else HINWEIS if status in ("verbindet", "pairing") else STOERUNG
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
        elif strom["stufe"] == OK and (strom.get("schaetzung") or {}).get("letzter"):
            text = "Ø " + f"{strom['schaetzung']['letzter']['w'] / 1000:.1f} kW".replace(".", ",") + " (geschätzt)"
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


# --------------------------------------------------------------------------------- Ø-Leistung aus kWh-Sprüngen
# Ohne PIN liefert der Zähler nur ganze kWh. Der Zählerstand springt genau an der kWh-Grenze; die Zeit zwischen
# zwei Sprüngen ergibt die exakte mittlere Leistung dieses Abschnitts (Unschärfe nur durch das 10-s-Sendeintervall).
# Unveränderte Stände speichert der Normalizer alle 5 min -> eine Lücke > LUECKE_S heißt Ausfall: Abschnitt unsicher.
LUECKE_S = 15 * 60


def power_from_steps(points: list[tuple[datetime, float]], now: datetime) -> dict:
    """points: (Zeit, Zählerstand kWh) aufsteigend. Ergebnis: Abschnitte [von, bis, W, sicher] + Obergrenze jetzt."""
    steps, prev_t, max_gap, segs = [], None, 0.0, []
    last_val = None
    for t, v in points:
        if prev_t is not None:
            max_gap = max(max_gap, (t - prev_t).total_seconds())
        if last_val is not None and v > last_val:
            steps.append((t, v, max_gap))
            max_gap = 0.0
        elif last_val is None:
            steps.append((t, v, None))          # erster Stand: kein Sprung, nur Ausgangspunkt
            max_gap = 0.0
        last_val, prev_t = v, t
    for (t0, v0, g0), (t1, v1, gap) in zip(steps, steps[1:]):
        if g0 is None:
            continue                            # Abschnitt vor dem ersten echten Sprung: Beginn unbekannt
        dt = (t1 - t0).total_seconds()
        if dt <= 0:
            continue
        segs.append([t0, t1, round((v1 - v0) * 3_600_000 / dt), g0 <= LUECKE_S and gap is not None and gap <= LUECKE_S])
    out = {"abschnitte": segs, "letzter": None, "obergrenze_w": None, "seit": None}
    if segs:
        out["letzter"] = {"von": segs[-1][0].isoformat(), "bis": segs[-1][1].isoformat(), "w": segs[-1][2],
                          "sicher": segs[-1][3], "minuten": round((segs[-1][1] - segs[-1][0]).total_seconds() / 60)}
    real_steps = [s for s in steps if s[2] is not None]
    if real_steps:
        t_last = real_steps[-1][0]
        since = (now - t_last).total_seconds()
        out["seit"] = t_last.isoformat()
        if since > 60:
            out["obergrenze_w"] = round(3_600_000 / since)
    return out


# --------------------------------------------------------------------------------- WP-Energie aus EEBUS-Leistung
# Der VR 940 liefert über EEBUS keine Energiezähler, nur die Leistung (acPowerTotal). Energie = Trapez-Integral der
# Leistung; Lücken > WP_LUECKE_S (VR 940 aus/getrennt) werden NICHT überbrückt, sondern als fehlende Abdeckung gezeigt.
# Aufteilung: zusammenhängender Anstieg der Warmwassertemperatur um >= DHW_MIN_RISE_K = Warmwasserladung.
# Abgleich mit myVAILLANT (Jahr bis 21.10.2026 24:00, App 6,2 / 8,2 / 14,3 kWh): siehe docs/EEBUS_Datenanalyse.md Kap. 5.
WP_LUECKE_S = 15 * 60
DHW_MIN_RISE_K = 2.0


def dhw_periods(temps: list[tuple[datetime, float]], power: list[tuple[datetime, float]] | None = None
                ) -> list[tuple[datetime, datetime]]:
    """Warmwasserladungen. Der Temperaturanstieg (>= DHW_MIN_RISE_K) zeigt, DASS geladen wurde; die Grenzen kommen aus
    der Leistung, denn der Fühler reagiert 5–15 min verzögert (gemessen 21.10.2026): Beginn = Leistungssprung vor dem
    Anstieg, Ende = Leistungsabfall danach. Ohne Leistungsdaten gilt der Temperaturanstieg selbst."""
    periods, i = [], 0
    while i < len(temps) - 1:
        j = peak = i
        while (j + 1 < len(temps) and temps[j + 1][1] >= temps[peak][1] - 0.5
               and (temps[j + 1][0] - temps[j][0]).total_seconds() < 3600):
            j += 1
            if temps[j][1] > temps[peak][1]:
                peak = j
        lo = min(range(i, peak + 1), key=lambda k: (temps[k][1], -k))   # letzter Tiefpunkt vor dem Anstieg
        if temps[peak][1] - temps[lo][1] >= DHW_MIN_RISE_K:
            start, end = temps[lo][0], temps[peak][0]
            if power:
                load = sorted(p for t, p in power if start <= t <= end)
                if load:
                    level = load[len(load) // 2] * 0.5          # halbe Ladeleistung als Schwelle
                    before = [(t, p) for t, p in power if start - timedelta(minutes=45) <= t <= start]
                    for t, p in reversed(before):              # rückwärts bis zum Leistungssprung
                        if p < level:
                            break
                        start = t
                    after = [(t, p) for t, p in power if temps[lo][0] < t <= end + timedelta(minutes=20)]
                    drop = next((t for t, p in after if p < level), None)
                    if drop is not None:
                        end = drop
            periods.append((start, end))
            i = peak
        else:
            i += 1
    return periods


def wp_energy(power: list[tuple[datetime, float]], temps: list[tuple[datetime, float]],
              start: datetime, end: datetime) -> dict:
    """kWh Heizung/Warmwasser im Zeitraum [start, end) aus Leistungspunkten (W) und Warmwassertemperaturen."""
    periods = dhw_periods(temps, power)
    heiz = ww = covered = 0.0
    for (t0, p0), (t1, p1) in zip(power, power[1:]):
        if (t1 - t0).total_seconds() > WP_LUECKE_S:
            continue
        a, b = max(t0, start), min(t1, end)
        dt = (b - a).total_seconds()
        if dt <= 0:
            continue
        kwh = (p0 + p1) / 2 * dt / 3_600_000
        mid = a + (b - a) / 2
        if any(pa <= mid <= pb for pa, pb in periods):
            ww += kwh
        else:
            heiz += kwh
        covered += dt
    return {"heizung": round(heiz, 3), "warmwasser": round(ww, 3), "gesamt": round(heiz + ww, 3),
            "abdeckung_h": round(covered / 3600, 2), "ladungen": sum(1 for a, b in periods if start <= a < end)}



# --------------------------------------------------------------------------------- Kosten
def kosten_summe(stunden: list[dict], von: datetime, bis: datetime) -> dict:
    """Summe im Zeitraum; je Stunde gilt die neueste Berechnungsversion (Neuberechnungen ersetzen ältere)."""
    neueste = {}
    for r in stunden:
        neueste[r["stunde"]] = max(neueste.get(r["stunde"], 0), r["version"])
    menge = eur = ohne_preis = 0.0
    geschaetzt = vorlaeufig = False
    for r in stunden:
        if not (von < r["stunde"] <= bis + timedelta(hours=1)) or r["version"] != neueste[r["stunde"]]:
            continue
        if r["feld"] == "menge":
            menge += r["wert"]
            if str(r["tarif_version"]) == "0":
                ohne_preis += r["wert"]
        else:
            eur += r["wert"]
        geschaetzt |= r["herkunft"] == "geschaetzt"
        vorlaeufig |= r["vorlaeufig"]
    return {"menge": round(menge, 3), "eur_arbeit": round(eur, 2), "ct_mittel": round(100 * eur / menge, 2) if menge else None,
            "geschaetzt": geschaetzt, "vorlaeufig": vorlaeufig, "menge_ohne_preis": round(ohne_preis, 3)}


def neuberechnung_hinweise(gruppen: list[dict], gueltige: dict[str, list[dict]]) -> list[dict]:
    """Kosten, die mit einer inzwischen ersetzten oder zeitlich nicht mehr passenden Preisversion berechnet wurden
    und noch nicht durch eine höhere Berechnungsversion abgelöst sind."""
    out = []
    for g in gruppen:
        if "von" not in g or "bis" not in g:
            continue
        ok = False
        for v in gueltige.get(g["zaehlpunkt"], []):
            if str(v["id"]) == str(g["tarif_version"]):
                ab = datetime.fromisoformat(v["gueltig_ab"])
                bis = datetime.fromisoformat(v["gueltig_bis"]) if v["gueltig_bis"] else None
                ok = ab <= g["von"] + timedelta(minutes=30) and (bis is None or g["bis"] <= bis + timedelta(minutes=30))
        if str(g["tarif_version"]) == "0" and not gueltige.get(g["zaehlpunkt"]):
            ok = True                               # noch gar kein Preis hinterlegt: nichts neu zu berechnen
        if ok:
            continue
        abgeloest = any(h["zaehlpunkt"] == g["zaehlpunkt"] and h["berechnung_version"] > g["berechnung_version"]
                        and h.get("von") and h.get("bis") and h["von"] <= g["von"] + timedelta(hours=1)
                        and h["bis"] >= g["bis"] - timedelta(hours=1) for h in gruppen)
        if not abgeloest:
            out.append({"zaehlpunkt": g["zaehlpunkt"], "von": g["von"].isoformat(), "bis": g["bis"].isoformat(),
                        "tarif_version": g["tarif_version"], "berechnung_version": g["berechnung_version"]})
    return out
