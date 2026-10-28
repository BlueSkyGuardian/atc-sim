"""Sessions: one running scenario each, with its own clock thread."""
from __future__ import annotations

import io
import json
import os
import pickle
import re
import shutil
import struct
import threading
import time
import uuid
import zlib
from typing import Dict, List, Optional

from . import airspace as airspace_mod
from .geometry import SEP_H_NM, SEP_V_FT
from .scenario import build_world, load_scenario, public_view
from .world import World


# the 10-minute look-ahead behind the radar pages is the heavy part of the simulator (a few seconds of CPU on a dense
# scenario): it is shared per session, rerun every PRED_MIN_INTERVAL_S at most, PRED_WORKERS at a time server-wide
import os as _os
PRED_MIN_INTERVAL_S = float(_os.environ.get("ATC_PRED_INTERVAL_S", "8"))
_pred_slots = threading.BoundedSemaphore(max(1, int(_os.environ.get("ATC_PRED_WORKERS", "1"))))
SNAP_S = 10.0                      # the timeline can rewind the simulation to any of these points


def _pid(o):
    """Stable name of a shared object in a snapshot (the same in another process, for a restart)."""
    from .airspace import Airspace
    from .performance import TypePerformance
    from .weather import Weather
    if isinstance(o, Weather):
        return ("W",)
    if isinstance(o, Airspace):
        return ("A",)
    if isinstance(o, TypePerformance):
        return ("P", o.icao, round(float(o.dT), 3))
    return None


