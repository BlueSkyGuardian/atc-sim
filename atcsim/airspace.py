"""Airspaces: the controlled volume, its named points, published routes, sectors and adjacent aerodromes.

Two kinds are provided:
  * "training"  : the original 120 x 120 NM synthetic square (N1-N4, S1-S4, W1/W2, E1/E2), flat NM coordinates.
  * "agadir"    : the real Agadir CTA (Casablanca FIR, GMMM) built from AIP Maroc ENR 4.4 coordinates, the CTA
                  boundary table and the ENR charts, projected onto the simulator plane (Lambert conformal conic,
                  see projection.py). Four sectors: Oceanic North, West North, West South, Oceanic South.

A scenario picks the airspace with "airspace": "agadir" and, optionally, the sectors the agent controls with
"control": ["WS"] (default: all of them, i.e. the whole CTA band-boxed). The controlled area is the union of
those sectors; aircraft enter and leave it at the named points lying on its boundary.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import geometry as g
from .geometry import Point, dist
from .projection import LambertConformal, destination, format_dms, gc_bearing, parse_dms

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "airspaces")
ON_BOUNDARY_NM = 0.75      # a named point this close to the controlled boundary is a transfer (entry/exit) point
FIR_POINT_TOLERANCE_NM = 2.0   # published FIR entry/exit points (FRA E/X) sit within this of the drawn boundary
RVSM_LOWER_FL = 290
RVSM_UPPER_FL = 410


@dataclass
class Sector:
    id: str
    name: str
    polygon: List[Point]
    freq: Optional[str] = None
    backup_freq: Optional[str] = None

    def snapshot(self, proj=None) -> dict:
        d = {"id": self.id, "name": self.name, "frequency": self.freq, "backup_frequency": self.backup_freq,
             "polygon": [[round(x, 2), round(y, 2)] for x, y in self.polygon]}
        return d


@dataclass
class Airspace:
    id: str
    name: str
    polygon: List[Point]                         # controlled area (NM plane)
    waypoints: Dict[str, Point]
    lower_fl: int
    upper_fl: int
    routes: List[List[str]] = field(default_factory=list)
    airways: Dict[str, dict] = field(default_factory=dict)
    sectors: List[Sector] = field(default_factory=list)
    controlled: List[str] = field(default_factory=list)
    outline: List[Point] = field(default_factory=list)   # whole CTA (= polygon when everything is controlled)
    projection: Optional[LambertConformal] = None
    point_info: Dict[str, dict] = field(default_factory=dict)
    default_aerodromes: List[dict] = field(default_factory=list)
    default_sequencing: List[dict] = field(default_factory=list)
    unit: str = ""
    notes: dict = field(default_factory=dict)
    square_half_nm: Optional[float] = None

    def __post_init__(self):
        self._poly = np.array(self.polygon, dtype=float)
        self._edges = [(self.polygon[i], self.polygon[(i + 1) % len(self.polygon)]) for i in range(len(self.polygon))]
        self.boundary_points: Dict[str, Point] = {}
        for k, p in self.waypoints.items():
            d = self.distance_to_boundary(p)
            fra = str(self.point_info.get(k, {}).get("fra", ""))
            if d <= ON_BOUNDARY_NM or (d <= FIR_POINT_TOLERANCE_NM and ("E" in fra or "X" in fra)):
                self.boundary_points[k] = p
        xs = [p[0] for p in self.outline or self.polygon]
        ys = [p[1] for p in self.outline or self.polygon]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def __deepcopy__(self, memo):          # immutable during a session
        return self

    # ---------------------------------------------------------------- geometry
    def inside(self, pt: Point) -> bool:
        if self.square_half_nm is not None:
            return abs(pt[0]) <= self.square_half_nm and abs(pt[1]) <= self.square_half_nm
        return g.point_in_polygon(pt, self.polygon)

    def inside_mask(self, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
        x, y = np.ravel(np.asarray(X, float)), np.ravel(np.asarray(Y, float))
        inside = np.zeros(x.shape, dtype=bool)
        px, py = self._poly[:, 0], self._poly[:, 1]
        qx, qy = np.roll(px, 1), np.roll(py, 1)
        for x1, y1, x2, y2 in zip(px, py, qx, qy):       # even-odd ray casting, vectorised over points
            crosses = (y1 > y) != (y2 > y)
            xin = (x2 - x1) * (y - y1) / ((y2 - y1) if y2 != y1 else 1e-15) + x1
            inside ^= crosses & (x < xin)
        return inside.reshape(np.shape(X))

    def distance_to_boundary(self, pt: Point) -> float:
        best = 1e18
        for a, b in self._edges:
            best = min(best, _seg_dist(pt, a, b))
        return best

    def nearest_transfer_point(self, pt: Point) -> Tuple[Optional[str], float]:
        best, d = None, 1e18
        for k, p in self.boundary_points.items():
            dd = dist(pt, p)
            if dd < d:
                best, d = k, dd
        return best, d

    def to_xy(self, lat: float, lon: float) -> Point:
        if self.projection is None:
            raise ValueError(f"airspace {self.id} has no geographic projection")
        return self.projection.forward(lat, lon)

    def to_latlon(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        return None if self.projection is None else self.projection.inverse(x, y)

    def resolve_point(self, v) -> Point:
        """A scenario coordinate: [x, y] in NM, {"lat":..,"lon":..}, an AIP string or a waypoint name."""
        if isinstance(v, str):
            if v.upper() in self.waypoints:
                return self.waypoints[v.upper()]
            return self.to_xy(*parse_dms(v))
        if isinstance(v, dict):
            if "lat" in v:
                return self.to_xy(float(v["lat"]), float(v["lon"]))
            if "coord" in v:
                return self.to_xy(*parse_dms(v["coord"]))
            return float(v["x"]), float(v["y"])
        return float(v[0]), float(v[1])

    def sector_of(self, pt: Point) -> Optional[str]:
        for s in self.sectors:
            if g.point_in_polygon(pt, s.polygon):
                return s.id
        return None

    def entry_exit_problems(self, names: Sequence[str]) -> List[str]:
        """A route must start at a point open to entries and end at one open to exits (FRA E / X designators)."""
        out = []
        fra = lambda n: str(self.point_info.get(n, {}).get("fra", ""))
        if names and fra(names[0]) and "E" not in fra(names[0]) and names[0] in self.boundary_points:
            out.append(f"{names[0]} is an exit-only point")
        if names and fra(names[-1]) and "X" not in fra(names[-1]) and names[-1] in self.boundary_points:
            out.append(f"{names[-1]} is an entry-only point")
        return out

    def published_legs(self) -> set:
        """Directed legs (a, b) of every published routing: airways (both ways unless one-way), standard routes and
        RAD direct routings (only in their published direction)."""
        legs = getattr(self, "_legs", None)
        if legs is None:
            legs = set()
            for r in self.routes:
                legs.update(zip(r, r[1:]))
            self._legs = legs
        return legs

    def against_one_way(self, names: Sequence[str]) -> List[str]:
        """Published one-way routings this sequence of points flies against: one-way airways, and standard / RAD
        routings flown backwards (a leg that is only published in the other direction)."""
        bad = []
        names = [n for n in names if not n.startswith("@")]
        legs = self.published_legs()
        for a, b in zip(names, names[1:]):
            for k, v in self.airways.items():
                pts = v["points"]
                if (v.get("one_way") and a in pts and b in pts and pts.index(b) < pts.index(a) and k not in bad
                        and (a, b) not in legs):         # not also a leg of a two-way route (UB11 / UN869 SLK-PELAX)
                    bad.append(k)
            if (b, a) in legs and (a, b) not in legs and not any(k in self.airways and a in self.airways[k]["points"]
                                                                and b in self.airways[k]["points"] for k in bad):
                for r in (self.notes.get("rad_routes") or []) + (self.notes.get("standard_routes") or []):
                    if a in r and b in r and r.index(b) < r.index(a):
                        tag = ("RAD " if r in (self.notes.get("rad_routes") or []) else "standard route ") + "-".join(r)
                        if tag not in bad:
                            bad.append(tag)
                        break
        return bad

    def route_string(self, names: Sequence[str]) -> str:
        """ICAO item-15 style route: consecutive points on one airway are written 'A UN857 B', the rest DCT."""
        names = [n for n in names if not n.startswith("@")]
        if not names:
            return ""
        def airway_of(a, b):
            for k, v in self.airways.items():
                pts = v["points"]
                if a in pts and b in pts and abs(pts.index(a) - pts.index(b)) == 1:
                    return k
            return None
        out, cur = [names[0]], None
        for a, b in zip(names, names[1:]):
            aw = airway_of(a, b) or "DCT"
            if aw == cur and aw != "DCT":
                out[-1] = b                       # extend the same airway
            else:
                out += [aw, b]
            cur = aw
        return " ".join(out)

    @staticmethod
    def rvsm_band() -> Tuple[int, int]:
        return RVSM_LOWER_FL, RVSM_UPPER_FL

    # ---------------------------------------------------------------- description
    def coast(self) -> Optional[dict]:
        """Land and coastlines (Natural Earth 1:10m) in this airspace's NM coordinates, when it is on real ground."""
        if not (self.notes or {}).get("coastline") or not self.projection:
            return None
        if getattr(self, "_coast", None) is None:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "coast.json"), encoding="utf-8") as f:
                d = json.load(f)
            P = lambda ll: [round(v, 1) for v in self.projection.forward(ll[1], ll[0])]  # noqa: E731
            self._coast = {"land": [[P(p) for p in ring] for ring in d["land"]],
                           "lines": [[P(p) for p in line] for line in d["coast"]], "source": d["_source"]}
        return self._coast

    def coast_clipped(self, box) -> Optional[dict]:
        """The coast within box (x0, y0, x1, y1) NM: land polygons clipped to it, coastlines cut at its edges."""
        c = self.coast()
        if not c:
            return None
        x0, y0, x1, y1 = box

        def clip(ring):
            edges = [(lambda p: p[0] >= x0, lambda a, b: [x0, a[1] + (x0 - a[0]) / (b[0] - a[0]) * (b[1] - a[1])]),
                     (lambda p: p[0] <= x1, lambda a, b: [x1, a[1] + (x1 - a[0]) / (b[0] - a[0]) * (b[1] - a[1])]),
                     (lambda p: p[1] >= y0, lambda a, b: [a[0] + (y0 - a[1]) / (b[1] - a[1]) * (b[0] - a[0]), y0]),
                     (lambda p: p[1] <= y1, lambda a, b: [a[0] + (y1 - a[1]) / (b[1] - a[1]) * (b[0] - a[0]), y1])]
            out = ring
            for ins, cut in edges:
                src, out = out, []
                for i, b in enumerate(src):
                    a = src[i - 1]
                    if ins(b):
                        if not ins(a):
                            out.append([round(v, 1) for v in cut(a, b)])
                        out.append(b)
                    elif ins(a):
                        out.append([round(v, 1) for v in cut(a, b)])
                if not out:
                    break
            return out
        land = [r for r in (clip(r) for r in c["land"]) if len(r) >= 3]
        inside = lambda p: x0 <= p[0] <= x1 and y0 <= p[1] <= y1  # noqa: E731
        lines = []
        for ln in c["lines"]:
            cur = []
            for p in ln:
                if inside(p):
                    cur.append(p)
                else:
                    if len(cur) > 1:
                        lines.append(cur)
                    cur = []
            if len(cur) > 1:
                lines.append(cur)
        return {"land": land, "lines": lines}

    def describe(self) -> dict:
        wps = {k: [round(p[0], 2), round(p[1], 2)] for k, p in self.waypoints.items() if not k.startswith("@")}
        out = {
            "id": self.id, "name": self.name, "unit": self.unit,
            "coordinates": "NM, x east, y north" + (" (Lambert conformal projection, see 'projection')"
                                                    if self.projection else ", origin at the sector centre"),
            "controlled_sectors": self.controlled,
            "shape": {"type": "polygon", "polygon": [[round(x, 2), round(y, 2)] for x, y in self.polygon]},
            "outline": [[round(x, 2), round(y, 2)] for x, y in (self.outline or self.polygon)],
            "bbox_nm": [round(v, 1) for v in self.bbox],
            "vertical_limits": {"lower_fl": self.lower_fl, "upper_fl": self.upper_fl},
            "rvsm": {"lower_fl": RVSM_LOWER_FL, "upper_fl": RVSM_UPPER_FL,
                     "rule": "1000 ft between RVSM-approved aircraft from FL290 to FL410 inclusive; 2000 ft between a "
                             "non-RVSM aircraft (not approved, unable RVSM, military formation) and any other aircraft "
                             "in that band, and 2000 ft above FL410"},
            "semicircular_rule": "southbound flights (direction of flight 090-269) at odd levels (FL250, 270, 290, 310 "
                                 "... 410, 450); northbound flights (270-089) at even levels (FL260, 280, 300 ... 400, "
                                 "430). The direction of flight is the bearing from the entry point to the exit point.",
            "waypoints": wps,
            "transfer_points": sorted(self.boundary_points),
            "routes": self.routes,
            "airways": self.airways,
            "sectors": [s.snapshot() for s in self.sectors],
        }
        if self.square_half_nm is not None:
            h = self.square_half_nm
            out["shape"].update({"type": "square", "half_size_nm": h,
                                 "corners": [[-h, h], [h, h], [h, -h], [-h, -h]]})
        if self.projection is not None:
            out["projection"] = self.projection.spec()
            out["waypoints_geo"] = {k: {"lat": round(ll[0], 5), "lon": round(ll[1], 5), "aip": format_dms(*ll),
                                        **{kk: vv for kk, vv in self.point_info.get(k, {}).items()
                                           if kk in ("role", "fra", "estimated", "provisional", "region")}}
                                    for k, p in self.waypoints.items() if not k.startswith("@")
                                    for ll in [self.projection.inverse(*p)]}
        out.update(self.notes)
        if getattr(self, "fir_sectors", None):
            # the rest of the FIR: the other control area's sectors (drawn, not ours) and the FIR outline
            names = getattr(self, "fir_cta_names", {}) or {}
            out["fir"] = {"outline": [[round(x, 2), round(y, 2)] for x, y in self.fir_outline],
                          "control_area": names.get(self.cta, self.cta),
                          "sectors": [{**s.snapshot(), "control_area": names.get(c, c)} for s, c in self.fir_sectors]}
            out["fir_external_points"] = sorted(getattr(self, "fir_external", []) or [])
        if getattr(self, "published_areas", None):
            out["published_areas_note"] = self.areas_note
        c = self.coast()
        if c:
            out["coast"] = c
        return out


