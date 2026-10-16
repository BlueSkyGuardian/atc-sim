"""HTTP API (FastAPI). Interactive docs at /docs once the server runs."""
from __future__ import annotations

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
from .platform import auth as pauth, exercises as pex, comms as pcomms, recorder as prec
from .platform.access import AccessMiddleware, seat_for

if pconfig.AUTH == "off":
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.add_middleware(AccessMiddleware)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
manager = SessionManager()
pex.manager = manager
pcomms.manager = manager
prec.manager = manager
app.include_router(pauth.router)
app.include_router(pex.router)
app.include_router(pcomms.router)
app.include_router(prec.router)


@app.on_event("startup")
def _startup():
    prec.recover()


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
    type: Literal["altitude", "heading", "direct", "speed", "resume", "reply", "deviation", "assume"]
    fl: Optional[int] = Field(None, description="altitude: target flight level, e.g. 360")
    rate_fpm: Optional[float] = Field(None, description="altitude: optional vertical rate")
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


class Command(BaseModel):
    text: str = Field(..., examples=["RAM101 FL370 H250"])


class Control(BaseModel):
    action: Literal["start", "resume", "pause", "set_speed", "set_mode", "step", "restart"]
    seconds: float = 1.0
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
def control(sid: str, body: Control):
    s = _session(sid)
    try:
        return s.control(body.action, seconds=body.seconds, time_factor=body.time_factor, mode=body.mode)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/sessions/{sid}/state")
def state(sid: str, trails: bool = False):
    """Full snapshot: aircraft, inbound traffic, pending requests, pending instructions, areas, current losses."""
    s = _session(sid)
    with s.lock:
        st = s.world.state(trails)
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
        if a.active:
            d["performance"] = a.performance_snapshot()
        return d


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
    pilot = _pilot_exec(s, request)
    if s.multi and seat_for(sid, _user(request)) == "pilot" and any(i.type == "assume" for i in items):
        raise HTTPException(403, "the controller assumes aircraft (label or double-click), not the pilot")
    with s.lock:
        if s.status == "finished":
            raise HTTPException(409, "scenario finished")
        return [s.world.issue(_clean(i.model_dump()), source="pilot" if pilot else "api", by_pilot=pilot)
                for i in items]


@app.post("/sessions/{sid}/command")
def command(sid: str, body: Command, request: Request):
    """Free-text controller shorthand, e.g. 'RAM101 FL370 H250', 'AFR22 D S3', 'IBE5 UNA'."""
    s = _session(sid)
    pilot = _pilot_exec(s, request)
    with s.lock:
        try:
            parsed = parse_command(body.text, s.world)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if s.multi and seat_for(sid, _user(request)) == "pilot" and any(p.get("type") == "assume" for p in parsed):
            raise HTTPException(403, "the controller assumes aircraft, not the pilot")
        return [s.world.issue(p, source="pilot" if pilot else "command", by_pilot=pilot) for p in parsed]


class Entry(BaseModel):
    callsign: str
    cfl: Optional[Union[int, str]] = None
    speed: Optional[str] = None
    heading: Optional[Union[int, str]] = None
    direct: Optional[str] = None
    note: Optional[str] = None


@app.post("/sessions/{sid}/entries")
def entries(sid: str, body: Entry, request: Request):
    """Controller's label input (connected mode): what was cleared, as entered in the system. The aircraft does
    not move until the pseudo-pilot executes the clearance given on the frequency. Send "" to clear a field."""
    s = _session(sid)
    from .world import InstructionError
    fields = {k: v for k, v in body.model_dump().items() if k != "callsign" and k in body.model_fields_set}
    u = _user(request)
    with s.lock:
        try:
            return s.world.enter(body.callsign, fields, who=pex.db.public_user(u)["name"] if u["id"] else "atc")
        except (InstructionError, ValueError) as e:
            raise HTTPException(400, str(e))


@app.post("/sessions/{sid}/assume/{callsign}")
def assume(sid: str, callsign: str):
    """The controller assumes an aircraft (on its first call)."""
    s = _session(sid)
    with s.lock:
        rec = s.world.issue({"callsign": callsign.upper(), "type": "assume"}, source="atc")
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
def patch_area(sid: str, area_id: str, body: AreaPatch):
    """Manually activate/deactivate an area (supervisor action; the published schedule still applies later)."""
    s = _session(sid)
    with s.lock:
        a = s.world.areas.get(area_id)
        if a is None:
            raise HTTPException(404, "unknown area")
        if a.active != body.active:
            a.active = body.active
            s.world._event("area_activated" if body.active else "area_deactivated", area_id=a.id, name=a.name,
                           kind=a.kind, manual=True)
        return a.snapshot(s.world.t)


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
def change_runway(sid: str, ad_id: str, body: RunwayPatch):
    """Change the runway in use (supervisor action). Arrivals recompute their top of descent."""
    s = _session(sid)
    with s.lock:
        ad = s.world.aerodromes.get(ad_id)
        if ad is None:
            raise HTTPException(404, "unknown aerodrome")
        if body.runway not in ad.runways:
            raise HTTPException(400, f"runways: {list(ad.runways)}")
        if ad.in_use != body.runway:
            ad.in_use = body.runway
            s.world._event("runway_change", aerodrome=ad.id, runway=body.runway, manual=True,
                           text=f"{ad.id} runway in use now {body.runway}")
            for a in s.world.aircraft.values():
                a.arr.pop("_last", None)
        return ad.snapshot()


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
                 since: int = 0, client: str = "stream"):
    """Push stream. Every new sim second the server sends
         {"type": "tick", "state": {...}, "events": [...new events...], "separation": {...} (every 2 s wall time)}
    The client may send {"op": "instruction", ...instruction fields} or {"op": "command", "text": "..."} or
    {"op": "control", "action": "pause"}; each gets {"type": "ack", ...} back."""
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
            st = w.state(trails)
            st["session"] = s.summary()
            ev = [e for e in w.events if e["seq"] > last_seq]
            return w.t, {"type": "tick", "state": st, "events": ev}

    def handle(op: dict):
        kind = op.pop("op", "instruction")
        multi = bool(getattr(s, "multi", False))
        pilot = multi and seat in ("pilot", "instructor")
        if kind == "control" and seat != "instructor":
            raise PermissionError("simulation control is for the instructor")
        if seat == "atc" and multi:
            if kind == "entry":
                with s.lock:
                    return s.world.enter(op.pop("callsign", ""), op, who="atc")
            if kind == "instruction" and op.get("type") == "assume":
                with s.lock:
                    return s.world.issue(op, source="atc")
            raise PermissionError("the controller's seat enters clearances in the labels; the pilot executes them")
        with s.lock:
            if kind == "command":
                return [s.world.issue(p, source="stream", by_pilot=pilot)
                        for p in parse_command(op.get("text", ""), s.world)]
            if kind == "control":
                return s.control(op.get("action", ""), seconds=op.get("seconds", 1), time_factor=op.get("time_factor"))
            if kind == "entry":
                return s.world.enter(op.pop("callsign", ""), op, who=seat or "atc")
            return s.world.issue(op, source="stream", by_pilot=pilot)

    send_lock = asyncio.Lock()

    async def send(obj):
        async with send_lock:
            await ws.send_json(obj)

    async def sender():
        # state every new sim second (cheap); the separation look-ahead is computed by separator() in parallel and
        # attached to the next tick when ready, so a slow prediction never delays the picture
        nonlocal last_t, last_seq
        while True:
            try:
                sep_msg = latest_sep["msg"]
                if s.world.t != last_t or sep_msg is not None:
                    t, msg = await run_in_threadpool(build)
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
