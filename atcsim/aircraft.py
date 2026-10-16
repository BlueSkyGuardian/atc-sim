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
XTE_GAIN_DEG_PER_NM = 12.0     # LNAV intercept angle per NM of cross-track error
MAX_INTERCEPT_DEG = 45.0
BANK_GAIN = 2.0                # commanded bank (deg) per degree of heading error, capped at nominal bank
SPEED_SHARE = 0.3              # share of excess power used for speed change while climbing/descending
SPEEDBRAKE_FACTOR = 1.5        # max descent rate with speedbrakes relative to idle descent
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
    first_contact_t: Optional[float] = None
    assumed_t: Optional[float] = None
    entered: bool = False              # has been inside the sector square
    destination: Optional[str] = None  # aerodrome id for arrivals (via a sequencing fix)
    arr: dict = field(default_factory=dict)   # arrival / top-of-descent state
    speedbrake: bool = False
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
        self.tas = self.perf.tas_for(*self.speed_target(), self.alt_ft)
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
        if self.nav_mode == "lnav":
            res = self._lnav(env, u, v, outside=outside)
            if res:
                return res
            hdg_cmd, forced = self._lnav_heading_cmd, None
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
        bank_cmd = max(-BANK_NOMINAL_DEG, min(BANK_NOMINAL_DEG, BANK_GAIN * err))
        roll = ROLL_RATE_DEG_S * dt
        self.bank += max(-roll, min(roll, bank_cmd - self.bank))
        tas_ms = max(self.tas, 100.0) * atm.KT
        rate = math.degrees(atm.G0 * math.tan(math.radians(self.bank)) / tas_ms)
        dpsi = rate * dt
        if err != 0 and dpsi * err > 0 and abs(dpsi) > abs(err):
            dpsi = err
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
            nxt = wps[self.route[self.next_index + 1]]
            dchi = abs(heading_diff(course, bearing(B, nxt)))
            anticipation = self.turn_radius_nm() * math.tan(math.radians(min(dchi, 120.0)) / 2.0)
            if to_go <= max(anticipation, 0.2):
                self.leg_from = B          # fly-by: sequence the next leg before the waypoint
                self.next_index += 1
                continue
            break
        intercept = max(-MAX_INTERCEPT_DEG, min(MAX_INTERCEPT_DEG, -XTE_GAIN_DEG_PER_NM * self.xte_nm))
        desired_track = (course + intercept) % 360.0
        self._lnav_heading_cmd = (desired_track - self._wca(desired_track, u, v)) % 360.0
        return None

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
            elif (climbing and dv > 0) or (not climbing and dv < 0):
                rocd = (1 - SPEED_SHARE) * pex / atm.G0
                dvk = SPEED_SHARE * pex / V * dt / atm.KT
                self.tas += dvk if abs(dvk) < abs(dv) else dv
            else:                                            # trade: climb while slowing / descend while speeding up
                rocd = pex / atm.G0 * esf
                dvk = math.copysign(min(abs(dv), 0.5 * dt), dv)
                self.tas += dvk
            fpm = rocd / atm.FT * 60.0
            if climbing:
                fpm = max(fpm, 0.0)
                if self.vs_cmd_fpm:
                    fpm = min(fpm, self.vs_cmd_fpm)
            else:
                fpm = min(fpm, 0.0)
                if self.vs_cmd_fpm:
                    fpm = -min(self.vs_cmd_fpm, SPEEDBRAKE_FACTOR * abs(fpm))
                elif self.speedbrake:
                    fpm = SPEEDBRAKE_FACTOR * fpm          # high on profile: speedbrakes out
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
            "squawk": (self.flight_plan or {}).get("ssr"),
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
            "assignable_mach": [self.perf.mach_assign_min, self.perf.mach_assign_max],
            "assignable_ias_kt": [self.perf.cas_assign_min, self.perf.cas_assign_max],
            "turn_radius_nm": round(self.turn_radius_nm(), 2),
            "own_speed_schedule": {"cas_kt": self.perf.cas_schedule, "mach": self.filed_mach or self.perf.cruise_mach,
                                   "conversion_fl": int(self.conversion_ft(self.filed_mach or self.perf.cruise_mach,
                                                                           self.perf.cas_schedule) // 100)},
        }
