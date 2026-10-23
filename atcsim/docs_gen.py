"""Generated parts of the documentation: the airspace / aircraft reference tables appended to the scenario JSON
page, the same data as JSON, and the JSON Schema of a scenario file.

Generated from the live data (airspace files, type catalogue), so the documentation never drifts from what the
scenario builder accepts.
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, List

FRA = {"E": "entry only", "X": "exit only", "EX": "entry / exit", "I": "intermediate (FRA)"}


def _sectors_near(asp, p, r: float = 1.5) -> List[str]:
    out = set()
    for k in range(12):
        a = 2 * math.pi * k / 12
        s = asp.sector_of((p[0] + r * math.cos(a), p[1] + r * math.sin(a)))
        if s:
            out.add(s)
    s = asp.sector_of(p)
    if s:
        out.add(s)
    return sorted(out)


@lru_cache(maxsize=1)
def reference() -> Dict:
    """Machine-readable reference: airspaces (points, routes, sectors, capacities, aerodromes) and aircraft types."""
    from . import airspace as asp_mod, performance
    out = {"airspaces": {}, "aircraft_types": []}
    for name in ("training", "agadir"):
        asp = asp_mod.get(name)
        pts = []
        for n, p in sorted(asp.waypoints.items()):
            if n.startswith("@"):
                continue
            info = asp.point_info.get(n, {})
            ext = bool(info.get("external"))
            fra = str(info.get("fra", ""))
            boundary = n in asp.boundary_points
            if ext:
                role = "external (outside the CTA: flight plans, directs)"
            elif fra in FRA:
                role = FRA[fra]
            elif boundary:
                role = "transfer point (boundary)"
            else:
                role = "inside"
            d = {"name": n, "x": round(p[0], 2), "y": round(p[1], 2), "role": role,
                 "sectors": [] if ext else _sectors_near(asp, p), "boundary": boundary}
            if info.get("coord"):
                d["coord"] = info["coord"]
            if info.get("region"):
                d["region"] = info["region"]
            if info.get("role") and not ext:
                d["airways"] = info["role"]
            pts.append(d)
        notes = asp.notes or {}
        a = {"id": asp.id, "name": asp.name, "levels": [asp.lower_fl, asp.upper_fl],
             "separation_horizontal_nm": notes.get("separation_horizontal_nm") or 5,
             "sectors": [{"id": s.id, "name": s.name, "freq": getattr(s, "freq", None)} for s in asp.sectors],
             "capacity_per_hour": notes.get("capacity_per_hour") or {},
             "points": pts,
             "airways": {k: {"points": v["points"], "one_way": bool(v.get("one_way")), "note": v.get("note", "")}
                         for k, v in (asp.airways or {}).items()},
             "rad_routes_one_way": notes.get("rad_routes") or [],
             "standard_routes": notes.get("standard_routes") or [],
             "published_routes": [list(r) for r in (asp.routes or [])] if asp.id == "training" else [],
             "aerodromes": asp.default_aerodromes or [],
             "sequencing": asp.default_sequencing or []}
        if asp.id == "training":
            from .arrivals import DEFAULT_AERODROMES, DEFAULT_SEQUENCING
            a["aerodromes"], a["sequencing"] = DEFAULT_AERODROMES, DEFAULT_SEQUENCING
        out["airspaces"][name] = a
    for t in performance.catalogue():
        p = performance.get(t["icao"])
        m = p.oew + performance.DEFAULT_LOAD * (p.mtow - p.oew)
        try:
            mx = int(p.max_fl("mach", t["typical_cruise_mach"], m))
        except Exception:                               # pragma: no cover - defensive
            mx = t.get("ceiling_fl")
        out["aircraft_types"].append({
            "icao": t["icao"], "name": t["name"], "category": t["category"], "rvsm_by_default": t["rvsm_by_default"],
            "typical_cruise_mach": round(t["typical_cruise_mach"], 2), "max_fl_mid_weight": mx, "ceiling_fl": t["ceiling_fl"],
            "mtow_kg": t["mtow_kg"], "oew_kg": t["oew_kg"], "assignable_mach": t["assignable_mach"]})
    return out


def _md_table(head: List[str], rows: List[List]) -> str:
    esc = lambda v: str(v if v is not None else "").replace("|", "\\|")
    return "| " + " | ".join(head) + " |\n|" + "---|" * len(head) + "\n" + \
        "\n".join("| " + " | ".join(esc(v) for v in r) + " |" for r in rows) + "\n"


@lru_cache(maxsize=1)
def reference_md() -> str:
    ref = reference()
    ag, tr = ref["airspaces"]["agadir"], ref["airspaces"]["training"]
    s = ["## Appendix: reference data (generated)\n",
         "This appendix is generated from the simulator's own data. Point names, routes and aircraft types that are "
         "not listed here are refused. The same data is available as JSON at "
         "[/api/help/reference.json](/api/help/reference.json).\n"]
    # ---------------------------------------------------------------- Agadir
    s.append("### Agadir CTA (`\"airspace\": \"agadir\"`)\n")
    s.append(f"Vertical limits FL{ag['levels'][0]}–FL{ag['levels'][1]}. Horizontal separation "
             f"{ag['separation_horizontal_nm']} NM. Coordinates: NM east (x) and north (y) of 33°N 11°30'W "
             "(Lambert conformal projection).\n")
    s.append("**Sectors** (`control`) and declared capacities (flights per hour):\n")
    cap = ag["capacity_per_hour"]
    s.append(_md_table(["id", "name", "frequency", "capacity alone"],
                       [[x["id"], x["name"], x["freq"] or "", cap.get(x["id"], "")] for x in ag["sectors"]]))
    names = {"WN+WS": "WW", "ON+OS": "OCE", "ON+OS+WN+WS": "WW-OCE"}
    s.append("\nBand-boxed configurations: " + "; ".join(
        f"{names.get(k, k)} = `\"control\": {[x for x in k.split('+')]}` → {v}/h".replace("'", '"')
        for k, v in cap.items() if "+" in k) + ".\n")
    inside = [p for p in ag["points"] if p["role"] != "external (outside the CTA: flight plans, directs)"]
    s.append("\n**CTA points** — the names to use in routes. *Role* says whether a route may start (entry) or end "
             "(exit) there.\n")
    s.append(_md_table(["name", "role", "sector(s)", "x", "y", "AIP coordinates", "airways / note"],
                       [[p["name"], p["role"], "+".join(p["sectors"]), p["x"], p["y"], p.get("coord", ""),
                         p.get("airways", "")] for p in inside]))
    ext = [p for p in ag["points"] if p not in inside]
    s.append("\n**Points outside the CTA** (adjacent units). They may appear before the entry or after the exit of a "
             "route (flight plan), or as directs; the simulation flies only the part inside the controlled "
             "sectors.\n")
    s.append(_md_table(["name", "region", "x", "y", "AIP coordinates"],
                       [[p["name"], p.get("region", ""), p["x"], p["y"], p.get("coord", "")] for p in ext]))
    s.append("\n**Airways:**\n")
    s.append(_md_table(["airway", "points (in order)", "direction"],
                       [[k, " ".join(v["points"]), ("one way, in the order listed" + (f" ({v['note']})" if v['note'] else ""))
                         if v["one_way"] else "both ways"] for k, v in ag["airways"].items()]))
    s.append("\n**RAD routings** (one way, in the order listed):\n\n" +
             "\n".join(f"- `{' '.join(r)}`" for r in ag["rad_routes_one_way"]) + "\n")
    s.append("\n**Standard routes** (one way):\n\n" + "\n".join(f"- `{' '.join(r)}`" for r in ag["standard_routes"]) + "\n")
    s.append("\nFree routing between CTA points is allowed (FRA): a route does not have to follow an airway, but it "
             "must not fly a one-way airway / RAD / standard route backwards, must start at an entry point (or "
             "outside the CTA) and end at an exit point (or outside).\n")
    s.append("\n**Arrival aerodromes and sequencing fixes** (defaults; a route ending at a sequencing fix is an "
             "arrival there unless `\"arrival\": false`):\n")
    s.append(_md_table(["fix", "aerodrome", "level at the fix", "spacing"],
                       [[q["fix"], q["aerodrome"], f"FL{q.get('cross_at_fl') or q.get('cross_at_or_below_fl')}",
                         f"{q.get('spacing_nm', 10)} NM"] for q in ag["sequencing"]]))
    # ---------------------------------------------------------------- training
    s.append("\n### Training sector (`\"airspace\": \"training\"`, the default)\n")
    s.append(f"A 120 × 120 NM square, FL{tr['levels'][0]}–FL{tr['levels'][1]}, coordinates in NM from its centre. "
             f"Horizontal separation {tr['separation_horizontal_nm']} NM.\n")
    s.append(_md_table(["point", "x", "y"], [[p["name"], p["x"], p["y"]] for p in tr["points"]]))
    s.append("\nRoutes join any two points on different edges (N1–S3, W2–E1…); E1 and S4 are sequencing fixes "
             "(Eastfield EFLD, Southport SPRT: FL250, 10 NM in trail). Add interior points with "
             "`extra_waypoints`.\n")
    # ---------------------------------------------------------------- types
    s.append("\n### Aircraft types\n")
    s.append("`max FL` is the highest level the type can reach at mid weight (`load` 0.5) at its cruise Mach, ISA; "
             "lighter (`load` 0.3) goes higher, heavier lower, hot days lower. File levels at or below it.\n")
    s.append(_md_table(["type", "name", "category", "RVSM by default", "cruise Mach", "max FL (mid weight)", "ceiling"],
                       [[t["icao"], t["name"], t["category"], "yes" if t["rvsm_by_default"] else "no",
                         t["typical_cruise_mach"], t["max_fl_mid_weight"], t["ceiling_fl"]]
                        for t in ref["aircraft_types"]]))
    return "\n".join(s)


# ------------------------------------------------------------------------------------------- JSON Schema
_POINT = {"description": "A position: [x, y] in NM, {\"lat\": .., \"lon\": ..} in degrees, an AIP string such as "
                         "\"320500N 0145000W\", or a point name",
          "anyOf": [{"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                    {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}},
                     "required": ["lat", "lon"]},
                    {"type": "string"}]}
_TIME = {"type": "number", "description": "seconds from the start of the scenario (T+0)"}

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "atcsim-scenario",
    "title": "ATC SIM scenario",
    "description": "One scenario file (scenarios/<name>.json). See /help/scenario-json for every rule.",
    "type": "object",
    "required": ["name", "aircraft"],
    "properties": {
        "name": {"type": "string", "pattern": "^[a-z0-9_-]{2,64}$", "description": "file name"},
        "title": {"type": "string"},
        "author": {"type": "string"},
        "complexity": {"type": "integer", "minimum": 1, "maximum": 5},
        "density": {"type": "integer", "minimum": 1, "maximum": 5},
        "traffic_load": {"type": "object", "description": "written by the server when saving; do not write it"},
        "description": {"type": "string", "description": "briefing shown to the controller"},
        "design_notes": {"type": "string", "description": "the traps, hidden from the controller and agents"},
        "airspace": {"enum": ["training", "agadir"], "default": "training"},
        "control": {"type": "array", "items": {"enum": ["WN", "WS", "ON", "OS", "N", "E", "C", "SS"]}, "minItems": 1,
                    "description": "agadir: sectors controlled, all in one control area: CTA Agadir WN WS ON OS (default all four) or CTA Casablanca N E C SS"},
        "duration_s": {"type": "number", "minimum": 300, "default": 1800},
        "pilot_delay_s": {"type": "number", "minimum": 0, "default": 4},
        "start_utc": {"type": "string", "pattern": "^[0-9]{1,2}:?[0-9]{2}$", "default": "12:00"},
        "magnetic_variation": {"type": "number", "description": "degrees, negative = west"},
        "separation": {"type": "object", "properties": {"horizontal_nm": {"type": "number", "minimum": 3, "maximum": 20},
                                                        "exit_spacing_nm": {"type": "number", "minimum": 0, "maximum": 40}}},
        "oldi": {"type": "object", "properties": {"abi_s": {"type": "number"}, "act_s": {"type": "number"},
                                                  "rev_limit_s": {"type": "number"}}},
        "auto_assume": {"type": "boolean", "default": False},
        "auto_routes": {"type": "boolean", "default": True},
        "pre_entry_nm": {"type": "number", "default": 30},
        "checkin_nm": {"type": "number", "default": 10},
        "post_exit_display_s": {"type": "number", "default": 240},
        "sector": {"type": "object", "properties": {"lower_fl": {"type": "integer"}, "upper_fl": {"type": "integer"}}},
        "extra_waypoints": {"type": "object", "additionalProperties": _POINT,
                            "description": "extra named points usable in routes: {\"NAME\": position}"},
        "weather": {"type": "object", "properties": {
            "seed": {"type": "integer"},
            "isa_deviation_c": {"type": "number"},
            "temperature": {"type": "object", "properties": {"surface_c": {"type": "number"},
                                                             "tropopause_fl": {"type": "number"},
                                                             "lapse_c_per_kft": {"type": "number"}}},
            "wind": {"type": "object", "properties": {
                "climatology": {"type": "object", "required": ["month"], "description": "ERA5 monthly climatology "
                                "(agadir): the month's mean wind and temperature field, varying with position and level",
                                "properties": {"month": {"type": "integer", "minimum": 1, "maximum": 12},
                                               "mode": {"type": "number", "description": "leading variability mode, "
                                                        "in standard deviations (+1 strong jet, -1 weak)"},
                                               "scale": {"type": "number"}, "file": {"type": "string"},
                                               "variability": {"type": "number", "minimum": 0, "maximum": 3,
                                                               "description": "the day around the mean (default 1, 0 = the mean)"}}},
                "profile": {"type": "array", "items": {"type": "object", "required": ["fl", "dir", "kt"],
                                                       "properties": {"fl": {"type": "number"},
                                                                      "dir": {"type": "number", "description": "FROM, degrees"},
                                                                      "kt": {"type": "number"}}}},
                "jet": {"type": "object", "properties": {k: {"type": ["number", "array"]} for k in (
                    "core_fl", "dir", "kt", "axis_point", "half_width_nm", "depth_below_ft", "depth_above_ft",
                    "streak_length_nm", "streak_speed_kt", "meander_amp_nm", "meander_wavelength_nm")}},
                "noise_kt": {"type": "number"}, "noise_scale_nm": {"type": "number"}}},
            "convection": {"description": "one pattern or a list",
                           "anyOf": [{"$ref": "#/$defs/convection"},
                                     {"type": "array", "items": {"$ref": "#/$defs/convection"}}]},
            "cells": {"type": "array", "items": {"type": "object", "required": ["x", "y"], "properties": {
                "id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "at_time": _TIME,
                "top_fl": {"type": "number"}, "dbz": {"type": "number"}, "radius_nm": {"type": "number"},
                "lifetime_s": {"type": "number"}, "age_s": {"type": "number"}, "aspect": {"type": "number"},
                "orient_deg": {"type": "number"},
                "motion": {"type": "object", "properties": {"toward": {"type": "number"}, "kt": {"type": "number"}}}}}},
            "stratiform": {"type": "array", "items": {"type": "object", "required": ["center"], "properties": {
                "center": {"type": "array"}, "radius_nm": {"type": "array"}, "orient_deg": {"type": "number"},
                "dbz": {"type": "number"}, "motion": {"type": "object"}}}},
            "pilot": {"type": "object", "properties": {k: {"type": "number"} for k in (
                "hazard_dbz", "margin_nm", "severe_dbz", "severe_margin_nm", "request_range_nm",
                "self_deviation_range_nm", "radar_range_nm")}}}},
        "areas": {"type": "array", "items": {"type": "object", "required": ["id", "kind"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "kind": {"enum": ["danger", "restricted", "prohibited"]},
            "polygon": {"type": "array", "items": _POINT, "minItems": 3},
            "circle": {"type": "object", "required": ["center", "radius_nm"],
                       "properties": {"center": _POINT, "radius_nm": {"type": "number"}}},
            "lower_fl": {"type": "integer", "default": 0}, "upper_fl": {"type": "integer", "default": 999},
            "active": {"type": "boolean", "default": False},
            "schedule": {"type": "array", "items": {"type": "object", "required": ["time", "active"],
                                                    "properties": {"time": _TIME, "active": {"type": "boolean"}}}}}}},
        "published_areas": {"description": "Agadir: the AIP areas (ENR 5.1 GMP / GMR / GMD), loaded into every scenario, "
                                           "prohibited ones active. false leaves them out; an object switches some.",
                            "oneOf": [{"const": False}, {"type": "object", "additionalProperties": {"oneOf": [
                                {"type": "boolean"},
                                {"type": "object", "properties": {"active": {"type": "boolean"}, "schedule": {
                                    "type": "array", "items": {"type": "object", "required": ["time", "active"],
                                                               "properties": {"time": _TIME,
                                                                              "active": {"type": "boolean"}}}}}}]}}]},
        "aircraft": {"type": "array", "items": {"$ref": "#/$defs/aircraft"}},
        "requests": {"type": "array", "items": {"type": "object", "required": ["time", "callsign", "type"], "properties": {
            "time": _TIME, "callsign": {"type": "string"},
            "type": {"enum": ["altitude", "direct", "speed", "descent", "weather_deviation"]},
            "value": {"description": "altitude: FL (integer); direct: point name; speed: Mach (<2) or IAS kt; "
                                     "descent: optional FL; weather_deviation: object"},
            "reason": {"type": "string", "description": "the pilot's words"}}}},
        "events": {"type": "array", "items": {"type": "object", "required": ["time", "callsign", "type"], "properties": {
            "time": _TIME, "callsign": {"type": "string"}, "type": {"enum": ["rvsm_unable", "rvsm_restored"]},
            "reason": {"type": "string"}}}},
        "aerodromes": {"type": "array", "items": {"type": "object", "required": ["id", "runways"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
            "lat": {"type": "number"}, "lon": {"type": "number"}, "coord": {"type": "string"},
            "elev_ft": {"type": "number"},
            "runways": {"type": "array", "items": {"type": "object", "required": ["id", "heading"]}},
            "runway_in_use": {"type": "string", "default": "auto"},
            "runway_schedule": {"type": "array", "items": {"type": "object", "required": ["time", "runway"]}},
            "final_nm": {"type": "number"}, "outbound_nm": {"type": "number"}}}},
        "sequencing": {"type": "array", "items": {"type": "object", "required": ["fix", "aerodrome"], "properties": {
            "fix": {"type": "string"}, "aerodrome": {"type": "string"}, "spacing_nm": {"type": "number"},
            "cross_at_fl": {"type": "integer"}, "cross_at_or_below_fl": {"type": "integer"},
            "min_fl": {"type": "integer"}, "speed_at_fix_kt": {"type": "number"}}}},
        "expected": {"type": "object", "additionalProperties": {"type": "object", "properties": {
            "los": {"type": "boolean"}, "pairs": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
            "infringement": {"type": "boolean"}, "seq_level": {"type": "boolean"}, "seq_spacing": {"type": "boolean"},
            "rvsm_violation": {"type": "boolean"}, "wx_request": {"type": "string"},
            "allow_left_sector": {"type": "boolean"}}}},
        "solution": {"type": "array", "items": {"type": "object"}},
    },
    "$defs": {
        "convection": {"type": "object", "properties": {
            "regime": {"enum": ["none", "isolated", "scattered", "squall_line", "embedded"]},
            "intensity": {"enum": ["moderate", "strong", "severe"], "default": "strong"},
            "count": {"type": "integer"},
            "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                       "description": "[x0, y0, x1, y1] NM"},
            "line": {"type": "object", "properties": {
                "center": {"type": "array"}, "length_nm": {"type": "number"}, "orient_deg": {"type": "number"},
                "gaps": {"type": "integer"}, "gap_list": {"type": "array"}, "spacing_nm": {"type": "number"}}}}},
        "aircraft": {"type": "object", "required": ["callsign", "type", "route", "fl"], "properties": {
            "callsign": {"type": "string", "pattern": "^[A-Z0-9]{2,8}$"},
            "type": {"type": "string", "description": "ICAO type from the appendix"},
            "route": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                      "description": "point names in flying order (2+, or 1 with start)"},
            "fl": {"type": "integer", "minimum": 50, "maximum": 600},
            "entry_time": _TIME,
            "reach": {"type": "object", "required": ["time"], "properties": {
                "waypoint": {"type": "string"}, "point": _POINT, "time": _TIME}},
            "start": {"type": "object", "required": ["point", "time"], "properties": {"point": _POINT, "time": _TIME}},
            "mach": {"type": "number", "minimum": 0.3, "maximum": 0.95},
            "ias_kt": {"type": "number"},
            "load": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            "mass_kg": {"type": "number"},
            "arrival": {"type": "boolean"},
            "auto_descent_request": {"type": "boolean", "default": True,
                                     "description": "arrival: the pilot asks for descent at its top of descent; false = only "
                                                    "when a scripted 'descent' request says so"},
            "direction": {"enum": ["southbound", "northbound"]},
            "dep": {"type": "string"}, "dest": {"type": "string"}, "rfl": {"type": "integer", "minimum": 100, "maximum": 600}, "cfl": {"type": "integer", "minimum": 50, "maximum": 600, "description": "cleared level at the start (a departure released climbing by approach)"},
            "rvsm": {"type": "boolean"}, "military": {"type": "boolean"},
            "formation": {"anyOf": [{"type": "integer", "minimum": 2},
                                    {"type": "object", "properties": {"size": {"type": "integer", "minimum": 2},
                                                                      "types": {"type": "string"},
                                                                      "extent_nm": {"type": "number"}}}]},
            "equipment": {"type": "string"}, "remarks": {"type": "string"},
            "route_after": {"type": "array", "items": {"type": "string"}},
            "auto_route": {"type": "boolean"},
            "pre_entry_nm": {"type": "number"}, "checkin_nm": {"type": "number"},
            "fpl": {"type": "object", "properties": {k: {"type": ["string", "integer"]} for k in (
                "ssr", "reg", "code", "eobt", "altn", "equipment", "opr", "pob", "rmk", "sts", "pbn", "sel", "dof",
                "type", "dep", "dest")}}},
            "oneOf": [{"required": ["entry_time"]}, {"required": ["reach"]}, {"required": ["start"]}]},
    },
}