def _seg_dist(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _line_intersection(p1: Point, p2: Point, q1: Point, q2: Point) -> Point:
    """Intersection of the infinite line p1-p2 with segment q1-q2 (extended if needed)."""
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    return p1[0] + t * d1[0], p1[1] + t * d1[1]


def _project_on_segment(p: Point, a: Point, b: Point) -> Point:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return a[0] + t * dx, a[1] + t * dy


def _union_ring(polys: List[List[Point]]) -> List[Point]:
    """Outer ring of the union of polygons that share whole edges (same vertices) - sectors of one CTA."""
    def key(p):
        return (round(p[0], 6), round(p[1], 6))
    edges = {}
    for poly in polys:
        if _signed_area(poly) < 0:
            poly = poly[::-1]
        for i in range(len(poly)):
            a, b = key(poly[i]), key(poly[(i + 1) % len(poly)])
            if a == b:
                continue
            if (b, a) in edges:
                del edges[(b, a)]          # shared internal edge
            else:
                edges[(a, b)] = True
    nxt = {}
    for a, b in edges:
        nxt.setdefault(a, []).append(b)
    start = min(nxt)
    ring, cur = [start], start
    for _ in range(len(edges) + 1):
        cur = nxt[cur].pop()
        if cur == start:
            break
        ring.append(cur)
    return [(float(x), float(y)) for x, y in ring]


def _signed_area(poly: Sequence[Point]) -> float:
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))


