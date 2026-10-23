"""Pilot behaviour driven by the weather: onboard weather radar scan, deviation planning, weather deviation
requests, deviating without clearance when a request is not granted in time, and turbulence (ride) requests.

Rules of thumb encoded here (configurable per scenario under weather.pilot):
  * hazard: reflectivity >= 30 dBZ at the aircraft's level, or any thunderstorm whose top is not at least
    2000 ft below the aircraft (radar tilted down: core >= 40 dBZ below); keep >= 6 NM from it
  * severe:  >= 40 dBZ at level, or such a storm with a core >= 50 dBZ; keep >= 10 NM
  * the onboard radar sees 80 NM ahead; the pilot asks for a deviation when the route ahead is blocked within
    60 NM, and deviates on his own authority (PIC) if nothing has been approved when it is 15 NM away
  * a deviation is a heading for N miles, then direct to a waypoint further along the route; the pilot picks the
    shortest dogleg that clears the weather with margin, with a small preference for the upwind side
  * climbing over the top is requested instead when the aircraft can reach >= 3000 ft above the echo tops in
    its way and that is shorter than going around
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional, Tuple

import numpy as np
from scipy.ndimage import distance_transform_edt

from .geometry import api_hdg, bearing, dist, heading_diff

if TYPE_CHECKING:
    from .aircraft import Aircraft
    from .world import World

SCAN_PERIOD_S = 10.0
REPEAT_AFTER_S = 75.0
GRID_RES = 2.0
TURB_MODERATE = 0.30
TURB_SEVERE = 0.50
TURB_SMOOTH = 0.18
TURB_CHECK_S = 15.0
TURB_PERSIST_S = 45.0
TURB_COOLDOWN_S = 300.0


def pilot_hazard(wx, X, Y, fl, t: float):
    """What a pilot treats as weather to avoid at a level, as seen on a tilted airborne radar.
    hazardous: reflectivity >= hazard_dbz at the level, OR a convective cell whose echo top reaches within
               2000 ft of the level (or above it) and whose core below is >= 40 dBZ (flying through or just
               over a CB top);
    severe   : reflectivity >= severe_dbz at the level, or such a cell with a core >= 50 dBZ.
    Returns (hazard, severe) boolean arrays."""
    P = wx.pilot
    z0 = wx.reflectivity(X, Y, fl, t)
    z20 = wx.reflectivity(X, Y, fl - 20, t)
    zc = np.maximum(wx.reflectivity(X, Y, fl - 60, t), wx.reflectivity(X, Y, fl - 120, t))
    core = np.maximum(zc, z20)
    in_cb = (z20 >= 18.0) & (core >= 40.0)
    haz = (z0 >= P["hazard_dbz"]) | in_cb
    sev = (z0 >= P["severe_dbz"]) | (in_cb & (core >= 50.0))
    return haz, sev


def new_wx_state() -> dict:
    return {"phase": "clear", "request_id": None, "requested_at": None, "repeated": False, "plan": None,
            "last_scan": -1e9, "hazard_ahead_nm": None, "self_deviations": 0,
            "turb_s": 0.0, "last_turb_check": -1e9, "turb_cooldown_until": -1e9, "turb_level": "smooth"}


# ----------------------------------------------------------------------------- path helpers
def _route_path(ac: "Aircraft", wps, max_nm: float, step: float = 2.0) -> np.ndarray:
    """Points ahead along the aircraft's intended path (route, heading or current deviation)."""
    pts = []
    pos = ac.pos
    if ac.nav_mode == "heading" or (ac.nav_mode == "wxdev" and ac.wx_plan is None):
        # while still turning, look along the assigned heading (corrected for drift), not the present track
        trk = ac.trk
        if ac.target_hdg is not None and abs(heading_diff(ac.hdg, ac.target_hdg)) > 3:
            trk = (ac.target_hdg + (ac.trk - ac.hdg)) % 360.0
        r = math.radians(trk)
        for d in np.arange(step, max_nm + 1e-6, step):
            pts.append((pos[0] + d * math.sin(r), pos[1] + d * math.cos(r)))
        return np.array(pts)
    legs: List[Tuple[float, float]] = []
    if ac.nav_mode == "wxdev":
        r = math.radians(ac.wx_plan.get("track", ac.trk))
        rem = max(ac.wx_plan["distance_nm"] - ac.wx_flown, 0.0)
        p1 = (pos[0] + rem * math.sin(r), pos[1] + rem * math.cos(r))
        legs = [pos, p1, wps[ac.wx_plan["then_direct"]]]
        idx = ac.route.index(ac.wx_plan["then_direct"]) if ac.wx_plan["then_direct"] in ac.route else len(ac.route)
        legs += [wps[w] for w in ac.route[idx + 1:]]
    else:
        legs = [pos] + [wps[w] for w in ac.route[ac.next_index:]]
    left = max_nm
    d0 = step
    for a, b in zip(legs[:-1], legs[1:]):
        L = dist(a, b)
        while d0 <= L and left > 0:
            f = d0 / L
            pts.append((a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])))
            d0 += step
            left -= step
        d0 -= L
        if left <= 0:
            break
    return np.array(pts) if pts else np.zeros((0, 2))


