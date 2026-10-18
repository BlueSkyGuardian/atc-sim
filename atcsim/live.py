"""Live actions during an exercise (instructor and pseudo-pilots, or the solo radar's controller):

* add an aircraft now (a pop-up, or one entering in a few minutes), built like a scenario aircraft;
* declare an emergency: emergency descent (7700, MAYDAY, the aircraft starts down), medical with a diversion
  (PAN PAN, a request direct to the aerodrome), radio failure (7600: no reply on the frequency, the flight plan is
  flown), or a general 7700; and clear it;
* make a pilot ask for something now (level, direct, speed, weather deviation, or a report in free words);
* activate or deactivate an area until its next scheduled change.

Everything is in the world (so a rewind or a restart keeps it) and logged as an event.
"""
from __future__ import annotations

from typing import Optional

from .geometry import dist

EMERGENCIES = {
    "descent": ("7700", "MAYDAY MAYDAY MAYDAY, {cs}, emergency descent, {why}, descending FL{fl:03d}"),
    "medical": ("7700", "PAN PAN PAN PAN PAN PAN, {cs}, medical emergency on board, request diversion to {ad}"),
    "radio": ("7600", "{cs} radio failure (squawk 7600): no reply on the frequency, the flight plan is flown"),
    "general": ("7700", "MAYDAY MAYDAY MAYDAY, {cs}, {why}"),
}


def _aircraft(w, cs: str):
    a = w.aircraft.get(str(cs).upper())
    if a is None or not (a.active or a.outside):
        raise ValueError(f"{str(cs).upper()} is not on the picture")
    return a


def _report(w, cs: str, text: str, kind: str) -> Optional[str]:
    """A transmission the controller must answer (it shows on the pseudo-pilot's To do, the solo radio log)."""
    from .model import Request
    val = text[len(cs) + 1:] if text.startswith(cs + " ") else text
    req = Request(id=f"R{len(w.requests) + 1:03d}", callsign=cs, type="report", value=val, reason="",
                  time=round(w.t, 1), origin="pilot", kind=kind)
    w.requests[req.id] = req
    w._event("pilot_request", request_id=req.id, callsign=cs, request_type="report", value=req.value, reason="",
             text=text, origin="live")
    return req.id


# ------------------------------------------------------------------ aircraft
def add_aircraft(w, spec: dict, by: str = "") -> dict:
    """spec: {"callsign", "type", "fl", "route": [points, entry first], "appear": "now" | seconds before entry,
    optional "dep", "dest", "mach", "rvsm"}. "now": at its first point now; a number: it enters (reaches the first
    point on the boundary) that many seconds from now, appearing before it like any traffic."""
    from . import performance
    from .scenario import ScenarioError, make_aircraft
    cs = str(spec.get("callsign", "")).upper().strip()
    if not cs or not cs.replace("-", "").isalnum():
        raise ValueError("callsign: letters and digits")
    if cs in w.aircraft:
        raise ValueError(f"{cs} is already in the exercise")
    route = [str(p).upper() for p in spec.get("route") or [] if str(p).strip()]
    if len(route) < 2:
        raise ValueError("route: at least two points (entry first)")
    for p in route:
        if p not in w.waypoints:
            raise ValueError(f"unknown point {p}")
    try:
        performance.get(str(spec.get("type") or "A320").upper(), w.env.dT)
    except KeyError:
        raise ValueError(f"unknown aircraft type {spec.get('type')}")
    a = {"callsign": cs, "type": str(spec.get("type") or "A320").upper(), "fl": int(spec.get("fl") or 350),
         "route": route}
    for k in ("dep", "dest", "mach", "rvsm", "equipment"):
        if spec.get(k) not in (None, ""):
            a[k] = spec[k]
    appear = spec.get("appear", "now")
    if appear == "now":
        a["start"] = {"point": route[0], "time": round(w.t + 1.0, 1)}
    else:
        a["entry_time"] = round(w.t + max(60.0, float(appear)), 1)
    ctx = getattr(w, "build_ctx", {}) or {}
    scn = {"auto_routes": ctx.get("auto_routes", True)}
    asp = w.env.airspace
    before = set(w.waypoints)
    try:
        ac = make_aircraft(a, scn, asp, w.waypoints, w.seq, w.aerodromes, w.env.weather, w.env.dT,
                           float(ctx.get("pre_entry_nm", 30.0)), float(ctx.get("checkin_nm", 10.0)),
                           float(ctx.get("start_min", getattr(w, "start_utc_min", 720.0))))
    except ScenarioError as e:
        raise ValueError(str(e))
    # what make_aircraft added to the airspace (start point, generated coordination points): kept with the world
    added = {k: w.waypoints[k] for k in set(w.waypoints) - before}
    w.live_points = {**getattr(w, "live_points", {}), **added}
    w.live_cops = sorted(set(getattr(w, "live_cops", [])) | (set(added) & set(getattr(asp, "cop_points", ()))))
    if a.get("start"):
        ac.entry_time = max(ac.entry_time, w.t)
    ac.mag_var = w.mag_var
    used = {x.flight_plan.get("ssr") for x in w.aircraft.values()}
    code = ac.flight_plan.get("ssr", "2000")
    while code in used or code in ("2000", "7000", "7500", "7600", "7700", "1200"):
        code = f"{(int(code, 8) + 1) % 0o10000:04o}"
    if code != ac.flight_plan.get("ssr"):
        from . import flightplan
        ac.flight_plan["ssr"] = code
        ac.flight_plan["message"] = flightplan.message(ac.flight_plan)
    w.aircraft[cs] = ac
    w._event("live", action="aircraft", callsign=cs, by=by,
             text=f"{cs} {ac.icao} FL{ac.fl if hasattr(ac, 'fl') else a['fl']} added "
                  + ("now at " + route[0] if appear == "now" else f"entering at {ac.entry_wp} in {int(float(appear) // 60)} min"))
    return {"callsign": cs, "type": ac.icao, "entry": ac.entry_wp, "exit": ac.planned_exit,
            "appears_at_s": round(ac.entry_time, 1), "ssr": code}


