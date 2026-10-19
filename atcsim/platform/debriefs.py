"""Debriefs: after an exercise, the instructor and the controller go through it together on the simulator screen.

The replay is the simulation itself, reproduced 1:1: a *player* (a simulator session) loads the snapshot before the
moment shown and steps second by second, applying every logged action (clearances entered, assumes, the pilots'
instructions, CPDLC, live actions...) at its time - the simulation is deterministic, and it is re-synchronised on the
run's own snapshots every 10 s. The radar shows the player like any live exercise: labels, control states, entries,
coordination, alerts as they were.

Timelines: the exercise as flown ("orig") never changes. Any action on the player (assume, decontrol, a level, a
heading... in the labels or the command line) creates a new timeline from that second; actions at the same second as
the branch point are added to it. A synthetic pseudo-pilot flies the clearances. Every new timeline is extrapolated to
the end of the exercise straight away (HORIZON_S at most), replaying the original's later actions except those about
the aircraft changed in it - "what if only this had been done differently" - so its outcome (losses, infringements)
is known. Timelines can be branched again, and switched between.

* A debrief *session* (started by the instructor) is an open voice call on one shared player: everybody sees the same
  timeline and moment, the instructor runs it (play, pause, seek, speed, which timeline), everybody may act on it;
  views and cursors are shared over the call (comms.py).
* Outside a session, every member gets their own player of the same timelines.

Files: data/debriefs/<id>/orig (the exercise's snapshots and replay frames), data/debriefs/<id>/tl/<n> (each new
timeline: meta.json, snaps.bin).
Simulator session ids: <id>00 the session's shared player, <id>a<user id in hex> a member's own player.
"""
from __future__ import annotations

import io
import json
import os
import pickle
import struct
import threading
import time
import zlib
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket
from pydantic import BaseModel

from .. import inputs
from ..session import Session, _pid
from . import config, db
from .auth import LOCAL_ADMIN, current_user, require_user

router = APIRouter()
manager = None                      # the SessionManager (set by server.py)
IDLE_S = 30 * 60                    # a player nobody uses any more is unloaded after this
HORIZON_S = 45 * 60                 # how far a new timeline is extrapolated (and never past the exercise's end)
SNAP_S = 10.0
KEEP_EVENTS = ("separation_loss_start", "area_infringement_start", "pilot_request", "instruction", "instruction_rejected",
               "rvsm_status", "weather_penetration_start", "aircraft_left_sector", "area_activated", "area_deactivated",
               "runway_change", "wrong_direction_level", "rvsm_airspace_violation_start", "bookmark", "live",
               "readback_missed", "atc_entry")
_lock = threading.RLock()
DEBRIEFS: Dict[str, "Debrief"] = {}
PLAYERS: Dict[str, dict] = {}       # sid -> {"eid", "kind": "shared"|"own", "uid", "touch"}


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


