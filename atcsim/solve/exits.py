"""Spacing at the exit (letter of agreement): aircraft leaving the sector by the same exit point at levels that are
not vertically separated must be at least exit_spacing_nm (10 NM) apart in trail when they cross it, before they are
transferred. Arrivals to a sequencing fix have their own agreement (arrivals.py) and are left out here.

From the predicted trajectories: the time each aircraft crosses its exit point (closest approach to it), its level
there, and for a pair at the same exit the distance of the one ahead from the point when the one behind crosses it.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from . import cpa, traj

H_EXIT = 1800.0        # trajectories this long, to reach the exits
ACT_S = 1200.0         # spacing problems reported (and solved) when the one behind is this close to the exit (20 min)


def required_nm(w) -> float:
    return float(getattr(w, "exit_spacing_nm", 10.0) or 10.0)


def exit_of(w, a) -> Optional[str]:
    """The exit point of an aircraft leaving the sector (None for an arrival handled by its sequencing fix)."""
    if a.destination and (a.arr or {}).get("fix"):
        return None
    try:
        x = a.exit_wp
    except Exception:  # noqa: BLE001
        return None
    return x if x in w.waypoints else None


def crossing(T, p) -> Optional[tuple]:
    """(t, z) when a trajectory passes closest to point p, if within 3 NM; else None."""
    best = None
    for k in range(len(T.t) - 1):
        r0 = np.array([T.x[k] - p[0], T.y[k] - p[1]])
        L = T.t[k + 1] - T.t[k]
        if L <= 1e-9:
            continue
        v = np.array([T.x[k + 1] - T.x[k], T.y[k + 1] - T.y[k]]) / L
        vv = float(v @ v)
        s = 0.0 if vv < 1e-12 else min(max(-(r0 @ v) / vv, 0.0), L)
        d = float(np.hypot(*(r0 + v * s)))
        if best is None or d < best[0]:
            best = (d, T.t[k] + s)
    if best is None or best[0] > 3.0:
        return None
    t = best[1]
    return t, float(np.interp(t, T.t, T.z))


def pair_spacing(w, A, B, p, sh_v_fn=None) -> Optional[dict]:
    """Spacing of two trajectories at the exit point p, or None when they do not both cross it or are vertically
    separated there."""
    ca, cb = crossing(A, p), crossing(B, p)
    if ca is None or cb is None:
        return None
    (ta, za), (tb, zb) = ca, cb
    sv = cpa._sv(za, zb, A.non_rvsm or B.non_rvsm, A.formation or B.formation)
    if abs(za - zb) >= sv - 1:
        return None                                   # levels vertically separated: no in-trail requirement
    lead, foll, t_lead, t_foll = (A, B, ta, tb) if ta <= tb else (B, A, tb, ta)
    xl, yl, _ = lead.at(t_foll)
    spacing = float(math.hypot(float(xl) - p[0], float(yl) - p[1]))
    return {"leader": lead.cs, "follower": foll.cs, "t_leader": float(t_lead), "t_follower": float(t_foll),
            "spacing_nm": spacing, "z_leader": float(za if lead is A else zb), "z_follower": float(zb if lead is A else za)}


def build_long(w, cs_list, H: float = H_EXIT) -> Dict[str, object]:
    return {cs: traj.build(w, w.aircraft[cs], H) for cs in cs_list}


def conflicts(w, H: float = H_EXIT, only_assumed: bool = True, trajs: Optional[dict] = None) -> List[dict]:
    """Pairs that will cross the same exit point less than the agreed spacing apart, at levels not separated."""
    req = required_nm(w)
    groups: Dict[str, List[str]] = {}
    for cs, a in w.aircraft.items():
        if not a.active or (only_assumed and not a.assumed):
            continue
        x = exit_of(w, a)
        if x:
            groups.setdefault(x, []).append(cs)
    out = []
    for x, members in groups.items():
        if len(members) < 2:
            continue
        T = trajs if trajs is not None else build_long(w, members, H)
        p = w.waypoints[x]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                r = pair_spacing(w, T[members[i]], T[members[j]], p)
                if r and r["spacing_nm"] < req and r["t_follower"] <= ACT_S:
                    out.append({**r, "exit": x, "required_nm": req})
    out.sort(key=lambda r: r["t_follower"])
    return out


def check(w, Tm, x: str, others: Dict[str, object], margin: float) -> tuple:
    """(ok, smallest spacing) of a trajectory at exit x against the other aircraft's long trajectories."""
    req = required_nm(w)
    p = w.waypoints[x]
    worst = math.inf
    for cs, To in others.items():
        if cs == Tm.cs:
            continue
        r = pair_spacing(w, Tm, To, p)
        if r:
            worst = min(worst, r["spacing_nm"])
    return worst >= req + margin, worst
