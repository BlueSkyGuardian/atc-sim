"""Hamilton-Jacobi reachability for a pair at close range: can the aircraft we move still avoid the other by turning
alone (at up to a standard-rate turn), the other flying straight on?

Relative state of the other aircraft in the frame of the one we move (x ahead, y to the right... here x along its
track, y to its left), and the heading difference theta = psi_other - psi_mover:

    x' = -v1 + v2 cos(theta) + u y
    y' =        v2 sin(theta) - u x
    theta' = -u,                    |u| <= OMEGA (the mover's turn rate)

Target (loss of separation): l(z) = sqrt(x^2 + y^2) - S <= 0. The backward reachable tube over a horizon T is
{V <= 0}, V the viscosity solution of

    dV/dtau = min(0, H(z, grad V)),   V(z, 0) = l(z),   H = max_u grad V . f(z, u)
            = p1 (-v1 + v2 cos theta) + p2 v2 sin theta + OMEGA |p1 y - p2 x - p3|

(the mover maximises its distance; min(0, .) freezes the tube: once unavoidable, unavoidable). Solved on a grid by a
first-order Lax-Friedrichs scheme, theta periodic. The tables (one per speed ratio v2/v1) are computed once and kept
in the data folder; a lookup is a trilinear interpolation.

Use: V < 0 for a pair means no heading of the moved aircraft separates it from the other within T: only a level (or
moving the other aircraft) can; the sign of the optimal u gives the turn direction that keeps it furthest away.
"""
from __future__ import annotations

import math
import os
from typing import Dict, Optional, Tuple

import numpy as np

V1_NM_S = 450.0 / 3600.0          # the mover's ground speed the tables are made for
OMEGA = math.radians(3.0)         # standard-rate turn
T_S = 300.0                       # horizon: close range, where turning dynamics matter
L_NM = 45.0                       # half-width of the grid
RATIOS = (0.8, 0.9, 1.0, 1.1, 1.2)


def _dir() -> str:
    from ..platform import config
    d = os.path.join(config.DATA_DIR, "hj")
    os.makedirs(d, exist_ok=True)
    return d


def build_table(ratio: float, sh: float = 5.0, n: int = 101, nth: int = 60, T: float = T_S, L: float = L_NM) -> Dict:
    v1, v2 = V1_NM_S, V1_NM_S * ratio
    xs = np.linspace(-L, L, n)
    th = np.linspace(0, 2 * math.pi, nth, endpoint=False)
    dx, dth = xs[1] - xs[0], th[1] - th[0]
    X, Y, TH = np.meshgrid(xs, xs, th, indexing="ij")
    V = np.sqrt(X ** 2 + Y ** 2) - sh
    ax = v1 + v2 + OMEGA * L
    ay = v2 + OMEGA * L
    at = OMEGA
    dt = 0.8 / (ax / dx + ay / dx + at / dth)
    steps = int(math.ceil(T / dt))
    dt = T / steps
    c, s = np.cos(TH), np.sin(TH)
    # local Lax-Friedrichs: the dissipation at each point is the local bound of |dH/dp| (a global bound, set by the
    # turning term at the grid's edge, would smear the whole solution)
    lax = np.abs(-v1 + v2 * c) + OMEGA * np.abs(Y)
    lay = v2 * np.abs(s) + OMEGA * np.abs(X)
    for _ in range(steps):
        Vx = np.pad(V, ((1, 1), (0, 0), (0, 0)), mode="edge")
        Vy = np.pad(V, ((0, 0), (1, 1), (0, 0)), mode="edge")
        px_m = (V - Vx[:-2]) / dx
        px_p = (Vx[2:] - V) / dx
        py_m = (V - Vy[:, :-2]) / dx
        py_p = (Vy[:, 2:] - V) / dx
        pt_m = (V - np.roll(V, 1, axis=2)) / dth
        pt_p = (np.roll(V, -1, axis=2) - V) / dth
        p1, p2, p3 = (px_m + px_p) / 2, (py_m + py_p) / 2, (pt_m + pt_p) / 2
        Hm = p1 * (-v1 + v2 * c) + p2 * v2 * s + OMEGA * np.abs(p1 * Y - p2 * X - p3)
        # dV/dtau = H  <=>  V_tau + G = 0 with G = -H: the Lax-Friedrichs flux of G, written back for H
        Hn = Hm + (lax * (px_p - px_m) + lay * (py_p - py_m) + at * (pt_p - pt_m)) / 2
        V = V + dt * np.minimum(0.0, Hn)
    return {"V": V.astype(np.float32), "xs": xs, "th": th, "ratio": ratio, "sh": sh, "T": T, "v1": v1}