def _levels(ac: "Aircraft") -> List[float]:
    lv = {round(ac.alt_ft / 100.0)}
    if abs(ac.cleared_alt_ft - ac.alt_ft) > 300:
        lv.add(round(ac.cleared_alt_ft / 100.0))
    return sorted(lv)


SCAN_MARGIN_SCALE = 0.7      # the look-ahead scan uses 70% of the planning margins (hysteresis), so a planned
                             # deviation does not immediately trigger another request


def hazard_ahead(world: "World", ac: "Aircraft", path: Optional[np.ndarray] = None,
                 levels: Optional[List[float]] = None, margin_scale: float = SCAN_MARGIN_SCALE) -> Optional[float]:
    """Distance (NM along the path) to the first point closer to weather than the pilot's margins, or None."""
    wx = world.env.weather
    P = wx.pilot
    if path is None:
        path = _route_path(ac, world.waypoints, P["radar_range_nm"])
    if len(path) == 0:
        return None
    seg = np.diff(np.vstack([np.array(ac.pos)[None, :], path]), axis=0)
    along = np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))
    nx = -seg[:, 1] / np.maximum(np.hypot(seg[:, 0], seg[:, 1]), 1e-9)
    ny = seg[:, 0] / np.maximum(np.hypot(seg[:, 0], seg[:, 1]), 1e-9)
    m, sm = P["margin_nm"] * margin_scale, P["severe_margin_nm"] * margin_scale
    offs = np.array([0.0, -m, m, -sm, sm])
    X = path[:, 0][:, None] + nx[:, None] * offs[None, :]
    Y = path[:, 1][:, None] + ny[:, None] * offs[None, :]
    hit = np.zeros(len(path), bool)
    if levels is None:
        # altitude the aircraft will have when it gets to each point (climbing/descending to its cleared level)
        dz = ac.cleared_alt_ft - ac.alt_ft
        if abs(dz) > 300:
            rate = abs(ac.vs_fpm) or (ac.climb_rate_available() if dz > 0 else ac.descent_rate_normal())
            t_min = along / max(ac.gs, 100.0) * 60.0
            alt = ac.alt_ft + np.sign(dz) * np.minimum(abs(dz), max(rate, 300.0) * t_min)
        else:
            alt = np.full(len(path), ac.alt_ft)
        lv = [np.round(alt / 100.0)[:, None] * np.ones((1, len(offs)))]
    else:
        lv = levels
    for fl in lv:
        haz, sev = pilot_hazard(wx, X, Y, fl, world.t)
        hit |= haz[:, :3].any(axis=1) | sev.any(axis=1)
    if not hit.any():
        return None
    return float(along[int(np.argmax(hit))])


