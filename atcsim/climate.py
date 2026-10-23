"""Climatological winds and temperatures from ERA5 (tools/era5_climatology.py builds the data file).

A scenario asks for it with
    "weather": {"wind": {"climatology": {"month": 1, "mode": 0.0, "scale": 1.0}, "noise_kt": 5}, ...}
  month : 1-12, the month's mean field (1991-2020 or whatever years the file holds)
  mode  : the leading mode of the year-to-year variability of that month, in standard deviations: +1 a strong-jet
          month, -1 a weak one (0 = the mean month)
  scale : multiplies the whole field (e.g. 1.2 for a day stronger than the mean)
The field varies with position (lat / lon, on the airspace's projection) and level, so the wind along a route
changes as it does in reality (e.g. the westerly component across TOVRA - TERTO). The synthetic jet streak, the
gradient and the small-scale noise of the weather model are added on top when the scenario gives them.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "climate")
DEFAULT = "era5_gmmm"
GRID_NM = 10.0
MARGIN_NM = 200.0


def available(name: str = DEFAULT) -> bool:
    return os.path.exists(os.path.join(DATA, name + ".npz"))


@lru_cache(maxsize=4)
def load(name: str = DEFAULT) -> dict:
    path = os.path.join(DATA, name + ".npz")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no climatology file {path}: build it with tools/era5_climatology.py")
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def _bilinear(lat, lon, field, qlat, qlon):
    """field [..., nlat, nlon] at query points (arrays of the same shape) -> [..., npts]."""
    i = np.clip(np.searchsorted(lat, qlat) - 1, 0, len(lat) - 2)
    j = np.clip(np.searchsorted(lon, qlon) - 1, 0, len(lon) - 2)
    fy = np.clip((qlat - lat[i]) / (lat[i + 1] - lat[i]), 0.0, 1.0)
    fx = np.clip((qlon - lon[j]) / (lon[j + 1] - lon[j]), 0.0, 1.0)
    f00, f01 = field[..., i, j], field[..., i, j + 1]
    f10, f11 = field[..., i + 1, j], field[..., i + 1, j + 1]
    return (f00 * (1 - fx) + f01 * fx) * (1 - fy) + (f10 * (1 - fx) + f11 * fx) * fy


class ClimField:
    """The month's field resampled on a regular grid of the simulator plane (x, y in NM), every GRID_NM."""

    def __init__(self, spec: dict, inverse, bbox, name: Optional[str] = None):
        d = load(name or spec.get("file") or DEFAULT)
        m = int(spec.get("month", 1))
        if not 1 <= m <= 12:
            raise ValueError("climatology month 1-12")
        mode, scale = float(spec.get("mode", 0.0)), float(spec.get("scale", 1.0))
        u = (d["u"][m - 1] + mode * d["eof_u"][m - 1]) * scale          # [lev, lat, lon]
        v = (d["v"][m - 1] + mode * d["eof_v"][m - 1]) * scale
        t = d["t"][m - 1]
        self.month, self.mode, self.scale = m, mode, scale
        self.fl = d["fl"].astype(float)
        x0, y0, x1, y1 = bbox
        self.xs = np.arange(x0 - MARGIN_NM, x1 + MARGIN_NM + 1e-6, GRID_NM)
        self.ys = np.arange(y0 - MARGIN_NM, y1 + MARGIN_NM + 1e-6, GRID_NM)
        X, Y = np.meshgrid(self.xs, self.ys)
        ll = np.array([inverse(float(a), float(b)) for a, b in zip(X.ravel(), Y.ravel())])
        qlat, qlon = ll[:, 0], ll[:, 1]
        lat, lon = d["lat"].astype(float), d["lon"].astype(float)
        sh = (len(self.fl), len(self.ys), len(self.xs))
        self.U = _bilinear(lat, lon, u, qlat, qlon).reshape(sh)
        self.V = _bilinear(lat, lon, v, qlat, qlon).reshape(sh)
        self.T = _bilinear(lat, lon, t, qlat, qlon).reshape(sh)
        self.years = [int(y) for y in d.get("years", [])]

    # ------------------------------------------------------------------ sampling
    def _h(self, x, y):
        fx = np.clip((x - self.xs[0]) / GRID_NM, 0, len(self.xs) - 1.000001)
        fy = np.clip((y - self.ys[0]) / GRID_NM, 0, len(self.ys) - 1.000001)
        j, i = fx.astype(int), fy.astype(int)
        return i, j, fx - j, fy - i

    def _sample(self, F, x, y, fl):
        x, y, fl = np.broadcast_arrays(np.asarray(x, float), np.asarray(y, float), np.asarray(fl, float))
        i, j, ax, ay = self._h(x, y)
        flc = np.clip(fl, self.fl[0], self.fl[-1])
        k = np.clip(np.searchsorted(self.fl, flc) - 1, 0, len(self.fl) - 2)
        az = (flc - self.fl[k]) / (self.fl[k + 1] - self.fl[k])

        def plane(kk):
            return ((F[kk, i, j] * (1 - ax) + F[kk, i, j + 1] * ax) * (1 - ay)
                    + (F[kk, i + 1, j] * (1 - ax) + F[kk, i + 1, j + 1] * ax) * ay)
        val = plane(k) * (1 - az) + plane(k + 1) * az
        return val, fl

    def uv(self, x, y, fl):
        u, fl = self._sample(self.U, x, y, fl)
        v, _ = self._sample(self.V, x, y, fl)
        # below the lowest level: weaker towards the surface; above the highest: decaying into the stratosphere
        lo, hi = self.fl[0], self.fl[-1]
        f = np.where(fl < lo, 0.35 + 0.65 * np.clip(fl, 0, lo) / lo,
                     np.where(fl > hi + 50, np.clip(1 - (fl - hi - 50) / 300.0, 0.3, 1.0), 1.0))
        return u * f, v * f

    def temperature(self, x, y, fl):
        t, _ = self._sample(self.T, x, y, fl)
        return t

    def mean_isa_dev(self, fl: float = 350.0) -> float:
        """Mean deviation from ISA at a level over the grid (for aircraft performance)."""
        k = int(np.argmin(np.abs(self.fl - fl)))
        isa = 15.0 - 1.98 * min(self.fl[k], 361.0) / 10.0
        return float(np.mean(self.T[k]) - isa)

    def describe(self) -> dict:
        return {"source": "ERA5 monthly means" + (f" {self.years[0]}-{self.years[-1]}" if self.years else ""),
                "month": self.month, "mode": self.mode, "scale": self.scale,
                "levels_fl": [int(round(f)) for f in self.fl]}
