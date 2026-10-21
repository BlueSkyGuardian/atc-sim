"""Closest points of approach.

Instantaneous (straight lines, the present velocities):  matrices T* (s) and D* (NM) for every pair, with the
vertical gap at t*:  t* = -(r.v)/|v|^2 (>= 0),  d* = |r + v t*|,  r = p_j - p_i, v = v_j - v_i.

Route-aware (piecewise-linear trajectories, traj.py): for a pair, the legs of both are merged; on each common leg the
relative motion is linear in x, y and z, so the time window where they are vertically too close is solved exactly,
and within it the closest horizontal distance (and the first time it falls under the minimum) in closed form.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

RVSM_LO, RVSM_HI = 29000.0, 41000.0


@dataclass
class PairCPA:
    a: str
    b: str
    conflict: bool          # horizontal < Sh while vertical < Sv, within the horizon
    t_first: Optional[float]   # first time both minima are infringed
    d_min: float            # closest horizontal distance while vertically too close (inf if never)
    t_min: Optional[float]
    d_min_h: float          # closest horizontal distance at any level (the geometry alone)
    t_min_h: float
    sv: float               # vertical minimum that applied


def _sv(za: float, zb: float, non_rvsm: bool, formation: bool) -> float:
    if formation:
        return 2000.0
    lo, hi = min(za, zb), max(za, zb)
    return 2000.0 if lo >= RVSM_LO - 50 and (hi > RVSM_HI + 50 or non_rvsm) else 1000.0


def pair(A, B, sh: float) -> PairCPA:
    """Exact CPA of two piecewise-linear trajectories (same horizon)."""
    U = np.unique(np.concatenate([A.t, B.t]))
    xa, ya, za = A.at(U)
    xb, yb, zb = B.at(U)
    rx, ry, rz = xb - xa, yb - ya, zb - za
    best_d, best_t, first, hd, ht = math.inf, None, None, math.inf, 0.0
    sv_used = 1000.0
    for k in range(len(U) - 1):
        L = U[k + 1] - U[k]
        if L <= 1e-9:
            continue
        r0 = np.array([rx[k], ry[k]])
        v = (np.array([rx[k + 1], ry[k + 1]]) - r0) / L
        dz0, dzv = rz[k], (rz[k + 1] - rz[k]) / L
        vv = float(v @ v)
        # horizontal minimum on the leg (any level)
        s = 0.0 if vv < 1e-12 else min(max(-(r0 @ v) / vv, 0.0), L)
        d = float(np.hypot(*(r0 + v * s)))
        if d < hd:
            hd, ht = d, U[k] + s
        sv = max(_sv(za[k], zb[k], A.non_rvsm or B.non_rvsm, A.formation or B.formation),
                 _sv(za[k + 1], zb[k + 1], A.non_rvsm or B.non_rvsm, A.formation or B.formation))
        # window where |dz0 + dzv s| < sv
        if abs(dzv) < 1e-9:
            if abs(dz0) >= sv:
                continue
            w0, w1 = 0.0, L
        else:
            s1, s2 = sorted(((-sv - dz0) / dzv, (sv - dz0) / dzv))
            w0, w1 = max(0.0, s1), min(L, s2)
            if w0 >= w1:
                continue
        sv_used = max(sv_used, sv)
        s = w0 if vv < 1e-12 else min(max(-(r0 @ v) / vv, w0), w1)
        d = float(np.hypot(*(r0 + v * s)))
        if d < best_d:
            best_d, best_t = d, U[k] + s
        if first is None and d < sh:
            # first s in [w0, w1] with |r0 + v s| = sh (or w0 if already inside)
            if float(np.hypot(*(r0 + v * w0))) < sh:
                first = U[k] + w0
            else:
                a_, b_, c_ = vv, 2 * float(r0 @ v), float(r0 @ r0) - sh * sh
                disc = b_ * b_ - 4 * a_ * c_
                if disc >= 0 and a_ > 1e-12:
                    root = (-b_ - math.sqrt(disc)) / (2 * a_)
                    first = U[k] + min(max(root, w0), w1)
    return PairCPA(A.cs, B.cs, best_d < sh, first, best_d, best_t, hd, ht, sv_used)


def matrix(trajs: Dict[str, object], sh: float, only: Optional[List[str]] = None) -> Dict:
    """Every pair of a situation (or every pair involving `only`): the conflict matrix."""
    cs = sorted(trajs)
    n = len(cs)
    D = np.full((n, n), np.inf)
    T = np.full((n, n), np.nan)
    C = np.zeros((n, n), dtype=bool)
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            if only and cs[i] not in only and cs[j] not in only:
                continue
            p = pair(trajs[cs[i]], trajs[cs[j]], sh)
            D[i, j] = D[j, i] = p.d_min
            if p.t_min is not None:
                T[i, j] = T[j, i] = p.t_min
            C[i, j] = C[j, i] = p.conflict
            if p.conflict:
                out.append(p)
    out.sort(key=lambda p: p.t_first if p.t_first is not None else 1e9)
    return {"callsigns": cs, "D": D, "T": T, "C": C, "conflicts": out}


def straight(w, look_s: float = 1200.0) -> Dict:
    """Instantaneous CPA matrices: present positions and velocities, straight ahead."""
    acs = [a for a in w.aircraft.values() if a.active]
    cs = [a.callsign for a in acs]
    P = np.array([[a.x, a.y] for a in acs]) if acs else np.zeros((0, 2))
    V = np.array([[a.gs * math.sin(math.radians(a.trk)), a.gs * math.cos(math.radians(a.trk))] for a in acs]) / 3600.0 \
        if acs else np.zeros((0, 2))
    Z = np.array([a.alt_ft for a in acs]) if acs else np.zeros(0)
    VZ = np.array([a.vs_fpm / 60.0 for a in acs]) if acs else np.zeros(0)
    R = P[None, :, :] - P[:, None, :]
    Vr = V[None, :, :] - V[:, None, :]
    vv = np.einsum("ijk,ijk->ij", Vr, Vr)
    with np.errstate(divide="ignore", invalid="ignore"):
        Ts = np.where(vv > 1e-12, -np.einsum("ijk,ijk->ij", R, Vr) / vv, 0.0)
    Ts = np.clip(Ts, 0.0, look_s)
    Ds = np.linalg.norm(R + Vr * Ts[:, :, None], axis=2)
    Zg = np.abs((Z[None, :] + VZ[None, :] * Ts) - (Z[:, None] + VZ[:, None] * Ts))
    np.fill_diagonal(Ds, np.inf)
    return {"callsigns": cs, "T": Ts, "D": Ds, "Zgap": Zg}
