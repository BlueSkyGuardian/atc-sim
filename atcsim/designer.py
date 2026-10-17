"""Back end of the visual scenario designer (static/designer.html).

compile()  - build the world from a scenario being edited: errors, lint warnings, every aircraft's planned
             trajectory (filed route at filed level, wind included) and the conflicts those plans contain.
preview()  - fast-time run (passive or naive_approve): real trajectories, losses, pilot requests.
weather()  - reflectivity image and cell list for a weather spec at a given time.
save()     - write the scenario to scenarios/<name>.json.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
from collections import OrderedDict
from typing import Dict, List, Optional

import numpy as np

from .aircraft import level_parity_ok
from .scenario import SCENARIO_DIR, ScenarioError, _airspace_for, _route_time_s, build_world
from .weather import Weather
from .world import in_rvsm_band, pair_minima

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


# --------------------------------------------------------------------------- planned trajectories
def _plan(ac, env, duration: float) -> List[list]:
    """[[t, x, y], ...] along the filed route at the filed level, from appearance to the exit point."""
    wps = env.waypoints
    pts = [wps[n] for n in ac.route]
    mode, val = ac.speed_target(ac.fl * 100.0)
    tas = ac.perf.tas_for(mode, val, ac.fl * 100.0)
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + float(np.hypot(b[0] - a[0], b[1] - a[1])))
    t0 = _route_time_s(pts, ac.start_along_nm, tas, env.weather, ac.fl)
    out = []
    # starting point (start_along may be mid-leg when the aircraft is already airborne at t=0)
    s = ac.start_along_nm
    for i in range(len(pts) - 1):
        if cum[i] <= s <= cum[i + 1]:
            k = (s - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
            out.append([round(ac.entry_time, 1), round(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * k, 2),
                        round(pts[i][1] + (pts[i + 1][1] - pts[i][1]) * k, 2)])
            break
    tv = _vertex_times(pts, tas, env.weather, ac.fl)
    for i, c in enumerate(cum):
        if c <= s + 1e-6:
            continue
        out.append([round(ac.entry_time + tv[i] - t0, 1), round(pts[i][0], 2), round(pts[i][1], 2)])
    return out


def _vertex_times(pts, tas, wx, fl) -> List[float]:
    """Cumulative flying time at every route vertex in one pass (same integration as scenario._route_time_s)."""
    import math
    out, t = [0.0], 0.0
    for a, b in zip(pts, pts[1:]):
        seg = float(np.hypot(b[0] - a[0], b[1] - a[1]))
        c = math.atan2(b[0] - a[0], b[1] - a[1])
        n = max(1, int(math.ceil(seg / 5.0)))
        for k in range(n):
            f = (k + 0.5) / n
            u, v = wx.at(a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]), fl, t)
            aw, cw = u * math.sin(c) + v * math.cos(c), u * math.cos(c) - v * math.sin(c)
            t += seg / n / (math.sqrt(max(tas ** 2 - cw ** 2, 1.0)) + aw) * 3600.0
        out.append(t)
    return out


def _interp(plan: List[list], ts: np.ndarray):
    p = np.asarray(plan, float)
    x = np.interp(ts, p[:, 0], p[:, 1])
    y = np.interp(ts, p[:, 0], p[:, 2])
    alive = (ts >= p[0, 0]) & (ts <= p[-1, 0])
    return x, y, alive


def _planned_conflicts(world, plans: Dict[str, list], step: float = 5.0) -> List[dict]:
    duration = world.duration_s
    ts = np.arange(0.0, duration + step, step)
    acs = [a for a in world.aircraft.values() if a.callsign in plans and len(plans[a.callsign]) >= 2]
    tracks = {a.callsign: _interp(plans[a.callsign], ts) for a in acs}
    out = []
    for i, a in enumerate(acs):
        for b in acs[i + 1:]:
            h, v = pair_minima(a, b, a.fl * 100.0, b.fl * 100.0, h_nm=world.sep_h)
            if abs(a.fl - b.fl) * 100.0 >= v - 1:
                continue
            xa, ya, la = tracks[a.callsign]
            xb, yb, lb = tracks[b.callsign]
            both = la & lb
            if not both.any():
                continue
            d = np.where(both, np.hypot(xa - xb, ya - yb), np.inf)
            bad = d < h
            if not bad.any():
                continue
            # one entry per contiguous episode (a merge followed by an overtake is two conflicts)
            idx = np.nonzero(bad)[0]
            splits = np.nonzero(np.diff(idx) > 1)[0]
            for run in np.split(idx, splits + 1):
                k = int(run[np.argmin(d[run])])
                inside = [bool(world.airspace.inside((xa[j], ya[j])) or world.airspace.inside((xb[j], yb[j])))
                          for j in (run[0], k)]
                out.append({"aircraft": [a.callsign, b.callsign], "fl": [a.fl, b.fl], "minimum_nm": h,
                            "start_s": float(ts[run[0]]), "end_s": float(ts[run[-1]]), "t_min_s": float(ts[k]),
                            "min_nm": round(float(d[k]), 2),
                            "at": [round(float((xa[k] + xb[k]) / 2), 1), round(float((ya[k] + yb[k]) / 2), 1)],
                            "inside": any(inside)})
    out.sort(key=lambda c: c["start_s"])
    return out


def _lint(scn: dict, world) -> List[str]:
    warn = []
    asp = world.airspace
    for a in world.aircraft.values():
        mx = a.perf.max_fl(*a.speed_target(a.fl * 100.0), a.mass)
        if a.fl > mx:
            warn.append(f"{a.callsign} ({a.icao}) filed FL{a.fl} but its max FL at this weight is FL{mx}")
        if not level_parity_ok(a.fl, a.southbound):
            warn.append(f"{a.callsign} FL{a.fl}: wrong-direction level for a "
                        f"{'south' if a.southbound else 'north'}bound flight")
        if not a.rvsm_approved and not a.military and in_rvsm_band(a.fl * 100.0):
            warn.append(f"{a.callsign}: non-RVSM civil aircraft filed in RVSM airspace (FL{a.fl})")
    for a in scn.get("aircraft", []):
        st = a.get("start")
        names = [n.upper() for n in a.get("route", [])]
        bad = asp.against_one_way(names)
        if bad:
            warn.append(f"{a['callsign']} flies {'/'.join(bad)} against its one-way direction")
        for p in asp.entry_exit_problems(names):
            if st and "exit-only" in p:
                continue                     # a free start position is its own entry
            warn.append(f"{a['callsign']}: {p}")
    for r in scn.get("requests", []):
        a = world.aircraft.get(str(r.get("callsign", "")).upper())
        if a and r.get("type") == "altitude" and not world.descending_to_destination(a, int(r["value"])) \
                and not level_parity_ok(int(r["value"]), a.southbound):
            warn.append(f"{a.callsign} requests FL{r['value']}: wrong-direction level")
        if a and not (0 <= float(r.get("time", 0)) <= world.duration_s):
            warn.append(f"{a.callsign}: request at t={r.get('time')} is outside the scenario")
    return warn


def compile(scn: dict) -> dict:
    """Build the scenario; return errors or plans + planned conflicts + warnings."""
    try:
        w = build_world(copy.deepcopy(scn))
    except (ScenarioError, KeyError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e) if not isinstance(e, KeyError) else f"missing field {e}"}
    plans, info = {}, {}
    for a in w.aircraft.values():
        try:
            plans[a.callsign] = _plan(a, w.env, w.duration_s)
        except Exception as e:                                  # pragma: no cover - defensive
            plans[a.callsign] = []
        info[a.callsign] = {
            "type": a.icao, "fl": a.fl, "route": [n for n in a.route if not n.startswith("@")],
            "entry": a.entry_wp, "exit": a.planned_exit, "appear_s": round(a.entry_time, 1),
            "entry_s": round(a.boundary_time, 1),
            "exit_s": plans[a.callsign][-1][0] if plans[a.callsign] else None,
            "southbound": a.southbound, "rvsm": a.rvsm_status, "military": a.military,
            "formation": a.formation["size"] if a.formation else None,
            "arrival": a.destination, "flight_plan": a.flight_plan,
            "route_before": list(a.route_before), "route_after": list(a.route_after),
        }
    return {"ok": True, "duration_s": w.duration_s, "separation_horizontal_nm": w.sep_h, "plans": plans, "aircraft": info,
            "planned_conflicts": _planned_conflicts(w, plans), "warnings": _lint(scn, w),
            "traffic_load": (load := _load(scn, w)),
            "ratings": _ratings({**scn, "traffic_load": load, "complexity": None, "density": None})}


def _ratings(scn) -> dict:
    from .scenario import ratings
    return ratings(scn)


def _load(scn, w) -> dict:
    from .capacity import traffic_load
    try:
        return traffic_load(scn, w)
    except Exception:                                           # pragma: no cover - defensive
        return {}


# --------------------------------------------------------------------------- fast-time preview
def preview(scn: dict, policy: str = "passive", every_s: float = 10.0) -> dict:
    from .evaluate import run
    try:
        r = run(copy.deepcopy(scn), policy, trace=True)
    except (ScenarioError, KeyError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    tracks = {}
    for cs, pts in r["trace"].items():
        keep, last = [], -1e9
        for p in pts:
            if p[0] - last >= every_s or p is pts[-1]:
                keep.append([p[0], round(p[1], 2), round(p[2], 2), int(round(p[3] / 100.0)), int(bool(p[4]))])
                last = p[0]
        tracks[cs] = keep
    m = r["metrics"]
    return {
        "ok": True, "policy": policy, "tracks": tracks,
        "losses": [{"aircraft": l["aircraft"], "start_s": l["start_s"], "end_s": l.get("end_s"),
                    "min_nm": l.get("min_horizontal_nm"), "min_ft": l.get("min_vertical_ft")}
                   for l in m["separation"]["losses"]],
        "pilot_requests": r["wx_requests"],
        "scenario_requests": [[q["time_s"], q["callsign"], q["phraseology"], q["status"]] for q in r["requests"]
                              if q["origin"] == "scenario"], "wx_penetrations": r["wx_penetrations"],
        "infringements": r["infringed"], "rvsm_violations": r["rvsm_violations"],
        "wrong_direction_exits": r["wrong_direction_exits"], "left_sector": r["left_sector"],
        "rejected": r["rejected"],
        "sequencing": {k: m["sequencing"].get(k) for k in ("level_violations", "spacing_violations")},
    }


# --------------------------------------------------------------------------- weather for an edited spec
_WX: "OrderedDict[str, Weather]" = OrderedDict()


def _weather_for(scn: dict) -> Weather:
    asp = _airspace_for(scn)
    spec = scn.get("weather") or {}
    dur = float(scn.get("duration_s", 1800))
    key = hashlib.md5(json.dumps([spec, dur, asp.id], sort_keys=True).encode()).hexdigest()
    if key in _WX:
        _WX.move_to_end(key)
        return _WX[key]
    span = max(asp.bbox[2] - asp.bbox[0], asp.bbox[3] - asp.bbox[1])
    wx = Weather(spec, dur, domain_nm=384.0 if span <= 300 else 768.0)
    b = asp.bbox
    wx.view_bbox = (b[0] - 30.0, b[1] - 30.0, b[2] + 30.0, b[3] + 30.0) if span > 300 else (-100.0, -100.0, 100.0, 100.0)
    _WX[key] = wx
    while len(_WX) > 6:
        _WX.popitem(last=False)
    return wx


def weather(scn: dict, at_s: float, product: str = "composite", fl: float = 350.0) -> dict:
    wx = _weather_for(scn)
    bb = wx.view_bbox
    span = bb[2] - bb[0]
    res = 3.0 if span > 250 else 2.0
    if product == "cappi":
        xs, ys, z = wx.cappi(fl, at_s, bb, res)
    else:
        v = wx.volume(at_s, bb, res, 20)
        xs, ys, z = v["xs"], v["ys"], v["composite"]
    enc = np.where(z > -9.9, np.clip((z + 10) * 2, 1, 255), 0)
    summ = wx.summary(at_s)
    return {"bbox": list(bb), "nx": len(xs), "ny": len(ys), "sim_time_s": at_s, "product": product,
            "encoding": "uint8_b64", "scale": "dbz = v/2 - 10, v=0 no echo",
            "data": base64.b64encode(np.asarray(enc, np.uint8).tobytes()).decode(),
            "cells": summ.get("convective_cells", summ.get("cells", [])),
            "line": getattr(wx, "line", None),
            "steering": summ.get("storm_motion")}


# --------------------------------------------------------------------------- persistence
def raw(name: str) -> dict:
    p = os.path.join(SCENARIO_DIR, os.path.basename(name) + ".json")
    if not os.path.exists(p):
        raise ScenarioError(f"scenario {name!r} not found")
    with open(p) as f:
        return json.load(f)


def save(name: str, scn: dict, overwrite: bool = False) -> dict:
    name = name.strip()
    if not NAME_RE.match(name):
        raise ScenarioError("name: lower-case letters, digits, '_' or '-' (2-64 characters)")
    p = os.path.join(SCENARIO_DIR, name + ".json")
    if os.path.exists(p) and not overwrite:
        raise FileExistsError(f"scenario {name} exists")
    c = compile(scn)
    if not c["ok"]:
        raise ScenarioError(c["error"])
    out = dict(scn)
    out["name"] = name
    if c.get("traffic_load"):
        out["traffic_load"] = c["traffic_load"]                # flights in the first hour against the capacity
    for k in ("complexity", "density"):                        # 1-5, or not set (estimated from the content)
        v = out.get(k)
        if v in (None, ""):
            out.pop(k, None)
        elif not (isinstance(v, (int, float)) and 1 <= int(v) <= 5):
            raise ScenarioError(f"{k}: 1 to 5")
        else:
            out[k] = int(v)
    order = ["name", "title", "author", "complexity", "density", "traffic_load", "description", "design_notes", "airspace", "control", "duration_s", "pilot_delay_s",
             "weather", "areas", "aircraft", "requests", "events", "expected"]
    out = {**{k: out[k] for k in order if k in out}, **{k: v for k, v in out.items() if k not in order}}
    with open(p, "w") as f:
        json.dump(out, f, indent=1)
    return {"saved": name, "path": os.path.relpath(p), "warnings": c["warnings"],
            "planned_conflicts": len(c["planned_conflicts"])}
