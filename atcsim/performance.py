"""Aircraft-type performance built from OpenAP (TU Delft, LGPL-3.0: https://github.com/junzis/openap).

For every type, OpenAP gives:
  * maximum climb thrust and idle thrust as a function of altitude and TAS (engine models)
  * a clean-configuration drag polar (parasitic + induced + wave drag), which depends on mass
  * fuel flow as a function of thrust
  * MTOW / OEW, ceiling, VMO / MMO, wing area, and WRAP statistics of real cruise Mach

OpenAP's raw models are not consistent across types (e.g. its B777/A350 polars give L/D ~13-14 at cruise,
versus ~19-20 in service, so those aircraft could barely hold their filed level). A per-type calibration
(data/type_calibration.json) fixes this with two factors per type:
  * drag factor  : CD0 and k scaled together so that L/D max matches a reference value
  * thrust factor: max climb thrust scaled so that the max FL at mid weight matches a reference value
Fuel flow is not calibrated (except an explicit fuel_factor for the few types whose OpenAP engine fuel model is
clearly off), so cruise fuel burn serves as an independent check of the result.

Everything is then sampled once per type into fast lookup tables on (pressure altitude, TAS):
  T_climb(h, tas), T_idle(h, tas), and drag at 7 masses interpolated in mass^2.
"""
from __future__ import annotations

import json
import math
import os
import warnings
from bisect import bisect_right
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import atmosphere as atm

warnings.filterwarnings("ignore", module="openap")

ALT_GRID = np.arange(0.0, 55001.0, 500.0)        # ft
TAS_GRID = np.arange(100.0, 701.0, 5.0)          # kt
CL_BUFFET = 0.8          # buffet-onset lift coefficient used for the high-altitude minimum speed
BUFFET_G = 1.3           # 1.3 g buffet margin (0.3 g manoeuvre protection)
MIN_ROC_FPM = 300.0      # a level is "available" if the aircraft can still climb at >= 300 ft/min
BANK_NOMINAL_DEG = 25.0  # autopilot bank limit in cruise
ROLL_RATE_DEG_S = 5.0


TEMP_THRUST_LAPSE = 0.008   # max climb thrust loss per degC above the flat-rating temperature (BADA-style)
FLAT_RATING_DT = 10.0       # engines are flat-rated up to about ISA+10


def _pressure(h):
    h = np.asarray(h, float)
    p_trop = atm.P0 * ((atm.T0 + atm.BETA_T * atm.H_TROP) / atm.T0) ** (-atm.G0 / (atm.BETA_T * atm.R))
    return np.where(h < atm.H_TROP,
                    atm.P0 * ((atm.T0 + atm.BETA_T * h) / atm.T0) ** (-atm.G0 / (atm.BETA_T * atm.R)),
                    p_trop * np.exp(-atm.G0 / (atm.R * (atm.T0 + atm.BETA_T * atm.H_TROP)) * (h - atm.H_TROP)))


def _temperature(h, dT):
    h = np.asarray(h, float)
    return np.where(h < atm.H_TROP, atm.T0 + dT + atm.BETA_T * h, atm.T0 + dT + atm.BETA_T * atm.H_TROP)


def _sound_speed(h, dT):
    return np.sqrt(atm.KAPPA * atm.R * _temperature(h, dT))


class Table2D:
    __slots__ = ("xs", "ys", "v")

    def __init__(self, xs: np.ndarray, ys: np.ndarray, v: np.ndarray):
        self.xs, self.ys = list(map(float, xs)), list(map(float, ys))
        self.v = v.tolist()

    def __call__(self, x: float, y: float) -> float:
        xs, ys, v = self.xs, self.ys, self.v
        x = min(max(x, xs[0]), xs[-1])
        y = min(max(y, ys[0]), ys[-1])
        i = min(max(bisect_right(xs, x) - 1, 0), len(xs) - 2)
        j = min(max(bisect_right(ys, y) - 1, 0), len(ys) - 2)
        fx = (x - xs[i]) / (xs[i + 1] - xs[i])
        fy = (y - ys[j]) / (ys[j + 1] - ys[j])
        a = v[i][j] + fy * (v[i][j + 1] - v[i][j])
        b = v[i + 1][j] + fy * (v[i + 1][j + 1] - v[i + 1][j])
        return a + fx * (b - a)