# ------------------------------------------------------------------ emergencies
def emergency(w, cs: str, kind: str, aerodrome: Optional[str] = None, why: Optional[str] = None, by: str = "") -> dict:
    a = _aircraft(w, cs)
    kind = str(kind).lower()
    if kind not in EMERGENCIES:
        raise ValueError(f"kind: one of {', '.join(EMERGENCIES)}")
    code, phrase = EMERGENCIES[kind]
    cs = a.callsign
    out = {"callsign": cs, "kind": kind, "squawk": code}
    if kind == "descent":
        fl = 100 if a.alt_ft > 14000 else max(50, int(a.alt_ft // 1000) * 10 - 40)
        text = phrase.format(cs=cs, why=why or "rapid decompression", fl=fl)
        a.emergency = {"code": code, "kind": kind, "t": round(w.t, 1), "text": text}
        r = w.issue({"callsign": cs, "type": "altitude", "fl": fl}, source="live", by_pilot=True)
        out["descending_to"] = fl if r["status"] != "rejected" else None
    elif kind == "medical":
        ad = (aerodrome or a.destination or "").upper()
        if not ad:
            ads = sorted(w.aerodromes.values(), key=lambda d: dist(a.pos, (d.x, d.y)))
            ad = ads[0].id if ads else ""
        if ad and ad in w.aerodromes and ad not in w.waypoints:
            d = w.aerodromes[ad]
            w.waypoints[ad] = (d.x, d.y)                 # the aerodrome as a point, to fly direct to it
            w.dyn_points[ad] = [round(d.x, 2), round(d.y, 2)]
        text = phrase.format(cs=cs, ad=ad or "the nearest aerodrome")
        a.emergency = {"code": code, "kind": kind, "t": round(w.t, 1), "text": text, "aerodrome": ad}
        _report(w, cs, text, "emergency")                 # the PAN PAN call, then the request to divert
        if ad in w.waypoints:
            w.add_dynamic_request(cs, "direct", ad, "medical emergency, diversion")
        out["aerodrome"] = ad
    else:
        text = phrase.format(cs=cs, why=why or "declaring an emergency")
        a.emergency = {"code": code, "kind": kind, "t": round(w.t, 1), "text": text}
    if kind in ("descent", "general"):
        _report(w, cs, text, "emergency")
    w._event("live", action="emergency", callsign=cs, kind=kind, squawk=code, by=by, text=text)
    out["text"] = text
    return out


def clear_emergency(w, cs: str, by: str = "") -> dict:
    a = _aircraft(w, cs)
    if not getattr(a, "emergency", None):
        raise ValueError(f"{a.callsign} has no emergency")
    kind = a.emergency["kind"]
    a.emergency = None
    w._event("live", action="clear_emergency", callsign=a.callsign, by=by,
             text=f"{a.callsign} " + ("radio contact regained" if kind == "radio" else "emergency over, squawking its code again"))
    return {"callsign": a.callsign, "cleared": kind}


def radio_failed(a) -> bool:
    e = getattr(a, "emergency", None)
    return bool(e and e.get("kind") == "radio")


# ------------------------------------------------------------------ requests, areas
def request(w, cs: str, kind: str, value=None, reason: str = "", by: str = "") -> dict:
    """The pilot asks for something now: level | direct | speed | deviation | report (free words)."""
    a = _aircraft(w, cs)
    k = str(kind).lower()
    reason = (reason or "").strip()
    if k == "level":
        fl = int(str(value).upper().lstrip("FL").lstrip("F"))
        if fl % 10:
            raise ValueError("a flight level, multiple of 10")
        rid = w.add_dynamic_request(a.callsign, "altitude", fl, reason or "")
    elif k == "direct":
        p = str(value).upper()
        if p not in w.waypoints:
            raise ValueError(f"unknown point {p}")
        rid = w.add_dynamic_request(a.callsign, "direct", p, reason or "")
    elif k == "speed":
        v = str(value).upper().replace(".", "").replace(" ", "")
        val = int(v[1:]) / 100.0 if v.startswith("M") else int(v)
        rid = w.add_dynamic_request(a.callsign, "speed", val, reason or "")
    elif k == "deviation":
        from . import pilot
        plan = pilot.plan_deviation(w, a, "either", 1e9, None)
        if plan is None:
            plan = {"heading": int((a.trk + 20) % 360) or 360, "distance_nm": 20, "then_direct": a.next_wp}
        rid = w.add_dynamic_request(a.callsign, "weather_deviation", plan, reason or "weather ahead")
    elif k == "report":
        t = " ".join(str(value or "").split())
        if not t:
            raise ValueError("report: the words the pilot says")
        rid = _report(w, a.callsign, f"{a.callsign} {t}", "live")
    else:
        raise ValueError("kind: level, direct, speed, deviation or report")
    if rid is None:
        raise ValueError("the request could not be made")
    w._event("live", action="request", callsign=a.callsign, request_id=rid, by=by,
             text=f"{a.callsign}: {w.requests[rid].phrase()}")
    return {"callsign": a.callsign, "request_id": rid, "text": w.requests[rid].phrase()}


def set_area(w, area_id: str, active: bool, by: str = "") -> dict:
    ar = w.areas.get(str(area_id))
    if ar is None:
        raise ValueError(f"unknown area {area_id}")
    if ar.active != bool(active):
        ar.active = bool(active)
        w._event("area_activated" if ar.active else "area_deactivated", area_id=ar.id, name=ar.name, kind=ar.kind,
                 by=by, text=f"{ar.name} {'activated' if ar.active else 'deactivated'} (live)")
    return {"area": ar.id, "active": ar.active, "next_change": ar.next_change()}


def options(w) -> dict:
    """What the panel offers: aircraft on the picture, emergencies in progress, areas, aerodromes, types."""
    from . import performance
    acs = sorted(a.callsign for a in w.aircraft.values() if a.active or (a.outside and a.assumed))
    return {"aircraft": acs,
            "emergencies": {a.callsign: a.emergency for a in w.aircraft.values() if getattr(a, "emergency", None)},
            "areas": [{"id": a.id, "name": a.name, "kind": a.kind, "active": a.active, "lower_fl": a.lower_fl,
                       "upper_fl": a.upper_fl, "next_change": a.next_change()} for a in w.areas.values()],
            "aerodromes": sorted(w.aerodromes),
            "types": sorted(performance.SUPPORTED)}
