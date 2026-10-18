"""Debriefs: after an exercise, the instructor and the controller go through it together on the simulator screen.

* When an exercise ends, its whole run is archived (data/debriefs/<id>/orig: the snapshots every 10 s and the replay
  frames) so it can be replayed on the radar and restarted from any moment.
* A debrief *session* (started by the instructor) is an open voice call with everybody on the same screen: the
  instructor leads (what is shown, the replay time, the view, their cursor), the others follow.
* **Try from here** branches the run at a moment into a live simulation (simulated pilots fly what is entered), so the
  instructor can show other solutions. The original run is never changed; every try is kept (data/debriefs/<id>/
  branches/<n>) and can be replayed afterwards like the original.
* When the session ends, the debrief stays with the exercise: its members open it in the same simulator view.

Simulator session ids used on the radar (/sessions/{sid}/...): <exercise id>00 for the original run (read-only),
<exercise id>b<n> for try n.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel

from . import config, db
from .auth import LOCAL_ADMIN, current_user, require_user

router = APIRouter()
manager = None                      # the SessionManager (set by server.py)
IDLE_S = 30 * 60                    # an opened run nobody looks at any more is unloaded after this
_lock = threading.RLock()
# simulator sessions opened for debriefs: sid -> {"eid", "kind": "orig"|"branch", "n", "touch"}
OPEN: Dict[str, dict] = {}


def root(eid: str) -> str:
    return os.path.join(config.DATA_DIR, "debriefs", eid)


def has_archive(eid: str) -> bool:
    return os.path.exists(os.path.join(root(eid), "orig", "snaps.bin"))


# ------------------------------------------------------------------ archive at the end of an exercise
def archive(eid: str, s) -> None:
    """Keep the whole run of an ending exercise (its last moment included) for the debrief."""
    d = os.path.join(root(eid), "orig")
    with s.lock:
        live_dir = s.persist_dir
        s._snapshot()                              # the very end
        s.persist_dir = d
        s._persisted_frames = 0
        try:
            s._persist(rewrite=True)
        finally:
            s.persist_dir = live_dir


# ------------------------------------------------------------------ the debrief record
def record(eid: str) -> dict:
    r = db.q1("SELECT * FROM debrief_sessions WHERE exercise_id=?", (eid,))
    if r is None:
        return {"exercise_id": eid, "status": "ready" if has_archive(eid) else "none", "started_at": None,
                "ended_at": None, "branches": []}
    r["branches"] = json.loads(r["branches"] or "[]")
    return r


def _save(eid: str, **kw) -> None:
    r = record(eid)
    r.update(kw)
    db.x("INSERT OR REPLACE INTO debrief_sessions (exercise_id, status, started_at, ended_at, started_by, branches) "
         "VALUES (?,?,?,?,?,?)", (eid, r["status"] if r["status"] != "none" else "ready", r.get("started_at"),
                                  r.get("ended_at"), r.get("started_by"), json.dumps(r["branches"])))


def is_leader(eid: str, u: dict) -> bool:
    """The exercise's instructor (for a solo run: its owner) leads the debrief; an administrator may too."""
    e = db.exercise(eid)
    return bool(e) and (e["instructor_id"] == u.get("id") or u.get("role") == "admin" or config.AUTH == "off")


def _member(eid: str, u: dict) -> str:
    from .access import seat_for
    seat = seat_for(eid, u)
    if seat is None:
        raise HTTPException(403, "you are not a member of this exercise")
    return seat


# ------------------------------------------------------------------ simulator sessions for the debrief
def sid_orig(eid: str) -> str:
    return eid + "00"


def sid_branch(eid: str, n: int) -> str:
    return f"{eid}b{n}"


def lookup(sid: str) -> Optional[dict]:
    """A debrief session id: {"eid", "kind", "n"} (also for runs not loaded yet), else None."""
    o = OPEN.get(sid)
    if o:
        o["touch"] = time.time()
        return o
    if len(sid) == 10 and sid.endswith("00") and has_archive(sid[:8]):
        return {"eid": sid[:8], "kind": "orig", "n": 0}
    if len(sid) > 9 and sid[8] == "b" and sid[9:].isdigit():
        return {"eid": sid[:8], "kind": "branch", "n": int(sid[9:])}
    return None


def can_write(sid: str, u: dict) -> bool:
    """Instructions, run / pause and the like on a debrief session: never on the original run; on a try, for the
    leader always, for the other members while the debrief session is on."""
    o = lookup(sid)
    if o is None or o["kind"] != "branch":
        return False
    return is_leader(o["eid"], u) or record(o["eid"])["status"] == "live"


