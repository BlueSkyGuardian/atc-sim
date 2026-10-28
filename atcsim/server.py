"""HTTP API (FastAPI). Interactive docs at /docs once the server runs."""
from __future__ import annotations

import math
import os
from typing import List, Literal, Optional, Union

import asyncio

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import performance
from .scenario import ScenarioError, list_scenarios, load_scenario, public_view
from .session import SessionManager, parse_command, sector_description

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(
    title="ATC Sector Simulator",
    version="1.0",
    description="En-route sector simulator for training/evaluating ATC agents. "
                "Coordinates in NM (origin = sector centre, x east, y north).",
)
from .platform import config as pconfig
from .platform import auth as pauth, exercises as pex, comms as pcomms, recorder as prec, stats as pstats, debriefs as pdeb, ailab as pai, notify as pnotify, news as pnews, seo as pseo
from .platform.access import AccessMiddleware, seat_for

if pconfig.AUTH == "off":
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(AccessMiddleware)


class NoStaleMiddleware:
    """Pages and /static files: the browser revalidates every time (ETag -> 304), so an update is never hidden
    behind a cached old radar.html / script."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                hs = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"cache-control"]
                ctype = next((v for k, v in hs if k.lower() == b"content-type"), b"")
                if scope["path"].startswith("/static/") or ctype.startswith(b"text/html"):
                    hs.append((b"cache-control", b"no-cache"))
                    msg = dict(msg, headers=hs)
            await send(msg)
        await self.app(scope, receive, send2)


app.add_middleware(NoStaleMiddleware)


class HeadMiddleware:
    """HEAD answered as GET without the body (the routes only declare GET): crawlers and link checkers - Google's
    among them - ask HEAD first, and a 405 there reads as a broken page or sitemap."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "HEAD":
            return await self.app(scope, receive, send)
        done = False

        async def send2(msg):
            nonlocal done
            if msg["type"] == "http.response.body":
                if done:
                    return
                done = True
                msg = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(msg)
        await self.app(dict(scope, method="GET"), receive, send2)


