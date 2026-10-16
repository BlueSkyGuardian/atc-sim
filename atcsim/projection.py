"""Map projection between geographic coordinates and the simulator's flat NM plane.

The simulator works on a plane (x east, y north, NM). Real airspaces are defined in lat/lon, so they are projected
with a spherical Lambert conformal conic projection (the projection of ICAO en-route charts). With standard parallels
either side of the airspace the scale error is a few tenths of a percent, so distances and separations measured on
the plane are within a few hundred metres of the great-circle values over a 400 NM wide CTA.

"Grid north" (the +y axis) is true north on the central meridian. Away from it the two differ by the meridian
convergence n * (lon - lon0), about 2 degrees at the edges of the Agadir CTA. Headings and bearings in the API are
grid headings, so they are true headings to within that convergence.
"""
from __future__ import annotations

import math
import re
from typing import Tuple

R_NM = 3440.065          # mean Earth radius in NM


class LambertConformal:
    def __init__(self, lat0: float, lon0: float, lat1: float, lat2: float):
        self.lat0, self.lon0, self.lat1, self.lat2 = lat0, lon0, lat1, lat2
        p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
        t = lambda p: math.tan(math.pi / 4 + p / 2)
        if abs(lat1 - lat2) < 1e-9:
            self.n = math.sin(p1)
        else:
            self.n = math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
        self.F = math.cos(p1) * t(p1) ** self.n / self.n
        self.rho0 = R_NM * self.F / t(p0) ** self.n

    def forward(self, lat: float, lon: float) -> Tuple[float, float]:
        rho = R_NM * self.F / math.tan(math.pi / 4 + math.radians(lat) / 2) ** self.n
        th = self.n * math.radians(lon - self.lon0)
        return rho * math.sin(th), self.rho0 - rho * math.cos(th)

    def inverse(self, x: float, y: float) -> Tuple[float, float]:
        dy = self.rho0 - y
        rho = math.copysign(math.hypot(x, dy), self.n)
        th = math.atan2(x, dy)
        lat = 2 * math.atan((R_NM * self.F / rho) ** (1 / self.n)) - math.pi / 2
        return math.degrees(lat), self.lon0 + math.degrees(th / self.n)

    def convergence_deg(self, lon: float) -> float:
        """Grid north minus true north at a longitude (true heading = grid heading + convergence)."""
        return self.n * (lon - self.lon0)

    def spec(self) -> dict:
        return {"type": "lambert_conformal_conic", "lat0": self.lat0, "lon0": self.lon0,
                "standard_parallels": [self.lat1, self.lat2], "earth_radius_nm": R_NM,
                "units": "NM", "axes": "x east, y north (grid north = true north at lon0)"}


def destination(lat: float, lon: float, brg_deg: float, dist_nm: float) -> Tuple[float, float]:
    """Great-circle destination point."""
    p1, l1, b, d = math.radians(lat), math.radians(lon), math.radians(brg_deg), dist_nm / R_NM
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def gc_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def gc_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(a))


_DMS = re.compile(r"^\s*(\d{2})(\d{2})(\d{2}(?:[.,]\d+)?)([NS])\s+(\d{3})(\d{2})(\d{2}(?:[.,]\d+)?)([EW])\s*$")


def parse_dms(text: str) -> Tuple[float, float]:
    """'321500N 0143700W' or '303021.5N 0122224W' (AIP format) -> (lat, lon) in decimal degrees."""
    m = _DMS.match(text.upper())
    if not m:
        raise ValueError(f"bad coordinate {text!r}")
    la = int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3).replace(",", ".")) / 3600
    lo = int(m.group(5)) + int(m.group(6)) / 60 + float(m.group(7).replace(",", ".")) / 3600
    return (la if m.group(4) == "N" else -la), (lo if m.group(8) == "E" else -lo)


def format_dms(lat: float, lon: float) -> str:
    def part(v, w, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = (v - d - m / 60) * 3600
        if s >= 59.995:
            s, m = 0.0, m + 1
        if m == 60:
            m, d = 0, d + 1
        return f"{d:0{w}d}{m:02d}{s:05.2f}{h}"
    return f"{part(lat, 2, 'N', 'S')} {part(lon, 3, 'E', 'W')}"