class Session:
    def __init__(self, scenario_name: str, mode: str = "realtime", time_factor: float = 1.0,
                 autostart: bool = True, label: Optional[str] = None, auto_assume: Optional[bool] = None,
                 sid: Optional[str] = None, scn: Optional[dict] = None, world: Optional[World] = None):
        if mode not in ("realtime", "stepped"):
            raise ValueError("mode must be 'realtime' or 'stepped'")
        self.id = sid or uuid.uuid4().hex[:8]
        self.persist_dir: Optional[str] = None     # set by the platform: the exercise survives a restart
        self.label = label or ""
        self._shared_sep, self._shared_at, self._shared_cost = None, 0.0, 0.0
        self._multi = False                # connected exercise: instructor + controller + pseudo-pilot seats
        self.scenario_name = scenario_name
        self.scn = scn if scn is not None else load_scenario(scenario_name)
        if auto_assume is not None:
            self.scn["auto_assume"] = bool(auto_assume)
        self.world: World = world if world is not None else build_world(self.scn)
        self.mode = mode
        self.time_factor = max(0.1, min(float(time_factor), 100.0))
        self.lock = threading.RLock()
        self.created_at = time.time()
        self.clients: List[dict] = []
        self.status = "created"
        self._stop = threading.Event()
        self._snaps: List[tuple] = []     # (sim time, compressed world) every SNAP_S, for the timeline's rewind
        self.rewinds = 0
        self._persisted_frames = 0
        self._snapshot()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"session-{self.id}")
        self._thread.start()
        if autostart and mode == "realtime":
            self.status = "running"
        elif mode == "stepped":
            self.status = "paused"

    @property
    def multi(self) -> bool:
        return self._multi

    @multi.setter
    def multi(self, v: bool) -> None:
        self._multi = bool(v)
        self.world.multi = self._multi
        if self._multi:                    # exercises: aircraft deviate only when a pseudo-pilot makes them
            self.world.auto_wx = False

    @property
    def solo(self) -> bool:
        return bool(getattr(self, "_solo", False))

    @solo.setter
    def solo(self, v: bool) -> None:
        """Solo radar: the controller's seat of an exercise, with simulated pilots that fly what is entered."""
        self._solo = bool(v)
        if self._solo:
            self.multi = True
            self.world.auto_pilot = True
            self.world.auto_wx = True

    # ------------------------------------------------------------------ clock
    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            if self.status == "running" and self.mode == "realtime":
                with self.lock:
                    if self.status == "running":
                        try:
                            self._tick()
                        except Exception:              # noqa: BLE001 - one bad step must not stop the exercise's clock
                            self._tick_errors = getattr(self, "_tick_errors", 0) + 1
                            import logging
                            import traceback
                            logging.getLogger("atcsim").error("session %s: step failed at T+%s\n%s",
                                                              getattr(self, "id", "?"), getattr(self.world, "t", "?"),
                                                              traceback.format_exc())
                            if self._tick_errors > 50:
                                self.status = "paused"     # failing every second: stop rather than spin
                next_tick += 1.0 / self.time_factor
                delay = next_tick - time.monotonic()
                if delay < -1.0:            # fell behind (e.g. very high time factor) -> resync
                    next_tick = time.monotonic()
                    delay = 0
                self._stop.wait(max(0.0, delay))
            else:
                next_tick = time.monotonic()
                self._stop.wait(0.05)

    def _tick(self) -> bool:
        """One simulated second (the caller holds the lock). False when nothing can advance any more."""
        self.world.step(1.0)
        self._maybe_snapshot()
        if self.world.finished:
            self.status = "finished"
            return False
        return True

    def stop(self) -> None:
        self._stop.set()

    def control(self, action: str, seconds: float = 1.0, time_factor: Optional[float] = None,
                mode: Optional[str] = None) -> dict:
        with self.lock:
            if action in ("start", "resume"):
                if self.status != "finished":
                    self.mode = "realtime"
                    self.status = "running"
            elif action == "pause":
                if self.status != "finished":
                    self.status = "paused"
            elif action == "set_speed":
                if time_factor is None:
                    raise ValueError("time_factor required")
                self.time_factor = max(0.1, min(float(time_factor), 100.0))
            elif action == "set_mode":
                if mode not in ("realtime", "stepped"):
                    raise ValueError("mode must be realtime|stepped")
                self.mode = mode
                if mode == "stepped" and self.status == "running":
                    self.status = "paused"
            elif action == "step":
                if self.status == "running":
                    raise ValueError("pause the session (or use stepped mode) before stepping")
                n = int(max(1, min(float(seconds), 7200)))
                for _ in range(n):
                    if not self._tick():
                        break
            elif action == "auto_wx":          # simulated pilots deviate around weather by themselves (or not)
                self.world.auto_wx = bool(time_factor if time_factor is not None else True)
            elif action == "restart":
                auto = getattr(self.world, "auto_wx", True)
                self.world = build_world(self.scn)
                self.world.multi = self._multi
                self.world.auto_wx = auto
                self.world.auto_pilot = self.solo
                self.status = "running" if self.mode == "realtime" else "paused"
                self._snaps = []
                self.rewinds += 1
                self._persisted_frames = 0
                pd, self.persist_dir = self.persist_dir, None
                self._snapshot()
                self.persist_dir = pd
                if pd:
                    try:
                        self._persist(rewrite=True)
                    except OSError:
                        pass
            elif action == "rewind":           # back to a point of the timeline; paused there
                return self.rewind(float(seconds))
            else:
                raise ValueError("action must be start|resume|pause|set_speed|set_mode|step|restart|auto_wx|rewind")
            self._persist_meta()
            return self.summary()

    # ------------------------------------------------------------------ snapshots / rewind
    def _snapshot(self) -> None:
        w = self.world
        rec = w.recording
        w.recording = {"every_s": rec["every_s"], "frames": [], "meta": rec["meta"]}   # the replay frames are kept apart

        class _P(pickle.Pickler):
            def persistent_id(self, o):
                return _pid(o)
        try:
            f = io.BytesIO()
            _P(f, protocol=5).dump(w)
        finally:
            w.recording = rec
        t = round(w.t, 1)
        blob = zlib.compress(f.getvalue(), 1)
        rewrite = bool(self._snaps) and self._snaps[-1][0] >= t
        self._snaps = [s for s in self._snaps if s[0] < t] + [(t, blob)]
        if self.persist_dir:
            try:
                self._persist(rewrite)
            except OSError:
                pass

    def _load_world(self, blob: bytes) -> World:
        from . import performance
        base = self.world

        class _U(pickle.Unpickler):
            def persistent_load(self, k):
                if k[0] == "W":
                    return base.env.weather
                if k[0] == "A":
                    return base.env.airspace
                return performance.get(k[1], k[2])
        w = _U(io.BytesIO(zlib.decompress(blob))).load()
        asp = w.env.airspace                       # points added by live actions (pop-up aircraft): back in it
        for k in getattr(w, "live_cops", []):
            if k in w.waypoints:
                asp.boundary_points.setdefault(k, w.waypoints[k])
                asp.cop_points.add(k)
        return w

    # ------------------------------------------------------------------ surviving a restart
    def _meta(self) -> dict:
        return {"id": self.id, "scenario": self.scenario_name, "label": self.label, "mode": self.mode,
                "time_factor": self.time_factor, "multi": self.multi, "solo": self.solo, "rewinds": self.rewinds,
                "created_at": self.created_at}

    def _persist(self, rewrite: bool = False) -> None:
        """Append the latest snapshot (and the replay frames since the last one) to the exercise's folder; rewrite
        them after a rewind or a restart. The scenario itself is kept with them, as it was at the start."""
        d = self.persist_dir
        os.makedirs(d, exist_ok=True)
        if not os.path.exists(os.path.join(d, "scenario.json")):
            with open(os.path.join(d, "scenario.json"), "w") as f:
                json.dump(self.scn, f)
        frames = self.world.recording["frames"]
        if rewrite:
            with open(os.path.join(d, "snaps.bin.tmp"), "wb") as f:
                for t, b in self._snaps:
                    f.write(struct.pack("<dI", t, len(b)) + b)
            os.replace(os.path.join(d, "snaps.bin.tmp"), os.path.join(d, "snaps.bin"))
            with open(os.path.join(d, "frames.jsonl.tmp"), "w") as f:
                for fr in frames:
                    f.write(json.dumps(fr) + "\n")
            os.replace(os.path.join(d, "frames.jsonl.tmp"), os.path.join(d, "frames.jsonl"))
        else:
            t, b = self._snaps[-1]
            with open(os.path.join(d, "snaps.bin"), "ab") as f:
                f.write(struct.pack("<dI", t, len(b)) + b)
            with open(os.path.join(d, "frames.jsonl"), "a") as f:
                for fr in frames[self._persisted_frames:]:
                    f.write(json.dumps(fr) + "\n")
        self._persisted_frames = len(frames)
        self._persist_meta()

    def _persist_meta(self) -> None:
        if not self.persist_dir:
            return
        try:
            os.makedirs(self.persist_dir, exist_ok=True)
            p = os.path.join(self.persist_dir, "meta.json")
            with open(p + ".tmp", "w") as f:
                json.dump(self._meta(), f)
            os.replace(p + ".tmp", p)
        except OSError:
            pass

    def forget(self) -> None:
        """The exercise has ended: its saved state is not needed any more."""
        if self.persist_dir:
            shutil.rmtree(self.persist_dir, ignore_errors=True)
            self.persist_dir = None

    @classmethod
    def restore(cls, d: str) -> "Session":
        """A session saved in folder d, at its last snapshot, paused (after a server restart)."""
        with open(os.path.join(d, "meta.json")) as f:
            meta = json.load(f)
        with open(os.path.join(d, "scenario.json")) as f:
            scn = json.load(f)
        s = cls(meta["scenario"], mode=meta.get("mode", "realtime"), time_factor=meta.get("time_factor", 1.0),
                autostart=False, label=meta.get("label"), sid=meta["id"], scn=scn)
        snaps = []
        with open(os.path.join(d, "snaps.bin"), "rb") as f:
            raw = f.read()
        i = 0
        while i + 12 <= len(raw):
            t, n = struct.unpack_from("<dI", raw, i)
            if i + 12 + n > len(raw):
                break                                  # cut short by the stop: keep the complete ones
            snaps.append((t, raw[i + 12:i + 12 + n]))
            i += 12 + n
        if not snaps:
            raise ValueError("no snapshot")
        frames = []
        if os.path.exists(os.path.join(d, "frames.jsonl")):
            with open(os.path.join(d, "frames.jsonl")) as f:
                for line in f:
                    try:
                        frames.append(json.loads(line))
                    except ValueError:
                        break
        with s.lock:
            w = s._load_world(snaps[-1][1])
            meta = w.recording.get("meta") or {}
            w.recording = s.world.recording
            w.recording["meta"].update(meta)
            w.recording["frames"] = [fr for fr in frames if fr["t"] <= snaps[-1][0] + 1e-6]
            w._last_frame_t = w.recording["frames"][-1]["t"] if w.recording["frames"] else -1e9
            s.world = w
            s._snaps = snaps
            if meta.get("solo"):
                s.solo = True
            elif meta.get("multi"):
                s.multi = True
            s.status = "paused"
            s.rewinds = int(meta.get("rewinds", 0)) + 1
            s.created_at = meta.get("created_at", s.created_at)
            s.persist_dir = d
            s._persisted_frames = len(w.recording["frames"])
            s._persist(rewrite=True)
            w._event("restored", text=f"server restarted: the exercise resumes at T+{int(w.t // 60):02d}:{int(w.t % 60):02d}, paused")
        return s

    def _maybe_snapshot(self) -> None:
        if self.world.t - (self._snaps[-1][0] if self._snaps else -1e9) >= SNAP_S - 1e-6:
            self._snapshot()

    def timeline(self) -> dict:
        return {"points": [s[0] for s in self._snaps], "every_s": SNAP_S, "rewinds": self.rewinds}

    def rewind(self, t: float) -> dict:
        """Put the simulation back to the last saved point at or before t (every SNAP_S): the world as it was then
        (aircraft, clearances, requests, CPDLC, chat, metrics), paused. The replay frames after it are dropped."""
        with self.lock:
            cand = [s for s in self._snaps if s[0] <= t + 1e-6]
            if not cand:
                raise ValueError("nothing saved that early")
            st, blob = cand[-1]
            w = self._load_world(blob)
            old = self.world
            meta = dict(w.recording.get("meta") or {}, **old.recording.get("meta", {}))
            w.recording = old.recording
            w.recording["meta"] = meta
            w.recording["frames"] = [f for f in w.recording["frames"] if f["t"] <= st + 1e-6]
            w._last_frame_t = w.recording["frames"][-1]["t"] if w.recording["frames"] else -1e9
            self.world = w
            self._snaps = [s for s in self._snaps if s[0] <= st + 1e-6]
            self._sep_key = None
            self._shared_sep, self._shared_at = None, 0.0
            if self.status != "finished" or w.t < w.duration_s:
                self.status = "paused"
            self.rewinds += 1
            if self.persist_dir:
                try:
                    self._persist(rewrite=True)
                except OSError:
                    pass
            w._event("rewind", text=f"simulation rewound to T+{int(st // 60):02d}:{int(st % 60):02d}")
            out = self.summary()
            out["rewound_to_s"] = st
            return out

    # ------------------------------------------------------------------ views
    def summary(self) -> dict:
        w = self.world
        return {
            "session_id": self.id, "label": self.label, "scenario": self.scenario_name,
            "title": self.scn.get("title", ""), "status": self.status, "mode": self.mode,
            "time_factor": self.time_factor, "sim_time_s": round(w.t, 1), "duration_s": w.duration_s,
            "active_aircraft": len(w.active_aircraft()),
            "auto_wx": getattr(w, "auto_wx", True),
            "pending_requests": sum(1 for r in w.requests.values() if r.status == "pending"),
            "clients": len(self.clients),
            "rewinds": getattr(self, "rewinds", 0),
            "rewind_from_s": self._snaps[0][0] if getattr(self, "_snaps", None) else None,
        }

    def separation(self, lookahead_s: float = 600.0, include_inbound: bool = True, all_pairs: bool = False,
                   max_pair_distance_nm: float = 40.0) -> dict:
        """Current pairs + predicted conflicts. Cached per sim time (prediction runs the full aircraft model).
        The look-ahead runs on a copy of the world taken under the lock, so the (slow) prediction never holds
        the session lock: the clock keeps running and the stream keeps flowing while it is computed."""
        with self.lock:
            w = self.world
            key = (w.t, lookahead_s, include_inbound, all_pairs, id(w))
            pairs = w.current_pairs(max_pair_distance_nm)
            cached = self._sep_val if getattr(self, "_sep_key", None) == key else None
            snap = None if cached is not None else w.clone()
        if cached is None:
            an = snap.analyze(snap.predict(lookahead_s, extra=None, include_inbound=include_inbound),
                              lookahead_conflicts_only=not all_pairs)
            cached = {"sim_time_s": round(key[0], 1), "minima": minima(snap),
                      "lookahead_s": lookahead_s, "predicted_conflicts": an["conflicts"],
                      "predicted_area_infringements": an["area_infringements"],
                      "predicted_weather_penetrations": an["weather_penetrations"]}
            with self.lock:
                self._sep_key, self._sep_val = key, cached
        res = dict(cached)
        res["current_pairs"] = pairs
        return res

    def separation_shared(self, lookahead_s: float = 600.0) -> Optional[dict]:
        """The look-ahead for the live radar pages: one computation per session shared by every connected page,
        rerun only when the previous result is older than max(PRED_MIN_INTERVAL_S, 3 x its computing time) of wall
        time, and at most PRED_WORKERS computations at once over the whole server (a small server stays
        responsive). Returns the latest result (possibly a few seconds old), or None before the first one."""
        now = time.monotonic()
        with self.lock:
            res = getattr(self, "_shared_sep", None)
            due = res is None or now - self._shared_at >= max(PRED_MIN_INTERVAL_S, 3 * self._shared_cost)
            if not due or getattr(self, "_shared_busy", False) or self.status == "finished":
                return res
            self._shared_busy = True
        try:
            if not _pred_slots.acquire(timeout=0.05):
                return res
            try:
                t0 = time.monotonic()
                out = self.separation(lookahead_s)
                with self.lock:
                    self._shared_sep, self._shared_at = out, time.monotonic()
                    self._shared_cost = time.monotonic() - t0
                return out
            finally:
                _pred_slots.release()
        finally:
            with self.lock:
                self._shared_busy = False

    def briefing(self) -> dict:
        """Everything a client needs when it connects: scenario, sector, waypoints, routes, areas."""
        w = self.world
        return {
            "session": self.summary(),
            "scenario": public_view(self.scn),
            "sector": sector_description(w),
            "areas": [a.snapshot(w.t) for a in w.areas.values()],
            "weather": weather_description(w),
            "arrivals": arrivals_description(w),
            "aircraft_types": sorted({a.icao for a in w.aircraft.values()}),
            "weather_products": ["/weather", "/weather/radar?product=cappi|composite|echo_tops|edr&fl=",
                                 "/weather/hazards?fl=", "/weather/column?x=&y=", "/weather/wind?fl="],
            "state": w.state(),
        }


