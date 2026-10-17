"""Declared sector capacities and the traffic load of a scenario against them.

Capacity = flights per hour the (group of) sector(s) can take, as declared by the unit (airspace JSON,
"capacity_per_hour", keyed by the sorted controlled sectors joined with '+').  The load of a scenario is the number of
flights inside the controlled sectors at some time during its first hour, estimated geometrically (each flight along
its route at its true airspeed, from its position at T+0 or from its entry point at its boundary time): no weather
deviations or instructions, so it is quick enough for every designer save.

Traffic density (1-5) is the load against the capacity: a slight overshoot of the declared capacity is the top of
the scale, not an exaggerated one.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .geometry import dist, point_in_polygon

# load / capacity -> density 1..5
DENSITY_BANDS = (0.50, 0.70, 0.90, 1.05)          # <50 %, 50-70, 70-90, 90-105, >105 % (aim at most ~115 %)
DENSITY_NAMES = {1: "very low", 2: "low", 3: "moderate", 4: "high", 5: "very high"}
# names the unit uses for the band-boxed configurations
CONFIG_NAMES = {"WN+WS": "WW", "ON+OS": "OCE", "ON+OS+WN+WS": "WW-OCE", "ON": "OCN", "OS": "OCS"}


def config_key(sectors: List[str]) -> str:
    return "+".join(sorted(sectors))


def capacity(asp, sectors: Optional[List[str]] = None) -> Optional[int]:
    table = (getattr(asp, "notes", None) or {}).get("capacity_per_hour") or getattr(asp, "capacity_per_hour", None)
    if not table:
        return None
    secs = sectors or asp.controlled or [s.id for s in asp.sectors]
    key = config_key(secs)
    if key in table:
        return int(table[key])
    known = [int(table[s]) for s in secs if s in table]
    return min(known) if known else None


def density_level(ratio: float) -> int:
    return 1 + sum(ratio >= b for b in DENSITY_BANDS)


def flights_in_hour(world, t0: float = 0.0, t1: float = 3600.0, step_nm: float = 4.0) -> List[str]:
    """Callsigns of the flights inside the controlled area at some time between t0 and t1 (geometric estimate)."""
    asp, wps = world.airspace, world.waypoints
    poly = asp.polygon
    out = []
    for a in world.aircraft.values():
        gs = a.tas if a.tas and a.tas > 150 else 460.0
        if a.status == "scheduled":
            if a.entry_wp not in a.route:
                continue
            i0 = a.route.index(a.entry_wp)
            pts = [wps[n] for n in a.route[i0:] if n in wps]
            t = a.boundary_time
        else:
            if not (a.active or a.outside):
                continue
            pts = [tuple(a.pos)] + [wps[n] for n in a.route[a.next_index:] if n in wps]
            t = 0.0
        hit = False
        for p, q in zip(pts, pts[1:]):
            L = dist(p, q)
            n = max(1, int(L / step_nm))
            for k in range(n + 1):
                tt = t + k / n * L / gs * 3600.0
                if tt > t1:
                    break
                if tt >= t0 and point_in_polygon((p[0] + (q[0] - p[0]) * k / n, p[1] + (q[1] - p[1]) * k / n), poly):
                    hit = True
                    break
            if hit or t > t1:
                break
            t += L / gs * 3600.0
        if hit:
            out.append(a.callsign)
    return out


def traffic_load(scn: dict, world=None) -> Dict:
    """{"config", "unit_name", "capacity", "flights_per_hour", "ratio", "density"} for the first hour of a scenario
    (flights per hour scaled when the scenario is shorter than an hour)."""
    if world is None:
        from .scenario import build_world
        world = build_world(scn)
    dur = min(3600.0, float(scn.get("duration_s") or 3600))
    n = len(flights_in_hour(world, 0.0, dur))
    per_h = n * 3600.0 / max(dur, 600.0)
    asp = world.airspace
    secs = asp.controlled or [s.id for s in asp.sectors]
    key = config_key(secs)
    cap = capacity(asp, secs)
    out = {"config": key, "unit_name": CONFIG_NAMES.get(key, key), "capacity": cap,
           "flights_per_hour": round(per_h)}
    if cap:
        out["ratio"] = round(per_h / cap, 2)
        out["density"] = density_level(per_h / cap)
    return out
