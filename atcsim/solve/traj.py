"""Piecewise-linear 4D trajectories: the system's prediction of each aircraft (as the conflict detection makes it:
route legs at the ground speed, climbs and descents at the present or a nominal rate, then level), and the same with
a clearance applied.

A trajectory is breakpoints t (s, from now, 0 .. horizon) with the position x, y (NM) and altitude z (ft) at each;
between two breakpoints the aircraft moves in a straight line at constant speed and vertical speed, so the closest
point of approach of two trajectories is exact leg by leg (cpa.py).

Clearances take time to act: a speed change after SPEED_DELAY_S, a turn after a delay growing with its angle, a level
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


def _horizontal(pts: List[Tuple[float, float]], gs0: float, gs1: float, t_switch: float, H: float,
                straight_brg: Optional[float]) -> Tuple[List[float], List[float], List[float]]:
    """Walk a polyline (then straight on along its last bearing, or along straight_brg when it has one point) at
    gs0 until t_switch then gs1 (kt). Breakpoints at the vertices, the speed change and the horizon."""
    P = [np.array(p, dtype=float) for p in pts]
    if len(P) == 1:
        P.append(P[0] + _unit(straight_brg if straight_brg is not None else 0.0) * 1000.0)
    else:
        d = P[-1] - P[-2]
        n = float(np.hypot(*d))
        if n > 1e-9:
            P.append(P[-1] + d / n * 1000.0)
    ts, xs, ys = [0.0], [P[0][0]], [P[0][1]]
    t, pos = 0.0, P[0].copy()
    for k in range(1, len(P)):
        seg = P[k] - pos
        L = float(np.hypot(*seg))
        if L < 1e-9:
            continue
        u = seg / L
        while L > 1e-9 and t < H:
            gs = gs0 if t < t_switch else gs1
            v = gs / 3600.0
            t_end = t + L / v
            nxt = t_switch if t < t_switch < t_end else min(t_end, H)
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


def _vertical(alt0: float, cfl_ft: float, vs_fpm: float, delay: float, H: float) -> Tuple[List[float], List[float]]:
    dz = cfl_ft - alt0
    if abs(dz) < 50:
        return [0.0, H], [alt0, alt0]
    rate = abs(vs_fpm) if vs_fpm * dz > 0 and abs(vs_fpm) > 300 and delay <= 0 else (1500.0 if dz > 0 else 2000.0)
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
          direct: Optional[str] = None, cfl: Optional[int] = None) -> Traj:
    """The predicted trajectory of aircraft a over H seconds, with an optional clearance."""
    from .. import atctools
    ent = dict(w.entries.get(a.callsign, {}))
    if cfl is not None:
        ent["cfl"] = int(cfl)
    pts, hdg = atctools._path(w, a, ent, None, direct.upper() if direct else None)
    gs0 = a.gs if a.gs > 100 else max(a.tas, 150.0)          # the ground speed (the wind included)
    # a speed change changes the true airspeed; the wind stays: the ground speed changes by as much
    gs1 = max(100.0, gs0 + a.tas * ((atctools.speed_factor(a, speed) if speed else 1.0) - 1.0))
    t_sw = SPEED_DELAY_S if speed else 1e9
    if heading is not None:
        trk = a.trk
        turn = abs((float(heading) - trk + 180) % 360 - 180)
        t_turn = PILOT_DELAY_S + turn / TURN_RATE_DPS / 2          # half the turn on the old track, half on the new
        p0 = np.array(a.pos, dtype=float)
        p1 = p0 + _unit(trk) * gs0 / 3600.0 * t_turn
        if for_s is None:
            pts, hdg = [tuple(p0), tuple(p1), tuple(p1 + _unit(float(heading)) * 1000.0)], None
        else:
            p2 = p1 + _unit(float(heading)) * gs0 / 3600.0 * for_s
            ahead = route_ahead(w, a)
            rejoin = ahead[1:] if len(ahead) > 1 else ahead
            pts, hdg = [tuple(p0), tuple(p1), tuple(p2)] + [w.waypoints[p] for p in rejoin], None
    th, xh, yh = _horizontal(pts, gs0, gs1, t_sw, H, hdg)
    cfl_ft = atctools._system_cfl(w, a, ent)
    lvl_delay = PILOT_DELAY_S if cfl is not None and abs(cfl * 100 - a.cleared_alt_ft) > 50 else 0.0
    tv, zv = _vertical(a.alt_ft, cfl_ft, a.vs_fpm, lvl_delay, H)
    return _merge(a.callsign, th, xh, yh, tv, zv, H, bool(a.non_rvsm() if callable(a.non_rvsm) else a.non_rvsm), bool(getattr(a, "formation", None)))


def build_all(w, H: float = 600.0) -> dict:
    return {a.callsign: build(w, a, H) for a in w.aircraft.values() if a.active}
