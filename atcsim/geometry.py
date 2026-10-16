"""Sector geometry and small math helpers.

Coordinate convention (used everywhere in the simulator and the API):
  * Units: nautical miles (NM). Origin (0, 0) is the centre of the sector.
  * x grows to the EAST, y grows to the NORTH.
  * Headings / bearings are degrees true, 0 = north, 90 = east (1..360 in the API).
  * Altitudes are feet internally; the API speaks flight levels (FL350 = 35 000 ft).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

# --- Sector definition ------------------------------------------------------
SECTOR_HALF_NM = 60.0          # square sector, 120 x 120 NM
SECTOR_LOWER_FL = 245          # sector vertical limits (aircraft may be cleared FL250..FL460)
SECTOR_UPPER_FL = 460
SEP_H_NM = 5.0                 # en-route radar separation minima
SEP_V_FT = 1000.0

_H = SECTOR_HALF_NM
# 4 waypoints on the northern and southern boundaries (west -> east),
# 2 waypoints on the western and eastern boundaries (north -> south).
BOUNDARY_WAYPOINTS: Dict[str, Point] = {
    "N1": (-45.0, _H), "N2": (-15.0, _H), "N3": (15.0, _H), "N4": (45.0, _H),
    "S1": (-45.0, -_H), "S2": (-15.0, -_H), "S3": (15.0, -_H), "S4": (45.0, -_H),
    "W1": (-_H, 20.0), "W2": (-_H, -20.0),
    "E1": (_H, 20.0), "E2": (_H, -20.0),
}
SIDE_OF = {name: name[0] for name in BOUNDARY_WAYPOINTS}
OPPOSITE = {"N": "S", "S": "N", "W": "E", "E": "W"}


def route_network() -> List[List[str]]:
    """Every published route: each boundary waypoint to every waypoint on the opposite side.

    This yields routes that cross each other (N1->S3 x N3->S1) and routes that converge
    on the same exit point (N1->S2 and N3->S2), in both directions.
    """
    routes = []
    for a, sa in SIDE_OF.items():
        for b, sb in SIDE_OF.items():
            if OPPOSITE[sa] == sb:
                routes.append([a, b])
    routes += [list(r) for r in EXTRA_ROUTES]
    return routes


# additional published routes (north-east arrival flows towards E1 / E2)
EXTRA_ROUTES = [("N3", "E1"), ("N3", "E2"), ("N4", "E1"), ("N4", "E2")]


# --- Vector helpers -----------------------------------------------------------
def dist(p: Point, q: Point) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def bearing(p: Point, q: Point) -> float:
    """True bearing from p to q in degrees [0, 360)."""
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360.0


def heading_diff(from_hdg: float, to_hdg: float) -> float:
    """Signed shortest turn from from_hdg to to_hdg, in (-180, 180]. Positive = right."""
    d = (to_hdg - from_hdg + 540.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def norm_hdg(h: float) -> float:
    h = h % 360.0
    return h


def api_hdg(h: float) -> int:
    """Heading as controllers say it: 1..360 (north = 360)."""
    v = int(round(h)) % 360
    return 360 if v == 0 else v


def velocity(hdg: float, spd_kt: float) -> Point:
    r = math.radians(hdg)
    return (spd_kt * math.sin(r), spd_kt * math.cos(r))


def segment_intersection(p1: Point, p2: Point, q1: Point, q2: Point) -> Optional[Point]:
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])
    return None


def project_on_polyline(points: Sequence[Point], pt: Point) -> Tuple[float, float, int]:
    """Project pt onto a polyline.

    Returns (distance along the polyline to the projected point, lateral offset in NM,
    index of the segment the projection lies on).
    """
    best = (float("inf"), 0.0, 0)
    along_start = 0.0
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg = dist(a, b)
        if seg < 1e-9:
            continue
        t = ((pt[0] - a[0]) * (b[0] - a[0]) + (pt[1] - a[1]) * (b[1] - a[1])) / (seg * seg)
        t = max(0.0, min(1.0, t))
        proj = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        off = dist(proj, pt)
        if off < best[0]:
            best = (off, along_start + t * seg, i)
        along_start += seg
    return best[1], best[0], best[2]


def point_along_polyline(points: Sequence[Point], along: float) -> Tuple[Point, int]:
    """Point at a given distance along the polyline, plus the index of the NEXT vertex."""
    remaining = along
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg = dist(a, b)
        if remaining <= seg or i == len(points) - 2:
            t = 0.0 if seg < 1e-9 else min(1.0, remaining / seg)
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), i + 1
        remaining -= seg
    return points[-1], len(points) - 1


def polyline_length(points: Sequence[Point]) -> float:
    return sum(dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def point_in_polygon(pt: Point, poly: Sequence[Point]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xin = (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
            if x < xin:
                inside = not inside
        j = i
    return inside


def inside_sector(pt: Point, margin: float = 0.0) -> bool:
    return abs(pt[0]) <= SECTOR_HALF_NM + margin and abs(pt[1]) <= SECTOR_HALF_NM + margin
