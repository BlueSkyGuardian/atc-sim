"""Controller tools on top of the simulation:

* MTCD (medium-term conflict detection): the system trajectory of every aircraft (its flight-plan route from the
  present position, or the heading / direct the controller entered, and its cleared level - the level entered by the
  controller in a connected exercise) is projected 20 minutes ahead; pairs that would come within the separation
  minima are listed long before the short-term conflict alert (STCA, 3 minutes, straight lines) goes off.
  A probe answers "what if": the same check for one aircraft with another level, heading or direct.
* Special tracks and monitoring alerts, visual only: NRVSM, military, non-standard level, route deviation (route
  adherence monitoring), level bust (cleared level adherence monitoring).
* Flight plans: the list, and one plan with its estimates (ETO at each point ahead, entry / exit coordination).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

from .aircraft import level_parity_ok
from .flightplan import fields, hhmm
from .geometry import bearing, dist

MTCD_LOOK_S = 1200.0
MTCD_STEP_S = 20.0
RVSM_LO, RVSM_HI = 29000.0, 41000.0
RDEV_NM = 3.0            # route adherence: further than this from the flight-plan route, with no cleared deviation
CLAM_FT = 300.0          # cleared level adherence


def _system_cfl(w, a, ent: dict) -> float:
    """Cleared level known to the system: the controller's entry; in a connected exercise without an entry, the
    coordinated level (the one it had when it appeared), whatever the pilot does; solo, the aircraft's own."""
    if ent.get("cfl") is not None:
        return float(ent["cfl"]) * 100.0
    if getattr(w, "multi", False):
        base = w.__dict__.setdefault("_sys_cfl", {})
        return base.setdefault(a.callsign, a.cleared_alt_ft)
    return a.cleared_alt_ft


def system_cfls(w) -> Dict[str, int]:
    """Connected exercise: the cleared level the system knows for each aircraft (entered, else coordinated)."""
    if not getattr(w, "multi", False):
        return {}
    return {a.callsign: int(round(_system_cfl(w, a, w.entries.get(a.callsign, {})) / 100))
            for a in w.aircraft.values() if a.active or a.outside}


def _path(w, a, ent: dict, heading: Optional[float] = None, direct: Optional[str] = None) -> Tuple[List[tuple], Optional[float]]:
    """Polyline the system expects the aircraft to fly (and a straight-line heading, if any)."""
    wps = w.waypoints
    if heading is None and direct is None:
        if ent.get("heading"):
            heading = float(ent["heading"])
        elif ent.get("direct"):
            direct = ent["direct"]
        elif ent.get("wx"):
            heading = a.trk                         # cleared weather deviation: present track
        elif not getattr(w, "multi", False) and a.nav_mode != "lnav":
            heading = a.trk                         # solo: the instructions given are the system's knowledge
    if heading is not None:
        return [a.pos], heading
    rest = list(a.route[a.next_index:]) + list(a.route_after)
    if direct is not None and direct in wps:
        if direct in rest:
            rest = rest[rest.index(direct):]
        else:
            rest = [direct]
    pts = [a.pos] + [wps[n] for n in rest if n in wps and not n.startswith("@")]
    return pts, None


