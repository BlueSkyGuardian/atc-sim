"""Velocity obstacles: for one aircraft, which headings (and speeds) bring it within the separation minimum of each
other aircraft vertically too close, if both fly straight on (the instantaneous CPA, closed form, evaluated over a
1-degree heading grid for each speed). The union over the other aircraft is what the aircraft must avoid; what is
left is the conflict-free set for a straight vector.

Straight lines only: a sound picture for the next few minutes and a fast pre-filter, the route-aware check is in
resolve.py.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np

from .cpa import _sv


def obstacles(w, cs: str, look_s: float = 600.0, speeds_kt: List[float] = None) -> Dict:
    a = w.aircraft[cs]
    others = [o for o in w.aircraft.values() if o.active and o.callsign != cs]
    hdg = np.arange(0, 360, 1.0)
    speeds = speeds_kt or [a.gs]
    U = np.stack([np.sin(np.radians(hdg)), np.cos(np.radians(hdg))], axis=1)          # 360 x 2
    forb = {}
    union = np.zeros((len(speeds), len(hdg)), dtype=bool)
    for o in others:
        if abs(o.alt_ft - a.alt_ft) >= _sv(o.alt_ft, a.alt_ft, bool(o.non_rvsm() if callable(o.non_rvsm) else o.non_rvsm), False) \
                and abs(o.vs_fpm) < 300 and abs(a.vs_fpm) < 300:
            continue                                                                    # level and separated vertically
        r = np.array([o.x - a.x, o.y - a.y])
        vo = np.array([o.gs * math.sin(math.radians(o.trk)), o.gs * math.cos(math.radians(o.trk))]) / 3600.0
        m = np.zeros((len(speeds), len(hdg)), dtype=bool)
        for k, s in enumerate(speeds):
            va = U * (s / 3600.0)                                                       # candidate velocities
            vr = vo[None, :] - va                                                       # relative velocity of o seen from a
            vv = np.einsum("ij,ij->i", vr, vr)
            ts = np.clip(np.where(vv > 1e-12, -(vr @ r) / vv, 0.0), 0.0, look_s)
            d = np.linalg.norm(r[None, :] + vr * ts[:, None], axis=1)
            m[k] = d < w.sep_h
        if m.any():
            forb[o.callsign] = m
            union |= m
    return {"headings": hdg, "speeds": speeds, "forbidden": forb, "union": union}


def free_arcs(row: np.ndarray, hdg: np.ndarray) -> List[tuple]:
    """Conflict-free heading arcs of one speed row, as (from, to) in degrees."""
    ok = ~row
    if ok.all():
        return [(0, 359)]
    arcs, start = [], None
    n = len(hdg)
    k0 = int(np.argmax(~ok))                            # start scanning just after a forbidden heading
    for k in range(1, n + 1):
        i = (k0 + k) % n
        if ok[i] and start is None:
            start = int(hdg[i])
        if not ok[i] and start is not None:
            arcs.append((start, int(hdg[(i - 1) % n])))
            start = None
    if start is not None:
        arcs.append((start, int(hdg[k0])))
    return arcs


def summary(w, cs: str, look_s: float = 600.0) -> str:
    o = obstacles(w, cs, look_s)
    a = w.aircraft[cs]
    if not o["forbidden"]:
        return f"{cs}: no aircraft constrains its heading (straight-line look-ahead {int(look_s // 60)} min)"
    parts = []
    for oc, m in o["forbidden"].items():
        bad = o["headings"][m[0]]
        parts.append(f"{oc} forbids {len(bad)}° of headings")
    arcs = free_arcs(o["union"][0], o["headings"])
    near = sorted(arcs, key=lambda ab: min(abs((ab[0] - a.trk + 180) % 360 - 180), abs((ab[1] - a.trk + 180) % 360 - 180)))
    txt = ", ".join(f"{f:03d}-{t:03d}" for f, t in near[:3]) or "none"
    return f"{cs} (track {a.trk:03.0f}): " + "; ".join(parts) + f"; conflict-free headings at its speed: {txt}"
