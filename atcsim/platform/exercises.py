"""Scenarios (ownership, sharing) and exercises (a simulator session with an instructor, controllers and
pseudo-pilots), invitations, the instructor's dashboard data, and the pages."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from ..scenario import SCENARIO_DIR, ScenarioError, list_scenarios
from . import config, db
from .access import seat_for
from .auth import require_user

router = APIRouter()
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
manager = None                      # set by server.py (the SessionManager)


def page(name: str) -> FileResponse:
    return FileResponse(os.path.join(STATIC, name))


# ------------------------------------------------------------------ pages
@router.get("/login", include_in_schema=False)
def login_page():
    return page("login.html")


@router.get("/", include_in_schema=False)
def home_page():
    return page("home.html")


@router.get("/profile", include_in_schema=False)
def profile_page():
    return page("profile.html")


@router.get("/admin", include_in_schema=False)
def admin_page():
    return page("admin.html")


DOCS_DIR = os.path.join(os.path.dirname(STATIC), "docs")
DOC_PAGES = [  # (slug, title, group)
    ("index", "Overview", "Start"),
    ("controller", "Controller manual", "User manuals"),
    ("pilot", "Pseudo-pilot manual", "User manuals"),
    ("instructor", "Instructor and admin manual", "User manuals"),
    ("designer", "Scenario designer", "Scenarios"),
    ("scenario-json", "Scenario JSON reference", "Scenarios"),
    ("ai", "AI lab: models controlling the simulation", "Scenarios"),
    ("scenarios", "Scenario library and progression", "Scenarios"),
    ("weather", "Weather modelling", "Models"),
    ("aircraft", "Aircraft, pilots and airspace", "Models"),
    ("api", "API reference", "Developers"),
]


_LANDING = None


@router.get("/api/landing", include_in_schema=False)
def landing_geometry():
    """Public: the Agadir CTA drawn behind the sign-in page (outline, sector lines, routes, points)."""
    global _LANDING
    if _LANDING is None:
        from .. import airspace as asp_mod
        a = asp_mod.get("agadir")
        d = a.describe()
        rnd = lambda p: [round(p[0], 1), round(p[1], 1)]
        wps = {n: rnd(p) for n, p in a.waypoints.items()
               if not n.startswith("@") and not a.point_info.get(n, {}).get("external")}
        routes = [v["points"] for v in a.airways.values()] + (a.notes.get("rad_routes") or []) + \
                 (a.notes.get("standard_routes") or [])
        _LANDING = {"outline": [rnd(p) for p in d["outline"]],
                    "sectors": [{"id": s["id"], "name": s["name"], "polygon": [rnd(p) for p in s["polygon"]]}
                                for s in d["sectors"]],
                    "airways": [{"points": v["points"], "one_way": bool(v.get("one_way"))} for v in a.airways.values()],
                    "rad": a.notes.get("rad_routes") or [],
                    "routes": [[n for n in r if n in wps] for r in routes],
                    "points": wps}
        xs, ys = [p[0] for p in d["outline"]], [p[1] for p in d["outline"]]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        c = a.coast_clipped((cx - 900, cy - 900, cx + 900, cy + 900))   # the scene's fog hides what is farther
        if c:
            _LANDING["coast"] = c
    return _LANDING


@router.get("/help", include_in_schema=False)
@router.get("/help/{slug}", include_in_schema=False)
def help_page(request: Request, slug: str = "index"):
    return page("help.html")


@router.get("/api/help")
def help_index(request: Request):
    """The documentation pages (for the /help reader)."""
    return [{"slug": s, "title": t, "group": g} for s, t, g in DOC_PAGES]


@router.get("/api/help/scenario.schema.json")
def help_schema():
    """JSON Schema of a scenario file."""
    from .. import docs_gen
    return docs_gen.SCHEMA


@router.get("/api/help/reference.json")
def help_reference():
    """Reference data for writing scenarios: airspaces (points, routes, sectors, capacities, aerodromes), types."""
    from .. import docs_gen
    return docs_gen.reference()


@router.get("/api/help/{slug}")
def help_doc(slug: str, request: Request):
    """One documentation page, as Markdown."""
    if slug not in {s for s, _, _ in DOC_PAGES}:
        raise HTTPException(404, "no such page")
    with open(os.path.join(DOCS_DIR, slug + ".md"), encoding="utf-8") as f:
        md = f.read()
    if "<!-- REFERENCE -->" in md:                 # generated tables: points, routes, types
        from .. import docs_gen
        md = md.replace("<!-- REFERENCE -->", docs_gen.reference_md())
    return {"slug": slug, "markdown": md}


@router.get("/debrief/{eid}", include_in_schema=False)
def debrief_page(eid: str, request: Request, join: Optional[str] = None):
    """The debrief on the simulator screen (radar page in debrief mode). ?join=<token>: its join link (the person
    signed in becomes a guest of the debrief)."""
    try:
        u = require_user(request)
    except HTTPException:
        from urllib.parse import quote
        return RedirectResponse("/login?next=" + quote(str(request.url.path) + (f"?join={join}" if join else "")), 303)
    if join and seat_for(eid, u) is None:
        from . import debriefs
        debriefs.join_by_token(eid, u, join)
    if seat_for(eid, u) is None:
        raise HTTPException(403, "you are not a member of this exercise")
    return page("radar.html")


@router.get("/debrief/{eid}/report", include_in_schema=False)
def debrief_report_page(eid: str, request: Request):
    u = require_user(request)
    if seat_for(eid, u) is None:
        raise HTTPException(403, "you are not a member of this exercise")
    return page("debrief.html")


@router.get("/api/exercises/{eid}/debrief")
def debrief_data(eid: str, request: Request):
    """The debrief of an ended exercise: workload, losses with the clearances before them, unanswered requests,
    read-backs, infringements, live actions, bookmarks. For a live exercise: built now."""
    u = require_user(request)
    if seat_for(eid, u) is None:
        raise HTTPException(403, "you are not a member of this exercise")
    e = db.exercise(eid)
    if e is None:
        raise HTTPException(404, "no such exercise")
    s = manager.sessions.get(eid)
    if s is not None:
        from .. import debrief
        with s.lock:
            d = debrief.build(s.world)
        live = True
    elif e.get("debrief"):
        d, live = json.loads(e["debrief"]), False
    else:
        raise HTTPException(404, "no debrief for this exercise (ended before debriefs existed, or after a restart)")
    out = _summary(e, u)
    out.update(debrief=d, live_now=live, metrics=json.loads(e["metrics"]) if e.get("metrics") else None)
    return out


@router.get("/solo", include_in_schema=False)
def solo_page(request: Request):
    require_user(request)
    return page("solo.html")


@router.get("/radar", include_in_schema=False)
def radar_page(request: Request):
    require_user(request, roles=("instructor",))
    return page("radar.html")


@router.get("/x/{eid}", include_in_schema=False)
def exercise_page(eid: str, request: Request):
    u = require_user(request)
    if seat_for(eid, u) is None:
        raise HTTPException(403, "you are not a member of this exercise")
    return page("radar.html")


@router.get("/join/{token}", include_in_schema=False)
def join(token: str, request: Request):
    from .auth import current_user
    e = (db.q1("SELECT *, 'atc' AS seat FROM exercises WHERE atc_token=?", (token,)) or
         db.q1("SELECT *, 'pilot' AS seat FROM exercises WHERE pilot_token=?", (token,)) or
         db.q1("SELECT *, 'instructor' AS seat FROM exercises WHERE observer_token=?", (token,)))
    if e is None:
        raise HTTPException(404, "this invitation link is not valid (any more)")
    u = current_user(request)
    if u is None:
        return RedirectResponse(f"/login?next=/join/{token}", status_code=303)
    if seat_for(e["id"], u) is None:
        db.add_member(e["id"], u["id"], e["seat"])
    return RedirectResponse(f"/x/{e['id']}", status_code=303)


# ------------------------------------------------------------------ scenarios
def _scn_meta(name: str) -> dict:
    m = db.q1("SELECT s.*, u.email, u.first_name, u.last_name FROM scenario_meta s LEFT JOIN users u ON u.id=s.owner_id "
              "WHERE s.name=?", (name,))
    if m is None or m["owner_id"] is None:
        return {"owner": None, "library": True}
    return {"owner": {"id": m["owner_id"], "email": m["email"], "name": f"{m['first_name']} {m['last_name']}".strip()},
            "library": False}


@router.get("/api/scenarios")
def scenarios(request: Request):
    u = require_user(request, roles=("instructor",))
    out = []
    for s in list_scenarios():
        acc = db.scenario_access(s["name"], u)
        if acc is None:
            continue
        meta = _scn_meta(s["name"])
        if not s.get("author"):
            s["author"] = meta["owner"]["name"] if meta["owner"] else None
        out.append({**s, "access": acc, **meta,
                    "shares": db.shares(s["name"]) if (meta["owner"] and meta["owner"]["id"] == u["id"])
                    or u["role"] == "admin" else []})
    return out


class Share(BaseModel):
    user_id: int
    perm: str = "read"


@router.post("/api/scenarios/{name}/shares")
def share(name: str, body: Share, request: Request):
    u = require_user(request, roles=("instructor",))
    meta = _scn_meta(name)
    if meta["library"] or (meta["owner"]["id"] != u["id"] and u["role"] != "admin"):
        raise HTTPException(403, "only the owner shares a scenario (library scenarios: make a copy first)")
    if body.perm not in ("read", "write"):
        raise HTTPException(400, "perm: read or write")
    t = db.user_by_id(body.user_id)
    if t is None or t["role"] not in ("instructor", "admin"):
        raise HTTPException(400, "scenarios are shared with instructors")
    db.x("INSERT OR REPLACE INTO scenario_shares (name, user_id, perm) VALUES (?,?,?)", (name, body.user_id, body.perm))
    return db.shares(name)


@router.delete("/api/scenarios/{name}/shares/{uid}")
def unshare(name: str, uid: int, request: Request):
    u = require_user(request, roles=("instructor",))
    meta = _scn_meta(name)
    if meta["library"] or (meta["owner"]["id"] != u["id"] and u["role"] != "admin"):
        raise HTTPException(403, "only the owner changes sharing")
    db.x("DELETE FROM scenario_shares WHERE name=? AND user_id=?", (name, uid))
    return db.shares(name)


class Copy(BaseModel):
    new_name: str


@router.post("/api/scenarios/{name}/copy")
def copy_scenario(name: str, body: Copy, request: Request):
    from ..designer import NAME_RE
    u = require_user(request, roles=("instructor",))
    if db.scenario_access(name, u) is None:
        raise HTTPException(403, "no access to this scenario")
    new = body.new_name.strip()
    if not NAME_RE.match(new):
        raise HTTPException(400, "name: lower-case letters, digits, '_' or '-'")
    dst = os.path.join(SCENARIO_DIR, new + ".json")
    if os.path.exists(dst):
        raise HTTPException(409, "a scenario with that name exists")
    with open(os.path.join(SCENARIO_DIR, name + ".json")) as f:
        d = json.load(f)
    d["name"] = new
    with open(dst, "w") as f:
        json.dump(d, f, indent=1)
    db.set_owner(new, u["id"])
    return {"copied": new}


@router.delete("/api/scenarios/{name}")
def delete_scenario(name: str, request: Request):
    u = require_user(request, roles=("instructor",))
    meta = _scn_meta(name)
    if meta["library"] or (meta["owner"]["id"] != u["id"] and u["role"] != "admin"):
        raise HTTPException(403, "only the owner deletes a scenario; library scenarios cannot be deleted")
    path = os.path.join(SCENARIO_DIR, name + ".json")
    if os.path.exists(path):
        os.makedirs(os.path.join(config.DATA_DIR, "deleted_scenarios"), exist_ok=True)
        shutil.move(path, os.path.join(config.DATA_DIR, "deleted_scenarios", f"{name}.{int(time.time())}.json"))
    db.x("DELETE FROM scenario_meta WHERE name=?", (name,))
    db.x("DELETE FROM scenario_shares WHERE name=?", (name,))
    return {"deleted": name}


def check_scenario_write(name: str, u: dict) -> None:
    """Designer save: a new name becomes the user's scenario; an existing one needs write access."""
    if os.path.exists(os.path.join(SCENARIO_DIR, name + ".json")):
        acc = db.scenario_access(name, u)
        if acc != "write":
            raise HTTPException(403, "read-only scenario: save it under a new name (a copy)")
    else:
        db.set_owner(name, u["id"])