def _load(d: str, sid: str):
    """A run saved in folder d as a paused simulator session (nothing is written back to the folder)."""
    from ..session import Session
    with open(os.path.join(d, "meta.json")) as f:
        meta = json.load(f)
    with open(os.path.join(d, "scenario.json")) as f:
        scn = json.load(f)
    s = Session(meta["scenario"], mode="realtime", time_factor=1.0, autostart=False, label=meta.get("label"),
                sid=sid, scn=scn)
    snaps = _read_snaps(d)
    frames = _read_frames(d)
    with s.lock:
        w = s._load_world(snaps[-1][1])
        meta = w.recording.get("meta") or {}
        w.recording = s.world.recording
        w.recording["meta"].update(meta)
        w.recording["frames"] = [fr for fr in frames if fr["t"] <= snaps[-1][0] + 1e-6]
        w._last_frame_t = w.recording["frames"][-1]["t"] if w.recording["frames"] else -1e9
        s.world = w
        s._snaps = snaps
        s.multi = True
        s.status = "paused"
        s.time_factor = float(meta.get("time_factor") or 1.0)
    return s


def _read_snaps(d: str) -> list:
    import struct
    with open(os.path.join(d, "snaps.bin"), "rb") as f:
        raw = f.read()
    out, i = [], 0
    while i + 12 <= len(raw):
        t, n = struct.unpack_from("<dI", raw, i)
        if i + 12 + n > len(raw):
            break
        out.append((t, raw[i + 12:i + 12 + n]))
        i += 12 + n
    if not out:
        raise ValueError("no snapshot")
    return out


def _read_frames(d: str) -> list:
    out = []
    p = os.path.join(d, "frames.jsonl")
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    break
    return out


def open_run(eid: str, src) -> dict:
    """Load the original run (src 'orig') or try n on the radar; returns its simulator session id."""
    if src in ("orig", 0, "0", None):
        sid, kind, n, d = sid_orig(eid), "orig", 0, os.path.join(root(eid), "orig")
    else:
        n = int(src)
        b = next((x for x in record(eid)["branches"] if x["n"] == n), None)
        if b is None:
            raise HTTPException(404, "no such try")
        sid, kind, d = sid_branch(eid, n), "branch", os.path.join(root(eid), "branches", str(n))
    with _lock:
        if sid not in manager.sessions:
            if not os.path.exists(os.path.join(d, "snaps.bin")):
                raise HTTPException(404, "this run was not kept")
            s = _load(d, sid)
            if kind == "branch":
                s.solo = True                      # simulated pilots fly what is entered and read it back
                s.persist_dir = d                  # what is tried next is kept too
                s._persisted_frames = len(s.world.recording["frames"])
            with manager.lock:
                manager.sessions[sid] = s
        OPEN[sid] = {"eid": eid, "kind": kind, "n": n, "touch": time.time()}
    return {"sid": sid, "kind": kind, "n": n}


def branch(eid: str, t: float, u: dict, name: str = "") -> dict:
    """Try from here: a live copy of the run at the last saved point at or before t, paused."""
    from ..session import Session
    base = manager.sessions.get(open_run(eid, "orig")["sid"])
    rec = record(eid)
    n = max([b["n"] for b in rec["branches"]] or [0]) + 1
    sid = sid_branch(eid, n)
    with base.lock:
        cand = [x for x in base._snaps if x[0] <= t + 1e-6] or base._snaps[:1]
        st, blob = cand[-1]
        frames = [f for f in base.world.recording["frames"] if f["t"] <= st + 1e-6]
        snaps = [x for x in base._snaps if x[0] <= st + 1e-6]
        scn, label = base.scn, base.label
        base_meta = dict(base.world.recording.get("meta") or {})
    s = Session(base.scenario_name, mode="realtime", time_factor=1.0, autostart=False, label=label, sid=sid, scn=scn)
    with s.lock:
        w = s._load_world(blob)
        meta = w.recording.get("meta") or {}
        w.recording = s.world.recording
        w.recording["meta"].update(meta)
        w.recording["meta"].update(base_meta)
        w.recording["frames"] = frames
        w._last_frame_t = frames[-1]["t"] if frames else -1e9
        s.world = w
        s._snaps = snaps
        s.solo = True
        s.status = "paused"
        who = db.public_user(u)["name"] if u.get("id") else "Instructor"
        title = (name or "").strip()[:60] or f"Try {n}"
        w._event("branch", text=f"⑂ {title}: tried from T+{int(st // 60):02d}:{int(st % 60):02d} ({who})", by=who)
        s.persist_dir = os.path.join(root(eid), "branches", str(n))
        s._persist(rewrite=True)
    with manager.lock:
        manager.sessions[sid] = s
    OPEN[sid] = {"eid": eid, "kind": "branch", "n": n, "touch": time.time()}
    b = {"n": n, "name": title, "t0": st, "by": who, "created_at": time.time()}
    _save(eid, branches=rec["branches"] + [b])
    return {**b, "sid": sid}


def unload(eid: Optional[str] = None, idle_only: bool = False) -> None:
    """Free the runs opened for a debrief (all of one exercise, or those nobody looked at for a while)."""
    now = time.time()
    with _lock:
        for sid, o in list(OPEN.items()):
            if eid is not None and o["eid"] != eid:
                continue
            if idle_only and (now - o["touch"] < IDLE_S or record(o["eid"])["status"] == "live"):
                continue
            OPEN.pop(sid, None)
            with manager.lock:
                s = manager.sessions.pop(sid, None)
            if s:
                with s.lock:
                    s.status = "paused"
                s.stop()                           # its folder stays: the debrief keeps it


