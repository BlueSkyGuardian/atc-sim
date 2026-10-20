"""The situation as the controller's screen shows it, written as compact text for a language model.

observe(w, opts) -> (text, struct). The struct is what was shown, kept with the decision (experience data).
Options (agent profile): conflicts (MTCD), weather, inbound, routes, max_aircraft.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

DEFAULTS = {"conflicts": True, "weather": True, "inbound": True, "routes": True, "max_aircraft": 40}


def _t(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _dist(w, a: dict, wp: str):
    p = w.waypoints.get(wp)
    if not p:
        return None
    return math.hypot(p[0] - a["x_nm"], p[1] - a["y_nm"])


def _ac_line(w, a: dict, ent: dict, co: dict, opts: dict) -> Tuple[str, dict]:
    cs = a["callsign"]
    vs = a.get("vertical_speed_fpm") or 0
    trend = "↑" if vs > 200 else "↓" if vs < -200 else "="
    lvl = f"FL{a['fl']:03d}{trend}"
    if a.get("cleared_fl") != a.get("fl") or vs:
        lvl += f" cleared FL{a['cleared_fl']:03d}"
    if a.get("speed_mode") == "mach":
        spd = f"M{int(round((a.get('mach') or 0) * 100)):02d}"
    else:
        spd = f"{a.get('ias_kt')}kt"
    nav = a.get("nav_mode")
    if nav == "heading":
        lat = f"heading {int(a.get('assigned_heading') or a['heading']):03d}"
    elif nav == "wxdev":
        lat = "deviating for weather"
    else:
        route = [p for p in (a.get("route") or []) if not str(p).startswith("@")]
        nxt = a.get("next_waypoint") or (route[0] if route else None)
        d = _dist(w, a, nxt) if nxt else None
        lat = f"direct {nxt}" + (f" {d:.0f}NM" if d is not None else "") if nxt else "own navigation"
        if opts.get("routes") and route:
            i = route.index(nxt) if nxt in route else 0
            lat += " then " + " ".join(route[i + 1:i + 5]) if len(route) > i + 1 else ""
    fp = a.get("flight_plan") or {}
    exitp = co.get("next_unit")
    xfl = co.get("xfl")
    parts = [cs, a.get("type", ""), lvl, f"trk {int(a['track_deg']):03d} gs {a['gs_kt']}", spd, lat]
    if fp.get("destination"):
        parts.append(f"to {fp.get('destination')}")
    if xfl and exitp:
        parts.append(f"exit FL{xfl:03d} to {exitp}")
    if a.get("weather_ahead_nm") is not None and opts.get("weather"):
        parts.append(f"WEATHER {a['weather_ahead_nm']:.0f}NM ahead")
    if a.get("emergency"):
        parts.append(f"EMERGENCY {a['emergency']}")
    if ent:
        e = " ".join(f"{k.upper()} {v}" for k, v in ent.items() if k not in ("t",) and v not in (None, ""))
        if e:
            parts.append(f"[entered: {e}]")
    st = {"callsign": cs, "type": a.get("type"), "fl": a["fl"], "cleared_fl": a.get("cleared_fl"), "vs": vs,
          "trk": int(a["track_deg"]), "gs": a["gs_kt"], "x": round(a["x_nm"], 1), "y": round(a["y_nm"], 1),
          "nav": nav, "next": a.get("next_waypoint"), "assumed": a.get("assumed"), "on_frequency": a.get("on_frequency"),
          "xfl": xfl, "dest": fp.get("destination")}
    return " · ".join(str(p) for p in parts if p), st


def observe(w, opts: Dict = None) -> Tuple[str, dict]:
    from .. import atctools
    o = dict(DEFAULTS, **(opts or {}))
    st = w.state()
    sep_h = st.get("separation_minimum_nm") or 5
    coord, entries = st.get("coord") or {}, st.get("entries") or {}
    acs = [a for a in st["aircraft"] if a.get("status") == "active"]
    mine = [a for a in acs if a.get("assumed")]
    calling = [a for a in acs if a.get("on_frequency") and not a.get("assumed")]
    lines = [f"TIME T+{_t(w.t)} (exercise {_t(w.duration_s)}), UTC {st.get('utc', '')}",
             f"SEPARATION {sep_h:g} NM or 1000 ft (2000 ft for non-RVSM aircraft in RVSM airspace). "
             f"Levels you can assign: FL100-FL{w.upper_fl}."]
    struct = {"t": round(w.t, 1), "mine": [], "calling": [], "requests": [], "conflicts": [], "losses": []}
    lines.append(f"\nYOUR AIRCRAFT (assumed) — {len(mine)}:")
    for a in mine[:o["max_aircraft"]]:
        txt, s = _ac_line(w, a, entries.get(a["callsign"], {}), coord.get(a["callsign"], {}), o)
        lines.append("- " + txt)
        struct["mine"].append(s)
    if not mine:
        lines.append("- none")
    lines.append(f"\nCALLING YOU (on frequency, not yet assumed: assume them) — {len(calling)}:")
    for a in calling[:o["max_aircraft"]]:
        txt, s = _ac_line(w, a, {}, coord.get(a["callsign"], {}), o)
        lines.append("- " + txt)
        struct["calling"].append(s)
    if not calling:
        lines.append("- none")
    if o.get("inbound"):
        inb = st.get("inbound") or []
        if inb:
            lines.append(f"\nINBOUND (not on frequency yet) — {len(inb)}:")
            for i in sorted(inb, key=lambda i: i.get("time_to_entry_s") or 0)[:12]:
                lines.append(f"- {i['callsign']} {i.get('type', '')} FL{i.get('fl', 0):03d} entering at {i.get('entry_waypoint')}"
                             f" in {_t(i.get('time_to_entry_s') or 0)}, route {' '.join(i.get('route') or [])}"
                             + (f" to {i['destination']}" if i.get("destination") else ""))
    reqs = [r for r in st.get("pending_requests") or [] if r.get("kind") != "checkin"]
    lines.append(f"\nPILOT REQUESTS waiting for your answer — {len(reqs)}:")
    for r in reqs:
        lines.append(f"- {r['id']}: {r['phraseology']}" + (f" ({r['reason']})" if r.get("reason") else "")
                     + f", waiting {_t(w.t - r['time_s'])}")
        struct["requests"].append({"id": r["id"], "callsign": r["callsign"], "type": r["type"], "value": r.get("value"),
                                   "reason": r.get("reason"), "age_s": round(w.t - r["time_s"])})
    if not reqs:
        lines.append("- none")
    if o.get("conflicts"):
        relevant = {a["callsign"] for a in mine + calling}
        cf = [c for c in atctools.mtcd(w) if set(c["aircraft"]) & relevant][:12]
        lines.append(f"\nPREDICTED CONFLICTS (system trajectories, next {int(atctools.look_ahead(w) // 60)} min) — {len(cf)}:")
        for c in cf:
            a, b = c["aircraft"]
            lines.append(f"- {a} × {b}: in {_t(c['time_to_conflict_s'])}, closest {c['min_horizontal_nm']} NM / "
                         f"{c['vertical_ft']} ft (levels {c['levels'][0]}/{c['levels'][1]})")
            struct["conflicts"].append({"aircraft": [a, b], "in_s": c["time_to_conflict_s"], "min_nm": c["min_horizontal_nm"],
                                        "vertical_ft": c["vertical_ft"]})
        if not cf:
            lines.append("- none")
    los = st.get("current_losses") or []
    if los:
        lines.append("\nLOSS OF SEPARATION NOW: " + "; ".join(" × ".join(p.get("aircraft", [])) for p in los))
        struct["losses"] = [p.get("aircraft") for p in los]
    areas = [a for a in st.get("areas") or [] if a.get("active")]
    if areas:
        lines.append("\nACTIVE AREAS (keep traffic out): " + "; ".join(
            f"{a.get('id')} {a.get('kind', '')} FL{a.get('lower_fl', '')}-{a.get('upper_fl', '')}" for a in areas))
    return "\n".join(lines), struct
