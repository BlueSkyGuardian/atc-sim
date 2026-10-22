"""Piecewise-linear 4D trajectories: the system's prediction of each aircraft (as the conflict detection makes it:
route legs at the ground speed, climbs and descents at the present rate, else the aircraft's own climb or idle descent rate, then level), and the same with
a clearance applied.

A trajectory is breakpoints t (s, from now, 0 .. horizon) with the position x, y (NM) and altitude z (ft) at each;
between two breakpoints the aircraft moves in a straight line at constant speed and vertical speed, so the closest
point of approach of two trajectories is exact leg by leg (cpa.py).

A turn the aircraft is making (after passing a point, or towards a new direct) is modelled like a heading: half of
it flown on the present track. Clearances take time to act: a speed change after SPEED_DELAY_S, a turn after a delay growing with its angle, a level
change after the pilot's delay. Candidates:
  speed       "M74", "0.74", "280" (IAS kt)
  heading     a heading, flown for `for_s` seconds then direct to the route point after the next one (a delaying vector
              and the resume), or open (no resume) when for_s is None
  direct      a point (then the rest of the route after it)
  cfl         a level
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

SPEED_DELAY_S = 40.0          # until most of a speed change is achieved (a few kt/s)
TURN_RATE_DPS = 3.0           # standard rate turn
PILOT_DELAY_S = 6.0


@dataclass
class Traj:
    cs: str
    t: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    non_rvsm: bool = False
    formation: bool = False

    def at(self, tt) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        tt = np.asarray(tt, dtype=float)
        return np.interp(tt, self.t, self.x), np.interp(tt, self.t, self.y), np.interp(tt, self.t, self.z)


def _unit(deg: float) -> np.ndarray:
    r = math.radians(deg)
    return np.array([math.sin(r), math.cos(r)])


def _horizontal(pts: List[Tuple[float, float]], sched: List[Tuple[float, float]], H: float,
                straight_brg: Optional[float]) -> Tuple[List[float], List[float], List[float]]:
    """Walk a polyline (then straight on along its last bearing, or along straight_brg when it has one point) with a
    ground-speed schedule [(t_from, gs_kt), ...]. Breakpoints at the vertices, the speed changes and the horizon."""
    P = [np.array(p, dtype=float) for p in pts]
    if len(P) == 1:
        P.append(P[0] + _unit(straight_brg if straight_brg is not None else 0.0) * 1000.0)
    else:
        d = P[-1] - P[-2]
        n = float(np.hypot(*d))
        if n > 1e-9:
            P.append(P[-1] + d / n * 1000.0)
    sched = sorted(sched)
    switches = [t for t, _ in sched[1:]]

    def gs_at(t):
        g = sched[0][1]
        for t0, g0 in sched:
            if t + 1e-9 >= t0:
                g = g0
        return g
    ts, xs, ys = [0.0], [P[0][0]], [P[0][1]]
    t, pos = 0.0, P[0].copy()
    for k in range(1, len(P)):
        seg = P[k] - pos
        L = float(np.hypot(*seg))
        if L < 1e-9:
            continue
        u = seg / L
        while L > 1e-9 and t < H:
            v = gs_at(t) / 3600.0
            t_end = t + L / v
            nxt = min([x for x in switches if t + 1e-9 < x < t_end] + [t_end, H])
            step = (nxt - t) * v
            pos = pos + u * step
            L -= step
            t = nxt
            ts.append(t)
            xs.append(pos[0])
            ys.append(pos[1])
        if t >= H:
            break
    return ts, xs, ys


def normal_gs(a, gs0: float) -> float:
    """The ground speed the aircraft will have at its own speed (no speed assigned), at its present level."""
    saved = (a.spd_mach, a.spd_cas, a.spd_ias_now)
    try:
        a.spd_mach, a.spd_cas, a.spd_ias_now = None, None, False
        mode, val = a.speed_target(a.alt_ft)
        tas_n = a.perf.tas_for(mode, val, a.alt_ft)
    except Exception:  # noqa: BLE001
        return gs0
    finally:
        a.spd_mach, a.spd_cas, a.spd_ias_now = saved
    return max(100.0, gs0 + tas_n - a.tas)


def _along_to(pts: List[Tuple[float, float]], target: Tuple[float, float]) -> Optional[float]:
    """Distance along a polyline to the vertex nearest a point (NM), or None."""
    d, best = 0.0, None
    for i in range(1, len(pts)):
        d += float(np.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
        if float(np.hypot(pts[i][0] - target[0], pts[i][1] - target[1])) < 1.0:
            best = d
            break
    return best


def limit_left_s(w, a, kind: str, gs: float, pts=None) -> Optional[float]:
    """Seconds left before the limit of the aircraft's heading / speed clearance in force (None: no limit)."""
    L = (getattr(a, "clr_limits", None) or {}).get(kind)
    if not L:
        return None
    if "until_t" in L:
        return max(0.0, L["until_t"] - w.t)
    if "nm_left" in L:
        return max(0.0, L["nm_left"]) / max(gs, 100.0) * 3600.0
    if "until_wp" in L and L["until_wp"] in w.waypoints and pts:
        d = _along_to(pts, w.waypoints[L["until_wp"]])
        return None if d is None else d / max(gs, 100.0) * 3600.0
    return None


