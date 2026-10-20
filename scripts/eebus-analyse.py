#!/usr/bin/env python3
"""Stufe B – Datenstromanalyse der EEBUS-Aufzeichnung (nur Standardbibliothek).

Liest data/eebus/spine-trace-*.jsonl und erzeugt data/eebus/analyse-<datum>.md:
je Entität und Messgröße/Sollwert: Anzahl Werte, Benachrichtigungen vs. Antworten, Abstände,
Wertebereich, Status (valueState), sowie Messgrößen, die beschrieben, aber nie mit Wert geliefert wurden.

Aufruf:  python3 scripts/eebus-analyse.py [tage=7]
"""
import glob
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "eebus")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
ENT = {"1": "Wärmepumpe", "4": "Warmwasser", "5.1.1": "Heizzone 1", "6": "Außenfühler"}


def flat(o):
    if isinstance(o, list) and o and all(isinstance(x, dict) and len(x) == 1 for x in o):
        return {k: flat(v) for x in o for k, v in x.items()}
    if isinstance(o, list):
        return [flat(x) for x in o]
    if isinstance(o, dict):
        return {k: flat(v) for k, v in o.items()}
    return o


def as_list(v):
    return [] if v is None else (v if isinstance(v, list) else [v])


def scaled(v):
    if isinstance(v, dict) and isinstance(v.get("number"), (int, float)):
        return v["number"] * 10 ** int(v.get("scale", 0) or 0)
    return None


since = datetime.now(timezone.utc) - timedelta(days=DAYS)
desc_m, desc_s = {}, {}
series = defaultdict(list)          # (entity, art, id) -> [(zeit, wert, status, klasse)]
msg_count = defaultdict(int)
for path in sorted(glob.glob(os.path.join(BASE, "spine-trace-*.jsonl"))):
    for line in open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        ts = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
        m = r.get("msg")
        if ts < since or r.get("dir") != "Recv" or not isinstance(m, dict) or "data" not in m:
            continue
        try:
            dg = flat([x for x in m["data"] if "payload" in x][0]["payload"])["datagram"]
            hdr = dg["header"]
            ent = ".".join(map(str, hdr.get("addressSource", {}).get("entity", [])))
            cls = hdr.get("cmdClassifier")
            cmds = as_list(dg["payload"].get("cmd"))
        except (KeyError, IndexError, TypeError):
            continue
        for c in cmds:
            if not isinstance(c, dict):
                continue
            for fn, data in c.items():
                msg_count[(ent, fn, cls)] += 1
                d = data if isinstance(data, dict) else {}
                if fn == "measurementDescriptionListData":
                    for x in as_list(d.get("measurementDescriptionData")):
                        desc_m[(ent, x.get("measurementId"))] = x
                elif fn == "setpointDescriptionListData":
                    for x in as_list(d.get("setpointDescriptionData")):
                        desc_s[(ent, x.get("setpointId"))] = x
                elif fn == "measurementListData":
                    for x in as_list(d.get("measurementData")):
                        series[(ent, "M", x.get("measurementId"))].append((ts, scaled(x.get("value")), x.get("valueState"), cls))
                elif fn == "setpointListData":
                    for x in as_list(d.get("setpointData")):
                        series[(ent, "S", x.get("setpointId"))].append((ts, scaled(x.get("value")), None, cls))

out = [f"# EEBUS-Datenstromanalyse – {datetime.now():%d.%m.%Y %H:%M}", "",
       f"Zeitraum: letzte {DAYS} Tage · Quelle: `data/eebus/spine-trace-*.jsonl`", "",
       "## Messwerte und Sollwerte", "",
       "| Entität | Art | Größe | Einheit | Werte | davon Notify | Abstand Median | Min | Max | Letzter | Status |",
       "|---|---|---|---|---|---|---|---|---|---|---|"]
for key in sorted(series, key=lambda k: (k[0], k[1], str(k[2]))):
    ent, art, i = key
    pts = [p for p in series[key] if p[1] is not None]
    desc = (desc_m if art == "M" else desc_s).get((ent, i), {})
    name = desc.get("scopeType") or desc.get("setpointType") or f"id {i}"
    if not pts:
        continue
    vals = [p[1] for p in pts]
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(pts, pts[1:])]
    gap = f"{statistics.median(gaps):.0f} s" if gaps else "–"
    states = sorted({str(p[2]) for p in pts})
    out.append(f"| {ENT.get(ent, ent)} | {'Messung' if art == 'M' else 'Sollwert'} | {name} | {desc.get('unit', '')} | "
               f"{len(pts)} | {sum(1 for p in pts if p[3] == 'notify')} | {gap} | {min(vals):g} | {max(vals):g} | "
               f"{vals[-1]:g} | {', '.join(states)} |")

never = [(e, i, d) for (e, i), d in desc_m.items() if not any(p[1] is not None for p in series.get((e, "M", i), []))]
out += ["", "## Beschrieben, aber nie mit Wert geliefert", ""]
out += [f"- {ENT.get(e, e)}: Messung {i} `{d.get('scopeType')}` ({d.get('unit', '')})" for e, i, d in sorted(never, key=str)] or ["- keine"]
out += ["", "## Nachrichten je Funktion", "", "| Entität | Funktion | Art | Anzahl |", "|---|---|---|---|"]
out += [f"| {ENT.get(e, e)} | {fn} | {cls} | {n} |" for (e, fn, cls), n in sorted(msg_count.items())]
target = os.path.join(BASE, f"analyse-{datetime.now():%Y-%m-%d}.md")
open(target, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"Bericht: {os.path.normpath(target)}")
