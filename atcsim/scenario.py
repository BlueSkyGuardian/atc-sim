"""Scenario loading and validation.

A scenario is a JSON document:

{
  "name": "s02_climb_request_trap",
  "title": "Short human title",
  "description": "What the controller sees (shown to agents).",
  "design_notes": "The trap. Hidden from agents unless ?include_notes=true.",
  "duration_s": 1500,
  "pilot_delay_s": 4,
  "airspace": "training" | "agadir",                      (optional, default training)
  "control": ["WS"],                                      (optional: Agadir sectors you control, default all)
  "events": [{"time": 400, "callsign": "X", "type": "rvsm_unable", "reason": "autopilot failure"}],
  "sector": {"lower_fl": 245, "upper_fl": 460},          (optional)
  "extra_waypoints": {"MID01": [0, 0]},                    (optional, interior fixes)
  "areas": [
     {"id": "R12", "name": "Firing range", "kind": "restricted",        # danger | restricted | prohibited
      "polygon": [[x, y], ...]  OR  "circle": {"center": [x, y], "radius_nm": 8},
      "lower_fl": 0, "upper_fl": 660, "active": false,
      "schedule": [{"time": 300, "active": true}, {"time": 1200, "active": false}]}
  ],
  "weather": {                                             (optional)
     "isa_deviation_c": -3,
     "wind": {"layers": [{"fl": 250, "dir": 260, "kt": 45}, {"fl": 350, "dir": 270, "kt": 85}],
              "gradient_kt_per_100nm": {"east": 0, "north": 10}}
  },
  "aircraft": [
     {"callsign": "RAM101", "type": "B738", "route": ["N2", "S3"], "fl": 350,
      "mach": 0.78,            # optional filed cruise Mach (default: type's typical cruise Mach, OpenAP WRAP)
      "load": 0.5,             # optional: mass = OEW + load * (MTOW - OEW); or "mass_kg": 65000
      "arrival": true,         # exiting at a sequencing fix = arrival to its aerodrome; false = overflight
      # timing - exactly one of (ground speed with wind is used to work out the entry time):
      "entry_time": 60                                     # appears at route[0] at t=60 s
      "reach": {"waypoint": "S3", "time": 900}             # is AT that waypoint at t=900 s
      "reach": {"point": [0.0, 0.0], "time": 480}          # is at that point (on its route) at t=480 s
     }
  ],
  "requests": [
     {"time": 180, "callsign": "RAM101", "type": "altitude", "value": 370, "reason": "ride reports"}
     # type: altitude (value=FL) | direct (value=waypoint) | speed (value=Mach if < 2, else IAS knots)
  ]
}
"""
from __future__ import annotations

import json
import os
from typing import Dict, List

import math

from . import airspace as airspace_mod, arrivals, performance
from .aircraft import Aircraft, Env
from .geometry import (
    SECTOR_LOWER_FL, SECTOR_UPPER_FL, bearing, dist, polyline_length, project_on_polyline,
)
from .model import AREA_KINDS, Area
from . import routing
from .weather import Weather
from .world import World

SCENARIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios")


EVENT_TYPES = ("rvsm_unable", "rvsm_restored")


class ScenarioError(ValueError):
    pass


def list_scenarios(directory: str = SCENARIO_DIR) -> List[dict]:
    out = []
    for fn in sorted(os.listdir(directory)):
        if fn.endswith(".json"):
            try:
                with open(os.path.join(directory, fn)) as f:
                    s = json.load(f)
                out.append({"name": s.get("name", fn[:-5]), "title": s.get("title", ""),
                            "description": s.get("description", ""), "aircraft": len(s.get("aircraft", [])),
                            "requests": len(s.get("requests", [])), "areas": len(s.get("areas", [])),
                            "airspace": s.get("airspace", "training"), "control": s.get("control"),
                            "duration_s": s.get("duration_s")})
            except Exception as e:  # pragma: no cover
                out.append({"name": fn[:-5], "error": str(e)})
    return out


def load_scenario(name: str, directory: str = SCENARIO_DIR) -> dict:
    fn = name if name.endswith(".json") else name + ".json"
    path = os.path.join(directory, os.path.basename(fn))
    if not os.path.exists(path):
        raise ScenarioError(f"scenario '{name}' not found")
    with open(path) as f:
        return json.load(f)