def _perf_rate(a, climbing: bool) -> Optional[float]:
    """The aircraft's own rate (ft/min) for a new climb or descent: its available climb rate, or its idle descent
    rate (speedbrakes out when it is high on its profile) - not a nominal figure: a descent at 3500 ft/min through
    another aircraft's level a minute earlier than a nominal 2000 ft/min would predict is a conflict missed."""
    try:
        if climbing:
            r = float(a.climb_rate_available())
        else:
            from ..aircraft import SPEEDBRAKE_FACTOR
            r = float(a.descent_rate_idle()) * (SPEEDBRAKE_FACTOR if getattr(a, "speedbrake", False) else 1.0)
        return r if r > 300 else None
    except Exception:  # noqa: BLE001
        return None


def _vertical(alt0: float, cfl_ft: float, vs_fpm: float, delay: float, H: float, a=None,
              rates: str = "expected", fixed_rate: Optional[float] = None) -> Tuple[List[float], List[float]]:
    """rates: "expected" (the aircraft's own), or the bounds of what it may do, "fast" (descents with speedbrakes)
    and "slow" (the lesser of its own and the nominal rate): a level change is checked with both bounds, the
    window in which it passes through another's level lying between theirs."""
    dz = cfl_ft - alt0
    if abs(dz) < 50:
        return [0.0, H], [alt0, alt0]
    nominal = 1500.0 if dz > 0 else 2000.0
    if fixed_rate:
        rate = fixed_rate                                   # an assigned rate the aircraft can hold (checked by the pilot)
    elif vs_fpm * dz > 0 and abs(vs_fpm) > 300 and delay <= 0:
        rate = abs(vs_fpm)
    else:
        own = (_perf_rate(a, dz > 0) if a is not None else None) or nominal
        if rates == "fast":
            from ..aircraft import SPEEDBRAKE_FACTOR
            rate = max(own, nominal) * (1.0 if dz > 0 else SPEEDBRAKE_FACTOR / (SPEEDBRAKE_FACTOR if getattr(a, "speedbrake", False) else 1.0))
        elif rates == "slow":
            rate = min(own, nominal)
        else:
            rate = own
    t_lv = delay + abs(dz) / rate * 60.0
    ts, zs = [0.0], [alt0]
    if delay > 0:
        ts.append(min(delay, H))
        zs.append(alt0)
    if t_lv < H:
        ts += [t_lv, H]
        zs += [cfl_ft, cfl_ft]
    else:
        ts.append(H)
        zs.append(alt0 + math.copysign(rate * (H - delay) / 60.0, dz))
    return ts, zs


def _merge(cs, th, xh, yh, tv, zv, H, non_rvsm, formation) -> Traj:
    T = np.unique(np.clip(np.concatenate([th, tv, [0.0, H]]), 0.0, H))
    return Traj(cs, T, np.interp(T, th, xh), np.interp(T, th, yh), np.interp(T, tv, zv), non_rvsm, formation)


def route_ahead(w, a) -> List[str]:
    return [p for p in list(a.route[a.next_index:]) + list(getattr(a, "route_after", []))
            if p in w.waypoints and not str(p).startswith("@")]


