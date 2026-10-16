"""The simulation world: time stepping, instructions, requests, separation and prediction."""
from __future__ import annotations

import copy
import itertools
import math
import re
from typing import Dict, List, Optional, Tuple

from .aircraft import Aircraft, Env, level_parity_ok
from .geometry import SEP_H_NM, SEP_V_FT, Point, api_hdg, dist, polyline_length
from .model import Area, Request
import numpy as np

from . import arrivals, pilot

ARRIVAL_FLOOR_FL = 100        # lowest level an arrival can be cleared to (after coordination)
INSTRUCTION_TYPES = ("altitude", "heading", "direct", "speed", "resume", "reply", "deviation", "assume")
MAX_EVENTS = 20000
PREDICT_DT = 4.0


class InstructionError(ValueError):
    pass


RVSM_LO_FT, RVSM_HI_FT = 29000.0, 41000.0
SEP_V_NON_RVSM_FT = 2000.0


def in_rvsm_band(alt_ft: float) -> bool:
    return RVSM_LO_FT - 50.0 <= alt_ft <= RVSM_HI_FT + 50.0


def vertical_minimum(alt_a: float, alt_b: float, non_rvsm: bool, formation: bool) -> float:
    """Applicable vertical separation minimum for a pair at these altitudes.
    * a military formation: 2000 ft from any other aircraft, at any level
    * both at or above FL290 and either above FL410: 2000 ft (conventional vertical separation above RVSM)
    * both in RVSM airspace (FL290-FL410) and either is non-RVSM (not approved / unable RVSM): 2000 ft
    * otherwise 1000 ft"""
    if formation:
        return SEP_V_NON_RVSM_FT
    lo, hi = (alt_a, alt_b) if alt_a <= alt_b else (alt_b, alt_a)
    if lo < RVSM_LO_FT - 50.0:
        return SEP_V_FT
    if hi > RVSM_HI_FT + 50.0 or non_rvsm:
        return SEP_V_NON_RVSM_FT
    return SEP_V_FT


def pair_minima(a: Aircraft, b: Aircraft, alt_a: Optional[float] = None, alt_b: Optional[float] = None,
                h_nm: float = SEP_H_NM):
    """(horizontal NM, vertical ft) separation minima for a pair. `h_nm` is the radar separation minimum of the
    airspace (5 NM in the training sector, 7 NM in the Agadir CTA, or the scenario's own). A formation's members
    spread around the leader, so its horizontal minimum is that plus the formation extent."""
    h = h_nm
    for ac in (a, b):
        if ac.formation:
            h = max(h, h_nm + float(ac.formation.get("extent_nm", 1.0)))
    v = vertical_minimum(a.alt_ft if alt_a is None else alt_a, b.alt_ft if alt_b is None else alt_b,
                         a.non_rvsm or b.non_rvsm, bool(a.formation or b.formation))
    return h, v


def speed_from_value(v: float) -> Tuple[str, float]:
    """Request/shorthand values: < 2 is a Mach number, otherwise IAS in knots."""
    v = float(v)
    return ("mach", round(v, 2)) if v < 2 else ("cas", float(int(round(v))))


def speed_text(mode: str, val: float) -> str:
    return f"Mach .{int(round(val * 100)):02d}" if mode == "mach" else f"{int(val)} knots"


