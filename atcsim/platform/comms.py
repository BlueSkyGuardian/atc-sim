"""Voice: the radio frequency (push-to-talk) and the phone lines to the adjacent units, over one WebSocket per
participant (/sessions/{id}/comms).

Audio: 16 kHz mono G.711 mu-law (8 bits per sample), in frames of a few tens of milliseconds.
  client -> server : binary frame = mu-law samples (what the microphone picked up while transmitting)
  server -> client : binary frame = [channel u8][speaker u16 LE][mu-law samples]   channel 0 = radio, 1 = phone
Control (JSON text frames):
  client -> server : {"op": "ptt", "on": true|false}
                     {"op": "call", "unit": "GMMM"}       controller: rings the unit (answered by a pseudo-pilot or
                                                          the instructor); pseudo-pilot/instructor: calls the
                                                          controller as that unit
                     {"op": "answer", "call": id} | {"op": "hangup", "call": id}
  server -> client : {"type": "hello", "me": conn, "roster": [...], "calls": [...]}
                     {"type": "roster", ...} {"type": "ptt", "conn": id, "on": bool} {"type": "calls", "calls": [...]}

Who hears what: the radio goes to everybody in the exercise (as on a real frequency); a phone call goes between its
two parties, and every instructor in the exercise listens to all calls. Everything is passed to the recorder.

Debrief rooms (/debriefs/{id}/comms) are an open call instead: every microphone is live unless muted
({"op": "mute", "on": true}) and everybody hears everybody (channel 2). They also carry the debrief's sync: the leader's
{"op": "sync", "s": {...}} (what is shown, the replay time, the view) and everybody's {"op": "cursor", "x", "y"}.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import struct
import time
from typing import Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import config, db
from .access import seat_for
from .auth import LOCAL_ADMIN, current_user

router = APIRouter()
manager = None
MAX_QUEUE = 120                     # frames waiting for a slow client before its audio is dropped


class Conn:
    def __init__(self, cid: int, ws: WebSocket, user: dict, seat: str):
        self.id, self.ws, self.user, self.seat = cid, ws, user, seat
        self.name = db.public_user(user)["name"] if user.get("id") else "Instructor"
        self.ptt = False
        self.muted = False
        self.leader = False
        self.q: asyncio.Queue = asyncio.Queue()
        try:                                       # the loop that serves this connection (its queue belongs to it)
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.joined = time.time()

    def public(self) -> dict:
        return {"conn": self.id, "name": self.name, "seat": self.seat, "user_id": self.user.get("id"),
                "ptt": self.ptt, "muted": self.muted, "leader": self.leader}

    def put(self, item) -> None:
        """Queue a frame or message for this participant, from any thread or event loop: an asyncio queue may only be
        touched from its own loop (from elsewhere the waiting sender is never woken - audio and calls would sit in
        the queue), so other threads (HTTP handlers in the thread pool, other connections' loops) hand it over."""
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if self.loop is None or here is self.loop:
            self._put(item)
            return
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:
            pass                                    # its loop is gone: the connection is closing

    def _put(self, item) -> None:
        if isinstance(item, (bytes, bytearray)) and self.q.qsize() > MAX_QUEUE:
            return                                  # client too slow: drop audio, keep control messages
        self.q.put_nowait(item)


class Call:
    def __init__(self, cid: int, unit: str, caller: Conn, from_atc: bool):
        self.id, self.unit, self.from_atc = cid, unit, from_atc
        self.caller, self.answerer = caller.id, None
        self.state, self.t0, self.t_answer = "ringing", time.time(), None

    def parties(self):
        return {self.caller, self.answerer} - {None}

    def public(self, room: "Room") -> dict:
        def nm(i):
            c = room.conns.get(i)
            return {"conn": i, "name": c.name, "seat": c.seat} if c else None
        return {"id": self.id, "unit": self.unit, "unit_name": config.UNIT_NAMES.get(self.unit, self.unit),
                "direction": "atc_to_unit" if self.from_atc else "unit_to_atc", "state": self.state,
                "caller": nm(self.caller), "answerer": nm(self.answerer),
                "since": self.t_answer or self.t0}


class Room:
    def __init__(self, eid: str, open_call: bool = False):
        self.eid = eid
        self.open = open_call               # debrief: an open call, no push-to-talk, no telephone
        self.sync: Optional[dict] = None    # debrief: the leader's last sync state, for those who join later
        self.conns: Dict[int, Conn] = {}
        self.calls: Dict[int, Call] = {}
        self._ids = itertools.count(1)
        self._call_ids = itertools.count(1)

    # ---------------------------------------------------------------- helpers
    def roster(self) -> list:
        return [c.public() for c in self.conns.values()]

    def calls_public(self) -> list:
        return [c.public(self) for c in self.calls.values()]

    def broadcast(self, msg: dict, only=None) -> None:
        for c in list(self.conns.values()):
            if only is None or c.id in only:
                c.put(msg)

    def record(self, kind: str, **data) -> None:
        from . import recorder
        recorder.event(self.eid, {"type": kind, "wall": time.time(), **data})

    def call_of(self, conn: Conn) -> Optional[Call]:
        for c in self.calls.values():
            if c.state == "active" and conn.id in c.parties():
                return c
        return None

    # ---------------------------------------------------------------- control
    def on_control(self, conn: Conn, m: dict) -> None:
        op = m.get("op")
        if self.open:
            if op == "mute":
                conn.muted = bool(m.get("on"))
                self.broadcast({"type": "roster", "roster": self.roster()})
            elif op == "sync" and conn.leader and isinstance(m.get("s"), dict):
                self.sync = m["s"]
                self.broadcast({"type": "sync", "s": self.sync, "conn": conn.id}, only={c.id for c in self.conns.values() if c.id != conn.id})
            elif op == "cursor":
                x, y = m.get("x"), m.get("y")
                msg = {"type": "cursor", "conn": conn.id, "name": conn.name, "leader": conn.leader,
                       "x": float(x) if isinstance(x, (int, float)) else None, "y": float(y) if isinstance(y, (int, float)) else None}
                self.broadcast(msg, only={c.id for c in self.conns.values() if c.id != conn.id})
            return
        if op == "ui":
            if conn.seat == "atc":
                from . import recorder
                ev = m.get("ev") or []
                wx = next((e[2].get("wx") for e in reversed(ev) if isinstance(e, list) and len(e) == 3 and e[1] == "v"
                           and isinstance(e[2], dict)), None)
                recorder.ui(self.eid, conn.user.get("id"), conn.id, time.time(), m.get("now", 0), ev, wx)
            return
        if op == "ptt":
            on = bool(m.get("on"))
            if on != conn.ptt:
                conn.ptt = on
                self.broadcast({"type": "ptt", "conn": conn.id, "name": conn.name, "seat": conn.seat, "on": on})
                self.record("ptt", conn=conn.id, name=conn.name, seat=conn.seat, on=on)
        elif op == "call":
            unit = str(m.get("unit", "")).upper()
            if unit not in config.ADJACENT_UNITS:
                conn.put({"type": "error", "error": f"unknown unit {unit}"})
                return
            if any(conn.id in c.parties() for c in self.calls.values()):
                conn.put({"type": "error", "error": "hang up the current call first"})
                return
            if conn.seat == "atc":
                from_atc = True
            elif conn.seat in ("pilot", "instructor"):
                from_atc = False
            else:
                return
            call = Call(next(self._call_ids), unit, conn, from_atc)
            self.calls[call.id] = call
            self.record("call", call=call.id, unit=unit, direction="atc_to_unit" if from_atc else "unit_to_atc",
                        by=conn.name, state="ringing", caller=conn.id)
            self.push_calls()
        elif op == "answer":
            call = self.calls.get(int(m.get("call", 0) or 0))
            if call is None or call.state != "ringing" or conn.id == call.caller:
                return
            # the controller answers the units' calls; pseudo-pilots and instructors answer the controller's
            if (call.from_atc and conn.seat not in ("pilot", "instructor")) or (not call.from_atc and conn.seat != "atc"):
                if not (conn.seat == "instructor" and not call.from_atc and not self._has_seat("atc")):
                    conn.put({"type": "error", "error": "this call is not for your seat"})
                    return
            if self.call_of(conn):
                conn.put({"type": "error", "error": "hang up the current call first"})
                return
            call.answerer, call.state, call.t_answer = conn.id, "active", time.time()
            self.record("call", call=call.id, unit=call.unit, by=conn.name, state="active", caller=call.caller,
                        answerer=conn.id)
            self.push_calls()
        elif op == "hangup":
            call = self.calls.get(int(m.get("call", 0) or 0))
            if call is None:
                return
            if conn.id not in call.parties() and conn.seat != "instructor":
                return
            self.end_call(call, conn.name)

    def _has_seat(self, seat: str) -> bool:
        return any(c.seat == seat for c in self.conns.values())

    def end_call(self, call: Call, by: str) -> None:
        self.calls.pop(call.id, None)
        self.record("call", call=call.id, unit=call.unit, by=by, state="ended")
        self.push_calls()

    def push_calls(self) -> None:
        self.broadcast({"type": "calls", "calls": self.calls_public()})

    # ---------------------------------------------------------------- audio
    def on_audio(self, conn: Conn, payload: bytes) -> None:
        if not payload or len(payload) > 16000:
            return
        from . import recorder
        if self.open:
            if conn.muted:
                return
            frame = struct.pack("<BH", 2, conn.id) + payload
            for c in list(self.conns.values()):
                if c.id != conn.id:
                    c.put(frame)
            return
        if conn.ptt:
            chan, to = 0, [c for c in self.conns.values() if c.id != conn.id]
        else:
            call = self.call_of(conn)
            if call is None:
                return
            chan = 1
            to = [c for c in self.conns.values() if c.id != conn.id and
                  (c.id in call.parties() or c.seat == "instructor")]
        frame = struct.pack("<BH", chan, conn.id) + payload
        for c in to:
            c.put(frame)
        recorder.audio(self.eid, time.time(), chan, conn.id, payload)

    # ---------------------------------------------------------------- membership
    def join(self, ws: WebSocket, user: dict, seat: str) -> Conn:
        c = Conn(next(self._ids), ws, user, seat)
        self.conns[c.id] = c
        self.record("join", conn=c.id, name=c.name, seat=seat, user_id=user.get("id"))
        self.broadcast({"type": "roster", "roster": self.roster()})
        return c

    def leave(self, c: Conn) -> None:
        self.conns.pop(c.id, None)
        for call in list(self.calls.values()):
            if c.id in call.parties():
                self.end_call(call, c.name)
        if c.ptt:
            self.broadcast({"type": "ptt", "conn": c.id, "name": c.name, "seat": c.seat, "on": False})
            self.record("ptt", conn=c.id, name=c.name, seat=c.seat, on=False)
        self.record("leave", conn=c.id, name=c.name, seat=c.seat)
        self.broadcast({"type": "roster", "roster": self.roster()})


class Hub:
    def __init__(self):
        self.rooms: Dict[str, Room] = {}

    def room(self, eid: str, open_call: bool = False) -> Room:
        r = self.rooms.get(eid)
        if r is None:
            r = self.rooms[eid] = Room(eid, open_call)
        return r

    def open_room(self, eid: str) -> Room:
        return self.room(eid)

    def close_room(self, eid: str) -> None:
        r = self.rooms.pop(eid, None)
        if r:
            for c in list(r.conns.values()):
                c.put({"type": "closed"})
                c.put(None)


hub = Hub()


@router.websocket("/sessions/{sid}/comms")
async def comms(ws: WebSocket, sid: str):
    user = current_user(ws) or (LOCAL_ADMIN if config.AUTH == "off" else None)
    seat = seat_for(sid, user) if user else None
    if seat is None or manager is None or sid not in manager.sessions:
        await ws.close(code=4403)
        return
    await ws.accept()
    await serve(ws, hub.room(sid), user, seat)


async def serve(ws: WebSocket, room: Room, user: dict, seat: str, leader: bool = False) -> None:
    """One participant's audio and control over an accepted WebSocket, until it closes."""
    conn = room.join(ws, user, seat)
    if leader:
        conn.leader = True
        room.broadcast({"type": "roster", "roster": room.roster()})
    conn.put({"type": "hello", "me": conn.public(), "roster": room.roster(), "calls": room.calls_public(),
              "units": config.ADJACENT_UNITS, "unit_names": config.UNIT_NAMES, "open": room.open, "sync": room.sync})

    async def sender():
        while True:
            item = await conn.q.get()
            if item is None:
                await ws.close()
                return
            if isinstance(item, (bytes, bytearray)):
                await ws.send_bytes(bytes(item))
            else:
                await ws.send_text(json.dumps(item))

    async def receiver():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            if m.get("bytes") is not None:
                room.on_audio(conn, m["bytes"])
            elif m.get("text"):
                try:
                    room.on_control(conn, json.loads(m["text"]))
                except (ValueError, TypeError):
                    pass

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for t in tasks:
            t.cancel()
        room.leave(conn)