# ------------------------------------------------------------------ exercises
class NewExercise(BaseModel):
    scenario: str
    name: Optional[str] = None
    time_factor: float = 1.0


def _summary(e: dict, u: dict, with_picture: bool = False) -> dict:
    s = manager.sessions.get(e["id"]) if manager else None
    out = {"id": e["id"], "name": e["name"], "scenario": e["scenario"], "created_at": e["created_at"],
           "ended_at": e["ended_at"], "seat": seat_for(e["id"], u), "members": db.members(e["id"]),
           "instructor": db.public_user(db.user_by_id(e["instructor_id"]) or {
               "id": 0, "email": "?", "first_name": "", "last_name": "", "licence": "", "role": "", "active": 0})["name"],
           "live": s is not None, "scenario_info": db.scenario_info(e), "solo": bool(e.get("solo"))}
    from . import comms
    room = comms.hub.rooms.get(e["id"])
    out["online"] = [{"name": c.name, "seat": c.seat, "ptt": c.ptt} for c in room.conns.values()] if room else []
    out["calls"] = len(room.calls) if room else 0
    if s is None:
        out["status"] = "ended" if e["ended_at"] else "closed (server restarted)"
        return out
    with s.lock:
        w = s.world
        losses = [p["aircraft"] for p in w.current_pairs() if p["loss_of_separation"]]
        out.update(status=s.status, sim_time_s=round(w.t), duration_s=w.duration_s, time_factor=s.time_factor,
                   losses=losses, aircraft=len(w.active_aircraft()),
                   calling=sum(1 for a in w.active_aircraft() if a.on_frequency and not a.assumed),
                   pending_requests=sum(1 for r in w.requests.values() if r.status == "pending"))
        if with_picture:
            out["picture"] = [[a.callsign, round(a.x, 1), round(a.y, 1), int(round(a.alt_ft / 100)),
                               1 if a.assumed else (0 if a.on_frequency else -1), int(round(a.gs)),
                               int(round(a.trk)), int(round(a.vs_fpm, -2))] for a in w.active_aircraft()]
            out["sep_h"] = w.sep_h
    return out