def arrivals_mod_procedures(ad) -> dict:
    from .arrivals import procedures
    try:
        return procedures(ad)
    except Exception:  # noqa: BLE001
        return {"sids": [], "stars": [], "holds": []}


def arrivals_description(w: World) -> dict:
    return {
        "aerodromes": [{**a.snapshot(), "procedures": arrivals_mod_procedures(a)} for a in w.aerodromes.values()],
        "sequencing_fixes": [{**q.snapshot(), "approach_with_runway_in_use": w.approach(f, q.aerodrome)}
                             for f, q in w.seq.items()],
        "note": "every flight whose exit is a sequencing fix is an arrival to that aerodrome; it requests descent "
                "at its computed top of descent, which depends on the runway in use (straight-in or reversal)",
    }


def weather_description(w: World) -> dict:
    return w.env.weather.summary(w.t)


def minima(w: Optional[World] = None) -> dict:
    h = float(w.sep_h) if w is not None else SEP_H_NM
    return {"horizontal_nm": h, "vertical_ft": SEP_V_FT,
            "vertical_non_rvsm_ft": 2000, "rvsm_band": [290, 410],
            "rules": [f"{h:g} NM horizontally (radar separation minimum of this airspace / scenario)",
                      "1000 ft below FL290, and between RVSM-approved aircraft from FL290 to FL410 inclusive",
                      "2000 ft in FL290-FL410 when either aircraft is non-RVSM (not approved, unable RVSM)",
                      "2000 ft when either aircraft is above FL410",
                      f"military formation: 2000 ft from any other aircraft at any level, or {h:g} NM + formation "
                      "extent (default 1 NM) horizontally"]}


