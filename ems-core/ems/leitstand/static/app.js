/* EMS-Leitstand – Oberfläche (ohne Bibliotheken). Holt /api/lage alle 30 s, Verläufe je Reiter bei Bedarf,
   Raum-Detailfenster über /api/raum. Zeitspannen: 30 min … 30 Tage (feste Stufen, siehe SPANNEN). */
(function () {
  "use strict";
  var TZ = "Europe/Berlin";
  var $ = function (id) { return document.getElementById(id); };
  var LAGE_MS = 30000;
  var SPANNEN = [["30m", "30 min"], ["60m", "60 min"], ["180m", "180 min"], ["6h", "6 h"], ["12h", "12 h"],
                 ["24h", "24 h"], ["7d", "7 Tage"], ["30d", "30 Tage"]];
  var SPANNE_MS = { "30m": 18e5, "60m": 36e5, "180m": 108e5, "6h": 216e5, "12h": 432e5, "24h": 864e5, "7d": 6048e5, "30d": 2592e6 };
  var state = { lage: null, tab: "fb", spanne: {}, verlauf: {}, raum: null, raumDaten: null, lastOk: null };
  ["fb", "wp", "raum"].forEach(function (b) { var s = store("ems-spanne-" + b); state.spanne[b] = SPANNE_MS[s] ? s : "24h"; });

  // ------------------------------------------------------------------ Hilfen
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function num(v, d) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toLocaleString("de-DE", { minimumFractionDigits: d == null ? 1 : d, maximumFractionDigits: d == null ? 1 : d });
  }
  function signed(v, d) { return v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "±") + num(Math.abs(v), d); }
  function ago(s) {
    if (s == null) return "–";
    if (s < 90) return Math.round(s) + " s";
    if (s < 5400) return Math.round(s / 60) + " min";
    if (s < 172800) return Math.round(s / 3600) + " h";
    return Math.round(s / 86400) + " Tage";
  }
  var fmtTime = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
  var fmtDateTime = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  var fmtFull = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  var fmtDay = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, weekday: "short" });
  var fmtDate = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, day: "2-digit", month: "2-digit" });
  var fmtHM = new Intl.DateTimeFormat("de-DE", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  function isToday(d) { return fmtDate.format(d) === fmtDate.format(new Date()); }
  function when(iso) {
    if (!iso) return "–";
    var d = new Date(iso);
    return isToday(d) ? "heute " + fmtTime.format(d) : fmtDateTime.format(d);
  }
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function kpi(cls, k, v, s, title) {
    return '<div class="kpi ' + (cls || "") + '"' + (title ? ' title="' + esc(title) + '"' : "") + '><div class="k">' + esc(k) +
      '</div><div class="v' + (/^</.test(v) || /^[\d−+±–]/.test(v) ? "" : " txt") + '">' + v + '</div><div class="s">' + esc(s || "") + "</div></div>";
  }
  function fensterMs(f) { var m = /^(\d+)([mh])$/.exec(f || ""); return m ? Number(m[1]) * (m[2] === "h" ? 36e5 : 6e4) : 6e5; }

  // ------------------------------------------------------------------ Liniendiagramm (SVG)
  // Reihen mit "stufig" (Fühler senden nur bei Änderung) werden als Stufen gezeichnet und bis zu halten_s
  // fortgesetzt – auch vom letzten Wert vor Beginn der Spanne bis jetzt. Längere Pausen bleiben als Lücke sichtbar.
  function niceStep(span, n) {
    var raw = span / n, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p;
  }
  function timeTicks(x0, x1) {
    var span = x1 - x0, steps = [5, 10, 15, 30, 60, 120, 180, 360, 720].map(function (m) { return m * 6e4; });
    var step = steps.filter(function (s) { return span / s <= 8; })[0];
    var out = [];
    if (step) {   // innerhalb von Stunden: auf runde Uhrzeiten (Ortszeit) ausgerichtet
      var berlinOff = tzOffset(x0);
      for (var t = Math.ceil((x0 + berlinOff) / step) * step - berlinOff; t <= x1; t += step) out.push([t, fmtHM.format(new Date(t))]);
      return out;
    }
    var dayStep = span <= 10 * 864e5 ? 1 : 5, first = Math.ceil((x0 + tzOffset(x0)) / 864e5) * 864e5;
    for (var d = 0; first + d * 864e5 <= x1 + 864e5; d += dayStep) {
      var tt = first + d * 864e5 - tzOffset(first + d * 864e5);
      if (tt < x0 || tt > x1) continue;
      out.push([tt, dayStep === 1 ? fmtDay.format(new Date(tt)) : fmtDate.format(new Date(tt))]);
    }
    return out;
  }
  function tzOffset(t) {   // Abstand Europe/Berlin zu UTC in ms (Sommer-/Winterzeit)
    var p = {};
    new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(t)).forEach(function (x) { p[x.type] = x.value; });
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(t / 1000) * 1000;
  }
  function buildSegs(s, x0, x1, gap, hold) {
    var pts = s.punkte.slice();
    if (s.stufig && s.vorher && (!pts.length || pts[0][0] > x0)) pts.unshift([x0, s.vorher[1]]);
    var segs = [], cur = [];
    pts.forEach(function (p, i) {
      if (i) {
        var prev = pts[i - 1], dt = p[0] - prev[0];
        if (dt > (s.stufig ? hold : gap)) { segs.push(cur); cur = []; }
        else if (s.stufig) cur.push([p[0], prev[1]]);
      }
      cur.push(p);
    });
    if (s.stufig && pts.length) {
      var last = pts[pts.length - 1];
      if (x1 > last[0] && x1 - last[0] <= hold) cur.push([x1, last[1]]);
    }
    segs.push(cur);
    return segs.filter(function (g) { return g.length; });
  }
  function valueAt(segs, t, stufig, tol) {
    var best = null;
    segs.forEach(function (g) {
      if (stufig) { if (t >= g[0][0] && t <= g[g.length - 1][0]) g.forEach(function (p) { if (p[0] <= t) best = p; }); }
      else g.forEach(function (p) { if (Math.abs(p[0] - t) <= tol && (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t))) best = p; });
    });
    return best;
  }
  function lineChart(el, series, opt) {
    opt = opt || {};
    var W = 640, H = 230, L = 44, R = 10, T = 12, B = 24;
    var x0 = opt.from, x1 = opt.to, gap = opt.gapMs || 3 * 36e5, hold = opt.holdMs || 6 * 36e5;
    series.forEach(function (s) { s._segs = buildSegs(s, x0, x1, gap, hold); });
    var all = [];
    series.forEach(function (s) { s._segs.forEach(function (g) { g.forEach(function (p) { all.push(p); }); }); });
    if (!all.length) { el.innerHTML = '<div class="empty">' + esc(opt.leer || "Keine Daten im gewählten Zeitraum") + "</div>"; return; }
    var vals = all.map(function (p) { return p[1]; });
    var y0 = Math.min.apply(null, vals), y1 = Math.max.apply(null, vals);
    if (opt.yMin != null) y0 = Math.min(y0, opt.yMin);
    var ms = opt.minSpan || 1;
    if (y1 - y0 < ms) { var mid = (y0 + y1) / 2; y0 = mid - ms / 2; y1 = mid + ms / 2; if (opt.yMin != null && y0 < opt.yMin) { y1 += opt.yMin - y0; y0 = opt.yMin; } }
    var step = niceStep(y1 - y0, 4);
    y0 = Math.floor(y0 / step) * step; y1 = Math.ceil(y1 / step) * step;
    var sx = function (t) { return L + (t - x0) / Math.max(1, x1 - x0) * (W - L - R); };
    var sy = function (v) { return T + (y1 - v) / Math.max(1e-9, y1 - y0) * (H - T - B); };
    var out = ['<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(opt.label || "Verlauf") + '">'];
    for (var v = y0; v <= y1 + step / 2; v += step) {
      out.push('<line x1="' + L + '" x2="' + (W - R) + '" y1="' + sy(v) + '" y2="' + sy(v) + '" stroke="' + css("--line") + '"/>');
      out.push('<text x="' + (L - 6) + '" y="' + (sy(v) + 3) + '" text-anchor="end">' + num(v, step < 1 ? 1 : 0) + "</text>");
    }
    timeTicks(x0, x1).forEach(function (tk) {
      out.push('<line x1="' + sx(tk[0]) + '" x2="' + sx(tk[0]) + '" y1="' + T + '" y2="' + (H - B) + '" stroke="' + css("--line") + '" stroke-dasharray="2 4"/>');
      out.push('<text x="' + sx(tk[0]) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(tk[1]) + "</text>");
    });
    series.slice().sort(function (a, b) { return (a.muted ? 0 : 1) - (b.muted ? 0 : 1); }).forEach(function (s) {
      var color = s.muted ? css("--chart-muted") : s.color;
      s._segs.forEach(function (g) {
        var pts = g.map(function (p) { return sx(p[0]).toFixed(1) + "," + sy(p[1]).toFixed(1); });
        if (s.area && pts.length > 1) {
          var base = sy(Math.max(y0, 0)).toFixed(1);
          out.push('<polygon fill="' + css("--accent-soft") + '" points="' + sx(g[0][0]).toFixed(1) + "," + base + " " + pts.join(" ") + " " + sx(g[g.length - 1][0]).toFixed(1) + "," + base + '"/>');
        }
        if (pts.length === 1) out.push('<circle cx="' + pts[0].split(",")[0] + '" cy="' + pts[0].split(",")[1] + '" r="2.5" fill="' + color + '"/>');
        else out.push('<polyline fill="none" stroke="' + color + '" stroke-width="' + (s.width || (s.muted ? 1 : 2)) + '"' + (s.dash ? ' stroke-dasharray="5 4"' : "") + ' stroke-linejoin="round" points="' + pts.join(" ") + '"/>');
      });
    });
    out.push('<line class="cross" x1="0" x2="0" y1="' + T + '" y2="' + (H - B) + '" stroke="' + css("--ink-3") + '" visibility="hidden"/>');
    out.push("</svg>");
    el.innerHTML = out.join("") + '<div class="readout" hidden></div>';
    var svg = el.querySelector("svg"), cross = el.querySelector(".cross"), ro = el.querySelector(".readout");
    var shown = series.filter(function (s) { return !s.muted; });
    function move(ev) {
      var r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * W;
      if (x < L || x > W - R) return leave();
      var t = x0 + (x - L) / (W - L - R) * (x1 - x0), parts = [];
      shown.forEach(function (s) {
        var p = valueAt(s._segs, t, s.stufig, gap);
        if (p) parts.push(esc(s.name) + " " + num(p[1], opt.digits) + (opt.unit ? " " + opt.unit : ""));
      });
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      ro.innerHTML = esc(fmtDateTime.format(new Date(t))) + (parts.length ? "<br>" + parts.join("<br>") : "");
      ro.hidden = false;
    }
    function leave() { cross.setAttribute("visibility", "hidden"); ro.hidden = true; }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
  }
  function chartOpt(v, extra) {
    var o = { from: v.von, to: v.bis, gapMs: Math.max(3 * fensterMs(v.fenster), 5 * 6e4), holdMs: (v.halten_s || 21600) * 1000 };
    for (var k in extra) o[k] = extra[k];
    return o;
  }
  function legend(el, items) {
    el.innerHTML = items.map(function (i) { return '<span><i class="' + (i.dash ? "dash" : "") + '" style="border-color:' + (i.muted ? css("--chart-muted") : i.color) + '"></i>' + esc(i.name) + "</span>"; }).join("");
  }
  function meanSeries(list) {
    var acc = {};
    list.forEach(function (s) { s.punkte.forEach(function (p) { (acc[p[0]] = acc[p[0]] || []).push(p[1]); }); });
    return Object.keys(acc).map(Number).sort(function (a, b) { return a - b; }).map(function (t) {
      var a = acc[t]; return [t, a.reduce(function (x, y) { return x + y; }, 0) / a.length];
    });
  }

  // ------------------------------------------------------------------ Kopfzeile
  function renderHead(l) {
    $("gesamt").className = "dot " + l.gesamt;
    $("gesamt").title = { ok: "Alles in Ordnung", hinweis: "Hinweise vorhanden", stoerung: "Störung", unbekannt: "Zustand unbekannt" }[l.gesamt] || "";
    $("status").innerHTML = l.kopf.map(function (k) {
      var val = k.text || (k.id === "backup" ? when(k.zeit) : k.alter_s != null ? "vor " + ago(k.alter_s) : "–");
      return '<div class="st ' + k.stufe + '" title="' + esc(k.titel || "") + '"><span class="dot"></span><span class="lbl">' + esc(k.label) + '</span><span class="val">' + esc(val) + "</span></div>";
    }).join("");
    $("stand").textContent = "Stand " + fmtFull.format(new Date(l.stand));
    var fb = l.fb, nfb = fb.hinweise.length, nwp = l.wp.hinweise.length;
    $("b-fb").className = "badge" + (nfb ? " hinweis" : "");
    $("b-fb").textContent = nfb ? nfb + (nfb === 1 ? " Hinweis" : " Hinweise") : fb.kennzahlen.raeume + " Räume";
    var wpSt = l.kopf.filter(function (k) { return k.id === "wp"; })[0];
    $("b-wp").className = "badge" + (wpSt && wpSt.stufe !== "ok" ? " " + wpSt.stufe : "");
    $("b-wp").textContent = nwp ? String(nwp) : (l.wp.verbindung.status === "online" ? "verbunden" : "–");
    $("b-shelly").textContent = l.shelly.geraete.length ? l.shelly.geraete.length + " erkannt" : "geplant";
  }

  // ------------------------------------------------------------------ Reiter 1
  function renderFb(fb) {
    var k = fb.kennzahlen, g = k.groesste_abweichung;
    var h = kpi("", "Räume im Sollbereich (±0,5 K)", k.im_soll + " <small>von " + k.mit_fuehler + "</small>",
      (k.raeume - k.mit_fuehler) + " Räume ohne eigenen Raumfühler");
    h += g ? kpi("", "Größte Abweichung", signed(g.wert) + " <small>K</small>", g.raum + " " + num(g.ist) + " / " + num(g.soll) + " °C")
      : kpi("unbekannt", "Größte Abweichung", "–", "keine aktuellen Fühlerwerte");
    h += k.ruecklauf_mittel != null
      ? kpi("", "Rücklauf Mittel", num(k.ruecklauf_mittel) + " <small>°C</small>", k.ruecklauf_kreise + " Kreise, Spanne " + num(k.ruecklauf_min) + "–" + num(k.ruecklauf_max))
      : kpi("unbekannt", "Rücklauf Mittel", "–", "keine aktuellen Werte");
    h += k.vorlauf != null
      ? kpi("", "Vorlauf", num(k.vorlauf) + " <small>°C</small>", k.vorlauf_soll != null ? "Soll " + num(k.vorlauf_soll) + " °C" : "vor " + ago(k.vorlauf_alter_s))
      : kpi("geplant", "Vorlauf", "Fühler wird eingebaut", "Controme liefert noch keinen Wert");
    $("fb-kpis").innerHTML = h;

    var maxDev = Math.max(2, Math.max.apply(null, fb.raeume.map(function (r) { return Math.abs(r.abweichung || 0); })));
    var rows = [], floor = null;
    if (!state.raum || !fb.raeume.some(function (r) { return r.asset_id === state.raum; })) {
      var pref = store("ems-raum");
      var withSensor = fb.raeume.filter(function (r) { return r.sensor; });
      state.raum = fb.raeume.some(function (r) { return r.asset_id === pref; }) ? pref : (withSensor[0] || fb.raeume[0] || {}).asset_id;
    }
    fb.raeume.forEach(function (r) {
      if (r.floor !== floor) { floor = r.floor; rows.push('<tr class="floor"><td colspan="9">' + esc(floor) + "</td></tr>"); }
      var bar = "–";
      if (r.abweichung != null) {
        var w = Math.min(50, Math.abs(r.abweichung) / maxDev * 50);
        bar = '<span class="devbar"><i style="' + (r.abweichung < 0 ? "right:50%" : "left:50%") + ";width:" + w + '%"></i></span> <span class="dim num">' + signed(r.abweichung) + "</span>";
      }
      var age = r.sensor ? (r.stufe === "ok" ? '<span class="dim">' + ago(r.alter_s) + "</span>" : '<span class="tag ' + r.stufe + '">' + ago(r.alter_s) + "</span>") : '<span class="dim">kein Fühler</span>';
      rows.push('<tr class="pick' + (r.asset_id === state.raum ? " sel" : "") + '" tabindex="0" data-raum="' + esc(r.asset_id) + '" aria-haspopup="dialog" title="Detailfenster öffnen">' +
        "<td>" + esc(r.name) + '</td><td class="n">' + num(r.ist) + '</td><td class="n">' + num(r.soll) + "</td><td>" + bar + "</td>" +
        '<td class="n dim">' + num(r.offset, 2) + '</td><td class="n">' + (r.feuchte != null ? num(r.feuchte, 0) + " %" : '<span class="dim">–</span>') + "</td>" +
        "<td>" + esc(r.szene || "–") + (r.betriebsart && r.betriebsart !== "Heizen" ? ' <span class="tag">' + esc(r.betriebsart) + "</span>" : "") + "</td>" +
        '<td class="dim">' + (r.wechsel_um ? esc(r.wechsel_um.slice(11)) + " → " + esc(r.naechste_szene || "") + (r.soll_naechste != null ? " (" + num(r.soll_naechste) + " °C)" : "") : "–") + "</td>" +
        '<td class="n">' + age + "</td></tr>");
    });
    $("fb-rooms").innerHTML = rows.join("");

    $("fb-returns").innerHTML = fb.ruecklauf.map(function (r) {
      return "<tr><td>" + esc(r.name.replace(/^Rücklauf /, "")) + '</td><td class="dim">' + esc(r.raum || "–") + '</td><td class="n">' + num(r.wert) + '</td><td class="n">' +
        (r.stufe === "ok" ? '<span class="dim">' + ago(r.alter_s) + "</span>" : '<span class="tag ' + r.stufe + '">' + ago(r.alter_s) + "</span>") + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="dim">keine Rücklauffühler</td></tr>';
    renderFbCharts();
    if ($("raum-dlg").open) renderRaumKpis();
  }
  function selectRoom(id) {
    state.raum = id; store("ems-raum", id);
    [].forEach.call(document.querySelectorAll("#fb-rooms tr.pick"), function (tr) { tr.classList.toggle("sel", tr.getAttribute("data-raum") === id); });
    renderFbCharts();
  }
  function renderFbCharts() {
    var v = state.verlauf["fb|" + state.spanne.fb], l = state.lage;
    if (!v || !l) return;
    var room = l.fb.raeume.filter(function (r) { return r.asset_id === state.raum; })[0];
    var rn = room ? room.name : "–", acc = css("--chart");
    var series = v.reihen.filter(function (s) { return s.role === "room_actual" && s.quantity === "temperature"; })
      .map(function (s) { return { name: "Ist", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, muted: s.asset_id !== state.raum, color: acc }; });
    v.reihen.forEach(function (s) { if (s.role === "setpoint_effective" && s.asset_id === state.raum) series.push({ name: "Soll", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: acc, dash: true, width: 1.5 }); });
    $("fb-chart-title").innerHTML = "Raumtemperatur – " + esc(rn) + " <small>übrige Räume grau</small>";
    $("fb-sel-hint").textContent = "– " + rn;
    lineChart($("fb-chart-rooms"), series, chartOpt(v, { unit: "°C", minSpan: 3, label: "Raumtemperaturen" }));
    legend($("fb-legend-rooms"), [{ name: rn + " Ist", color: acc }, { name: rn + " Soll", color: acc, dash: true }, { name: "übrige Räume", muted: true }]);

    var ret = v.reihen.filter(function (s) { return s.role === "return_flow"; });
    var mean = meanSeries(ret);
    var rs = ret.map(function (s) { return { name: s.name, punkte: s.punkte, muted: true }; });
    if (mean.length) rs.push({ name: "Mittel", punkte: mean, color: acc });
    lineChart($("fb-chart-return"), rs, chartOpt(v, { unit: "°C", minSpan: 3, label: "Rücklauftemperaturen" }));
    legend($("fb-legend-return"), [{ name: "Mittel", color: acc }, { name: "einzelne Kreise", muted: true }]);

    var flow = v.reihen.filter(function (s) { return s.role === "flow_actual" || s.role === "flow_setpoint"; });
    if (!flow.length) {
      $("fb-flow").innerHTML = '<div class="empty">Vorlauffühler noch nicht eingebaut – Controme liefert noch keinen Wert. Der Verlauf erscheint hier automatisch, sobald Werte kommen.</div>';
    } else {
      $("fb-flow").innerHTML = '<div class="chart" id="fb-chart-flow"></div><div class="legend" id="fb-legend-flow"></div>';
      var fs = flow.map(function (s) { return { name: s.role === "flow_actual" ? "Ist" : "Soll", punkte: s.punkte, color: css("--chart-2"), dash: s.role === "flow_setpoint" }; });
      lineChart($("fb-chart-flow"), fs, chartOpt(v, { unit: "°C", minSpan: 5, label: "Vorlauf" }));
      legend($("fb-legend-flow"), fs);
    }
  }

  // ------------------------------------------------------------------ Raum-Detailfenster
  function openRoom(id) {
    selectRoom(id);
    state.raumDaten = null;
    renderRaumKpis();
    ["raum-chart-temp", "raum-chart-return", "raum-chart-hum"].forEach(function (c) { $(c).innerHTML = '<div class="empty">lädt …</div>'; });
    var dlg = $("raum-dlg");
    if (!dlg.open) { if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", ""); }
    loadRaum();
  }
  function closeRoom() { var d = $("raum-dlg"); if (d.close) d.close(); else d.removeAttribute("open"); }
  function renderRaumKpis() {
    var r = state.lage && state.lage.fb.raeume.filter(function (x) { return x.asset_id === state.raum; })[0];
    if (!r) return;
    $("raum-titel").textContent = r.name;
    $("raum-sub").textContent = r.floor + " · " + (r.sensor ? "eigener Raumfühler" : "ohne Raumfühler – nur Sollwerte") + " · Controme";
    var h = kpi(r.sensor ? (r.stufe === "ok" ? "" : r.stufe) : "unbekannt", "Ist", r.ist != null ? num(r.ist) + " <small>°C</small>" : "–", r.sensor ? "Fühler vor " + ago(r.alter_s) : "kein Raumfühler");
    h += kpi("", "Soll", num(r.soll) + " <small>°C</small>", r.wechsel_um ? "ab " + r.wechsel_um.slice(11) + ": " + num(r.soll_naechste) + " °C" : "");
    h += kpi(r.abweichung == null ? "unbekannt" : "", "Abweichung", r.abweichung == null ? "–" : signed(r.abweichung) + " <small>K</small>", "Ist − Soll");
    h += kpi(r.feuchte == null ? "unbekannt" : "", "Luftfeuchte", r.feuchte == null ? "–" : num(r.feuchte, 0) + " <small>%</small>", r.feuchte == null ? "Fühler misst keine Feuchte" : "");
    h += kpi("", "Szene", esc(r.szene || "–"), "Offset " + num(r.offset, 2) + " K" + (r.betriebsart ? " · " + r.betriebsart : ""));
    $("raum-kpis").innerHTML = h;
  }
  function renderRaumCharts() {
    var v = state.raumDaten;
    if (!v) return;
    var acc = css("--chart"), sp = SPANNEN.filter(function (s) { return s[0] === state.spanne.raum; })[0];
    $("raum-fenster").textContent = "– letzte " + sp[1] + ", Mittel je " + v.fenster.replace("m", " min").replace("h", " h");
    var temp = [];
    v.reihen.forEach(function (s) {
      if (s.asset_id !== state.raum) return;
      if (s.quantity === "temperature") temp.push({ name: "Ist", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: acc });
      if (s.quantity === "temperature_setpoint") temp.push({ name: "Soll", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: acc, dash: true, width: 1.5 });
    });
    temp.sort(function (a, b) { return a.name < b.name ? -1 : 1; });
    lineChart($("raum-chart-temp"), temp, chartOpt(v, { unit: "°C", minSpan: 2, label: "Raumtemperatur" }));
    legend($("raum-legend-temp"), temp);

    var colors = [css("--chart-2"), css("--chart-3"), css("--ink-2"), css("--chart")];
    var ret = v.reihen.filter(function (s) { return s.role === "return_flow"; })
      .map(function (s, i) { return { name: s.name.replace(/^Rücklauf /, ""), punkte: s.punkte, color: colors[i % colors.length] }; });
    lineChart($("raum-chart-return"), ret, chartOpt(v, { unit: "°C", minSpan: 2, label: "Rücklauf", leer: "Diesem Raum ist kein Rücklauffühler zugeordnet" }));
    legend($("raum-legend-return"), ret);

    var hum = v.reihen.filter(function (s) { return s.asset_id === state.raum && s.quantity === "relative_humidity"; })
      .map(function (s) { return { name: "Feuchte", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: css("--chart-3") }; });
    lineChart($("raum-chart-hum"), hum, chartOpt(v, { unit: "%", digits: 0, minSpan: 10, label: "Luftfeuchte", leer: "Der Fühler dieses Raums misst keine Luftfeuchte" }));
    legend($("raum-legend-hum"), hum);
  }
  function loadRaum() {
    var id = state.raum, sp = state.spanne.raum;
    getJSON("api/raum?id=" + encodeURIComponent(id) + "&spanne=" + sp).then(function (v) {
      if (id !== state.raum || sp !== state.spanne.raum) return;   // inzwischen anderer Raum/Spanne gewählt
      state.raumDaten = v; renderRaumCharts();
    }).catch(function (e) {
      ["raum-chart-temp", "raum-chart-return", "raum-chart-hum"].forEach(function (c) { $(c).innerHTML = '<div class="empty">Keine Daten: ' + esc(e.message) + "</div>"; });
    });
  }

  // ------------------------------------------------------------------ Reiter 2
  function renderWp(wp) {
    var h = "";
    var p = wp.leistung;
    h += p ? kpi(p.alter_s > 900 ? "hinweis" : "", "Leistungsaufnahme", num(p.wert, 0) + " <small>W</small>", (p.wert < 50 ? "Bereitschaft" : "in Betrieb") + " · vor " + ago(p.alter_s))
      : kpi("unbekannt", "Leistungsaufnahme", "–", "noch kein Wert");
    var w = wp.warmwasser;
    h += kpi(w.ist ? "" : "unbekannt", "Warmwasser Ist / Soll", (w.ist ? num(w.ist.wert, 0) : "–") + " <small>/ " + (w.soll ? num(w.soll.wert, 0) : "–") + " °C</small>",
      (w.betriebsart || "–") + (w.schnellladung === true ? " · Schnellladung aktiv" : ""));
    var z = wp.zone;
    h += kpi(z.ist ? "" : "unbekannt", "Heizzone 1 Ist / Soll", (z.ist ? num(z.ist.wert, 1) : "–") + " <small>/ " + (z.soll ? num(z.soll.wert, 1) : "–") + " °C</small>",
      "Heizbetrieb " + (z.heizbetrieb || "–"));
    var a = wp.aussen;
    h += a.wert ? kpi("", "Außentemperatur", num(a.wert.wert) + " <small>°C</small>", "Vaillant-Fühler · vor " + ago(a.wert.alter_s))
      : a.stoerung ? kpi("hinweis", "Außentemperatur", "Fühler meldet Fehler", "VR 940: valueState „error“ – noch nicht angeschlossen?")
      : kpi("unbekannt", "Außentemperatur", "–", "noch kein Wert");
    var lpc = wp.lpc;
    h += kpi(lpc.aktiv ? "hinweis" : "", "Leistungsbegrenzung § 14a", lpc.aktiv == null ? "–" : lpc.aktiv ? num(lpc.grenze_w, 0) + " <small>W</small>" : "nicht aktiv",
      lpc.failsafe_dauer ? "Failsafe " + lpc.failsafe_dauer.replace(/^PT/, "").replace("H", " h").replace("M", " min") : "");
    $("wp-kpis").innerHTML = h;

    var v = wp.verbindung, chips = [];
    chips.push('<span class="chip' + (v.status === "online" ? "" : " hinweis") + '">EEBUS: <b>' + esc(v.status === "online" ? "verbunden" : v.status || "unbekannt") + "</b>" + (v.seit ? " seit " + esc(when(v.seit)) : "") + "</span>");
    chips.push('<span class="chip">Warmwasser: <b>' + esc(w.betriebsart || "–") + "</b></span>");
    chips.push('<span class="chip">Heizen Zone 1: <b>' + esc(z.heizbetrieb || "–") + "</b></span>");
    chips.push('<span class="chip">Schnellladung: <b>' + (w.schnellladung == null ? "–" : w.schnellladung ? "aktiv" : "inaktiv") + "</b></span>");
    chips.push(wp.energie ? '<span class="chip">Energie: <b>' + num(wp.energie.wert, 1) + " kWh</b></span>"
      : '<span class="chip geplant">Energie (kWh): <b>nicht über EEBUS</b> – eigener WP-Zähler folgt</span>');
    $("wp-chips").innerHTML = chips.join("");

    $("wp-msgs").innerHTML = wp.meldungen.map(function (m) {
      return '<tr><td class="n">' + esc(when(m.zuletzt)) + '</td><td class="wrap-text">' + esc(m.text) + '</td><td class="n">' + m.anzahl + '</td><td class="n dim">' + esc(when(m.erstmals)) + "</td></tr>";
    }).join("") || '<tr><td colspan="4" class="dim">keine Meldungen</td></tr>';
    renderWpCharts();
  }
  function renderWpCharts() {
    var v = state.verlauf["wp|" + state.spanne.wp];
    if (!v) return;
    var pw = v.reihen.filter(function (s) { return s.asset_id === "vaillant_wp" && s.quantity === "active_power"; })
      .map(function (s) { return { name: "Leistung", punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: css("--chart"), area: true, width: 1.5 }; });
    lineChart($("wp-chart-power"), pw, chartOpt(v, { unit: "W", digits: 0, yMin: 0, minSpan: 100, label: "Leistungsaufnahme", leer: "Noch keine Leistungswerte" }));
    legend($("wp-legend-power"), pw);
    var names = {
      "vaillant_warmwasser|temperature": ["Warmwasser Ist", "--chart-2", false],
      "vaillant_warmwasser|temperature_setpoint": ["Warmwasser Soll", "--chart-2", true],
      "vaillant_zone_1|temperature": ["Zone Ist", "--chart", false],
      "vaillant_zone_1|temperature_setpoint": ["Zone Soll", "--chart", true],
      "vaillant_aussenfuehler|temperature": ["Außen", "--chart-3", false]
    };
    var ts = v.reihen.filter(function (s) { return names[s.key]; }).map(function (s) {
      var n = names[s.key]; return { name: n[0], punkte: s.punkte, vorher: s.vorher, stufig: s.stufig, color: css(n[1]), dash: n[2], width: n[2] ? 1.5 : 2 };
    });
    lineChart($("wp-chart-temp"), ts, chartOpt(v, { unit: "°C", minSpan: 5, label: "Temperaturen Wärmepumpe" }));
    legend($("wp-legend-temp"), ts);
  }

  // ------------------------------------------------------------------ Reiter 3
  function renderShelly(sh) {
    $("sh-on").textContent = "– von " + sh.geraete.length;
    $("sh-devs").innerHTML = sh.geraete.map(function (d) {
      return '<div class="dev"><div class="nm">' + esc(d.zweck || d.name) + "</div>" +
        (d.zweck ? '<div class="row"><span>Gerät</span><span>' + esc(d.name) + "</span></div>" : "") +
        '<div class="row"><span>Modell</span><span>' + esc(d.modell || "–") + "</span></div>" +
        '<div class="row"><span>Adresse</span><span class="num">' + esc(d.ip || "–") + "</span></div>" +
        '<div class="row"><span>Firmware</span><span>' + esc(d.firmware || "–") + "</span></div>" +
        '<div class="row"><span>Stromkreis</span><span>' + esc(d.stromkreis || "offen") + "</span></div>" +
        (d.befund ? '<div class="row befund"><span>' + esc(d.befund) + "</span></div>" : "") + "</div>";
    }).join("") || '<div class="empty">Noch keine Geräte im Inventar.</div>';
  }

  // ------------------------------------------------------------------ Laden
  function getJSON(url) {
    return fetch(url, { cache: "no-store" }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.fehler || "HTTP " + r.status); return j; });
    });
  }
  function loadLage() {
    return getJSON("api/lage").then(function (l) {
      state.lage = l; state.lastOk = new Date();
      $("fehler").hidden = true;
      renderHead(l); renderFb(l.fb); renderWp(l.wp); renderShelly(l.shelly);
    }).catch(function (e) {
      $("fehler").hidden = false;
      $("fehler").textContent = "Keine aktuellen Daten vom Leitstand-Dienst (" + e.message + ")" +
        (state.lastOk ? " – angezeigte Werte stammen von " + fmtTime.format(state.lastOk) + " Uhr." : ".");
      $("gesamt").className = "dot stoerung";
    });
  }
  function refreshMs(sp) { return SPANNE_MS[sp] <= 108e5 ? 6e4 : 3e5; }   // kurze Spannen jede Minute neu
  function loadVerlauf(bereich, force) {
    var key = bereich + "|" + state.spanne[bereich], cached = state.verlauf[key];
    var render = bereich === "fb" ? renderFbCharts : renderWpCharts;
    if (cached && !force && Date.now() - cached._geladen < refreshMs(state.spanne[bereich])) { render(); return; }
    getJSON("api/verlauf?bereich=" + bereich + "&spanne=" + state.spanne[bereich]).then(function (v) {
      v._geladen = Date.now(); state.verlauf[key] = v;
      if (key === bereich + "|" + state.spanne[bereich]) render();
    }).catch(function () { /* Kopfzeile zeigt die Störung */ });
  }

  // ------------------------------------------------------------------ Reiter + Bedienung
  var tabs = [].slice.call(document.querySelectorAll(".tab"));
  function show(name, push) {
    state.tab = name;
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-hash") === name;
      t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    });
    store("ems-tab", name);
    if (push && location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
    if (name === "fb" || name === "wp") loadVerlauf(name);
  }
  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { show(t.getAttribute("data-hash"), true); });
    t.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      var n = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      show(n.getAttribute("data-hash"), true); n.focus();
    });
  });
  [].forEach.call(document.querySelectorAll("[data-range]"), function (box) {
    var b = box.getAttribute("data-range");
    box.setAttribute("role", "group"); box.setAttribute("aria-label", "Zeitspanne");
    box.innerHTML = SPANNEN.map(function (s) {
      return '<button class="seg" data-s="' + s[0] + '" aria-pressed="' + (state.spanne[b] === s[0]) + '">' + s[1] + "</button>";
    }).join("");
    box.addEventListener("click", function (e) {
      var s = e.target.getAttribute && e.target.getAttribute("data-s");
      if (!s) return;
      state.spanne[b] = s; store("ems-spanne-" + b, s);
      [].forEach.call(box.children, function (c) { c.setAttribute("aria-pressed", c.getAttribute("data-s") === s); });
      if (b === "raum") loadRaum(); else loadVerlauf(b);
    });
  });
  $("fb-rooms").addEventListener("click", function (e) {
    var tr = e.target.closest("tr.pick"); if (tr) openRoom(tr.getAttribute("data-raum"));
  });
  $("fb-rooms").addEventListener("keydown", function (e) {
    var tr = e.target.closest("tr.pick"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openRoom(tr.getAttribute("data-raum")); }
  });
  $("raum-zu").addEventListener("click", closeRoom);
  $("raum-dlg").addEventListener("click", function (e) { if (e.target === $("raum-dlg")) closeRoom(); });   // Klick auf den Hintergrund
  window.addEventListener("hashchange", function () { var h = location.hash.slice(1); if (h && h !== state.tab && $("p-" + h)) show(h); });
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var rerender = function () { if (state.lage) { renderFbCharts(); renderWpCharts(); renderRaumCharts(); } };
    if (mq.addEventListener) mq.addEventListener("change", rerender);
  }

  var h0 = location.hash.slice(1), start = $("p-" + h0) ? h0 : store("ems-tab");
  show($("p-" + start) ? start : "fb");
  loadLage().then(function () { loadVerlauf(state.tab === "shelly" ? "fb" : state.tab); });
  setInterval(loadLage, LAGE_MS);
  setInterval(function () {
    if (state.tab !== "shelly") loadVerlauf(state.tab);
    if ($("raum-dlg").open) loadRaum();
  }, 6e4);
})();