CALIBRATION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "type_calibration.json")
with open(CALIBRATION_FILE) as _f:
    CALIBRATION: Dict[str, dict] = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

MILITARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "military_types.json")
with open(MILITARY_FILE) as _f:
    MILITARY: Dict[str, dict] = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}

# ICAO designators supported (OpenAP codes; synonyms fill gaps for types without their own drag/engine data),
# plus the parametric military types
CIVIL = sorted(CALIBRATION)
SUPPORTED = sorted(set(CALIBRATION) | set(MILITARY))
DEFAULT_LOAD = 0.5   # default mass = OEW + 0.5 * (MTOW - OEW): a typical mid-flight mass


class TypePerformance:
    def __init__(self, icao: str, dT: float = 0.0):
        from openap import Drag, FuelFlow, Thrust, WRAP, prop
        code = icao.lower()
        self.icao = icao.upper()
        self.dT = dT
        p = prop.aircraft(code, use_synonym=True)
        self.name = p["aircraft"]
        self.mtow = float(p["mtow"])
        self.oew = float(p["oew"])
        self.ceiling_ft = float(p["ceiling"]) / atm.FT
        self.mmo = float(p["mmo"])
        self.vmo = float(p["vmo"]) if p.get("vmo") else 340.0
        self.wing_area = float(p["wing"]["area"])
        thrust, drag = Thrust(code, use_synonym=True), Drag(code, use_synonym=True)
        fuel = FuelFlow(code, use_synonym=True)
        self.model_source = getattr(drag, "ac", code).upper()
        try:
            cm = WRAP(code, use_synonym=True).cruise_mach()
            self.cruise_mach = float(cm["default"])
            self.cruise_mach_min, self.cruise_mach_max = float(cm["minimum"]), float(cm["maximum"])
        except Exception:
            self.cruise_mach, self.cruise_mach_min, self.cruise_mach_max = 0.78, 0.74, 0.80
        cal = CALIBRATION.get(self.icao, {})
        if "cruise_mach" in cal:
            self.cruise_mach = float(cal["cruise_mach"])
            self.cruise_mach_min, self.cruise_mach_max = self.cruise_mach - 0.03, self.cruise_mach + 0.02
        self.cruise_mach = min(self.cruise_mach, self.mmo - 0.02)
        # ATC-assignable ranges (operational, not aerodynamic, limits)
        self.mach_assign_max = round(min(self.mmo - 0.01, self.cruise_mach_max + 0.03), 2)
        self.mach_assign_min = round(max(0.60, min(self.cruise_mach_min - 0.06, self.mach_assign_max - 0.1)), 2)
        self.cas_assign_max = float(int(self.vmo - 5))
        self.cas_assign_min = 210.0
        # the aircraft's own (company) speed schedule: CAS below the crossover altitude, Mach above
        self.cas_schedule = float(cal.get("cas_schedule", min(self.vmo - 30.0, 300.0)))
        self.crossover_ft = atm.crossover_altitude(self.cas_schedule * atm.KT, self.cruise_mach, dT) / atm.FT

        # ---- calibration (always derived at ISA, so it does not depend on the scenario temperature)
        pol = drag.polar["clean"]
        self.raw_cd0, self.raw_k = float(pol["cd0"]), float(pol["k"])
        self.raw_ld_max = 1.0 / (2.0 * math.sqrt(self.raw_cd0 * self.raw_k))
        self.drag_factor = self.raw_ld_max / float(cal["ld_max"]) if "ld_max" in cal else 1.0
        pol["cd0"] = self.raw_cd0 * self.drag_factor
        pol["k"] = self.raw_k * self.drag_factor
        self.ld_max = 1.0 / (2.0 * math.sqrt(pol["cd0"] * pol["k"]))
        self.thrust_factor = 1.0
        self.max_fl_target = cal.get("max_fl_mid")
        if self.max_fl_target:
            # residual climb of MIN_ROC_FPM exactly half-way to the next level -> max FL == target
            m = self.oew + DEFAULT_LOAD * (self.mtow - self.oew)
            h_ft = self.max_fl_target * 100.0 + 500.0
            h = h_ft * atm.FT
            V = atm.mach_to_tas(self.cruise_mach, h)
            esf = atm.energy_share_factor("mach", self.cruise_mach, h)
            self._sweep = math.radians(float(p["wing"].get("sweep") or 25.0))
            self._tc = float(p["wing"].get("t/c") or 0.12)
            self._cd0, self._k = float(pol["cd0"]), float(pol["k"])
            D0 = float(self._drag_raw(np.array(h_ft), np.array(V / atm.KT), m, 0.0))
            T0 = float(thrust.climb(tas=V / atm.KT, alt=h_ft, roc=0, dT=0))
            need = MIN_ROC_FPM * atm.FT / 60.0 * m * atm.G0 / (V * esf) + D0
            self.thrust_factor = max(0.6, min(1.6, need / T0))

        # ---- lookup tables on (pressure altitude, TAS) for this temperature deviation.
        # OpenAP's atmosphere treats altitude as geometric and shifts pressure with dT; flight levels are pressure
        # altitudes, so drag is computed here from the (calibrated) polar with the simulator's atmosphere, and
        # thrust is taken from OpenAP at ISA for the same Mach, then corrected for temperature (flat rating).
        wing = p["wing"]
        self._sweep = math.radians(float(wing.get("sweep") or 25.0))
        self._tc = float(wing.get("t/c") or 0.12)
        self._cd0, self._k = float(pol["cd0"]), float(pol["k"])
        H, V = np.meshgrid(ALT_GRID, TAS_GRID, indexing="ij")
        zeros = np.zeros_like(H)
        mach = V * atm.KT / _sound_speed(H * atm.FT, dT)
        tas_isa = mach * _sound_speed(H * atm.FT, 0.0) / atm.KT
        t_corr = np.clip(1.0 - TEMP_THRUST_LAPSE * max(dT - FLAT_RATING_DT, 0.0), 0.6, 1.0)
        self.t_climb = Table2D(ALT_GRID, TAS_GRID, t_corr * self.thrust_factor *
                               np.asarray(thrust.climb(tas=tas_isa, alt=H, roc=zeros, dT=0), float))
        self.t_idle = Table2D(ALT_GRID, TAS_GRID, np.asarray(thrust.descent_idle(tas=tas_isa, alt=H, dT=0), float))
        # drag tables at several masses, interpolated in mass^2 (exact for induced drag; wave drag also varies with
        # lift, which is why a single quadratic fit is not enough near the critical Mach)
        self._m2 = [float(m) ** 2 for m in np.linspace(self.oew * 0.9, self.mtow, 7)]
        self._dtabs = [Table2D(ALT_GRID, TAS_GRID, self._drag_raw(H, V, math.sqrt(m2), dT)) for m2 in self._m2]
        tmax = self.thrust_factor * float(np.nanmax(np.asarray(thrust.climb(tas=250, alt=0, roc=0))))
        self._ff_thr = np.linspace(0, tmax * 1.05, 120)
        self.fuel_factor = float(cal.get("fuel_factor", 1.0))
        self._ff_val = self.fuel_factor * np.asarray(fuel.at_thrust(self._ff_thr), float)

    def _drag_raw(self, H_ft, V_kt, mass, dT):
        """Clean drag (N): parabolic polar + compressibility (wave) drag, as OpenAP (Gur et al. 2010), evaluated with
        the simulator's pressure-altitude atmosphere."""
        h = np.asarray(H_ft, float) * atm.FT
        p = _pressure(h)
        T = _temperature(h, dT)
        rho = p / (atm.R * T)
        Vm = np.asarray(V_kt, float) * atm.KT
        q = 0.5 * rho * Vm * Vm
        mach = Vm / np.sqrt(atm.KAPPA * atm.R * T)
        cl = mass * atm.G0 / np.maximum(q * self.wing_area, 1e-6)
        cs = math.cos(self._sweep)
        mcrit = 0.95 / cs - self._tc / cs ** 2 - 0.1 * cl / cs ** 3 - 0.108
        dcdw = 20.0 * np.maximum(mach - mcrit, 0.0) ** 4
        return (self._cd0 + dcdw + self._k * cl * cl) * q * self.wing_area

    def __deepcopy__(self, memo):          # shared, immutable lookup tables
        return self

    # ---------------------------------------------------------------- forces (N), alt ft, tas kt, mass kg
    def thrust_climb(self, alt_ft: float, tas_kt: float) -> float:
        return self.t_climb(alt_ft, tas_kt)

    def thrust_idle(self, alt_ft: float, tas_kt: float) -> float:
        return self.t_idle(alt_ft, tas_kt)

    def drag(self, alt_ft: float, tas_kt: float, mass: float) -> float:
        m2 = mass * mass
        g = self._m2
        i = min(max(bisect_right(g, m2) - 1, 0), len(g) - 2)
        f = (m2 - g[i]) / (g[i + 1] - g[i])
        a = self._dtabs[i](alt_ft, tas_kt)
        return a + f * (self._dtabs[i + 1](alt_ft, tas_kt) - a)

    military = False
    rvsm_default = True

    def fuel_flow(self, thrust_n: float, tas_kt: Optional[float] = None) -> float:
        """kg/s for the whole aircraft."""
        return float(np.interp(max(thrust_n, 0.0), self._ff_thr, self._ff_val))

    # ---------------------------------------------------------------- derived performance
    def tas_for(self, mode: str, value: float, alt_ft: float) -> float:
        return _tas_for(mode, round(value, 4), round(alt_ft / 20.0) * 20.0, self.dT)

    def schedule(self, alt_ft: float) -> Tuple[str, float]:
        """Own speed schedule at an altitude: ('mach', M) above crossover, ('cas', kt) below."""
        if alt_ft >= self.crossover_ft:
            return "mach", self.cruise_mach
        if alt_ft < 10000.0:
            return "cas", min(250.0, self.cas_schedule)         # 250 kt below FL100
        return "cas", self.cas_schedule

    def roc_fpm(self, alt_ft: float, mode: str, value: float, mass: float, thrust: str = "climb") -> float:
        """Steady climb (descent if negative) rate at constant Mach/CAS, total-energy model with energy share factor."""
        tas = self.tas_for(mode, value, alt_ft)
        T = self.thrust_climb(alt_ft, tas) if thrust == "climb" else self.thrust_idle(alt_ft, tas)
        D = self.drag(alt_ft, tas, mass)
        mach = atm.tas_to_mach(tas * atm.KT, alt_ft * atm.FT, self.dT)
        esf = atm.energy_share_factor(mode, mach, alt_ft * atm.FT, self.dT)
        return (T - D) * tas * atm.KT / (mass * atm.G0) * esf / atm.FT * 60.0

    def min_tas_buffet(self, alt_ft: float, mass: float) -> float:
        rho = atm.density(alt_ft * atm.FT, self.dT)
        return math.sqrt(2 * mass * atm.G0 * BUFFET_G / (rho * self.wing_area * CL_BUFFET)) / atm.KT

    def level_available(self, fl: int, mode: str, value: float, mass: float) -> Tuple[bool, str]:
        alt = fl * 100.0
        if alt > self.ceiling_ft + 1:
            return False, f"FL{fl} above {self.icao} certified ceiling FL{int(self.ceiling_ft // 100)}"
        tas = self.tas_for(mode, value, alt)
        if tas < self.min_tas_buffet(alt, mass):
            return False, f"FL{fl} not available at present weight (buffet margin)"
        if self.roc_fpm(alt, mode, value, mass) < MIN_ROC_FPM:
            return False, f"FL{fl} not available at present weight (insufficient climb performance)"
        mach = atm.tas_to_mach(tas * atm.KT, alt * atm.FT, self.dT)
        if mach > self.mmo:
            return False, f"FL{fl} would exceed MMO at the present speed"
        return True, ""

    def max_fl(self, mode: str, value: float, mass: float) -> int:
        """Highest available level. A Mach target applies above the crossover altitude, the CAS schedule below it."""
        best = 0
        for fl in range(200, int(self.ceiling_ft // 100) + 1, 10):
            m_, v_ = (mode, value) if (mode == "cas" or fl * 100 >= self.crossover_ft) else ("cas", self.cas_schedule)
            ok, _ = self.level_available(fl, m_, v_, mass)
            if ok:
                best = fl
        return best

    def summary(self) -> dict:
        return {"icao": self.icao, "name": self.name, "model_source": self.model_source,
                "category": "military" if self.military else "civil", "rvsm_by_default": self.rvsm_default,
                "mtow_kg": self.mtow, "oew_kg": self.oew, "ceiling_fl": int(self.ceiling_ft // 100),
                "vmo_kt": self.vmo, "mmo": self.mmo, "wing_area_m2": self.wing_area,
                "typical_cruise_mach": self.cruise_mach,
                "speed_schedule": {"cas_kt": self.cas_schedule, "mach": self.cruise_mach,
                                   "crossover_fl": int(self.crossover_ft // 100)},
                "assignable_mach": [self.mach_assign_min, self.mach_assign_max],
                "assignable_ias_kt": [self.cas_assign_min, self.cas_assign_max],
                "calibration": {"openap_ld_max": round(self.raw_ld_max, 1), "ld_max": round(self.ld_max, 1),
                                "drag_factor": round(self.drag_factor, 3),
                                "thrust_factor": round(self.thrust_factor, 3),
                                "fuel_factor": self.fuel_factor,
                                "max_fl_mid_target": self.max_fl_target}}


class ParametricPerformance(TypePerformance):
    """Military types: parabolic polar + wave drag (same formulation as the civil types), generic engine lapse
    (turbofan: thrust ~ sigma^0.75 (1 + 0.25 M); turboprop: shaft power ~ sigma^0.6, thrust = eta P / V),
    thrust-specific (jets) or brake-specific (turboprops) fuel consumption. Max climb thrust is calibrated so the
    max FL at mid weight matches max_fl_mid, exactly like the civil calibration."""
    military = True

    def __init__(self, icao: str, dT: float = 0.0):
        d = MILITARY[icao]
        self.icao, self.dT, self.d = icao.upper(), dT, d
        self.name = d["name"]
        self.mtow, self.oew = float(d["mtow"]), float(d["oew"])
        self.ceiling_ft = float(d["ceiling_fl"]) * 100.0
        self.mmo, self.vmo = float(d["mmo"]), float(d["vmo"])
        self.wing_area = float(d["wing_area"])
        self.model_source = "parametric (military)"
        self.rvsm_default = bool(d.get("rvsm", False))
        self.cruise_mach = float(d["cruise_mach"])
        self.cruise_mach_min, self.cruise_mach_max = d["mach_assign"]
        self.mach_assign_min, self.mach_assign_max = map(float, d["mach_assign"])
        self.cas_assign_min, self.cas_assign_max = map(float, d["cas_assign"])
        self.cas_schedule = float(d["cas_schedule"])
        self.crossover_ft = atm.crossover_altitude(self.cas_schedule * atm.KT, self.cruise_mach, dT) / atm.FT
        k = 1.0 / (math.pi * float(d["aspect_ratio"]) * float(d["oswald"]))
        self._cd0, self._k = float(d["cd0"]), k
        self.raw_cd0, self.raw_k = self._cd0, k
        self.raw_ld_max = self.ld_max = 1.0 / (2.0 * math.sqrt(self._cd0 * k))
        self.drag_factor = 1.0
        self._sweep = math.radians(float(d.get("sweep_deg", 25)))
        self._tc = float(d.get("t_c", 0.1))
        self.turboprop = d["engine"] == "turboprop"
        self.fuel_factor = 1.0
        self.max_fl_target = d.get("max_fl_mid")
        self.thrust_factor = 1.0
        if self.max_fl_target:
            m = self.oew + DEFAULT_LOAD * (self.mtow - self.oew)
            h_ft = self.max_fl_target * 100.0 + 500.0
            h = h_ft * atm.FT
            mode, val = ("mach", self.cruise_mach) if h_ft >= self.crossover_ft else ("cas", self.cas_schedule)
            V = atm.mach_to_tas(val, h) if mode == "mach" else atm.cas_to_tas(val * atm.KT, h)
            mach = atm.tas_to_mach(V, h)
            esf = atm.energy_share_factor(mode, mach, h)
            D0 = float(self._drag_raw(np.array(h_ft), np.array(V / atm.KT), m, 0.0))
            T0 = float(self._thrust_raw(np.array(h_ft), np.array(V / atm.KT)))
            need = MIN_ROC_FPM * atm.FT / 60.0 * m * atm.G0 / (V * esf) + D0
            self.thrust_factor = max(0.4, min(2.5, need / T0))
        H, V = np.meshgrid(ALT_GRID, TAS_GRID, indexing="ij")
        mach = V * atm.KT / _sound_speed(H * atm.FT, dT)
        tas_isa = mach * _sound_speed(H * atm.FT, 0.0) / atm.KT
        t_corr = np.clip(1.0 - TEMP_THRUST_LAPSE * max(dT - FLAT_RATING_DT, 0.0), 0.6, 1.0)
        self.t_climb = Table2D(ALT_GRID, TAS_GRID, t_corr * self.thrust_factor * self._thrust_raw(H, tas_isa))
        self.t_idle = Table2D(ALT_GRID, TAS_GRID, self._thrust_raw(H, tas_isa, idle=True))
        self._m2 = [float(mm) ** 2 for mm in np.linspace(self.oew * 0.9, self.mtow, 7)]
        self._dtabs = [Table2D(ALT_GRID, TAS_GRID, self._drag_raw(H, V, math.sqrt(m2), dT)) for m2 in self._m2]

    def _thrust_raw(self, H_ft, V_kt, idle: bool = False):
        h = np.asarray(H_ft, float) * atm.FT
        sigma = _pressure(h) / (atm.R * _temperature(h, 0.0)) / 1.225
        Vm = np.maximum(np.asarray(V_kt, float), 1.0) * atm.KT
        d = self.d
        if self.turboprop:
            P = d["n_engines"] * d["power_kw"] * 1000.0 * 0.9 * sigma ** 0.6
            if idle:
                P = 0.04 * P
            return 0.82 * P / np.maximum(Vm, 120 * atm.KT)
        mach = Vm / _sound_speed(h, 0.0)
        T = d["n_engines"] * d["thrust_kn"] * 1000.0 * 0.92 * sigma ** 0.75 * (1 + 0.25 * mach)
        return 0.05 * d["n_engines"] * d["thrust_kn"] * 1000.0 * sigma if idle else T

    def fuel_flow(self, thrust_n: float, tas_kt: Optional[float] = None) -> float:
        T = max(thrust_n, 0.0)
        if self.turboprop:
            P_kw = T * max(tas_kt or 250.0, 120.0) * atm.KT / 0.82 / 1000.0
            return self.d["bsfc"] * max(P_kw, 0.05 * self.d["n_engines"] * self.d["power_kw"]) / 3600.0
        return self.d["tsfc"] * max(T, 0.03 * self.d["n_engines"] * self.d["thrust_kn"] * 1000) / 3600.0


@lru_cache(maxsize=200000)
def _tas_for(mode: str, value: float, alt_ft: float, dT: float) -> float:
    h = alt_ft * atm.FT
    if mode == "mach":
        return atm.mach_to_tas(value, h, dT) / atm.KT
    return atm.cas_to_tas(value * atm.KT, h, dT) / atm.KT


@lru_cache(maxsize=None)
def get(icao: str, dT: float = 0.0) -> TypePerformance:
    icao = icao.upper()
    if icao not in SUPPORTED:
        raise KeyError(f"unknown aircraft type {icao}; supported: {', '.join(SUPPORTED)}")
    if icao in MILITARY:
        return ParametricPerformance(icao, round(float(dT), 1))
    return TypePerformance(icao, round(float(dT), 1))


def catalogue(dT: float = 0.0) -> List[dict]:
    return [get(t, dT).summary() for t in SUPPORTED]
