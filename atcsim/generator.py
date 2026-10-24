"""Scenario generator: a random but controlled exercise for the sectors chosen.

Parameters (all optional, see DEFAULTS):
  control                  sectors controlled (e.g. ["WN", "WS"], ["N"], ["ON", "OS"])
  duration_min, seed, start_utc
  aircraft_per_hour        background traffic through the controlled sectors
  density                  1-5: 1 = spread evenly over the hour, 5 = in tight waves
  sequences_per_hour       groups of arrivals reaching a sequencing fix too close together (sectors with sequencing
  seq_size                 fixes only), from different routes, speeds mixed: [min, max] aircraft per group
  overtakes_per_hour       same route, same level, the faster one behind
  crossings_per_hour       two routes crossing inside the sectors, same level, the same minute
  level_crossings_per_hour a pilot asking for a level through the level of crossing traffic
  slow_share               share of slow traffic (C550, C130, A400) at FL170-250, on the arrival routes when there are
                           some (in the way of the descending sequences)
  heavy_share              share of long-haul types
  transatlantic_per_hour    flights to / from North America by IBALU and SUBAL / BEXAL (westbound: mostly Moroccan
                           departures still climbing to their requested level; eastbound: at cruise). Crossing the
                           West / Oceanic boundary, they are coordinated by OLDI like any flight whose route touches both
                           sides (IBALU - SAMAR included: that one is ordinary traffic, in the random flow and the
                           crossings)
  levels                   [lowest, highest] cruise level of the background traffic
  separation_nm            horizontal minimum of the exercise (None: the airspace's); flights enter the sectors spaced
                           at least this much from the traffic at their level
  airports                 {"GMMN": {"dep": 6, "arr": 8}, ...}: departures and arrivals per hour of the aerodromes of the
                           sectors (airports_for(control) lists them); departures take off on a SID and climb into the
                           sectors, arrivals are filed to the aerodrome (by its sequencing fix when it has one)

The planted situations are built geometrically (an aircraft at a point at a time: "reach"), the background traffic is
moved off the levels where it would conflict unplanned (planned-trajectory check), and the scenario is checked by
building it. The result is a normal scenario (open it in the designer, edit, save).
"""
from __future__ import annotations

import copy
import math
import random
from typing import Dict, List, Optional, Tuple

from . import airspace as asp_mod
from . import performance
from .aircraft import level_parity_ok
from .geometry import bearing, dist, point_in_polygon, segment_intersection

DEFAULTS = dict(control=["WS"], duration_min=60, seed=None, start_utc="10:00", aircraft_per_hour=30, density=2,
                sequences_per_hour=2, seq_size=[3, 4], overtakes_per_hour=1, crossings_per_hour=2,
                level_crossings_per_hour=2, slow_share=0.12, heavy_share=0.3, transatlantic_per_hour=2,
                levels=[290, 400], weather="calm", airports=None, separation_nm=None)

SHORT = ["B738", "A320", "A321", "B38M", "A20N", "A21N", "A319", "B739", "E190", "E195", "CRJ9", "B752"]
LONG = ["A332", "A333", "B77W", "B789", "B788", "A359", "B772", "A343", "B744", "A388", "B763"]
SLOW = [("C550", False), ("C550", False), ("C130", True), ("A400", True)]
OPS_SHORT = ["RAM", "RYR", "EZY", "EXS", "TOM", "IBE", "VLG", "TVF", "EWG", "CFG", "BAW", "TAP", "KLM", "DLH", "AFR",
             "SWR", "BEL", "NAX", "AEA"]
OPS_LONG = ["RAM", "AZU", "TAM", "GLO", "AVA", "AEA", "IBE", "TAP", "KLM", "AFR", "BAW", "DLH", "LAN", "ARG", "DAL",
            "UAE", "QTR"]
OPS_SLOW = ["CNA", "RMAF", "FAF", "EXE", "NJE"]
REGIONS = {  # neighbouring unit -> airports beyond it
    "LPPC": ["LPPT", "LPPR", "LFPG", "EGLL", "EHAM", "EDDF", "EGKK", "EIDW", "EBBR", "LSZH"],
    "LECM": ["LEMD", "LEBL", "LEMG", "LEZL", "LFPO", "LIRF", "LIMC", "EDDM", "LOWW"],
    "LECS": ["LEMG", "LEZL", "LEAL", "LEMD", "LEVC"],
    "GCCC": ["GCLP", "GCTS", "GCXO", "GCFV", "GCRR", "GCLA"],
    "GOOO": ["GOBD", "GVAC", "DIAP", "DNMM", "SBGR", "SBGL", "SBRF", "SAEZ"],
    "GVSC": ["GVAC", "SBGR", "SBGL", "SBRF", "SBFZ", "SAEZ", "SCEL"],
    "LPPO": ["KJFK", "KMIA", "KBOS", "CYUL", "CYYZ", "KIAD"],
    "GMMM": ["GMMN", "GMME", "GMMX", "GMAD", "GMFF", "GMTT"],
    "DAAA": ["DAAG", "DAOO", "DTTA"],
}
# rough direction of each neighbouring unit's airports, seen from the Agadir / Casablanca FIR (degrees true)
REGION_BRG = {"LPPC": 355, "LECM": 25, "LECS": 40, "GCCC": 235, "GOOO": 190, "GVSC": 215, "LPPO": 295, "DAAA": 80}
NAT = ["KJFK", "KMIA", "KIAD", "KBOS", "KORD", "CYUL", "CYYZ"]


# ------------------------------------------------------------------------------------------- helpers
def _mach_tas(m: float, fl: float) -> float:
    """TAS (kt) for a Mach at a level (ISA, troposphere / isothermal above FL361)."""
    h = fl * 100 * 0.3048
    T = 288.15 - 0.0065 * min(h, 11000.0)
    return m * 661.47 * math.sqrt(T / 288.15)


def _max_fl(typ, load=0.5):
    p = performance.get(typ)
    return p.max_fl("mach", p.cruise_mach, p.oew + load * (p.mtow - p.oew))


def _poly_len_inside(pts, polys) -> float:
    L = 0.0
    for p, q in zip(pts, pts[1:]):
        n = max(1, int(dist(p, q) / 5))
        for k in range(n):
            m = (p[0] + (q[0] - p[0]) * (k + 0.5) / n, p[1] + (q[1] - p[1]) * (k + 0.5) / n)
            if any(point_in_polygon(m, poly) for poly in polys):
                L += dist(p, q) / n
    return L


def _along(pts, s) -> Tuple[float, float]:
    for p, q in zip(pts, pts[1:]):
        L = dist(p, q)
        if s <= L:
            return (p[0] + (q[0] - p[0]) * s / max(L, 1e-9), p[1] + (q[1] - p[1]) * s / max(L, 1e-9))
        s -= L
    return pts[-1]


def _first_inside(pts, polys) -> Optional[float]:
    """Distance along the polyline where it first enters the controlled area."""
    s0 = 0.0
    for p, q in zip(pts, pts[1:]):
        L = dist(p, q)
        n = max(1, int(L / 2))
        for k in range(n + 1):
            m = (p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n)
            if any(point_in_polygon(m, poly) for poly in polys):
                return s0 + L * k / n
        s0 += L
    return None


def _last_inside(pts, polys) -> Optional[float]:
    """Distance along the polyline where it last leaves the controlled area."""
    rev = list(reversed(pts))
    s = _first_inside(rev, polys)
    return None if s is None else _poly_len(pts) - s


def _poly_len(pts) -> float:
    return sum(dist(p, q) for p, q in zip(pts, pts[1:]))


def _seg_dist(p, a, b) -> float:
    L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1e-9
    t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
    return dist(p, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))


def _cut(pts, s0, s1):
    """The part of the polyline between s0 and s1 NM along it."""
    res, a = [_along(pts, s0)], 0.0
    for i in range(1, len(pts)):
        a += dist(pts[i - 1], pts[i])
        if s0 < a < s1:
            res.append(pts[i])
    res.append(_along(pts, s1))
    return res