# ----------------------------------------------------------------------------- planning
def _rejoin_candidates(ac: "Aircraft") -> List[str]:
    if ac.nav_mode == "wxdev" and ac.wx_plan:
        start = ac.route.index(ac.wx_plan["then_direct"]) if ac.wx_plan["then_direct"] in ac.route else ac.next_index
    else:
        start = ac.next_index
    return ac.route[start:]


def plan_deviation(world: "World", ac: "Aircraft", side: str = "either", max_offset_nm: float = 1e9,
                   rejoin: Optional[str] = None) -> Optional[dict]:
    """Shortest dogleg (track offset theta for L1 NM, then direct to a downstream waypoint) clear of the weather."""
    wx = world.env.weather
    P = wx.pilot
    wps = world.waypoints
    cands = [rejoin] if rejoin else _rejoin_candidates(ac)
    if not cands:
        return None
    # reference course: towards the next waypoint (route) or present track (vectored)
    if ac.nav_mode == "lnav" and ac.next_wp:
        c0 = bearing(ac.pos, wps[ac.next_wp])
    else:
        c0 = ac.trk
    cr, sr = math.cos(math.radians(c0)), math.sin(math.radians(c0))

    def to_world(a, c):          # local (along, cross-right) -> world
        return ac.x + a * sr + c * cr, ac.y + a * cr - c * sr

    def to_local(x, y):
        dx, dy = x - ac.x, y - ac.y
        return dx * sr + dy * cr, dx * cr - dy * sr

    A = np.arange(-8.0, 110.0 + 1e-6, GRID_RES)
    C = np.arange(-66.0, 66.0 + 1e-6, GRID_RES)
    AA, CC = np.meshgrid(A, C, indexing="ij")
    X, Y = to_world(AA, CC)
    haz = np.zeros(X.shape, bool)
    sev = np.zeros(X.shape, bool)
    for fl in _levels(ac):
        h_, s_ = pilot_hazard(wx, X, Y, fl, world.t)
        haz |= h_
        sev |= s_
    d_h = distance_transform_edt(~haz) * GRID_RES if haz.any() else np.full(X.shape, 1e9)
    d_s = distance_transform_edt(~sev) * GRID_RES if sev.any() else np.full(X.shape, 1e9)
    pad = 0.5 * GRID_RES          # grid quantisation allowance
    blocked = (d_h < P["margin_nm"] + pad) | (d_s < P["severe_margin_nm"] + pad)

    def lookup(a, c):
        ia = np.round((a - A[0]) / GRID_RES).astype(int)
        ic = np.round((c - C[0]) / GRID_RES).astype(int)
        inside = (ia >= 0) & (ia < len(A)) & (ic >= 0) & (ic < len(C))
        out = np.zeros(a.shape, bool)
        out[inside] = blocked[ia[inside], ic[inside]]
        return out

    # upper-level wind: prefer the upwind side (anvils and hail are blown downwind)
    u, v = wx.wind_at(ac.x, ac.y, ac.alt_ft / 100.0, world.t)
    wind_right = float(u) * cr - float(v) * sr           # wind component toward the right of course
    best = None
    thetas = [t for t in range(10, 85, 5)]
    sides = {"left": [-1], "right": [1], "either": [-1, 1]}[side]
    L1s = np.arange(5.0, 105.0, 5.0)
    for rj in cands:
        wa, wc = to_local(*wps[rj])
        direct = math.hypot(wa, wc)
        for sgn in sides:
            for th in thetas:
                t = math.radians(th * sgn)
                for L1 in L1s:
                    p1a, p1c = L1 * math.cos(t), L1 * math.sin(t)
                    if abs(p1c) > max_offset_nm:
                        break
                    l2 = math.hypot(wa - p1a, wc - p1c)
                    # sample both legs
                    s1 = np.arange(2.0, L1 + 1e-6, GRID_RES)
                    n2 = max(int(l2 / GRID_RES), 1)
                    s2 = np.linspace(0.0, 1.0, n2 + 1)[1:]
                    la = np.concatenate([s1 * math.cos(t), p1a + s2 * (wa - p1a)])
                    lc = np.concatenate([s1 * math.sin(t), p1c + s2 * (wc - p1c)])
                    if lookup(la, lc).any():
                        continue
                    extra = L1 + l2 - direct
                    px, py = to_world(la, lc)
                    asp = world.airspace
                    if asp.square_half_nm is not None:
                        outside = np.clip(np.maximum(np.abs(px), np.abs(py)) - (asp.square_half_nm + 1), 0,
                                          None).sum() * GRID_RES
                    else:
                        outside = float((~asp.inside_mask(px, py)).sum()) * GRID_RES
                    downwind = 3.0 if (sgn > 0) == (wind_right > 0) else 0.0
                    cost = extra + 0.5 * outside + downwind
                    if best is None or cost < best[0]:
                        best = (cost, sgn, th, L1, rj, extra, outside)
                    break          # longer L1 on the same heading only costs more
    if best is None:
        return None
    cost, sgn, th, L1, rj, extra, outside = best
    track = (c0 + sgn * th) % 360.0
    from .aircraft import wind_correction
    hdg = (track - wind_correction(ac.tas, track, float(u), float(v))) % 360.0
    hdg5 = int(5 * round(hdg / 5.0)) % 360 or 360
    return {"heading": hdg5, "track": round(float(track), 1), "distance_nm": int(L1), "then_direct": rj,
            "side": "left" if sgn < 0 else "right", "offset_deg": int(th), "extra_nm": round(float(extra), 1),
            "leaves_sector": bool(outside > 0)}