def public_view(scn: dict, include_notes: bool = False) -> dict:
    """What a client is allowed to see about a scenario (no future requests, no trap notes)."""
    # the raw weather spec (storm positions, life cycles) is future knowledge: agents get the weather through the
    # /weather endpoints (what a radar shows now); evaluators can see the spec with include_notes
    keys = ["name", "title", "description", "duration_s", "pilot_delay_s", "sector", "extra_waypoints",
            "airspace", "control"]
    out = {k: scn[k] for k in keys if k in scn}
    if include_notes:
        out["weather"] = scn.get("weather")
        out["design_notes"] = scn.get("design_notes")
        out["expected"] = scn.get("expected")
        out["requests"] = scn.get("requests", [])
        out["events"] = scn.get("events", [])
        out["aircraft"] = scn.get("aircraft", [])
    return out


def _route_time_s(pts, along_nm: float, tas_kt: float, wx: Weather, fl: float) -> float:
    """Flying time along the route polyline up to `along_nm`, using ground speed with the (3D) wind, 5 NM steps."""
    t, left = 0.0, along_nm
    for i in range(len(pts) - 1):
        seg = dist(pts[i], pts[i + 1])
        d_seg = min(seg, left)
        if d_seg <= 0:
            break
        c = math.radians(bearing(pts[i], pts[i + 1]))
        n = max(1, int(math.ceil(d_seg / 5.0)))
        piece = d_seg / n
        for k in range(n):
            f = (k + 0.5) * piece / seg
            x = pts[i][0] + f * (pts[i + 1][0] - pts[i][0])
            y = pts[i][1] + f * (pts[i + 1][1] - pts[i][1])
            u, v = wx.at(x, y, fl, t)
            along_w = u * math.sin(c) + v * math.cos(c)
            cross_w = u * math.cos(c) - v * math.sin(c)
            gs = math.sqrt(max(tas_kt ** 2 - cross_w ** 2, 1.0)) + along_w
            t += piece / gs * 3600.0
        left -= d_seg
    return t


DEFAULT_EQUIPMENT = {"rvsm": "SDE2E3FGHIJ1RWY/LB1", "non_rvsm": "SDE2FGHIRY/LB1", "military": "SDFGHIUY/S"}


def _wake(perf) -> str:
    if perf.icao == "A388":
        return "J"
    return "H" if perf.mtow >= 136000 else "M" if perf.mtow > 7000 else "L"


def _airspace_for(scn: dict):
    try:
        return airspace_mod.get(scn.get("airspace"), scn.get("control"))
    except (KeyError, FileNotFoundError, ValueError) as e:
        raise ScenarioError(f"airspace: {e}")


def _place_pre_entry(names: List[str], waypoints: Dict[str, tuple], idx_entry: int, pre_nm: float):
    """Point `pre_nm` before the entry waypoint, along the upstream part of the route if there is one, else on the
    extension of the first leg. Returns (pre_point, upstream names still ahead of it)."""
    up = [waypoints[w] for w in names[:idx_entry + 1]]
    left = pre_nm
    for i in range(len(up) - 1, 0, -1):
        seg = dist(up[i], up[i - 1])
        if seg >= left:
            f = left / seg
            p = (up[i][0] + f * (up[i - 1][0] - up[i][0]), up[i][1] + f * (up[i - 1][1] - up[i][1]))
            return p, names[i:idx_entry]
        left -= seg
    a, b = (up[0], up[1]) if len(up) > 1 else (waypoints[names[0]], waypoints[names[1]])
    L = dist(a, b)
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    return (a[0] - ux * left, a[1] - uy * left), names[:idx_entry]


def _boundary_crossings(asp, a, b, ka: str = "", kb: str = "") -> List[tuple]:
    """Points where the leg a->b crosses the controlled boundary, ordered from a (ends of the leg excluded)."""
    from .geometry import segment_intersection
    pts = []
    for e0, e1 in asp._edges:
        p = segment_intersection(a, b, e0, e1)
        if p is not None and dist(a, p) > 0.5 and dist(b, p) > 0.5 and not any(
                dist(e, p) < 2.5 and k in asp.boundary_points for e, k in ((a, ka), (b, kb))):
            if all(dist(p, q) > 0.5 for q in pts):
                pts.append(p)
    return sorted(pts, key=lambda p: dist(a, p))


