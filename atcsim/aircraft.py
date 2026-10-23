"""Aircraft model: point-mass total-energy dynamics with type-specific performance (OpenAP), ISA atmosphere,
wind, Mach/CAS speed management, bank-limited turns with roll dynamics, and FMS-style LNAV
(track-to-fix legs, cross-track correction, wind correction, fly-by turn anticipation).
"""
from __future__ import annotations

import math
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import atmosphere as atm
from .geometry import Point, api_hdg, bearing, dist, heading_diff
from .performance import BANK_NOMINAL_DEG, ROLL_RATE_DEG_S, TypePerformance
from .pilot import new_wx_state
from .weather import Weather, describe_uv

TRAIL_EVERY_S = 10.0
TRAIL_LEN = 12
MAX_INTERCEPT_DEG = 45.0
BANK_GAIN = 2.0                # commanded bank (deg) per degree of heading error, capped at nominal bank
SPEED_SHARE = 0.3              # share of excess power used for speed change while climbing/descending
SPEEDBRAKE_FACTOR = 1.5        # max descent rate with speedbrakes relative to idle descent
DECEL_DESCENT_SHARE = 0.3      # slowing down in a descent: share of the (idle) energy given to the descent
# a descent with no rate given (the pilot's own, managed descent): never steeper than idle, and not steeper than
# these NM flown per 1000 ft (still air) - about 100 levels in 43 NM from the cruise down to FL250, then about the
# idle path; "expedite" / an assigned rate / speedbrakes use the full idle rate (and more)
DESCENT_NM_PER_KFT = ((25000.0, 4.3), (10000.0, 3.3), (0.0, 3.0))


_K_ROLL = (180.0 / math.pi) ** 2


def _capture_bank(err_deg: float, tas_ms: float, dt: float, max_bank: float = BANK_NOMINAL_DEG) -> float:
    """The autopilot's bank for a heading error: up to the nominal bank, rolled out early enough to arrive on the
    heading with the wings level - no overshoot, no slow creep onto it. Rolling out from bank b at the roll rate p
    still turns the aircraft (180/pi)^2 (g/V) (-ln cos b) / p degrees; the bank is the one for which that (plus half
    a step at the present rate) is the error left."""
    rate_full = math.degrees(atm.G0 * math.tan(math.radians(max_bank)) / tas_ms)
    budget = max(0.0, err_deg - rate_full * dt * 0.5)
    x = budget * ROLL_RATE_DEG_S * tas_ms / (atm.G0 * _K_ROLL)
    c = math.exp(-x)
    return min(max_bank, math.degrees(math.acos(max(-1.0, min(1.0, c)))))


def intercept_deg(xte_nm: float, radius_nm: float) -> float:
    """Intercept angle (deg, + = turn right) back onto a track for a cross-track error (+ = right of it): no more than
    the angle from which a turn at that radius just rolls out on the line (acos(1 - |xte|/R), with a margin), and
    proportional near it (gain inversely to the radius) - a fast aircraft cuts in at a shallower angle and does not
    overshoot the line; capped at 45 degrees."""
    r = max(radius_nm, 0.5)
    e = abs(xte_nm)
    geo = math.degrees(math.acos(max(-1.0, 1.0 - e / (1.5 * r))))
    lin = 60.0 / r * e
    return -math.copysign(min(MAX_INTERCEPT_DEG, geo, lin), xte_nm)


def rate_one_bank(tas_ms: float) -> float:
    """The bank (deg) of a rate one turn (3 deg/s) at that true airspeed."""
    return math.degrees(math.atan(math.radians(3.0) * tas_ms / atm.G0))


def hold_entry(trk: float, inbound: float, turn: str) -> str:
    """ICAO holding entry for an aircraft arriving over the fix on track trk: 'direct', 'teardrop' (offset) or
    'parallel' - the sectors of 180, 70 and 110 degrees split by the line at 70 degrees to the inbound track."""
    r = heading_diff(inbound, trk)                   # + = arriving turned right of the inbound track
    if turn == "L":
        r = -r
    if -70.0 <= r <= 110.0:
        return "direct"
    return "teardrop" if r > 110.0 else "parallel"


def hold_leg_s(alt_ft: float) -> float:
    """ICAO outbound timing: 1 minute at or below 14000 ft, 1.5 minutes above."""
    return 60.0 if alt_ft <= 14000.0 else 90.0


def _is_lap(name: str) -> bool:
    """A point of a lap of a published hold set up by approach ("@CS~H<lap>.<j>")."""
    return "~H" in name and not name.endswith("~HF")


def descent_profile_fpm(tas_kt: float, alt_ft: float) -> float:
    """The rate (ft/min) of the pilot's own descent at that true airspeed and level (before the idle limit)."""
    nm = next(v for a, v in DESCENT_NM_PER_KFT if alt_ft >= a)
    return max(tas_kt, 120.0) / 60.0 / nm * 1000.0
EXIT_CAPTURE_NM = 3.0


@lru_cache(maxsize=4096)
def _conversion_ft(mach: float, cas: float, dT: float) -> float:
    return atm.crossover_altitude(cas * atm.KT, mach, dT) / atm.FT


def wind_correction(tas: float, course: float, u: float, v: float) -> float:
    """Wind correction angle (deg) so that heading = course - wca gives ground track = course."""
    c = math.radians(course)
    cross = u * math.cos(c) - v * math.sin(c)      # wind component to the right of the course
    r = max(-0.99, min(0.99, cross / max(tas, 50.0)))
    return math.degrees(math.asin(r))