_cache: Dict[Tuple[float, float], Dict] = {}


def table(ratio: float, sh: float, build_if_missing: bool = True, **kw) -> Optional[Dict]:
    r = min(RATIOS, key=lambda x: abs(x - ratio))
    key = (r, round(sh, 1))
    if key in _cache:
        return _cache[key]
    p = os.path.join(_dir(), f"hj_r{r:.1f}_s{sh:.1f}.npz")
    if os.path.exists(p):
        z = np.load(p)
        _cache[key] = {k: z[k] for k in z.files}
        return _cache[key]
    if not build_if_missing:
        return None
    t = build_table(r, sh, **kw)
    np.savez_compressed(p, **t)
    _cache[key] = t
    return t


MARGIN = 1.5                      # the scheme is ~1-1.5 NM optimistic (checked against simulated turns): trust "a turn
                                  # is enough" only above this
_building: set = set()


def ensure_async(sh: float) -> None:
    """Build the tables of a separation minimum in the background (about a minute each), once."""
    import threading
    key = round(sh, 1)
    if key in _building:
        return
    _building.add(key)

    def run():
        for r in RATIOS:
            try:
                table(r, sh)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=run, daemon=True, name=f"hj-{key}").start()


def relative_state(a, b) -> Tuple[float, float, float]:
    """b seen from a: x along a's track, y to its left (NM), theta = track_b - track_a (rad)."""
    psi = math.radians(a.trk)
    dxe, dyn = b.x - a.x, b.y - a.y                  # east, north
    ux, uy = math.sin(psi), math.cos(psi)            # a's track unit vector (east, north)
    x = dxe * ux + dyn * uy
    y = -dxe * uy + dyn * ux                         # left of track positive
    return x, y, math.radians(b.trk - a.trk) % (2 * math.pi)


def value(t: Dict, x: float, y: float, th: float) -> float:
    from scipy.ndimage import map_coordinates
    xs, ths = t["xs"], t["th"]
    if abs(x) > xs[-1] or abs(y) > xs[-1]:
        return float("inf")                          # out of the table: far, no close-range danger
    i = (x - xs[0]) / (xs[1] - xs[0])
    j = (y - xs[0]) / (xs[1] - xs[0])
    k = (th % (2 * math.pi)) / (ths[1] - ths[0])
    V = t["V"]
    Vp = np.concatenate([V, V[:, :, :1]], axis=2)   # periodic in theta
    return float(map_coordinates(Vp, [[i], [j], [k]], order=1, mode="nearest")[0])


def can_turn_away(w, mover: str, other: str, build: bool = False) -> Optional[Dict]:
    """For a pair at close range (both level and vertically too close): is a heading still enough? None when the
    table is not built yet (it is then built in the background)."""
    a, b = w.aircraft[mover], w.aircraft[other]
    ratio = (b.gs or 1) / max(a.gs, 1)
    t = table(ratio, float(w.sep_h), build_if_missing=build)
    if t is None:
        ensure_async(float(w.sep_h))
        return None
    x, y, th = relative_state(a, b)
    v = value(t, x, y, th)
    return {"value": v, "turn_enough": v > MARGIN, "rel": (round(x, 1), round(y, 1), round(math.degrees(th)))}


if __name__ == "__main__":                          # python -m atcsim.solve.hj 5 7   (separation minima, NM)
    import sys
    import time
    for sh_ in [float(x) for x in sys.argv[1:]] or [5.0, 7.0]:
        for r_ in RATIOS:
            t0 = time.time()
            table(r_, sh_)
            print(f"S={sh_:g} NM, speed ratio {r_}: {time.time() - t0:.0f} s")