@router.get("/api/exercises")
def exercises(request: Request, pictures: bool = False, live: bool = False, limit: int = 200):
    """Exercises this user ran or joined, newest first. live=true returns only the running ones (cheap, for polling);
    limit (max 1000) widens the history for the dashboard search."""
    u = require_user(request)
    limit = max(1, min(int(limit), 1000))
    if u["role"] == "admin":
        rows = db.q("SELECT * FROM exercises ORDER BY created_at DESC LIMIT ?", (limit,))
    elif u["role"] == "instructor":
        rows = db.q("SELECT * FROM exercises WHERE instructor_id=? OR id IN (SELECT exercise_id FROM exercise_members "
                    "WHERE user_id=?) ORDER BY created_at DESC LIMIT ?", (u["id"], u["id"], limit))
    else:
        rows = db.q("SELECT e.* FROM exercises e JOIN exercise_members m ON m.exercise_id=e.id WHERE m.user_id=? "
                    "ORDER BY e.created_at DESC LIMIT ?", (u["id"], limit))
    rows = [e for e in rows if not e.get("ai")]          # AI lab runs: on the AI lab page
    if live:
        rows = [e for e in rows if manager and e["id"] in manager.sessions]
    return [_summary(e, u, with_picture=pictures and seat_for(e["id"], u) == "instructor") for e in rows]