MINIMA = minima()          # training default, kept for importers


def sector_description(w: Optional[World] = None) -> dict:
    asp = w.airspace if w is not None else airspace_mod.get("training")
    d = asp.describe()
    if w is not None:
        d["waypoints"] = {k: [round(v[0], 2), round(v[1], 2)] for k, v in w.waypoints.items() if not k.startswith("@")}
        d["vertical_limits"] = {"lower_fl": w.lower_fl, "upper_fl": w.upper_fl}
    d["headings"] = "degrees, 1-360, relative to grid north (+y)" + (
        "; grid north = true north on the central meridian, within about 2 deg elsewhere" if asp.projection else
        " (true)")
    d["separation_minima"] = minima(w)
    # transfer points generated where a route crosses the boundary of the controlled sectors between two published
    # points (e.g. CIBSA between IBLIV and SAMAR on an internal sector line): used internally, not drawn as points
    d["coordination_points"] = sorted(getattr(asp, "cop_points", ()) or ())
    return {
        **d,
        "pilot_delay_s": w.pilot_delay_s if w else None,
        "instruction_types": {
            "altitude": {"fl": "int, multiple of 10", "rate_fpm": "optional vertical rate, ft/min",
                         "rate_mode": "optional: 'min' (at X or greater, default), 'max' (at X or less), 'exact'"},
            "heading": {"heading": "1-360 (true)", "turn": "optional 'left'|'right'"},
            "direct": {"waypoint": "waypoint name; a transfer point on the boundary becomes the new exit"},
            "speed": {"mach": "e.g. 0.76 (flown above the conversion altitude)",
                      "conversion_kt": "IAS to fly after converting from Mach, e.g. 280",
                      "ias_kt": "fly this IAS now, at every level", "resume_normal": "true = own speed schedule"},
            "altitude+speed": "an altitude instruction can carry mach / conversion_kt / ias_kt: "
                              "'descend FL250, maintain Mach .76, on conversion 280 knots'",
            "resume": {},
            "reply": {"request_id": "Rxxx", "decision": "'approved' | 'unable' ('roger' for pilot reports)"},
            "deviation": {"side": "left|right|either", "max_nm": "optional max offset",
                          "then_direct": "optional waypoint to rejoin"},
        },
    }


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Session] = {}
        self.lock = threading.Lock()

    def create(self, **kw) -> Session:
        s = Session(**kw)
        with self.lock:
            self.sessions[s.id] = s
        return s

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise KeyError(sid)
        return s

    def delete(self, sid: str) -> None:
        with self.lock:
            s = self.sessions.pop(sid, None)
        if s:
            s.stop()
            s.forget()

    def list(self) -> List[dict]:
        return [s.summary() for s in self.sessions.values()]