def _read_snaps(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        raw = f.read()
    out, i = {}, 0
    while i + 12 <= len(raw):
        t, n = struct.unpack_from("<dI", raw, i)
        if i + 12 + n > len(raw):
            break
        out[round(t, 1)] = raw[i + 12:i + 12 + n]          # the last one written for a time wins
        i += 12 + n
    return sorted(out.items())


def _append_snap(path: str, t: float, blob: bytes) -> None:
    with open(path, "ab") as f:
        f.write(struct.pack("<dI", t, len(blob)) + blob)


def blob_of(w) -> bytes:
    rec = w.recording
    w.recording = {"every_s": rec["every_s"], "frames": [], "meta": rec["meta"]}

    class _P(pickle.Pickler):
        def persistent_id(self, o):
            return _pid(o)
    try:
        f = io.BytesIO()
        _P(f, protocol=5).dump(w)
    finally:
        w.recording = rec
    return zlib.compress(f.getvalue(), 1)


# ------------------------------------------------------------------ timelines
class Timeline:
    """One version of the run: the exercise as flown ('orig'), or a branch of another timeline from a moment."""

    def __init__(self, tid, parent: Optional["Timeline"], t0: float, snaps: list, log: list, meta: dict, d: str):
        self.id, self.parent, self.t0, self.dir = tid, parent, float(t0), d
        self.snaps: List[tuple] = snaps            # own snapshots (t, blob), t >= t0
        self.exact: Dict[float, bytes] = dict(snaps)
        self.log: list = log                       # every action of this timeline (its final world's log)
        self.meta = meta
        self.frontier = snaps[-1][0] if snaps else t0
        self.events: list = meta.get("events", [])
        self.gen = 0
        self.lock = threading.RLock()

    @property
    def status(self) -> str:
        return self.meta.get("status", "ready")

    def at_or_before(self, t: float) -> tuple:
        if self.parent is not None and t < self.t0 - 1e-6:
            return self.parent.at_or_before(min(t, self.t0 - 0.1))
        with self.lock:
            c = [x for x in self.snaps if x[0] <= t + 1e-6]
            return c[-1] if c else self.snaps[0]

    def exact_at(self, t: float) -> Optional[bytes]:
        t = round(t, 1)
        if self.parent is not None and t < self.t0 - 1e-6:
            return self.parent.exact_at(t)
        return self.exact.get(t)

    def add_snap(self, t: float, blob: bytes, log: list, gen: Optional[int] = None) -> bool:
        with self.lock:
            if gen is not None and gen != self.gen:
                return False                           # computed for a version of the timeline that was changed since
            t = round(t, 1)
            self.snaps = [x for x in self.snaps if x[0] < t] + [(t, blob)]
            self.exact[t] = blob
            self.log = log
            self.frontier = t
            if self.id != "orig":
                _append_snap(os.path.join(self.dir, "snaps.bin"), t, blob)
        return True

    def public(self) -> dict:
        m = self.meta
        return {"id": self.id, "parent": self.parent.id if self.parent else None, "t0": self.t0,
                "name": m.get("name") or ("Exercise as flown" if self.id == "orig" else f"Timeline {self.id}"),
                "by": m.get("by"), "created_at": m.get("created_at"), "status": self.status,
                "frontier": self.frontier, "end_t": m.get("end_t", self.frontier), "changes": m.get("changes", []),
                "summary": m.get("summary", {})}

    def save_meta(self) -> None:
        if self.id == "orig":
            p = os.path.join(os.path.dirname(self.dir), "orig_meta.json")
        else:
            p = os.path.join(self.dir, "meta.json")
        m = dict(self.meta, events=self.events, t0=self.t0, parent=self.parent.id if self.parent else None)
        with open(p + ".tmp", "w") as f:
            json.dump(m, f)
        os.replace(p + ".tmp", p)


def _summary(w, t0: float) -> dict:
    m = w.metrics()
    losses = [x for x in (m.get("separation") or {}).get("losses", []) if (x.get("start_s") or 0) >= t0 - 1]
    infr = [x for x in (m.get("airspace") or {}).get("log", []) if (x.get("start_s") or 0) >= t0 - 1]
    return {"losses": len(losses), "first_loss_s": min([x["start_s"] for x in losses], default=None),
            "loss_pairs": [x.get("aircraft") for x in losses][:6], "infringements": len(infr),
            "min_sep_nm": min([x["min_horizontal_nm"] for x in losses if x.get("min_horizontal_nm") is not None], default=None)}


def _timeline_events(w) -> list:
    out = []
    for e in w.events:
        if e["type"] in KEEP_EVENTS:
            out.append({k: e[k] for k in ("type", "time_s", "callsign", "text", "aircraft", "area_id", "request_type",
                                          "value", "entry") if k in e})
    return out[-3000:]


class Debrief:
    """The timelines of one exercise and the worker that extrapolates new ones."""

    def __init__(self, eid: str):
        self.eid, self.dir = eid, root(eid)
        od = os.path.join(self.dir, "orig")
        with open(os.path.join(od, "meta.json")) as f:
            self.run_meta = json.load(f)
        with open(os.path.join(od, "scenario.json")) as f:
            self.scn = json.load(f)
        self.scenario_name = self.run_meta["scenario"]
        self.loader = Session(self.scenario_name, mode="realtime", autostart=False, sid="ld" + eid, scn=self.scn)
        self.loader.stop()
        self.timelines: Dict[object, Timeline] = {}
        snaps = _read_snaps(os.path.join(od, "snaps.bin"))
        last = self.load(snaps[-1][1])
        mp = os.path.join(self.dir, "orig_meta.json")
        meta = json.load(open(mp)) if os.path.exists(mp) else {}
        orig = Timeline("orig", None, 0.0, snaps, list(inputs.log(last)), meta, od)
        if not meta:
            orig.events = _timeline_events(last)
            orig.meta = {"name": "Exercise as flown", "end_t": snaps[-1][0], "status": "ready", "summary": _summary(last, 0)}
            orig.save_meta()
        self.timelines["orig"] = orig
        tdir = os.path.join(self.dir, "tl")
        if os.path.isdir(tdir):
            metas = []
            for n in os.listdir(tdir):
                try:
                    metas.append(json.load(open(os.path.join(tdir, n, "meta.json"))))
                except (OSError, ValueError):
                    pass
            for m in sorted(metas, key=lambda m: m["n"]):
                d = os.path.join(tdir, str(m["n"]))
                sn = _read_snaps(os.path.join(d, "snaps.bin"))
                parent = self.timelines.get(m.get("parent"))
                if not sn or parent is None:
                    continue
                log = m.pop("log", None) or list(inputs.log(self.load(sn[-1][1])))
                if m.get("status") == "computing":
                    m["status"] = "stopped"             # the server stopped while it was being extrapolated
                    m["end_t"] = sn[-1][0]
                self.timelines[m["n"]] = Timeline(m["n"], parent, m["t0"], sn, log, m, d)

    def load(self, blob: bytes):
        with self.loader.lock:
            return self.loader._load_world(blob)

    def next_id(self) -> int:
        return max([t for t in self.timelines if isinstance(t, int)] or [0]) + 1

    def public(self) -> list:
        return [t.public() for t in self.timelines.values()]

    # ---------------------------------------------------------------- new timelines
    def branch(self, parent: Timeline, w, parent_k: int, change: dict, actor: dict) -> Timeline:
        """A new timeline from w (the player's world, the change just applied), extrapolated in the background."""
        n = self.next_id()
        d = os.path.join(self.dir, "tl", str(n))
        os.makedirs(d, exist_ok=True)
        t0 = round(w.t, 1)
        cs = change.get("callsign")
        meta = {"n": n, "name": change["text"], "by": actor.get("name"), "created_at": time.time(), "status": "computing",
                "parent_k": parent_k, "touched": [cs] if cs else [], "changes": [change], "end_t": t0}
        tl = Timeline(n, parent, t0, [], [], meta, d)
        tl.add_snap(t0, blob_of(w), list(inputs.log(w)))
        tl.save_meta()
        self.timelines[n] = tl
        self.extrapolate(tl)
        return tl

    def amend(self, tl: Timeline, w, change: dict) -> None:
        """Another change at the timeline's own moment: part of it; its future is computed again."""
        with tl.lock:
            tl.gen += 1
            tl.snaps, tl.exact = [], {}
            open(os.path.join(tl.dir, "snaps.bin"), "wb").close()
            cs = change.get("callsign")
            if cs and cs not in tl.meta["touched"]:
                tl.meta["touched"].append(cs)
            tl.meta["changes"].append(change)
            if len(tl.meta["changes"]) == 2 and tl.meta.get("name") == tl.meta["changes"][0]["text"]:
                tl.meta["name"] = tl.meta["changes"][0]["text"] + " …"
            tl.meta["status"] = "computing"
        tl.add_snap(tl.t0, blob_of(w), list(inputs.log(w)))
        tl.save_meta()
        self.extrapolate(tl)

    def extrapolate(self, tl: Timeline) -> None:
        threading.Thread(target=self._extrapolate, args=(tl, tl.gen), daemon=True, name=f"debrief-{self.eid}-{tl.id}").start()

    def _extrapolate(self, tl: Timeline, gen: int) -> None:
        try:
            w = self.load(tl.snaps[0][1])
            parent = tl.parent
            fut = list(parent.log[tl.meta.get("parent_k", 0):])
            touched = set(tl.meta.get("touched") or [])
            end = min(float(w.duration_s), tl.t0 + HORIZON_S)
            i, last = 0, tl.t0
            while w.t < end - 1e-6 and not w.finished and tl.gen == gen:
                w.step(1.0)
                while i < len(fut) and fut[i]["t"] <= w.t + 1e-6:
                    inp = fut[i]
                    i += 1
                    if inp["kind"] in inputs.NOT_REPLAYED_IN_BRANCHES or inputs.callsign_of(inp["kind"], inp["body"]) in touched:
                        continue
                    try:
                        inputs.apply(w, inp["kind"], inp["body"], {"name": inp.get("by"), "seat": inp.get("seat")})
                    except Exception:  # noqa: BLE001 - an action that does not fit this timeline any more
                        pass
                if w.t - last >= SNAP_S - 1e-6 or w.t >= end - 1e-6 or w.finished:
                    if not tl.add_snap(w.t, blob_of(w), list(inputs.log(w)), gen):
                        return
                    last = w.t
            with tl.lock:
                if tl.gen != gen:
                    return
                tl.events = _timeline_events(w)
                tl.meta.update(status="ready", end_t=round(w.t, 1), summary=_summary(w, tl.t0))
            tl.save_meta()
        except Exception as e:  # noqa: BLE001
            tl.meta.update(status="failed", error=f"{type(e).__name__}: {e}")
            tl.save_meta()


def get(eid: str) -> Debrief:
    with _lock:
        d = DEBRIEFS.get(eid)
        if d is None:
            if not has_archive(eid):
                raise HTTPException(404, "this exercise has no run to debrief (it ended before debriefs on the simulator existed)")
            d = DEBRIEFS[eid] = Debrief(eid)
        return d


# ------------------------------------------------------------------ the player
class Player(Session):
    """A simulator session that shows a timeline: seek, play (re-simulating it 1:1), and branch on any action."""

    def __init__(self, deb: Debrief, sid: str):
        super().__init__(deb.scenario_name, mode="realtime", time_factor=1.0, autostart=False,
                         label=deb.run_meta.get("label"), sid=sid, scn=deb.scn)
        self.deb = deb
        self.tl = deb.timelines["orig"]
        self.multi = True
        self.status = "paused"
        self.time_factor = 5.0
        self.seek(0.0)

    # nothing is saved by a player: the timelines hold the runs
    def _maybe_snapshot(self) -> None:
        pass

    def _snapshot(self) -> None:
        pass

    def _persist(self, rewrite: bool = False) -> None:
        pass

    def summary(self) -> dict:
        out = super().summary()
        tl = self.tl
        out["debrief"] = {"timeline": tl.id, "frontier": tl.frontier, "end_t": tl.meta.get("end_t", tl.frontier),
                          "computing": tl.status == "computing", "t0": tl.t0}
        return out

    def timeline(self) -> dict:
        return {"points": [], "every_s": SNAP_S, "rewinds": self.rewinds}

    def _swap(self, w) -> None:
        meta = (w.recording or {}).get("meta") or {}
        w.recording = self.world.recording
        w.recording["meta"].update(meta)
        self.world = w
        self._sep_key = None
        self._shared_sep, self._shared_at = None, 0.0

    def _apply_due(self) -> None:
        w, log = self.world, self.tl.log
        k = len(inputs.log(w))
        while k < len(log) and log[k]["t"] <= w.t + 1e-6:
            inp = log[k]
            try:
                inputs.apply(w, inp["kind"], inp["body"], {"name": inp.get("by"), "seat": inp.get("seat")})
            except Exception:  # noqa: BLE001
                inputs.log(w).append(inp)
            if len(inputs.log(w)) <= k:                # never stall on an action that did not log itself
                inputs.log(w).append(inp)
            k = len(inputs.log(w))

    def _tick(self) -> bool:
        tl = self.tl
        if self.world.t + 1 > tl.frontier + 1e-6:
            if tl.status == "computing":
                return True                            # wait for the extrapolation
            self.status = "paused"                     # the end of this timeline
            return False
        self.world.step(1.0)
        blob = tl.exact_at(self.world.t)
        if blob is not None:                           # re-synchronise on the run's own snapshot
            self._swap(self.deb.load(blob))
        self._apply_due()
        return True

    def seek(self, t: float) -> dict:
        with self.lock:
            tl = self.tl
            t = max(0.0, min(float(t), tl.frontier))
            st, blob = tl.at_or_before(t)
            self._swap(self.deb.load(blob))
            self._apply_due()
            while self.world.t + 1e-6 < t:
                self.world.step(1.0)
                b2 = tl.exact_at(self.world.t)
                if b2 is not None:
                    self._swap(self.deb.load(b2))
                self._apply_due()
            self.rewinds += 1
            if self.status == "finished":
                self.status = "paused"
            return self.summary()

    def set_timeline(self, tid, t: Optional[float] = None) -> dict:
        tl = self.deb.timelines.get(tid)
        if tl is None:
            raise KeyError("no such timeline")
        with self.lock:
            cur = self.world.t if t is None else float(t)
            self.tl = tl
            return self.seek(min(cur, tl.frontier))

    def control(self, action: str, seconds: float = 1.0, time_factor: Optional[float] = None, mode: Optional[str] = None) -> dict:
        if action == "rewind":
            return self.seek(seconds)
        if action == "restart":
            return self.seek(0.0)
        if action in ("start", "resume"):
            with self.lock:
                if self.world.t + 1 > self.tl.frontier + 1e-6 and self.tl.status != "computing":
                    self.seek(self.tl.t0 if self.tl.id != "orig" else 0.0)     # at the end: from its start again
        return super().control(action, seconds, time_factor, mode)

    def on_input(self, kind: str, body: dict, actor: dict):
        """An action on the timeline shown: a new timeline from this second (or added to this one, when it is the
        second it was branched at)."""
        if kind in ("bookmark", "chat", "mtcd_settings"):
            with self.lock:
                return inputs.apply(self.world, kind, body, actor, logged=False)
        with self.lock:
            tl, w = self.tl, self.world
            self.status = "paused"
            change = {"t": round(w.t, 1), "text": inputs.describe(kind, body), "by": actor.get("name"),
                      "callsign": inputs.callsign_of(kind, body)}
            if tl.id != "orig" and abs(w.t - tl.t0) < 0.05:
                out = inputs.apply(w, kind, body, actor)
                self.deb.amend(tl, w, change)
            else:
                k = len(inputs.log(w))
                w.auto_pilot = True                    # a synthetic pseudo-pilot flies the clearances
                w.auto_wx = True
                out = inputs.apply(w, kind, body, actor)
                self.tl = self.deb.branch(tl, w, k, change, actor)
            self.world = self.deb.load(self.tl.snaps[0][1])
            self.rewinds += 1
            return out


def lookup(sid: str) -> Optional[dict]:
    """A debrief player's session id: {"eid", "kind": "shared"|"own", "uid"}, else None."""
    if len(sid) <= 8:
        return None
    o = PLAYERS.get(sid)
    if o:
        o["touch"] = time.time()
        return o
    if len(sid) == 10 and sid.endswith("00"):
        return {"eid": sid[:8], "kind": "shared", "uid": None}
    if sid[8] == "a" and len(sid) > 9:
        try:
            return {"eid": sid[:8], "kind": "own", "uid": int(sid[9:], 16)}
        except ValueError:
            return None
    return None


def can_write(sid: str, u: dict) -> bool:
    """Actions on a player's timeline (they branch it): the shared player while the session is on, by its members;
    one's own player, by its owner."""
    o = lookup(sid)
    if o is None:
        return False
    if o["kind"] == "own":
        return o["uid"] == u.get("id")
    return record(o["eid"])["status"] == "live" or is_leader(o["eid"], u)


def can_control(sid: str, u: dict) -> bool:
    """Play, pause, seek, speed, which timeline: the instructor on the shared player, the owner on their own."""
    o = lookup(sid)
    if o is None:
        return False
    if o["kind"] == "own":
        return o["uid"] == u.get("id")
    return is_leader(o["eid"], u)


def player(eid: str, u: dict, shared: bool) -> Player:
    sid = eid + "00" if shared else f"{eid}a{int(u.get('id') or 0):x}"
    with _lock:
        s = manager.sessions.get(sid)
        if s is None:
            s = Player(get(eid), sid)
            with manager.lock:
                manager.sessions[sid] = s
        PLAYERS[sid] = {"eid": eid, "kind": "shared" if shared else "own", "uid": None if shared else u.get("id"),
                        "touch": time.time()}
    return s


def unload(eid: Optional[str] = None, idle_only: bool = False) -> None:
    now = time.time()
    with _lock:
        for sid, o in list(PLAYERS.items()):
            if eid is not None and o["eid"] != eid:
                continue
            if idle_only and (now - o["touch"] < IDLE_S or (o["kind"] == "shared" and record(o["eid"])["status"] == "live")):
                continue
            PLAYERS.pop(sid, None)
            with manager.lock:
                s = manager.sessions.pop(sid, None)
            if s:
                s.stop()
        for e in list(DEBRIEFS):
            if (eid is None or e == eid) and not any(o["eid"] == e for o in PLAYERS.values()):
                busy = any(t.status == "computing" for t in DEBRIEFS[e].timelines.values())
                if not busy:
                    DEBRIEFS.pop(e, None)


def _gc() -> None:
    while True:
        time.sleep(300)
        try:
            unload(idle_only=True)
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_gc, daemon=True, name="debrief-gc").start()


