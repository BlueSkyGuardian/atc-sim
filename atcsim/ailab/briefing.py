"""The sector briefing given to the model once per run (with the system prompt, so it goes with every request):
the airspace, the coordinates used in the situations, the sector, the points with their positions, the routes,
the separation rules, the semicircular rule, the level band, what is allowed.

briefing(w) -> text. Positions are in NM on the simulator's grid: x east, y north, the same as in the situations.
"""
from __future__ import annotations

import math
from typing import List


def _r(v) -> str:
    return f"{v:.0f}"


def _poly(pts: List[list], max_pts: int = 40) -> str:
    if len(pts) > max_pts:                       # keep the shape, not every vertex
        step = math.ceil(len(pts) / max_pts)
        pts = pts[::step]
    return " ".join(f"({_r(p[0])},{_r(p[1])})" for p in pts)


def briefing(w) -> str:
    from ..session import minima, sector_description
    d = sector_description(w)
    ctl = set(d.get("controlled_sectors") or [])
    secs = [s for s in d.get("sectors") or [] if s.get("id") in ctl]
    others = [s for s in d.get("sectors") or [] if s.get("id") not in ctl]
    vl = d.get("vertical_limits") or {}
    out = [f"SECTOR BRIEFING — {d.get('name', '')} ({d.get('unit', '')}). You control sector(s) {', '.join(sorted(ctl)) or '-'}, "
           f"FL{vl.get('lower_fl', '')} to FL{vl.get('upper_fl', '')}.",
           "COORDINATES: every position is (x, y) in NM, x towards the east, y towards the north. Headings and tracks are "
           "degrees from north, clockwise (090 = east, 180 = south, 270 = west). The heading from P1 to P2 is "
           "atan2(x2-x1, y2-y1) in degrees; the distance is sqrt(dx^2+dy^2) NM. 1 NM per minute = 60 kt; at 450 kt an "
           "aircraft flies 7.5 NM per minute."]
    for s in secs:
        out.append(f"YOUR SECTOR {s['id']} ({s.get('name', '')}, {s.get('frequency', '')}): boundary {_poly(s.get('polygon') or [])}")
    if others:
        out.append("NEIGHBOUR SECTORS (other controllers): " + "; ".join(f"{s['id']} {s.get('name', '')}" for s in others))
    # points: those inside or near the sectors (the routes go through them)
    bb = d.get("bbox_nm") or [-1e9, -1e9, 1e9, 1e9]
    far = set(d.get("fir_external_points") or [])       # the other control area of the FIR: not listed
    wps = {k: v for k, v in (d.get("waypoints") or {}).items()
           if bb[0] - 80 <= v[0] <= bb[2] + 80 and bb[1] - 80 <= v[1] <= bb[3] + 80 and k not in far}
    out.append("POINTS (name x,y): " + " ".join(f"{k}({_r(v[0])},{_r(v[1])})" for k, v in sorted(wps.items())))
    if d.get("entry_exit"):
        out.append("ENTRY / EXIT: " + str(d["entry_exit"]))
    rts = d.get("standard_routes") or []
    if rts:
        out.append("MAIN ROUTES: " + "; ".join(" ".join(r) for r in rts))
    ext = set(d.get("external_points") or [])
    aw = {}
    for k, v in (d.get("airways") or {}).items():      # the part of each airway in our control area
        run, best = [], []
        for n in v.get("points") or []:
            run = run + [n] if n not in ext else []
            best = run if len(run) > len(best) else best
        if len(best) >= 2:
            aw[k] = {**v, "points": best}
    if aw:
        out.append("AIRWAYS: " + "; ".join(f"{k} {' '.join(v.get('points') or [])}" + (f" ({v['note']})" if v.get("note") else "")
                                             for k, v in aw.items()))
    if d.get("free_route"):
        out.append("DIRECT ROUTINGS: " + str(d["free_route"]))
    m = minima(w)
    out.append("SEPARATION (two aircraft must have at least one of these at all times):\n" + "\n".join("- " + r for r in m["rules"]))
    rv = d.get("rvsm") or {}
    if rv.get("rule"):
        out.append(f"RVSM: FL{rv.get('lower_fl')}-FL{rv.get('upper_fl')}: {rv['rule']}")
    if d.get("semicircular_rule"):
        out.append("SEMICIRCULAR RULE (cruising and exit levels): " + str(d["semicircular_rule"])
                   + " An aircraft must leave your sector at a level of its direction (its exit level, XFL, is given).")
    if d.get("sequencing_note"):
        out.append("ARRIVALS: " + str(d["sequencing_note"]))
    out.append("HOW AIRCRAFT BEHAVE: they fly their route (own navigation) at their cleared level until you instruct them. "
               "A HEADING is flown until you clear them again: an aircraft on a heading never goes back to its route by "
               "itself — give it 'resume' (or a 'direct' to a point of its route) once it is clear of the conflict. "
               "Climbs and descents take time (about 1000-2000 ft per minute at high levels). A level change is the "
               "usual way to solve a conflict; a heading is for when no level works, and needs a plan to resume.")
    return "\n".join(out)