# --- free-text command parser (used by the radar page and POST /command) -------------
_CMD_HELP = ("CALLSIGN then commands: FL360 [V1500] | H270 / L270 / R270 (turn left/right) | D S2 | "
             "M76 (Mach) | K280 (on conversion 280 kt) | S280 (280 kt now) | SN (resume normal speed) | DEV / DEVL20 / DEVR (weather deviation) | "
             "RES | APP [Rxxx] | UNA [Rxxx] | ROG [Rxxx] (acknowledge a report) | AS (assume on first contact) | REL (decontrol).  "
             "e.g. 'RAM101 FL370 H250'")


def parse_command(text: str, world: World) -> List[dict]:
    toks = text.strip().upper().split()
    if len(toks) < 2:
        raise ValueError("expected: " + _CMD_HELP)
    cs = toks[0]
    out = []
    i = 1
    while i < len(toks):
        tk = toks[i]
        m = re.fullmatch(r"(?:FL|F|C|DES|CL)(\d{2,3})", tk)
        if m:
            out.append({"callsign": cs, "type": "altitude", "fl": int(m.group(1))}); i += 1; continue
        m = re.fullmatch(r"(H|HDG|L|R|HL|HR|TL|TR)(\d{1,3})", tk)
        if m:
            p = m.group(1)
            turn = "left" if p in ("L", "HL", "TL") else "right" if p in ("R", "HR", "TR") else None
            out.append({"callsign": cs, "type": "heading", "heading": int(m.group(2)), "turn": turn}); i += 1; continue
        m = re.fullmatch(r"V(\d{3,4})([+=-]?)", tk)
        if m and out and out[-1]["type"] == "altitude":
            out[-1]["rate_fpm"] = int(m.group(1))
            out[-1]["rate_mode"] = {"-": "max", "=": "exact"}.get(m.group(2), "min"); i += 1; continue
        m = re.fullmatch(r"(?:S|SPD|IAS)(\d{3})", tk)
        mm = re.fullmatch(r"M\.?0?(\d{2})", tk)
        mk = re.fullmatch(r"K(\d{3})", tk)
        if m or mm or mk:
            key, val = (("ias_kt", int(m.group(1))) if m else ("mach", int(mm.group(1)) / 100.0) if mm
                        else ("conversion_kt", int(mk.group(1))))
            # speed after a level in the same command = speed restriction of that clearance
            host = next((o for o in reversed(out) if o["type"] in ("altitude", "speed")), None)
            if host is None:
                host = {"callsign": cs, "type": "speed"}
                out.append(host)
            host[key] = val
            i += 1; continue
        if tk in ("ASSUME", "AS", "ASS", "RC"):
            out.append({"callsign": cs, "type": "assume"}); i += 1; continue
        if tk in ("REL", "RELEASE", "DECONTROL", "DECON"):
            out.append({"callsign": cs, "type": "release"}); i += 1; continue
        if tk in ("SN", "RNS", "NORMAL"):
            out.append({"callsign": cs, "type": "speed", "resume_normal": True}); i += 1; continue
        m = re.fullmatch(r"(?:D|DCT)([A-Z]{1,5}\d*)", tk)
        if m and m.group(1) in world.waypoints:
            out.append({"callsign": cs, "type": "direct", "waypoint": m.group(1)}); i += 1; continue
        m = re.fullmatch(r"DEV([LR]?)(\d{0,3})", tk)
        if m:
            ins = {"callsign": cs, "type": "deviation",
                   "side": {"L": "left", "R": "right"}.get(m.group(1), "either")}
            if m.group(2):
                ins["max_nm"] = int(m.group(2))
            out.append(ins); i += 1; continue
        if tk in ("RES", "RESUME", "RON"):
            out.append({"callsign": cs, "type": "resume"}); i += 1; continue
        if tk in ("APP", "APPROVE", "APPROVED", "UNA", "UNABLE", "ROG", "ROGER"):
            dec = "approved" if tk.startswith("APP") else "roger" if tk.startswith("ROG") else "unable"
            rid = None
            if i + 1 < len(toks) and re.fullmatch(r"R\d+", toks[i + 1]):
                rid = toks[i + 1]; i += 1
            if rid is None:
                pend = [r for r in world.requests.values() if r.callsign == cs and r.status == "pending"]
                if not pend:
                    raise ValueError(f"{cs} has no pending request")
                rid = pend[0].id
            out.append({"callsign": cs, "type": "reply", "request_id": rid, "decision": dec}); i += 1; continue
        raise ValueError(f"cannot parse '{tk}'. " + _CMD_HELP)
    return out