class _Ctx:
    def __init__(self, P):
        self.P = P
        self.rng = random.Random(P["seed"])
        self.asp = asp_mod.get("agadir", P["control"])
        self.wp = self.asp.waypoints
        self.polys = [s.polygon for s in self.asp.sectors if s.id in P["control"]]
        self.used = set()
        self.aircraft: List[dict] = []
        self.requests: List[dict] = []
        self.notes: List[str] = []
        self.keep: set = set()
        self.forced_dep: set = set()
        self.flow_cs: set = set()
        self.level_cap: Dict[str, int] = {}     # departures: the highest level they reach level before our boundary             # flights of a planned flow: they keep their origin          # departures asked per aerodrome: re-timed, never refiled
        self.T = P["duration_min"] * 60.0
        self._routes()

    # ---- the routes through the controlled sectors: the planned traffic flows when the airspace has them
    def _flows(self):
        import json as _json
        import os as _os
        path = _os.path.join(_os.path.dirname(__file__), "data", "flows", "agadir.json")
        if not _os.path.exists(path):
            return []
        d = _json.loads(open(path, encoding="utf-8").read())
        groups = d.get("groups", {})
        aw = {k: v["points"] for k, v in (self.asp.airways or {}).items()}
        raw = _json.loads(open(_os.path.join(_os.path.dirname(__file__), "data", "airspaces", "agadir_cta.json"),
                               encoding="utf-8").read())
        for k, v in (raw.get("external_airways") or {}).items():       # beyond the FIR: only where ours stop
            if not k.startswith("_") and k not in aw:
                aw[k] = v["points"]
        expand = lambda lst: [a for g in lst for a in (groups.get(g) or [g])]
        out = []
        for f in d.get("flows", []):
            toks = f["route"].split()
            names, ok = [toks[0]], True
            i = 1
            while i < len(toks):
                t = toks[i]
                if t in aw and i + 1 < len(toks):
                    pts, a, b = aw[t], names[-1], toks[i + 1]
                    if a not in pts or b not in pts:
                        ok = False
                        break
                    ia, ib = pts.index(a), pts.index(b)
                    seg = pts[ia:ib + 1] if ia <= ib else pts[ib:ia + 1][::-1]
                    names += seg[1:]
                    i += 2
                elif t == "DCT":
                    i += 1
                else:
                    names.append(t)
                    i += 1
            if not ok or any(n not in self.wp for n in names):
                self.flow_errors.append(f["id"])
                continue
            out.append((names, f["id"], expand(f["from"]), expand(f["to"])))
        return out

    def _routes(self):
        self.flow_errors = []
        flows = self._flows()
        out, seen = [], set()
        src = [(n, fid, fr, to) for n, fid, fr, to in flows] if flows else []
        for names, fid, fr, to in src:
            pts = [self.wp[n] for n in names]
            Lin = _poly_len_inside(pts, self.polys)
            if Lin < 15.0:
                continue
            s_in = _first_inside(pts, self.polys) or 0.0
            s_out = _last_inside(pts, self.polys) or _poly_len(pts)
            south = 90.0 <= bearing(_along(pts, s_in), _along(pts, s_out)) < 270.0
            out.append({"route": names, "pts": pts, "inside_nm": Lin, "south": south, "s_in": s_in, "s_out": s_out,
                        "flow": fid, "deps": fr, "dests": to})
        if len(out) >= 3:
            self.routes = out
            self._aerodromes()
            return
        out = []                                       # no flows here: the published routes
        for r in self.asp.routes:
            r = [n for n in r if n in self.wp]
            if len(r) < 2 or tuple(r) in seen:
                continue
            seen.add(tuple(r))
            pts = [self.wp[n] for n in r]
            Lin = _poly_len_inside(pts, self.polys)
            if Lin < 25.0:
                continue
            s_in = _first_inside(pts, self.polys) or 0.0
            s_out = _last_inside(pts, self.polys) or _poly_len(pts)
            # the direction (semicircular rule) of the part we control, as the scenario reckons it
            south = 90.0 <= bearing(_along(pts, s_in), _along(pts, s_out)) < 270.0
            out.append({"route": r, "pts": pts, "inside_nm": Lin, "south": south, "s_in": s_in, "s_out": s_out})
        self.routes = out
        self._aerodromes()

    def _aerodromes(self):
        # aerodromes: the ones with SIDs (their departures are shown from take-off), every one for destinations
        # every aerodrome of the simulation can take off: on a SID when it has them, else omnidirectionally
        self.sid_ads = {d["id"]: (d["x"], d["y"]) for d in self.asp.default_aerodromes if d.get("runways") and "y" in d}
        self.sid_ends = {d["id"]: {sd.get("end") or (sd["via"][-1] if sd.get("via") else None) for sd in d["sids"]}
                         for d in self.asp.default_aerodromes if d.get("sids")}
        self.ap_xy = {a["id"]: (a["x"], a["y"]) for a in (getattr(self.asp, "airports", None) or [])
                      if a.get("type") in ("large", "medium") and a.get("x") is not None
                      and len(a["id"]) == 4 and a["id"].isalpha()}
        self.ap_xy.update(self.sid_ads)

    def _near(self, cands: Dict[str, tuple], part, ref, max_off=30.0, max_ref=150.0) -> List[str]:
        """Aerodromes within max_off NM of the polyline part and max_ref NM of ref."""
        out = []
        for k, xy in cands.items():
            if dist(xy, ref) > max_ref:
                continue
            if min((_seg_dist(xy, a, b) for a, b in zip(part, part[1:])), default=dist(xy, part[0])) <= max_off:
                out.append(k)
        return out

    def dep_for(self, r, domestic=0.6) -> str:
        """A departure aerodrome consistent with the route: one with SIDs near the route before it enters (it is then
        shown from take-off), else a distant one beyond the unit the route comes from (none close to the entry)."""
        if r.get("deps"):                      # a planned flow: its own origins
            return self.rng.choice(r["deps"])
        if r["route"][0] in ("SUBAL", "BEXAL") and "IBALU" in r["route"] and self.rng.random() < 0.85:
            return self.rng.choice(NAT)        # the North Atlantic flow
        entry = _along(r["pts"], r["s_in"])
        near = self._near(self.sid_ads, _cut(r["pts"], 0.0, r["s_in"]) if r["s_in"] > 1 else [r["pts"][0], entry], entry)
        if near and self.rng.random() < domestic:
            return self.rng.choice(near)
        return self._far(r, "dep")

    def join_route(self, r, dep) -> List[str]:
        """The route from where a departure from a nearby aerodrome joins it (the route point before the entry
        closest to the aerodrome): its SID then leads there and it is shown from take-off."""
        names = list(r["route"])
        if dep not in self.sid_ads:
            return names
        # where one of its SIDs ends on the route: the departure joins there, in its direction of travel (a SID that
        # ends at NIKZO for the south, LACAJ for the east...), never back along the route; the furthest such point
        # within the first part of the route (before the exit)
        ends = self.sid_ends.get(dep, set())
        ad = self.sid_ads[dep]
        turn = lambda b1, b2: abs((b2 - b1 + 540.0) % 360.0 - 180.0)
        acc, sid_on, ahead = 0.0, [], []
        for i in range(len(names) - 1):
            if i:
                acc += dist(r["pts"][i - 1], r["pts"][i])
            if acc >= r["s_out"] - 10.0:
                break
            p, q = r["pts"][i], r["pts"][i + 1]
            d = dist(ad, p)
            if d > (90.0 if ends else 250.0):          # (no SIDs: a direct of any length to its airway)
                continue
            # from here the route leads away from the aerodrome: a departure can join here without turning back
            away = d < 3.0 or turn(bearing(ad, p), bearing(p, q)) <= 90.0
            if not away:
                continue
            if names[i] in ends:
                sid_on.append(i)
            elif d >= 12.0 or (d < 3.0 and not ends):   # (its own VOR, for an aerodrome without SIDs)
                ahead.append(i)
        # an aerodrome under or beside our sectors: approach keeps the departure until a point some way out, so that
        # it is handed to us level - not at its own VOR or a SID end next to the field (when such a point exists)
        keep_out = False
        if self.on_sectors(ad, tol=15.0):
            far = lambda lst: [i for i in lst if dist(ad, r["pts"][i]) >= 22.0]
            if far(sid_on) or far(ahead):
                sid_on, ahead, keep_out = far(sid_on), far(ahead), True
        if sid_on:                                   # a SID ends on the route: the furthest such point
            return names[sid_on[-1]:]
        if not ends and ahead:
            # no SIDs: out by its own VOR when the route starts there (LAY, DKH, TNA), else the first point heading
            # away - or a longer direct further on when it saves more than 30 NM (Smara direct ADM, not via SOLNA)
            along = [0.0]
            for k in range(1, len(names)):
                along.append(along[-1] + dist(r["pts"][k - 1], r["pts"][k]))
            first = ahead[0]
            if dist(ad, r["pts"][first]) < 3.0 and not keep_out:
                return names[first:]
            via = lambda k: dist(ad, r["pts"][first]) + along[k] - along[first]
            best = max(ahead, key=lambda k: via(k) - dist(ad, r["pts"][k]))
            return names[best:] if via(best) - dist(ad, r["pts"][best]) > 30.0 else names[first:]
        # otherwise the first point heading away that a SID reaches in line: from the SID's end to the point and on
        # along the route, neither leg turning back (the SID end lies on the way, not beyond the point)
        sid_xy = [self.wp[e] for e in ends if e in self.wp]
        for i in ahead:
            p, q = r["pts"][i], r["pts"][i + 1]
            for e in sid_xy:
                de = dist(e, p)
                if de < 0.5 or (de < 45.0 and turn(bearing(ad, e), bearing(e, p)) <= 90.0
                                and turn(bearing(e, p), bearing(p, q)) <= 90.0):
                    return names[i:]
        return None                                  # every way onto this route turns back: not a departure of it

    def dest_for(self, r) -> str:
        if r.get("dests"):
            return self.rng.choice(r["dests"])
        if r["route"][-1] in ("SUBAL", "BEXAL") and "IBALU" in r["route"] and self.rng.random() < 0.85:
            return self.rng.choice(NAT)
        exitp = _along(r["pts"], r["s_out"])
        L = _poly_len(r["pts"])
        near = self._near(self.ap_xy, _cut(r["pts"], r["s_out"], L) if L - r["s_out"] > 1 else [exitp, r["pts"][-1]], exitp)
        if near and self.rng.random() < 0.4:
            return self.rng.choice(near)
        return self._far(r, "dest")

    def _far(self, r, end) -> str:
        """A distant aerodrome in the direction the flight comes from (dep) or goes to (dest): beyond the unit the
        route's end point belongs to when known, else the neighbours lying that way; never an aerodrome with SIDs
        (those are close enough to be shown from take-off) nor one close to where it crosses our boundary."""
        pts, L = r["pts"], _poly_len(r["pts"])
        if end == "dep":
            ref = _along(pts, r["s_in"])
            far_pt = pts[0] if r["s_in"] > 20 else _along(pts, max(0.0, r["s_in"] - 1))
            toward = bearing(ref, far_pt) if r["s_in"] > 20 else (bearing(_along(pts, r["s_in"] + 20), ref))
            name = r["route"][0]
        else:
            ref = _along(pts, r["s_out"])
            toward = bearing(ref, pts[-1]) if L - r["s_out"] > 20 else bearing(_along(pts, max(0.0, r["s_out"] - 20)), ref)
            name = r["route"][-1]
        ok = lambda k: k not in self.sid_ads and (k not in self.ap_xy or (
            dist(self.ap_xy[k], ref) > 200.0 and abs((bearing(ref, self.ap_xy[k]) - toward + 540) % 360 - 180) < 70))
        lst = [k for k in self.region_of(name, end) if ok(k)]
        if not lst:
            near_dir = [fir for fir, b in REGION_BRG.items() if abs((b - toward + 540) % 360 - 180) < 60]
            lst = [k for fir in near_dir for k in REGIONS[fir] if ok(k)]
            lst += [k for k in self.ap_xy if ok(k) and k not in lst]
        if not lst:
            fir = min(REGION_BRG, key=lambda f: abs((REGION_BRG[f] - toward + 540) % 360 - 180))
            lst = REGIONS[fir]
        return self.rng.choice(lst)

    def on_sectors(self, p, tol=4.0) -> bool:
        """In or on the edge of the controlled sectors."""
        if any(point_in_polygon(p, poly) for poly in self.polys):
            return True
        for poly in self.polys:
            for a, b in zip(poly, poly[1:] + poly[:1]):
                L2 = (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2 or 1e-9
                t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / L2))
                if dist(p, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))) <= tol:
                    return True
        return False

    def region_of(self, name, end) -> List[str]:
        info = self.asp.point_info.get(name, {})
        role = str(info.get("role", "")) + " " + str(info.get("region", ""))
        for fir, airports in REGIONS.items():
            if fir in role and fir != "GMMM":
                return airports
        if end in ("dep", "dest"):
            return []                          # by direction (see _far)
        lat = self.asp.projection.inverse(*self.wp[name])[0] if self.asp.projection else 33
        return REGIONS["LPPC"] if lat > 35 else REGIONS["GCCC"] if lat < 29.5 else REGIONS["GMMM"]

    def callsign(self, ops) -> str:
        for _ in range(200):
            cs = self.rng.choice(ops) + str(self.rng.randint(10, 9899)) + self.rng.choice(["", "", "", "A", "K", "X", "P"])
            if cs not in self.used:
                self.used.add(cs)
                return cs
        raise RuntimeError("no callsign")

    def level(self, south, lo, hi, typ, avoid=()):
        mx = min(hi, _max_fl(typ))
        lv = [l for l in range(lo, mx + 1, 10) if level_parity_ok(l, south)]
        lv = [l for l in lv if l not in avoid] or lv
        return self.rng.choice(lv) if lv else None

    def add(self, r, typ, fl, t, at_s=None, ops=None, dest=None, dep=None, mach=None, keep=False, **kw) -> dict:
        """An aircraft on route r at level fl, at the point at_s NM along the route (default: where it enters the
        controlled area, plus a little) at time t."""
        s = r["s_in"] + 10.0 if at_s is None else at_s
        p = None
        dep = dep or self.dep_for(r)
        names = self.join_route(r, dep)
        tries = 0
        while names is None and tries < 12:              # its SIDs all lead the other way: another origin
            tries += 1
            dep = self.rng.choice([d for d in (r.get("deps") or []) if d != dep] or [self._far(r, "dep")])
            names = self.join_route(r, dep)
        if names is None:
            dep, names = self._far(r, "dep"), list(r["route"])
        if names and names[0] != r["route"][0]:            # joined further on: the timing point after the join
            k = r["route"].index(names[0])
            s0 = sum(dist(a_, b_) for a_, b_ in zip(r["pts"][:k], r["pts"][1:k + 1]))
            s = max(s, s0 + 5.0)
        p = _along(r["pts"], s)
        if dest and dest in self.ap_xy and not kw.get("arrival"):
            # to an aerodrome without a sequencing fix: the route ends where it passes closest to it, beyond the exit
            pts = [self.wp[n] for n in names]
            acc, best = 0.0, None
            for i in range(1, len(names)):
                acc += dist(pts[i - 1], pts[i])
                d_ = dist(pts[i], self.ap_xy[dest])
                if acc > r["s_out"] - (r["s_in"] if names[0] == r["route"][0] else 0) + 1.0 and d_ < 45.0 \
                        and (best is None or d_ < best[0]):
                    best = (d_, i)
            if best is not None and best[1] < len(names) - 1:
                names = names[:best[1] + 1]
        if kw.get("arrival") and dest:                 # an arrival ends at its aerodrome's sequencing fix
            fx = next((q["fix"] for q in self.asp.default_sequencing if q["aerodrome"] == dest and q["fix"] in names[1:]), None)
            if fx:
                names = names[:names.index(fx) + 1]
        a = {"callsign": self.callsign(ops or (OPS_LONG if typ in LONG else OPS_SHORT)), "type": typ,
             "route": names, "fl": int(fl),
             "dep": dep,
             "dest": dest or self.dest_for(r),
             "reach": {"point": [round(p[0], 3), round(p[1], 3)], "time": int(round(t))}}
        if r.get("flow"):
            self.flow_cs.add(a["callsign"])
        if mach:
            a["mach"] = round(mach, 2)
        a.update(kw)
        self.aircraft.append(a)
        if keep:
            self.keep.add(a["callsign"])
        return a

    def times(self, n, lo=-600.0, hi=None) -> List[float]:
        """n times over the exercise: even (density 1) to tight waves (density 5)."""
        hi = self.T - 300.0 if hi is None else hi
        d = max(1, min(5, int(self.P["density"])))
        peaks = [lo + (hi - lo) * (k + 0.5) / max(1, d - 1 or 1) for k in range(max(1, d - 1))]
        out = []
        for _ in range(n):
            if self.rng.random() < (d - 1) / 4.0:
                c = self.rng.choice(peaks)
                out.append(min(hi, max(lo, self.rng.gauss(c, 240.0 + 120.0 * (5 - d)))))
            else:
                out.append(self.rng.uniform(lo, hi))
        return sorted(out)

    def pick_type(self, long_share=None):
        return self.rng.choice(LONG if self.rng.random() < (self.P["heavy_share"] if long_share is None else long_share) else SHORT)