class World:
    def __init__(self, meta: dict, env: Env, aircraft: List[Aircraft],
                 areas: List[Area], requests: List[dict], duration_s: float,
                 pilot_delay_s: float, lower_fl: int, upper_fl: int,
                 aerodromes: Optional[dict] = None, seq: Optional[dict] = None, events: Optional[List[dict]] = None):
        self.scheduled_events = sorted(events or [], key=lambda e: e["time"])
        self._ev_idx = 0
        self.persist_s = 240.0             # transferred traffic stays painted this long after leaving
        self.sep_h = SEP_H_NM              # horizontal radar separation minimum (set per airspace / scenario)
        self.auto_assume = False           # True: aircraft are assumed automatically on first contact
        self.aerodromes = aerodromes or {}
        self.seq = seq or {}
        self._approach_cache: Dict[tuple, dict] = {}
        self.meta = meta
        self.t = 0.0
        self.env = env
        self.waypoints = env.waypoints
        self.aircraft: Dict[str, Aircraft] = {a.callsign: a for a in aircraft}
        self.areas: Dict[str, Area] = {a.id: a for a in areas}
        self.scheduled_requests = sorted(requests, key=lambda r: r["time"])
        self._req_idx = 0
        self._deferred_req: List[dict] = []    # scripted requests waiting for the aircraft to be on frequency
        self.requests: Dict[str, Request] = {}
        self.duration_s = duration_s
        self.pilot_delay_s = pilot_delay_s
        self.lower_fl, self.upper_fl = lower_fl, upper_fl
        self.pending: List[dict] = []         # instructions waiting for pilot delay
        self.instructions: List[dict] = []    # full log
        # controller's label entries (connected mode): what the controller has entered in the system for each
        # aircraft (cleared level, speed, heading, direct). They do not move the aircraft - the pseudo-pilot does.
        self.entries: Dict[str, dict] = {}
        self.events: List[dict] = []
        self._seq = 0
        self._instr_seq = 0
        self.record = True
        # recording for replay: a compact frame of every aircraft on the picture every `every_s` sim seconds
        self.recording = {"every_s": 5.0, "frames": [], "meta": {}}
        self._last_frame_t = -1e9
        self.finished = False
        self._los: Dict[Tuple[str, str], dict] = {}
        self._inf: Dict[Tuple[str, str], dict] = {}
        self.m = {
            "los_events": 0, "los_seconds": 0.0, "los_pairs": [],
            "min_separation": {},  # pair -> min horizontal NM while vertical < 1000 ft
            "area_infringements": 0, "area_seconds": {"danger": 0.0, "restricted": 0.0, "prohibited": 0.0},
            "infringement_log": [],
            "instructions_issued": 0, "instructions_rejected": 0,
            "left_sector": [],
            "wx_seconds": {"30dbz": 0.0, "40dbz": 0.0, "50dbz": 0.0}, "wx_penetrations": [],
            "wx_self_deviations": 0, "turb_seconds": {"moderate": 0.0, "severe": 0.0},
            "sequencing": {}, "seq_spacing_violations": 0, "seq_level_violations": 0,
            "rvsm_violations": [], "rvsm_violation_seconds": 0.0,
            "wrong_direction_exits": [], "wrong_direction_clearances": 0,
        }
        self._rvsm_in: Dict[str, dict] = {}
        self._wx_in: Dict[str, dict] = {}
        for a in self.areas.values():
            a.advance(0.0)

    # ------------------------------------------------------------------ events
    def _event(self, etype: str, **data) -> None:
        if not self.record:
            return
        self._seq += 1
        ev = {"seq": self._seq, "time_s": round(self.t, 1), "type": etype}
        ev.update(data)
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]

    @property
    def airspace(self):
        return self.env.airspace

    def approach(self, fix: str, ad_id: str) -> dict:
        ad = self.aerodromes[ad_id]
        key = (fix, ad_id, ad.in_use)
        if key not in self._approach_cache:
            self._approach_cache[key] = arrivals.approach_path(self.waypoints[fix], ad, ad.in_use)
        return self._approach_cache[key]

    # ------------------------------------------------------------------ queries
    def active_aircraft(self) -> List[Aircraft]:
        return [a for a in self.aircraft.values() if a.active]

    def get_active(self, callsign: str) -> Aircraft:
        ac = self.aircraft.get(callsign.upper())
        if ac is None:
            raise InstructionError(f"unknown callsign {callsign}")
        if not ac.active:
            raise InstructionError(f"{ac.callsign} is not in the sector (status: {ac.status})")
        return ac

    # ------------------------------------------------------------------ instructions
    def validate(self, instr: dict, by_pilot: bool = False) -> dict:
        """Normalise and validate an instruction, including the pilot's performance check. Raises InstructionError.
        by_pilot: executed by a pseudo-pilot (connected mode) - the aircraft does what it was told on the frequency,
        whether or not the controller has assumed it in the system."""
        itype = str(instr.get("type", "")).lower()
        if itype not in INSTRUCTION_TYPES:
            raise InstructionError(f"unknown instruction type '{itype}', expected one of {INSTRUCTION_TYPES}")
        cs = str(instr.get("callsign", "")).upper()
        ac = self.get_active(cs)
        if not ac.on_frequency:
            d = dist(ac.pos, self.waypoints[ac.entry_wp])
            raise InstructionError(f"{cs} is not on your frequency yet (still with the previous sector, "
                                   f"{d:.0f} NM before {ac.entry_wp})")
        p = ac.perf
        out = {"type": itype, "callsign": cs}
        if itype == "assume":
            if ac.assumed:
                raise InstructionError(f"{cs} is already assumed")
            return out
        is_checkin_reply = (itype == "reply" and str(instr.get("request_id", "")) in self.requests
                            and self.requests[str(instr["request_id"])].kind == "checkin")
        if not ac.assumed and not is_checkin_reply and not by_pilot:
            raise InstructionError(f"{cs} is calling you but not assumed: assume it first ({cs} ASSUME)")
        if itype == "altitude":
            fl = int(instr.get("fl", instr.get("value", -1)))
            if fl % 10 != 0:
                raise InstructionError("flight level must be a multiple of 10 (e.g. 350)")
            seqfix = self.seq.get(ac.arr.get("fix")) if ac.destination else None
            # arrivals: the LoA level is what is normally given, but a lower level can be coordinated with the next
            # sector (to avoid levelling off too long) - allowed down to FL100, with a reminder to coordinate
            low = ARRIVAL_FLOOR_FL                 # below the sector floor / LoA: allowed, with a reminder
            if not (low <= fl <= self.upper_fl):
                raise InstructionError(f"FL{fl} is outside the levels you can assign to {cs} (FL{low}-FL{self.upper_fl})")
            climbing = fl * 100 > ac.alt_ft + 50
            if climbing:
                mode, val = ac.speed_target(fl * 100.0)
                ok, why = p.level_available(fl, mode, val, ac.mass)
                if not ok:
                    raise InstructionError(f"{cs} unable FL{fl}: {why}")
            out["fl"] = fl
            if instr.get("rate_fpm") is not None:
                r = float(instr["rate_fpm"])
                if r < 300:
                    raise InstructionError("rate_fpm must be at least 300")
                if climbing:
                    avail = ac.climb_rate_available((ac.alt_ft + fl * 100.0) / 2)
                    if r > avail:
                        raise InstructionError(f"{cs} unable {int(r)} ft/min, best rate about {int(avail // 100 * 100)} ft/min")
                else:
                    maxd = 1.5 * ac.descent_rate_idle()
                    if r > maxd:
                        raise InstructionError(f"{cs} unable {int(r)} ft/min, best rate about {int(maxd // 100 * 100)} ft/min")
                out["rate_fpm"] = r
            spec = self._parse_speed(ac, instr, fl * 100.0, allow_value=False)
            if spec is not None:
                out["speed"] = spec
        elif itype == "heading":
            h = float(instr.get("heading", instr.get("value", -1)))
            if not (0 <= h <= 360):
                raise InstructionError("heading must be 1..360")
            turn = instr.get("turn")
            if turn is not None:
                turn = str(turn).lower()
                if turn not in ("left", "right"):
                    raise InstructionError("turn must be 'left', 'right' or null")
            out["heading"] = 360 if round(h) % 360 == 0 else int(round(h))
            out["turn"] = turn
            if pilot.heading_into_weather(self, ac, float(out["heading"])):
                raise InstructionError(f"{cs} unable heading {out['heading']:03d}, weather on that heading")
        elif itype == "direct":
            wp = str(instr.get("waypoint", instr.get("value", ""))).upper()
            if wp not in self.waypoints or wp.startswith("@"):
                raise InstructionError(f"unknown waypoint {wp}")
            if dist(ac.pos, self.waypoints[wp]) < 1.0:
                raise InstructionError(f"{cs} is already at {wp}")
            from .geometry import bearing
            if pilot.heading_into_weather(self, ac, bearing(ac.pos, self.waypoints[wp])):
                raise InstructionError(f"{cs} unable direct {wp}, weather on the direct track")
            out["waypoint"] = wp
        elif itype == "deviation":
            side = str(instr.get("side", "either")).lower()
            if side not in ("left", "right", "either"):
                raise InstructionError("side must be left, right or either")
            max_nm = float(instr.get("max_nm", 1e9) or 1e9)
            rejoin = instr.get("then_direct")
            if rejoin is not None:
                rejoin = str(rejoin).upper()
                if rejoin not in self.waypoints:
                    raise InstructionError(f"unknown waypoint {rejoin}")
            plan = pilot.plan_deviation(self, ac, side, max_nm, rejoin)
            if plan is None:
                raise InstructionError(f"{cs} unable to deviate {side}"
                                       + (f" within {int(max_nm)} miles" if max_nm < 1e8 else "") + ", weather")
            out.update(side=side, max_nm=None if max_nm >= 1e8 else max_nm, then_direct=rejoin, plan=plan)
        elif itype == "speed":
            spec = self._parse_speed(ac, instr, ac.cleared_alt_ft)
            if spec is None:
                raise InstructionError("speed needs 'mach', 'conversion_kt', 'ias_kt' or 'resume_normal'")
            out["speed"] = spec
        elif itype == "reply":
            rid = str(instr.get("request_id", ""))
            req = self.requests.get(rid)
            if req is None or req.callsign != cs:
                raise InstructionError(f"no request {rid} from {cs}")
            if req.status != "pending":
                raise InstructionError(f"request {rid} is already {req.status}")
            dec = str(instr.get("decision", "")).lower()
            if req.type == "report":
                if dec not in ("roger", "acknowledged", "ack", "approved", "approve"):
                    raise InstructionError("a pilot report is acknowledged with decision 'roger'")
                out["request_id"] = rid
                out["decision"] = "roger"
                return out
            if dec not in ("approved", "approve", "unable", "deny", "denied"):
                raise InstructionError("decision must be 'approved' or 'unable'")
            out["request_id"] = rid
            out["decision"] = "approved" if dec.startswith("approv") else "unable"
        return out

    def _parse_speed(self, ac: Aircraft, instr: dict, target_alt_ft: float, allow_value: bool = True):
        """Speed part of an instruction -> {'mach', 'conversion_kt', 'ias_kt', 'resume_normal'} (validated)."""
        g = lambda *ks: next((instr[k] for k in ks if instr.get(k) is not None), None)
        if instr.get("resume_normal"):
            return {"resume_normal": True}
        mach, conv, ias = g("mach"), g("conversion_kt", "on_conversion_kt"), g("ias_kt", "cas_kt", "kt")
        if mach is None and conv is None and ias is None and allow_value and instr.get("value") is not None:
            mode, val = speed_from_value(instr["value"])
            mach, ias = (val, None) if mode == "mach" else (None, val)
        if mach is None and conv is None and ias is None:
            return None
        if ias is not None and (mach is not None or conv is not None):
            raise InstructionError("give either 'ias_kt' (knots now) or 'mach' / 'conversion_kt', not both")
        spec = {"mach": None if mach is None else round(float(mach), 2),
                "conversion_kt": None if conv is None else float(int(round(float(conv)))),
                "ias_kt": None if ias is None else float(int(round(float(ias))))}
        p, cs = ac.perf, ac.callsign
        if spec["mach"] is not None and not (p.mach_assign_min <= spec["mach"] <= p.mach_assign_max):
            raise InstructionError(f"{cs} unable Mach {spec['mach']:.2f}: {p.icao} range "
                                   f"M{p.mach_assign_min:.2f}-M{p.mach_assign_max:.2f}")
        for k in ("conversion_kt", "ias_kt"):
            v = spec[k]
            if v is not None and not (p.cas_assign_min <= v <= p.cas_assign_max):
                raise InstructionError(f"{cs} unable {int(v)} kt: {p.icao} range "
                                       f"{int(p.cas_assign_min)}-{int(p.cas_assign_max)} kt")
        # fly the resulting schedule over the whole band between present and cleared level
        saved = (ac.spd_mach, ac.spd_cas, ac.spd_ias_now)
        try:
            self._apply_speed(ac, spec)
            lo, hi = sorted((ac.alt_ft, target_alt_ft))
            alts = list(range(int(lo // 1000 * 1000), int(hi) + 1, 1000)) + [lo, hi]
            for alt in alts:
                if alt < lo - 1 or alt > hi + 1:
                    continue
                mode, val = ac.speed_target(alt)
                tas = p.tas_for(mode, val, alt)
                if tas < p.min_tas_buffet(alt, ac.mass):
                    raise InstructionError(f"{cs} unable {speed_text(mode, val)} at FL{int(alt // 100)}: "
                                           "too slow at present weight")
                if p.tas_for("mach", p.mmo - 0.005, alt) < tas - 0.5 or p.tas_for("cas", p.vmo, alt) < tas - 0.5:
                    raise InstructionError(f"{cs} unable {speed_text(mode, val)} at FL{int(alt // 100)}: "
                                           "exceeds VMO/MMO")
        finally:
            ac.spd_mach, ac.spd_cas, ac.spd_ias_now = saved
        return spec

    @staticmethod
    def _apply_speed(ac: Aircraft, spec: dict) -> None:
        if spec.get("resume_normal"):
            ac.spd_mach, ac.spd_cas, ac.spd_ias_now = None, None, False
            return
        if spec.get("ias_kt") is not None:
            ac.spd_cas, ac.spd_ias_now = spec["ias_kt"], True
            return
        if spec.get("mach") is not None:
            ac.spd_mach = spec["mach"]
            ac.spd_ias_now = False
        if spec.get("conversion_kt") is not None:
            ac.spd_cas = spec["conversion_kt"]
            ac.spd_ias_now = False

    @staticmethod
    def speed_phrase(spec: dict) -> str:
        if spec.get("resume_normal"):
            return "resume normal speed"
        if spec.get("ias_kt") is not None:
            return f"maintain {int(spec['ias_kt'])} knots"
        parts = []
        if spec.get("mach") is not None:
            parts.append(f"maintain {speed_text('mach', spec['mach'])}")
        if spec.get("conversion_kt") is not None:
            parts.append(f"on conversion {int(spec['conversion_kt'])} knots")
        return ", ".join(parts)

    def issue(self, instr: dict, source: str = "api", by_pilot: bool = False) -> dict:
        """Issue an instruction. Returns the instruction record (accepted or rejected)."""
        self._instr_seq += 1
        rec = {"id": f"I{self._instr_seq:04d}", "time_s": round(self.t, 1), "source": source,
               "request": instr}
        try:
            norm = self.validate(instr, by_pilot=by_pilot)
        except InstructionError as e:
            rec.update(status="rejected", reason=str(e))
            self.m["instructions_rejected"] += 1
            self.instructions.append(rec)
            self._event("instruction_rejected", instruction_id=rec["id"], reason=str(e), request=instr)
            return rec
        rec["instruction"] = norm
        if norm["type"] != "assume":
            self.m["instructions_issued"] += 1
        cs = norm["callsign"]

        rec["warnings"] = self._warnings(norm)
        if rec["warnings"] and norm["type"] == "altitude":
            self.m["wrong_direction_clearances"] += sum(1 for x in rec["warnings"] if "direction" in x)
        if norm["type"] == "assume":
            self._assume(self.aircraft[cs], rec["id"])
            rec.update(status="executed", execute_at_s=round(self.t, 1))
            self.instructions.append(rec)
            return rec
        if norm["type"] == "reply":
            req = self.requests[norm["request_id"]]
            if req.kind == "checkin":                      # answering the first call = assuming the aircraft
                self._assume(self.aircraft[cs], rec["id"])
                rec.update(status="executed", execute_at_s=round(self.t, 1))
                self.instructions.append(rec)
                return rec
            if norm["decision"] == "roger":
                self._resolve_request(req, "acknowledged", rec["id"])
                rec.update(status="executed", execute_at_s=round(self.t, 1))
                self.instructions.append(rec)
                self._event("instruction", instruction_id=rec["id"], callsign=cs, text=f"{cs} roger")
                return rec
            if norm["decision"] == "unable":
                self._resolve_request(req, "unable", rec["id"])
                ac_ = self.aircraft.get(cs)
                if ac_ is not None and ac_.wx.get("request_id") == req.id:
                    ac_.wx["phase"] = "refused"
                rec.update(status="executed", execute_at_s=round(self.t, 1))
                self.instructions.append(rec)
                self._event("instruction", instruction_id=rec["id"], callsign=cs,
                            text=f"{cs} unable {req.what()} ({req.id})")
                return rec
            if req.type == "weather_deviation":
                norm = {"type": "wx_plan", "callsign": cs, "plan": dict(req.value), "request_id": req.id}
                self._resolve_request(req, "approved", rec["id"])
            else:
                conv = {"callsign": cs, "type": req.type}
                conv.update({"altitude": {"fl": req.value}, "direct": {"waypoint": req.value},
                             "speed": {"value": req.value}}[req.type])
                try:
                    norm = self.validate(conv, by_pilot=by_pilot)
                except InstructionError as e:
                    rec.update(status="rejected", reason=f"cannot approve: {e}")
                    self.instructions.append(rec)
                    return rec
                rec["warnings"] = self._warnings(norm)
                if norm["type"] == "altitude":
                    self.m["wrong_direction_clearances"] += sum(1 for x in rec["warnings"] if "direction" in x)
            rec["instruction"] = norm

        self._match_requests(norm, rec["id"])
        rec.update(status="accepted", execute_at_s=round(self.t + self.pilot_delay_s, 1))
        self.pending.append(rec)
        self.instructions.append(rec)
        self._event("instruction", instruction_id=rec["id"], callsign=cs, text=self.phrase(norm))
        return rec

    def _warnings(self, n: dict) -> List[str]:
        """System alerts on a clearance (accepted anyway: the controller may have a reason)."""
        if n.get("type") == "direct":
            fra = str(self.airspace.point_info.get(n["waypoint"], {}).get("fra", ""))
            if n["waypoint"] in self.airspace.boundary_points and fra and "X" not in fra:
                return [f"{n['waypoint']} is an entry-only point: not a valid exit"]
            return []
        if n.get("type") != "altitude":
            return []
        ac = self.aircraft.get(n["callsign"])
        if ac is None:
            return []
        out = []
        fl = n["fl"]
        if not self.descending_to_destination(ac, fl) and not level_parity_ok(fl, ac.southbound):
            out.append(f"FL{fl} is a wrong-direction level for a {'south' if ac.southbound else 'north'}bound flight "
                       f"(semicircular rule: {'odd' if ac.southbound else 'even'} levels)")
        if fl < self.lower_fl:
            out.append(f"FL{fl} is below the sector (FL{self.lower_fl}): coordinate with the sector / unit below")
        seqfix = self.seq.get(ac.arr.get("fix")) if ac.destination else None
        if seqfix is not None:
            loa = seqfix.cross_at_fl or seqfix.cross_at_or_below_fl or seqfix.min_fl
            if loa and fl < loa:
                out.append(f"FL{fl} is below the LoA level FL{loa} at {seqfix.fix}: coordinate with the next sector "
                           f"(recorded as coordinated)")
        if in_rvsm_band(fl * 100.0) and ac.rvsm_status == "non_approved" and not ac.military:
            out.append(f"{ac.callsign} is not RVSM approved (equipment {ac.equipment}): FL{fl} is in RVSM airspace "
                       f"FL290-FL410")
        if in_rvsm_band(fl * 100.0) and ac.non_rvsm:
            out.append(f"{ac.callsign} is non-RVSM ({'formation' if ac.formation else ac.rvsm_status}): "
                       "2000 ft vertical separation applies in RVSM airspace")
        return out

    def _assume(self, ac: Aircraft, by: str) -> None:
        """The controller assumes the aircraft (accepts the transfer): from now on it is under our control."""
        ac.assumed = True
        ac.assumed_t = self.t
        wait = round(self.t - (ac.first_contact_t if ac.first_contact_t is not None else self.t), 1)
        if self.record:
            self.m.setdefault("assume_times_s", []).append(wait)
        for req in self.requests.values():
            if req.callsign == ac.callsign and req.kind == "checkin" and req.status == "pending":
                self._resolve_request(req, "acknowledged", by)
        self._event("assumed", callsign=ac.callsign, after_s=wait,
                    text=f"{ac.callsign} assumed, radar contact" + (f" ({wait:.0f} s after first call)" if wait > 0 else ""))

    def descending_to_destination(self, ac: Aircraft, fl: int) -> bool:
        """Arrival traffic going down: the semicircular rule does not apply to its descent levels."""
        if ac.destination:
            return True
        if fl < 250 and fl < ac.fl:
            return True                      # below FL250 / the LoA levels: a descent towards destination
        dest = str((ac.flight_plan or {}).get("destination", "")).upper()
        return dest in ((self.airspace.notes or {}).get("descent_destinations") or []) and fl < ac.fl

    def phrase(self, n: dict) -> str:
        cs, t = n["callsign"], n["type"]
        if t == "altitude":
            ac = self.aircraft.get(cs)
            verb = "climb" if ac and n["fl"] * 100 > ac.alt_ft else "descend"
            rate = f", {int(n['rate_fpm'])} ft/min or greater" if n.get("rate_fpm") else ""
            spd = f", {self.speed_phrase(n['speed'])}" if n.get("speed") else ""
            return f"{cs} {verb} FL{n['fl']}{rate}{spd}"
        if t == "heading":
            turn = f"turn {n['turn']} " if n.get("turn") else "fly "
            return f"{cs} {turn}heading {n['heading']:03d}"
        if t == "direct":
            return f"{cs} proceed direct {n['waypoint']}"
        if t == "speed":
            return f"{cs} {self.speed_phrase(n['speed'])}"
        if t == "resume":
            return f"{cs} resume own navigation"
        if t == "wx_plan":
            p = n["plan"]
            return (f"{cs} deviation approved, heading {int(p['heading']):03d} for {p['distance_nm']} miles, "
                    f"then direct {p['then_direct']}")
        if t == "deviation":
            lim = f" up to {int(n['max_nm'])} miles" if n.get("max_nm") else ""
            side = "left or right" if n["side"] == "either" else n["side"]
            then = f", when able direct {n['then_direct']}" if n.get("then_direct") else ""
            return f"{cs} deviation {side} of track approved{lim}{then}"
        return f"{cs} {t}"

    def _match_requests(self, norm: dict, instr_id: str) -> None:
        if norm["type"] == "wx_plan":
            return
        for req in self.requests.values():
            if req.status != "pending" or req.callsign != norm["callsign"]:
                continue
            if req.type == "report":
                if norm["type"] in ("altitude", "heading", "direct"):
                    self._resolve_request(req, "acknowledged", instr_id)
                continue
            if req.type == "weather_deviation":
                if norm["type"] in ("heading", "direct", "deviation", "altitude"):
                    self._resolve_request(req, "amended", instr_id)
                    ac = self.aircraft.get(req.callsign)
                    if ac is not None:
                        ac.wx["phase"] = "clear"
                continue
            if req.type != norm["type"]:
                continue
            if req.type == "altitude" and req.kind == "descent":
                ac = self.aircraft.get(req.callsign)
                if ac is None or norm.get("fl") is None or norm["fl"] * 100 >= ac.alt_ft - 50:
                    continue
                same = True                         # any descent clearance answers a request for descent
            elif req.type == "altitude":
                same = norm.get("fl") == req.value
            elif req.type == "direct":
                same = norm.get("waypoint") == req.value
            else:
                sp = norm.get("speed") or {}
                mode, val = speed_from_value(req.value)
                same = (sp.get("mach") == val) if mode == "mach" else (sp.get("ias_kt") == val)
            self._resolve_request(req, "approved" if same else "amended", instr_id)

    def add_dynamic_request(self, callsign: str, rtype: str, value, reason: str,
                            kind: Optional[str] = None) -> Optional[str]:
        """A request generated by the pilot model (weather deviation, ride, climb over weather, descent)."""
        if not self.record:
            return None
        req = Request(id=f"R{len(self.requests) + 1:03d}", callsign=callsign, type=rtype, value=value,
                      reason=reason, time=round(self.t, 1), origin="pilot", kind=kind)
        self.requests[req.id] = req
        self._event("pilot_request", request_id=req.id, callsign=callsign, request_type=rtype,
                    value=value, reason=reason, text=req.phrase(), origin="pilot")
        return req.id

    def cancel_request(self, req, why: str, status: str = "cancelled") -> None:
        if req is None or req.status != "pending":
            return
        self._resolve_request(req, status, "pilot")
        self._event("request_cancelled", request_id=req.id, callsign=req.callsign, reason=why)

    def has_pending_request(self, callsign: str) -> bool:
        return any(r.status == "pending" and r.callsign == callsign for r in self.requests.values())

    def _resolve_request(self, req: Request, status: str, by: str) -> None:
        req.status = status
        req.resolved_time = round(self.t, 1)
        req.resolved_by = by
        self._event("request_resolved", request_id=req.id, callsign=req.callsign, status=status,
                    response_time_s=round(self.t - req.time, 1))

    def _apply(self, ac: Aircraft, n: dict) -> None:
        t = n["type"]
        if t == "wx_plan":
            ac.start_wx_deviation(n["plan"])
            ac.wx.update(phase="deviating", plan=n["plan"])
            return
        if t == "deviation":
            plan = pilot.plan_deviation(self, ac, n["side"], n.get("max_nm") or 1e9, n.get("then_direct"))
            if plan is not None:
                ac.start_wx_deviation(plan)
                ac.wx.update(phase="deviating", plan=plan)
                self._event("pilot_readback", callsign=ac.callsign,
                            text=f"{ac.callsign} deviating {plan['side']}, heading {plan['heading']:03d} for "
                                 f"{plan['distance_nm']} miles, then direct {plan['then_direct']}")
            return
        if t in ("heading", "direct", "resume") and ac.nav_mode == "wxdev":
            ac.wx["phase"] = "clear"
        if t == "altitude":
            ac.cleared_alt_ft = n["fl"] * 100.0
            ac.vs_cmd_fpm = n.get("rate_fpm")
            sq = self.seq.get(ac.arr.get("fix")) if ac.destination else None
            if sq is not None:
                loa = sq.cross_at_fl or sq.cross_at_or_below_fl or sq.min_fl
                ac.arr["coordinated_fl"] = n["fl"] if loa and n["fl"] < loa else None
            if n.get("speed"):
                self._apply_speed(ac, n["speed"])
        elif t == "heading":
            ac.wx_plan = None
            ac.nav_mode = "heading"
            ac.target_hdg = float(n["heading"]) % 360.0
            ac.turn_dir = n.get("turn")
        elif t == "speed":
            self._apply_speed(ac, n["speed"])
        elif t == "resume":
            ac.nav_mode = "lnav"            # direct to the next waypoint of the route from present position
            ac.leg_from = ac.pos
            ac.turn_dir = None
        elif t == "direct":
            wp = n["waypoint"]
            remaining = ac.route[ac.next_index:]
            if wp in remaining:
                ac.next_index = ac.route.index(wp, ac.next_index)
            else:
                prefix = ac.route[:ac.next_index]
                ext = bool(self.airspace.point_info.get(wp, {}).get("external"))
                if wp in self.airspace.boundary_points:
                    ac.route = prefix + [wp]          # new exit point
                elif ext:
                    # a point in the next sector (e.g. after the exit point): fly straight to it, the transfer
                    # happens where the direct track crosses the boundary; the rest of the flight plan follows
                    if wp in ac.route_after:
                        ac.route_after = ac.route_after[ac.route_after.index(wp) + 1:]
                    ac.route = prefix + [wp]
                else:
                    ac.route = prefix + [wp, ac.route[-1]]
                ac.next_index = len(prefix)
            ac.leg_from = ac.pos            # FMS DIR-TO leg starts at present position
            ac.nav_mode = "lnav"
            ac.turn_dir = None

    # ------------------------------------------------------------------ stepping
    def step(self, dt: float = 1.0) -> None:
        if self.finished:
            return
        t_next = self.t + dt
        self.t = t_next          # events produced during this step are stamped with the new time
        # 1. airspace schedule
        for a in self.areas.values():
            for state in a.advance(t_next):
                self._event("area_activated" if state else "area_deactivated", area_id=a.id,
                            name=a.name, kind=a.kind)
        for ad in self.aerodromes.values():
            rwy = ad.advance(t_next)
            if rwy:
                self._event("runway_change", aerodrome=ad.id, runway=rwy,
                            text=f"{ad.id} runway in use now {rwy}")
        # 2. spawns
        for ac in self.aircraft.values():
            if ac.status == "scheduled" and ac.entry_time <= t_next:
                ac.spawn(self.env, t_next)
                self._event("aircraft_entered", callsign=ac.callsign, at=ac.route[0], aircraft_type=ac.icao,
                            fl=ac.fl, exit=ac.exit_wp)
        # 3. pilot requests (never in prediction)
        if self.record:
            due_req, self._deferred_req = self._deferred_req, []
            while self._req_idx < len(self.scheduled_requests) and self.scheduled_requests[self._req_idx]["time"] <= t_next:
                due_req.append(self.scheduled_requests[self._req_idx])
                self._req_idx += 1
            for r in due_req:
                ac = self.aircraft.get(r["callsign"])
                if ac is None or ac.status in ("exited", "left_sector"):
                    continue                                   # gone: the request is never made
                if not ac.active or not ac.assumed:
                    self._deferred_req.append(r)               # a pilot asks only once you have assumed him
                    continue
                rtype, value, kind = r["type"], r["value"], None
                if rtype == "weather_deviation":               # resolved against the present situation
                    value = pilot.scripted_plan(self, ac, value)
                elif rtype == "descent":                       # "request descent": the level is the controller's
                    rtype, kind = "altitude", "descent"
                    if value is None:
                        sq = self.seq.get(ac.arr.get("fix")) if ac.destination else None
                        value = int((sq.cross_at_fl or sq.min_fl) if sq else max(ARRIVAL_FLOOR_FL, min(250, int(ac.fl) - 40)))
                req = Request(id=f"R{len(self.requests) + 1:03d}", callsign=r["callsign"], type=rtype,
                              value=value, reason=r.get("reason", ""), time=round(t_next, 1), kind=kind)
                self.requests[req.id] = req
                if rtype == "weather_deviation":
                    ac.wx.update(phase="requested", request_id=req.id, requested_at=t_next, repeated=False,
                                 plan=value, scripted=True)
                self._event("pilot_request", request_id=req.id, callsign=req.callsign, request_type=req.type,
                            value=req.value, reason=req.reason, text=req.phrase(), kind=kind)
        # 3b. scripted aircraft events (RVSM capability), never in prediction
        if self.record:
            while self._ev_idx < len(self.scheduled_events) and self.scheduled_events[self._ev_idx]["time"] <= t_next:
                e = self.scheduled_events[self._ev_idx]
                self._ev_idx += 1
                ac = self.aircraft.get(e["callsign"])
                if ac is None or not ac.active:
                    continue
                self._aircraft_event(ac, e)
        # 4. instructions whose pilot delay elapsed
        due = [p for p in self.pending if p["execute_at_s"] <= t_next + 1e-9]
        if due:
            self.pending = [p for p in self.pending if p["execute_at_s"] > t_next + 1e-9]
            for rec in due:
                n = rec["instruction"]
                ac = self.aircraft.get(n["callsign"])
                if ac and ac.active:
                    self._apply(ac, n)
                    rec["status"] = "executed"
                    self._event("instruction_executed", instruction_id=rec["id"], callsign=n["callsign"])
                else:
                    rec["status"] = "dropped"
        # 5. dynamics (winds for all airborne aircraft evaluated in one vectorised call)
        act = [a for a in self.aircraft.values() if a.active]
        if act:
            u, v = self.env.weather.wind_at(np.array([a.x for a in act]), np.array([a.y for a in act]),
                                            np.array([a.alt_ft / 100.0 for a in act]), self.t)
            for a, uu, vv in zip(act, np.atleast_1d(u), np.atleast_1d(v)):
                a.wind_uv = (float(uu), float(vv))
        for ac in self.aircraft.values():
            res = ac.step(dt, self.env, self.t)
            if ac.wx_done:
                ac.wx_done = False
                ac.wx["phase"] = "clear"
                self._event("weather_deviation_complete", callsign=ac.callsign,
                            text=f"{ac.callsign} clear of weather, proceeding direct {ac.wx_plan['then_direct']}")
            if res in ("exited", "left_sector"):
                ac.ghost_until = self.t + self.persist_s
            if res == "exited":
                if self.record:
                    arrivals.on_fix_crossing(self, ac)
                    fl_x = int(round(ac.alt_ft / 100))
                    if ac.on_frequency and not ac.destination and abs(ac.vs_fpm) < 100 \
                            and not level_parity_ok(fl_x, ac.southbound):
                        self.m["wrong_direction_exits"].append({"callsign": ac.callsign, "fl": fl_x,
                                                                "direction": "southbound" if ac.southbound else "northbound"})
                        self._event("wrong_direction_level", callsign=ac.callsign, fl=fl_x,
                                    text=f"{ac.callsign} transferred at FL{fl_x}, a wrong-direction level")
                self._event("aircraft_exited", callsign=ac.callsign, at=ac.exit_point,
                            planned_exit=ac.planned_exit, fl=int(round(ac.alt_ft / 100)),
                            fuel_used_kg=round(ac.fuel_used, 1))
                self._expire_requests(ac.callsign)
            elif res == "left_sector":
                if self.record:
                    self.m["left_sector"].append(ac.callsign)
                self._event("aircraft_left_sector", callsign=ac.callsign, x_nm=round(ac.x, 1), y_nm=round(ac.y, 1),
                            note="left the sector away from any exit waypoint")
                self._expire_requests(ac.callsign)
        # 5b. transfer of communication from the previous sector
        for ac in self.aircraft.values():
            if ac.active and not ac.on_frequency:
                passed = ac.next_index >= 2 or ac.entered
                if passed or dist(ac.pos, self.waypoints[ac.entry_wp]) <= ac.checkin_nm:
                    ac.on_frequency = True
                    ac.first_contact_t = self.t
                    if self.t <= 1.0 or self.auto_assume:
                        # already ours when the scenario starts (or the session assumes automatically)
                        ac.assumed, ac.assumed_t = True, self.t
                    vert = ""
                    if ac.cleared_alt_ft < ac.alt_ft - 300:
                        vert = f" descending FL{int(ac.cleared_alt_ft // 100)}"
                    elif ac.cleared_alt_ft > ac.alt_ft + 300:
                        vert = f" climbing FL{int(ac.cleared_alt_ft // 100)}"
                    self._event("checkin", callsign=ac.callsign, entry=ac.entry_wp, fl=int(round(ac.alt_ft / 100)),
                                assumed=ac.assumed,
                                text=f"{ac.callsign} {ac.icao} FL{int(round(ac.alt_ft / 100))}{vert}, checking in "
                                     f"({ac.entry_wp} in {dist(ac.pos, self.waypoints[ac.entry_wp]):.0f} NM)")
                    if not ac.assumed and self.record:
                        # first contact: waits for the controller to assume the aircraft (assume, or reply to it)
                        req = Request(id=f"R{len(self.requests) + 1:03d}", callsign=ac.callsign, type="report",
                                      value=f"FL{int(round(ac.alt_ft / 100))}{vert}, checking in", reason="first contact",
                                      time=round(self.t, 1), origin="pilot", kind="checkin")
                        self.requests[req.id] = req
                    elif not ac.assumed:
                        ac.assumed = True                  # look-ahead predictions: nobody to answer
        # 6. pilots: onboard weather radar, deviation / ride requests (also flown in look-ahead predictions)
        for ac in self.aircraft.values():
            if ac.active:
                pilot.update(self, ac)
        # 7. monitoring
        if self.record:
            self._monitor(dt)
            self._monitor_weather(dt)
            if self.t - self._last_frame_t >= self.recording["every_s"] - 1e-6 or self.finished:
                self._record_frame()
            self._check_finished()

    def _record_frame(self) -> None:
        self._last_frame_t = self.t
        rv = {"approved": "A", "non_approved": "N", "unable": "U"}
        acs = []
        for a in self.aircraft.values():
            ghost = a.status in ("exited", "left_sector") and self.t <= a.ghost_until
            if not (a.active or ghost):
                continue
            if a.callsign not in self.recording["meta"]:
                self.recording["meta"][a.callsign] = {
                    "type": a.icao, "dep": a.flight_plan.get("departure"), "dest": a.flight_plan.get("destination"),
                    "route": [w for w in a.route if not w.startswith("@")], "fp_route": a.flight_plan.get("route"),
                    "formation": (a.formation or {}).get("size"), "military": a.military,
                    "arrival": bool(a.destination), "entry": a.entry_wp}
            mode, val = a.speed_target()
            spd = f"M{int(round(a.mach() * 100))}" if mode == "mach" else f"I{int(round(a.cas()))}"
            nav = {"lnav": "L", "heading": "H", "wxdev": "W"}.get(a.nav_mode, "L")
            acs.append([a.callsign, round(a.x, 2), round(a.y, 2), int(round(a.alt_ft / 100)),
                        int(round(a.cleared_alt_ft / 100)), int(round(a.vs_fpm, -1)), int(round(a.gs)),
                        int(round(a.trk)), spd, nav,
                        api_hdg(a.target_hdg) if nav != "L" and a.target_hdg is not None else None,
                        a.next_wp if nav == "L" and a.active else None, 1 if a.on_frequency else 0,
                        "A" if a.active else "T", rv.get(a.rvsm_status, "A")])
        self.recording["frames"].append({
            "t": round(self.t, 1), "ac": acs,
            "req": [[r.callsign, r.type, r.kind] for r in self.requests.values() if r.status == "pending"],
            "los": [list(k) for k in self._los],
            "areas": [a.id for a in self.areas.values() if a.active]})

    def recording_view(self, since: float = -1.0, every: float = 0.0) -> dict:
        """Frames after `since` (optionally thinned to one per `every` seconds), aircraft metadata, timeline events."""
        frames, last = [], -1e9
        for f in self.recording["frames"]:
            if f["t"] > since and (every <= 0 or f["t"] - last >= every - 1e-6):
                frames.append(f)
                last = f["t"]
        keep = ("separation_loss_start", "area_infringement_start", "pilot_request", "instruction",
                "instruction_rejected", "rvsm_status", "weather_penetration_start", "aircraft_left_sector",
                "area_activated", "area_deactivated", "runway_change", "fix_crossing", "wrong_direction_level",
                "rvsm_airspace_violation_start", "checkin")
        evs = [e for e in self.events if e["type"] in keep and e["time_s"] > since]
        return {"every_s": self.recording["every_s"], "sim_time_s": round(self.t, 1),
                "duration_s": self.duration_s, "frame_fields": ["callsign", "x_nm", "y_nm", "fl", "cleared_fl",
                                                                "vertical_speed_fpm", "gs_kt", "track_deg", "speed",
                                                                "nav", "assigned_heading", "next_waypoint",
                                                                "on_frequency", "status", "rvsm"],
                "meta": self.recording["meta"], "frames": frames, "events": evs}

    def _aircraft_event(self, ac: Aircraft, e: dict) -> None:
        if e["type"] == "rvsm_unable" and ac.rvsm_status != "unable":
            ac.rvsm_status, ac.rvsm_reason, ac.rvsm_since = "unable", e.get("reason", "equipment"), round(self.t, 1)
            self._event("rvsm_status", callsign=ac.callsign, status="unable", reason=ac.rvsm_reason,
                        fl=int(round(ac.alt_ft / 100)))
            req = Request(id=f"R{len(self.requests) + 1:03d}", callsign=ac.callsign, type="report",
                          value=ac.rvsm_reason, reason="RVSM capability lost", time=round(self.t, 1),
                          origin="pilot", kind="rvsm_unable")
            self.requests[req.id] = req
            self._event("pilot_request", request_id=req.id, callsign=ac.callsign, request_type="report",
                        value=req.value, reason=req.reason, text=req.phrase(), origin="pilot")
        elif e["type"] == "rvsm_restored" and ac.rvsm_status == "unable":
            ac.rvsm_status = "approved" if ac.rvsm_approved else "non_approved"
            ac.rvsm_reason, ac.rvsm_since = None, None
            self._event("rvsm_status", callsign=ac.callsign, status=ac.rvsm_status,
                        text=f"{ac.callsign} able RVSM again")

    def _expire_requests(self, cs: str) -> None:
        if not self.record:
            return
        for req in self.requests.values():
            if req.callsign == cs and req.status == "pending":
                self._resolve_request(req, "expired", "aircraft_left")

    def _monitor(self, dt: float) -> None:
        act = self.active_aircraft()
        current = set()
        for a, b in itertools.combinations(sorted(act, key=lambda x: x.callsign), 2):
            if not (a.on_frequency or b.on_frequency):
                continue            # both still with the previous sector: not our separation
            h = dist(a.pos, b.pos)
            v = abs(a.alt_ft - b.alt_ft)
            key = (a.callsign, b.callsign)
            hmin, vmin = pair_minima(a, b, h_nm=self.sep_h)
            if v < vmin:
                ms = self.m["min_separation"]
                if h < ms.get(key, 1e9):
                    ms[key] = h
            if h < hmin and v < vmin:
                current.add(key)
                self.m["los_seconds"] += dt
                if key not in self._los:
                    self._los[key] = {"start": self.t, "min_h": h, "min_v": v, "req_h": hmin, "req_v": vmin}
                    self.m["los_events"] += 1
                    self._event("separation_loss_start", aircraft=list(key), horizontal_nm=round(h, 2),
                                vertical_ft=round(v), required_horizontal_nm=hmin, required_vertical_ft=vmin)
                else:
                    self._los[key]["min_h"] = min(self._los[key]["min_h"], h)
                    self._los[key]["min_v"] = min(self._los[key]["min_v"], v)
        for key in list(self._los):
            if key not in current:
                info = self._los.pop(key)
                rec = {"aircraft": list(key), "start_s": info["start"], "end_s": self.t,
                       "duration_s": round(self.t - info["start"], 1), "min_horizontal_nm": round(info["min_h"], 2),
                       "min_vertical_ft": round(info["min_v"]), "required_horizontal_nm": info["req_h"],
                       "required_vertical_ft": info["req_v"]}
                self.m["los_pairs"].append(rec)
                self._event("separation_loss_end", **rec)
        # non-RVSM-approved civil aircraft inside RVSM airspace (state aircraft may enter, with 2000 ft)
        cur_rv = set()
        for ac in act:
            if ac.on_frequency and ac.rvsm_status == "non_approved" and not ac.military and in_rvsm_band(ac.alt_ft):
                cur_rv.add(ac.callsign)
                self.m["rvsm_violation_seconds"] += dt
                if ac.callsign not in self._rvsm_in:
                    self._rvsm_in[ac.callsign] = {"start": self.t, "max_fl": ac.alt_ft / 100}
                    self._event("rvsm_airspace_violation_start", callsign=ac.callsign, fl=int(round(ac.alt_ft / 100)),
                                text=f"{ac.callsign} (not RVSM approved) inside RVSM airspace")
                else:
                    self._rvsm_in[ac.callsign]["max_fl"] = max(self._rvsm_in[ac.callsign]["max_fl"], ac.alt_ft / 100)
        for cs in list(self._rvsm_in):
            if cs not in cur_rv:
                info = self._rvsm_in.pop(cs)
                rec = {"callsign": cs, "start_s": info["start"], "end_s": self.t,
                       "duration_s": round(self.t - info["start"], 1), "max_fl": int(round(info["max_fl"]))}
                self.m["rvsm_violations"].append(rec)
                self._event("rvsm_airspace_violation_end", **rec)
        # area infringements
        current_inf = set()
        for ac in act:
            if not ac.on_frequency:
                continue
            for ar in self.areas.values():
                if ar.active and ar.contains(ac.pos, ac.alt_ft):
                    key = (ac.callsign, ar.id)
                    current_inf.add(key)
                    self.m["area_seconds"][ar.kind] += dt
                    if key not in self._inf:
                        self._inf[key] = {"start": self.t}
                        self.m["area_infringements"] += 1
                        self._event("area_infringement_start", callsign=ac.callsign, area_id=ar.id, kind=ar.kind,
                                    fl=int(round(ac.alt_ft / 100)))
        for key in list(self._inf):
            if key not in current_inf:
                info = self._inf.pop(key)
                rec = {"callsign": key[0], "area_id": key[1], "kind": self.areas[key[1]].kind,
                       "start_s": info["start"], "end_s": self.t, "duration_s": round(self.t - info["start"], 1)}
                self.m["infringement_log"].append(rec)
                self._event("area_infringement_end", **rec)

    def _monitor_weather(self, dt: float) -> None:
        wx = self.env.weather
        act = [a for a in self.active_aircraft() if a.on_frequency]
        if not act or not wx.has_precip:
            return
        z = np.atleast_1d(wx.reflectivity(np.array([a.x for a in act]), np.array([a.y for a in act]),
                                          np.array([a.alt_ft / 100.0 for a in act]), self.t))
        for a, dbz in zip(act, z):
            for thr, key in ((30, "30dbz"), (40, "40dbz"), (50, "50dbz")):
                if dbz >= thr:
                    self.m["wx_seconds"][key] += dt
            cur = self._wx_in.get(a.callsign)
            if dbz >= 30:
                if cur is None:
                    self._wx_in[a.callsign] = {"start": self.t, "max": float(dbz)}
                    self._event("weather_penetration_start", callsign=a.callsign, dbz=round(float(dbz), 1),
                                fl=int(round(a.alt_ft / 100)))
                else:
                    cur["max"] = max(cur["max"], float(dbz))
            elif cur is not None:
                rec = {"callsign": a.callsign, "start_s": cur["start"], "end_s": self.t,
                       "duration_s": round(self.t - cur["start"], 1), "max_dbz": round(cur["max"], 1)}
                self.m["wx_penetrations"].append(rec)
                self._wx_in.pop(a.callsign)
                self._event("weather_penetration_end", **rec)

    def _check_finished(self) -> None:
        if self.t >= self.duration_s:
            self.finished = True
        elif (all(a.status in ("exited", "left_sector") for a in self.aircraft.values())
              and self._req_idx >= len(self.scheduled_requests)):
            self.finished = True
        if self.finished:
            # close open LoS / infringements
            for key, info in list(self._los.items()):
                self.m["los_pairs"].append({"aircraft": list(key), "start_s": info["start"], "end_s": self.t,
                                            "duration_s": round(self.t - info["start"], 1),
                                            "min_horizontal_nm": round(info["min_h"], 2),
                                            "min_vertical_ft": round(info["min_v"])})
            self._los.clear()
            for cs, info in list(self._rvsm_in.items()):
                self.m["rvsm_violations"].append({"callsign": cs, "start_s": info["start"], "end_s": self.t,
                                                  "duration_s": round(self.t - info["start"], 1),
                                                  "max_fl": int(round(info["max_fl"]))})
            self._rvsm_in.clear()
            self._event("scenario_finished")

    # ------------------------------------------------------------------ prediction
    def clone(self) -> "World":
        """Lightweight copy used for look-ahead. Pilot requests are NOT copied (the agent cannot see the future)."""
        w = copy.copy(self)
        w.aircraft = copy.deepcopy(self.aircraft)
        w.areas = copy.deepcopy(self.areas)
        w.pending = copy.deepcopy(self.pending)
        w.requests = copy.deepcopy(self.requests)
        w.scheduled_requests = []
        w._req_idx = 0
        w._deferred_req = []
        w.scheduled_events = []
        w._ev_idx = 0
        w.recording = {"every_s": self.recording["every_s"], "frames": [], "meta": {}}
        w._rvsm_in = {}
        w.events = []
        w.instructions = []
        w.record = False
        w.finished = False
        w.m = copy.deepcopy(self.m)          # probes must never touch the real metrics
        w._wx_in = {}
        w._los, w._inf = {}, {}
        return w

    def predict(self, horizon_s: float = 600.0, dt: float = PREDICT_DT, extra: Optional[List[dict]] = None,
                include_inbound: bool = True) -> dict:
        w = self.clone()
        if not include_inbound:
            for a in list(w.aircraft.values()):
                if a.status == "scheduled":
                    del w.aircraft[a.callsign]
        rejected = []
        for ins in extra or []:
            rec = w.issue(ins, source="probe")
            if rec["status"] == "rejected":
                rejected.append({"instruction": ins, "reason": rec["reason"]})
        t0 = w.t
        times: List[float] = []
        traj: Dict[str, List[Optional[Tuple[float, float, float]]]] = {}
        exits: Dict[str, dict] = {}
        n_steps = int(horizon_s / dt)
        for i in range(n_steps + 1):
            if i > 0:
                w.step(dt)
            times.append(w.t - t0)
            for cs, ac in w.aircraft.items():
                lst = traj.setdefault(cs, [None] * i)
                if ac.active:
                    lst.append((ac.x, ac.y, ac.alt_ft))
                else:
                    lst.append(None)
                    if ac.status in ("exited", "left_sector") and cs not in exits:
                        exits[cs] = {"status": ac.status, "at": ac.exit_point, "time_s": round((ac.end_time or w.t) - t0)}
        return {"t0": t0, "times": times, "traj": traj, "exits": exits, "world": w, "rejected": rejected}

    def analyze(self, pred: dict, lookahead_conflicts_only: bool = True) -> dict:
        times, traj, w = pred["times"], pred["traj"], pred["world"]
        css = sorted(traj)
        conflicts = []
        for a, b in itertools.combinations(css, 2):
            ta, tb = traj[a], traj[b]
            A, B = w.aircraft[a], w.aircraft[b]
            hmin = pair_minima(A, B, h_nm=w.sep_h)[0]
            nr, fm = A.non_rvsm or B.non_rvsm, bool(A.formation or B.formation)
            first_los = None
            min_h, t_min, v_at_min = 1e9, None, None
            min_h_vsep = 1e9
            req_v_at_los = None
            for i, t in enumerate(times):
                pa, pb = ta[i], tb[i]
                if pa is None or pb is None:
                    continue
                h = dist((pa[0], pa[1]), (pb[0], pb[1]))
                v = abs(pa[2] - pb[2])
                if h < min_h:
                    min_h, t_min, v_at_min = h, t, v
                vmin = vertical_minimum(pa[2], pb[2], nr, fm)
                if v < vmin:
                    min_h_vsep = min(min_h_vsep, h)
                    if h < hmin and first_los is None:
                        first_los, req_v_at_los = t, vmin
            if t_min is None:
                continue
            entry = {"aircraft": [a, b], "los_predicted": first_los is not None,
                     "time_to_los_s": first_los, "time_to_cpa_s": t_min,
                     "cpa_horizontal_nm": round(min_h, 2), "vertical_at_cpa_ft": round(v_at_min),
                     "required_horizontal_nm": hmin, "required_vertical_ft": req_v_at_los,
                     "min_horizontal_when_vertical_lt_1000ft_nm": None if min_h_vsep > 1e8 else round(min_h_vsep, 2)}
            if first_los is not None or not lookahead_conflicts_only:
                conflicts.append(entry)
        conflicts.sort(key=lambda c: (not c["los_predicted"], c["time_to_los_s"] or 1e9, c["cpa_horizontal_nm"]))
        # area infringements
        area_hits = []
        for cs in css:
            seen = set()
            for i, t in enumerate(times):
                p = traj[cs][i]
                if p is None:
                    continue
                for ar in w.areas.values():
                    if ar.id in seen:
                        continue
                    if ar.active_at(pred["t0"] + t) or ar.active:
                        if ar.active_at(pred["t0"] + t) and ar.contains((p[0], p[1]), p[2]):
                            seen.add(ar.id)
                            area_hits.append({"callsign": cs, "area_id": ar.id, "kind": ar.kind,
                                              "time_to_entry_s": t})
        area_hits.sort(key=lambda x: x["time_to_entry_s"])
        # weather: first time each aircraft would be inside >= 30 dBZ at its level (sampled every 3 steps)
        wxp = []
        wx = w.env.weather
        if wx.has_precip:
            seen = {}
            for i in range(0, len(times), 3):
                pts = [(cs, traj[cs][i]) for cs in css if traj[cs][i] is not None and cs not in seen]
                if not pts:
                    continue
                z = np.atleast_1d(wx.reflectivity(np.array([p[1][0] for p in pts]), np.array([p[1][1] for p in pts]),
                                                  np.array([p[1][2] / 100.0 for p in pts]), pred["t0"] + times[i]))
                for (cs, _), dbz in zip(pts, z):
                    if dbz >= 30:
                        seen[cs] = True
                        wxp.append({"callsign": cs, "time_to_entry_s": times[i], "dbz": round(float(dbz), 1)})
        return {"conflicts": conflicts, "area_infringements": area_hits, "exits": pred["exits"],
                "weather_penetrations": wxp}

    # ------------------------------------------------------------------ snapshots
    def current_pairs(self, max_h_nm: float = 40.0) -> List[dict]:
        out = []
        act = sorted(self.active_aircraft(), key=lambda x: x.callsign)
        for a, b in itertools.combinations(act, 2):
            h = dist(a.pos, b.pos)
            if h > max_h_nm:
                continue
            v = abs(a.alt_ft - b.alt_ft)
            hmin, vmin = pair_minima(a, b, h_nm=self.sep_h)
            out.append({"aircraft": [a.callsign, b.callsign], "horizontal_nm": round(h, 2), "vertical_ft": round(v),
                        "required_horizontal_nm": hmin, "required_vertical_ft": vmin,
                        "loss_of_separation": h < hmin and v < vmin})
        out.sort(key=lambda p: p["horizontal_nm"])
        return out

    def inbound(self, window_s: float = 900.0) -> List[dict]:
        out = []
        for a in self.aircraft.values():
            if a.status == "scheduled" and a.entry_time - self.t <= window_s:
                mode, val = a.speed_target(a.fl * 100.0)
                out.append({"callsign": a.callsign, "type": a.icao, "entry_waypoint": a.entry_wp,
                            "route": [w for w in a.route if not w.startswith("@")], "fl": a.fl, "speed_mode": mode,
                            "flight_plan": a.flight_plan, "destination": a.flight_plan.get("destination"),
                            "rvsm": a.rvsm_status, "category": "military" if a.military else "civil",
                            "formation": a.formation,
                            "speed": round(val, 2) if mode == "mach" else int(val),
                            "estimated_entry_s": round(a.entry_time, 1),
                            "time_to_entry_s": round(a.entry_time - self.t, 1)})
        out.sort(key=lambda x: x["estimated_entry_s"])
        return out

    def requests_list(self, status: Optional[str] = None) -> List[dict]:
        return [r.snapshot() for r in self.requests.values() if status in (None, r.status)]

    def metrics(self) -> dict:
        reqs = list(self.requests.values())
        answered = [r for r in reqs if r.status in ("approved", "unable", "amended")]
        exited = [a for a in self.aircraft.values() if a.status == "exited"]
        extra_nm = sum(max(0.0, a.distance_flown - a.planned_length_nm) for a in exited
                       if a.exit_point == a.planned_exit)
        return {
            "sim_time_s": round(self.t, 1),
            "separation": {
                "loss_events": self.m["los_events"],
                "loss_seconds": round(self.m["los_seconds"], 1),
                "losses": self.m["los_pairs"] + [
                    {"aircraft": list(k), "start_s": v["start"], "ongoing": True,
                     "min_horizontal_nm": round(v["min_h"], 2), "min_vertical_ft": round(v["min_v"])}
                    for k, v in self._los.items()],
                "closest_same_level_approach": sorted(
                    [{"aircraft": list(k), "min_horizontal_nm": round(v, 2)} for k, v in self.m["min_separation"].items()],
                    key=lambda x: x["min_horizontal_nm"])[:10],
            },
            "airspace": {
                "infringement_events": self.m["area_infringements"],
                "seconds_by_kind": {k: round(v, 1) for k, v in self.m["area_seconds"].items()},
                "log": self.m["infringement_log"],
            },
            "weather": {
                "seconds_in_30dbz": round(self.m["wx_seconds"]["30dbz"], 1),
                "seconds_in_40dbz": round(self.m["wx_seconds"]["40dbz"], 1),
                "seconds_in_50dbz": round(self.m["wx_seconds"]["50dbz"], 1),
                "penetrations": self.m["wx_penetrations"] + [
                    {"callsign": k, "start_s": v["start"], "ongoing": True, "max_dbz": round(v["max"], 1)}
                    for k, v in self._wx_in.items()],
                "deviations_without_clearance": self.m["wx_self_deviations"],
                "turbulence_seconds": {k: round(v, 1) for k, v in self.m["turb_seconds"].items()},
            },
            "sequencing": {
                "spacing_violations": self.m["seq_spacing_violations"],
                "level_violations": self.m["seq_level_violations"],
                "speed_violations": self.m.get("seq_speed_violations", 0),
                "crossings": self.m["sequencing"],
            },
            "rvsm": {
                "violations": self.m["rvsm_violations"] + [
                    {"callsign": k, "start_s": v["start"], "ongoing": True, "max_fl": int(round(v["max_fl"]))}
                    for k, v in self._rvsm_in.items()],
                "violation_seconds": round(self.m["rvsm_violation_seconds"], 1),
                "note": "a non-RVSM-approved civil aircraft inside FL290-FL410 while on your frequency",
            },
            "semicircular": {
                "wrong_direction_exits": self.m["wrong_direction_exits"],
                "wrong_direction_clearances": self.m["wrong_direction_clearances"],
            },
            "instructions": {"issued": self.m["instructions_issued"], "rejected": self.m["instructions_rejected"]},
            "assumption": (lambda ts, never: {
                "assumed": len(ts), "mean_delay_s": round(sum(ts) / len(ts), 1) if ts else None,
                "max_delay_s": max(ts) if ts else None, "late_over_30s": sum(1 for x in ts if x > 30),
                "never_assumed": never,
                "note": "time between the aircraft's first call and the controller assuming it"})(
                self.m.get("assume_times_s", []),
                [a.callsign for a in self.aircraft.values() if a.on_frequency and not a.assumed]),
            "requests": {
                "total": len(reqs),
                "by_status": {s: sum(1 for r in reqs if r.status == s)
                              for s in ("pending", "approved", "unable", "amended", "acknowledged", "expired",
                                        "cancelled")},
                "weather_deviation_requests": sum(1 for r in reqs if r.type == "weather_deviation"),
                "pilot_generated": sum(1 for r in reqs if r.origin == "pilot"),
                "mean_response_time_s": round(sum(r.resolved_time - r.time for r in answered) / len(answered), 1) if answered else None,
            },
            "traffic": {
                "total": len(self.aircraft),
                "exited": len(exited),
                "exited_at_planned_exit": sum(1 for a in exited if a.exit_point == a.planned_exit),
                "rerouted_exits": [{"callsign": a.callsign, "planned": a.planned_exit, "actual": a.exit_point}
                                   for a in exited if a.exit_point != a.planned_exit],
                "left_sector_off_route": self.m["left_sector"],
                "extra_track_miles": round(extra_nm, 2),
                "fuel_used_kg": round(sum(a.fuel_used for a in self.aircraft.values()), 1),
                "fuel_by_aircraft_kg": {a.callsign: round(a.fuel_used, 1) for a in self.aircraft.values()
                                        if a.status != "scheduled"},
            },
        }

    def stca(self, look_s: float = 180.0, step_s: float = 10.0) -> List[dict]:
        """Short-term conflict alert: pairs predicted to lose separation within `look_s`, by straight-line
        extrapolation of track, ground speed and vertical rate (towards the cleared level). Cheap enough to run
        every second, unlike the full look-ahead (`predict`)."""
        act = [a for a in self.active_aircraft()]
        out = []
        for a, b in itertools.combinations(act, 2):
            if not (a.on_frequency or b.on_frequency):
                continue
            hmin, vmin = pair_minima(a, b, h_nm=self.sep_h)
            reach = hmin + (a.gs + b.gs) * look_s / 3600.0
            if abs(a.x - b.x) > reach or abs(a.y - b.y) > reach:
                continue
            va = (a.gs * math.sin(math.radians(a.trk)) / 3600.0, a.gs * math.cos(math.radians(a.trk)) / 3600.0)
            vb = (b.gs * math.sin(math.radians(b.trk)) / 3600.0, b.gs * math.cos(math.radians(b.trk)) / 3600.0)

            def alt(ac, t):
                z = ac.alt_ft + ac.vs_fpm * t / 60.0
                lo, hi = sorted((ac.alt_ft, ac.cleared_alt_ft))
                return min(max(z, lo), hi) if abs(ac.vs_fpm) > 50 else ac.alt_ft
            t, hit, best = 0.0, None, (1e9, 0.0, 0.0)
            while t <= look_s + 1e-6:
                h = math.hypot(b.x - a.x + (vb[0] - va[0]) * t, b.y - a.y + (vb[1] - va[1]) * t)
                v = abs(alt(a, t) - alt(b, t))
                if h < best[0]:
                    best = (h, t, v)
                if h < hmin and v < vmin:
                    hit = t
                    break
                t += step_s
            if hit is not None:
                out.append({"aircraft": sorted([a.callsign, b.callsign]), "time_to_los_s": round(hit),
                            "cpa_horizontal_nm": round(best[0], 1), "vertical_at_cpa_ft": int(round(best[2], -2)),
                            "required_horizontal_nm": hmin, "required_vertical_ft": vmin})
        out.sort(key=lambda c: c["time_to_los_s"])
        return out

    ENTRY_FIELDS = ("cfl", "speed", "heading", "direct", "note")

    def enter(self, callsign: str, fields: dict, who: str = "atc") -> dict:
        """Controller's label input (connected mode): cleared level, speed, heading, direct, free note.
        A field set to "" or None clears it. Only checks that the value is well formed."""
        cs = callsign.upper()
        ac = self.aircraft.get(cs)
        if ac is None or not (ac.active or ac.status == "scheduled"):
            raise InstructionError(f"unknown callsign {cs}")
        e = dict(self.entries.get(cs, {}))
        for k, v in fields.items():
            if k not in self.ENTRY_FIELDS:
                continue
            if v is None or v == "":
                e.pop(k, None)
                continue
            if k == "cfl":
                v = int(v)
                if v % 10 or not (0 < v <= 660):
                    raise InstructionError("cleared level: a flight level, multiple of 10 (e.g. 350)")
            elif k == "heading":
                v = int(v)
                if not (1 <= v <= 360):
                    raise InstructionError("heading 1-360")
            elif k == "direct":
                v = str(v).upper()
                if v not in self.waypoints or v.startswith("@"):
                    raise InstructionError(f"unknown point {v}")
            elif k == "speed":
                v = str(v).upper().replace(" ", "")
                if not re.match(r"^(M0?\.?\d{2}|\d{3}(KT)?|S\d{3}|K\d{3}|SN|MIN|MAX)$", v):
                    raise InstructionError("speed: M78, 280 (kt), SN (resume normal)")
            else:
                v = str(v)[:40]
            e[k] = v
        e["t"] = round(self.t, 1)
        self.entries[cs] = e
        self._event("atc_entry", callsign=cs, entry={k: v for k, v in e.items() if k != "t"}, by=who)
        return e

    def state(self, trails: bool = False) -> dict:
        return {
            "sim_time_s": round(self.t, 1),
            "duration_s": self.duration_s,
            "finished": self.finished,
            "aircraft": [a.snapshot(trails) for a in self.aircraft.values() if a.active],
            "transferred": [a.snapshot(trails) for a in self.aircraft.values()
                            if a.status in ("exited", "left_sector") and self.t <= a.ghost_until],
            "inbound": self.inbound(),
            "pending_requests": self.requests_list("pending"),
            "pending_instructions": [{"id": p["id"], "text": self.phrase(p["instruction"]),
                                      "execute_at_s": p["execute_at_s"]} for p in self.pending],
            "areas": [a.snapshot(self.t) for a in self.areas.values()],
            "current_losses": [p for p in self.current_pairs() if p["loss_of_separation"]],
            "separation_minimum_nm": self.sep_h,
            "entries": self.entries,
            "stca": self.stca(),
            "aerodromes": [a.snapshot() for a in self.aerodromes.values()],
            "sequence": arrivals.sequence_status(self),
        }