def _gc() -> None:
    while True:
        time.sleep(300)
        try:
            unload(idle_only=True)
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_gc, daemon=True, name="debrief-gc").start()


# ------------------------------------------------------------------ API
def info(eid: str, u: dict) -> dict:
    from .exercises import _summary
    e = db.exercise(eid)
    if e is None:
        raise HTTPException(404, "no such exercise")
    seat = _member(eid, u)
    from . import comms
    room = comms.hub.rooms.get("db:" + eid)
    r = record(eid)
    out = _summary(e, u)
    out.update(debrief_status=r["status"], started_at=r["started_at"], debrief_ended_at=r["ended_at"],
               branches=r["branches"], leader=is_leader(eid, u), seat=seat, has_report=bool(e.get("debrief")),
               on_call=[c.public() for c in room.conns.values()] if room else [],
               evaluated=bool(db.q1("SELECT 1 FROM evaluations WHERE exercise_id=? LIMIT 1", (eid,))))
    return out


@router.get("/api/debriefs")
def list_debriefs(request: Request):
    """The debriefs of this user's ended exercises: ready (not held yet), live (on now: join it) or done."""
    from .exercises import exercises as ex_list
    out = []
    for x in ex_list(request, limit=300):
        if x["live"] or not x["ended_at"]:
            continue
        r = record(x["id"])
        if r["status"] == "none":
            continue
        from . import comms
        room = comms.hub.rooms.get("db:" + x["id"])
        out.append({**x, "debrief_status": r["status"], "started_at": r["started_at"],
                    "debrief_ended_at": r["ended_at"], "branches": len(r["branches"]),
                    "on_call": [c.name for c in room.conns.values()] if room else []})
    return out


@router.get("/api/debriefs/{eid}")
def get_debrief(eid: str, request: Request):
    return info(eid, require_user(request))


@router.post("/api/debriefs/{eid}/start")
def start(eid: str, request: Request):
    """The instructor starts the debrief session: the members see it live on their home page and join."""
    u = require_user(request)
    _member(eid, u)
    if not is_leader(eid, u):
        raise HTTPException(403, "the exercise's instructor starts the debrief")
    if not has_archive(eid):
        raise HTTPException(404, "this exercise has no run to debrief (ended before debriefs existed)")
    r = record(eid)
    _save(eid, status="live", started_at=r["started_at"] or time.time(), started_by=u.get("id"), ended_at=None)
    from . import comms
    comms.hub.room("db:" + eid, open_call=True)
    return info(eid, u)


@router.post("/api/debriefs/{eid}/end")
def end(eid: str, request: Request):
    """End the debrief session: the call closes; the debrief (the run and every try) stays with the exercise."""
    u = require_user(request)
    _member(eid, u)
    if not is_leader(eid, u):
        raise HTTPException(403, "the exercise's instructor ends the debrief")
    _save(eid, status="done", ended_at=time.time())
    for sid, o in list(OPEN.items()):
        s = manager.sessions.get(sid)
        if s is not None and o["eid"] == eid and o["kind"] == "branch":
            with s.lock:
                s.status = "paused"
                if s.persist_dir:
                    s._snapshot()
    from . import comms
    comms.hub.close_room("db:" + eid)
    return info(eid, u)


class OpenRun(BaseModel):
    src: str = "orig"


@router.post("/api/debriefs/{eid}/open")
def open_api(eid: str, body: OpenRun, request: Request):
    """Load the original run ('orig') or a try ('1', '2', ...) on the radar: its simulator session id."""
    u = require_user(request)
    _member(eid, u)
    if not has_archive(eid):
        raise HTTPException(404, "this exercise has no run to debrief (ended before debriefs existed)")
    out = open_run(eid, body.src)
    out["writable"] = can_write(out["sid"], u)
    return out


class NewBranch(BaseModel):
    t: float
    name: str = ""


@router.post("/api/debriefs/{eid}/branches")
def new_branch(eid: str, body: NewBranch, request: Request):
    """Try from here: the run at (the 10-second point at or before) T+t as a live simulation, paused; simulated pilots
    fly what is entered. The original run is not changed."""
    u = require_user(request)
    _member(eid, u)
    if not is_leader(eid, u):
        raise HTTPException(403, "the instructor tries other solutions (the controller follows)")
    if not has_archive(eid):
        raise HTTPException(404, "this exercise has no run to debrief")
    return branch(eid, max(0.0, body.t), u, body.name)


@router.websocket("/debriefs/{eid}/comms")
async def debrief_comms(ws: WebSocket, eid: str):
    """The debrief's open call and its sync (see comms.py)."""
    from . import comms
    from .access import seat_for
    user = current_user(ws) or (LOCAL_ADMIN if config.AUTH == "off" else None)
    seat = seat_for(eid, user) if user else None
    if seat is None or record(eid)["status"] != "live":
        await ws.close(code=4403)
        return
    await ws.accept()
    await comms.serve(ws, comms.hub.room("db:" + eid, open_call=True), user, seat, leader=is_leader(eid, user))