# ------------------------------------------------------------------------------------------- planted situations
def _sequences(c: _Ctx):
    fixes = [q for q in c.asp.default_sequencing if q["fix"] in c.wp and c.on_sectors(c.wp[q["fix"]])]
    fixes = [q for q in fixes if any((r["route"][-1] == q["fix"] or q["fix"] in r["route"][1:])
                                     and (not r.get("dests") or q["aerodrome"] in r["dests"]) for r in c.routes)]
    n = int(round(c.P["sequences_per_hour"] * c.T / 3600.0))
    if not fixes or n <= 0:
        return 0
    done = 0
    for ts in c.times(n, lo=420.0, hi=c.T - 300.0):
        q = c.rng.choice(fixes)
        fix = q["fix"]
        inbound = [r for r in c.routes if fix in r["route"] and r["route"].index(fix) > 0
                   and (not r.get("dests") or q["aerodrome"] in r["dests"])]
        if not inbound:
            continue
        k = c.rng.randint(*c.P["seq_size"])
        t = ts
        names = []
        for i in range(k):
            r = inbound[i % len(inbound)] if c.rng.random() < 0.7 else c.rng.choice(inbound)
            s_fix = sum(dist(p, qq) for p, qq in zip(r["pts"], r["pts"][1:r["route"].index(fix) + 1]))
            typ = c.pick_type(0.25)
            fl = c.level(r["south"], 300, 380, typ) or 330
            m = performance.get(typ).cruise_mach + c.rng.choice([-0.02, 0.0, 0.0, 0.02, 0.03])
            a = c.add(r, typ, fl, t, at_s=s_fix, dest=q["aerodrome"], mach=m, keep=True, arrival=True)
            names.append(f"{a['callsign']} ({typ}, F{fl})")
            t += c.rng.uniform(25.0, 70.0)          # closer than the spacing: they must be sequenced
        # slow traffic in the way of the descent
        if c.rng.random() < min(1.0, c.P["slow_share"] * 4):
            r = c.rng.choice(inbound)
            typ, mil = c.rng.choice(SLOW)
            s_fix = sum(dist(p, qq) for p, qq in zip(r["pts"], r["pts"][1:r["route"].index(fix) + 1]))
            fl = c.level(r["south"], 170, 250, typ) or 210
            a = c.add(r, typ, fl, ts - c.rng.uniform(60, 240), at_s=s_fix, ops=OPS_SLOW,
                      dest=q["aerodrome"], keep=True, arrival=True, **({"military": True} if mil else {}))
            names.append(f"slow {a['callsign']} ({typ}, F{fl}) just ahead")
        c.notes.append(f"Sequence at {fix} ({q['aerodrome']}) around T+{int(ts // 60)}': " + ", ".join(names) + ".")
        done += 1
    return done