app.add_middleware(HeadMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
manager = SessionManager()
from .world import World as _World  # noqa: E402
_World.radar_sweep_s = pconfig.RADAR_SWEEP_S         # the radar's sweep: the picture taken at each (ATC_RADAR_SWEEP_S)
pex.manager = manager
pcomms.manager = manager
prec.manager = manager
pdeb.manager = manager
app.include_router(pdeb.router)
app.include_router(pai.router)
app.include_router(pauth.router)
app.include_router(pex.router)
app.include_router(pcomms.router)
app.include_router(prec.router)
app.include_router(pstats.router)
app.include_router(pnotify.router)
app.include_router(pnews.router)
app.include_router(pseo.router)


@app.on_event("startup")
def _startup():
    restored = pex.restore_live()                 # exercises running when the server stopped come back, paused
    if restored:
        print(f"  restored {len(restored)} exercise(s): {', '.join(restored)}")
    prec.recover()
    pnews.start()                               # aviation news: the feeds every hour, the newsletter once a day
    import threading
    from . import docs_gen                     # build the documentation's generated reference in the background
    threading.Thread(target=docs_gen.reference_md, daemon=True).start()


# ------------------------------------------------------------------ models
class SessionCreate(BaseModel):
    scenario: str = Field(..., examples=["s01_basic_crossing"])
    mode: Literal["realtime", "stepped"] = "realtime"
    time_factor: float = Field(1.0, description="Sim seconds per wall second in realtime mode (0.1-100)")
    autostart: bool = True
    label: Optional[str] = None
    auto_assume: Optional[bool] = Field(None, description="assume aircraft automatically on first contact "
                                        "(default: the scenario's setting, normally false - assume them yourself)")


class Instruction(BaseModel):
    callsign: str
    type: Literal["altitude", "heading", "direct", "speed", "resume", "reply", "deviation", "assume", "release",
                  "reroute", "wx_clear"]
    fl: Optional[int] = Field(None, description="altitude: target flight level, e.g. 360")
    rate_fpm: Optional[float] = Field(None, description="altitude: optional vertical rate, ft/min")
    rate_mode: Optional[Literal["min", "max", "exact"]] = Field(
        None, description="altitude: the rate is a minimum (at X or greater, the default), a maximum (at X or less) or exact (at X)")
    heading: Optional[float] = Field(None, description="heading: 1-360")
    turn: Optional[Literal["left", "right"]] = None
    waypoint: Optional[str] = Field(None, description="direct: waypoint name")
    mach: Optional[float] = Field(None, description="speed / altitude: Mach to fly above the conversion altitude")
    conversion_kt: Optional[float] = Field(None, description="speed / altitude: IAS after conversion, e.g. 280")
    ias_kt: Optional[float] = Field(None, description="speed / altitude: fly this IAS now, at every level")
    resume_normal: Optional[bool] = Field(None, description="speed: true = resume own speed schedule")
    request_id: Optional[str] = Field(None, description="reply: request id (Rxxx)")
    decision: Optional[Literal["approved", "unable", "roger"]] = None
    side: Optional[Literal["left", "right", "either"]] = Field(None, description="deviation: side of track")
    max_nm: Optional[float] = Field(None, description="deviation: maximum lateral offset from track")
    then_direct: Optional[str] = Field(None, description="deviation: waypoint to proceed to afterwards")
    points: Optional[list] = Field(None, description="reroute: points to fly via (names, or [x, y] positions in NM)")


class Command(BaseModel):
    text: str = Field(..., examples=["RAM101 FL370 H250"])


class Control(BaseModel):
    action: Literal["start", "resume", "pause", "set_speed", "set_mode", "step", "restart", "auto_wx", "rewind"]
    seconds: float = 1.0
    value: Optional[bool] = None
    time_factor: Optional[float] = None
    mode: Optional[Literal["realtime", "stepped"]] = None


class Probe(BaseModel):
    instructions: List[Instruction] = []
    horizon_s: float = Field(600, ge=30, le=3600)
    include_inbound: bool = True


class AreaPatch(BaseModel):
    active: bool


# ------------------------------------------------------------------ helpers
def _session(sid: str):
    try:
        return manager.get(sid)
    except KeyError:
        raise HTTPException(404, f"session {sid} not found")


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


# ------------------------------------------------------------------ routes
def _user(request) -> dict:
    return pauth.current_user(request) or pauth.LOCAL_ADMIN


def _pilot_exec(s, request) -> bool:
    """Connected exercise: instructions sent by the pseudo-pilot (or the instructor) are executed by the aircraft
    whether or not the controller has assumed it; the controller's own actions go through /entries and /assume."""
    return bool(getattr(s, "multi", False)) and seat_for(s.id, _user(request)) in ("pilot", "instructor")


def _act(sid: str, request: Request, kind: str, body: dict):
    """A logged action on a session (see inputs.py): carried out, recorded with its sim time; errors as HTTP."""
    from . import inputs
    from .world import InstructionError
    s = _session(sid)
    u = _user(request)
    seat = seat_for(sid, u) if getattr(s, "multi", False) else "instructor"
    actor = {"id": u.get("id") if u else None, "seat": seat,
             "name": pex.db.public_user(u)["name"] if u and u.get("id") else (seat or "atc")}
    try:
        return inputs.run(s, kind, body, actor)
    except inputs.Refused as e:
        raise HTTPException(403, str(e))
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    except (InstructionError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/health")
def health():
    return {"ok": True, "sessions": len(manager.sessions)}


@app.get("/airspace")
def airspace(name: str = Query("training", description="training | agadir"),
             control: Optional[str] = Query(None, description="comma-separated sectors, e.g. WS or ON,OS")):
    """Static airspace geometry: boundary, sectors, waypoints (with lat/lon for real airspaces), airways,
    transfer points, separation minima. A session's own airspace is in its /connect briefing."""
    from . import airspace as airspace_mod
    try:
        asp = airspace_mod.get(name, control.split(",") if control else None)
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(404, str(e))
    d = sector_description()
    d.update(asp.describe())
    return d


@app.get("/airspaces")
def airspaces():
    from . import airspace as airspace_mod
    return airspace_mod.available()


@app.get("/types")
def types():
    """Supported aircraft types with their OpenAP-derived performance summary."""
    return performance.catalogue()


@app.get("/scenarios")
def scenarios(request: Request):
    u = _user(request)
    return [s for s in list_scenarios() if pex.db.scenario_access(s["name"], u)]


@app.get("/scenarios/{name}/raw")
def scenario_raw(name: str, request: Request):
    """The scenario file itself (for the designer)."""
    from . import designer
    acc = pex.db.scenario_access(name, _user(request))
    if acc is None:
        raise HTTPException(403, "no access to this scenario")
    try:
        d = designer.raw(name)
        return {**d, "_access": acc} if isinstance(d, dict) else d
    except ScenarioError as e:
        raise HTTPException(404, str(e))


@app.get("/designer", include_in_schema=False)
def designer_page():
    return FileResponse(os.path.join(STATIC, "designer.html"))


class DesignerBody(BaseModel):
    scenario: dict
    policy: Literal["passive", "naive_approve"] = "passive"
    at_s: float = 0.0
    fl: float = 350.0
    product: Literal["composite", "cappi"] = "composite"
    name: Optional[str] = None
    overwrite: bool = False


class GenerateBody(BaseModel):
    params: dict = {}


@app.post("/designer/generate")
def designer_generate(body: GenerateBody):
    """A random scenario for the sectors given, from parameters (see atcsim/generator.py): flights per hour, density,
    sequences per hour and their size, overtakes, crossings, level requests through traffic, West / Oceanic
    transatlantic flights, slow and long-haul shares, levels, seed. Returns the scenario (not saved)."""
    from . import generator
    try:
        return generator.generate(body.params)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.get("/designer/generate/airports")
def designer_generate_airports(control: str):
    """The aerodromes whose departures / arrivals the sectors handle (per-aerodrome rates of the generator)."""
    from . import generator
    return generator.airports_for([s for s in control.split(",") if s])


@app.post("/designer/compile")
def designer_compile(body: DesignerBody):
    """Build an edited scenario: errors, lint warnings, planned trajectories and the conflicts they contain."""
    from . import designer
    return designer.compile(body.scenario)


@app.post("/designer/preview")
def designer_preview(body: DesignerBody):
    """Fast-time run of an edited scenario (passive or naive_approve): trajectories, losses, pilot requests."""
    from . import designer
    return designer.preview(body.scenario, body.policy)


@app.post("/designer/weather")
def designer_weather(body: DesignerBody):
    """Reflectivity and cell list of an edited scenario's weather at `at_s`."""
    from . import designer
    try:
        return designer.weather(body.scenario, body.at_s, body.product, body.fl)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, f"weather spec: {e}")


@app.post("/designer/save")
def designer_save(body: DesignerBody, request: Request):
    from . import designer
    body.scenario.pop("_access", None)
    name = body.name or body.scenario.get("name", "")
    u = _user(request)
    if not (body.scenario.get("author") or "").strip():               # the designer who saves it, by default
        body.scenario["author"] = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or u.get("email", "")
    if designer.NAME_RE.match(name or ""):
        pex.check_scenario_write(name, _user(request))
    try:
        return designer.save(body.name or body.scenario.get("name", ""), body.scenario, body.overwrite)
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    except ScenarioError as e:
        raise HTTPException(400, str(e))


@app.get("/scenarios/{name}")
def scenario(name: str, include_notes: bool = False):
    """Scenario briefing. `include_notes=true` reveals the trap design and all future requests (for humans/evaluators)."""
    try:
        return public_view(load_scenario(name), include_notes)
    except ScenarioError as e:
        raise HTTPException(404, str(e))


@app.post("/sessions", status_code=201)
def create_session(body: SessionCreate, request: Request):
    u = _user(request)
    if u["id"] != 0 and pex.db.scenario_access(body.scenario, u) is None:
        raise HTTPException(403, "no access to this scenario")
    try:
        s = manager.create(scenario_name=body.scenario, mode=body.mode, time_factor=body.time_factor,
                           autostart=body.autostart, label=body.label, auto_assume=body.auto_assume)
    except (ScenarioError, ValueError) as e:
        raise HTTPException(400, str(e))
    if u["id"] != 0:                 # signed-in instructor: the session is one of their exercises (single seat)
        pex.register(s, u, body.label or body.scenario, multi=False)
    return s.briefing()


@app.get("/sessions")
def list_sessions(request: Request):
    u = _user(request)
    return [x for x in manager.list() if seat_for(x["session_id"], u) == "instructor"]


@app.get("/sessions/{sid}")
def get_session(sid: str):
    """Briefing: session status, scenario description, sector, waypoints, routes and area activation status."""
    s = _session(sid)
    with s.lock:
        return s.briefing()


@app.post("/sessions/{sid}/connect")
def connect(sid: str, client: str = "agent"):
    """Register a client and get the full briefing (incl. current activation status of every area)."""
    s = _session(sid)
    with s.lock:
        s.clients.append({"client": client, "sim_time_s": s.world.t})
        s.world._event("client_connected", client=client)
        return s.briefing()


@app.delete("/sessions/{sid}")
def delete_session(sid: str):
    _session(sid)
    pex.end(sid)
    return {"deleted": sid}


@app.post("/sessions/{sid}/control")
def control(sid: str, body: Control, request: Request):
    s = _session(sid)
    try:
        if body.action == "auto_wx":
            _act(sid, request, "auto_wx", {"value": body.value is not False})
            return s.summary()
        return s.control(body.action, seconds=body.seconds, time_factor=body.time_factor, mode=body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))