# ============================================================================= training square
def training(control: Optional[List[str]] = None) -> Airspace:
    h = g.SECTOR_HALF_NM
    poly = [(-h, h), (h, h), (h, -h), (-h, -h)]
    sq = Airspace(id="training", name="Training sector (synthetic 120 x 120 NM square)",
                  polygon=poly, outline=poly, waypoints=dict(g.BOUNDARY_WAYPOINTS),
                  lower_fl=g.SECTOR_LOWER_FL, upper_fl=g.SECTOR_UPPER_FL, routes=g.route_network(),
                  sectors=[Sector("TRN", "Training sector", poly)], controlled=["TRN"], square_half_nm=h)
    from .arrivals import DEFAULT_AERODROMES, DEFAULT_SEQUENCING
    sq.default_aerodromes, sq.default_sequencing = DEFAULT_AERODROMES, DEFAULT_SEQUENCING
    return sq


# ============================================================================= real airspaces from data files
@lru_cache(maxsize=None)
def _load_json(name: str) -> dict:
    path = os.path.join(DATA_DIR, f"{name}_cta.json")
    if not os.path.exists(path):
        path = os.path.join(DATA_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def available() -> List[str]:
    names = ["training"]
    for fn in sorted(os.listdir(DATA_DIR)):
        if fn.endswith(".json"):
            names.append(json.load(open(os.path.join(DATA_DIR, fn)))["id"])
    return names


def _arc_points(proj, c_ll, r_nm: float, a_ll, b_ll, direction: str) -> List[Point]:
    """Intermediate points of an arc of a circle from a to b (both excluded)."""
    b0 = gc_bearing(*c_ll, *a_ll)
    b1 = gc_bearing(*c_ll, *b_ll)
    span = (b0 - b1) % 360 if direction == "ccw" else -((b1 - b0) % 360)
    n = max(2, int(abs(span) / 2.0))
    return [proj.forward(*destination(*c_ll, b0 - span * k / n, float(r_nm))) for k in range(1, n)]


def _circles_hull(pts: List[Tuple[Point, float]]) -> List[Point]:
    """Two circles and their common tangents = convex hull of the circles."""
    ring = []
    for (cx, cy), r in pts:
        ring += [(cx + r * math.sin(math.radians(a)), cy + r * math.cos(math.radians(a))) for a in range(0, 360, 10)]
    ring = sorted(set(ring))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, hi = [], []
    for p in ring:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(ring):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


@lru_cache(maxsize=None)
def _geo_airspace(name: str, control: Tuple[str, ...]) -> Airspace:
    d = _load_json(name)
    pr = d["projection"]
    proj = LambertConformal(pr["lat0"], pr["lon0"], pr["lat1"], pr["lat2"])
    xy = lambda s: proj.forward(*parse_dms(s))

    points: Dict[str, Point] = {k: xy(v["coord"]) for k, v in d["points"].items()}
    ext = (d.get("external_points") or {}).get("points", {})
    cons: Dict[str, Point] = {}
    # boundary ring with inserted named vertices
    ring_raw: List = []
    prev_ll = None
    for item in d["boundary"]:
        if isinstance(item, str):
            ll = parse_dms(item)
            if prev_ll is not None and abs(ll[0] - prev_ll[0]) < 1e-9:
                # edges along a parallel (e.g. 35 58N) are arcs on a conformal conic chart: follow the parallel
                n = max(1, int(abs(ll[1] - prev_ll[1]) / 0.25))
                for k in range(1, n):
                    ring_raw.append(("v", proj.forward(ll[0], prev_ll[1] + (ll[1] - prev_ll[1]) * k / n)))
            ring_raw.append(("v", proj.forward(*ll)))
            prev_ll = ll
        elif "insert" in item:
            ring_raw.append(("i", item["insert"]))
        elif "arc" in item:
            a = item["arc"]
            last = [v for kind, v in ring_raw if kind == "v"][-1]
            for p in _arc_points(proj, parse_dms(a["center"]), a["radius_nm"], proj.inverse(*last),
                                 parse_dms(a["to"]), a.get("direction", "ccw")):
                ring_raw.append(("v", p))
            prev_ll = None
    # construction points (junctions, intercepts of sector lines with the boundary, points kept on a line)
    for k, c in d.get("construction_points", {}).items():
        if "coord" in c:
            cons[k] = xy(c["coord"])
    allp = {**points, **{k: xy(v["coord"]) for k, v in ext.items()}, **cons}
    ref = lambda n: allp[n] if n in allp else xy(n)
    for k, c in d.get("construction_points", {}).items():
        if "on_edge" in c:
            cons[k] = _line_intersection(ref(c["line"][0]), ref(c["line"][1]), xy(c["on_edge"][0]), xy(c["on_edge"][1]))
            allp[k] = cons[k]
    for k, c in d.get("construction_points", {}).items():
        if "snap" in c:                        # a point kept on a straight boundary segment (e.g. DOMOG)
            p = _project_on_segment(ref(c["snap"]), ref(c["onto"][0]), ref(c["onto"][1]))
            if k in points:
                points[k] = p
            else:
                cons[k] = p
            allp[k] = p
    # snap inserted named points onto their boundary edge
    # (the vertex list is complete first - parallels are densified - then each named point goes on its nearest edge)
    ring: List[Tuple[Point, Optional[str]]] = [(v, None) for kind, v in ring_raw if kind == "v"]
    for kind, v in ring_raw:
        if kind != "i":
            continue
        n = len(ring)
        e = min(range(n), key=lambda i: _seg_dist(allp[v], ring[i][0], ring[(i + 1) % n][0]))
        p = _project_on_segment(allp[v], ring[e][0], ring[(e + 1) % n][0])
        if dist(p, allp[v]) > 3.0:
            raise ValueError(f"boundary point {v} is {dist(p, allp[v]):.1f} NM from the boundary")
        if v in points:
            points[v] = p
        else:
            cons[v] = p
        allp[v] = p
        ring.insert(e + 1, (p, v))
    fir_outline = [p for p, _ in ring]
    idx = {name: i for i, (_, name) in enumerate(ring) if name}

    sectors, cta_of = [], {}
    for s in d["sectors"]:
        a, b = idx[s["ring"][0]], idx[s["ring"][1]]
        path = []
        i = a
        while True:
            path.append(ring[i][0])
            if i == b:
                break
            i = (i + 1) % len(ring)
        line, prev = [], s["line"][0]
        for item in s["line"][1:]:
            if isinstance(item, dict):                 # an arc of a circle from the previous point to "to"
                arc = item["arc"]
                p0, p1 = proj.inverse(*allp[prev]), proj.inverse(*allp[arc["to"]])
                if prev < arc["to"]:
                    line += _arc_points(proj, parse_dms(arc["center"]), arc["radius_nm"], p0, p1, arc["direction"])
                else:                                  # same vertices whichever sector draws it
                    line += _arc_points(proj, parse_dms(arc["center"]), arc["radius_nm"], p1, p0,
                                        "cw" if arc["direction"] == "ccw" else "ccw")[::-1]
                line.append(allp[arc["to"]])
                prev = arc["to"]
                continue
            line.append(allp[item])
            prev = item
        poly = [q for k, q in enumerate(path + line[:-1]) if k == 0 or q != (path + line[:-1])[k - 1]]
        if s.get("start") in allp and allp[s["start"]] in poly:     # first vertex (as published / as before)
            k = poly.index(allp[s["start"]])
            poly = poly[k:] + poly[:k]
        sectors.append(Sector(s["id"], s["name"], poly, s.get("freq"), s.get("backup_freq")))
        cta_of[s["id"]] = s.get("cta", "")

    ids = [s.id for s in sectors]
    ctl = list(control) if control else list(d.get("default_control") or ids)
    for c in ctl:
        if c not in ids:
            raise ValueError(f"unknown sector {c} in airspace {name}; sectors: {ids}")
    own = cta_of[ctl[0]]
    if any(cta_of[c] != own for c in ctl):
        raise ValueError(f"sectors {ctl} belong to different control areas: control sectors of one of them")
    mine = [s for s in sectors if cta_of[s.id] == own]          # sectors of our unit
    others = [s for s in sectors if cta_of[s.id] != own]        # the rest of the FIR, drawn, not ours
    one_cta = not others
    outline = fir_outline if one_cta else _union_ring([s.polygon for s in mine])
    polys = [s.polygon for s in mine if s.id in ctl]
    polygon = outline if len(ctl) == len(mine) else _union_ring(polys)
    if _signed_area(polygon) > 0:            # clockwise like the CTA table, purely cosmetic
        polygon = polygon[::-1]
    if _signed_area(outline) > 0:
        outline = outline[::-1]
    ctas = {k: v for k, v in (d.get("ctas") or {}).items() if not k.startswith("_")}
    cta = ctas.get(own, {})

    airways = {k: {"points": v["points"], "lower": v.get("lower"), "one_way": bool(v.get("one_way")),
                   **({"note": v["note"]} if v.get("note") else {})} for k, v in d["airways"].items()}
    routes = []
    for v in d["airways"].values():
        routes.append(list(v["points"]))
        if not v.get("one_way"):
            routes.append(list(v["points"])[::-1])
    std = [list(r) for r in d.get("standard_routes", {}).get("routes", [])]
    routes += std
    rad = [list(r) for r in d.get("rad_routes", {}).get("routes", [])]
    routes += rad
    rad2 = [list(r) for r in d.get("rad_routes", {}).get("two_way", [])]
    routes += [r[::-1] for r in rad2]          # RAD routings also flown in the other direction
    # points of the neighbouring sectors / units: route points before the entry / after the exit, valid
    # direct-to targets. Points outside the FIR always; points of the FIR outside our control area too.
    pinfo = dict(d["points"])
    for k, v in ext.items():
        points[k] = xy(v["coord"])
        pinfo[k] = {**v, "role": f"external ({v.get('region', '')})", "external": True}
    ctl_poly = outline                         # our control area (its other sectors are ours: not external)
    near_ctl = lambda p: min(_seg_dist(p, ctl_poly[i], ctl_poly[(i + 1) % len(ctl_poly)]) for i in range(len(ctl_poly)))
    fir_ext = []
    if not one_cta:
        for k, p in points.items():
            if k in ext and not g.point_in_polygon(p, fir_outline):
                continue
            fra = str(pinfo.get(k, {}).get("fra", ""))
            dd = near_ctl(p)
            inside = g.point_in_polygon(p, ctl_poly)
            if inside or dd <= ON_BOUNDARY_NM or (dd <= FIR_POINT_TOLERANCE_NM and ("E" in fra or "X" in fra)):
                if pinfo.get(k, {}).get("external"):
                    pinfo[k] = {kk: vv for kk, vv in pinfo[k].items() if kk != "external"}
                    pinfo[k]["role"] = pinfo[k].get("region", "")
                continue
            if pinfo.get(k, {}).get("external"):
                continue
            sec = next((s for s in sectors if g.point_in_polygon(p, s.polygon)), None)
            region = (ctas.get(cta_of[sec.id], {}).get("short") or cta_of[sec.id].title()) if sec else "FIR"
            pinfo[k] = {**pinfo.get(k, {}), "region": f"{region} ({sec.name})" if sec else region,
                        "external": True, "fir": True}
            fir_ext.append(k)
    ads = []
    for a in d.get("aerodromes", []):
        x, y = xy(a["coord"])
        ads.append({**{k: v for k, v in a.items() if k != "coord"}, "x": round(x, 3), "y": round(y, 3)})
    # published prohibited / restricted / danger areas (AIP ENR 5.1)
    areas = []
    for a in (d.get("published_areas") or {}).get("areas", []):
        ar = {k: v for k, v in a.items() if k not in ("circle", "polygon", "circles")}
        if "circle" in a:
            c = xy(a["circle"]["center"])
            ar["circle"] = {"center": [c[0], c[1]], "radius_nm": float(a["circle"]["radius_nm"])}
        elif "circles" in a:
            ar["polygon"] = [list(p) for p in _circles_hull([(xy(c["center"]), float(c["radius_nm"]))
                                                              for c in a["circles"]])]
        else:
            ar["polygon"] = [list(xy(p)) for p in a["polygon"]]
        ar["active"] = bool(a.get("active", a["kind"] == "prohibited"))
        areas.append(ar)
    v = d["vertical"]
    base = cta.get("name") or d["name"]
    sp = Airspace(id=d["id"], name=base + ("" if len(ctl) == len(mine) else f" - sector(s) {'+'.join(ctl)}"),
                  polygon=polygon, outline=outline, waypoints=points, lower_fl=int(v["lower_fl"]),
                  upper_fl=int(v["upper_fl"]), routes=routes, airways=airways, sectors=mine, controlled=ctl,
                  projection=proj, point_info=pinfo, default_aerodromes=ads,
                  default_sequencing=(cta["sequencing"] if "sequencing" in cta else
                                      d.get("sequencing", []) if own == cta_of[ids[0]] else []),
                  unit=cta.get("unit") or d.get("unit", ""),
                  notes={"airspace_classes": v.get("classes"), "free_route": d.get("free_route"),
                         "rad_routes": rad, "rad_note": d.get("rad_routes", {}).get("_note"),
                         "standard_routes": std, "entry_exit": d.get("_entry_exit_note"),
                         "source": d.get("_source"),
                         "sequencing_note": cta.get("sequencing_note") or d.get("_sequencing_note"),
                         "external_points": sorted(set(ext) | set(fir_ext)),
                         "external_note": (d.get("external_points") or {}).get("_note"),
                         "provisional_points": sorted(k for k, v in ext.items() if v.get("provisional")),
                         "onward_routes": d.get("onward_routes"),
                         "separation_horizontal_nm": (d.get("separation_minima") or {}).get("horizontal_nm"),
                         "descent_destinations": cta.get("descent_destinations") or
                                                 (d.get("descent_destinations") or {}).get("aerodromes", []),
                         "departures_request_rfl": cta.get("departures_request_rfl"),
                         "cpdlc": _cpdlc_for(d, own, ctas, others),
                         "coastline": bool(d.get("coastline")),
                         "capacity_per_hour": {k: v for k, v in (d.get("capacity_per_hour") or {}).items()
                                               if not k.startswith("_")} if own == cta_of[ids[0]] else {}})
    sp.construction_points = cons
    sp.cta = own
    sp.fir_outline = fir_outline if not one_cta else []
    sp.fir_sectors = [(s, cta_of[s.id]) for s in others]
    sp.fir_external = fir_ext
    sp.fir_cta_names = {k: v.get("short") or k.title() for k, v in ctas.items()}
    sp.published_areas = areas
    sp.areas_note = (d.get("published_areas") or {}).get("_note")
    return sp


def _cpdlc_for(d: dict, own: str, ctas: dict, others) -> dict:
    """Data link identity of our unit; the other control area of the FIR is an adjacent unit."""
    cfg = {k: v for k, v in (d.get("cpdlc") or {}).items() if not k.startswith("_")}
    if not ctas or own == next(iter(ctas)):
        return cfg
    me = ctas[own]
    cfg = {**cfg, "address": me.get("address") or cfg.get("address"), "name": me.get("callsign") or own}
    units = {k: v for k, v in (cfg.get("units") or {}).items() if v.get("address") != me.get("address")}
    for k, c in ctas.items():
        if k != own:
            units[c.get("address") or k] = {"name": f"{c.get('callsign') or k} CONTROL",
                                            "freq": next((s.freq for s in others if s.freq), "") or "",
                                            "address": c.get("address") or k}
    cfg["units"] = units
    return cfg


def get(name: Optional[str] = None, control: Optional[Sequence[str]] = None) -> Airspace:
    name = (name or "training").lower()
    if name in ("training", "square", "default"):
        return training()
    return _geo_airspace(name, tuple(c.upper() for c in control) if control else tuple())