def _overtakes(c: _Ctx):
    n = int(round(c.P["overtakes_per_hour"] * c.T / 3600.0))
    long_routes = [r for r in c.routes if r["inside_nm"] >= 70]
    if not long_routes or n <= 0:
        return 0
    for t in c.times(n, lo=0.0, hi=c.T - 900.0):
        r = c.rng.choice(long_routes)
        t1 = c.rng.choice(SHORT[:8])
        t2 = c.rng.choice(LONG)
        fl = c.level(r["south"], 330, 390, t1) or 350
        m1 = performance.get(t1).cruise_mach - 0.02
        m2 = max(performance.get(t2).cruise_mach, m1 + 0.06)
        v1, v2 = _mach_tas(m1, fl), _mach_tas(m2, fl)
        L = r["inside_nm"] * 0.6
        gap_s = max(30.0, L / v1 * 3600.0 - L / v2 * 3600.0 - 40.0)   # it catches up about 60 % of the way across
        a = c.add(r, t1, fl, t, mach=m1, keep=True)
        b = c.add(r, t2, fl, t + gap_s, mach=m2, keep=True)
        c.notes.append(f"Overtake on {r['route'][0]}-{r['route'][-1]} at F{fl} around T+{int(t // 60)}': "
                       f"{b['callsign']} ({t2}, M{m2:.2f}) behind {a['callsign']} ({t1}, M{m1:.2f}).")
    return n


def _crossing_pairs(c: _Ctx):
    """(route 1, route 2, crossing point, distance along 1, along 2, name) for every two routes whose legs cross inside
    the controlled sectors at 30-150 degrees."""
    pairs = []
    for i, r1 in enumerate(c.routes):
        for r2 in c.routes[i + 1:]:
            if set(r1["route"]) == set(r2["route"]):
                continue                                   # the same route the other way
            s1 = 0.0
            for a1, b1 in zip(r1["pts"], r1["pts"][1:]):
                s2 = 0.0
                for a2, b2 in zip(r2["pts"], r2["pts"][1:]):
                    x = segment_intersection(a1, b1, a2, b2)
                    if x and any(point_in_polygon(x, p) for p in c.polys):
                        ang = abs((bearing(a1, b1) - bearing(a2, b2) + 540) % 360 - 180)
                        if 30 <= ang <= 150:
                            name = next((n for n in r1["route"] if dist(c.wp[n], x) < 1.0), None)
                            pairs.append((r1, r2, x, s1 + dist(a1, x), s2 + dist(a2, x), name))
                    s2 += dist(a2, b2)
                s1 += dist(a1, b1)
            # converging on a common fix inside the sectors from different directions (a merge: IBALU, MAK, ADM...)
            for k1, n in enumerate(r1["route"][1:], 1):
                if n not in r2["route"][1:] or not any(point_in_polygon(c.wp[n], p) for p in c.polys):
                    continue
                k2 = r2["route"].index(n)
                p1, p2 = r1["route"][k1 - 1], r2["route"][k2 - 1]
                if p1 == p2:
                    continue
                ang = abs((bearing(c.wp[p1], c.wp[n]) - bearing(c.wp[p2], c.wp[n]) + 540) % 360 - 180)
                if 15 <= ang <= 150:
                    s1n = sum(dist(a, b) for a, b in zip(r1["pts"][:k1 + 1], r1["pts"][1:k1 + 1]))
                    s2n = sum(dist(a, b) for a, b in zip(r2["pts"][:k2 + 1], r2["pts"][1:k2 + 1]))
                    pairs.append((r1, r2, c.wp[n], s1n, s2n, n))
    return pairs


def _pick_pair(c: _Ctx, pairs):
    """A crossing pair, each route about as likely as any other (a route with many crossings, like the long Atlantic
    ones, would otherwise crowd out a route with one, like IBALU - SAMAR)."""
    key = lambda r: tuple(r["route"])
    n = {}
    for p in pairs:
        for r in p[:2]:
            n[key(r)] = n.get(key(r), 0) + 1
    return c.rng.choices(pairs, [1.0 / n[key(p[0])] + 1.0 / n[key(p[1])] for p in pairs])[0]