@router.get("/api/exercises/{eid}/outline")
def outline(eid: str, request: Request):
    """Airspace outline and sector lines for the dashboard thumbnails (static; fetched once per exercise)."""
    u = require_user(request)
    if seat_for(eid, u) is None:
        raise HTTPException(403, "not a member")
    s = manager.sessions.get(eid)
    if s is None:
        raise HTTPException(404, "exercise not running")
    a = s.world.airspace
    d = a.describe()
    b = d["bbox_nm"]
    c = a.coast_clipped((b[0] - 80, b[1] - 80, b[2] + 80, b[3] + 80))
    return {"outline": d["outline"], "controlled": d["shape"]["polygon"], "bbox": d["bbox_nm"],
            "sectors": [sc["polygon"] for sc in d["sectors"]] if len(d["sectors"]) > 1 else [],
            "coast": c["lines"] if c else [], "sep_h": s.world.sep_h}


@router.post("/api/exercises")
def create_exercise(body: NewExercise, request: Request):
    u = require_user(request, roles=("instructor",))
    if db.scenario_access(body.scenario, u) is None:
        raise HTTPException(403, "no access to this scenario")
    try:
        s = manager.create(scenario_name=body.scenario, mode="realtime", time_factor=body.time_factor,
                           autostart=False, label=body.name or body.scenario)
    except (ScenarioError, ValueError) as e:
        raise HTTPException(400, str(e))
    register(s, u, body.name or body.scenario, multi=True)
    return _summary(db.exercise(s.id), u)