def _sample(a, pts: List[tuple], heading: Optional[float], times: np.ndarray, alt0: float, cfl: float) -> np.ndarray:
    gs = max(a.gs, a.tas, 150.0)
    d = gs * times / 3600.0
    out = np.zeros((len(times), 3))
    if heading is not None or len(pts) < 2:
        h = math.radians(heading if heading is not None else a.trk)
        out[:, 0] = a.x + d * math.sin(h)
        out[:, 1] = a.y + d * math.cos(h)
    else:
        seg = np.array([dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)])
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        P = np.array(pts)
        last_brg = math.radians(bearing(pts[-2], pts[-1]))
        for k, dk in enumerate(d):
            if dk >= cum[-1]:
                extra = dk - cum[-1]
                out[k, 0] = P[-1, 0] + extra * math.sin(last_brg)
                out[k, 1] = P[-1, 1] + extra * math.cos(last_brg)
            else:
                i = int(np.searchsorted(cum, dk, side="right") - 1)
                f = (dk - cum[i]) / seg[i] if seg[i] > 0 else 0.0
                out[k, :2] = P[i] + f * (P[i + 1] - P[i])
    # vertical: towards the cleared level at the present rate, else a nominal 1500 ft/min up / 2000 down
    dz = cfl - alt0
    if abs(dz) < 50:
        out[:, 2] = alt0
    else:
        rate = abs(a.vs_fpm) if a.vs_fpm * dz > 0 and abs(a.vs_fpm) > 300 else (1500.0 if dz > 0 else 2000.0)
        z = alt0 + np.sign(dz) * rate * times / 60.0
        out[:, 2] = np.minimum(z, cfl) if dz > 0 else np.maximum(z, cfl)
    return out


def _vmin(za: np.ndarray, zb: np.ndarray, non_rvsm: bool, formation: bool) -> np.ndarray:
    if formation:
        return np.full_like(za, 2000.0)
    lo, hi = np.minimum(za, zb), np.maximum(za, zb)
    two = (lo >= RVSM_LO - 50) & ((hi > RVSM_HI + 50) | non_rvsm)
    return np.where(two, 2000.0, 1000.0)


def _candidates(w) -> list:
    return [a for a in w.aircraft.values() if a.active]


def _trajectories(w, acs, times, override: Optional[dict] = None) -> Dict[str, np.ndarray]:
    out = {}
    for a in acs:
        ent = dict(w.entries.get(a.callsign, {}))
        hd, dc = None, None
        if override and override.get("callsign") == a.callsign:
            if override.get("cfl") is not None:
                ent["cfl"] = int(override["cfl"])
            if override.get("heading"):
                hd = float(override["heading"])
            if override.get("direct"):
                dc = str(override["direct"]).upper()
        pts, heading = _path(w, a, ent, hd, dc)
        out[a.callsign] = _sample(a, pts, heading, times, a.alt_ft, _system_cfl(w, a, ent))
    return out


def _pairs(w, acs, tr, times, only: Optional[str] = None) -> List[dict]:
    res = []
    by = {a.callsign: a for a in acs}
    css = sorted(tr)
    if not css:
        return res
    X = np.stack([tr[c] for c in css])                 # (N, T, 3)
    reach = 2 * 9.0 * times[-1] / 60.0                  # quick reject: max closure 2 * 540 kt
    n = len(css)
    if only:
        if only not in css:
            return res
        io = css.index(only)
        combos = [(min(io, j), max(io, j)) for j in range(n) if j != io]
    else:
        combos = [(i, j) for i in range(n) for j in range(i + 1, n)]
    asp = w.airspace
    for i, j in combos:
        ca = css[i]
        cb = css[j]
        A, B = by[ca], by[cb]
        if abs(A.x - B.x) > reach or abs(A.y - B.y) > reach:
            continue
        h = np.hypot(X[i, :, 0] - X[j, :, 0], X[i, :, 1] - X[j, :, 1])
        v = np.abs(X[i, :, 2] - X[j, :, 2])
        hmin = w.sep_h + max(float((A.formation or {}).get("extent_nm", 0) if A.formation else 0),
                             float((B.formation or {}).get("extent_nm", 0) if B.formation else 0))
        vm = _vmin(X[i, :, 2], X[j, :, 2], A.non_rvsm or B.non_rvsm, bool(A.formation or B.formation))
        hit = (h < hmin) & (v < vm)
        if not hit.any():
            continue
        k0 = int(np.argmax(hit))
        pa, pb = (X[i, k0, 0], X[i, k0, 1]), (X[j, k0, 0], X[j, k0, 1])
        if not any(asp.inside(q) or asp.distance_to_boundary(q) < 10 for q in (pa, pb)):
            continue                                # in another sector's airspace: not ours
        ks = np.nonzero(hit)[0]
        kmin = int(ks[np.argmin(h[ks])])
        res.append({"aircraft": [ca, cb], "time_to_conflict_s": float(times[k0]),
                    "duration_s": float(times[ks[-1]] - times[k0] + (times[1] - times[0])),
                    "min_horizontal_nm": round(float(h[kmin]), 1), "vertical_ft": int(round(float(v[kmin]), -2)),
                    "required_horizontal_nm": hmin, "required_vertical_ft": int(vm[kmin]),
                    "at": [[round(float(X[i, k0, 0]), 2), round(float(X[i, k0, 1]), 2)],
                           [round(float(X[j, k0, 0]), 2), round(float(X[j, k0, 1]), 2)]],
                    "levels": [int(round(X[i, k0, 2] / 100)), int(round(X[j, k0, 2] / 100))],
                    "current": bool(h[0] < hmin and v[0] < vm[0])})
    res.sort(key=lambda c: (c["time_to_conflict_s"], c["min_horizontal_nm"]))
    return res


