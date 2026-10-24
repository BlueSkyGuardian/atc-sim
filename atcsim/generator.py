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
                levels=[290, 400], weather="calm")

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
        self.T = P["duration_min"] * 60.0
        self._routes()

    # ---- the published routes through the controlled sectors
    def _routes(self):
        out, seen = [], set()
        for r in self.asp.routes:
            r = [n for n in r if n in self.wp]
            if len(r) < 2 or tuple(r) in seen:
                continue
            seen.add(tuple(r))
            pts = [self.wp[n] for n in r]
            Lin = _poly_len_inside(pts, self.polys)
            if Lin < 25.0:
                continue
            south = 90.0 <= bearing(pts[0], pts[-1]) < 270.0
            out.append({"route": r, "pts": pts, "inside_nm": Lin, "south": south,
                        "s_in": _first_inside(pts, self.polys) or 0.0})
        self.routes = out

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
            if fir in role:
                return airports
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
        lv = [l for l in range(lo, mx + 1, 10) if l < 290 or level_parity_ok(l, south)]
        lv = [l for l in lv if l not in avoid] or lv
        return self.rng.choice(lv) if lv else None

    def add(self, r, typ, fl, t, at_s=None, ops=None, dest=None, dep=None, mach=None, keep=False, **kw) -> dict:
        """An aircraft on route r at level fl, at the point at_s NM along the route (default: where it enters the
        controlled area, plus a little) at time t."""
        s = r["s_in"] + 10.0 if at_s is None else at_s
        p = _along(r["pts"], s)
        a = {"callsign": self.callsign(ops or (OPS_LONG if typ in LONG else OPS_SHORT)), "type": typ,
             "route": list(r["route"]), "fl": int(fl),
             "dep": dep or self.rng.choice(self.region_of(r["route"][0], "dep")),
             "dest": dest or self.rng.choice(self.region_of(r["route"][-1], "dest")),
             "reach": {"point": [round(p[0], 3), round(p[1], 3)], "time": int(round(t))}}
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
    fixes = [q for q in fixes if any(r["route"][-1] == q["fix"] or q["fix"] in r["route"] for r in c.routes)]
    n = int(round(c.P["sequences_per_hour"] * c.T / 3600.0))
    if not fixes or n <= 0:
        return 0
    done = 0
    for ts in c.times(n, lo=420.0, hi=c.T - 300.0):
        q = c.rng.choice(fixes)
        fix = q["fix"]
        inbound = [r for r in c.routes if fix in r["route"] and r["route"].index(fix) > 0]
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
    n = int(round(c.P["crossings_per_hour"] * c.T / 3600.0))
    if not pairs or n <= 0:
        return 0
    done = 0
    for t in c.times(n, lo=300.0, hi=c.T - 300.0):
        r1, r2, xp, s1, s2, x = _pick_pair(c, pairs)
        x = x or f"{r1['route'][0]}-{r1['route'][-1]} x {r2['route'][0]}-{r2['route'][-1]}"
        t1, t2 = c.pick_type(), c.pick_type()
        both = [l for l in range(330, 400, 10) if (l < 290 or level_parity_ok(l, r1["south"])) and
                (l < 290 or level_parity_ok(l, r2["south"])) and l <= min(_max_fl(t1), _max_fl(t2))]
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
            if fl >= 290 and not level_parity_ok(fl, r["south"]):
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
        c.add(r, typ, fl, t, **({"dest": seq["aerodrome"], "arrival": True} if seq else {"arrival": False}))


def _deconflict(scn: dict, keep: set, rounds: int = 4) -> int:
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
            south = r["aircraft"].get(cs, {}).get("southbound", True)
            mx = _max_fl(a["type"])
            cands = [a["fl"] + d for d in (20, -20, 40, -40, 60, -60)
                     if (a["fl"] + d < 290 or level_parity_ok(a["fl"] + d, south)) and 170 <= a["fl"] + d <= min(mx, 410)]
            if cands:
                a["fl"] = cands[(rnd + len(cs)) % len(cands)]
    return left


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
           "areas": [], "events": [], "aircraft": c.aircraft, "requests": c.requests,
           "design_notes": " ".join(c.notes), "generator": {k: P[k] for k in DEFAULTS}}
    _prune(scn)
    left = _deconflict(scn, c.keep)
    scn["generator"]["planted"] = planted
    scn["generator"]["unplanned_conflicts_left"] = left
    return scn