class LiveAction(BaseModel):
    action: Literal["aircraft", "emergency", "clear_emergency", "ident", "request", "area", "readback_rate", "readback_result"]
    rate: Optional[float] = None
    trap: Optional[int] = None
    caught: Optional[bool] = None
    callsign: Optional[str] = None
    type: Optional[str] = None
    fl: Optional[int] = None
    route: Optional[List[str]] = None
    appear: Optional[Union[str, float]] = "now"
    dep: Optional[str] = None
    dest: Optional[str] = None
    kind: Optional[str] = None
    aerodrome: Optional[str] = None
    why: Optional[str] = None
    value: Optional[Union[str, float, int]] = None
    reason: Optional[str] = None
    area: Optional[str] = None
    active: Optional[bool] = None


@app.get("/sessions/{sid}/live")
def live_options(sid: str):
    """What the live actions can use: aircraft on the picture, emergencies in progress, areas, aerodromes, types."""
    from . import live
    s = _session(sid)
    with s.lock:
        return live.options(s.world)


@app.post("/sessions/{sid}/live")
def live_action(sid: str, body: LiveAction, request: Request):
    """A live action (instructor, pseudo-pilots, the solo radar's controller): "aircraft" (callsign, type, fl, route,
    appear: "now" or seconds to its entry), "emergency" (callsign, kind: descent | medical | radio | general,
    aerodrome, why), "clear_emergency" (callsign), "ident" (callsign: squawk ident, the label flashes ~18 s), "request" (callsign, kind: level | direct | speed | deviation |
    report, value, reason), "area" (area, active)."""
    return _act(sid, request, "live", body.model_dump())


class Bookmark(BaseModel):
    note: str = ""


@app.post("/sessions/{sid}/bookmark")
def bookmark(sid: str, body: Bookmark, request: Request):
    """Mark this moment for the debrief (instructor, pseudo-pilots, the solo radar's controller): on the timeline and
    in the debrief, with a note."""
    return _act(sid, request, "bookmark", {"note": body.note})


@app.get("/sessions/{sid}/timeline")
def timeline(sid: str):
    """The points the simulation can be rewound to (POST /control {"action": "rewind", "seconds": t})."""
    s = _session(sid)
    with s.lock:
        return s.timeline()


@app.get("/sessions/{sid}/state")
def state(sid: str, trails: bool = False, sweep: bool = False):
    """Full snapshot: aircraft, inbound traffic, pending requests, pending instructions, areas, current losses.
    sweep=true: the aircraft as the radar painted them at its last sweep (ATC_RADAR_SWEEP_S); default: live."""
    s = _session(sid)
    with s.lock:
        st = s.world.state(trails, pconfig.RADAR_SWEEP_S if sweep else None)
        st["session"] = s.summary()
        return st


@app.get("/sessions/{sid}/aircraft")
def aircraft(sid: str, trails: bool = False):
    s = _session(sid)
    with s.lock:
        return [a.snapshot(trails) for a in s.world.active_aircraft()]


@app.get("/sessions/{sid}/aircraft/{callsign}")
def aircraft_one(sid: str, callsign: str):
    """Aircraft detail incl. type performance at present weight (max FL, climb/descent rates, speed ranges)."""
    s = _session(sid)
    with s.lock:
        a = s.world.aircraft.get(callsign.upper())
        if a is None:
            raise HTTPException(404, "unknown callsign")
        d = a.snapshot(True)
        if a.active or a.outside:
            d["performance"] = a.performance_snapshot()
            d["assignable_fl"] = [100, s.world.upper_fl] if a.active and not a.released else [50, 600]
        return d


