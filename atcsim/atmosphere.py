"""ISA atmosphere (with temperature deviation), airspeed conversions and the energy share factor.

Formulas follow the BADA 3 user manual (EUROCONTROL), section 3.1-3.2, which in turn follow ISA (ICAO Doc 7488).
All internal units SI: metres, m/s, kelvin, pascal.
"""
from __future__ import annotations

import math

KT = 0.514444            # m/s per knot
FT = 0.3048              # m per foot
NM = 1852.0              # m per nautical mile
G0 = 9.80665
R = 287.05287
KAPPA = 1.4
MU = (KAPPA - 1) / KAPPA
T0 = 288.15
P0 = 101325.0
RHO0 = 1.225
BETA_T = -0.0065
H_TROP = 11000.0


def temperature(h: float, dT: float = 0.0) -> float:
    return T0 + dT + BETA_T * h if h < H_TROP else T0 + dT + BETA_T * H_TROP


def pressure(h: float, dT: float = 0.0) -> float:
    if h < H_TROP:
        return P0 * ((T0 + BETA_T * h) / T0) ** (-G0 / (BETA_T * R))
    p_trop = P0 * ((T0 + BETA_T * H_TROP) / T0) ** (-G0 / (BETA_T * R))
    return p_trop * math.exp(-G0 / (R * (T0 + BETA_T * H_TROP)) * (h - H_TROP))


def density(h: float, dT: float = 0.0) -> float:
    return pressure(h, dT) / (R * temperature(h, dT))


def sound_speed(h: float, dT: float = 0.0) -> float:
    return math.sqrt(KAPPA * R * temperature(h, dT))


def tas_to_mach(tas: float, h: float, dT: float = 0.0) -> float:
    return tas / sound_speed(h, dT)


def mach_to_tas(m: float, h: float, dT: float = 0.0) -> float:
    return m * sound_speed(h, dT)


def cas_to_tas(cas: float, h: float, dT: float = 0.0) -> float:
    p, rho = pressure(h, dT), density(h, dT)
    a = (1 + MU / 2 * RHO0 / P0 * cas * cas) ** (1 / MU) - 1
    b = (1 + P0 / p * a) ** MU - 1
    return math.sqrt(2 / MU * p / rho * b)


def tas_to_cas(tas: float, h: float, dT: float = 0.0) -> float:
    p, rho = pressure(h, dT), density(h, dT)
    a = (1 + MU / 2 * rho / p * tas * tas) ** (1 / MU) - 1
    b = (1 + p / P0 * a) ** MU - 1
    return math.sqrt(2 / MU * P0 / RHO0 * b)


def crossover_altitude(cas: float, mach: float, dT: float = 0.0) -> float:
    """Altitude (m) where the given CAS and Mach give the same TAS."""
    lo, hi = 0.0, 20000.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if cas_to_tas(cas, mid, dT) < mach_to_tas(mach, mid, dT):
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def energy_share_factor(mode: str, mach: float, h: float, dT: float = 0.0) -> float:
    """Fraction of excess power going into altitude change while holding Mach or CAS (BADA 3, eq. 3.2-8..11)."""
    k = KAPPA
    T = temperature(h, dT)
    trop = h < H_TROP
    if mode == "mach":
        if not trop:
            return 1.0
        return 1.0 / (1 + (k * R * BETA_T) / (2 * G0) * mach * mach * (T - dT) / T)
    # constant CAS
    q = 1 + (k - 1) / 2 * mach * mach
    term = q ** (-1 / (k - 1)) * (q ** (k / (k - 1)) - 1)
    if trop:
        return 1.0 / (1 + (k * R * BETA_T) / (2 * G0) * mach * mach * (T - dT) / T + term)
    return 1.0 / (1 + term)
