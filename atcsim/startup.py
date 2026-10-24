"""Start-up clearances: before each departure from an aerodrome under the sectors being worked (North, Central, South,
East...; but GMMN and GMAD, whose departures are "silent": released by agreement), its approach asks the ACC sector above for a
start-up clearance - by phone. Sectors with no aerodrome under them (West, Oceanic) get no request.

The request comes LEAD_S before the planned take-off (the time to start up and taxi). The sector answers with the
point where the aircraft will leave the TMA and the level it is cleared to (or refuses: approach asks again
RETRY_S later). Until it is cleared the aircraft does not take off; once cleared it climbs to that level on its way
to that point. The clearance can be changed until the aircraft takes off.

Requests from the same aerodrome due close together (GROUP_S) come in one call. Who plays which side:
* solo radar: the phone rings on the controller's radar; picked up, the window shows the call's flights with the
  suggested point and level, to approve (as they are or changed) or refuse;
* with a pseudo-pilot: the request is a task on the pseudo-pilot's seat - call the controller by phone, then enter
  the clearance received (point, level) or the refusal;
* in both: POST /sessions/{id}/startup/answer {callsigns, approve, point, level}.

Scenario: "startup_requests": true (default) | false, "startup_exempt": ["GMMN", "GMAD"] (default), and per aircraft
"startup": false | {"request_s": t}.
"""
from __future__ import annotations

from typing import Dict, List, Optional

LEAD_S = 600.0            # the request comes this long before the planned take-off
GROUP_S = 240.0           # requests from one aerodrome due within this are made in one call
RETRY_S = 300.0           # refused: asked again this much later
TAXI_S = 180.0            # cleared: the earliest take-off is this much after the clearance (start-up, taxi)


def _cfg(w) -> dict:
    return getattr(w, "startup_cfg", None) or {}


def _recs(w) -> Dict[str, dict]:
    if not hasattr(w, "startups"):
        w.startups = {}
        cfg = _cfg(w)
        if cfg.get("enabled", True):
            exempt = {str(x).upper() for x in cfg.get("exempt", ["GMMN", "GMAD"])}
            per = cfg.get("per", {})
            from .approach import _inside_ours
            ours = {k: _inside_ours(w, ad) for k, ad in w.aerodromes.items()}
            for ac in w.aircraft.values():
                s = getattr(ac, "sid", None)
                if not s or ac.status != "scheduled" or getattr(ac, "spawn_alt_ft", None) is None:
                    continue
                spec = per.get(ac.callsign)
                if not ours.get(s["aerodrome"]) and not isinstance(spec, dict):
                    continue                    # an aerodrome not under the sectors worked: not asked of us
                if spec is False or (s["aerodrome"] in exempt and not isinstance(spec, dict)):
                    continue
                t_req = float(spec["request_s"]) if isinstance(spec, dict) and "request_s" in spec else \
                    max(0.0, ac.entry_time - LEAD_S)
                w.startups[ac.callsign] = {
                    "callsign": ac.callsign, "aerodrome": s["aerodrome"], "runway": s.get("runway"), "sid": s.get("name"),
                    "type": ac.icao, "dest": (ac.flight_plan or {}).get("destination"), "t_req": t_req,
                    "t_takeoff": ac.entry_time, "state": "waiting", "group": None,
                    "point": _suggested_point(w, ac), "points": _points(w, ac), "level": _suggested_level(ac),
                    "cleared_point": None, "cleared_level": None, "t_asked": None, "t_answer": None, "by": None}
    return w.startups


def _suggested_point(w, ac) -> str:
    """Where it leaves the TMA: its SID's end (or the first point of its route after the SID)."""
    ad = w.aerodromes.get(ac.sid["aerodrome"])
    sd = next((x for x in (ad.sids if ad else []) if x["name"] == ac.sid.get("name")), None)
    if sd and sd.get("end"):
        return sd["end"]
    named = [n for n in ac.route if not n.startswith("@")]
    return named[0] if named else ac.entry_wp


def _points(w, ac) -> List[str]:
    """Points the clearance can name: its SID's end and the route points up to our entry."""
    out = [_suggested_point(w, ac)]
    for n in ac.route:
        if not n.startswith("@") and n not in out:
            out.append(n)
        if n == ac.entry_wp:
            break
    return out


def _suggested_level(ac) -> int:
    v = getattr(ac, "initial_cfl", None) or ac.fl
    return int(v)


def due(w) -> List[dict]:
    """The requests being made now (asked, not yet answered)."""
    return [r for r in _recs(w).values() if r["state"] == "requested"]


def update(w) -> None:
    recs = _recs(w)
    if not recs:
        return
    t = w.t
    # requests falling due: with the others of that aerodrome due soon, in one call
    for r in sorted(recs.values(), key=lambda r: r["t_req"]):
        if r["state"] != "waiting" or r["t_req"] > t:
            continue
        ac = w.aircraft.get(r["callsign"])
        if ac is None or ac.status != "scheduled":
            r["state"] = "gone"
            continue
        gid = f"{r['aerodrome']}-{int(t)}"
        group = [x for x in recs.values() if x["state"] == "waiting" and x["aerodrome"] == r["aerodrome"]
                 and x["t_req"] <= t + GROUP_S]
        for x in group:
            x["state"], x["group"], x["t_asked"] = "requested", gid, t
        names = ", ".join(x["callsign"] for x in group)
        w._event("startup_request", aerodrome=r["aerodrome"], callsigns=[x["callsign"] for x in group], group=gid,
                 text=f"{r['aerodrome']} approach requests start-up for {names}")
    # an airborne aircraft: the record is closed
    for r in recs.values():
        if r["state"] in ("cleared", "requested", "refused") and r["callsign"] in w.aircraft \
                and w.aircraft[r["callsign"]].status != "scheduled":
            r["state"] = "airborne" if r["state"] == "cleared" else r["state"]