def scripted_plan(world: "World", ac: "Aircraft", spec: dict) -> dict:
    """Turn a scenario's weather-deviation request into a concrete plan at the moment it is made.
    spec (all optional):
      {"heading": 250, ...}                 fixed heading
      {"side": "left", "offset_deg": 30}    track offset from the present track
      {"side": "left"|"right"|"either"}     (no heading / offset) the pilot's own plan around the real weather
      "distance_nm": 20, "then_direct": "IBALU"
    Without weather to plan around, the 'auto' form falls back to a 30 deg offset on the requested side."""
    from .aircraft import wind_correction
    spec = dict(spec or {})
    side = str(spec.get("side", "either")).lower()
    rejoin = spec.get("then_direct")
    rejoin = str(rejoin).upper() if rejoin else None
    if "heading" not in spec and "offset_deg" not in spec:
        plan = (plan_deviation(world, ac, side, float(spec.get("max_offset_nm", 1e9)), rejoin)
                if hazard_ahead(world, ac) is not None else None)
        if plan is not None:
            if "distance_nm" in spec:
                plan["distance_nm"] = int(spec["distance_nm"])
            return plan
        spec["offset_deg"] = 30
    wps = world.waypoints
    L1 = float(spec.get("distance_nm", 20))
    u, v = world.env.weather.at(ac.x, ac.y, ac.alt_ft / 100.0, world.t)
    if "heading" in spec:
        hdg = int(spec["heading"]) % 360 or 360
        track = (hdg + wind_correction(ac.tas, float(hdg), float(u), float(v))) % 360.0
        dh = heading_diff(track, ac.trk)
        side_out = "left" if dh < 0 else "right"
        off = abs(dh)
    else:
        sgn = -1.0 if side == "left" else 1.0
        off = float(spec["offset_deg"])
        track = (ac.trk + sgn * off) % 360.0
        hdg = int(5 * round(((track - wind_correction(ac.tas, track, float(u), float(v))) % 360.0) / 5.0)) % 360 or 360
        side_out = "left" if sgn < 0 else "right"
    r = math.radians(track)
    p1 = (ac.x + L1 * math.sin(r), ac.y + L1 * math.cos(r))
    if rejoin is None or rejoin not in wps:
        down = ac.route[ac.next_index:] or ac.route[-1:]
        rejoin = next((w for w in down if w in wps and dist(wps[w], p1) > 1.3 * L1), down[-1])
    via = L1 + dist(p1, wps[rejoin])
    asp = getattr(world.env, "airspace", None)
    return {"heading": hdg, "track": round(track, 1), "distance_nm": int(L1), "then_direct": rejoin,
            "side": side_out, "offset_deg": int(round(off)), "extra_nm": round(max(0.0, via - dist(ac.pos, wps[rejoin])), 1),
            "leaves_sector": bool(asp is not None and not asp.inside(p1))}