# ------------------------------------------------------------------ the debrief record
def record(eid: str) -> dict:
    r = db.q1("SELECT * FROM debrief_sessions WHERE exercise_id=?", (eid,))
    if r is None:
        return {"exercise_id": eid, "status": "ready" if has_archive(eid) else "none", "started_at": None,
                "ended_at": None}
    return r


def _save(eid: str, **kw) -> None:
    r = record(eid)
    r.update(kw)
    db.x("INSERT OR REPLACE INTO debrief_sessions (exercise_id, status, started_at, ended_at, started_by, branches) "
         "VALUES (?,?,?,?,?,?)", (eid, r["status"] if r["status"] != "none" else "ready", r.get("started_at"),
                                  r.get("ended_at"), r.get("started_by"), None))


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


def _timelines_count(eid: str) -> int:
    d = os.path.join(root(eid), "tl")
    return len(os.listdir(d)) if os.path.isdir(d) else 0


# ------------------------------------------------------------------ API
def info(eid: str, u: dict) -> dict:
    from .exercises import _summary as ex_summary
    e = db.exercise(eid)
    if e is None:
        raise HTTPException(404, "no such exercise")
    seat = _member(eid, u)
    from . import comms
    room = comms.hub.rooms.get("db:" + eid)
    r = record(eid)
    out = ex_summary(e, u)
    live = r["status"] == "live"
    out.update(debrief_status=r["status"], started_at=r["started_at"], debrief_ended_at=r["ended_at"],
               leader=is_leader(eid, u), seat=seat, has_report=bool(e.get("debrief")),
               on_call=[c.public() for c in room.conns.values()] if room else [],
               timelines=get(eid).public() if has_archive(eid) else [],
               player_sid=(eid + "00") if live else f"{eid}a{int(u.get('id') or 0):x}")
    return out


