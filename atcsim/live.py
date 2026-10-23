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


IDENT_S = 18.0          # the special position identification pulse lasts about 18 seconds


def ident(w, cs: str, by: str = "") -> dict:
    """"Squawk ident": the pilot presses IDENT, the label flashes on the radar for about 18 seconds (SPI)."""
    a = _aircraft(w, cs)
    a.ident_until = w.t + IDENT_S
    w._event("live", action="ident", callsign=a.callsign, by=by, text=f"{a.callsign} squawk ident")
    return {"callsign": a.callsign, "ident_until_s": round(a.ident_until, 1)}


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
            "types": sorted(performance.SUPPORTED),
            "readback": readback_view(w)}


# ------------------------------------------------------------------ read-back errors (hearback training)
READBACK_WINDOW_S = 60.0       # solo: the controller has this long to correct a wrong read-back
PROMPT_EXPIRE_S = 180.0        # exercise: a pseudo-pilot's prompt not reported by then is closed as unreported
_FIELD = {"altitude": "cfl", "heading": "heading", "speed": "speed", "direct": "direct"}


def set_readback_rate(w, rate: float, by: str = "") -> dict:
    r = max(0.0, min(0.5, float(rate)))
    w.readback_rate = r
    w._event("live", action="readback_rate", rate=r, by=by,
             text="read-back errors off" if r == 0 else f"read-back errors: about 1 clearance in {round(1 / r)}")
    return {"readback_rate": r}


def _hit(w, cs: str, field: str) -> bool:
    import random
    r = float(getattr(w, "readback_rate", 0.0) or 0.0)
    if r <= 0:
        return False
    return random.Random(f"{cs}|{field}|{int(w.t)}|{len(getattr(w, 'readback_traps', []))}").random() < r


def _wrong(w, ac, ins: dict) -> Optional[dict]:
    """The same clearance with a plausible wrong value (as a pilot mishears it), or None."""
    import random
    rnd = random.Random(f"{ac.callsign}|{int(w.t)}")
    t = ins["type"]
    if t == "altitude":
        fl = int(ins["fl"]) + rnd.choice([10, -10, 20, -20])
        return {**ins, "fl": fl} if 100 <= fl <= 460 else None
    if t == "heading":
        h = (int(ins["heading"]) + rnd.choice([10, -10, 20, 30]) - 1) % 360 + 1
        return {**ins, "heading": h}
    if t == "speed":
        if ins.get("mach"):
            return {**ins, "mach": round(ins["mach"] + rnd.choice([0.01, -0.01, 0.02]), 2)}
        if ins.get("ias_kt"):
            return {**ins, "ias_kt": int(ins["ias_kt"]) + rnd.choice([10, -10, 20])}
        return None
    if t == "direct":
        ahead = [n for n in ac.route[ac.next_index:] if not n.startswith("@") and n != ins["waypoint"] and n in w.waypoints]
        return {**ins, "waypoint": ahead[0]} if ahead else None
    return None


def _value(ins: dict) -> str:
    t = ins["type"]
    return (f"FL{int(ins['fl']):03d}" if t == "altitude" else f"heading {int(ins['heading']):03d}" if t == "heading"
            else f"M.{int(round(ins['mach'] * 100))}" if t == "speed" and ins.get("mach")
            else f"{ins.get('ias_kt')} kt" if t == "speed" else f"direct {ins.get('waypoint')}")


def _new_trap(w, cs: str, ins: dict, wrong: dict, mode: str) -> dict:
    traps = w.__dict__.setdefault("readback_traps", [])
    tr = {"id": len(traps) + 1, "callsign": cs, "field": _FIELD[ins["type"]], "cleared": _value(ins), "said": _value(wrong),
          "t": round(w.t, 1), "status": "open", "mode": mode, "instr": wrong}
    traps.append(tr)
    m = w.m.setdefault("readback", {"errors": 0, "caught": 0, "missed": 0})
    m["errors"] += 1
    return tr


