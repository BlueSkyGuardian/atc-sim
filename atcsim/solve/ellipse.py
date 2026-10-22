"""Heading-only sequencing by ellipses.

A follower at P must reach a sequence point T having flown a path of length L, so that when it crosses T the leader is
the required spacing S past it: L = v2 * (t1 + S / v1), t1 the leader's time to T, v1 its ground speed after T, v2 the
follower's (L = v2 t1 + S when they fly the same speed). Flying a heading
to a turn point M and then direct to T, the path is |PM| + |MT|: the turn points with |PM| + |MT| = L form the ellipse
with foci P and T and major axis 2a = L (2c = |PT|, b^2 = a^2 - c^2). From the focus P, the ray of heading psi meets it
at distance

    r(theta) = b^2 / (a - c cos theta),    theta = psi - bearing(P -> T),

so every heading gives its clearance: "heading psi for r NM, then direct T" - a one-parameter family of solutions,
among which the rules choose (direction of flight, smallest turn, other traffic). The follower must be back on its
direct to T before the leader crosses it: |MT| >= S, i.e. r <= L - S.

The pure geometry ignores the two turns (and the pilot's delay): refine() measures the spacing with the solver's own
trajectory model and corrects r by secant steps, so the result is exact within that model.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np


def ellipse(P, T, L: float) -> Optional[Dict]:
    """The ellipse of the turn points: foci P (the follower) and T (the sequence point), major axis L. None when the
    follower already flies L or more going direct (no vector needed)."""
    D = math.hypot(T[0] - P[0], T[1] - P[1])
    if L <= D + 1e-6:
        return None
    a, c = L / 2.0, D / 2.0
    return {"a": a, "c": c, "b": math.sqrt(a * a - c * c), "P": tuple(P), "T": tuple(T),
            "brg_PT": math.degrees(math.atan2(T[0] - P[0], T[1] - P[1])) % 360}


def leg(e: Dict, heading: float) -> float:
    """r(theta): how far to fly heading psi from P before turning direct to T."""
    th = math.radians(heading - e["brg_PT"])
    return e["b"] ** 2 / (e["a"] - e["c"] * math.cos(th))


def turn_point(e: Dict, heading: float):
    r = leg(e, heading)
    h = math.radians(heading)
    return (e["P"][0] + r * math.sin(h), e["P"][1] + r * math.cos(h))


def outline(e: Dict, n: int = 361) -> np.ndarray:
    """Points of the ellipse (for drawing): the turn points of every heading."""
    return np.array([turn_point(e, h) for h in np.linspace(0, 360, n)])


def family(P, T, L: float, S: float, headings=None) -> List[Dict]:
    """Every heading (1-degree steps by default) with its leg r, keeping those that put the follower back on its
    direct to T before the leader crosses it (|MT| >= S, i.e. r <= L - S)."""
    e = ellipse(P, T, L)
    if e is None:
        return []
    out = []
    for h in (headings if headings is not None else range(0, 360)):
        r = leg(e, h)
        if r <= L - S + 1e-9:
            out.append({"heading": int(h) % 360 or 360, "leg_nm": r, "turn_point": turn_point(e, h)})
    return out


def required_path(leader_time_s: float, follower_gs: float, S: float, leader_gs: Optional[float] = None) -> float:
    """The follower's path to T so that the leader is S past T when the follower crosses it (no wind):
    L = v2 * (t1 + S / v1); with v1 = v2 it is L = v2 t1 + S."""
    return follower_gs / 3600.0 * (leader_time_s + S / (leader_gs or follower_gs) * 3600.0)


def with_wind(P, T, t1_s: float, S: float, tas: float, wind, heading: float, iters: int = 4,
              g_lead: Optional[float] = None) -> Optional[Dict]:
    """The same construction in a constant wind W (kt, toward east / north). In the frame of the air mass the aircraft
    flies straight lines at its true airspeed V while the sequence point drifts with -W: arriving at T at time t_A, the
    turn points (in that frame) are on the ellipse with foci P and T - W t_A and major axis 2a = V t_A. The follower must
    cross T when the leader is S ahead: t_A = t1 + S / g_f, g_f its ground speed on the final leg (iterated).
    Returns the time on the heading (s), the ground distance flown on it (NM), the turn point over the ground."""
    from .traj import wind_gs
    Wx, Wy = float(wind[0]), float(wind[1])
    D = math.hypot(T[0] - P[0], T[1] - P[1])
    u = ((T[0] - P[0]) / D, (T[1] - P[1]) / D)
    g_f = wind_gs(tas, (Wx, Wy), u)
    out = None
    for _ in range(iters):
        tA = (t1_s + S / (g_lead or g_f) * 3600.0) / 3600.0         # hours
        F2 = (T[0] - Wx * tA, T[1] - Wy * tA)
        e = ellipse(P, F2, tas * tA)
        if e is None:
            return None                                              # it already arrives late enough flying direct
        r_air = leg(e, heading)
        t_m = r_air / tas                                            # hours on the heading
        h = math.radians(heading)
        M = (P[0] + (tas * math.sin(h) + Wx) * t_m, P[1] + (tas * math.cos(h) + Wy) * t_m)
        dM = math.hypot(T[0] - M[0], T[1] - M[1])
        if dM < 1e-6 or tA - t_m <= 0:
            return None
        g_f = dM / (tA - t_m)
        out = {"heading": int(round(heading)) % 360 or 360, "time_s": t_m * 3600.0,
               "ground_nm": math.hypot(M[0] - P[0], M[1] - P[1]), "turn_point": M, "final_leg_nm": dM, "ellipse": e}
    if out and out["final_leg_nm"] < S:
        return None                                                  # not back on its direct before the leader crosses
    return out


def refine(w, follower, leader_traj, T_name: str, heading: int, S: float, t0_s: float, H: float = 1800.0,
           iters: int = 5) -> Optional[Dict]:
    """The time on the heading corrected so that, in the solver's trajectory model (turns, pilot delay, wind on each
    leg), the follower is exactly S behind the leader at T: secant steps on the spacing. None if it does not converge
    to within 0.2 NM."""
    from . import traj, exits
    p = w.waypoints[T_name]

    def spacing(t):
        T2 = traj.build(w, follower, H, heading=heading, for_s=max(30.0, t), then=T_name)
        sp = exits.pair_spacing(w, leader_traj, T2, p)
        if sp is None or sp["leader"] != leader_traj.cs:
            return None
        return sp["spacing_nm"]
    t_a, s_a = t0_s, spacing(t0_s)
    if s_a is None:
        return None
    t_b = t0_s * 1.05 + 10.0
    s_b = spacing(t_b)
    for _ in range(iters):
        if s_b is None or abs(s_b - s_a) < 1e-6:
            break
        t_n = max(30.0, t_b + (S - s_b) * (t_b - t_a) / (s_b - s_a))
        t_a, s_a, t_b, s_b = t_b, s_b, t_n, spacing(t_n)
        if s_b is not None and abs(s_b - S) < 0.05:
            break
    if s_b is None or abs(s_b - S) > 0.2:
        return None
    _, gs_h = traj.air_to_ground(follower.tas, heading, tuple(follower.wind_uv))
    return {"heading": heading, "time_s": t_b, "ground_nm": gs_h * t_b / 3600.0, "spacing_nm": s_b}