@app.get("/sessions/{sid}/aircraft/{callsign}/performance")
def aircraft_performance(sid: str, callsign: str):
    """The aircraft type's performance (type data, speed schedule, assignable speeds) and, at the present weight,
    a table by level: speeds of the schedule, climb rate available, idle descent rate, whether the level can be
    reached, plus the maximum level and turn radius now."""
    from . import atmosphere as atm
    s = _session(sid)
    with s.lock:
        a = s.world.aircraft.get(callsign.upper())
        if a is None:
            raise HTTPException(404, "unknown callsign")
        p = a.perf
        out = {"callsign": a.callsign, "type": p.summary(), "mass_kg": round(a.mass),
               "load": round((a.mass - p.oew) / max(1.0, p.mtow - p.oew), 2),
               "wake": (a.flight_plan or {}).get("wake"), "now": a.performance_snapshot() if a.status != "scheduled" else None}
        rows = []
        top = int(p.ceiling_ft // 100)
        for fl in sorted(set([100, 150, 200, 250, 280, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400, 410, 430,
                              450, 470, 490, 510] + ([int(round(a.alt_ft / 1000)) * 10] if a.status != "scheduled" else []))):
            if fl > top:
                continue
            alt = fl * 100.0
            mode, val = a.speed_target(alt)
            tas = p.tas_for(mode, val, alt)
            ok, why = p.level_available(fl, mode, val, a.mass)
            rows.append({"fl": fl, "mode": mode, "tas_kt": round(tas),
                         "mach": round(atm.tas_to_mach(tas * atm.KT, alt * atm.FT, p.dT), 2),
                         "ias_kt": round(atm.tas_to_cas(tas * atm.KT, alt * atm.FT, p.dT) / atm.KT),
                         "climb_fpm": round(p.roc_fpm(alt, mode, val, a.mass)),
                         "idle_descent_fpm": round(-p.roc_fpm(alt, mode, val, a.mass, "idle")),
                         "available": ok, "why": None if ok else why})
        out["levels"] = rows
        return out


@app.get("/sessions/{sid}/positions")
def positions(sid: str):
    """Compact positions only."""
    s = _session(sid)
    with s.lock:
        return {"sim_time_s": round(s.world.t, 1),
                "positions": [{"callsign": a.callsign, "x_nm": round(a.x, 3), "y_nm": round(a.y, 3),
                               "fl": int(round(a.alt_ft / 100)), "heading": round(a.hdg, 1), "speed_kt": round(a.spd)}
                              for a in s.world.active_aircraft()]}


@app.get("/sessions/{sid}/separation")
def separation(sid: str, lookahead_s: float = Query(600, ge=30, le=3600), max_pair_distance_nm: float = 40,
               include_inbound: bool = True, all_pairs: bool = False):
    """Current pairwise separation and predicted conflicts. The look-ahead flies the full aircraft model
    (type performance, wind, LNAV, pending clearances) forward in time."""
    s = _session(sid)
    return s.separation(lookahead_s, include_inbound, all_pairs, max_pair_distance_nm)


@app.post("/sessions/{sid}/probe")
def probe(sid: str, body: Probe):
    """What-if: predict conflicts if these instructions were issued now (nothing is changed)."""
    s = _session(sid)
    base = s.separation(body.horizon_s, body.include_inbound)           # cached per sim second, lock-free
    with s.lock:                                                        # copy the world, predict outside the lock
        w = s.world.clone()
    pw = w.predict(body.horizon_s, extra=[_clean(i.model_dump()) for i in body.instructions],
                   include_inbound=body.include_inbound)
    withi = w.analyze(pw)
    return {"sim_time_s": round(w.t, 1), "horizon_s": body.horizon_s,
            "rejected_instructions": pw["rejected"],
            "baseline": {"conflicts": base["predicted_conflicts"],
                         "area_infringements": base["predicted_area_infringements"],
                         "weather_penetrations": base["predicted_weather_penetrations"]},
            "with_instructions": {"conflicts": withi["conflicts"], "area_infringements": withi["area_infringements"],
                                  "weather_penetrations": withi["weather_penetrations"],
                                  "exits": withi["exits"]}}


@app.post("/sessions/{sid}/instructions")
def instructions(sid: str, body: Union[Instruction, List[Instruction]], request: Request):
    """Issue one instruction or a list. Each gets a record: accepted (executes after pilot delay) or rejected."""
    s = _session(sid)
    items = body if isinstance(body, list) else [body]
    if s.status == "finished":
        raise HTTPException(409, "scenario finished")
    return _act(sid, request, "instructions", {"items": [_clean(i.model_dump()) for i in items]})


@app.post("/sessions/{sid}/command")
def command(sid: str, body: Command, request: Request):
    """Free-text controller shorthand, e.g. 'RAM101 FL370 H250', 'AFR22 D S3', 'IBE5 UNA'."""
    return _act(sid, request, "command", {"text": body.text})


class Entry(BaseModel):
    callsign: str
    cfl: Optional[Union[int, str]] = None
    xfl: Optional[Union[int, str]] = None
    speed: Optional[str] = None
    heading: Optional[Union[int, str]] = None
    direct: Optional[str] = None
    note: Optional[str] = None
    wx: Optional[str] = None
    coord: Optional[str] = None
    route: Optional[Union[str, list]] = None
    hlim: Optional[str] = None                # limit of the heading: "5MIN", "20NM"
    then: Optional[str] = None                # after the heading's limit: direct to this point
    slim: Optional[str] = None                # limit of the speed: "10MIN", "40NM", a point
    rate: Optional[Union[int, str]] = None    # rate with the level: 1500 (ft/min or greater), "1500-" (or less)
    force: Optional[bool] = None              # simulated pilots: fly it whatever the weather or performance (what-ifs)


@app.post("/sessions/{sid}/entries")
def entries(sid: str, body: Entry, request: Request):
    """Controller's label input (connected mode): what was cleared, as entered in the system. The aircraft does
    not move until the pseudo-pilot executes the clearance given on the frequency. Send "" to clear a field."""
    fields = {k: v for k, v in body.model_dump().items() if k != "callsign" and k in body.model_fields_set}
    return _act(sid, request, "entries", {"callsign": body.callsign, **fields})     # solo: with what the pilot read back


class SequenceReq(BaseModel):
    callsigns: List[str] = Field(..., description="the aircraft to sequence (two or more)")
    point: str = Field(..., description="the point where they must be in trail (a waypoint on their routes)")
    spacing_nm: Optional[float] = Field(None, description="in-trail spacing at the point (default: the letter of agreement's)")


@app.post("/sessions/{sid}/sequence")
def sequence_tool(sid: str, body: SequenceReq):
    """The controller's sequencing tool: the aircraft chosen in trail at the point chosen. For each follower, its
    sequencing ellipse (the turn points that put it exactly the spacing behind the one ahead, wind included) and every
    heading on it with its time and distance and whether the rules allow it; the plan (speed first, then a fan of
    vectors, checked against all the traffic) with its clearances, ready for /entries."""
    from .solve import resolve, sequence as seq_mod
    s = _session(sid)
    with s.lock:
        w = resolve.clone(s.world)
    try:
        return {"sim_time_s": round(w.t, 1), **seq_mod.tool(w, [c.upper() for c in body.callsigns], body.point, body.spacing_nm)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/sessions/{sid}/solve")
def solve(sid: str, callsign: Optional[str] = None, other: Optional[str] = None, horizon_s: float = Query(600, ge=120, le=1200),
          rollout: int = Query(2, ge=0, le=4)):
    """The conflict solver: for each predicted conflict involving an assumed aircraft (kind "conflict"), and each pair
    leaving by the same exit closer than the letter of agreement's spacing (kind "exit"), the set of clearances (speed,
    direct, delaying vector, level, or a pair of them) that leave everyone separated, ranked speed first, then
    heading, then level, the aircraft with priority in its level left alone where possible; the best ones confirmed by
    running a copy of the simulation ahead. Each solution's "actions" can be sent as they are to /entries.
    Recomputed at most every 5 simulated seconds."""
    import threading
    from .solve import resolve
    s = _session(sid)
    with s.lock:
        import json as _json
        key = (int(s.world.t // 5), callsign, other, horizon_s, rollout, getattr(s, "rewinds", 0),
               hash(_json.dumps(s.world.entries, sort_keys=True, default=str)),
               tuple(sorted(c for c, a in s.world.aircraft.items() if a.active and a.assumed)),
               tuple(sorted(q.id for q in s.world.requests.values() if q.status == "pending")))
        c = getattr(s, "_solve_cache", None)
        if c and c[0] == key:
            return c[1]
        w = resolve.clone(s.world)
    lk = s.__dict__.setdefault("_solve_lock", threading.Lock())
    with lk:
        c = getattr(s, "_solve_cache", None)
        if c and c[0] == key:
            return c[1]
        res = resolve.solve(w, (callsign, other) if callsign and other else None, horizon_s, rollout=rollout)
        if callsign and not other:
            res["sets"] = [x for x in res["sets"] if callsign in x["pair"]]
        out = {"sim_time_s": round(w.t, 1), "horizon_s": horizon_s, "conflicts": [
            {"pair": x["pair"], "kind": x.get("kind", "conflict"), "exit": x.get("exit"), "required_nm": x.get("required_nm"),
             "t_first_s": None if x["t_first"] is None else round(x["t_first"]), "d_min_nm": x["d_min"],
             "move": x["move"], "priority": x["priority"],
             "turn_enough": {m: hv["turn_enough"] for m, hv in (x.get("hj") or {}).items()},
             "ruled_out": x.get("rejected") or {},
             "optimal": [o.text for o in x["optimal"]],
             "solutions": [o.public() for o in x["solutions"]]} for x in res["sets"]],
            "text": resolve.describe(res)}
        # pending level / direct requests of the controller's aircraft: as asked, with a speed or vector, or not now
        reqs = []
        for q in w.requests.values():
            ac_q = w.aircraft.get(q.callsign)
            if q.status != "pending" or q.type not in ("altitude", "direct") or ac_q is None or not ac_q.active or not ac_q.assumed:
                continue
            try:
                v = str(q.value).upper()
                rq = {"cfl": int(v.lstrip("F").lstrip("L"))} if q.type == "altitude" else {"direct": v}
                sol = resolve.accommodate(w, q.callsign, rq)
            except Exception:  # noqa: BLE001
                continue
            reqs.append({"request_id": q.id, "callsign": q.callsign, "text": q.phrase(), "possible": sol is not None,
                         "as_asked": bool(sol) and len(sol["actions"]) == 1 and not (set(sol["actions"][0]) - {"type", "callsign", "cfl", "direct"}),
                         "actions": sol["actions"] if sol else [], "how": sol["text"] if sol else "nothing makes it separated now"})
        out["requests"] = reqs
        s._solve_cache = (key, out)
        return out


class MtcdProbe(BaseModel):
    callsign: str
    cfl: Optional[int] = None
    heading: Optional[int] = None
    direct: Optional[str] = None
    levels: Optional[List[int]] = None


@app.get("/sessions/{sid}/mtcd")
def mtcd_list(sid: str):
    """Medium-term conflict detection: conflicts between the system trajectories over the exercise's look-ahead
    (20 minutes unless changed with POST /sessions/{sid}/mtcd/settings)."""
    from . import atctools
    s = _session(sid)
    with s.lock:
        return {"utc": atctools.utc(s.world), "look_ahead_s": atctools.look_ahead(s.world), "conflicts": atctools.mtcd(s.world)}


class MtcdSettings(BaseModel):
    look_ahead_min: float


@app.post("/sessions/{sid}/mtcd/settings")
def mtcd_settings(sid: str, body: MtcdSettings, request: Request):
    """Set the MTCD look-ahead of this exercise, in minutes (5 to 60). Every seat's MTCD (list, tags, conflict
    display) and the what-if probes then look that far ahead. Controller or instructor."""
    return _act(sid, request, "mtcd_settings", {"look_ahead_min": body.look_ahead_min})


@app.post("/sessions/{sid}/mtcd")
def mtcd_probe(sid: str, body: MtcdProbe):
    """What-if probe for one aircraft: conflicts with another cleared level, heading or direct (over the MTCD
    look-ahead);
    "levels": [...] checks a list of levels at once (the label menu colours them)."""
    from . import atctools
    s = _session(sid)
    with s.lock:
        try:
            return atctools.probe(s.world, body.callsign, body.cfl, body.heading, body.direct, body.levels)
        except ValueError as e:
            raise HTTPException(400, str(e))


class CpdlcText(BaseModel):
    text: str
    callsign: Optional[str] = None
    voice: bool = False
    force: bool = False                       # simulated pilots: comply whatever the weather or performance


class CpdlcAnswer(BaseModel):
    message_id: int
    answer: str
    reason: Optional[str] = None
    text: Optional[str] = None


class CpdlcDown(BaseModel):
    callsign: str
    kind: Optional[str] = None
    value: Optional[object] = None
    reason: Optional[str] = None
    request_id: Optional[str] = None


def _who(request: Request, dflt: str) -> str:
    u = _user(request)
    return pex.db.public_user(u)["name"] if u and u.get("id") else dflt


@app.get("/sessions/{sid}/cpdlc")
def cpdlc_messages(sid: str, since: int = 0, callsign: Optional[str] = None):
    """CPDLC: every message since an id (optionally one aircraft's), and the connection of each equipped aircraft
    (logon, cda = current data authority, transferred; nda = next data authority) with its open dialogues."""
    from . import cpdlc
    s = _session(sid)
    with s.lock:
        return {"utc": cpdlc._utc(s.world), "own": cpdlc.own(s.world), "messages": cpdlc.messages(s.world, since, callsign),
                "connections": cpdlc.state_view(s.world)}


@app.post("/sessions/{sid}/cpdlc/parse")
def cpdlc_parse(sid: str, body: CpdlcText):
    """What a command line would send (the elements in clear), for the suggestions as the controller types."""
    from . import cpdlc
    s = _session(sid)
    with s.lock:
        return cpdlc.preview(s.world, body.text, body.callsign)


@app.post("/sessions/{sid}/cpdlc/command")
def cpdlc_command(sid: str, body: CpdlcText, request: Request):
    """The controller's command line, e.g. {"callsign": "RAM201", "text": "CL350 DCT ROLRO"}: an uplink to a connected
    aircraft (or an answer to its request: APP, UNA, SBY, ROG). voice=true, or an aircraft without an active
    connection: only puts the clearance in the system (cleared level, heading, direct, speed...) for a voice clearance."""
    return _act(sid, request, "cpdlc_command", {"text": body.text, "callsign": body.callsign, "voice": body.voice,
                                                **({"force": True} if body.force else {})})


@app.post("/sessions/{sid}/cpdlc/respond")
def cpdlc_respond(sid: str, body: CpdlcAnswer, request: Request):
    """The pilot answers an uplink: WILCO (the aircraft executes it and the system takes it), UNABLE (reason
    weather / performance), STANDBY, ROGER, AFFIRM, NEGATIVE."""
    return _act(sid, request, "cpdlc_respond", body.model_dump())


@app.post("/sessions/{sid}/cpdlc/downlink")
def cpdlc_downlink(sid: str, body: CpdlcDown, request: Request):
    """A pilot request by data link: kind level | climb | descent | direct | speed | deviation | text, with value;
    or request_id = one of the aircraft's pending requests, sent by CPDLC instead of the frequency."""
    return _act(sid, request, "cpdlc_downlink", body.model_dump())


class ChatIn(BaseModel):
    text: str
    to: Optional[str] = None
    sender: Optional[str] = Field(None, alias="from")
    callsign: Optional[str] = None
    ref: Optional[int] = None

    model_config = {"populate_by_name": True}


@app.get("/sessions/{sid}/chat")
def chat_messages(sid: str, since: int = 0, station: Optional[str] = None):
    """Coordination chat: the stations (ours, the other sectors, the adjacent units) and the messages since an id,
    each with its sender ("from") and destination ("to"); station= keeps one station's messages."""
    from . import chat
    s = _session(sid)
    with s.lock:
        try:
            return {"own": chat.own_station(s.world), "stations": chat.stations(s.world), "quick": chat.QUICK,
                    "messages": chat.messages(s.world, since, station)}
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/sessions/{sid}/chat")
def chat_send(sid: str, body: ChatIn, request: Request):
    """Send a coordination message: {"from": station, "to": station, "text", "callsign", "ref"}. The controller writes
    as our sector (from may be left out) to another station; the pseudo-pilot writes as another sector or unit."""
    return _act(sid, request, "chat", {"text": body.text, "to": body.to, "from": body.sender, "callsign": body.callsign,
                                       "ref": body.ref})


@app.get("/sessions/{sid}/oldi")
def oldi_messages(sid: str, since: int = 0, callsign: Optional[str] = None):
    """OLDI messages exchanged with the adjacent units (ABI, ACT, REV; dir out = sent by us, in = received),
    and the coordination state of each aircraft (cleared level, exit level, coordinated level, manual coordination)."""
    from . import oldi, atctools, levelcoord
    s = _session(sid)
    with s.lock:
        w = s.world
        abi, act, rev = oldi.settings(w)
        return {"utc": atctools.utc(w), "abi_s": abi, "act_s": act, "rev_limit_s": rev,
                "messages": oldi.messages(w, since, callsign), "coord": oldi.coord_view(w),
                "level_requests": levelcoord.view(w)}


class LevelAnswer(BaseModel):
    callsign: str
    level: Optional[int] = None


@app.post("/sessions/{sid}/oldi/answer")
def oldi_answer(sid: str, body: LevelAnswer, request: Request):
    """Answer the West sector's level request for a climb to SUBAL / BEXAL (trainee on Oceanic): the level given
    (the requested one = ACP, another = CDN) or null (RJC: no climb). West climbs the aircraft to it."""
    return _act(sid, request, "oldi_answer", body.model_dump())


class StartupAnswer(BaseModel):
    callsigns: List[str] = []
    callsign: Optional[str] = None
    approve: bool = True
    point: Optional[str] = None
    level: Optional[int] = None


@app.post("/sessions/{sid}/startup/answer")
def startup_answer(sid: str, body: StartupAnswer, request: Request):
    """The ACC sector's answer to an approach's start-up request (or a change of a clearance before take-off): approve
    with the point where the aircraft leaves the TMA and its cleared level, or refuse (approach asks again later).
    The controller answers it in solo; with a pseudo-pilot, the pseudo-pilot (playing approach) enters it."""
    return _act(sid, request, "startup_answer", body.model_dump())


@app.get("/sessions/{sid}/flightplans")
def flightplans(sid: str):
    """Every flight plan of the session (planned, inbound, on frequency, transferred)."""
    from . import atctools
    s = _session(sid)
    with s.lock:
        return {"utc": atctools.utc(s.world), "flights": atctools.flight_list(s.world)}


@app.get("/sessions/{sid}/flightplans/{callsign}")
def flightplan_one(sid: str, callsign: str):
    """One flight plan: the ICAO FPL message, decoded items, estimates over the points ahead, coordination."""
    from . import atctools
    s = _session(sid)
    with s.lock:
        try:
            return atctools.flight_detail(s.world, callsign)
        except KeyError:
            raise HTTPException(404, f"no flight plan for {callsign}")


@app.post("/sessions/{sid}/release/{callsign}")
def release(sid: str, callsign: str, request: Request):
    """The controller decontrols an aircraft (by hand). It keeps flying, as another sector's traffic."""
    rec = _act(sid, request, "release", {"callsign": callsign.upper()})
    if rec["status"] == "rejected":
        raise HTTPException(400, rec["reason"])
    return rec


@app.post("/sessions/{sid}/assume/{callsign}")
def assume(sid: str, callsign: str, request: Request):
    """The controller assumes an aircraft (on its first call)."""
    rec = _act(sid, request, "assume", {"callsign": callsign.upper()})
    if rec["status"] == "rejected":
        raise HTTPException(400, rec["reason"])
    return rec


@app.get("/sessions/{sid}/instructions")
def instruction_log(sid: str):
    s = _session(sid)
    with s.lock:
        return s.world.instructions


@app.get("/sessions/{sid}/requests")
def requests_(sid: str, status: Optional[str] = None):
    s = _session(sid)
    with s.lock:
        return s.world.requests_list(status)


@app.get("/sessions/{sid}/areas")
def areas(sid: str):
    s = _session(sid)
    with s.lock:
        return [a.snapshot(s.world.t) for a in s.world.areas.values()]


@app.patch("/sessions/{sid}/areas/{area_id}")
def patch_area(sid: str, area_id: str, body: AreaPatch, request: Request):
    """Manually activate/deactivate an area (supervisor action; the published schedule still applies later)."""
    return _act(sid, request, "area", {"area": area_id, "active": body.active})


@app.get("/sessions/{sid}/events")
def events(sid: str, since: int = 0, limit: int = 500):
    """Event log (entries, exits, requests, instructions, separation losses, infringements). Poll with ?since=<last seq>."""
    s = _session(sid)
    with s.lock:
        ev = [e for e in s.world.events if e["seq"] > since]
        return {"sim_time_s": round(s.world.t, 1), "events": ev[:limit],
                "last_seq": ev[-1]["seq"] if ev else since}


@app.get("/sessions/{sid}/metrics")
def metrics(sid: str):
    s = _session(sid)
    with s.lock:
        m = s.world.metrics()
        m["session"] = s.summary()
        return m


# ------------------------------------------------------------------ arrivals / sequencing
class RunwayPatch(BaseModel):
    runway: str


@app.get("/sessions/{sid}/sequence")
def sequence(sid: str):
    """Per sequencing fix: aerodrome, runway in use, approach type, required spacing and crossing level, and the
    inbound arrivals in ETA order with estimated spacing to the one ahead, distance to their top of descent,
    and every crossing so far (spacing / level achieved)."""
    from . import arrivals
    s = _session(sid)
    with s.lock:
        return {"sim_time_s": round(s.world.t, 1), "fixes": arrivals.sequence_status(s.world)}


@app.get("/sessions/{sid}/aerodromes")
def aerodromes(sid: str):
    """Destination aerodromes, runways, runway in use and the approach path from each sequencing fix."""
    from .session import arrivals_description
    s = _session(sid)
    with s.lock:
        return arrivals_description(s.world)


@app.patch("/sessions/{sid}/aerodromes/{ad_id}")
def change_runway(sid: str, ad_id: str, body: RunwayPatch, request: Request):
    """Change the runway in use (supervisor action). Arrivals recompute their top of descent."""
    return _act(sid, request, "runway", {"aerodrome": ad_id, "runway": body.runway})


# ------------------------------------------------------------------ recording / replay
@app.get("/sessions/{sid}/recording")
def recording(sid: str, since: float = Query(-1.0, description="only frames after this sim time"),
              every: float = Query(0.0, ge=0, description="thin to one frame per N s (0 = all, 5 s)")):
    """Recorded traffic for replay: one compact frame per aircraft every 5 sim seconds, aircraft metadata and the
    events worth a marker on a timeline. Frame aircraft rows follow `frame_fields`."""
    s = _session(sid)
    with s.lock:
        return s.world.recording_view(since, every)


@app.get("/sessions/{sid}/recording/export")
def recording_export(sid: str):
    """Whole recording + scenario + airspace as one JSON file (open it later in the radar's replay mode)."""
    from fastapi.responses import JSONResponse
    from .session import sector_description
    s = _session(sid)
    with s.lock:
        d = s.world.recording_view()
        d.update(kind="atcsim-recording", scenario=s.scenario_name, session_id=s.id,
                 sector=sector_description(s.world), areas=[a.snapshot(s.world.t) for a in s.world.areas.values()],
                 metrics=s.world.metrics())
    return JSONResponse(d, headers={"Content-Disposition":
                                    f'attachment; filename="recording_{s.scenario_name}_{s.id}.json"'})


# ------------------------------------------------------------------ weather
def _now(s) -> float:
    with s.lock:
        return s.world.t, s.world.env.weather


def _bbox(bbox: Optional[str], sid: Optional[str] = None):
    if not bbox:
        if sid is not None:
            b = _session(sid).world.airspace.bbox
            if b[2] - b[0] > 200 or b[3] - b[1] > 200:
                return (b[0] - 30.0, b[1] - 30.0, b[2] + 30.0, b[3] + 30.0)
        return (-100.0, -100.0, 100.0, 100.0)
    v = [float(a) for a in bbox.split(",")]
    if len(v) != 4 or v[2] <= v[0] or v[3] <= v[1]:
        raise HTTPException(400, "bbox = xmin,ymin,xmax,ymax (NM)")
    return tuple(v)


@app.get("/sessions/{sid}/weather")
def weather(sid: str, at_s: Optional[float] = Query(None, description="sim time (s); default now - for replay")):
    """Weather briefing: temperature profile, freezing level, tropopause, wind profile, jet, storm motion,
    every active convective cell (position, motion, echo top, max dBZ, 30/45 dBZ radii, life-cycle stage)."""
    t, wx = _now(_session(sid))
    t = t if at_s is None else float(at_s)
    d = wx.summary(t)
    d["sim_time_s"] = round(t, 1)
    return d


@app.get("/sessions/{sid}/weather/radar")
def weather_radar(sid: str, product: Literal["cappi", "composite", "echo_tops", "edr", "temperature"] = "composite",
                  fl: float = Query(350, ge=0, le=800), res_nm: float = Query(2.0, ge=0.5, le=10),
                  bbox: Optional[str] = None, encoding: Literal["b64", "json"] = "b64",
                  at_s: Optional[float] = Query(None, description="sim time (s); default now - for replay")):
    """Gridded weather product. Rows go south->north, columns west->east, cell centres at
    bbox_min + (i + 0.5) * res_nm.
      cappi      : reflectivity (dBZ) at `fl`        composite : column-maximum reflectivity (dBZ)
      echo_tops  : highest FL with >= 18 dBZ         edr       : turbulence (EDR) at `fl`
    encoding=b64 -> uint8 base64 (row-major), decode: cappi/composite dBZ = v/2 - 10 (v=0: no echo);
    echo_tops FL = v*5; edr = v/250."""
    import base64

    import numpy as np
    t, wx = _now(_session(sid))
    t = t if at_s is None else float(at_s)
    bb = _bbox(bbox, sid)
    if product == "cappi":
        xs, ys, z = wx.cappi(fl, t, bb, res_nm)
        enc = np.where(z > -9.9, np.clip((z + 10) * 2, 1, 255), 0)
        scale = "dbz = v/2 - 10, v=0 no echo"
    elif product in ("composite", "echo_tops"):
        v = wx.volume(t, bb, max(res_nm, 2.0), 20)
        xs, ys = v["xs"], v["ys"]
        if product == "composite":
            z = v["composite"]
            enc = np.where(z > -9.9, np.clip((z + 10) * 2, 1, 255), 0)
            scale = "dbz = v/2 - 10, v=0 no echo"
        else:
            z = v["echo_tops"]
            enc = np.clip(z / 5.0, 0, 255)
            scale = "fl = v*5, v=0 no echo"
    elif product == "edr":
        xs, ys, X, Y = wx.grid(bb, res_nm)
        z = wx.edr(X, Y, fl, t)
        enc = np.clip(z * 250, 0, 255)
        scale = "edr = v/250"
    else:
        xs, ys, X, Y = wx.grid(bb, res_nm)
        z = np.full(X.shape, float(wx.temperature_c(fl)))
        enc = np.clip(z + 100, 0, 255)
        scale = "temp_c = v - 100"
    out = {"product": product, "fl": fl if product in ("cappi", "edr", "temperature") else None,
           "sim_time_s": round(t, 1), "bbox": list(bb), "res_nm": float(xs[1] - xs[0]) if len(xs) > 1 else res_nm,
           "nx": len(xs), "ny": len(ys), "scale": scale}
    if encoding == "b64":
        out["encoding"] = "uint8_b64"
        out["data"] = base64.b64encode(np.asarray(enc, np.uint8).tobytes()).decode()
    else:
        out["encoding"] = "json"
        out["data"] = np.round(np.where(np.asarray(z) > -9.9, z, np.nan), 2).tolist()
    return out


@app.get("/sessions/{sid}/weather/hazards")
def weather_hazards(sid: str, fl: float = Query(350, ge=0, le=800), threshold_dbz: float = 30.0):
    """Polygons of the areas at `fl` where reflectivity >= threshold (what an agent should keep clear of)."""
    t, wx = _now(_session(sid))
    return {"sim_time_s": round(t, 1), "fl": fl, "threshold_dbz": threshold_dbz,
            "pilot_margins": wx.pilot, "areas": wx.hazards(fl, t, threshold_dbz)}


@app.get("/sessions/{sid}/weather/column")
def weather_column(sid: str, x: float, y: float, fl_step: int = Query(10, ge=5, le=100)):
    """Vertical profile GND-FL800 at a point: reflectivity, wind, temperature, turbulence."""
    t, wx = _now(_session(sid))
    return {"sim_time_s": round(t, 1), "x_nm": x, "y_nm": y, "levels": wx.column(x, y, t, fl_step)}


@app.get("/sessions/{sid}/weather/wind")
def weather_wind(sid: str, fl: float = Query(350, ge=0, le=800), res_nm: float = Query(15, ge=2, le=60),
                 bbox: Optional[str] = None):
    """Wind vectors on a grid at a flight level."""
    from .weather import describe_uv
    t, wx = _now(_session(sid))
    xs, ys, X, Y = wx.grid(_bbox(bbox, sid), res_nm)
    u, v = wx.wind_at(X, Y, fl, t)
    pts = [{"x_nm": round(float(a), 1), "y_nm": round(float(b), 1), **describe_uv(float(uu), float(vv))}
           for a, b, uu, vv in zip(X.ravel(), Y.ravel(), u.ravel(), v.ravel())]
    return {"sim_time_s": round(t, 1), "fl": fl, "vectors": pts}


# ------------------------------------------------------------------ live stream
@app.websocket("/sessions/{sid}/stream")
async def stream(ws: WebSocket, sid: str, separation: bool = True, trails: bool = True, lookahead_s: float = 600,
                 since: int = 0, client: str = "stream", sweep_s: Optional[float] = None):
    """Push stream of {"type": "tick", "state": {...}, "events": [...new events...], "separation": {...}}.
    What goes out, and when (one message carries everything; nothing waits behind anything else):
      - at once (within 0.15 s): new events (read-backs, requests, CPDLC, coordination, calls, alerts...) and every
        action on the session (clearances, entries, assume...), with the system data they change;
      - the radar picture: once per sweep (sweep_s seconds of simulation time; the radar screens ask for
        ATC_RADAR_SWEEP_S, 4 s by default): positions, levels, speeds and trails as painted at the sweep;
        sweep_s=0 (the default for other clients): every new simulation second, live;
      - the conflict look-ahead (separation): attached to the next message as soon as it is computed.
    Voice goes over its own socket (/sessions/{id}/comms), never behind the picture.
    The client may send {"op": "instruction", ...instruction fields} or {"op": "command", "text": "..."} or
    {"op": "control", "action": "pause"}; each gets {"type": "ack", ...} back."""
    if sweep_s is None:
        sweep_s = pconfig.RADAR_SWEEP_S if client == "radar" else 0.0
    sweep_s = max(0.0, min(float(sweep_s), 30.0))
    try:
        s = manager.get(sid)
    except KeyError:
        await ws.close(code=4404)
        return
    seat = seat_for(sid, pauth.current_user(ws) or pauth.LOCAL_ADMIN) if pconfig.AUTH != "off" else "instructor"
    await ws.accept()
    with s.lock:
        s.clients.append({"client": client, "sim_time_s": s.world.t})
    last_t, last_seq = -1.0, since
    loop = asyncio.get_running_loop()

    latest_sep = {"msg": None}

    def build():
        with s.lock:
            w = s.world
            st = w.state(trails, sweep_s or None)
            st["session"] = s.summary()
            ev = [e for e in w.events if e["seq"] > last_seq]
            return w.t, {"type": "tick", "state": st, "events": ev}

    def handle(op: dict):
        kind = op.pop("op", "instruction")
        multi = bool(getattr(s, "multi", False))
        if kind == "control" and not (seat in ("instructor", "pilot") or getattr(s, "solo", False)):
            raise PermissionError("simulation control is for the instructor")
        from . import inputs
        actor = {"name": seat or "atc", "seat": seat if multi else "instructor"}
        if seat == "atc" and multi:
            if kind == "entry":
                return inputs.run(s, "entries", {"callsign": op.pop("callsign", ""), **op}, actor)
            if kind == "instruction" and op.get("type") in ("assume", "release"):
                return inputs.run(s, op["type"], {"callsign": op.get("callsign", "")}, actor)
            raise PermissionError("the controller's seat enters clearances in the labels; the pilot executes them")
        if kind == "command":
            return inputs.run(s, "command", {"text": op.get("text", "")}, actor)
        if kind == "control":
            return s.control(op.get("action", ""), seconds=op.get("seconds", 1), time_factor=op.get("time_factor"))
        if kind == "entry":
            return inputs.run(s, "entries", {"callsign": op.pop("callsign", ""), **op}, actor)
        return inputs.run(s, "instructions", {"items": [op]}, actor)

    send_lock = asyncio.Lock()

    async def send(obj):
        async with send_lock:
            await ws.send_json(obj)

    async def sender():
        # a message as soon as there is something new: an event or an action at once, the picture once per sweep
        # (or every simulation second), the separation look-ahead (computed by separator() in parallel) when ready -
        # a slow prediction never delays the rest
        nonlocal last_t, last_seq
        seen_rw = getattr(s, "rewinds", 0)
        seen_ver = getattr(s, "version", 0)
        while True:
            try:
                if getattr(s, "rewinds", 0) != seen_rw:     # rewound / restarted / seeked: the event log starts again
                    ev = s.world.events                       # (its recent part only: a debrief seeks often)
                    seen_rw, last_t = s.rewinds, -1.0
                    last_seq = ev[-80]["seq"] if len(ev) > 80 else 0
                sep_msg = latest_sep["msg"]
                ver = getattr(s, "version", 0)            # an action on the session (clearance, entry, assume...)
                w = s.world
                pic = math.floor(w.t / sweep_s + 1e-9) * sweep_s if sweep_s else w.t      # the picture's time
                evs = w.events
                new_ev = bool(evs) and evs[-1]["seq"] > last_seq
                if pic != last_t or new_ev or sep_msg is not None or ver != seen_ver:
                    seen_ver = ver
                    t, msg = await run_in_threadpool(build)
                    t = math.floor(t / sweep_s + 1e-9) * sweep_s if sweep_s else t
                    if sep_msg is not None:
                        msg["separation"] = sep_msg
                        latest_sep["msg"] = None
                    last_t = t
                    if msg["events"]:
                        last_seq = msg["events"][-1]["seq"]
                    await send(msg)
            except (WebSocketDisconnect, RuntimeError):
                raise
            except Exception as e:  # noqa: BLE001 - keep streaming; report the problem to the client
                await send({"type": "error", "error": f"{type(e).__name__}: {e}"})
            await asyncio.sleep(0.15)

    async def separator():
        # the look-ahead is shared by every page on this session and rerun only every few seconds
        # (Session.separation_shared); a new result is pushed with the next tick
        last = None
        while separation:
            try:
                res = await run_in_threadpool(s.separation_shared, lookahead_s)
                if res is not None and res is not last:
                    last = res
                    latest_sep["msg"] = res
            except Exception as e:  # noqa: BLE001
                await send({"type": "error", "error": f"separation: {type(e).__name__}: {e}"})
            await asyncio.sleep(1.0)

    async def receiver():
        while True:
            op = await ws.receive_json()
            try:
                res = await run_in_threadpool(handle, dict(op))
                await send({"type": "ack", "ok": True, "result": res})
            except Exception as e:  # noqa: BLE001
                await send({"type": "ack", "ok": False, "error": str(e)})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver()), asyncio.create_task(separator())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