@router.get("/api/debriefs")
def list_debriefs(request: Request):
    """The debriefs of this user's ended exercises: ready (not held yet), live (on now: join it) or done."""
    from .exercises import exercises as ex_list
    from . import comms
    out = []
    for x in ex_list(request, limit=300):
        if x["live"] or not x["ended_at"]:
            continue
        r = record(x["id"])
        if r["status"] == "none":
            continue
        room = comms.hub.rooms.get("db:" + x["id"])
        out.append({**x, "debrief_status": r["status"], "started_at": r["started_at"],
                    "debrief_ended_at": r["ended_at"], "branches": _timelines_count(x["id"]),
                    "on_call": [c.name for c in room.conns.values()] if room else []})
    return out


@router.get("/api/debriefs/{eid}")
def get_debrief(eid: str, request: Request):
    """The debrief: the exercise, the session's status, the timelines (the exercise as flown, and every branch with
    its changes, its extrapolation status and its outcome), and which player this user should open."""
    return info(eid, require_user(request))


@router.post("/api/debriefs/{eid}/start")
def start(eid: str, request: Request):
    """The instructor starts the debrief session: the members see it live on their home page and join."""
    u = require_user(request)
    _member(eid, u)
    if not is_leader(eid, u):
        raise HTTPException(403, "the exercise's instructor starts the debrief")
    get(eid)
    r = record(eid)
    _save(eid, status="live", started_at=r["started_at"] or time.time(), started_by=u.get("id"), ended_at=None)
    from . import comms
    comms.hub.room("db:" + eid, open_call=True)
    player(eid, u, shared=True)
    return info(eid, u)