def may_take_off(w, ac):
    """(False, why) while its start-up clearance is missing (tma.may_take_off)."""
    r = _recs(w).get(ac.callsign)
    if r is None or r["state"] in ("gone", "airborne"):
        return True, ""
    if r["state"] != "cleared":
        return False, "awaiting start-up clearance"
    if w.t < (r["t_answer"] or 0) + TAXI_S:
        return False, "starting up, taxiing"
    return True, ""


def answer(w, callsigns, approve: bool, point: Optional[str] = None, level: Optional[int] = None,
           by: str = "") -> dict:
    """The sector's answer (or a change of a clearance before take-off) for one or more flights."""
    recs = _recs(w)
    done, errors = [], []
    for cs in ([callsigns] if isinstance(callsigns, str) else list(callsigns or [])):
        cs = str(cs).upper()
        r = recs.get(cs)
        if r is None:
            errors.append(f"{cs}: no start-up request")
            continue
        ac = w.aircraft.get(cs)
        if ac is None or ac.status != "scheduled":
            errors.append(f"{cs}: already airborne")
            continue
        if r["state"] not in ("requested", "cleared", "refused"):
            errors.append(f"{cs}: no request yet")
            continue
        if not approve:
            r.update(state="waiting", t_req=w.t + RETRY_S, t_answer=w.t, by=by, cleared_point=None, cleared_level=None)
            w._event("startup_refused", callsign=cs, aerodrome=r["aerodrome"],
                     text=f"{cs} start-up refused - {r['aerodrome']} approach asks again in {int(RETRY_S // 60)} min")
            done.append(cs)
            continue
        pt = str(point or r["point"]).upper()
        if pt not in w.waypoints:
            errors.append(f"{cs}: unknown point {pt}")
            continue
        lvl = int(level) if level not in (None, "") else r["level"]
        if not (20 <= lvl <= 450):
            errors.append(f"{cs}: level {lvl}?")
            continue
        changed = r["state"] == "cleared"
        r.update(state="cleared", cleared_point=pt, cleared_level=lvl, t_answer=r["t_answer"] if changed else w.t, by=by)
        _apply(w, ac, pt, lvl)
        w._event("startup_cleared", callsign=cs, aerodrome=r["aerodrome"], point=pt, fl=lvl, changed=changed,
                 text=f"{cs} start-up {'clearance changed' if changed else 'approved'}: {pt} F{lvl:03d}")
        done.append(cs)
    if errors and not done:
        raise ValueError("; ".join(errors))
    return {"done": done, "errors": errors}


def _apply(w, ac, point: str, lvl: int) -> None:
    """The clearance: approach climbs it to that level; a point off its route becomes its way out of the TMA."""
    ac.initial_cfl = lvl
    if point not in ac.route:
        # direct from the end of its SID to the point, then its route from the point after the one nearest it
        from .geometry import dist
        names = list(ac.route)
        sid_end = next((i for i, n in enumerate(names) if not n.startswith("@")), 0)
        p = w.waypoints[point]
        k = min(range(sid_end, len(names)), key=lambda i: dist(w.waypoints.get(names[i], (1e9, 1e9)), p))
        ac.route = names[:sid_end] + [point] + names[k + 1:] if k + 1 < len(names) else names[:sid_end] + [point] + names[k:]


def view(w) -> List[dict]:
    """For the screens: the requests being made, and the clearances given, until take-off."""
    out = []
    for r in _recs(w).values():
        if r["state"] in ("requested", "cleared", "refused") or (r["state"] == "waiting" and r["t_answer"] is not None):
            ac = w.aircraft.get(r["callsign"])
            if ac is None or ac.status != "scheduled":
                continue
            out.append({k: r[k] for k in ("callsign", "aerodrome", "runway", "sid", "type", "dest", "state", "group",
                                          "point", "points", "level", "cleared_point", "cleared_level", "t_asked",
                                          "t_answer")} | {"t_takeoff": round(ac.entry_time)})
    return sorted(out, key=lambda x: (x["t_asked"] or 1e9, x["callsign"]))


def metrics(w) -> dict:
    recs = list(_recs(w).values())
    asked = [r for r in recs if r["t_asked"] is not None]
    answered = [r for r in asked if r["t_answer"] is not None]
    waits = [r["t_answer"] - r["t_asked"] for r in answered if r["t_answer"] >= r["t_asked"]]
    return {"requests": len(asked), "answered": len(answered),
            "cleared": sum(1 for r in recs if r["state"] in ("cleared", "airborne")),
            "pending": sum(1 for r in recs if r["state"] == "requested"),
            "mean_answer_s": round(sum(waits) / len(waits), 1) if waits else None}