def mtcd(w, look_s: float = MTCD_LOOK_S, step_s: float = MTCD_STEP_S) -> List[dict]:
    """Medium-term conflicts between the system trajectories, cached for 8 simulated seconds."""
    key = (int(w.t // 8), id(w.entries), sum(len(e) for e in w.entries.values()), len(w.entries),
           tuple(sorted((k, e.get("t")) for k, e in w.entries.items())))
    cache = getattr(w, "_mtcd_cache", None)
    if cache and cache[0] == key:
        return cache[1]
    acs = _candidates(w)
    times = np.arange(0.0, look_s + 1e-6, step_s)
    res = _pairs(w, acs, _trajectories(w, acs, times), times)
    w._mtcd_cache = (key, res)
    return res


def probe(w, callsign: str, cfl=None, heading=None, direct=None, levels: Optional[List[int]] = None) -> dict:
    """What-if for one aircraft: the conflicts it would have with another level / heading / direct (20 minutes)."""
    cs = callsign.upper()
    a = w.aircraft.get(cs)
    if a is None or not a.active:
        raise ValueError(f"{cs} is not on the picture")
    acs = _candidates(w)
    times = np.arange(0.0, MTCD_LOOK_S + 1e-6, MTCD_STEP_S)
    base = _trajectories(w, acs, times)
    out = {"callsign": cs}

    def run(ov):
        tr = dict(base)
        tr.update(_trajectories(w, [a], times, {"callsign": cs, **ov}))
        return [c for c in _pairs(w, acs, tr, times, only=cs) if cs in c["aircraft"]]
    if levels:
        out["levels"] = {}
        for fl in levels[:20]:
            cs_list = run({"cfl": int(fl)})
            out["levels"][str(int(fl))] = [{"with": [x for x in c["aircraft"] if x != cs][0],
                                            "in_s": c["time_to_conflict_s"], "min_nm": c["min_horizontal_nm"]}
                                           for c in cs_list]
    if cfl is not None or heading or direct:
        out["conflicts"] = run({"cfl": cfl, "heading": heading, "direct": direct})
    return out


# ------------------------------------------------------------------ special tracks / monitoring alerts
def _dist_to_polyline(p, pts) -> float:
    best = 1e9
    for a, b in zip(pts, pts[1:]):
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        f = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
        best = min(best, math.hypot(p[0] - ax - f * dx, p[1] - ay - f * dy))
    return best


def special_tracks(w) -> Dict[str, list]:
    """{callsign: [[code, severity, text], ...]} - severity: info | warn | alert. Visual alerts only."""
    out: Dict[str, list] = {}
    st = getattr(w, "_clam", None)
    if st is None:
        st = w._clam = {}
    multi = getattr(w, "multi", False)
    for a in w.aircraft.values():
        if not (a.active or (a.outside and a.assumed)):
            continue
        tags = []
        ent = w.entries.get(a.callsign, {})
        if a.rvsm_status != "approved" and not a.formation:
            inband = RVSM_LO - 50 <= a.alt_ft <= RVSM_HI + 50
            tags.append(["NRVSM", "warn" if inband else "info",
                         ("RVSM unable" if a.rvsm_status == "unable" else "not RVSM approved") + (" · in RVSM airspace" if inband else "")])
        if a.military:
            tags.append(["MIL", "info", "military" + (f" formation {a.formation['types']}" if a.formation else "")])
        # non-standard level: cleared or flown level against the semicircular rule (not for descents to destination)
        cfl_sys = _system_cfl(w, a, ent)
        fl_c = int(round(cfl_sys / 100))
        level = abs(a.vs_fpm) < 100
        fl_now = int(round(a.alt_ft / 100))
        if a.on_frequency or a.assumed:
            bad = None
            if level and fl_now % 10 and abs(a.alt_ft - cfl_sys) < 150:
                bad = f"FL{fl_now} is not a flight level of the table"
            elif fl_c >= 200 and not level_parity_ok(fl_c, a.southbound) and not w.descending_to_destination(a, fl_c):
                bad = f"FL{fl_c} is a {'northbound' if a.southbound else 'southbound'} level ({'south' if a.southbound else 'north'}bound flight)"
            if bad:
                tags.append(["NSL", "warn", "non-standard level: " + bad])
        # route deviation (route adherence monitoring): away from the flight-plan route with no deviation entered
        cleared_off = bool(ent.get("heading") or ent.get("wx")) or (not multi and a.nav_mode != "lnav")
        if a.active and a.on_frequency and not cleared_off:
            wps = w.waypoints
            dct = ent.get("direct") if ent.get("direct") in wps else None
            passed = dct is not None and dct in a.route[:a.next_index]
            if dct and not passed:
                tgt = wps[dct]
                off = abs(((a.trk - bearing(a.pos, tgt)) + 540) % 360 - 180)
                if w.t - float(ent.get("t", w.t)) > 90 and off > 30 and dist(a.pos, tgt) > 5:
                    tags.append(["RDEV", "warn", f"not flying direct {dct} (track {off:.0f}° off)"])
            else:
                if dct:                                  # direct point passed: its route onwards
                    names = a.route[a.route.index(dct):] + a.route_after
                elif multi:                              # the filed route
                    names = a.route + a.route_after
                else:                                    # solo: the route as amended by the instructions given
                    names = a.route[a.next_index:] + a.route_after
                pts = ([] if (dct or multi) else [a.leg_from]) + [wps[n] for n in names if n in wps]
                if len(pts) >= 2:
                    d = _dist_to_polyline(a.pos, pts)
                    if d > RDEV_NM:
                        tags.append(["RDEV", "warn", f"{d:.1f} NM off the flight-plan route"])
        # level bust (cleared level adherence monitoring): moving away from the cleared level, or levelled off at
        # another level for more than a minute, once the cleared level is known to the system
        if a.assumed:
            dz = a.alt_ft - cfl_sys
            s = st.setdefault(a.callsign, {"since": None, "cfl": cfl_sys})
            if s["cfl"] != cfl_sys:
                s.update(cfl=cfl_sys, since=None, set_t=w.t)
            settled = w.t - s.get("set_t", -1e9) > 60
            if abs(dz) > CLAM_FT and ((dz > 0 and a.vs_fpm > 300) or (dz < 0 and a.vs_fpm < -300)):
                tags.append(["LVL", "alert", f"level bust: {'above' if dz > 0 else 'below'} cleared FL{fl_c} and going away"])
            elif abs(dz) > CLAM_FT and level and settled:
                s["since"] = s["since"] if s["since"] is not None else w.t
                if w.t - s["since"] > 30:
                    tags.append(["LVL", "alert", f"levelled at FL{fl_now}, cleared FL{fl_c}"])
            else:
                s["since"] = None
        if tags:
            out[a.callsign] = tags
    return out


# ------------------------------------------------------------------ flight plans
def utc(w, t_s: Optional[float] = None) -> str:
    m = getattr(w, "start_utc_min", 720.0) + (w.t if t_s is None else t_s) / 60.0
    return f"{hhmm(m)[:2]}:{hhmm(m)[2:]}"


def _status(a) -> str:
    if a.gone:
        return "gone"
    if a.status == "scheduled":
        return "planned"
    if a.outside:
        return "transferred" if not a.assumed else "left, assumed"
    if a.assumed:
        return "assumed"
    if a.on_frequency:
        return "calling"
    return "inbound"


def flight_list(w) -> List[dict]:
    out = []
    for a in w.aircraft.values():
        fp = a.flight_plan or {}
        out.append({"callsign": a.callsign, "ssr": fp.get("ssr"), "type": a.icao, "wake": fp.get("wake"),
                    "dep": fp.get("departure"), "dest": fp.get("destination"), "rfl": fp.get("cruise"),
                    "entry": a.entry_wp, "eto_entry": utc(w, a.boundary_time), "entry_s": round(a.boundary_time),
                    "exit": a.planned_exit, "prev_unit": a.prev_unit, "next_unit": a.next_unit,
                    "status": _status(a), "fl": int(round(a.alt_ft / 100)) if a.status != "scheduled" else a.fl,
                    "rvsm": a.rvsm_status, "military": a.military})
    out.sort(key=lambda r: r["entry_s"])
    return out


def flight_detail(w, cs: str) -> dict:
    a = w.aircraft.get(cs.upper())
    if a is None:
        raise KeyError(cs)
    fp = a.flight_plan or {}
    wps = w.waypoints
    ent = w.entries.get(a.callsign, {})
    # estimates along the route ahead (planned speed before the aircraft is on the picture)
    if a.status == "scheduled":
        mode, val = a.speed_target(a.fl * 100.0)
        gs = a.perf.tas_for(mode, val, a.fl * 100.0)
        names = [n for n in a.route if not n.startswith("@")] + a.route_after
        pos, t0 = wps.get(a.entry_wp), a.boundary_time
        names = names[names.index(a.entry_wp):] if a.entry_wp in names else names
    else:
        gs = max(a.gs, 150.0)
        names = list(a.route[a.next_index:]) + list(a.route_after)
        pos, t0 = a.pos, w.t
    cops = getattr(w.airspace, "cop_points", set())
    etos, d = [], 0.0
    p = pos
    for n in names:
        if n not in wps or n.startswith("@"):
            continue
        d += dist(p, wps[n]) if p is not None else 0.0
        p = wps[n]
        if n in cops:
            continue
        etos.append({"point": n, "eto": utc(w, t0 + d / gs * 3600.0), "in_s": round(t0 - w.t + d / gs * 3600.0),
                     "dist_nm": round(d, 1),
                     "role": "entry" if n == a.entry_wp else "exit" if n == a.planned_exit else
                             ("next sector" if n in a.route_after else "")})
    exit_eto = next((e for e in etos if e["role"] == "exit"), None)
    from . import oldi
    xl, xe = oldi.xfl(w, a)
    return {"callsign": a.callsign, "status": _status(a), "utc": utc(w), "flight_plan": fp, "items": fields(fp),
            "message": fp.get("message", ""), "etos": etos,
            "coordination": {
                "entry": {"point": a.entry_wp, "eto": utc(w, a.boundary_time), "level": a.fl, "from": a.prev_unit},
                "exit": {"point": a.planned_exit, "eto": exit_eto["eto"] if exit_eto else None,
                         "level": xl, "xfl_entered": xe, "cfl": oldi.cfl(w, a), "to": a.next_unit,
                         "state": (getattr(w, "coord", {}).get(a.callsign) or {}).get("state")}},
            "oldi": oldi.messages(w, callsign=a.callsign),
            "correlation": {"ssr": fp.get("ssr"), "mode_s_address": fp.get("mode_s_address"),
                            "surveillance": fp.get("surveillance"), "track": a.status != "scheduled"},
            "entries": ent}