@router.post("/api/debriefs/{eid}/end")
def end(eid: str, request: Request):
    """End the debrief session: the call closes; the debrief (every timeline) stays with the exercise."""
    u = require_user(request)
    _member(eid, u)
    if not is_leader(eid, u):
        raise HTTPException(403, "the exercise's instructor ends the debrief")
    _save(eid, status="done", ended_at=time.time())
    s = manager.sessions.get(eid + "00")
    if s is not None:
        s.control("pause")
    from . import comms
    comms.hub.close_room("db:" + eid)
    return info(eid, u)


@router.post("/api/debriefs/{eid}/player")
def open_player(eid: str, request: Request):
    """The player this user works on: the session's shared one while the debrief session is on, else their own.
    Returns its simulator session id (use /sessions/{sid}/... on it: state, stream, entries...) and the rights."""
    u = require_user(request)
    _member(eid, u)
    live = record(eid)["status"] == "live"
    s = player(eid, u, shared=live)
    return {"sid": s.id, "shared": live, "timeline": s.tl.id, "can_write": can_write(s.id, u), "can_control": can_control(s.id, u)}


class Seek(BaseModel):
    sid: str
    t: Optional[float] = None
    timeline: Optional[str] = None


@router.post("/api/debriefs/{eid}/seek")
def seek(eid: str, body: Seek, request: Request):
    """Move a player: to T+t on its timeline, and/or to another timeline ('orig' or its number; at the same time
    when t is not given)."""
    u = require_user(request)
    _member(eid, u)
    o = lookup(body.sid)
    if o is None or o["eid"] != eid or body.sid not in manager.sessions:
        raise HTTPException(404, "open the player first")
    if not can_control(body.sid, u):
        raise HTTPException(403, "the instructor runs the debrief session")
    s = manager.sessions[body.sid]
    try:
        if body.timeline is not None:
            tid = "orig" if body.timeline == "orig" else int(body.timeline)
            return s.set_timeline(tid, body.t)
        return s.seek(body.t or 0.0)
    except (KeyError, ValueError) as e:
        raise HTTPException(404, str(e))