def _crossings(c: _Ctx, pairs):
    # at the same level: two flights of the same direction (semicircular rule)
    pairs = [p for p in pairs if p[0]["south"] == p[1]["south"]]
    n = int(round(c.P["crossings_per_hour"] * c.T / 3600.0))
    if not pairs or n <= 0:
        return 0
    done = 0
    for t in c.times(n, lo=300.0, hi=c.T - 300.0):
        r1, r2, xp, s1, s2, x = _pick_pair(c, pairs)
        x = x or f"{r1['route'][0]}-{r1['route'][-1]} x {r2['route'][0]}-{r2['route'][-1]}"
        t1, t2 = c.pick_type(), c.pick_type()
        both = [l for l in range(330, 400, 10) if level_parity_ok(l, r1["south"]) and level_parity_ok(l, r2["south"])
                and l <= min(_max_fl(t1), _max_fl(t2))]
        fl = c.rng.choice(both) if both else (c.level(r1["south"], 330, 390, t1) or 350)
        a = c.add(r1, t1, fl, t, at_s=s1, keep=True)
        b = c.add(r2, t2, fl, t + c.rng.uniform(-35, 35), at_s=s2, keep=True)
        c.notes.append(f"Crossing at {x} at F{fl} around T+{int(t // 60)}': {a['callsign']} and {b['callsign']}.")
        done += 1
    return done


def _level_crossings(c: _Ctx, pairs):
    n = int(round(c.P["level_crossings_per_hour"] * c.T / 3600.0))
    if not pairs or n <= 0:
        return 0
    done = 0
    for t in c.times(n, lo=600.0, hi=c.T - 300.0):
        for _try in range(12):                             # a level step with a level of the crossing traffic inside it
            r1, r2, xp, s1, s2, x = _pick_pair(c, pairs)
            t1, t2 = c.pick_type(), c.pick_type()
            mine = [l for l in range(300, min(400, _max_fl(t1)) + 1, 10) if level_parity_ok(l, r1["south"])]
            if len(mine) < 3:
                continue
            lo = c.rng.choice(mine[:-2])
            hi = c.rng.choice([l for l in mine if 20 <= l - lo <= 40])
            mid = [l for l in range(lo + 10, hi, 10) if level_parity_ok(l, r2["south"]) and l <= _max_fl(t2)]
            if mid:
                break
        else:
            continue
        x = x or f"{r1['route'][0]}-{r1['route'][-1]} x {r2['route'][0]}-{r2['route'][-1]}"
        up = c.rng.random() < 0.5
        a = c.add(r1, t1, lo if up else hi, t, at_s=s1, keep=True)
        b = c.add(r2, t2, c.rng.choice(mid), t + c.rng.uniform(-40, 40), at_s=s2, keep=True)
        ask = hi if up else lo
        c.requests.append({"time": int(max(30, t - c.rng.uniform(150, 330))), "callsign": a["callsign"],
                           "type": "altitude", "value": ask,
                           "reason": c.rng.choice(["requesting higher, better ride", "for performance", "fuel",
                                                   "turbulence reported"]) if up else "requesting lower, turbulence"})
        c.notes.append(f"Level request through crossing traffic at {x} around T+{int(t // 60)}': {a['callsign']} "
                       f"F{a['fl']}→F{ask} asks, {b['callsign']} at F{b['fl']} crossing.")
        done += 1
    return done


def levelcoord_req_s() -> float:
    from .levelcoord import REQ_S
    return float(REQ_S)


def _transatlantic(c: _Ctx):
    """Flights to / from North America by IBALU and SUBAL / BEXAL. Westbound, mostly Moroccan departures climbing to
    their requested level (the West sector asks the Oceanic one for it); eastbound, at cruise."""
    from .levelcoord import WEST, OCEANIC
    ctl = set(c.P["control"])
    cands = [r for r in c.routes if "IBALU" in r["route"] and (r["route"][0] in ("SUBAL", "BEXAL")
                                                               or r["route"][-1] in ("SUBAL", "BEXAL"))]
    n = int(round(c.P["transatlantic_per_hour"] * c.T / 3600.0))
    if not cands or n <= 0:
        return 0
    for t in c.times(n, lo=240.0, hi=c.T - 600.0):
        r = c.rng.choice(cands)
        typ = c.rng.choice(["B788", "B789", "A332", "A333", "B77W", "B763", "A359"])
        west_bound = r["route"][-1] in ("SUBAL", "BEXAL")
        if (west_bound and ctl & set(OCEANIC) and not ctl & set(WEST)) or (not west_bound and ctl & set(WEST) and not ctl & set(OCEANIC)):
            # it enters the sectors across the boundary: the request comes 15 min before, so give it time to come
            # (flights within 15 min of the boundary at the start count as already coordinated)
            t = max(t, levelcoord_req_s() + 180.0)
        rfl = c.level(r["south"], 340, 400, typ) or 360
        moroccan = west_bound and c.rng.random() < 0.7
        fl = rfl
        if moroccan:
            fl = max(240, rfl - c.rng.choice([40, 60, 80, 100]))
            if not level_parity_ok(fl, r["south"]):
                fl -= 10
        kw = {"rfl": rfl} if fl != rfl else {}
        if west_bound:
            a = c.add(r, typ, fl, t, at_s=r["s_in"] + 5.0, keep=True, dest=c.rng.choice(NAT),
                      dep=c.rng.choice(["GMMN", "GMMX", "GMMN", "GMME"]) if moroccan else None,
                      ops=["RAM"] if moroccan else None, **kw)
        else:
            a = c.add(r, typ, fl, t, at_s=r["s_in"] + 5.0, keep=True, dep=c.rng.choice(NAT))
        c.notes.append(f"Transatlantic: {a['callsign']} {a['dep']}-{a['dest']} {r['route'][0]}-{r['route'][-1]} F{fl}"
                       + (f" climbing, requests F{rfl}" if rfl > fl else "") + f" around T+{int(t // 60)}'.")
    return n


def _dep_routes(c: _Ctx, icao: str):
    """Routes a departure from icao can join before entering the sectors (its SID leads to the route)."""
    if icao not in c.sid_ads:
        return []
    out = []
    flows = [r for r in c.routes if icao in (r.get("deps") or [])]
    if flows:                                          # the planned flows from it: those its SIDs join cleanly
        return [r for r in flows if c.join_route(r, icao) is not None]
    pool = [r for r in c.routes if not r.get("deps")]
    for r in pool:
        entry = _along(r["pts"], r["s_in"])
        part = _cut(r["pts"], 0.0, r["s_in"]) if r["s_in"] > 1 else [r["pts"][0], entry]
        if icao in c._near({icao: c.sid_ads[icao]}, part, entry, max_off=30.0, max_ref=130.0) \
                and c.join_route(r, icao) is not None:          # its SIDs lead onto it without turning back
            out.append(r)
    return out


def _arr_routes(c: _Ctx, icao: str):
    """Routes to icao: ending at its sequencing fix, else passing close to it after leaving the sectors."""
    fixes = {q["fix"] for q in c.asp.default_sequencing if q["aerodrome"] == icao}
    flows = [r for r in c.routes if icao in (r.get("dests") or [])]
    if flows:                                          # the planned flows to it
        return flows, any(r["route"][-1] in fixes for r in flows)
    seq = [r for r in c.routes if r["route"][-1] in fixes]
    if seq:
        return seq, True
    xy = c.ap_xy.get(icao)
    if xy is None:
        return [], False
    out = []
    for r in c.routes:
        exitp, L = _along(r["pts"], r["s_out"]), _poly_len(r["pts"])
        part = _cut(r["pts"], r["s_out"], L) if L - r["s_out"] > 1 else [exitp, r["pts"][-1]]
        if c._near({icao: xy}, part, exitp, max_off=30.0, max_ref=130.0):
            out.append(r)
    return out, False


