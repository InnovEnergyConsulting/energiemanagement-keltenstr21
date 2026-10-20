"""EEBUS/SPINE (Vaillant VR 940) -> normalisierte Messwerte, Zustände, Qualitätsmeldungen.

SPINE schickt Werte und ihre Bedeutung in getrennten Nachrichten: measurementDescription sagt, dass
Messung 0 die Warmwassertemperatur in degC ist; measurementList liefert später nur "Messung 0 = 55".
Deshalb merkt sich der Parser die Beschreibungen je Entität (Klasse EebusState) – der Adapter liest
sie bei jeder Verbindung und alle 5 Minuten neu, nach einem Neustart sind sie also schnell wieder da.
"""
from ..common.timeutil import iso, parse_iso


def flat(o):
    """SPINE-JSON ("Objekt" = Liste von Ein-Schlüssel-Dicts, "Array" = Liste von Listen) -> normale Dicts/Listen."""
    if isinstance(o, list) and o and all(isinstance(x, dict) and len(x) == 1 for x in o):
        out = {}
        for x in o:
            (k, v), = x.items()
            out[k] = flat(v)
        return out
    if isinstance(o, list):
        return [flat(x) for x in o]
    if isinstance(o, dict):
        return {k: flat(v) for k, v in o.items()}
    return o


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def scaled(v):
    """{"number": 55, "scale": -1} -> 5.5; alles andere -> None."""
    if not isinstance(v, dict) or not isinstance(v.get("number"), (int, float)) or isinstance(v.get("number"), bool):
        return None
    return float(v["number"]) * 10 ** int(v.get("scale", 0) or 0)


def datagram(payload: dict):
    """Ganze SHIP-Datennachricht -> (Quell-Entität "1"/"5.1.1", cmdClassifier, [(funktion, daten)])."""
    msg = flat(payload)
    dg = msg["data"]["payload"]["datagram"]
    hdr = dg["header"]
    src = hdr.get("addressSource", {})
    ent = src.get("entity", [])
    entity = ".".join(str(x) for x in ent) if isinstance(ent, list) else str(ent)
    cmds = []
    for c in as_list(dg["payload"].get("cmd")):
        if isinstance(c, dict):
            for fn, data in c.items():
                if fn not in ("function", "filter"):   # SPINE-Steuerangaben, keine Daten
                    cmds.append((fn, data))
    return entity, hdr.get("cmdClassifier"), cmds


class EebusState:
    """Merkt sich je Entität die Beschreibungen (Messungen, Sollwerte, Phasen, Betriebsarten, Schlüssel)."""

    def __init__(self):
        self.measure = {}     # (entity, measurementId) -> description
        self.phase = {}       # (entity, measurementId) -> "a" | "abc" | ...
        self.setpoint = {}    # (entity, setpointId) -> description
        self.opmode = {}      # (entity, operationModeId) -> "auto"/"on"/"off"
        self.sysfunc = {}     # (entity, systemFunctionId) -> "dhw"/"heating"
        self.cfgkey = {}      # (entity, keyId) -> keyName
        self.limit = {}       # (entity, limitId) -> description

    def learn(self, entity: str, fn: str, data) -> None:
        d = data if isinstance(data, dict) else {}
        if fn == "measurementDescriptionListData":
            for x in as_list(d.get("measurementDescriptionData")):
                self.measure[(entity, x.get("measurementId"))] = x
        elif fn == "electricalConnectionParameterDescriptionListData":
            for x in as_list(d.get("electricalConnectionParameterDescriptionData")):
                if x.get("measurementId") is not None and x.get("acMeasuredPhases"):
                    self.phase[(entity, x["measurementId"])] = x["acMeasuredPhases"]
        elif fn == "setpointDescriptionListData":
            for x in as_list(d.get("setpointDescriptionData")):
                self.setpoint[(entity, x.get("setpointId"))] = x
        elif fn == "hvacOperationModeDescriptionListData":
            for x in as_list(d.get("hvacOperationModeDescriptionData")):
                self.opmode[(entity, x.get("operationModeId"))] = x.get("operationModeType")
        elif fn == "hvacSystemFunctionDescriptionListData":
            for x in as_list(d.get("hvacSystemFunctionDescriptionData")):
                self.sysfunc[(entity, x.get("systemFunctionId"))] = x.get("systemFunctionType")
        elif fn == "deviceConfigurationKeyValueDescriptionListData":
            for x in as_list(d.get("deviceConfigurationKeyValueDescriptionData")):
                self.cfgkey[(entity, x.get("keyId"))] = x.get("keyName")
        elif fn == "loadControlLimitDescriptionListData":
            for x in as_list(d.get("loadControlLimitDescriptionData")):
                self.limit[(entity, x.get("limitId"))] = x