def register(s, u: dict, name: str, multi: bool = True, solo: bool = False) -> None:
    """Record a simulator session as an exercise of this instructor; start its radio/phone room and recording.
    solo: the solo radar - the user sits as the controller, simulated pilots fly what is entered."""
    from . import recorder
    if solo:
        s.solo = True
    else:
        s.multi = multi
    db.x("INSERT INTO exercises (id, name, scenario, instructor_id, created_at, atc_token, pilot_token, observer_token, "
         "scn_info, multi, solo) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (s.id, name, s.scenario_name, u["id"], time.time(),
                                                 secrets.token_urlsafe(12), secrets.token_urlsafe(12),
                                                 secrets.token_urlsafe(12), db.dumps(db.scenario_snapshot(s.scenario_name)),
                                                 0 if solo or not multi else 1, 1 if solo else 0))
    if solo:
        db.add_member(s.id, u["id"], "atc")
    if multi or solo:
        recorder.start(s.id, s, name)
        _keep(s)


def _keep(s) -> None:
    """The exercise survives a server restart: its snapshots go to the data folder (data/live/<id>)."""
    with s.lock:
        s.persist_dir = os.path.join(config.DATA_DIR, "live", s.id)
        s._persist(rewrite=True)


def restore_live() -> list:
    """At startup: the exercises that were running when the server stopped come back, paused at their last
    snapshot (at most 10 simulated seconds back), with their recording resumed."""
    from . import recorder
    from ..session import Session
    root = os.path.join(config.DATA_DIR, "live")
    out = []
    if not os.path.isdir(root):
        return out
    for sid in sorted(os.listdir(root)):
        d = os.path.join(root, sid)
        e = db.exercise(sid)
        if e is None or e["ended_at"]:
            shutil.rmtree(d, ignore_errors=True)
            continue
        try:
            s = Session.restore(d)
        except Exception as ex:  # noqa: BLE001 - a broken save must not stop the server
            print(f"exercise {sid}: could not be restored ({type(ex).__name__}: {ex})")
            continue
        with manager.lock:
            manager.sessions[s.id] = s
        recorder.start(s.id, s, e["name"])
        out.append(s.id)
    return out


