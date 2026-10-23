"""Arrivals: destination aerodromes behind the sequencing fixes, runway in use, approach paths, top-of-descent
computation and the pilots' descent requests, and the sequencing rules at the fixes.

Each sequencing fix (default E1 and S4) leads to a destination aerodrome outside the sector. Every flight whose
exit point is a sequencing fix is an arrival to that aerodrome. Its pilot continuously computes:

    distance to go  = remaining route to the fix + approach path from the fix to the final approach fix (FAF)
    distance needed = idle descent from the present level to FAF altitude (aerodrome elevation + 3000 ft),
                      integrated with the aircraft's own performance (OpenAP + calibration), descent speed
                      schedule (Mach -> CAS -> 250 kt below FL100), the wind along the way, the deceleration to
                      250 kt at FL100 and to approach speed before the FAF
    top of descent  = where distance to go == distance needed

The approach path depends on the runway in use:
    straight-in : fix -> intermediate fix on the extended centreline -> FAF -> threshold
    reversal    : the aircraft arrives from the far side of the runway in use: fix -> aerodrome overhead ->
                  outbound on the approach side (offset 4 NM) -> 180 deg turn -> inbound -> FAF -> threshold,
                  i.e. ~30 NM more to fly, so the top of descent comes much later.

At the top of descent the pilot asks for descent; while nothing is given the aircraft stays level and gets high
(it repeats the request and tells you how far past its TOD it is). Cleared late, it descends at idle with
speedbrakes. At the fix, spacing to the previous arrival (miles in trail) and the crossing level constraint are
checked and scored.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from . import atmosphere as atm
from .geometry import Point, bearing, dist, polyline_length

if TYPE_CHECKING:
    from .aircraft import Aircraft
    from .world import World

ARR_PERIOD_S = 10.0
REQUEST_AHEAD_NM = 3.0          # pilots ask a little before their computed TOD
REPEAT_S = 60.0
RETRY_AFTER_UNABLE_S = 90.0
EARLY_DESCENT_FPM = 1000.0      # managed descent started before the top of descent: about 1000 ft/min until the
                                # aircraft meets its idle descent path (then idle, Mach / IAS schedule)
FINAL_DECEL_NM = 4.0            # 250 kt -> approach speed before the FAF
BUFFER_NM = 2.0

DEFAULT_AERODROMES = [
    {"id": "EFLD", "name": "Eastfield (fictional)", "x": 97.0, "y": 6.0, "elev_ft": 300,
     "runways": [{"id": "09", "heading": 90}, {"id": "27", "heading": 270}], "runway_in_use": "auto"},
    {"id": "SPRT", "name": "Southport (fictional)", "x": 58.0, "y": -148.0, "elev_ft": 150,
     "runways": [{"id": "18", "heading": 180}, {"id": "36", "heading": 360}], "runway_in_use": "auto"},
]
# Letter of agreement with the next sector: arrivals level at FL250 at the fix, 10 NM in trail.
# FL250 is the bottom of this sector, so it is also the lowest level arrivals can be given here.
DEFAULT_SEQUENCING = [
    {"fix": "E1", "aerodrome": "EFLD", "spacing_nm": 10, "cross_at_fl": 250, "min_fl": 250},
    {"fix": "S4", "aerodrome": "SPRT", "spacing_nm": 10, "cross_at_fl": 250, "min_fl": 250},
]
LOA_LEVEL_TOLERANCE_FT = 300


@dataclass
class Aerodrome:
    id: str
    name: str
    x: float
    y: float
    elev_ft: float
    runways: Dict[str, float]                 # id -> landing heading (deg true)
    in_use: str = ""
    schedule: List[Tuple[float, str]] = field(default_factory=list)
    final_nm: float = 10.0
    faf_agl_ft: float = 3000.0
    outbound_nm: float = 12.0
    racetrack_nm: float = 4.0
    _sidx: int = 0

    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    @property
    def faf_alt_ft(self) -> float:
        return self.elev_ft + self.faf_agl_ft

    def advance(self, t: float) -> Optional[str]:
        changed = None
        while self._sidx < len(self.schedule) and self.schedule[self._sidx][0] <= t:
            rwy = self.schedule[self._sidx][1]
            self._sidx += 1
            if rwy != self.in_use:
                self.in_use = rwy
                changed = rwy
        return changed

    def snapshot(self) -> dict:
        return {"id": self.id, "name": self.name, "x_nm": self.x, "y_nm": self.y, "elev_ft": self.elev_ft,
                "runways": [{"id": k, "landing_heading": v} for k, v in self.runways.items()],
                "runway_in_use": self.in_use,
                "runway_changes": [{"time_s": t, "runway": r} for t, r in self.schedule[self._sidx:]]}


@dataclass
class SeqFix:
    fix: str
    aerodrome: str
    spacing_nm: float
    cross_at_fl: Optional[int] = None            # level at this FL at the fix (LoA)
    cross_at_or_below_fl: Optional[int] = None   # alternative form of constraint
    min_fl: int = 250                            # lowest level that can be assigned to these arrivals here
    speed_at_fix_kt: Optional[float] = None      # optional IAS at the fix (+-10 kt)

    @property
    def target_fl(self) -> Optional[int]:
        return self.cross_at_fl or self.cross_at_or_below_fl

    def describe(self) -> str:
        c = (f"level FL{self.cross_at_fl}" if self.cross_at_fl else
             f"at or below FL{self.cross_at_or_below_fl}" if self.cross_at_or_below_fl else "no level constraint")
        sp = f", {int(self.speed_at_fix_kt)} kt" if self.speed_at_fix_kt else ""
        return f"{self.fix}: {c}{sp}, {self.spacing_nm:.0f} NM in trail"

    def snapshot(self) -> dict:
        return {"fix": self.fix, "aerodrome": self.aerodrome, "spacing_nm": self.spacing_nm,
                "cross_at_fl": self.cross_at_fl, "cross_at_or_below_fl": self.cross_at_or_below_fl,
                "speed_at_fix_kt": self.speed_at_fix_kt, "lowest_assignable_fl": self.min_fl,
                "letter_of_agreement": self.describe()}


def build(scn: dict, waypoints: Dict[str, Point], weather, asp=None) -> Tuple[Dict[str, Aerodrome], Dict[str, SeqFix]]:
    ads: Dict[str, Aerodrome] = {}
    d_ads = asp.default_aerodromes if asp is not None else DEFAULT_AERODROMES
    d_seq = asp.default_sequencing if asp is not None else DEFAULT_SEQUENCING
    for a in scn.get("aerodromes", d_ads):
        if "x" in a:
            x, y = float(a["x"]), float(a["y"])
        else:
            x, y = asp.resolve_point(a.get("coord") or {"lat": a["lat"], "lon": a["lon"]})
        ad = Aerodrome(id=a["id"], name=a.get("name", a["id"]), x=x, y=y,
                       elev_ft=float(a.get("elev_ft", 0)),
                       runways={str(r["id"]): float(r["heading"]) % 360 for r in a["runways"]},
                       schedule=sorted((float(e["time"]), str(e["runway"])) for e in a.get("runway_schedule", [])),
                       final_nm=float(a.get("final_nm", 10)), outbound_nm=float(a.get("outbound_nm", 12)))
        want = str(a.get("runway_in_use", "auto"))
        if want == "auto":
            # into the surface wind
            u, v = weather.at(ad.x, ad.y, 0.0, 0.0)
            best, score = None, -1e9
            for rid, hdg in ad.runways.items():
                h = math.radians(hdg)
                headwind = -(u * math.sin(h) + v * math.cos(h))
                if headwind > score:
                    best, score = rid, headwind
            want = best
        if want not in ad.runways:
            raise ValueError(f"aerodrome {ad.id}: unknown runway {want}")
        ad.in_use = want
        ads[ad.id] = ad
    seq: Dict[str, SeqFix] = {}
    for s in scn.get("sequencing", d_seq):
        if s["fix"] not in waypoints:
            raise ValueError(f"sequencing fix {s['fix']} is not a waypoint")
        if s["aerodrome"] not in ads:
            raise ValueError(f"sequencing fix {s['fix']}: unknown aerodrome {s['aerodrome']}")
        seq[s["fix"]] = SeqFix(fix=s["fix"], aerodrome=s["aerodrome"], spacing_nm=float(s.get("spacing_nm", 10)),
                               cross_at_fl=s.get("cross_at_fl"), cross_at_or_below_fl=s.get("cross_at_or_below_fl"),
                               min_fl=int(s.get("min_fl", 250)), speed_at_fix_kt=s.get("speed_at_fix_kt"))
    return ads, seq


# ----------------------------------------------------------------------------- approach geometry
def approach_path(fix: Point, ad: Aerodrome, runway: str) -> dict:
    h = math.radians(ad.runways[runway])
    u = (math.sin(h), math.cos(h))                       # landing direction
    n = (u[1], -u[0])                                    # right of the landing direction
    thr = (ad.x - u[0] * 0.8, ad.y - u[1] * 0.8)         # threshold ~0.8 NM before the reference point
    faf = (thr[0] - u[0] * ad.final_nm, thr[1] - u[1] * ad.final_nm)
    along = (fix[0] - thr[0]) * u[0] + (fix[1] - thr[1]) * u[1]
    if along < -ad.final_nm * 0.5:
        kind = "straight-in"
        if along < -(ad.final_nm + 4):
            ifx = (thr[0] - u[0] * (ad.final_nm + 4), thr[1] - u[1] * (ad.final_nm + 4))
            pts = [fix, ifx, faf, thr]
        else:
            pts = [fix, faf, thr]
        faf_idx = len(pts) - 2
    else:
        kind = "reversal"
        W, out = ad.racetrack_nm, ad.outbound_nm
        side = 1.0 if ((fix[0] - thr[0]) * n[0] + (fix[1] - thr[1]) * n[1]) >= 0 else -1.0
        nn = (n[0] * side, n[1] * side)                  # racetrack on the side the aircraft comes from
        ovh = ad.pos
        p1 = (ovh[0] + nn[0] * W, ovh[1] + nn[1] * W)
        p2 = (thr[0] - u[0] * out + nn[0] * W, thr[1] - u[1] * out + nn[1] * W)
        c = (thr[0] - u[0] * out + nn[0] * W / 2, thr[1] - u[1] * out + nn[1] * W / 2)
        arc = []
        for k in range(1, 8):
            ph = math.pi * k / 8
            arc.append((c[0] + W / 2 * (math.cos(ph) * nn[0] - math.sin(ph) * u[0]),
                        c[1] + W / 2 * (math.cos(ph) * nn[1] - math.sin(ph) * u[1])))
        p3 = (thr[0] - u[0] * out, thr[1] - u[1] * out)
        pts = [fix, ovh, p1, p2] + arc + [p3, faf, thr]
        faf_idx = len(pts) - 2
    to_faf = polyline_length(pts[:faf_idx + 1])
    return {"type": kind, "runway": runway, "points": [[round(p[0], 2), round(p[1], 2)] for p in pts],
            "to_faf_nm": round(to_faf, 1), "total_nm": round(polyline_length(pts), 1),
            "faf": [round(faf[0], 2), round(faf[1], 2)], "threshold": [round(thr[0], 2), round(thr[1], 2)]}


# ----------------------------------------------------------------------------- descent performance
def descent_distance(ac: "Aircraft", from_alt: float, to_alt: float, wind_along, approach_extras: bool = True) -> float:
    """NM needed for an idle descent from from_alt to to_alt (ft), with the speed schedule and the wind."""
    p = ac.perf
    m = ac.mass
    d = 0.0
    alt = from_alt
    while alt > to_alt + 1:
        a2 = max(to_alt, alt - 1000.0)
        amid = (alt + a2) / 2
        mode, val = ac.speed_target(amid)          # Mach / IAS pair in force, converting at its own altitude
        rod = max(-p.roc_fpm(amid, mode, val, m, "idle"), 600.0)
        tas = p.tas_for(mode, val, amid)
        gs = max(tas + wind_along(amid), 120.0)
        d += gs * (alt - a2) / rod / 60.0
        alt = a2
    if from_alt > 10000 > to_alt:
        # level deceleration at FL100 from the descent CAS to 250 kt (idle thrust, clean)
        cas_hi = ac.speed_pair()[1]
        if cas_hi > 250:
            tas = p.tas_for("cas", cas_hi, 10000)
            dec = (p.drag(10000, tas, m) - p.thrust_idle(10000, tas)) / m / atm.KT     # kt/s
            t = (cas_hi - 250) / max(dec, 0.3)
            d += (tas - 0.5 * (cas_hi - 250) + wind_along(10000)) * t / 3600.0
    return d + (FINAL_DECEL_NM + BUFFER_NM if approach_extras else 0.0)


def _route_to_fix(ac: "Aircraft", wps, fix: str) -> float:
    """Distance along the remaining route (from the present position) to the fix."""
    pos = ac.pos
    if ac.nav_mode == "wxdev" and ac.wx_plan:
        nxt = ac.wx_plan["then_direct"]
        start = ac.route.index(nxt) if nxt in ac.route else ac.next_index
        rem = max(ac.wx_plan["distance_nm"] - ac.wx_flown, 0.0)
        r = math.radians(ac.wx_plan.get("track", ac.trk))
        p1 = (pos[0] + rem * math.sin(r), pos[1] + rem * math.cos(r))
        d, prev = rem, p1
    else:
        start, d, prev = ac.next_index, 0.0, pos
    names = [w for w in ac.route[start:] if not w.startswith("@")]
    if fix not in names:
        return d + dist(prev, wps[fix])
    for w in names:
        d += dist(prev, wps[w])
        prev = wps[w]
        if w == fix:
            break
    return d


def handover_point(ac: "Aircraft") -> str:
    """Where the arrival leaves us: its sequencing fix, or the end of the route it was sent along instead."""
    names = [w for w in ac.route if not w.startswith("@")]
    fix = ac.arr["fix"]
    return fix if (fix in names or not names) else names[-1]


def compute(world: "World", ac: "Aircraft") -> dict:
    seq = world.seq[ac.arr["fix"]]
    ad = world.aerodromes[seq.aerodrome]
    via = handover_point(ac)
    path = world.approach(via, ad.id)
    to_fix = _route_to_fix(ac, world.waypoints, via)
    remaining = to_fix + path["to_faf_nm"]
    brg = math.radians(bearing(ac.pos, ad.pos))
    mx, my = (ac.x + ad.x) / 2, (ac.y + ad.y) / 2
    wx = world.env.weather

    def wind_along(alt_ft: float) -> float:
        u, v = wx.at(mx, my, alt_ft / 100.0, world.t)
        return u * math.sin(brg) + v * math.cos(brg)

    need = descent_distance(ac, ac.alt_ft, ad.faf_alt_ft, wind_along)
    out = {"fix": seq.fix, "aerodrome": ad.id, "runway": ad.in_use, "approach": path["type"],
           "distance_to_fix_nm": round(to_fix, 1), "distance_to_faf_nm": round(remaining, 1),
           "descent_needed_nm": round(need, 1), "tod_in_nm": round(remaining - need, 1),
           "letter_of_agreement": seq.describe()}
    if seq.target_fl and ac.alt_ft > seq.target_fl * 100 + 100:
        # latest point to start an idle descent (own speeds) and still be at the LoA level at the fix
        need_loa = descent_distance(ac, ac.alt_ft, seq.target_fl * 100.0, wind_along, approach_extras=False)
        out["loa_descent_needed_nm"] = round(need_loa, 1)
        out["loa_latest_descent_in_nm"] = round(to_fix - need_loa, 1)
    return out


# ----------------------------------------------------------------------------- pilot logic
def update(world: "World", ac: "Aircraft") -> None:
    st = ac.arr
    if world.t - st.get("_last", -1e9) < ARR_PERIOD_S * (1 if world.record else 3):
        return
    st["_last"] = world.t
    via = handover_point(ac)
    if via != st["fix"] and (via in world.airspace.boundary_points or not world.airspace.inside(world.waypoints[via])):
        return                                  # rerouted away from its arrival fix, out of the sector sideways
    info = compute(world, ac)
    st.update(info)
    tod = info["tod_in_nm"]
    seq = world.seq[st["fix"]]
    descending = ac.cleared_alt_ft < ac.alt_ft - 300
    # cleared down before its own top of descent: the FMS descends gently (about 1000 ft/min) until it meets the
    # idle path, then idle - not straight down at idle (a rate given by ATC takes precedence)
    own = st.get("_vs_own", False)
    if descending and (ac.vs_cmd_fpm is None or own):
        if tod > 1.0:
            ac.vs_cmd_fpm, ac.vs_cmd_mode = EARLY_DESCENT_FPM, "max"
            st["_vs_own"] = True
        elif own:
            ac.vs_cmd_fpm = None
            st["_vs_own"] = False
    elif own and not descending:
        st["_vs_own"] = False
    ad = world.aerodromes[seq.aerodrome]
    # speedbrakes when well above the profile
    if descending and tod < -4:
        if not ac.speedbrake and world.record:
            world._event("pilot_report", callsign=ac.callsign,
                         text=f"{ac.callsign} we're high, {abs(tod):.0f} NM past our descent point, speedbrakes out")
        ac.speedbrake = True
    elif ac.speedbrake and (tod > -1 or not descending):
        ac.speedbrake = False
    # descent request
    req = world.requests.get(st.get("_req")) if st.get("_req") else None
    pending = req is not None and req.status == "pending"
    if req is not None and req.status == "unable" and "_unable_at" not in st:
        st["_unable_at"] = world.t
    floor = max(seq.min_fl * 100.0, ad.faf_alt_ft)
    if descending or ac.alt_ft <= floor + 500:
        return
    if pending:
        if tod < -2 and world.t - st.get("_req_t", world.t) >= REPEAT_S and world.t - st.get("_rep_t", -1e9) >= REPEAT_S:
            st["_rep_t"] = world.t
            world._event("request_repeated", request_id=req.id, callsign=ac.callsign,
                         text=f"{ac.callsign} still requesting descent, {abs(tod):.0f} NM past our top of descent")
        return
    if st.get("_unable_at") is not None and world.t - st["_unable_at"] < RETRY_AFTER_UNABLE_S:
        return
    scripted_again = st.get("auto", True) is False and req is not None and req.status == "unable"
    if (tod <= REQUEST_AHEAD_NM and st.get("auto", True) is not False) or scripted_again:
        cur = int(round(ac.alt_ft / 100.0))
        target = seq.target_fl if (seq.target_fl and cur > seq.target_fl + 5) else seq.min_fl
        further = cur < int(round(ac.fl)) - 5
        reason = (f"top of descent for {ad.id} runway {ad.in_use} ({info['approach']}), "
                  f"{info['distance_to_faf_nm']:.0f} NM to the FAF" + (", further descent" if further else ""))
        rid = world.add_dynamic_request(ac.callsign, "altitude", target, reason, kind="descent")
        st.pop("_unable_at", None)
        st.update(_req=rid, _req_t=world.t)


# ----------------------------------------------------------------------------- sequencing
def on_fix_crossing(world: "World", ac: "Aircraft") -> None:
    seq = world.seq.get(ac.exit_point)
    if (seq is None and ac.arr and ac.arr.get("fix") in world.seq and ac.exit_point in world.waypoints
            and ac.exit_point not in world.airspace.boundary_points):
        seq = world.seq[ac.arr["fix"]]          # sent another way (e.g. via DEVNO): judged where it was handed over
    if seq is None or not ac.destination:
        return
    fl = int(round(ac.alt_ft / 100.0))
    log = world.m["sequencing"].setdefault(seq.fix, [])
    prev = log[-1] if log else None
    spacing = None
    if prev is not None:
        spacing = round((world.t - prev["time_s"]) * ac.gs / 3600.0, 1)
    if seq.cross_at_fl:
        level_ok = abs(ac.alt_ft - seq.cross_at_fl * 100.0) <= LOA_LEVEL_TOLERANCE_FT
    else:
        level_ok = seq.cross_at_or_below_fl is None or fl <= seq.cross_at_or_below_fl
    coord = ac.arr.get("coordinated_fl")
    if not level_ok and coord is not None and fl <= (seq.cross_at_fl or seq.cross_at_or_below_fl or fl):
        level_ok = True                         # cleared below the LoA level after coordination with the next sector
    loa = seq.cross_at_fl or seq.cross_at_or_below_fl
    descending = False
    if not level_ok and loa and ac.cleared_alt_ft <= loa * 100.0 + 50 and ac.alt_ft > loa * 100.0:
        level_ok = descending = True            # cleared to the agreed level, still descending to it: as agreed
    speed_ok = seq.speed_at_fix_kt is None or abs(ac.cas() - seq.speed_at_fix_kt) <= 10
    spacing_ok = spacing is None or spacing >= seq.spacing_nm
    rec = {"callsign": ac.callsign, "time_s": round(world.t, 1), "fl": fl, "ias_kt": round(ac.cas()),
           "gs_kt": round(ac.gs),
           "behind": prev["callsign"] if prev else None, "spacing_nm": spacing,
           "spacing_ok": spacing_ok, "level_ok": level_ok, "speed_ok": speed_ok,
           "coordinated_fl": coord, "descending": descending}
    log.append(rec)
    if not spacing_ok:
        world.m["seq_spacing_violations"] += 1
    if not level_ok:
        world.m["seq_level_violations"] += 1
    if not speed_ok:
        world.m["seq_speed_violations"] = world.m.get("seq_speed_violations", 0) + 1
    want = (f"FL{seq.cross_at_fl}" if seq.cross_at_fl else
            f"at or below FL{seq.cross_at_or_below_fl}" if seq.cross_at_or_below_fl else None)
    lvl = f"FL{fl}" + (f" descending FL{int(round(ac.cleared_alt_ft / 100))}" if descending else "") + ("" if want is None else f" ({'OK' if level_ok else 'NOT'} {want}"
                       + (f", coordinated FL{coord}" if coord is not None else "") + ")")
    if seq.speed_at_fix_kt:
        lvl += f", {round(ac.cas())} kt ({'OK' if speed_ok else 'NOT'} {int(seq.speed_at_fix_kt)} kt)"
    spc = "" if spacing is None else f", {spacing} NM behind {prev['callsign']} ({'OK' if spacing_ok else 'LESS THAN'} {seq.spacing_nm:.0f} NM)"
    world._event("fix_crossing", callsign=ac.callsign, fix=seq.fix, fl=fl, spacing_nm=spacing,
                 spacing_ok=spacing_ok, level_ok=level_ok, via=None if ac.exit_point == seq.fix else ac.exit_point,
                 text=(f"{ac.callsign} crossed {seq.fix} {lvl}{spc}" if ac.exit_point == seq.fix else
                       f"{ac.callsign} handed over at {ac.exit_point} (instead of {seq.fix}) {lvl}{spc}"))


def sequence_status(world: "World") -> List[dict]:
    out = []
    for fix, seq in world.seq.items():
        ad = world.aerodromes[seq.aerodrome]
        path = world.approach(fix, ad.id)
        inbound = []
        for ac in world.aircraft.values():
            if not ac.destination or ac.planned_exit != fix or ac.status in ("exited", "left_sector"):
                continue
            if ac.status == "scheduled":
                route = [w for w in ac.route if not w.startswith("@")]
                L = polyline_length([world.waypoints[w] for w in route])
                tas = ac.perf.tas_for(*ac.speed_target(ac.fl * 100.0), ac.fl * 100.0)
                eta = ac.boundary_time + L / tas * 3600.0
                gs = tas
                row = {"callsign": ac.callsign, "type": ac.icao, "status": "not yet visible", "fl": ac.fl}
            else:
                d = _route_to_fix(ac, world.waypoints, fix)
                gs = max(ac.gs, 150.0)
                eta = world.t + d / gs * 3600.0
                row = {"callsign": ac.callsign, "type": ac.icao,
                       "status": "on frequency" if ac.on_frequency else "previous sector",
                       "fl": int(round(ac.alt_ft / 100)), "cleared_fl": int(round(ac.cleared_alt_ft / 100)),
                       "distance_to_fix_nm": round(d, 1), "gs_kt": round(ac.gs), "ias_kt": round(ac.cas()),
                       "mach": round(ac.mach(), 2), "tod_in_nm": ac.arr.get("tod_in_nm"),
                       "loa_latest_descent_in_nm": ac.arr.get("loa_latest_descent_in_nm")}
            row["eta_s"] = round(eta)
            row["_gs"] = gs
            inbound.append(row)
        inbound.sort(key=lambda r: r["eta_s"])
        prev_eta = None
        last = (world.m["sequencing"].get(fix) or [None])[-1]
        if last:
            prev_eta = last["time_s"]
        for r in inbound:
            if prev_eta is not None:
                r["spacing_to_preceding_nm"] = round((r["eta_s"] - prev_eta) * r["_gs"] / 3600.0, 1)
                r["spacing_ok"] = r["spacing_to_preceding_nm"] >= seq.spacing_nm
            prev_eta = r["eta_s"]
            del r["_gs"]
        out.append({**seq.snapshot(), "runway_in_use": ad.in_use, "approach": path["type"],
                    "approach_path_to_faf_nm": path["to_faf_nm"],
                    "inbound": inbound, "crossed": world.m["sequencing"].get(fix, [])})
    return out
