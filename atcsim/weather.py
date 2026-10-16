"""Synthetic but physically-structured weather: temperature, 3D wind with a jet stream, convection, radar products,
turbulence.

Everything is a deterministic function of (x_nm, y_nm, flight level, sim time) generated from a seed, so the
look-ahead prediction and every client see the same atmosphere. Coordinates: NM from the sector centre, x east,
y north. Altitudes in flight levels (FL0 = ground ... FL800). Time in sim seconds.

Scenario spec (all optional; "seed" makes the random parts reproducible):

"weather": {
  "seed": 7,
  "isa_deviation_c": -3,                    # used for aircraft performance; default = deviation at FL350
  "temperature": {"surface_c": 22, "tropopause_fl": 360},
  "wind": {
     "profile": [{"fl": 0, "dir": 240, "kt": 10}, {"fl": 300, "dir": 265, "kt": 50}, ...],  # background (FROM)
     "jet": {"core_fl": 340, "dir": 270, "kt": 110, "axis_point": [0, 30], "half_width_nm": 80,
             "depth_below_ft": 10000, "depth_above_ft": 6000, "streak_length_nm": 500,
             "streak_speed_kt": 35, "meander_amp_nm": 12, "meander_wavelength_nm": 450},
     "noise_kt": 5, "noise_scale_nm": 40
  },
  "convection": {"regime": "none|isolated|scattered|squall_line|embedded", "intensity": "moderate|strong|severe",
                 # or a list of such patterns, generated together
                 "count": 8, "region": [-90, -90, 90, 90],
                 "line": {"center": [x, y], "orient_deg": 20, "length_nm": 160, "gaps": 1}},
  "cells": [ {"x": 10, "y": 5, "at_time": 600, "top_fl": 430, "dbz": 55, "radius_nm": 3.5,
              "lifetime_s": 4000, "age_s": 1200, "motion": {"toward": 80, "kt": 25}} ],
  "stratiform": [ {"center": [x, y], "radius_nm": [60, 30], "orient_deg": 30, "dbz": 32} ]
}
The legacy form {"wind": {"layers": [...]}} is accepted as a background profile.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

KT_TO_NM_S = 1.0 / 3600.0
DOMAIN_NM = 384.0            # periodic noise domain
NOISE_N = 256
TILT_S = 300.0               # convective tilt: shear (kt) * TILT_S seconds -> horizontal offset
ECHO_TOP_DBZ = 18.0
FL_MAX = 800

INTENSITY = {
    "moderate": {"dbz": (44, 50), "top": (280, 380), "radius": (2.0, 3.0)},
    "strong": {"dbz": (50, 57), "top": (360, 450), "radius": (2.5, 4.0)},
    "severe": {"dbz": (55, 63), "top": (420, 520), "radius": (3.0, 5.0)},
}


# ----------------------------------------------------------------------------- helpers
def _uv_from(dir_from: float, kt: float) -> Tuple[float, float]:
    d = math.radians(dir_from)
    return -kt * math.sin(d), -kt * math.cos(d)


def _uv_toward(toward: float, kt: float) -> Tuple[float, float]:
    d = math.radians(toward)
    return kt * math.sin(d), kt * math.cos(d)


def describe_uv(u: float, v: float) -> dict:
    spd = math.hypot(u, v)
    frm = (math.degrees(math.atan2(-u, -v))) % 360 if spd > 0.05 else 0.0
    return {"dir": int(round(frm)) or 360, "kt": round(spd)}


class NoiseField:
    """Smooth, periodic, unit-variance 2D random field (spectral synthesis), sampled bilinearly."""

    def __init__(self, rng: np.random.Generator, scale_nm: float, n: int = NOISE_N, domain: float = DOMAIN_NM):
        white = rng.standard_normal((n, n))
        k = np.fft.fftfreq(n, d=domain / n)
        kx, ky = np.meshgrid(k, k, indexing="ij")
        filt = np.exp(-0.5 * (kx * kx + ky * ky) * (2 * math.pi * scale_nm / 2.5) ** 2)
        f = np.real(np.fft.ifft2(np.fft.fft2(white) * filt))
        f = (f - f.mean()) / (f.std() + 1e-12)
        self.f = f
        self.flat = np.ascontiguousarray(f, dtype=np.float32).ravel()
        self.n = n
        self.mask = n - 1                      # n is a power of two
        self.inv_dx = n / domain

    def __call__(self, x, y):
        gx = np.asarray(x, np.float64) * self.inv_dx
        gy = np.asarray(y, np.float64) * self.inv_dx
        x0 = np.floor(gx)
        y0 = np.floor(gy)
        fx = (gx - x0).astype(np.float32)
        fy = (gy - y0).astype(np.float32)
        m, n = self.mask, self.n
        i0 = x0.astype(np.int64) & m
        j0 = y0.astype(np.int64) & m
        i1 = (i0 + 1) & m
        j1 = (j0 + 1) & m
        f = self.flat
        a = f[i0 * n + j0]
        b = f[i1 * n + j0]
        c = f[i0 * n + j1]
        d = f[i1 * n + j1]
        top = a + (b - a) * fx
        bot = c + (d - c) * fx
        return top + (bot - top) * fy


# ----------------------------------------------------------------------------- convective cell
@dataclass
class Cell:
    id: str
    x0: float                 # position at t_birth (NM)
    y0: float
    t_birth: float
    t_grow: float
    t_mature: float
    t_decay: float
    zmax: float               # peak core reflectivity (dBZ)
    top_fl: float             # peak 18-dBZ echo top
    radius: float             # core radius (NM), reflectivity drops 4.3 dB at this distance
    aspect: float
    orient: float             # radians
    mvx: float                # motion NM/s
    mvy: float
    nox: float                # texture offsets
    noy: float
    kind: str = "convective"

    @property
    def t_end(self) -> float:
        return self.t_birth + self.t_grow + self.t_mature + self.t_decay

    def state(self, t: float) -> Optional[dict]:
        age = t - self.t_birth
        if age < 0 or t > self.t_end:
            return None
        if age < self.t_grow:
            g = age / self.t_grow
            st = {"stage": "developing", "top": 0.45 + 0.55 * g, "dz": -15.0 * (1 - g), "rad": 0.6 + 0.4 * g,
                  "anvil": 0.0, "decay": 0.0}
        elif age < self.t_grow + self.t_mature:
            a = (age - self.t_grow) / max(self.t_mature, 1.0)
            st = {"stage": "mature", "top": 1.0, "dz": 0.0, "rad": 1.0, "anvil": min(1.0, 0.3 + 1.4 * a),
                  "decay": 0.0}
        else:
            d = (age - self.t_grow - self.t_mature) / max(self.t_decay, 1.0)
            st = {"stage": "dissipating", "top": 1.0 - 0.3 * d, "dz": -22.0 * d, "rad": 1.0 + 0.3 * d,
                  "anvil": 1.0, "decay": d}
        st["cx"] = self.x0 + self.mvx * age
        st["cy"] = self.y0 + self.mvy * age
        st["age"] = age
        return st


@dataclass
class Stratiform:
    id: str
    cx: float
    cy: float
    a: float
    b: float
    orient: float
    dbz: float
    mvx: float
    mvy: float
    t_on: float = -1e9
    t_off: float = 1e9


# ----------------------------------------------------------------------------- weather
class Weather:
    def __deepcopy__(self, memo):          # immutable function of time: shared by look-ahead clones
        return self

    def _nf(self, rng, scale_nm: float) -> "NoiseField":
        return NoiseField(rng, scale_nm, n=self._noise_n, domain=self.domain_nm)

    def __init__(self, spec: Optional[dict] = None, duration_s: float = 1800.0, domain_nm: float = DOMAIN_NM):
        # periodic noise domain: 384 NM for the training square, 768 NM for real CTAs (no visible repetition)
        self.domain_nm = float(domain_nm)
        self._noise_n = int(NOISE_N * self.domain_nm / DOMAIN_NM)
        spec = dict(spec or {})
        self.spec = spec
        self.seed = int(spec.get("seed", 0))
        rng = np.random.default_rng(self.seed)
        self.duration_s = duration_s

        # ---- temperature
        tspec = spec.get("temperature") or {}
        dev = spec.get("isa_deviation_c")
        self.surface_c = float(tspec.get("surface_c", 15.0 + (dev if dev is not None else 0.0)))
        self.tropopause_fl = float(tspec.get("tropopause_fl", 361.0))
        self.lapse_c_per_kft = float(tspec.get("lapse_c_per_kft", 1.98))
        self.isa_deviation_c = float(dev) if dev is not None else round(self.isa_dev(350.0), 1)
        self.freezing_level_ft = max(0.0, self.surface_c / self.lapse_c_per_kft * 1000.0)

        # ---- wind
        w = spec.get("wind") or {}
        prof = w.get("profile") or w.get("layers")
        if not prof:
            prof = [] if not (w or spec.get("convection") or spec.get("cells")) else [
                {"fl": 0, "dir": 240, "kt": 10}, {"fl": 100, "dir": 250, "kt": 25}, {"fl": 250, "dir": 260, "kt": 40},
                {"fl": int(self.tropopause_fl), "dir": 265, "kt": 50}, {"fl": 500, "dir": 270, "kt": 25},
                {"fl": 650, "dir": 90, "kt": 10}, {"fl": 800, "dir": 90, "kt": 25}]
        prof = sorted(prof, key=lambda p: p["fl"])
        self.profile = prof
        self.p_fl = np.array([float(p["fl"]) for p in prof]) if prof else np.array([0.0])
        uv = [_uv_from(float(p["dir"]), float(p["kt"])) for p in prof] or [(0.0, 0.0)]
        self.p_u = np.array([a for a, _ in uv])
        self.p_v = np.array([b for _, b in uv])
        g = w.get("gradient_kt_per_100nm") or {}
        self.grad_e = float(g.get("east", 0.0)) / 100.0
        self.grad_n = float(g.get("north", 0.0)) / 100.0
        self.jet = w.get("jet")
        if self.jet:
            j = self.jet
            to = math.radians((float(j.get("dir", 270)) + 180.0) % 360.0)
            self.j_e = (math.sin(to), math.cos(to))            # along-flow unit vector
            self.j_n = (math.cos(to), -math.sin(to))           # right of flow
            self.j_ax, self.j_ay = (float(c) for c in j.get("axis_point", [0.0, 0.0]))
            self.j_kt = float(j.get("kt", 100.0))
            self.j_core = float(j.get("core_fl", 340.0))
            self.j_w = float(j.get("half_width_nm", 80.0))
            self.j_db = float(j.get("depth_below_ft", 10000.0)) / 100.0
            self.j_da = float(j.get("depth_above_ft", 6000.0)) / 100.0
            self.j_ls = float(j.get("streak_length_nm", 0.0))
            self.j_s0 = float(j.get("streak_center_along_nm", 0.0))
            self.j_vs = float(j.get("streak_speed_kt", 35.0)) * KT_TO_NM_S
            self.j_ma = float(j.get("meander_amp_nm", 0.0))
            self.j_ml = float(j.get("meander_wavelength_nm", 450.0))
        self.noise_kt = float(w.get("noise_kt", 4.0 if prof else 0.0))
        nscale = float(w.get("noise_scale_nm", 40.0))
        self.n_u1, self.n_v1 = self._nf(rng, nscale), self._nf(rng, nscale)
        self.n_u2, self.n_v2 = self._nf(rng, nscale), self._nf(rng, nscale)
        self.n_patch = self._nf(rng, 25.0)          # turbulence patchiness
        self.n_tex = self._nf(rng, 3.0)             # convective texture
        self.n_tex2 = self._nf(rng, 8.0)            # stratiform texture
        u3, v3 = self._bg(300.0)
        self.adv = (float(u3) * KT_TO_NM_S, float(v3) * KT_TO_NM_S)

        # ---- precipitation
        self.cells: List[Cell] = []
        self.strat: List[Stratiform] = []
        self._ids = 0
        self._build_convection(spec, rng)
        self._vol_cache: Dict[tuple, dict] = {}
        p = spec.get("pilot") or {}
        self.pilot = {"hazard_dbz": float(p.get("hazard_dbz", 30.0)), "margin_nm": float(p.get("margin_nm", 6.0)),
                      "severe_dbz": float(p.get("severe_dbz", 40.0)),
                      "severe_margin_nm": float(p.get("severe_margin_nm", 10.0)),
                      "request_range_nm": float(p.get("request_range_nm", 60.0)),
                      "self_deviation_range_nm": float(p.get("self_deviation_range_nm", 15.0)),
                      "radar_range_nm": float(p.get("radar_range_nm", 80.0))}

    @property
    def has_precip(self) -> bool:
        return bool(self.cells or self.strat)

    # ========================================================================= temperature
    def temperature_c(self, fl):
        fl = np.asarray(fl, float)
        t_trop = self.surface_c - self.lapse_c_per_kft * self.tropopause_fl / 10.0
        t = np.where(fl <= self.tropopause_fl, self.surface_c - self.lapse_c_per_kft * fl / 10.0, t_trop)
        return np.where(fl > 650, t_trop + 0.3 * (fl - 650) / 10.0, t)

    @staticmethod
    def isa_temperature_c(fl):
        fl = np.asarray(fl, float)
        return np.where(fl <= 361.0, 15.0 - 1.98 * fl / 10.0, -56.5) + np.where(fl > 656, 0.3 * (fl - 656) / 10.0, 0)

    def isa_dev(self, fl):
        return float(self.temperature_c(fl) - self.isa_temperature_c(fl))

    # ========================================================================= wind
    def _bg(self, fl):
        return np.interp(fl, self.p_fl, self.p_u), np.interp(fl, self.p_fl, self.p_v)

    def wind_at(self, x, y, fl, t: float = 0.0):
        """Wind (u east, v north) in knots. x, y, fl may be arrays (broadcast)."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        fl = np.asarray(fl, float)
        u, v = self._bg(fl)
        u = u + np.zeros(np.broadcast(x, y, fl).shape)
        v = v + np.zeros_like(u)
        if self.grad_e or self.grad_n:
            spd = np.hypot(u, v)
            sc = np.where(spd > 1e-6, np.maximum(0.0, spd + self.grad_e * x + self.grad_n * y) / np.maximum(spd, 1e-6), 1.0)
            u, v = u * sc, v * sc
        if self.jet:
            px, py = x - self.j_ax, y - self.j_ay
            s = px * self.j_e[0] + py * self.j_e[1]
            n = px * self.j_n[0] + py * self.j_n[1]
            if self.j_ma:
                ph = 2 * math.pi * s / self.j_ml
                n = n - self.j_ma * np.sin(ph)
                slope = self.j_ma * 2 * math.pi / self.j_ml * np.cos(ph)
            else:
                slope = 0.0
            mag = self.j_kt * np.exp(-(n / self.j_w) ** 2)
            if self.j_ls > 0:
                sr = s - (self.j_s0 + self.j_vs * t)
                mag = mag * np.exp(-(sr / self.j_ls) ** 2)
            d = fl - self.j_core
            mag = mag * np.where(d < 0, np.exp(-(d / self.j_db) ** 2), np.exp(-(d / self.j_da) ** 2))
            ang = np.arctan(slope)
            ca, sa = np.cos(ang), np.sin(ang)
            ex = self.j_e[0] * ca + self.j_n[0] * sa
            ey = self.j_e[1] * ca + self.j_n[1] * sa
            u = u + mag * ex
            v = v + mag * ey
        if self.noise_kt:
            ax, ay = x - self.adv[0] * t, y - self.adv[1] * t
            a = np.clip(fl / 400.0, 0.0, 1.0)
            u = u + self.noise_kt * ((1 - a) * self.n_u1(ax, ay) + a * self.n_u2(ax, ay))
            v = v + self.noise_kt * ((1 - a) * self.n_v1(ax, ay) + a * self.n_v2(ax, ay))
        return u, v

    def at(self, x, y, fl, t: float = 0.0):            # legacy interface (scalar)
        u, v = self.wind_at(x, y, fl, t)
        return float(u), float(v)

    # ========================================================================= convection generation
    def _new_id(self, prefix: str = "C") -> str:
        self._ids += 1
        return f"{prefix}{self._ids:02d}"

    def steering(self) -> Tuple[float, float]:
        """Storm motion: ~75% of the FL050-FL300 mean wind, 25 deg to the right (northern hemisphere)."""
        fls = np.arange(50, 301, 25.0)
        u, v = self._bg(fls)
        um, vm = float(np.mean(u)), float(np.mean(v))
        spd = math.hypot(um, vm) * 0.75
        to = math.degrees(math.atan2(um, vm)) + 25.0 if spd > 0.1 else 90.0
        return _uv_toward(to, max(spd, 8.0))

    def _make_cell(self, rng, preset, x_mid, y_mid, t_mid, mv, *, t_birth=None, lifetime=None, top=None, dbz=None,
                   radius=None) -> Cell:
        lt = lifetime or float(rng.uniform(2400, 5400))
        tg = min(1200.0, lt * 0.25)
        td = lt * 0.3
        tm = lt - tg - td
        if t_birth is None:
            t_birth = float(rng.uniform(-lt * 0.8, self.duration_s - lt * 0.2))
        # position at t_birth so that the cell passes (x_mid, y_mid) at t_mid
        x0 = x_mid - mv[0] * (t_mid - t_birth)
        y0 = y_mid - mv[1] * (t_mid - t_birth)
        return Cell(id=self._new_id(), x0=x0, y0=y0, t_birth=t_birth, t_grow=tg, t_mature=tm, t_decay=td,
                    zmax=dbz or float(rng.uniform(*preset["dbz"])), top_fl=top or float(rng.uniform(*preset["top"])),
                    radius=radius or float(rng.uniform(*preset["radius"])), aspect=float(rng.uniform(0.7, 1.0)),
                    orient=float(rng.uniform(0, math.pi)), mvx=mv[0], mvy=mv[1],
                    nox=float(rng.uniform(0, self.domain_nm)), noy=float(rng.uniform(0, self.domain_nm)))

    def _build_convection(self, spec: dict, rng: np.random.Generator) -> None:
        """`convection` is one pattern or a list of them (e.g. a squall line in one area and scattered storms in
        another); each is generated in turn from the same random stream, so a single pattern gives the same
        weather as before. Explicit cells and stratiform areas are added afterwards."""
        convs = spec.get("convection") or {}
        convs = [c for c in convs if c] if isinstance(convs, list) else [convs]
        self.lines, self.line_gaps = [], []
        for conv in convs:
            self._build_pattern(conv or {}, rng)
        if self.lines:
            self.line = self.lines[0]
        self._build_explicit(spec, rng, INTENSITY[(convs[0] if convs else {}).get("intensity", "strong")])

    def _build_pattern(self, conv: dict, rng: np.random.Generator) -> None:
        regime = conv.get("regime", "none")
        preset = INTENSITY[conv.get("intensity", "strong")]
        su, sv = self.steering()
        mv = (su * KT_TO_NM_S, sv * KT_TO_NM_S)
        D = self.duration_s
        x0, y0, x1, y1 = conv.get("region", [-90, -90, 90, 90])

        if regime in ("isolated", "scattered", "embedded"):
            n = int(conv.get("count", {"isolated": 3, "scattered": 10, "embedded": 6}[regime]))
            for _ in range(n):
                xm, ym = float(rng.uniform(x0, x1)), float(rng.uniform(y0, y1))
                tm = float(rng.uniform(0, D))
                c = self._make_cell(rng, preset, xm, ym, tm, mv)
                if regime == "embedded":
                    c.top_fl = float(rng.uniform(280, 360))
                    c.zmax -= 3
                self.cells.append(c)
                if regime == "scattered" and rng.random() < 0.6:
                    # multicell: daughters on the right-forward flank, every ~12 min
                    ang = math.atan2(mv[0], mv[1]) + math.radians(rng.uniform(30, 70))
                    for k in range(1, int(rng.integers(2, 4)) + 1):
                        dx, dy = 6.0 * k * math.sin(ang), 6.0 * k * math.cos(ang)
                        tb = c.t_birth + 700.0 * k
                        d = self._make_cell(rng, preset, c.x0 + dx + mv[0] * (tb - c.t_birth),
                                            c.y0 + dy + mv[1] * (tb - c.t_birth), tb, mv, t_birth=tb)
                        self.cells.append(d)
            if regime == "embedded":
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                self.strat.append(Stratiform(self._new_id("S"), cx, cy, (x1 - x0) * 0.35, (y1 - y0) * 0.25,
                                             float(rng.uniform(0, math.pi)), 30.0, mv[0], mv[1]))

        elif regime == "squall_line":
            ln = conv.get("line") or {}
            cx, cy = (float(c) for c in ln.get("center", [0.0, 0.0]))
            spd = math.hypot(*mv)
            heading_mv = math.atan2(mv[0], mv[1])
            orient = math.radians(float(ln.get("orient_deg", math.degrees(heading_mv) + 90.0)))
            ex, ey = math.sin(orient), math.cos(orient)                 # along line
            nx, ny = math.cos(orient), -math.sin(orient)                # normal
            if nx * mv[0] + ny * mv[1] < 0:
                nx, ny = -nx, -ny                                       # normal points in direction of motion
            line_speed = max(nx * mv[0] + ny * mv[1], 15 * KT_TO_NM_S)
            lmv = (nx * line_speed, ny * line_speed)
            L = float(ln.get("length_nm", 170.0))
            spacing = float(ln.get("spacing_nm", 9.0))
            gaps = []
            for _ in range(int(ln.get("gaps", 1))):
                gc = float(rng.uniform(-L / 2 + 25, L / 2 - 25))
                gaps.append((gc, float(rng.uniform(16, 26))))
            for gc, gw in ln.get("gap_list", []):
                gaps.append((float(gc), float(gw)))
            self.line_gaps.extend(gaps)
            s = -L / 2
            while s <= L / 2:
                if not any(abs(s - gc) < gw / 2 for gc, gw in gaps):
                    # several generations so the line persists for the whole scenario
                    gen_t = float(rng.uniform(-3000, -1500))
                    while gen_t < D + 600:
                        lt = float(rng.uniform(2700, 4200))
                        jit_s = float(rng.uniform(-2.5, 2.5))
                        jit_n = float(rng.uniform(-3, 2))
                        xm = cx + ex * (s + jit_s) + nx * jit_n
                        ym = cy + ey * (s + jit_s) + ny * jit_n
                        c = self._make_cell(rng, preset, xm + lmv[0] * gen_t, ym + lmv[1] * gen_t, gen_t, lmv,
                                            t_birth=gen_t, lifetime=lt)
                        c.zmax = min(c.zmax + 2, 65)
                        self.cells.append(c)
                        gen_t += lt * float(rng.uniform(0.45, 0.6))
                s += spacing * float(rng.uniform(0.85, 1.15))
            # trailing stratiform region behind the line
            self.strat.append(Stratiform(self._new_id("S"), cx - nx * 30, cy - ny * 30, L * 0.5, 22.0,
                                         orient, 31.0, lmv[0], lmv[1]))
            self.lines.append({"center": [cx, cy], "orient_deg": round(math.degrees(orient) % 180, 1), "length_nm": L,
                               "intensity": conv.get("intensity", "strong"), "motion": describe_toward(lmv),
                               "gaps_along_nm": [[round(g, 1), round(w, 1)] for g, w in gaps]})

    def _build_explicit(self, spec: dict, rng: np.random.Generator, preset: dict) -> None:
        su, sv = self.steering()
        # explicit cells
        for c in spec.get("cells", []):
            m = c.get("motion")
            cm = _uv_toward(float(m["toward"]), float(m["kt"])) if m else (su, sv)
            cmv = (cm[0] * KT_TO_NM_S, cm[1] * KT_TO_NM_S)
            lt = float(c.get("lifetime_s", 5400))
            t_at = float(c.get("at_time", 0.0))
            age = float(c.get("age_s", lt * 0.4))           # age when it is at (x, y) at at_time
            tb = t_at - age
            cell = self._make_cell(rng, preset, float(c["x"]), float(c["y"]), t_at, cmv, t_birth=tb, lifetime=lt,
                                   top=float(c.get("top_fl", 420)), dbz=float(c.get("dbz", 55)),
                                   radius=float(c.get("radius_nm", 3.5)))
            if "aspect" in c:
                cell.aspect = float(c["aspect"])
            if "orient_deg" in c:
                cell.orient = math.radians(float(c["orient_deg"]))
            if "id" in c:
                cell.id = str(c["id"])
            self.cells.append(cell)
        for s in spec.get("stratiform", []):
            m = s.get("motion")
            sm = _uv_toward(float(m["toward"]), float(m["kt"])) if m else (su, sv)
            r = s.get("radius_nm", [50, 30])
            self.strat.append(Stratiform(self._new_id("S"), float(s["center"][0]), float(s["center"][1]),
                                         float(r[0]), float(r[1]), math.radians(float(s.get("orient_deg", 0))),
                                         float(s.get("dbz", 30)), sm[0] * KT_TO_NM_S, sm[1] * KT_TO_NM_S))

    # ========================================================================= reflectivity
    def _shear_offset(self, fl):
        """Convective tilt offset (NM) at a level: (wind(fl) - wind(FL050)) * TILT_S."""
        u, v = self._bg(fl)
        u0, v0 = self._bg(50.0)
        return (u - u0) * KT_TO_NM_S * TILT_S, (v - v0) * KT_TO_NM_S * TILT_S

    def active_cells(self, t: float) -> List[Tuple[Cell, dict]]:
        c = getattr(self, "_ac", None)          # (t, cells) in one attribute: safe with concurrent readers
        if c is not None and c[0] == t:
            return c[1]
        out = []
        for c in self.cells:
            st = c.state(t)
            if st is not None:
                out.append((c, st))
        self._ac = (t, out)
        return out

    def reflectivity(self, x, y, fl, t: float, cells=None):
        """Radar reflectivity (dBZ) at points. x, y, fl arrays (broadcast). Values < 0 mean no echo."""
        n = int(np.prod(np.broadcast(np.asarray(x), np.asarray(y), np.asarray(fl)).shape) or 1)
        active = cells if cells is not None else self.active_cells(t)
        if n * max(len(active), 1) <= 400_000:
            return self._reflectivity_vec(x, y, fl, t, active)
        return self._reflectivity_loop(x, y, fl, t, active)

    def _cell_arrays(self, t: float, active) -> dict:
        key = (t, id(active))
        c = getattr(self, "_ca_c", None)
        if c is not None and c[0] == key:
            return c[1]
        n = len(active)
        a = {k: np.empty(n) for k in ("cx", "cy", "H", "R0", "Z0", "hm", "co", "so", "asp", "nox", "noy",
                                       "anv", "dec", "La", "ex", "ey", "wa")}
        F = self.freezing_level_ft
        for i, (c, st) in enumerate(active):
            H = c.top_fl * 100.0 * st["top"]
            R0 = c.radius * st["rad"]
            a["cx"][i], a["cy"][i] = st["cx"], st["cy"]
            a["H"][i], a["R0"][i] = H, R0
            a["Z0"][i] = c.zmax + st["dz"]
            a["hm"][i] = min(max(F, 0.25 * H), 0.6 * H)
            a["co"][i], a["so"][i] = math.cos(c.orient), math.sin(c.orient)
            a["asp"][i], a["nox"][i], a["noy"][i] = c.aspect, c.nox, c.noy
            a["anv"][i], a["dec"][i] = st["anvil"], st["decay"]
            ua, va = self._bg(H / 100.0)
            sp = math.hypot(ua, va)
            La = st["anvil"] * min(80.0, 10.0 + 0.4 * sp) * (1 + 0.4 * st["decay"])
            a["La"][i] = La
            a["ex"][i], a["ey"][i] = (ua / sp, va / sp) if sp > 1 else (1.0, 0.0)
            a["wa"][i] = 1.6 * R0 + 0.18 * La
        self._ca_c = (key, a)
        return a

    def _reflectivity_vec(self, x, y, fl, t: float, active):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        fl = np.asarray(fl, float)
        shape = np.broadcast(x, y, fl).shape
        xf = np.broadcast_to(x, shape).ravel()
        yf = np.broadcast_to(y, shape).ravel()
        flf = np.broadcast_to(fl, shape).ravel()
        h = flf * 100.0
        zlin = np.zeros(xf.shape)
        a = self._cell_arrays(t, active) if active else None
        if a is not None:
            ox, oy = self._shear_offset(flf)
            reach = a["R0"] * 3.2 + a["La"] + 2 * a["R0"] + 16.0 + max(float(np.abs(ox).max()), float(np.abs(oy).max()))
            keep = ((a["cx"] > xf.min() - reach) & (a["cx"] < xf.max() + reach) &
                    (a["cy"] > yf.min() - reach) & (a["cy"] < yf.max() + reach) & (a["H"] + 4000 > h.min()))
            a = {k: v[keep] for k, v in a.items()} if keep.any() else None
        if a is not None:
            dx = xf[:, None] - (a["cx"][None, :] + ox[:, None])
            dy = yf[:, None] - (a["cy"][None, :] + oy[:, None])
            hh = h[:, None]
            H, Z0, hm = a["H"][None, :], a["Z0"][None, :], a["hm"][None, :]
            zc = np.where(hh <= hm, Z0 - 3.0 * (1.0 - hh / np.maximum(hm, 1.0)),
                          np.where(hh <= H, Z0 - (Z0 - ECHO_TOP_DBZ) * np.clip((hh - hm) / np.maximum(H - hm, 1.0), 0, 1) ** 1.5,
                                   ECHO_TOP_DBZ - 8.0 * (hh - H) / 1000.0))
            R = a["R0"][None, :] * (1.0 - 0.35 * np.clip(hh / np.maximum(H, 1.0), 0.0, 1.2))
            a1 = dx * a["co"] + dy * a["so"]
            a2 = -dx * a["so"] + dy * a["co"]
            r2 = (a1 / R) ** 2 + (a2 / (R * a["asp"])) ** 2
            tex = self.n_tex(dx + a["nox"], dy + a["noy"])
            dbz = zc - 4.343 * r2 + 3.5 * tex
            zlin = np.where(dbz > 0, 10.0 ** (np.minimum(dbz, 90.0) / 10.0), 0.0).sum(axis=1)
            # anvils: only cells whose anvil layer (0.68 H .. H) contains some of the points
            sel = (a["anv"] > 0) & (a["H"] > 15000) & (h.max() >= 0.68 * a["H"]) & (h.min() <= a["H"])
            if sel.any():
                b = {k: v[sel] for k, v in a.items()}
                dxa, dya = dx[:, sel], dy[:, sel]
                Hb, La = b["H"][None, :], b["La"][None, :]
                along = dxa * b["ex"] + dya * b["ey"] - La / 2
                cross = -dxa * b["ey"] + dya * b["ex"]
                q = (along / (La / 2 + b["R0"])) ** 2 + (cross / b["wa"]) ** 2
                vb = np.clip((hh - 0.68 * Hb) / (0.32 * Hb), 0, 1)
                vb = np.sin(vb * math.pi) ** 0.6 * (hh <= Hb)
                za = 21.0 + 3.0 * self.n_tex(along + b["noy"], cross + b["nox"]) - 7.0 * b["dec"]
                zlin = zlin + (vb * np.exp(-q * q) * 10.0 ** (za / 10.0)).max(axis=1)
        F = self.freezing_level_ft
        for s in self.strat:
            if not (s.t_on <= t <= s.t_off):
                continue
            cx = s.cx + s.mvx * t
            cy = s.cy + s.mvy * t
            ddx, ddy = xf - cx, yf - cy
            co, so = math.cos(s.orient), math.sin(s.orient)
            q = ((ddx * so + ddy * co) / s.a) ** 2 + ((ddx * co - ddy * so) / s.b) ** 2
            prof = np.where(h < F - 1000, s.dbz, np.where(h < F + 500, s.dbz + 6.0, s.dbz - 2.6 * (h - F - 500) / 1000.0 - 3.0))
            tex = self.n_tex2(xf - s.mvx * t + 50, yf - s.mvy * t + 50)
            dbz = prof + 5.0 * tex - 6.0 * q ** 2
            zlin = zlin + np.where((q < 2.5) & (dbz > 0), 10.0 ** (np.minimum(dbz, 90.0) / 10.0), 0.0)
        out = np.where(zlin > 1.0, 10.0 * np.log10(np.maximum(zlin, 1e-9)), -10.0)
        return out.reshape(shape)

    def _reflectivity_loop(self, x, y, fl, t: float, cells):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        fl = np.asarray(fl, float)
        shape = np.broadcast(x, y, fl).shape
        x = np.broadcast_to(x, shape)
        y = np.broadcast_to(y, shape)
        fl = np.broadcast_to(fl, shape)
        h = fl * 100.0
        zlin = np.zeros(shape)
        zanv = np.zeros(shape)            # anvils combine by maximum (they overlap, they do not add up)
        F = self.freezing_level_ft
        ox, oy = self._shear_offset(fl)
        utop_cache = {}
        for c, st in cells:
            H = c.top_fl * 100.0 * st["top"]
            R0 = c.radius * st["rad"]
            reach = R0 * 3.2 + 6.0
            # anvil geometry (from the upper-level wind at the top)
            if st["anvil"] > 0:
                ua, va = self._bg(H / 100.0)
                utop_cache[id(c)] = (ua, va)
                sp = math.hypot(ua, va)
                La = st["anvil"] * min(80.0, 10.0 + 0.4 * sp) * (1 + 0.4 * st["decay"])
                reach = max(reach, La + 2 * R0 + 10)
            cx = st["cx"] + ox
            cy = st["cy"] + oy
            dx = x - cx
            dy = y - cy
            near = (np.abs(dx) < reach + 12) & (np.abs(dy) < reach + 12) & (h < H + 4000)
            if not near.any():
                continue
            dxn, dyn, hn = dx[near], dy[near], h[near]
            # --- core vertical profile
            Z0 = c.zmax + st["dz"]
            hm = min(max(F, 0.25 * H), 0.6 * H)
            zc = np.where(hn <= hm, Z0 - 3.0 * (1.0 - hn / max(hm, 1.0)),
                          np.where(hn <= H, Z0 - (Z0 - ECHO_TOP_DBZ) * np.clip((hn - hm) / max(H - hm, 1.0), 0, 1) ** 1.5,
                                   ECHO_TOP_DBZ - 8.0 * (hn - H) / 1000.0))
            R = R0 * (1.0 - 0.35 * np.clip(hn / max(H, 1.0), 0.0, 1.2))
            co, so = math.cos(c.orient), math.sin(c.orient)
            a1 = dxn * co + dyn * so
            a2 = -dxn * so + dyn * co
            r2 = (a1 / R) ** 2 + (a2 / (R * c.aspect)) ** 2
            age = st["age"]
            tex = self.n_tex(dxn + c.nox - c.mvx * 0 * age, dyn + c.noy)
            dbz = zc - 4.343 * r2 + 3.5 * tex
            contrib = np.where(dbz > 0, 10.0 ** (dbz / 10.0), 0.0)
            # --- anvil: flat, low-reflectivity shield blown downwind from the top
            if st["anvil"] > 0 and H > 15000:
                ua, va = utop_cache[id(c)]
                sp = math.hypot(ua, va)
                if sp > 1:
                    ex, ey = ua / sp, va / sp
                else:
                    ex, ey = 1.0, 0.0
                along = dxn * ex + dyn * ey - La / 2
                cross = -dxn * ey + dyn * ex
                wa = 1.6 * R0 + 0.18 * La
                q = (along / (La / 2 + R0)) ** 2 + (cross / wa) ** 2
                vb = np.clip((hn - 0.68 * H) / (0.32 * H), 0, 1)
                vb = np.sin(vb * math.pi) ** 0.6 * (hn <= H)
                za = 21.0 + 3.0 * self.n_tex(along + c.noy, cross + c.nox) - 7.0 * st["decay"]
                zanv[near] = np.maximum(zanv[near], vb * np.exp(-q * q) * 10.0 ** (za / 10.0))
            zlin[near] += contrib
        for s in self.strat:
            if not (s.t_on <= t <= s.t_off):
                continue
            cx = s.cx + s.mvx * t
            cy = s.cy + s.mvy * t
            dx, dy = x - cx, y - cy
            co, so = math.cos(s.orient), math.sin(s.orient)
            a1 = (dx * so + dy * co) / s.a          # along orient
            a2 = (dx * co - dy * so) / s.b
            q = a1 * a1 + a2 * a2
            m = q < 2.5
            if not m.any():
                continue
            hn = h[m]
            prof = np.where(hn < F - 1000, s.dbz,
                            np.where(hn < F + 500, s.dbz + 6.0, s.dbz - 2.6 * (hn - F - 500) / 1000.0 - 3.0))
            tex = self.n_tex2(x[m] - s.mvx * t + 50, y[m] - s.mvy * t + 50)
            dbz = prof + 5.0 * tex - 6.0 * q[m] ** 2
            zlin[m] += np.where(dbz > 0, 10.0 ** (dbz / 10.0), 0.0)
        zlin = zlin + zanv
        out = np.where(zlin > 1.0, 10.0 * np.log10(np.maximum(zlin, 1e-9)), -10.0)
        return out

    # ========================================================================= turbulence
    def edr(self, x, y, fl, t: float):
        """Eddy dissipation rate (m^2/3 s^-1): ~0.1 light, 0.3 moderate, 0.5 severe."""
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        fl = np.asarray(fl, float)
        u1, v1 = self.wind_at(x, y, fl + 10, t)
        u0, v0 = self.wind_at(x, y, fl - 10, t)
        shear = np.hypot(u1 - u0, v1 - v0) / 2.0                  # kt per 1000 ft
        patch = np.clip(0.55 + 0.6 * self.n_patch(x - self.adv[0] * t, y - self.adv[1] * t + fl * 0.3), 0, 1.3)
        cat = np.clip((shear - 5.0) * 0.03, 0, 0.6) * patch      # ~15 kt/1000 ft -> moderate (0.3)
        conv = np.zeros(np.broadcast(x, y, fl).shape)
        h = fl * 100.0
        ox, oy = self._shear_offset(fl)
        for c, st in self.active_cells(t):
            H = c.top_fl * 100.0 * st["top"]
            f = np.clip((c.zmax + st["dz"] - 35.0) / 25.0, 0, 1)
            if f <= 0:
                continue
            R = c.radius * st["rad"]
            dx = x - (st["cx"] + ox)
            dy = y - (st["cy"] + oy)
            r2 = (dx * dx + dy * dy) / (R * R)
            inside = 0.6 * f * np.exp(-r2 / 5.0) * (h <= H + 2000)
            above = 0.3 * f * np.exp(-r2 / 30.0) * np.exp(-((h - H) / 5000.0) ** 2)
            conv = np.maximum(conv, np.maximum(inside, above))
        return np.sqrt(cat * cat + conv * conv)

    # ========================================================================= products
    def grid(self, bbox=(-100, -100, 100, 100), res_nm: float = 2.0):
        x0, y0, x1, y1 = bbox
        xs = np.arange(x0 + res_nm / 2, x1, res_nm)
        ys = np.arange(y0 + res_nm / 2, y1, res_nm)
        X, Y = np.meshgrid(xs, ys)          # rows = y (south->north), cols = x
        return xs, ys, X, Y

    def cappi(self, fl: float, t: float, bbox=(-100, -100, 100, 100), res_nm: float = 2.0):
        xs, ys, X, Y = self.grid(bbox, res_nm)
        return xs, ys, self.reflectivity(X, Y, float(fl), t)

    def volume(self, t: float, bbox=(-100, -100, 100, 100), res_nm: float = 3.0, fl_step: int = 20):
        key = (round(t / 30.0), tuple(bbox), res_nm, fl_step)
        hit = self._vol_cache.get(key)
        if hit is not None:
            return hit
        xs, ys, X, Y = self.grid(bbox, res_nm)
        fls = np.arange(0, FL_MAX + 1, fl_step, dtype=float)
        cells = self.active_cells(t)
        vol = np.stack([self.reflectivity(X, Y, f, t, cells) for f in fls])
        comp = vol.max(axis=0)
        above = vol >= ECHO_TOP_DBZ
        idx = np.where(above.any(axis=0), len(fls) - 1 - np.argmax(above[::-1], axis=0), -1)
        tops = np.where(idx >= 0, fls[np.maximum(idx, 0)], 0.0)
        res = {"xs": xs, "ys": ys, "fls": fls, "vol": vol, "composite": comp, "echo_tops": tops}
        if len(self._vol_cache) > 8:
            self._vol_cache = {}
        self._vol_cache[key] = res
        return res

    def column(self, x: float, y: float, t: float, fl_step: int = 10) -> List[dict]:
        fls = np.arange(0, FL_MAX + 1, fl_step, dtype=float)
        dbz = self.reflectivity(np.full_like(fls, x), np.full_like(fls, y), fls, t)
        u, v = self.wind_at(np.full_like(fls, x), np.full_like(fls, y), fls, t)
        e = self.edr(np.full_like(fls, x), np.full_like(fls, y), fls, t)
        T = self.temperature_c(fls)
        return [{"fl": int(f), "dbz": round(float(z), 1) if z > 0 else None, "wind": describe_uv(float(a), float(b)),
                 "temp_c": round(float(tc), 1), "edr": round(float(ed), 3)}
                for f, z, a, b, tc, ed in zip(fls, dbz, u, v, T, e)]

    view_bbox = (-110.0, -110.0, 110.0, 110.0)     # area of interest (set from the airspace by the scenario)

    def cells_summary(self, t: float, bbox=None) -> List[dict]:
        if bbox is None:
            b = self.view_bbox
            bbox = (b[0] - 20, b[1] - 20, b[2] + 20, b[3] + 20)
        out = []
        for c, st in self.active_cells(t):
            if not (bbox[0] <= st["cx"] <= bbox[2] and bbox[1] <= st["cy"] <= bbox[3]):
                continue
            H = c.top_fl * st["top"]
            zc = c.zmax + st["dz"]
            r = c.radius * st["rad"]
            r30 = r * math.sqrt(max(zc - 30.0, 0) / 4.343) if zc > 30 else 0.0
            r45 = r * math.sqrt(max(zc - 45.0, 0) / 4.343) if zc > 45 else 0.0
            out.append({"id": c.id, "stage": st["stage"], "x_nm": round(st["cx"], 1), "y_nm": round(st["cy"], 1),
                        "motion": describe_toward((c.mvx, c.mvy)), "echo_top_fl": int(H),
                        "max_dbz": round(zc, 1), "radius_30dbz_nm": round(r30, 1),
                        "radius_45dbz_nm": round(r45, 1), "age_s": int(st["age"]),
                        "remaining_life_s": int(c.t_end - t)})
        out.sort(key=lambda c: -c["max_dbz"])
        return out

    def summary(self, t: float) -> dict:
        d = {
            "seed": self.seed,
            "temperature": {"surface_c": self.surface_c, "tropopause_fl": self.tropopause_fl,
                            "freezing_level_fl": int(self.freezing_level_ft / 100),
                            "isa_deviation_at_fl350_c": round(self.isa_dev(350), 1),
                            "isa_deviation_used_for_performance_c": self.isa_deviation_c},
            "wind_profile_at_centre": {int(f): describe_uv(*self.at(0.0, 0.0, f, t)) for f in range(0, 801, 50)},
            "jet": self.jet,
            "storm_motion": describe_toward(tuple(a * KT_TO_NM_S for a in self.steering())),
            "convection": self.regimes_text(),
            "cells": self.cells_summary(t),
        }
        if getattr(self, "lines", None):
            d["squall_line"] = self.lines[0]
            d["squall_lines"] = self.lines
        return d

    def regimes_text(self) -> str:
        convs = self.spec.get("convection") or {}
        convs = convs if isinstance(convs, list) else [convs]
        names = [f"{c.get('regime')} ({c.get('intensity', 'strong')})" for c in convs
                 if c and c.get("regime", "none") != "none"]
        if self.spec.get("cells"):
            names.append(f"{len(self.spec['cells'])} placed cell(s)")
        if self.spec.get("stratiform"):
            names.append(f"{len(self.spec['stratiform'])} stratiform area(s)")
        return " + ".join(names) or "none"

    def hazards(self, fl: float, t: float, threshold_dbz: float = 30.0, res_nm: float = 1.5,
                bbox=None) -> List[dict]:
        """Hazard areas at a level as polygons (contour of reflectivity >= threshold)."""
        import contourpy
        bbox = bbox or self.view_bbox
        xs, ys, z = self.cappi(fl, t, bbox, res_nm)
        gen = contourpy.contour_generator(xs, ys, z)
        out = []
        for poly in gen.lines(threshold_dbz):
            if len(poly) < 4:
                continue
            px, py = poly[:, 0], poly[:, 1]
            area = 0.5 * abs(np.dot(px, np.roll(py, 1)) - np.dot(py, np.roll(px, 1)))
            if area < 2.0:
                continue
            m = (xs[:, None] >= px.min()) & (xs[:, None] <= px.max())
            sub = z[np.ix_((ys >= py.min()) & (ys <= py.max()), m[:, 0])]
            step = max(1, len(poly) // 40)
            out.append({"max_dbz": round(float(sub.max()), 1) if sub.size else None,
                        "area_nm2": round(float(area), 1),
                        "centroid": [round(float(px.mean()), 1), round(float(py.mean()), 1)],
                        "polygon": [[round(float(a), 1), round(float(b), 1)] for a, b in poly[::step]]})
        out.sort(key=lambda h: -(h["max_dbz"] or 0))
        return out


def describe_toward(mv) -> dict:
    """Motion vector given in NM/s -> {'toward': deg, 'kt': speed}."""
    u, v = mv[0] / KT_TO_NM_S, mv[1] / KT_TO_NM_S
    spd = math.hypot(u, v)
    return {"toward": int(round(math.degrees(math.atan2(u, v)) % 360)), "kt": round(spd)}