@router.get("/api/exercises/{eid}")
def exercise_detail(eid: str, request: Request):
    u = require_user(request)
    e = db.exercise(eid)
    seat = seat_for(eid, u)
    if seat is None:
        raise HTTPException(403, "you are not a member of this exercise")
    if e is None:                                  # a bare session (local mode / admin)
        return {"id": eid, "name": eid, "seat": seat, "live": eid in manager.sessions, "members": [],
                "units": config.ADJACENT_UNITS, "unit_names": config.UNIT_NAMES, "me": db.public_user(u)}
    out = _summary(e, u)
    out.update(units=config.ADJACENT_UNITS, unit_names=config.UNIT_NAMES, me=db.public_user(u))
    if seat == "instructor":
        base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
        out["links"] = {"atc": f"{base}/join/{e['atc_token']}", "pilot": f"{base}/join/{e['pilot_token']}",
                        "instructor": f"{base}/join/{e['observer_token']}"}
    return out


class Member(BaseModel):
    user_id: int
    seat: str


@router.post("/api/exercises/{eid}/members")
def add_member(eid: str, body: Member, request: Request):
    u = require_user(request, roles=("instructor",))
    if seat_for(eid, u) != "instructor":
        raise HTTPException(403, "only the exercise's instructor invites")
    if body.seat not in config.SEATS:
        raise HTTPException(400, f"seat: {config.SEATS}")
    if db.user_by_id(body.user_id) is None:
        raise HTTPException(404, "no such user")
    db.add_member(eid, body.user_id, body.seat)
    return db.members(eid)


@router.delete("/api/exercises/{eid}/members/{uid}")
def remove_member(eid: str, uid: int, request: Request):
    u = require_user(request, roles=("instructor",))
    if seat_for(eid, u) != "instructor":
        raise HTTPException(403, "only the exercise's instructor removes members")
    db.x("DELETE FROM exercise_members WHERE exercise_id=? AND user_id=?", (eid, uid))
    return db.members(eid)


class UiBatch(BaseModel):
    now: float = 0
    ev: list = []


@router.post("/api/exercises/{eid}/ui")
def exercise_ui(eid: str, body: UiBatch, request: Request):
    """The controller's screen activity (view, zoom, panels, mouse, clicks, labels) for the exercise's video, from a
    seat without the voice connection that normally carries it (the solo radar)."""
    from . import recorder
    u = require_user(request)
    if seat_for(eid, u) != "atc":
        raise HTTPException(403, "the controller's seat only")
    ev = body.ev[:2000]
    wx = next((e[2].get("wx") for e in reversed(ev) if isinstance(e, list) and len(e) == 3 and e[1] == "v"
               and isinstance(e[2], dict)), None)
    recorder.ui(eid, u.get("id"), 0, time.time(), body.now, ev, wx)
    return {"ok": True, "n": len(ev)}


@router.post("/api/exercises/{eid}/end")
def end_exercise(eid: str, request: Request):
    u = require_user(request)
    e = db.exercise(eid)
    if e and e.get("solo") and e["instructor_id"] == u["id"]:
        return end(eid)                            # solo radar: the controller ends it
    if u["role"] not in ("instructor", "admin") or seat_for(eid, u) != "instructor":
        raise HTTPException(403, "only the exercise's instructor ends it")
    return end(eid)


# ------------------------------------------------------------------ solo radar
class SoloStart(BaseModel):
    scenario: str
    name: Optional[str] = None


def _solo_access(name: str, u: dict) -> Optional[str]:
    """Library scenarios for everyone; instructors also their own and those shared with them."""
    acc = db.scenario_access(name, u)
    if acc is None and db.q1("SELECT 1 FROM scenario_meta WHERE name=? AND owner_id IS NOT NULL", (name,)) is None:
        return "read"
    return acc


@router.get("/api/solo/scenarios")
def solo_scenarios(request: Request):
    """The scenarios this user can fly on the solo radar."""
    u = require_user(request)
    out = []
    for s in list_scenarios():
        if _solo_access(s["name"], u) is None:
            continue
        meta = _scn_meta(s["name"])
        if not s.get("author"):
            s["author"] = meta["owner"]["name"] if meta["owner"] else None
        out.append({**s, **meta})
    return out