def airports_for(control) -> List[dict]:
    """The aerodromes whose departures or arrivals the sectors handle, for the generator's per-aerodrome rates."""
    c = _Ctx({**DEFAULTS, "control": [str(s).upper() for s in control], "seed": 1})
    out = []
    for d in c.asp.default_aerodromes:
        icao = d["id"]
        dep = bool(_dep_routes(c, icao))
        arr = bool(_arr_routes(c, icao)[0])
        if dep or arr:
            out.append({"id": icao, "name": d.get("name", icao), "dep": dep, "arr": arr})
    return out


def _airport_flows(c: _Ctx) -> Dict[str, int]:
    done = {"departures": 0, "arrivals": 0}
    lo_fl, hi_fl = c.P["levels"]
    for icao, rates in (c.P.get("airports") or {}).items():
        icao = str(icao).upper()
        nd = int(round(float((rates or {}).get("dep") or 0) * c.T / 3600.0))
        na = int(round(float((rates or {}).get("arr") or 0) * c.T / 3600.0))
        routes = _dep_routes(c, icao)
        if routes and nd > 0:
            for t in c.times(nd, lo=0.0, hi=c.T - 300.0):
                r = c.rng.choice(routes)
                typ = c.pick_type()
                ad = c.sid_ads[icao]
                fl = c.level(r["south"], 200, min(340, hi_fl), typ) or 260
                # long enough after the start to be shown from take-off: the SID and the climb to its level
                climb_s = max(240.0 + dist(ad, _along(r["pts"], r["s_in"])) / 300.0 * 3600.0, 180.0 + fl * 100.0 / 1800.0 * 60.0)
                t = max(t, climb_s + 120.0)
                rfl = c.level(r["south"], max(fl + 20, lo_fl), hi_fl, typ)
                kw = {"rfl": rfl} if rfl and rfl > fl else {}
                a = c.add(r, typ, fl, t, dep=icao, **kw)
                c.forced_dep.add(a["callsign"])
                done["departures"] += 1
        arr, seq = _arr_routes(c, icao)
        if arr and na > 0:
            for t in c.times(na, lo=-300.0, hi=c.T - 240.0):
                r = c.rng.choice(arr)
                typ = c.pick_type()
                fl = c.level(r["south"], max(250, lo_fl - 40), hi_fl, typ) or 330
                c.add(r, typ, fl, t, dest=icao,
                      arrival=any(q["fix"] == r["route"][-1] and q["aerodrome"] == icao for q in c.asp.default_sequencing))
                done["arrivals"] += 1
    return done


def _background(c: _Ctx):
    # the hour's traffic minus the planted aircraft (they are part of it), plus the flights already in at the start
    n = max(0, int(round(c.P["aircraft_per_hour"] * c.T / 3600.0)) - len(c.aircraft)
            + int(round(c.P["aircraft_per_hour"] * 600.0 / 3600.0)))
    weights = [r["inside_nm"] for r in c.routes]
    arr_routes = [r for r in c.routes if any(r["route"][-1] == q["fix"] for q in c.asp.default_sequencing)]
    lo, hi = c.P["levels"]
    for t in c.times(n, lo=-600.0, hi=c.T - 240.0):
        if c.rng.random() < c.P["slow_share"]:
            r = c.rng.choice(arr_routes) if arr_routes and c.rng.random() < 0.7 else c.rng.choices(c.routes, weights)[0]
            typ, mil = c.rng.choice(SLOW)
            fl = c.level(r["south"], 170, 250, typ) or 210
            c.add(r, typ, fl, t, ops=OPS_SLOW, **({"military": True} if mil else {}))
            continue
        r = c.rng.choices(c.routes, weights)[0]
        typ = c.pick_type()
        fl = c.level(r["south"], lo, hi, typ)
        if fl is None:
            continue
        seq = next((q for q in c.asp.default_sequencing if q["fix"] == r["route"][-1]), None)
        if r.get("dests"):                         # a planned flow: its destination; an arrival when it lands there
            dest = c.rng.choice(r["dests"])
            c.add(r, typ, fl, t, dest=dest, arrival=bool(seq and seq["aerodrome"] == dest))
            continue
        c.add(r, typ, fl, t, **({"dest": seq["aerodrome"], "arrival": True} if seq else {"arrival": False}))


def _deconflict(scn: dict, keep: set, rounds: int = 7, caps: Optional[Dict[str, int]] = None) -> int:
    """Background flights in an unplanned conflict (planned trajectories) moved to another level of the right
    direction; planted ones are left alone. Returns the conflicts left that are not planted."""
    from .designer import compile as dcompile
    by = {a["callsign"]: a for a in scn["aircraft"]}
    left = 0
    for rnd in range(rounds):
        r = dcompile(scn)
        if not r.get("ok"):
            raise ValueError(r.get("error"))
        bad = [c for c in r["planned_conflicts"] if not (c["aircraft"][0] in keep and c["aircraft"][1] in keep)]
        left = len(bad)
        if not bad:
            break
        for cf in bad:
            a_cs, b_cs = cf["aircraft"]
            cs = b_cs if b_cs not in keep else a_cs
            if cs in keep or cs not in by:
                continue
            a = by[cs]
            if rnd >= 3:                                   # the levels did not do it: a few minutes later or earlier
                dt = (1 if (rnd + len(cs)) % 2 else -1) * (90 + 60 * (rnd - 3))
                if 0 <= a["reach"]["time"] + dt <= scn["duration_s"] - 120:
                    a["reach"]["time"] += dt
                continue
            south = r["aircraft"].get(cs, {}).get("southbound", True)
            mx = _max_fl(a["type"])
            cands = [a["fl"] + d for d in (20, -20, 40, -40, 60, -60)
                     if level_parity_ok(a["fl"] + d, south) and 170 <= a["fl"] + d <= min(mx, 410, (caps or {}).get(cs, 999))]
            if cands:
                a["fl"] = cands[(rnd + len(cs)) % len(cands)]
    return left


def _pos(plan, t):
    """Position on a planned track [[t, x, y], ...] at time t (None outside it)."""
    if not plan or t < plan[0][0] or t > plan[-1][0]:
        return None
    for (t0, x0, y0), (t1, x1, y1) in zip(plan, plan[1:]):
        if t0 <= t <= t1:
            k = (t - t0) / max(t1 - t0, 1e-9)
            return (x0 + (x1 - x0) * k, y0 + (y1 - y0) * k)
    return None


def _entry_spacing(scn: dict, keep: set, rounds: int = 10) -> int:
    """Every flight enters the sectors separated (the minimum of the exercise) from the traffic at its level: a planted
    pair already too close when the second one crosses the boundary is re-timed by the smallest shift that separates
    them at the entry and still lets them meet inside (the planted conflict is kept); a background flight is moved off
    the level (_deconflict). Returns the planted pairs left too close at the entry."""
    from .designer import compile as dcompile
    by = {a["callsign"]: a for a in scn["aircraft"]}
    bad = []
    for rnd in range(rounds):
        r = dcompile(scn)
        if not r.get("ok"):
            raise ValueError(r.get("error"))
        plans, info, sep = r["plans"], r["aircraft"], r["separation_horizontal_nm"]
        bad = []
        for cf in r["planned_conflicts"]:
            a_cs, b_cs = cf["aircraft"]
            if not (a_cs in keep and b_cs in keep):
                continue
            ea, eb = info[a_cs]["entry_s"], info[b_cs]["entry_s"]
            later, other = (a_cs, b_cs) if ea >= eb else (b_cs, a_cs)
            te = max(ea, eb)
            if cf["start_s"] > te + 5.0:
                continue                                  # they enter separated, the conflict comes later: fine
            bad.append((later, other, te, cf["minimum_nm"]))
        if not bad:
            return 0
        moved = set()
        for later, other, te, h in bad:
            if later in moved or other in moved or later not in by:
                continue
            pl, po = plans[later], plans[other]
            best = None
            for d in sorted(range(-300, 301, 15), key=abs):
                if d == 0:
                    continue
                pe, oe = _pos(pl, te), _pos(po, te + d)    # shifting 'later' by d = the other one seen at te + d
                if pe is None or oe is None or dist(pe, oe) < h + 0.5:
                    continue
                # still meeting inside: closest approach after the entry under the minimum
                dmin = min((dist(p, q) for t in range(int(te), int(pl[-1][0]), 10)
                            for p, q in [(_pos(pl, t), _pos(po, t + d))] if p and q), default=99.0)
                if dmin < h:
                    best = d
                    break
            if best is None:                               # cannot keep both: separate them at the entry anyway
                best = next((d for d in sorted(range(-300, 301, 15), key=abs) if d and _pos(pl, te) and _pos(po, te + d)
                             and dist(_pos(pl, te), _pos(po, te + d)) >= h + 0.5), None)
            if best is not None:
                by[later]["reach"]["time"] = int(by[later]["reach"]["time"] + best)
                moved.add(later)
    return len(bad)