def _add_cops(names: List[str], asp, waypoints: Dict[str, tuple]) -> List[str]:
    """Where a route leg enters or leaves the controlled area between two named points, a coordination point (COP)
    is added on the boundary and used as the entry / exit (transfer) point: 'C' + 2 letters of each neighbour."""
    out = [names[0]]
    for a, b in zip(names, names[1:]):
        for k, p in enumerate(_boundary_crossings(asp, waypoints[a], waypoints[b], a, b)):
            cop = f"C{a.lstrip('@')[:2]}{b.lstrip('@')[:2]}" + (str(k + 1) if k else "")
            waypoints[cop] = p
            asp.boundary_points[cop] = p
            out.append(cop)
        out.append(b)
    return out


def build_world(scn: dict) -> World:
    import copy as _copy
    asp = _copy.copy(_airspace_for(scn))           # per-world copy: coordination points are added to it
    asp.boundary_points = dict(asp.boundary_points)
    asp.waypoints = dict(asp.waypoints)
    waypoints: Dict[str, tuple] = asp.waypoints
    for k, v in (scn.get("extra_waypoints") or {}).items():
        waypoints[k.upper()] = asp.resolve_point(v)
    sector = scn.get("sector") or {}
    lower_fl = int(sector.get("lower_fl", asp.lower_fl if asp.id != "training" else SECTOR_LOWER_FL))
    upper_fl = int(sector.get("upper_fl", asp.upper_fl if asp.id != "training" else SECTOR_UPPER_FL))
    wspec = scn.get("weather") or {}
    span = max(asp.bbox[2] - asp.bbox[0], asp.bbox[3] - asp.bbox[1])
    weather = Weather(wspec, float(scn.get("duration_s", 1800)), domain_nm=384.0 if span <= 300 else 768.0)
    if span > 300:
        b = asp.bbox
        weather.view_bbox = (b[0] - 30.0, b[1] - 30.0, b[2] + 30.0, b[3] + 30.0)
    dT = weather.isa_deviation_c
    env = Env(waypoints=waypoints, weather=weather, dT=dT, airspace=asp)
    wind = weather

    try:
        aerodromes, seqfixes = arrivals.build(scn, waypoints, weather, asp)
    except ValueError as e:
        raise ScenarioError(str(e))
    # only the sequencing fixes on the boundary of the controlled area are ours (a single sector may not have any)
    seqfixes = {k: v for k, v in seqfixes.items() if k in asp.boundary_points}
    pre_entry_nm = float(scn.get("pre_entry_nm", 30.0))     # aircraft appear this far before their entry point
    checkin_nm = float(scn.get("checkin_nm", 10.0))         # and call on frequency this far before it
    aircraft: List[Aircraft] = []
    seen = set()
    for i, a in enumerate(scn.get("aircraft", [])):
        cs = str(a["callsign"]).upper()
        if cs in seen:
            raise ScenarioError(f"duplicate callsign {cs}")
        seen.add(cs)
        names = [w.upper() for w in a["route"]]
        if len(names) < (1 if a.get("start") else 2):
            raise ScenarioError(f"{cs}: route needs at least an entry and an exit waypoint"
                                + (" (or a start position and one waypoint)" if not a.get("start") else ""))
        for w in names:
            if w not in waypoints:
                raise ScenarioError(f"{cs}: unknown waypoint {w}")
        # flight plan beyond the controlled airspace: points before the entry / after the exit, by transfer point
        # and destination (departure for entries), unless the route already gives them or auto_route is off
        dest0 = str(a.get("dest") or "").upper()
        if not dest0 and names[-1] in seqfixes and a.get("arrival", True):
            dest0 = seqfixes[names[-1]].aerodrome
        start = a.get("start")              # free starting position: {"point": [x, y] or a point name, "time": s}
        if a.get("auto_route", scn.get("auto_routes", True)) and asp.id != "training":
            before, after = routing.complete(asp, names, dest0, str(a.get("dep") or ""), key=cs,
                                             runways={k: v.in_use for k, v in aerodromes.items()})
            names = ([] if start else before) + names + after
        filed = list(names)
        start_name = None
        if start:
            if "time" not in start:
                raise ScenarioError(f"{cs}: start needs a 'time'")
            start_name = f"@{cs}"
            waypoints[start_name] = asp.resolve_point(start["point"])
            if dist(waypoints[start_name], waypoints[names[0]]) < 0.5:
                names = names[1:] if len(names) > 2 else names
            names = [start_name] + names
        if asp.id != "training":
            names = _add_cops(names, asp, waypoints)
        # entry = first route point on the controlled boundary (or inside); points before it are upstream
        # (previous sector); the route ends at the exit = last point on the boundary or inside.
        inside = [w in asp.boundary_points or asp.inside(waypoints[w]) for w in names]
        if not any(inside):
            raise ScenarioError(f"{cs}: route {names} never enters the controlled airspace")
        idx_entry = inside.index(True)
        idx_exit = len(inside) - 1 - inside[::-1].index(True)
        if idx_exit <= idx_entry:
            raise ScenarioError(f"{cs}: route has no leg inside the controlled airspace")
        route = names[idx_entry:idx_exit + 1]
        after_exit = [w for w in names[idx_exit + 1:] if not w.startswith("C") or w in asp.point_info]
        try:
            perf = performance.get(a.get("type", "A320"), dT)
        except KeyError as e:
            raise ScenarioError(f"{cs}: {e.args[0]}")
        fl = int(a["fl"])
        if "mass_kg" in a:
            mass = float(a["mass_kg"])
        else:
            load = float(a.get("load", performance.DEFAULT_LOAD))
            mass = perf.oew + load * (perf.mtow - perf.oew)
        if not (perf.oew < mass <= perf.mtow):
            raise ScenarioError(f"{cs}: mass {mass:.0f} kg outside {perf.icao} OEW-MTOW")
        filed_mach = float(a["mach"]) if "mach" in a else None
        ac = Aircraft(callsign=cs, perf=perf, route=route, entry_time=0.0, fl=fl, mass=mass, filed_mach=filed_mach)
        if "ias_kt" in a:
            ac.spd_cas, ac.spd_ias_now = float(a["ias_kt"]), True
        # category, formation, RVSM approval and equipment
        ac.military = bool(a.get("military", perf.military))
        form = a.get("formation")
        if form:
            if isinstance(form, int):
                form = {"size": form}
            n = int(form.get("size", 2))
            if n < 2:
                raise ScenarioError(f"{cs}: a formation has at least 2 aircraft")
            ac.formation = {"size": n, "types": form.get("types", f"{n}x{perf.icao}"),
                            "extent_nm": float(form.get("extent_nm", 1.0)),
                            "note": "formation flight: only the leader is shown; 2000 ft from any other aircraft, "
                                    f"horizontal minimum radar minimum + {float(form.get('extent_nm', 1.0)):.0f} NM"}
            ac.military = True
        rv_default = perf.rvsm_default and ac.formation is None
        ac.rvsm_approved = bool(a.get("rvsm", rv_default))
        if ac.formation is not None and ac.rvsm_approved:
            raise ScenarioError(f"{cs}: formation flights are not RVSM approved")
        ac.rvsm_status = "approved" if ac.rvsm_approved else "non_approved"
        ac.equipment = str(a.get("equipment") or (DEFAULT_EQUIPMENT["military"] if ac.military and not ac.rvsm_approved
                                                  else DEFAULT_EQUIPMENT["rvsm" if ac.rvsm_approved else "non_rvsm"]))
        if ac.rvsm_approved and "W" not in ac.equipment.split("/")[0]:
            raise ScenarioError(f"{cs}: RVSM approved but equipment {ac.equipment} has no 'W'")
        ok, why = perf.level_available(fl, *ac.speed_target(fl * 100.0), mass)
        if not ok and fl * 100 > perf.ceiling_ft:
            raise ScenarioError(f"{cs}: {why}")
        tas = perf.tas_for(*ac.speed_target(fl * 100.0), fl * 100.0)
        pts = [waypoints[w] for w in route]
        brg = bearing(pts[0], pts[-1])
        ac.southbound = str(a.get("direction", "southbound" if 90.0 <= brg < 270.0 else "northbound")) == "southbound"
        if start_name:
            # the aircraft is at its start point at start.time and flies the route from there
            full = [waypoints[w] for w in names]
            entry = float(start["time"]) + _route_time_s(full, polyline_length(full[:idx_entry + 1]), tas, wind, fl)
        elif "entry_time" in a:
            entry = float(a["entry_time"])
        elif "reach" in a:
            r = a["reach"]
            full = [waypoints[w] for w in names]          # whole filed route, upstream and downstream included
            if "waypoint" in r:
                wn = r["waypoint"].upper()
                if wn not in names:
                    raise ScenarioError(f"{cs}: reach waypoint {wn} is not on its route")
                target = waypoints[wn]
            else:
                target = asp.resolve_point(r["point"])
            along, off, _ = project_on_polyline(full, target)
            if off > 1.0:
                raise ScenarioError(f"{cs}: reach point is {off:.1f} NM off its route")
            along_entry = polyline_length(full[:idx_entry + 1])
            entry = float(r["time"]) - (_route_time_s(full, along, tas, wind, fl)
                                        - _route_time_s(full, along_entry, tas, wind, fl))
        else:
            raise ScenarioError(f"{cs}: needs 'entry_time' or 'reach'")
        # ---- appear before the entry waypoint (previous sector's traffic), along the upstream route if any
        pre_nm = float(a.get("pre_entry_nm", pre_entry_nm))
        if start_name:
            pre_name, pre_pt, upstream = start_name, waypoints[start_name], names[1:idx_entry]
        else:
            pre_pt, upstream = _place_pre_entry(names, waypoints, idx_entry, pre_nm)
            pre_name = f"@{cs}"
            waypoints[pre_name] = pre_pt
        head = [pre_pt] + [waypoints[w] for w in upstream] + ([pts[0]] if route[0] != pre_name else [])
        ext_pts = head + pts[1:]
        t_pre = _route_time_s(head, polyline_length(head), tas, wind, fl)
        spawn = entry - t_pre                       # entry = planned time at the entry waypoint
        start_along = 0.0                           # measured along the extended route (from the pre-entry point)
        if spawn < 0:
            lo, hi = 0.0, polyline_length(ext_pts)
            for _ in range(40):
                mid = (lo + hi) / 2
                if _route_time_s(ext_pts, mid, tas, wind, fl) < -spawn:
                    lo = mid
                else:
                    hi = mid
            start_along = lo
            if start_along >= polyline_length(ext_pts) - 1:
                raise ScenarioError(f"{cs}: would already have left the sector at t=0")
            spawn = 0.0
        head_len = polyline_length(head)
        ac.route = ([pre_name] + upstream if route[0] != pre_name else []) + route
        ac.entry_time = spawn
        ac.boundary_time = entry
        ac.entry_wp = route[0]
        ac.checkin_nm = float(a.get("checkin_nm", checkin_nm))
        ac.start_along_nm = start_along
        ac.planned_exit = route[-1]
        ac.planned_length_nm = polyline_length(pts) - max(0.0, start_along - head_len)
        dest_fp = str(a.get("dest") or "").upper()
        if (route[-1] in seqfixes and a.get("arrival", True)
                and (not dest_fp or dest_fp == seqfixes[route[-1]].aerodrome)):   # arrival behind the fix
            ac.destination = seqfixes[route[-1]].aerodrome
            ac.arr = {"fix": route[-1]}
        dest = str(a.get("dest") or ac.destination or "ZZZZ").upper()
        spd_mode, spd_val = ac.speed_target(fl * 100.0)
        ac.flight_plan = {
            "callsign": cs, "aircraft_type": perf.icao, "wake": _wake(perf),
            "flight_rules": "I", "flight_type": "M" if ac.military else "S",
            "departure": str(a.get("dep") or "ZZZZ").upper(), "destination": dest,
            "cruise": f"F{fl:03d}", "speed": (f"M{int(round(spd_val * 100)):03d}" if spd_mode == "mach"
                                              else f"N{int(round(tas)):04d}"),
            "route": asp.route_string(filed + [w.upper() for w in a.get("route_after", [])]),
            "equipment": ac.equipment, "rvsm": "approved (W)" if ac.rvsm_approved else "non-RVSM",
            "formation": ac.formation["types"] if ac.formation else None,
            "remarks": str(a.get("remarks", "")) or ("STS/STATE" if ac.military else ""),
        }
        ac.route_after = after_exit
        ac.route_before = [w for w in filed[:filed.index(route[0])] if w in waypoints] if route[0] in filed else []
        aircraft.append(ac)

    areas: List[Area] = []
    for a in scn.get("areas", []):
        kind = a["kind"].lower()
        if kind not in AREA_KINDS:
            raise ScenarioError(f"area {a.get('id')}: kind must be one of {list(AREA_KINDS)}")
        area = Area(id=str(a["id"]), name=a.get("name", a["id"]), kind=kind,
                    lower_fl=int(a.get("lower_fl", 0)), upper_fl=int(a.get("upper_fl", 999)),
                    active=bool(a.get("active", False)),
                    schedule=sorted([(float(e["time"]), bool(e["active"])) for e in a.get("schedule", [])]))
        if "circle" in a:
            area.circle = (asp.resolve_point(a["circle"]["center"]), float(a["circle"]["radius_nm"]))
        elif "polygon" in a:
            area.polygon = [asp.resolve_point(p) for p in a["polygon"]]
            if len(area.polygon) < 3:
                raise ScenarioError(f"area {area.id}: polygon needs 3+ points")
        else:
            raise ScenarioError(f"area {area.id}: needs 'polygon' or 'circle'")
        areas.append(area)

    requests = []
    for r in scn.get("requests", []):
        cs = str(r["callsign"]).upper()
        if cs not in seen:
            raise ScenarioError(f"request for unknown callsign {cs}")
        rtype = r["type"].lower()
        if rtype not in ("altitude", "direct", "speed", "weather_deviation", "descent"):
            raise ScenarioError(f"request type must be altitude|direct|speed|weather_deviation|descent, got {rtype}")
        val = r.get("value")
        if rtype == "descent":
            # "request descent": optional level the pilot would like (default: LoA level, or about 4000 ft lower)
            val = None if val in (None, "") else int(val)
        elif rtype == "weather_deviation":
            val = dict(val or {})
            if str(val.get("side", "either")).lower() not in ("left", "right", "either"):
                raise ScenarioError(f"{cs}: weather deviation side must be left|right|either")
            if val.get("then_direct"):
                val["then_direct"] = str(val["then_direct"]).upper()
                if val["then_direct"] not in waypoints:
                    raise ScenarioError(f"{cs}: weather deviation then_direct {val['then_direct']} is not a known point")
            if "heading" in val and not (1 <= int(val["heading"]) <= 360):
                raise ScenarioError(f"{cs}: weather deviation heading must be 001-360")
            if "offset_deg" in val and not (5 <= float(val["offset_deg"]) <= 90):
                raise ScenarioError(f"{cs}: weather deviation offset must be 5-90 degrees")
            if "distance_nm" in val and not (3 <= float(val["distance_nm"]) <= 200):
                raise ScenarioError(f"{cs}: weather deviation distance must be 3-200 NM")
        elif rtype == "direct":
            val = str(val).upper()
        elif rtype == "altitude":
            val = int(val)
        else:
            val = round(float(val), 2) if float(val) < 2 else int(val)
        requests.append({"time": float(r["time"]), "callsign": cs, "type": rtype, "value": val,
                         "reason": r.get("reason", "")})

    events = []
    for e in scn.get("events", []):
        cs = str(e["callsign"]).upper()
        if cs not in seen:
            raise ScenarioError(f"event for unknown callsign {cs}")
        et = e["type"].lower()
        if et not in EVENT_TYPES:
            raise ScenarioError(f"event type must be one of {EVENT_TYPES}, got {et}")
        events.append({"time": float(e["time"]), "callsign": cs, "type": et, "reason": e.get("reason", "equipment")})

    meta = public_view(scn)
    w = World(meta=meta, env=env, aircraft=aircraft, areas=areas, requests=requests,
              duration_s=float(scn.get("duration_s", 1800)), pilot_delay_s=float(scn.get("pilot_delay_s", 4)),
              lower_fl=lower_fl, upper_fl=upper_fl, aerodromes=aerodromes, seq=seqfixes, events=events)
    w.persist_s = float(scn.get("post_exit_display_s", 240.0))
    w.auto_assume = bool(scn.get("auto_assume", False))
    # horizontal radar separation: the scenario's own, else the airspace's (Agadir CTA 7 NM), else 5 NM
    sep = scn.get("separation") or {}
    w.sep_h = float(sep.get("horizontal_nm") or (asp.notes or {}).get("separation_horizontal_nm") or 5.0)
    if w.sep_h < 3 or w.sep_h > 20:
        raise ScenarioError(f"separation.horizontal_nm {w.sep_h} is out of range (3-20 NM)")
    return w