@router.post("/api/solo")
def solo_start(body: SoloStart, request: Request):
    """Start a solo radar exercise: the user is the controller, simulated pilots fly what is entered and answer.
    It is kept like any exercise (history, statistics, recording)."""
    u = require_user(request)
    if _solo_access(body.scenario, u) is None:
        raise HTTPException(403, "no access to this scenario")
    try:
        s = manager.create(scenario_name=body.scenario, mode="realtime", time_factor=1.0, autostart=False,
                           label=body.name or body.scenario)
    except (ScenarioError, ValueError) as e:
        raise HTTPException(400, str(e))
    register(s, u, body.name or f"Solo · {body.scenario}", multi=False, solo=True)
    return _summary(db.exercise(s.id), u)


def summarize_metrics(w) -> dict:
    """The numbers kept per exercise (the full metrics stay in the recording)."""
    m = w.metrics()
    asm = m.get("assumption") or {}
    req = m.get("requests") or {}
    sep = m.get("separation") or {}
    return {
        "los_events": sep.get("loss_events", 0), "los_seconds": sep.get("loss_seconds", 0),
        "min_separation_nm": min([x["min_horizontal_nm"] for x in sep.get("losses", []) if x.get("min_horizontal_nm") is not None] or [None])
        if sep.get("losses") else None,
        "area_infringements": (m.get("airspace") or {}).get("infringement_events", 0),
        "rvsm_violations": len((m.get("rvsm") or {}).get("violations", [])),
        "wrong_direction_exits": len((m.get("semicircular") or {}).get("wrong_direction_exits", [])),
        "weather_penetrations": len((m.get("weather") or {}).get("penetrations", [])),
        "assume_mean_delay_s": asm.get("mean_delay_s"), "never_assumed": len(asm.get("never_assumed") or []),
        "requests": req.get("total", 0), "request_mean_response_s": req.get("mean_response_time_s"),
        "requests_expired": (req.get("by_status") or {}).get("expired", 0),
        "wx_requests_unanswered": len((m.get("weather") or {}).get("requests_unanswered_before_weather", [])),
        "extra_track_miles": (m.get("traffic") or {}).get("extra_track_miles", 0),
        "aircraft": (m.get("traffic") or {}).get("total", 0),
        "atc_entries": sum(1 for e in w.events if e.get("type") == "atc_entry"),
        "instructions": (m.get("instructions") or {}).get("issued", 0),
        "readback_errors": (m.get("readback") or {}).get("errors", 0),
        "readback_caught": (m.get("readback") or {}).get("caught", 0),
    }


def end(eid: str) -> dict:
    """Stop the simulation, close the voice room, render the recording."""
    from . import comms, recorder
    s = manager.sessions.get(eid)
    if s is not None:
        with s.lock:
            s.status = "finished"
            try:                                   # the exercise's results, for the statistics
                db.x("UPDATE exercises SET metrics=?, sim_time_s=?, multi=? WHERE id=?",
                     (db.dumps(summarize_metrics(s.world)), round(s.world.t, 1), 1 if s.multi and not s.solo else 0, eid))
                from .. import debrief
                db.x("UPDATE exercises SET debrief=? WHERE id=?", (db.dumps(debrief.build(s.world)), eid))
            except Exception:  # noqa: BLE001 - never block the end of an exercise
                pass
    if s is not None and db.exercise(eid) is not None:
        try:                                       # the whole run, for the debrief on the simulator
            from . import debriefs
            debriefs.archive(eid, s)
        except Exception as ex:  # noqa: BLE001
            print(f"exercise {eid}: run not archived for the debrief ({type(ex).__name__}: {ex})")
    rec_id = recorder.finish(eid, s)
    comms.hub.close_room(eid)
    if s is not None:
        manager.delete(eid)
    db.x("UPDATE exercises SET ended_at=COALESCE(ended_at, ?), status='ended' WHERE id=?", (time.time(), eid))
    return {"ended": eid, "recording_id": rec_id}