def _prune(scn: dict) -> None:
    from .scenario import ScenarioError, build_world
    for _ in range(300):
        try:
            build_world(copy.deepcopy(scn))
            return
        except ScenarioError as e:
            cs = str(e).split(":")[0].strip()
            before = len(scn["aircraft"])
            scn["aircraft"] = [a for a in scn["aircraft"] if a["callsign"] != cs]
            scn["requests"] = [q for q in scn["requests"] if q["callsign"] != cs]
            if len(scn["aircraft"]) == before:
                raise


# ---------------------------------------------------------------- whole routes (beyond the sectors)
def _graph(c: _Ctx):
    """The route network: published routes and airways (one-way ones one way), forward and backward."""
    fwd, bwd = {}, {}
    one_way = {tuple(v["points"]) for v in (c.asp.airways or {}).values() if v.get("one_way")}
    seqs = [list(r) for r in c.asp.routes] + [list(v["points"]) for v in (c.asp.airways or {}).values()]
    for r in seqs:
        r = [n for n in r if n in c.wp]
        ow = tuple(r) in one_way
        for a, b in zip(r, r[1:]):
            fwd.setdefault(a, set()).add(b); bwd.setdefault(b, set()).add(a)
            if not ow:
                fwd.setdefault(b, set()).add(a); bwd.setdefault(a, set()).add(b)
    return fwd, bwd


def _shortest(c: _Ctx, adj, start: str, targets: set, avoid: set, limit_nm: float):
    import heapq
    best, prev, heap = {start: 0.0}, {}, [(0.0, start)]
    while heap:
        d, n = heapq.heappop(heap)
        if n in targets:
            path = [n]
            while path[-1] != start:
                path.append(prev[path[-1]])
            return path[::-1][1:]
        if d > best.get(n, 1e18) or d > limit_nm:
            continue
        for m in adj.get(n, ()):
            if m in avoid:
                continue
            nd = d + dist(c.wp[n], c.wp[m])
            if nd < best.get(m, 1e18):
                best[m], prev[m] = nd, n
                heapq.heappush(heap, (nd, m))
    return None


def _onward(c: _Ctx, adj, path: List[str], toward: Optional[float], avoid: set, steps: int = 30) -> List[str]:
    """Carry on along the network as straight as possible (and toward a direction when given) until it runs out."""
    out, seen = [], set(path) | avoid
    a, b = path[-2], path[-1]
    for _ in range(steps):
        trk = bearing(c.wp[a], c.wp[b])
        cands = []
        for m in adj.get(b, ()):
            if m in seen or dist(c.wp[b], c.wp[m]) < 0.5:
                continue
            brg = bearing(c.wp[b], c.wp[m])
            turn = abs((brg - trk + 540) % 360 - 180)
            if turn > 60:
                continue
            aim = abs((brg - toward + 540) % 360 - 180) if toward is not None else 0.0
            cands.append((turn + 0.6 * aim, m))
        if not cands:
            break
        m = min(cands)[1]
        out.append(m); seen.add(m)
        a, b = b, m
    return out


def _inside_any(c: _Ctx, n: str) -> bool:
    return any(point_in_polygon(c.wp[n], p) for p in c.polys)


def _whole_routes(scn: dict, c: _Ctx) -> int:
    """Each flight's route from its departure (or as far back as the network goes) to its destination (when in the
    GMMM FIR: by the airways to a STAR, then the STAR) or as far on as the network goes, never back into our sectors
    (the entry and exit stay where they were)."""
    fwd, bwd = _graph(c)
    stars = {d["id"]: d.get("stars") or [] for d in c.asp.default_aerodromes}
    n_ext = 0
    for a in scn["aircraft"]:
        names = list(a["route"])
        if len(names) < 2:
            continue
        inside = {n for n in names if _inside_any(c, n)}
        avoid = {n for n in c.wp if n not in names and _inside_any(c, n)}       # our sectors: entered once
        # ---- after the last point
        dest, after = a.get("dest", ""), []
        st = [s_ for s_ in stars.get(dest, []) if s_.get("via")]
        if a.get("arrival") and any(q["fix"] == names[-1] for q in c.asp.default_sequencing):
            star = next((s_ for s_ in st if s_["via"][0] == names[-1]), None)       # the STAR from its fix
            after = [n for n in (star["via"][1:] if star else []) if n in c.wp and n not in names]
        else:
            if st:
                tgt = {s_["via"][0] for s_ in st if s_["via"][0] in c.wp}
                if names[-1] in tgt:
                    path = []
                else:
                    path = _shortest(c, fwd, names[-1], tgt, avoid | set(names), 400.0)
                if path is not None:
                    star = next(s_ for s_ in st if s_["via"][0] == (path[-1] if path else names[-1]))
                    after = path + [n for n in star["via"][1:] if n in c.wp and n not in names and n not in path]
            if not after:
                toward = None
                if dest in c.ap_xy:
                    toward = bearing(c.wp[names[-1]], c.ap_xy[dest])
                after = _onward(c, fwd, names, toward, avoid)
                if dest in c.ap_xy and after:              # stop where it is closest to its destination
                    k = min(range(len(after)), key=lambda i: dist(c.wp[after[i]], c.ap_xy[dest]))
                    after = after[:k + 1]
                    if dist(c.wp[after[-1]], c.ap_xy[dest]) >= dist(c.wp[names[-1]], c.ap_xy[dest]):
                        after = []                          # already where it leaves for its destination
        # ---- before the first point (not for a departure joining from its SID: that is its way in)
        before = []
        if a.get("dep") not in c.sid_ads:
            dep = a.get("dep", "")
            toward = bearing(c.wp[names[0]], c.ap_xy[dep]) if dep in c.ap_xy else None
            back = _onward(c, bwd, names[::-1], toward, avoid | set(after))
            if dep in c.ap_xy and back:
                k = min(range(len(back)), key=lambda i: dist(c.wp[back[i]], c.ap_xy[dep]))
                back = back[:k + 1]
            before = back[::-1]
        if before or after:
            a["_route_core"] = names
            a["route"] = before + names + after
            n_ext += 1
    return n_ext


def _check_whole_routes(scn: dict) -> int:
    """Back to the published part for any flight whose whole route changed where it enters or leaves our sectors."""
    from .scenario import build_world
    core = {a["callsign"]: a["_route_core"] for a in scn["aircraft"] if "_route_core" in a}
    if not core:
        return 0
    test = copy.deepcopy(scn)
    for a in test["aircraft"]:
        a.pop("_route_core", None)
    short = copy.deepcopy(test)
    for a in short["aircraft"]:
        if a["callsign"] in core:
            a["route"] = core[a["callsign"]]
    reverted = 0
    try:
        w1, w0 = build_world(test), build_world(short)
    except Exception:
        w1 = None
    for a in scn["aircraft"]:
        cs = a["callsign"]
        if cs in core:
            ok = w1 is not None and cs in w1.aircraft and cs in w0.aircraft and (
                w1.aircraft[cs].entry_wp == w0.aircraft[cs].entry_wp
                and w1.aircraft[cs].planned_exit == w0.aircraft[cs].planned_exit
                and bool(getattr(w1.aircraft[cs], "arr", None)) == bool(getattr(w0.aircraft[cs], "arr", None)))
            if not ok:
                a["route"] = core[cs]
                reverted += 1
        a.pop("_route_core", None)
    return reverted