def normalize(raw: dict, mapping: dict, st: EebusState) -> tuple[list[dict], list[dict], list[dict]]:
    received = parse_iso(raw["received_at"])
    entity, cls, cmds = datagram(raw["payload"])
    if cls not in ("reply", "notify"):
        return [], [], []
    ver = mapping["mapping_version"]
    ent = mapping["entities"].get(entity)
    telemetry, states, quality = [], [], []

    for fn, data in cmds:                      # zuerst lernen (Beschreibung kann in derselben Nachricht stehen)
        st.learn(entity, fn, data)
    for fn, data in cmds:
        if fn in mapping["description_functions"]:
            continue
        d = data if isinstance(data, dict) else {}
        if ent is None:
            quality.append({"source": "eebus", "kind": "unknown_entity",
                            "detail": f"Entität {entity} ({fn}) nicht im Mapping"})
            continue
        base = {"asset_id": ent["asset_id"], "name": ent["name"], "circuit_id": ent.get("circuit_id", "unassigned"),
                "direction": "none", "source": "eebus", "mapping_version": ver, "received_at": iso(received)}

        if fn == "measurementListData":
            for m in as_list(d.get("measurementData")):
                mid = m.get("measurementId")
                desc = st.measure.get((entity, mid))
                value = scaled(m.get("value"))
                if desc is None or value is None:
                    continue                    # ohne Beschreibung keine Bedeutung; ohne Wert kein Messwert (nie 0)
                state = m.get("valueState")
                if state is not None and state not in mapping["accepted_value_states"]:
                    quality.append({"source": "eebus", "kind": "value_state",
                                    "detail": f"{ent['name']}: {desc.get('scopeType')} meldet {state}"})
                    continue
                spec = mapping["measurements"].get(desc.get("scopeType"))
                if spec is None:
                    quality.append({"source": "eebus", "kind": "unknown_scope",
                                    "detail": f"{ent['name']}: scopeType {desc.get('scopeType')} nicht im Mapping"})
                    continue
                unit = mapping["units"].get(desc.get("unit"), {"unit": desc.get("unit"), "factor": 1})
                quantity = spec["quantity"]
                if spec.get("per_phase"):
                    quantity = f"{quantity}_{st.phase.get((entity, mid), f'm{mid}')}"
                measured = parse_iso(m["timestamp"]) if isinstance(m.get("timestamp"), str) else received
                telemetry.append({**base,
                    "point_id": f"{mapping['point_prefix']}.{ent['asset_id']}.{spec['medium']}.{quantity}",
                    "medium": spec["medium"], "quantity": quantity, "role": spec["role"], "unit": unit["unit"],
                    "measurement_kind": "device_reported" if m.get("valueSource") != "measuredValue" else "measured",
                    "value": round(value * unit["factor"], 6), "measured_at": iso(measured),
                    "age_s": round(max(0.0, (received - measured).total_seconds()), 1)})

        elif fn == "setpointListData":
            for s in as_list(d.get("setpointData")):
                desc = st.setpoint.get((entity, s.get("setpointId")))
                value = scaled(s.get("value"))
                spec = mapping["setpoints"].get((desc or {}).get("setpointType"))
                if desc is None or value is None or spec is None:
                    continue
                unit = mapping["units"].get(desc.get("unit"), {"unit": desc.get("unit"), "factor": 1})
                medium = "domestic_hot_water" if ent["asset_id"] == "vaillant_warmwasser" else "room_climate"
                telemetry.append({**base,
                    "point_id": f"{mapping['point_prefix']}.{ent['asset_id']}.{medium}.{spec['quantity']}",
                    "medium": medium, "quantity": spec["quantity"], "role": spec["role"], "unit": unit["unit"],
                    "measurement_kind": "device_reported", "value": round(value * unit["factor"], 6),
                    "measured_at": iso(received), "age_s": 0.0})

        elif fn == "hvacSystemFunctionListData":
            state = {**base, "kind": "hvac"}
            for f in as_list(d.get("hvacSystemFunctionData")):
                func = st.sysfunc.get((entity, f.get("systemFunctionId")))
                if func is None:
                    continue                    # Bedeutung noch unbekannt (Beschreibung folgt) -> nichts Falsches schreiben
                mode = st.opmode.get((entity, f.get("currentOperationModeId")))
                if mode is not None:
                    state[f"{func}_mode"] = mode
                if "isOverrunActive" in f:
                    state[f"{func}_overrun_active"] = bool(f["isOverrunActive"])
            if len(state) > len(base) + 1:
                states.append(state)

        elif fn == "hvacOverrunListData":
            ov = as_list(d.get("hvacOverrunData"))
            if ov:
                states.append({**base, "kind": "hvac_overrun", "overrun_status": ov[0].get("overrunStatus")})

        elif fn == "loadControlLimitListData":
            for lim in as_list(d.get("loadControlLimitData")):
                desc = st.limit.get((entity, lim.get("limitId")), {})
                value = scaled(lim.get("value"))
                states.append({**base, "kind": "load_control", "lpc_limit_active": bool(lim.get("isLimitActive")),
                               "lpc_limit_w": value, "lpc_limit_changeable": bool(lim.get("isLimitChangeable")),
                               "lpc_scope": desc.get("scopeType")})

        elif fn == "deviceConfigurationKeyValueListData":
            state = {**base, "kind": "device_configuration"}
            for kv in as_list(d.get("deviceConfigurationKeyValueData")):
                name = st.cfgkey.get((entity, kv.get("keyId")), f"key_{kv.get('keyId')}")
                v = kv.get("value") or {}
                if "scaledNumber" in v:
                    state[name] = scaled(v["scaledNumber"])
                elif "duration" in v:
                    state[name] = v["duration"]
                elif v:
                    state[name] = str(v)
            if len(state) > len(base) + 1:
                states.append(state)

        elif fn in ("deviceDiagnosisStateData", "deviceDiagnosisHeartbeatData", "deviceClassificationManufacturerData",
                    "deviceClassificationUserData"):
            if isinstance(d, dict) and d:
                states.append({**base, "kind": fn, **{k: (v if isinstance(v, (str, int, float, bool)) else str(v))
                                                    for k, v in d.items()}})
        else:
            quality.append({"source": "eebus", "kind": "unknown_function",
                            "detail": f"{ent['name']}: Funktion {fn} nicht im Mapping"})
    return telemetry, states, quality