def build(w, a, H: float = 600.0, speed=None, heading: Optional[float] = None, for_s: Optional[float] = None,
          direct: Optional[str] = None, cfl: Optional[int] = None, rates: str = "expected", speed_for_s: Optional[float] = None,
          rate: Optional[float] = None) -> Traj:
    """The predicted trajectory of aircraft a over H seconds, with an optional clearance. A speed is held for
    speed_for_s (its limit) then the aircraft resumes its normal speed; a heading for for_s then the route; a level at
    an assigned rate (ft/min) when rate is given. The limits of the clearances in force are flown as well."""
    from .. import atctools
    ent = dict(w.entries.get(a.callsign, {}))
    if cfl is not None:
        ent["cfl"] = int(cfl)
    lims = getattr(a, "clr_limits", None) or {}
    gs0 = a.gs if a.gs > 100 else max(a.tas, 150.0)          # the ground speed (the wind included)
    lateral_new = heading is not None or direct is not None
    # a heading in force with its limit: on the present track until the limit, then its point (or the route)
    if not lateral_new and a.nav_mode == "heading" and "heading" in lims:
        left = limit_left_s(w, a, "heading", gs0)
        if left is not None:
            heading, for_s = float(a.trk), left
            then = lims["heading"].get("then")
            direct_after = then if then in w.waypoints else None
        else:
            direct_after = None
    else:
        direct_after = None
    pts, hdg = atctools._path(w, a, ent, None, direct.upper() if direct else None)
    # speed schedule: a new speed (with its limit), else a speed in force with its limit, else constant
    if speed:
        gs1 = max(100.0, gs0 + a.tas * (atctools.speed_factor(a, speed) - 1.0))
        sched = [(0.0, gs0), (SPEED_DELAY_S, gs1)]
        if speed_for_s is not None:
            sched.append((SPEED_DELAY_S + speed_for_s, normal_gs(a, gs1)))
    else:
        sched = [(0.0, gs0)]
        left = limit_left_s(w, a, "speed", gs0, pts if hdg is None else None) if "speed" in lims else None
        if left is not None:
            sched.append((left, normal_gs(a, gs0)))
    if heading is not None:
        trk = a.trk
        turn = abs((float(heading) - trk + 180) % 360 - 180)
        t_turn = (PILOT_DELAY_S if lateral_new else 0.0) + turn / TURN_RATE_DPS / 2   # half the turn on the old track
        p0 = np.array(a.pos, dtype=float)
        p1 = p0 + _unit(trk) * gs0 / 3600.0 * t_turn
        if for_s is None:
            pts, hdg = [tuple(p0), tuple(p1), tuple(p1 + _unit(float(heading)) * 1000.0)], None
        else:
            p2 = p1 + _unit(float(heading)) * gs0 / 3600.0 * for_s
            ahead = route_ahead(w, a)
            if direct_after:
                rejoin = ahead[ahead.index(direct_after):] if direct_after in ahead else [direct_after]
            else:
                rejoin = ahead[1:] if len(ahead) > 1 and lateral_new else ahead
            pts, hdg = [tuple(p0), tuple(p1), tuple(p2)] + [w.waypoints[p] for p in rejoin], None
    elif len(pts) >= 2:
        # a turn in progress or to come at once (past a point, a new direct): the track changes at a standard rate,
        # modelled as for a heading, half the turn on the present track; without it the prediction cuts the corner
        p0 = np.array(pts[0], dtype=float)
        d = np.array(pts[1], dtype=float) - p0
        L = float(np.hypot(*d))
        if L > 1e-6:
            brg = math.degrees(math.atan2(d[0], d[1])) % 360
            turn = abs((brg - a.trk + 180) % 360 - 180)
            lead = gs0 / 3600.0 * turn / TURN_RATE_DPS / 2
            if turn > 5 and lead < 0.8 * L:
                pts = [tuple(p0), tuple(p0 + _unit(a.trk) * lead)] + list(pts[1:])
    th, xh, yh = _horizontal(pts, sched, H, hdg)
    cfl_ft = atctools._system_cfl(w, a, ent)
    lvl_delay = PILOT_DELAY_S if cfl is not None and abs(cfl * 100 - a.cleared_alt_ft) > 50 else 0.0
    if rate is not None and cfl is not None:
        tv, zv = _vertical(a.alt_ft, cfl_ft, 0.0, lvl_delay, H, None, "fixed", fixed_rate=float(rate))
    else:
        tv, zv = _vertical(a.alt_ft, cfl_ft, a.vs_fpm, lvl_delay, H, a, rates)
    return _merge(a.callsign, th, xh, yh, tv, zv, H, bool(a.non_rvsm() if callable(a.non_rvsm) else a.non_rvsm), bool(getattr(a, "formation", None)))


def build_all(w, H: float = 600.0) -> dict:
    return {a.callsign: build(w, a, H) for a in w.aircraft.values() if a.active}