def _climb_alt_at(perf, mass: float, along_nm: float, elev_ft: float, sid_kt: float) -> float:
    """Altitude a departure reaches along_nm from the runway, climbing all the way (its own rates and speeds: the
    SID's speed up to 4000 ft above the field, 250 kt below FL100, then its schedule), still air."""
    alt, d, t, dt = elev_ft + 50.0, 0.0, 0.0, 5.0
    while d < along_nm and t < 3600.0 and alt < perf.ceiling_ft:
        if alt < elev_ft + 4000.0:
            mode, val = "cas", min(sid_kt, 250.0)
        elif alt < 10000.0:
            mode, val = "cas", 250.0
        else:
            mode, val = perf.schedule(alt)
        tas = perf.tas_for(mode, val, alt)
        alt += max(300.0, 0.75 * perf.roc_fpm(alt, mode, val, mass)) * dt / 60.0   # (the simulation climbs a bit slower)
        d += tas * dt / 3600.0
        t += dt
    return alt


LEVEL_BY_NM = 12.0          # a departure handed over from a nearby aerodrome is level this far before our boundary


def _check_built(scn: dict, c: _Ctx) -> dict:
    """On the built exercise: a flight filed from an aerodrome with SIDs that cannot be shown from take-off (too early
    in the exercise, or no SID joins its route) is refiled from a distant aerodrome; a level against the
    semicircular rule for the direction the scenario gives it is moved to the nearest right one (background only)."""
    from .scenario import build_world
    w = build_world(copy.deepcopy(scn))
    by_route = {tuple(r["route"]): r for r in c.routes}
    out = {"from_takeoff": 0, "refiled": 0, "levels_fixed": 0, "levelled": 0}
    wps = w.env.waypoints
    for a in scn["aircraft"]:
        ac = w.aircraft.get(a["callsign"].upper())
        if ac is None:
            continue
        if a["dep"] in w.aerodromes and getattr(w.aerodromes[a["dep"]], "runways", None):
            if getattr(ac, "sid", None):
                out["from_takeoff"] += 1
                # it must reach us level: the transfer level is one it reaches LEVEL_BY_NM before the entry; the
                # level it wants goes into its requested level (it asks us for the climb)
                if ac.entry_wp in ac.route:
                    k = ac.route.index(ac.entry_wp)
                    pts = [wps[n] for n in ac.route[:k + 1] if n in wps]
                    L = sum(dist(p, q) for p, q in zip(pts, pts[1:]))
                    # level LEVEL_BY_NM before the boundary, or (an aerodrome right under it: Fes, Tanger, Errachidia)
                    # a third of the way there - approach releases those low
                    margin = min(LEVEL_BY_NM, L / 3.0)
                    reach = _climb_alt_at(ac.perf, ac.mass, max(0.0, L - margin), ac.sid.get("elev_ft", 0.0),
                                          ac.sid.get("max_kt", 250.0))
                    floor = int(ac.sid.get("elev_ft", 0.0) // 1000) * 10 + 40
                    cap = next((l for l in range(int(reach // 1000) * 10, floor - 1, -10)
                                if level_parity_ok(l, ac.southbound)), None) or max(60, floor)
                    if cap is not None:
                        c.level_cap[a["callsign"]] = cap
                        if a["fl"] > cap:
                            a["rfl"] = max(a.get("rfl") or 0, a["fl"])
                            a["fl"] = cap
                            out["levelled"] += 1
            elif a["callsign"] in c.forced_dep or (a["callsign"] in c.flow_cs and a["callsign"] not in c.keep):
                if a["reach"]["time"] + 300 <= scn["duration_s"] - 240:
                    a["reach"]["time"] += 300               # later: time to take off and climb in view
                    out["retimed"] = out.get("retimed", 0) + 1
            elif a["callsign"] in c.flow_cs:
                pass                                        # a planted flight of a planned flow: airborne, same origin
            elif not a.get("start"):
                r = by_route.get(tuple(a["route"]))
                if r is not None:
                    a["dep"] = c._far(r, "dep")
                    out["refiled"] += 1
        if a["callsign"] not in c.keep and not level_parity_ok(a["fl"], ac.southbound):
            mx = _max_fl(a["type"])
            for d in (10, -10, 30, -30):
                if level_parity_ok(a["fl"] + d, ac.southbound) and 150 <= a["fl"] + d <= min(mx, 410):
                    a["fl"] += d
                    out["levels_fixed"] += 1
                    break
    return out


def generate(params: Optional[dict] = None) -> dict:
    params = dict(params or {})
    if "coordinations_per_hour" in params:                 # the earlier name
        params.setdefault("transatlantic_per_hour", params.pop("coordinations_per_hour"))
    P = {**DEFAULTS, **{k: v for k, v in params.items() if v is not None}}
    if P["seed"] is None:
        P["seed"] = random.randrange(1, 10 ** 6)
    P["control"] = [str(s).upper() for s in (P["control"] if isinstance(P["control"], list) else [P["control"]])]
    P["seq_size"] = [int(P["seq_size"][0]), int(P["seq_size"][-1])]
    c = _Ctx(P)
    if not c.routes:
        raise ValueError(f"no published route through {'+'.join(P['control'])}")
    planted = {"sequences": _sequences(c), "overtakes": _overtakes(c)}
    pairs = _crossing_pairs(c)
    planted["crossings"] = _crossings(c, pairs)
    planted["level_crossings"] = _level_crossings(c, pairs)
    planted["transatlantic"] = _transatlantic(c)
    planted.update(_airport_flows(c))
    _background(c)
    scn = {"name": f"gen_{'_'.join(s.lower() for s in P['control'])}_{P['seed']}", "airspace": "agadir",
           "control": P["control"], "duration_s": int(c.T), "pilot_delay_s": 4, "start_utc": P["start_utc"],
           "title": f"Generated - {'+'.join(P['control'])}, {P['aircraft_per_hour']} flights/h",
           "description": (f"Generated (seed {P['seed']}): {P['aircraft_per_hour']} flights/h, density {P['density']}, "
                           f"{P['sequences_per_hour']} sequences/h of {P['seq_size'][0]}-{P['seq_size'][1]}, "
                           f"{P['overtakes_per_hour']} overtakes/h, {P['crossings_per_hour']} crossings/h, "
                           f"{P['level_crossings_per_hour']} level requests through traffic/h, slow traffic "
                           f"{int(P['slow_share'] * 100)} %, long-haul {int(P['heavy_share'] * 100)} %."),
           "weather": {"seed": P["seed"] % 1000, "wind": {"climatology": {"month": c.rng.randint(1, 12)}, "noise_kt": 5}},
           **({"separation": {"horizontal_nm": float(P["separation_nm"])}} if P.get("separation_nm") else {}),
           "pre_entry_nm": 55.0,     # on the scope well before they call (the departures nearby from take-off)
           "areas": [], "events": [], "aircraft": c.aircraft, "requests": c.requests,
           "design_notes": " ".join(c.notes), "generator": {k: P[k] for k in DEFAULTS}}
    extended = _whole_routes(scn, c)
    reverted = _check_whole_routes(scn)
    _prune(scn)
    checks = _check_built(scn, c)
    for _ in range(3):
        if not checks.get("retimed"):
            break
        checks = _check_built(scn, c)
    entry_left = _entry_spacing(scn, c.keep)
    left = _deconflict(scn, c.keep, caps=c.level_cap)
    scn["generator"]["planted"] = planted
    scn["generator"]["departures_from_takeoff"] = checks["from_takeoff"]
    scn["generator"]["departures_levelled"] = checks.get("levelled", 0)
    scn["generator"]["planted_too_close_at_entry"] = entry_left
    scn["generator"]["whole_routes"] = extended - reverted
    scn["generator"]["unplanned_conflicts_left"] = left
    return scn