def pilot_level_ok(ac: "Aircraft", fl: int) -> bool:
    """Levels a pilot asks for: right direction for the semicircular rule (arrivals excepted), and outside RVSM
    airspace for an aircraft that is not RVSM approved / unable RVSM."""
    from .aircraft import level_parity_ok
    if not ac.destination and not level_parity_ok(fl, ac.southbound):
        return False
    if ac.non_rvsm and not ac.military and 290 <= fl <= 410:
        return False
    return True


def plan_climb_over(world: "World", ac: "Aircraft", hazard_nm: float) -> Optional[int]:
    """Lowest level (>= 3000 ft above the weather in the way) where the path is clear, if the aircraft can reach it."""
    path = _route_path(ac, world.waypoints, min(world.env.weather.pilot["radar_range_nm"], hazard_nm + 40))
    cur = int(round(ac.cleared_alt_ft / 100.0))
    mode, val = ac.speed_target()
    for fl in range(cur + 20, min(world.upper_fl, int(ac.perf.ceiling_ft // 100)) + 1, 10):
        if not pilot_level_ok(ac, fl):
            continue
        ok, _ = ac.perf.level_available(fl, *ac.speed_target(fl * 100.0), ac.mass)
        if not ok:
            break
        if hazard_ahead(world, ac, path, [fl, fl - 20]) is None:
            return fl
    return None


def heading_into_weather(world: "World", ac: "Aircraft", track: float, nm: float = 50.0) -> bool:
    """Pilot check of an ATC heading/direct: would it fly into hazardous weather soon?"""
    wx = world.env.weather
    if not wx.has_precip:
        return False
    r = math.radians(track)
    d = np.arange(2.0, nm + 1e-6, 1.5)
    xs, ys = ac.x + d * math.sin(r), ac.y + d * math.cos(r)
    for fl in _levels(ac):
        if pilot_hazard(wx, xs, ys, fl, world.t)[0].any():
            return True
    return False


# ----------------------------------------------------------------------------- per-step pilot logic
def update(world: "World", ac: "Aircraft") -> None:
    wx = world.env.weather
    st = ac.wx
    t = world.t
    period = SCAN_PERIOD_S if world.record else 3 * SCAN_PERIOD_S     # coarser inside look-ahead predictions
    reading_back = any(p["instruction"]["callsign"] == ac.callsign for p in world.pending)
    if wx.has_precip and t - st["last_scan"] >= period and not reading_back and not st.get("forced"):
        st["last_scan"] = t
        _weather_scan(world, ac)
    if ac.assumed and t - st["last_turb_check"] >= TURB_CHECK_S:
        dt = t - st["last_turb_check"] if st["last_turb_check"] > -1e8 else TURB_CHECK_S
        st["last_turb_check"] = t
        _turbulence(world, ac, min(dt, TURB_CHECK_S * 2))
    if ac.destination and ac.assumed and not reading_back:
        from . import arrivals
        arrivals.update(world, ac)


def _weather_scan(world: "World", ac: "Aircraft") -> None:
    st = ac.wx
    P = world.env.weather.pilot
    d = hazard_ahead(world, ac)
    st["hazard_ahead_nm"] = None if d is None else round(d, 1)
    if not ac.assumed:
        # still with the previous sector (or calling us, not yet assumed): its deviation is coordinated there, it simply flies it
        if d is not None and d <= P["request_range_nm"] and st["phase"] != "deviating" and getattr(world, "auto_wx", True):
            plan = plan_deviation(world, ac)
            if plan is not None:
                ac.start_wx_deviation(plan)
                st.update(phase="deviating", plan=plan)
                world._event("deviation_previous_sector", callsign=ac.callsign,
                             text=(f"{ac.callsign} (previous sector) deviating heading {plan['heading']:03d} "
                                   f"for {plan['distance_nm']} miles, then direct {plan['then_direct']}"), plan=plan)
        return
    req = world.requests.get(st["request_id"]) if st["request_id"] else None
    pending = req is not None and req.status == "pending"
    if st.get("scripted") and not pending:
        st["scripted"] = False                   # the scenario's request has been answered: back to normal
    if st["phase"] in ("requested",) and not pending and req is not None and req.status in ("amended", "approved"):
        st["phase"] = "clear"
    if d is None:
        if st["phase"] in ("requested", "refused") and pending and not st.get("scripted"):
            world.cancel_request(req, "no longer required")
        if st["phase"] != "deviating":
            st["phase"] = "clear"
        return
    # hazard ahead
    if st["phase"] == "clear" and d <= P["request_range_nm"]:
        _make_request(world, ac, d)
    elif st["phase"] in ("requested", "refused"):
        if pending and not st["repeated"] and world.t - st["requested_at"] >= REPEAT_AFTER_S:
            st["repeated"] = True
            world._event("request_repeated", request_id=req.id, callsign=ac.callsign,
                         text=f"{ac.callsign} say again, {req.phrase()[len(ac.callsign) + 1:]}, weather {int(d)} miles ahead")
        if d <= P["self_deviation_range_nm"]:
            _self_deviate(world, ac, d, req if pending else None)
    elif st["phase"] == "deviating" and d <= P["request_range_nm"]:
        # the weather moved onto the remaining deviation path: ask again (or deviate if it is already close)
        if d <= P["self_deviation_range_nm"]:
            _self_deviate(world, ac, d, None)
        else:
            _make_request(world, ac, d, further=True)


def _make_request(world: "World", ac: "Aircraft", d: float, further: bool = False) -> None:
    st = ac.wx
    if not world.record:
        # look-ahead prediction: no controller will answer, so the request only matters for its timing
        # (the pilot deviates on his own at the self-deviation range)
        st.update(phase="requested", request_id=None, requested_at=world.t, repeated=True, plan=None)
        return
    plan = plan_deviation(world, ac)
    climb = plan_climb_over(world, ac, d)
    if climb is not None and (plan is None or plan["extra_nm"] > 15.0):
        rid = world.add_dynamic_request(ac.callsign, "altitude", climb,
                                        f"to top weather {int(d)} miles ahead")
        st.update(phase="requested", request_id=rid, requested_at=world.t, repeated=False, plan=None)
        return
    if plan is not None and further and ac.wx_plan and plan["then_direct"] == ac.wx_plan.get("then_direct") \
            and abs(heading_diff(plan["heading"], ac.wx_plan["heading"])) <= 5:
        return                       # the deviation already being flown is still the right one
    if plan is None:
        world._event("pilot_report", callsign=ac.callsign,
                     text=f"{ac.callsign} weather ahead in {int(d)} miles, no way around found, advise")
        st.update(phase="requested", request_id=None, requested_at=world.t, repeated=True, plan=None)
        return
    reason = ("further deviation, weather moved onto our track" if further else
              f"weather {int(d)} miles ahead")
    rid = world.add_dynamic_request(ac.callsign, "weather_deviation", plan, reason)
    st.update(phase="requested", request_id=rid, requested_at=world.t, repeated=False, plan=plan)


def _self_deviate(world: "World", ac: "Aircraft", d: float, req) -> None:
    st = ac.wx
    if not getattr(world, "auto_wx", True):
        # pseudo-pilots only: the aircraft does not deviate by itself. A weather-deviation request still unanswered
        # when the weather is this close is a task the controller did not handle
        if req is not None and req.status == "pending" and world.record:
            world.cancel_request(req, "not answered before reaching the weather", status="expired")
            world.m["wx_requests_unanswered"].append({"callsign": ac.callsign, "request_id": req.id,
                                                      "time_s": round(world.t, 1), "weather_nm": round(d, 1)})
            world._event("request_unanswered", callsign=ac.callsign, request_id=req.id,
                         text=f"{ac.callsign}: weather-deviation request {req.id} not answered, weather {int(d)} NM ahead")
            st["phase"] = "refused"
        return
    if world.t - st.get("last_self_dev", -1e9) < 30.0:
        return
    st["last_self_dev"] = world.t
    plan = plan_deviation(world, ac)
    if plan is None:
        return
    ac.start_wx_deviation(plan)
    st.update(phase="deviating", plan=plan)
    st["self_deviations"] += 1
    if req is not None:
        world.cancel_request(req, "pilot deviating without clearance", status="expired")
    if world.record:
        world.m["wx_self_deviations"] += 1
    world._event("deviation_without_clearance", callsign=ac.callsign,
                 text=(f"{ac.callsign} deviating due weather, heading {plan['heading']:03d} for "
                       f"{plan['distance_nm']} miles, then direct {plan['then_direct']}"),
                 weather_nm=round(d, 1), plan=plan)


def _turbulence(world: "World", ac: "Aircraft", dt: float) -> None:
    wx = world.env.weather
    st = ac.wx
    fl = ac.alt_ft / 100.0
    e = float(wx.edr(ac.x, ac.y, fl, world.t))
    level = "severe" if e >= TURB_SEVERE else "moderate" if e >= TURB_MODERATE else "light" if e >= 0.12 else "smooth"
    if level != st["turb_level"] and level in ("moderate", "severe"):
        world._event("turbulence_report", callsign=ac.callsign, fl=int(round(fl)), severity=level, edr=round(e, 2),
                     text=f"{ac.callsign} {level} turbulence FL{int(round(fl))}")
    st["turb_level"] = level
    if world.record:
        if e >= TURB_SEVERE:
            world.m["turb_seconds"]["severe"] += dt
        elif e >= TURB_MODERATE:
            world.m["turb_seconds"]["moderate"] += dt
    st["turb_s"] = st["turb_s"] + dt if e >= TURB_MODERATE else 0.0
    if (st["turb_s"] >= TURB_PERSIST_S and world.t >= st["turb_cooldown_until"] and st["phase"] == "clear"
            and abs(ac.vs_fpm) < 100 and not world.has_pending_request(ac.callsign)):
        best = None
        r = math.radians(ac.trk)
        ahead = (ac.x + 20 * math.sin(r), ac.y + 20 * math.cos(r))
        cur = int(round(fl / 10.0)) * 10
        for dfl in (20, -20, 40, -40, 10, -10):
            cand = cur + dfl
            if not (world.lower_fl < cand <= world.upper_fl) or not pilot_level_ok(ac, cand):
                continue
            ok, _ = ac.perf.level_available(cand, *ac.speed_target(cand * 100.0), ac.mass) if dfl > 0 else (True, "")
            if not ok:
                continue
            ee = float(np.max(wx.edr(np.array([ac.x, ahead[0]]), np.array([ac.y, ahead[1]]), cand, world.t)))
            if ee < TURB_SMOOTH:
                best = cand
                break
        st["turb_cooldown_until"] = world.t + TURB_COOLDOWN_S
        if best is not None:
            rid = world.add_dynamic_request(ac.callsign, "altitude", best,
                                            f"{level} turbulence FL{cur}, smoother ride expected FL{best}")
            st["request_id"] = st["request_id"]  # weather-deviation bookkeeping unaffected
        else:
            world._event("pilot_report", callsign=ac.callsign,
                         text=f"{ac.callsign} continuous {level} turbulence FL{cur}, request smoother level when able")