def solo_readback(w, ac, ins: dict):
    """Solo radar: maybe a wrong read-back. Returns the clearance the pilot will fly (the wrong one, if so)."""
    if ins["type"] not in _FIELD or not _hit(w, ac.callsign, ins["type"]):
        return ins, None
    if any(t["status"] == "open" and t["callsign"] == ac.callsign for t in getattr(w, "readback_traps", [])):
        return ins, None
    wrong = _wrong(w, ac, ins)
    if wrong is None:
        return ins, None
    tr = _new_trap(w, ac.callsign, ins, wrong, "solo")
    w._event("readback_error", callsign=ac.callsign, trap=tr["id"], cleared=tr["cleared"], said=tr["said"],
             text=f"{ac.callsign} read back {tr['said']} (cleared {tr['cleared']})")
    return wrong, tr


def on_entry(w, cs: str, fields: dict) -> None:
    """A new entry for the aircraft: an open solo read-back error on that field is caught (corrected); in an
    exercise, maybe a prompt for the pseudo-pilot to read it back wrong."""
    for tr in getattr(w, "readback_traps", []):
        if tr["status"] == "open" and tr["mode"] == "solo" and tr["callsign"] == cs and fields.get(tr["field"]) not in (None, ""):
            tr["status"] = "caught"
            tr["closed_t"] = round(w.t, 1)
            w.m.setdefault("readback", {"errors": 0, "caught": 0, "missed": 0})["caught"] += 1
            w._event("readback_corrected", callsign=cs, trap=tr["id"], text=f"{cs} read-back error corrected ({tr['cleared']})")
    if getattr(w, "multi", False) and not getattr(w, "auto_pilot", False) and getattr(w, "readback_rate", 0):
        ac = w.aircraft.get(cs)
        if ac is None or not ac.assumed:
            return
        for k, t in (("cfl", "altitude"), ("heading", "heading"), ("direct", "direct")):
            v = fields.get(k)
            if v in (None, "") or not _hit(w, cs, t):
                continue
            ins = {"type": t, "callsign": cs, **({"fl": int(v)} if t == "altitude" else {"heading": int(v)} if t == "heading"
                                                 else {"waypoint": str(v).upper()})}
            wrong = _wrong(w, ac, ins)
            if wrong:
                tr = _new_trap(w, cs, ins, wrong, "pilot")
                w._event("readback_prompt", callsign=cs, trap=tr["id"], text=f"pseudo-pilot: read back {tr['said']} for {cs} (cleared {tr['cleared']})")
                break


def readback_result(w, trap_id: int, caught: bool, by: str = "") -> dict:
    """The pseudo-pilot reports whether the controller caught the wrong read-back."""
    tr = next((t for t in getattr(w, "readback_traps", []) if t["id"] == int(trap_id)), None)
    if tr is None or tr["status"] != "open":
        raise ValueError("no such open read-back error")
    tr["status"] = "caught" if caught else "missed"
    tr["closed_t"] = round(w.t, 1)
    w.m.setdefault("readback", {"errors": 0, "caught": 0, "missed": 0})["caught" if caught else "missed"] += 1
    w._event("readback_corrected" if caught else "readback_missed", callsign=tr["callsign"], trap=tr["id"], by=by,
             text=f"{tr['callsign']} wrong read-back ({tr['said']} for {tr['cleared']}) " + ("caught" if caught else "missed"))
    return tr


def update(w) -> None:
    """Every second: solo read-back errors not corrected in time are missed; old prompts close."""
    for tr in getattr(w, "readback_traps", []):
        if tr["status"] != "open":
            continue
        age = w.t - tr["t"]
        if tr["mode"] == "solo" and age >= READBACK_WINDOW_S:
            tr["status"], tr["closed_t"] = "missed", round(w.t, 1)
            w.m.setdefault("readback", {"errors": 0, "caught": 0, "missed": 0})["missed"] += 1
            w._event("readback_missed", callsign=tr["callsign"], trap=tr["id"],
                     text=f"{tr['callsign']} wrong read-back not corrected: flying {tr['said']} (cleared {tr['cleared']})")
        elif tr["mode"] == "pilot" and age >= PROMPT_EXPIRE_S:
            tr["status"], tr["closed_t"] = "unreported", round(w.t, 1)


def readback_view(w) -> dict:
    return {"rate": float(getattr(w, "readback_rate", 0.0) or 0.0),
            "traps": [{k: v for k, v in t.items() if k != "instr"} for t in getattr(w, "readback_traps", [])][-30:],
            "counts": dict(w.m.get("readback", {"errors": 0, "caught": 0, "missed": 0}))}