@dataclass
class Env:
    waypoints: Dict[str, Point]
    weather: Weather
    dT: float = 0.0
    airspace: object = None            # airspace.Airspace (controlled area, transfer points)


def level_parity_ok(fl: int, southbound: bool) -> bool:
    """Semicircular rule: southbound odd levels (..., 270, 290, 310, ..., 410, 450, 490),
    northbound even levels (..., 280, 300, ..., 400, 430, 470). Above FL410 levels are 2000 ft apart."""
    fl = int(round(fl))
    if fl % 10:
        return False
    if fl <= 410:
        odd = (fl // 10) % 2 == 1
    elif (fl - 410) % 40 == 0:        # 450, 490
        odd = True
    elif (fl - 430) % 40 == 0:        # 430, 470
        odd = False
    else:
        return False
    return odd == southbound


def correct_levels(southbound: bool, lo: int = 200, hi: int = 460) -> List[int]:
    return [f for f in range(lo, hi + 1, 10) if level_parity_ok(f, southbound)]


@dataclass
class Aircraft:
    callsign: str
    perf: TypePerformance
    route: List[str]
    entry_time: float
    fl: int
    mass: float
    start_along_nm: float = 0.0
    filed_mach: Optional[float] = None                 # filed cruise Mach (None = type's typical cruise Mach)

    status: str = "scheduled"          # scheduled | active | exited | left_sector
    # flight category, RVSM and formation (equipment = ICAO FPL item 10a/10b; 'W' = RVSM approved)
    military: bool = False
    formation: Optional[dict] = None   # {"size": 4, "types": "4xF16", "extent_nm": 1.0}: only the leader is on radar
    rvsm_approved: bool = True
    rvsm_status: str = "approved"      # approved | non_approved | unable (lost RVSM capability in flight)
    rvsm_reason: Optional[str] = None
    rvsm_since: Optional[float] = None
    equipment: str = ""
    southbound: bool = True            # direction of flight for the semicircular rule (entry -> exit bearing)
    ghost_until: float = -1.0
    route_after: List[str] = field(default_factory=list)   # flight plan after the exit (next sectors)
    route_before: List[str] = field(default_factory=list)  # flight plan before the entry
    flight_plan: dict = field(default_factory=dict)          # keeps being drawn (dead reckoning) after leaving, until this time
    prev_unit: str = ""                # unit (or sector) handing the aircraft over to us
    next_unit: str = ""                # unit (or sector) we hand it over to
    mag_var: float = 0.0               # magnetic variation (deg, east +) for the Mode S magnetic heading
    entry_wp: str = ""                 # boundary waypoint where it enters the sector
    boundary_time: float = 0.0         # planned time at the entry waypoint
    checkin_nm: float = 10.0           # calls on frequency this far before the entry waypoint
    on_frequency: bool = False         # has called us (before: previous sector)
    assumed: bool = False              # the controller has assumed it (accepted the transfer): under our control
    released: bool = False             # the controller has decontrolled it (by hand): no longer ours on the screen
    gone: bool = False                 # out of the picture (far away): no longer simulated
    rfl: Optional[int] = None          # requested final level (a departure climbing to it asks for it on first contact)
    first_contact_t: Optional[float] = None
    assumed_t: Optional[float] = None
    entered: bool = False              # has been inside the sector square
    destination: Optional[str] = None  # aerodrome id for arrivals (via a sequencing fix)
    arr: dict = field(default_factory=dict)   # arrival / top-of-descent state
    speedbrake: bool = False
    emergency: Optional[dict] = None   # live action: {code 7700 / 7600, kind descent | medical | radio | general}
    x: float = 0.0
    y: float = 0.0
    alt_ft: float = 0.0
    hdg: float = 0.0                   # true heading
    bank: float = 0.0                  # deg, + = right
    tas: float = 0.0                   # kt
    gs: float = 0.0
    trk: float = 0.0
    vs_fpm: float = 0.0
    wind_uv: Tuple[float, float] = (0.0, 0.0)
    cleared_alt_ft: float = 0.0
    vs_cmd_fpm: Optional[float] = None
    vs_cmd_mode: str = "min"           # assigned rate: "min" (at X ft/min or greater) | "max" (at X or less)
    # limits of the clearances in force (a heading or a speed is given with its limit): {"heading": {...}, "speed":
    # {...}}, each with until_t (s) or nm_left (NM flown) or until_wp (a point passed), and for a heading "then" (point)
    clr_limits: dict = field(default_factory=dict)
    # ATC speed control. The aircraft always flies a Mach/IAS pair and converts from Mach to IAS at the altitude
    # where the two are equal (conversion / crossover altitude). Assignments replace one or both members of the pair:
    spd_mach: Optional[float] = None   # assigned Mach (above the conversion altitude)
    spd_cas: Optional[float] = None    # assigned IAS: "on conversion X knots", or IAS now if spd_ias_now
    spd_ias_now: bool = False          # "maintain X knots": fly the IAS at every level, starting now
    nav_mode: str = "lnav"             # lnav | heading | wxdev (weather deviation: heading for N miles, then direct)
    wx: dict = field(default_factory=new_wx_state)
    wx_plan: Optional[dict] = None
    wx_flown: float = 0.0
    wx_done: bool = False
    target_hdg: Optional[float] = None
    turn_dir: Optional[str] = None
    leg_from: Point = (0.0, 0.0)
    next_index: int = 1
    xte_nm: float = 0.0
    thrust_n: float = 0.0
    fuel_used: float = 0.0
    planned_exit: str = ""
    planned_length_nm: float = 0.0
    distance_flown: float = 0.0
    spawn_time: Optional[float] = None
    end_time: Optional[float] = None
    exit_point: Optional[str] = None
    trail: List[Tuple[float, float]] = field(default_factory=list)
    _trail_clock: float = 0.0

    # ---------------------------------------------------------------- properties
    @property
    def icao(self) -> str:
        return self.perf.icao

    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def outside(self) -> bool:
        """Left the sector but still flying (next sectors) and still on the picture."""
        return self.status in ("exited", "left_sector") and not self.gone

    @property
    def next_wp(self) -> Optional[str]:
        return self.route[self.next_index] if self.next_index < len(self.route) else None

    @property
    def exit_wp(self) -> str:
        return self.route[-1]

    @property
    def non_rvsm(self) -> bool:
        """Needs 2000 ft from every other aircraft in RVSM airspace (not approved, unable, or a formation)."""
        return self.rvsm_status != "approved" or self.formation is not None

    @property
    def speed_assigned(self) -> bool:
        return self.spd_mach is not None or self.spd_cas is not None

    def speed_pair(self) -> Tuple[float, float]:
        """(Mach, IAS) currently in force: ATC assignments first, else the company schedule."""
        m = self.spd_mach if self.spd_mach is not None else (self.filed_mach or self.perf.cruise_mach)
        c = self.spd_cas if self.spd_cas is not None else self.perf.cas_schedule
        return m, c

    def conversion_ft(self, mach: Optional[float] = None, cas: Optional[float] = None) -> float:
        """Altitude where the Mach and the IAS of the pair give the same TAS (Mach above, IAS below)."""
        m0, c0 = self.speed_pair()
        m, c = mach or m0, cas or c0
        return _conversion_ft(round(m, 3), round(c, 1), round(self.perf.dT, 1))

    def speed_target(self, alt_ft: Optional[float] = None) -> Tuple[str, float]:
        """Speed mode and value at an altitude, honouring every ATC speed assignment in force."""
        a = self.alt_ft if alt_ft is None else alt_ft
        m, c = self.speed_pair()
        if self.spd_ias_now and self.spd_cas is not None:
            return "cas", self.spd_cas
        if a >= self.conversion_ft(m, c):
            return "mach", m
        if a < 10000.0 and self.spd_cas is None:
            return "cas", min(250.0, c)
        return "cas", c

    def mach(self) -> float:
        return atm.tas_to_mach(self.tas * atm.KT, self.alt_ft * atm.FT, self.perf.dT)

    def cas(self) -> float:
        return atm.tas_to_cas(self.tas * atm.KT, self.alt_ft * atm.FT, self.perf.dT) / atm.KT

    # ---------------------------------------------------------------- lifecycle
    def spawn(self, env: Env, t: float) -> None:
        from .geometry import point_along_polyline
        pts = [env.waypoints[w] for w in self.route]
        (px, py), nxt = point_along_polyline(pts, self.start_along_nm)
        self.x, self.y = px, py
        self.next_index = max(1, nxt)
        self.leg_from = pts[self.next_index - 1]
        self.alt_ft = self.cleared_alt_ft = self.fl * 100.0
        if getattr(self, "initial_cfl", None):          # a departure released climbing (an arrival descending)
            self.cleared_alt_ft = float(self.initial_cfl) * 100.0
        sid = getattr(self, "sid", None)
        if sid and getattr(self, "spawn_alt_ft", None) is not None:
            # just airborne off the runway, flying its SID (approach climbs it to the level it is released at)
            self.alt_ft = float(self.spawn_alt_ft)
            self.spd_cas, self.spd_ias_now = float(sid["max_kt"]), True
        self.tas = self.perf.tas_for(*self.speed_target(), self.alt_ft)
        if sid and getattr(self, "spawn_alt_ft", None) is not None:
            self.tas = self.perf.tas_for("cas", 165.0, self.alt_ft)
        course = bearing(self.leg_from, pts[self.next_index])
        u, v = env.weather.at(self.x, self.y, self.alt_ft / 100, t)
        self.hdg = (course - self._wca(course, u, v)) % 360
        self.wind_uv = (u, v)
        self._update_ground(u, v)
        self.status = "active"
        self.spawn_time = t
        self.trail = []

    # ---------------------------------------------------------------- helpers
    def _wca(self, course: float, u: float, v: float) -> float:
        return wind_correction(self.tas, course, u, v)

    def start_wx_deviation(self, plan: dict) -> None:
        """Fly the deviation heading for plan['distance_nm'], then proceed direct plan['then_direct']."""
        self.nav_mode = "wxdev"
        self.wx_plan = dict(plan)
        self.target_hdg = float(plan["heading"]) % 360.0
        self.turn_dir = None
        self.wx_flown = 0.0

    def _finish_wx_deviation(self) -> None:
        wp = self.wx_plan["then_direct"]
        if wp in self.route[self.next_index:]:
            self.next_index = self.route.index(wp, self.next_index)
        elif wp in self.route:
            self.next_index = self.route.index(wp)
        else:
            self.route = self.route[:self.next_index] + [wp] + self.route[self.next_index:]
        self.leg_from = self.pos
        self._dct = True
        self.nav_mode = "lnav"
        self.wx_done = True

    def _update_ground(self, u: float, v: float) -> None:
        h = math.radians(self.hdg)
        gx, gy = self.tas * math.sin(h) + u, self.tas * math.cos(h) + v
        self.gs = math.hypot(gx, gy)
        self.trk = math.degrees(math.atan2(gx, gy)) % 360

    def turn_radius_nm(self) -> float:
        v = max(self.gs, 100.0) * atm.KT
        return v * v / (atm.G0 * math.tan(math.radians(BANK_NOMINAL_DEG))) / atm.NM

    # ---------------------------------------------------------------- dynamics
    def step(self, dt: float, env: Env, t_now: float) -> Optional[str]:
        if not self.active and not self.outside:
            return None
        outside = self.outside        # gone to the next sectors: keeps flying (onward route, pilot's instructions)
        u, v = self.wind_uv            # evaluated for all aircraft at once by the world (vectorised)

        # ---- lateral guidance
        max_bank = BANK_NOMINAL_DEG
        if self.nav_mode != "lnav":
            self.hold_state = None                    # vectored out of the hold
        hold_cmd = self._hold_guidance(dt, env, u, v) if getattr(self, "hold_state", None) else None
        if hold_cmd is not None:
            hdg_cmd, forced = hold_cmd
            # holding: rate one or 25 degrees of bank, whichever is less
            max_bank = min(BANK_NOMINAL_DEG, rate_one_bank(max(self.tas, 100.0) * atm.KT))
        elif self.nav_mode == "lnav":
            res = self._lnav(env, u, v, outside=outside)
            if res:
                return res
            hdg_cmd, forced = self._lnav_heading_cmd, None
            if getattr(self, "hold_state", None):     # just reached its holding fix
                hdg_cmd, forced = self._hold_guidance(dt, env, u, v) or (hdg_cmd, None)
        else:                           # heading or wxdev
            hdg_cmd = self.target_hdg if self.target_hdg is not None else self.hdg
            forced = self.turn_dir
        if forced == "left":
            err = -((self.hdg - hdg_cmd) % 360.0)
        elif forced == "right":
            err = (hdg_cmd - self.hdg) % 360.0
        else:
            err = heading_diff(self.hdg, hdg_cmd)
        if forced and abs(err) < 1.0:
            self.turn_dir = None
        tas_ms = max(self.tas, 100.0) * atm.KT
        bank_cmd = math.copysign(_capture_bank(abs(err), tas_ms, dt, max_bank), err)
        roll = ROLL_RATE_DEG_S * dt
        self.bank += max(-roll, min(roll, bank_cmd - self.bank))
        rate = math.degrees(atm.G0 * math.tan(math.radians(self.bank)) / tas_ms)
        dpsi = rate * dt
        if abs(err) < 0.3 and abs(self.bank) < roll:      # on the heading, wings level
            dpsi, self.bank = err, 0.0
        self.hdg = (self.hdg + dpsi) % 360.0

        # ---- speed & vertical: total-energy model
        self._energy(dt)

        # ---- kinematics
        self._update_ground(u, v)
        r = math.radians(self.trk)
        step_nm = self.gs * dt / 3600.0
        self.x += step_nm * math.sin(r)
        self.y += step_nm * math.cos(r)
        if self.entered and not outside:
            self.distance_flown += step_nm            # track miles inside the sector
        if self.nav_mode == "wxdev":
            self.wx_flown += step_nm
            if self.wx_flown >= self.wx_plan["distance_nm"]:
                self._finish_wx_deviation()

        self._trail(dt)
        if outside:
            return None

        if self.arr and self.entered and self.nav_mode != "lnav":
            # an arrival on vectors / a direct handed over at its fix inside the sector (to the approach unit below)
            fx = self.arr.get("fix")
            if fx in env.waypoints and fx not in getattr(env.airspace, "boundary_points", {}):
                d = dist(self.pos, env.waypoints[fx])
                prev = getattr(self, "_fix_d", None)
                self._fix_d = d
                if d <= 2.5 or (d <= 6.0 and prev is not None and d > prev):
                    self.status, self.exit_point = "exited", fx
                    self.end_time = None
                    return "exited"
        sid = getattr(self, "sid", None)
        if sid and not sid.get("released"):
            return None                               # on its SID with approach, below and before our airspace
        if env.airspace.inside(self.pos):
            self.entered = True
        elif self.entered:
            near, d = env.airspace.nearest_transfer_point(self.pos)
            self.end_time = t_now
            if near is not None and d <= EXIT_CAPTURE_NM:
                self.status, self.exit_point = "exited", near
                return "exited"
            tgt = self.route[-1] if self.route else None
            if (self.nav_mode == "lnav" and tgt is not None
                    and env.airspace.point_info.get(tgt, {}).get("external")):
                # cleared direct to a point of the next sector: transferred where the direct crosses the boundary
                self.status, self.exit_point = "exited", f"DCT {tgt}"
                return "exited"
            self.status = "left_sector"
            return "left_sector"
        return None

    def _trail(self, dt: float) -> None:
        self._trail_clock += dt
        if self._trail_clock >= TRAIL_EVERY_S:
            self._trail_clock = 0.0
            self.trail.append((round(self.x, 2), round(self.y, 2)))
            if len(self.trail) > TRAIL_LEN:
                self.trail.pop(0)

    def _lnav(self, env: Env, u: float, v: float, outside: bool = False) -> Optional[str]:
        wps = env.waypoints
        if getattr(self, "_dct", False) and self.next_wp in wps:
            # direct to a point: the FMS flies the turn, then the line from where the turn ends (no S back onto a
            # line drawn from where the clearance was given)
            brg = bearing(self.pos, wps[self.next_wp])
            if abs(heading_diff(self.trk, brg)) > 2.0 and dist(self.pos, wps[self.next_wp]) > 1.0:
                self.leg_from = self.pos
            else:
                self._dct = False
        while True:
            to_name = self.next_wp
            B = wps[to_name]
            A = self.leg_from
            if dist(A, B) < 0.1:
                A = self.pos
            course = bearing(A, B)
            c = math.radians(course)
            dx, dy = self.x - A[0], self.y - A[1]
            along_from_A = dx * math.sin(c) + dy * math.cos(c)
            self.xte_nm = dx * math.cos(c) - dy * math.sin(c)     # + = right of track
            to_go = dist(A, B) - along_from_A
            final = self.next_index >= len(self.route) - 1
            if final:
                if outside:
                    if to_go <= 0.0:                # end of the known route: carry on, same heading
                        self.nav_mode, self.target_hdg, self.turn_dir = "heading", self.hdg, None
                        self._lnav_heading_cmd = self.hdg
                        return None
                    break
                if to_go <= 0.0 and abs(self.xte_nm) <= EXIT_CAPTURE_NM:
                    self.status, self.exit_point = "exited", to_name
                    self.end_time = None
                    return "exited"
                break
            hs = getattr(self, "hold_spec", None)
            if hs and to_name == hs["fix_name"] and _is_lap(self.route[self.next_index + 1]):
                if to_go <= 0.05:                  # over the holding fix (fly-over): into the hold
                    self._enter_hold(hs)
                break
            nxt = wps[self.route[self.next_index + 1]]
            dchi = abs(heading_diff(course, bearing(B, nxt)))
            anticipation = self.turn_radius_nm() * math.tan(math.radians(min(dchi, 120.0)) / 2.0)
            if to_go <= max(anticipation, 0.2):
                self.leg_from = B          # fly-by: sequence the next leg before the waypoint
                self.next_index += 1
                continue
            break
        intercept = intercept_deg(self.xte_nm, self.turn_radius_nm())
        desired_track = (course + intercept) % 360.0
        self._lnav_heading_cmd = (desired_track - self._wca(desired_track, u, v)) % 360.0
        return None

    # ---------------------------------------------------------------- holding
    def _enter_hold(self, hs: dict) -> None:
        """Over the holding fix: the entry for the track it arrives on, then the racetrack. The lap points stay in the
        route (for the times and the picture); the next one is the first of the lap being flown."""
        self.next_index += 1
        entry = hold_entry(self.trk, hs["inbound"], hs["turn"])
        t_out = float(hs["leg_min"]) * 60.0 if hs.get("leg_min") else hold_leg_s(self.alt_ft)
        self.hold_state = {"phase": {"direct": "turn_out", "teardrop": "teardrop", "parallel": "parallel"}[entry],
                           "entry": entry, "t": 0.0, "t_out": t_out, "t_out0": t_out, "timer": None,
                           "t_in0": None, "a_prev": 0.0}

    def _hold_frame(self, hs: dict) -> Tuple[float, float]:
        """Position in the hold: along the inbound track from the fix (+ = past it) and to the holding side."""
        th = math.radians(hs["inbound"])
        ux, uy = math.sin(th), math.cos(th)
        nx, ny = (uy, -ux) if hs["turn"] == "R" else (-uy, ux)
        dx, dy = self.x - hs["x"], self.y - hs["y"]
        return dx * ux + dy * uy, dx * nx + dy * ny

    def _exit_hold(self, hs: dict) -> None:
        self.hold_state = None
        self.leg_from = (hs["x"], hs["y"])
        self._dct = False

    def _hold_guidance(self, dt: float, env: Env, u: float, v: float):
        """Heading command and forced turn direction in the hold (None: not holding any more, lnav flies on).
        Entry (direct, teardrop 30 degrees off the outbound track, parallel), the fix flown over, turns in the holding
        direction (rate one or 25 degrees of bank), the outbound track flown the pattern's width from the inbound one
        (wind corrected), timed from abeam the fix or flown to its published length, its time adjusted so the inbound
        leg (from wings level) comes out at the standard time, the inbound track intercepted and flown to the fix."""
        hs, st = getattr(self, "hold_spec", None), self.hold_state
        if not hs or self.next_index >= len(self.route) or not _is_lap(self.route[self.next_index]):
            if hs:
                self._exit_hold(hs)                   # re-cleared by approach (another route): lnav takes it
            else:
                self.hold_state = None
            return None
        st["t"] += dt
        inb, out = hs["inbound"], (hs["inbound"] + 180.0) % 360.0
        turn = "right" if hs["turn"] == "R" else "left"
        other = "left" if turn == "right" else "right"
        a, s_ = self._hold_frame(hs)
        tas_ms = max(self.tas, 100.0) * atm.KT
        b = min(BANK_NOMINAL_DEG, rate_one_bank(tas_ms))
        v_ms = max(self.gs, 100.0) * atm.KT
        radius = v_ms * v_ms / (atm.G0 * math.tan(math.radians(b))) / atm.NM
        r_air = tas_ms * tas_ms / (atm.G0 * math.tan(math.radians(b))) / atm.NM
        side = 1.0 if hs["turn"] == "R" else -1.0
        # the pattern's width: the turn's diameter, less what the wind drifts it towards the holding side during
        # the turn back (half a circle), so that turn ends on the inbound track
        th = math.radians(hs["inbound"])
        w_side = (u * math.cos(th) - v * math.sin(th)) * side            # kt, + = blowing to the holding side
        t_turn = math.pi * r_air * atm.NM / tas_ms
        width = max(0.5 * r_air, 2.0 * r_air - w_side * t_turn / 3600.0)
        crossed = st["a_prev"] < 0.0 <= a and abs(s_) < 3.0
        st["a_prev"] = a

        def heading_for(track):
            return (track - self._wca(track, u, v)) % 360.0

        def turning(cmd, direction):
            return cmd, (direction if abs(heading_diff(self.hdg, cmd)) > 20.0 else None)

        def inbound_cmd():
            # the inbound track to the fix: intercept angle from the offset to the side, the wind corrected
            # s_ > 0 is towards the holding side (right of the inbound track in a right hold): steer back to the line
            icpt = intercept_deg(s_ * side, radius)
            return heading_for((inb + icpt) % 360.0)

        ph = st["phase"]
        if ph in ("inbound", "to_fix") and crossed:
            # over the fix: a lap done (the entry counts as the first one when it ends on the inbound leg)
            if ph == "inbound" and st.get("t_in0") is not None and not hs.get("leg_nm"):
                t_in = st["t"] - st["t_in0"]               # the inbound leg's time: the next outbound adjusted
                if t_in > 5.0:
                    st["t_out"] = max(0.3 * st["t_out0"], min(2.5 * st["t_out0"], st["t_out"] + (st["t_out0"] - t_in)))
            j = next((i for i in range(self.next_index, len(self.route))
                      if _is_lap(self.route[i]) and self.route[i].endswith(".5")), None)
            self.next_index = (j + 1) if j is not None else self.next_index + 1
            if self.next_index >= len(self.route) or not _is_lap(self.route[self.next_index]):
                self._exit_hold(hs)                   # last lap: on to the approach from the fix
                return None
            st.update(phase="turn_out", timer=None, t_in0=None)
            ph = "turn_out"
        if ph == "teardrop":
            # offset entry: 30 degrees off the outbound track to the holding side, for the outbound time
            off = -30.0 if hs["turn"] == "R" else 30.0
            if st["timer"] is None:
                st["timer"] = st["t"]
            if st["t"] - st["timer"] >= st["t_out"]:
                st.update(phase="turn_in")
                return turning(inbound_cmd(), turn)
            return heading_for((out + off) % 360.0), None
        if ph == "parallel":
            # parallel entry: the outbound track on the non-holding side, then back to the fix turning to the holding side
            if st["timer"] is None:
                st["timer"] = st["t"]
            if st["t"] - st["timer"] >= st["t_out"]:
                st.update(phase="to_fix", forced=other)
                ph = "to_fix"
            else:
                return heading_for(out), None
        if ph == "to_fix":
            cmd = heading_for(bearing(self.pos, (hs["x"], hs["y"])))
            if st.get("forced") and abs(heading_diff(self.hdg, cmd)) <= 20.0:
                st["forced"] = None
            return cmd, st.get("forced")
        def outbound_cmd():
            # the outbound track, the pattern's width from the inbound one (as the FMS flies it): the turn back then
            # ends on the inbound track whatever the entry and the wind
            icpt = intercept_deg((s_ - width) * -side, radius)
            return heading_for((out + icpt) % 360.0)

        if ph == "turn_out":
            cmd = outbound_cmd()
            if abs(heading_diff(self.hdg, cmd)) < 20.0:
                st.update(phase="outbound", timer=None)
                ph = "outbound"
            else:
                return cmd, turn
        if ph == "outbound":
            if st["timer"] is None and a <= 0.0:
                st["timer"] = st["t"]                 # abeam the fix (wings level by now): timing starts
            if hs.get("leg_nm"):
                done = a <= -float(hs["leg_nm"])
            else:
                done = st["timer"] is not None and st["t"] - st["timer"] >= st["t_out"]
            if done:
                st.update(phase="turn_in")
                ph = "turn_in"
            else:
                return outbound_cmd(), None
        if ph == "turn_in":
            cmd = inbound_cmd()
            if abs(heading_diff(self.hdg, cmd)) <= 20.0:
                st.update(phase="inbound", t_in0=None)
                ph = "inbound"
            else:
                return cmd, turn
        # inbound: the inbound track to the fix; timing from wings level on it
        cmd = inbound_cmd()
        if st.get("t_in0") is None and abs(self.bank) < 2.0:
            st["t_in0"] = st["t"]
        return cmd, None

    def _energy(self, dt: float) -> None:
        p = self.perf
        mode, value = self.speed_target()
        tgt = p.tas_for(mode, value, self.alt_ft)
        # envelope protection: never exceed MMO / VMO
        tgt = min(tgt, p.tas_for("mach", p.mmo - 0.005, self.alt_ft), p.tas_for("cas", p.vmo, self.alt_ft))
        dz = self.cleared_alt_ft - self.alt_ft
        dv = tgt - self.tas
        V = self.tas * atm.KT
        D = p.drag(self.alt_ft, self.tas, self.mass)
        Tclb = p.thrust_climb(self.alt_ft, self.tas)
        Tidle = p.thrust_idle(self.alt_ft, self.tas)
        h = self.alt_ft * atm.FT
        m = atm.tas_to_mach(V, h, p.dT)
        esf = atm.energy_share_factor(mode, m, h, p.dT)

        if abs(dz) < 1.0:                                   # level flight
            self.alt_ft = self.cleared_alt_ft
            self.vs_fpm = 0.0
            if dv > 0.3:
                T = Tclb
            elif dv < -0.3:
                T = Tidle
            else:
                T = D
            acc = (T - D) / self.mass / atm.KT * dt         # kt
            self.tas = self.tas + acc if abs(acc) < abs(dv) and (T != D) else tgt
        else:
            climbing = dz > 0
            T = Tclb if climbing else Tidle
            pex = (T - D) * V / self.mass                    # specific excess power, W/kg
            speed_change = abs(dv) > 1.0
            if not speed_change:
                rocd = pex / atm.G0 * esf
                self.tas = tgt
            elif not climbing and dv < 0:
                # slowing down in the descent: at idle the energy is shared (BADA's reduced energy share), a shallower
                # descent while the speed comes off (about 0.5 kt/s, against 0.7 level)
                drag_x = SPEEDBRAKE_FACTOR if (self.speedbrake or self.vs_cmd_fpm) else 1.0
                pex_d = (T - D * drag_x) * V / self.mass
                rocd = DECEL_DESCENT_SHARE * pex_d / atm.G0
                dvk = (1 - DECEL_DESCENT_SHARE) * pex_d / V * dt / atm.KT
                self.tas += dvk if abs(dvk) < abs(dv) else dv
            elif climbing and dv > 0:
                rocd = (1 - SPEED_SHARE) * pex / atm.G0
                dvk = SPEED_SHARE * pex / V * dt / atm.KT
                self.tas += dvk if abs(dvk) < abs(dv) else dv
            else:                                            # trade: climb while slowing / descend while speeding up
                rocd = pex / atm.G0 * esf
                dvk = math.copysign(min(abs(dv), 0.5 * dt), dv)
                self.tas += dvk
            fpm = rocd / atm.FT * 60.0
            mode = getattr(self, "vs_cmd_mode", "min")
            if climbing:
                fpm = max(fpm, 0.0)
                if self.vs_cmd_fpm and mode == "max":
                    fpm = min(fpm, self.vs_cmd_fpm)          # at X or less (an "or greater" was checked when given)
            else:
                fpm = min(fpm, 0.0)
                if self.vs_cmd_fpm:
                    if mode == "max":
                        fpm = -min(self.vs_cmd_fpm, abs(fpm))
                    else:                                    # at X or greater: speedbrakes if idle is not enough
                        fpm = -min(max(self.vs_cmd_fpm, abs(fpm)), SPEEDBRAKE_FACTOR * abs(fpm))
                elif self.speedbrake:
                    fpm = SPEEDBRAKE_FACTOR * fpm          # high on profile: speedbrakes out
                else:                                      # its own descent: the managed profile, not a dive at idle
                    fpm = max(fpm, -descent_profile_fpm(self.tas, self.alt_ft))
            step = fpm * dt / 60.0
            if abs(step) >= abs(dz):
                self.alt_ft = self.cleared_alt_ft
                self.vs_fpm = 0.0
                self.vs_cmd_fpm = None
            else:
                self.alt_ft += step
                self.vs_fpm = fpm
            if climbing and self.vs_cmd_fpm and fpm < pex / atm.G0 * esf / atm.FT * 60.0:
                T = D + (T - D) * fpm / max(pex / atm.G0 * esf / atm.FT * 60.0, 1e-6)
        self.thrust_n = T
        burn = p.fuel_flow(T, self.tas) * dt
        self.mass -= burn
        if self.active:                    # the sector's fuel metric: what was burnt while in our sector
            self.fuel_used += burn

    # ---------------------------------------------------------------- performance queries
    def climb_rate_available(self, alt_ft: Optional[float] = None) -> float:
        a = self.alt_ft if alt_ft is None else alt_ft
        mode, val = self.speed_target(a)
        return self.perf.roc_fpm(a, mode, val, self.mass)

    def descent_rate_idle(self, alt_ft: Optional[float] = None) -> float:
        a = self.alt_ft if alt_ft is None else alt_ft
        mode, val = self.speed_target(a)
        return -self.perf.roc_fpm(a, mode, val, self.mass, "idle")

    def descent_rate_normal(self, alt_ft: Optional[float] = None) -> float:
        """The rate of its own descent (no rate given, no speedbrakes): the managed profile, at most idle."""
        a = self.alt_ft if alt_ft is None else alt_ft
        mode, val = self.speed_target(a)
        return min(self.descent_rate_idle(a), descent_profile_fpm(self.perf.tas_for(mode, val, a), a))

    def normal_speed(self, alt_ft: Optional[float] = None) -> Tuple[str, float]:
        """(mode, value) the aircraft flies at a level with no speed assigned: its own Mach / IAS schedule."""
        saved = (self.spd_mach, self.spd_cas, self.spd_ias_now)
        try:
            self.spd_mach, self.spd_cas, self.spd_ias_now = None, None, False
            return self.speed_target(self.alt_ft if alt_ft is None else alt_ft)
        finally:
            self.spd_mach, self.spd_cas, self.spd_ias_now = saved

    def max_fl(self) -> int:
        mode, val = self.speed_target()
        return self.perf.max_fl(mode, val, self.mass)

    # ---------------------------------------------------------------- snapshot
    def to_exit_nm(self, wps: Dict[str, Point]) -> Optional[float]:
        """Distance to the exit point along the planned route (from the present position)."""
        rest = [w for w in self.route[self.next_index:] if w in wps]
        if not rest:
            return None
        d, p = 0.0, self.pos
        for w in rest:
            d += dist(p, wps[w])
            p = wps[w]
        return d

    def snapshot(self, with_trail: bool = False) -> dict:
        from .flightplan import mode_s
        mode, val = self.speed_target()
        wind = describe_uv(*self.wind_uv)
        d = {
            "callsign": self.callsign,
            "type": self.icao,
            "type_name": self.perf.name,
            "status": self.status,
            "x_nm": round(self.x, 3),
            "y_nm": round(self.y, 3),
            "altitude_ft": round(self.alt_ft),
            "fl": int(round(self.alt_ft / 100.0)),
            "cleared_fl": int(round(self.cleared_alt_ft / 100.0)),
            "vertical_speed_fpm": round(self.vs_fpm),
            "assigned_rate_fpm": self.vs_cmd_fpm,
            "assigned_rate_mode": getattr(self, "vs_cmd_mode", "min") if self.vs_cmd_fpm else None,
            "clearance_limits": {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                                 for k, v in (getattr(self, "clr_limits", None) or {}).items()},
            "heading": api_hdg(self.hdg),
            "heading_deg": round(self.hdg, 1),
            "track_deg": round(self.trk, 1),
            "bank_deg": round(self.bank, 1),
            "tas_kt": round(self.tas),
            "gs_kt": round(self.gs),
            "ias_kt": round(self.cas()),
            "mach": round(self.mach(), 3),
            "speed_mode": mode,
            "speed_target": round(val, 3) if mode == "mach" else round(val),
            "speed_assigned_by_atc": self.speed_assigned,
            "speed_restriction": ({"mach": self.spd_mach, "conversion_kt": None if self.spd_ias_now else self.spd_cas,
                                   "ias_kt": self.spd_cas if self.spd_ias_now else None}
                                  if self.speed_assigned else None),
            "speed_pair": {"mach": round(self.speed_pair()[0], 2), "ias_kt": round(self.speed_pair()[1]),
                           "conversion_fl": int(self.conversion_ft() // 100)},
            "wind": wind,
            "nav_mode": self.nav_mode,
            "assigned_heading": api_hdg(self.target_hdg) if (self.nav_mode in ("heading", "wxdev") and self.target_hdg is not None) else None,
            "weather_deviation": ({**self.wx_plan, "flown_nm": round(self.wx_flown, 1)} if self.nav_mode == "wxdev" else None),
            "weather_phase": self.wx["phase"],
            "weather_ahead_nm": self.wx["hazard_ahead_nm"],
            "turbulence": self.wx["turb_level"],
            "route": [w for w in self.route if not w.startswith("@")],
            "on_frequency": self.on_frequency, "assumed": self.assumed, "released": self.released,
            "control": ("left the sector, still assumed (decontrol it)" if self.outside and self.assumed else
                        "next sector" if self.status in ("exited", "left_sector") or self.released else
                        "you" if self.assumed else "calling you, not assumed" if self.on_frequency else "previous sector"),
            "exit_point": self.exit_point,
            "entry_waypoint": self.entry_wp,
            "destination": self.destination,
            "arrival": ({k: v for k, v in self.arr.items() if not k.startswith("_")} if self.destination else None),
            "speedbrake": self.speedbrake,
            "flight_plan": self.flight_plan,
            "squawk": (self.emergency or {}).get("code") or (self.flight_plan or {}).get("ssr"),
            "emergency": self.emergency,
            "ident_until_s": getattr(self, "ident_until", None),
            "mode_s": mode_s(self, self.mag_var),
            "fp_next": self.next_wp,
            "prev_unit": self.prev_unit, "next_unit": self.next_unit,
            "route_before_entry": self.route_before, "route_after_exit": self.route_after,
            "category": "military" if self.military else "civil",
            "formation": self.formation,
            "rvsm": {"approved": self.rvsm_approved, "status": self.rvsm_status,
                     "reason": self.rvsm_reason, "since_s": self.rvsm_since,
                     "needs_2000ft": self.non_rvsm},
            "equipment": self.equipment,
            "direction": "southbound" if self.southbound else "northbound",
            "level_parity_ok": level_parity_ok(int(round(self.cleared_alt_ft / 100.0)), self.southbound),
            "next_waypoint": self.next_wp if self.nav_mode == "lnav" else None,
            "cross_track_nm": round(self.xte_nm, 2) if self.nav_mode == "lnav" else None,
            "holding": ({"fix": (getattr(self, "hold_spec", None) or {}).get("fix"), "entry": self.hold_state["entry"],
                         "phase": self.hold_state["phase"]} if getattr(self, "hold_state", None) else None),
            "planned_exit": self.planned_exit,
            "current_exit": self.exit_wp,
            "mass_kg": round(self.mass),
            "fuel_used_kg": round(self.fuel_used, 1),
            "distance_flown_nm": round(self.distance_flown, 2),
        }
        if with_trail:
            d["trail"] = [list(p) for p in self.trail]
        return d

    def performance_snapshot(self) -> dict:
        return {
            "max_fl_at_present_weight": self.max_fl(),
            "climb_rate_available_fpm": round(self.climb_rate_available()),
            "idle_descent_rate_fpm": round(self.descent_rate_idle()),
            "normal_descent_rate_fpm": round(self.descent_rate_normal()),
            "assignable_mach": [self.perf.mach_assign_min, self.perf.mach_assign_max],
            "assignable_ias_kt": [self.perf.cas_assign_min, self.perf.cas_assign_max],
            "turn_radius_nm": round(self.turn_radius_nm(), 2),
            "own_speed_schedule": {"cas_kt": self.perf.cas_schedule, "mach": self.filed_mach or self.perf.cruise_mach,
                                   "conversion_fl": int(self.conversion_ft(self.filed_mach or self.perf.cruise_mach,
                                                                           self.perf.cas_schedule) // 100)},
        }