@router.get("/api/debriefs/{eid}/timelines/{tid}")
def timeline_detail(eid: str, tid: str, request: Request):
    """One timeline: its markers (losses, requests, entries, bookmarks...) for the time bar, and its details."""
    u = require_user(request)
    _member(eid, u)
    d = get(eid)
    tl = d.timelines.get("orig" if tid == "orig" else int(tid) if tid.isdigit() else None)
    if tl is None:
        raise HTTPException(404, "no such timeline")
    ev = list(tl.events)
    if tl.parent is not None:                      # its history before the branch point is its parent's
        ev = [e for e in _chain_events(tl.parent) if e["time_s"] < tl.t0] + [e for e in ev if e["time_s"] >= tl.t0]
    return {**tl.public(), "events": ev}


def _chain_events(tl: Timeline) -> list:
    if tl.parent is None:
        return tl.events
    return [e for e in _chain_events(tl.parent) if e["time_s"] < tl.t0] + [e for e in tl.events if e["time_s"] >= tl.t0]


class Rename(BaseModel):
    name: str


@router.patch("/api/debriefs/{eid}/timelines/{tid}")
def rename(eid: str, tid: int, body: Rename, request: Request):
    u = require_user(request)
    _member(eid, u)
    tl = get(eid).timelines.get(tid)
    if tl is None:
        raise HTTPException(404, "no such timeline")
    tl.meta["name"] = " ".join(body.name.split())[:80] or tl.meta.get("name")
    tl.save_meta()
    return tl.public()


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
