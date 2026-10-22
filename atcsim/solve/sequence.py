"""In-trail sequencing at a point: every aircraft at least S behind the one ahead, measured along the track, when it
crosses T (an exit, a merge point, an arrival fix), at levels that are not vertically separated.

How a controller does it, and how the planner does it:
  1. ORDER - first come, first served at T (by the time each would cross it flying as cleared now).
  2. TIMES - aircraft k must cross T no earlier than t_k = t_(k-1) + S_k / g_k, from the ASSIGNED time of the one
     ahead (so a delay given to one carries to the next); S_k = S plus a margin, plus the catch-up on any common track
     after T when k is faster.
  3. SPEED FIRST - when a speed within the aircraft's range (its own Mach -0.03 at most; none for a departure still
     climbing) can absorb the delay, held from the LATEST moment it still can, so not too early: the hold distance
     X = delay / (1/g' - 1/g). Except when the aircraft is gaining on the one ahead at the same level now: then at once.
  4. A FAN OF VECTORS otherwise - each aircraft on its sequencing ellipse (ellipse.py): heading psi for the time that
     puts it exactly S_k behind the one ahead at T, then direct T. All turns to the same side, each further than the one
     ahead (the step chosen as the smallest that keeps them separated from everyone), the later aircraft turned first;
     each resumes only after the one ahead has (from the front of the sequence), so that resuming it does not gain.
  5. CHECK - the whole plan flown in the trajectory model: exact closest approach of every trajectory against every
     other, and the in-trail spacing at T of each pair.

The result is a plan: timed clearances (now, or at a later moment for a speed), and what each one achieves.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import cpa, ellipse, exits, traj

H_SEQ = 2400.0
MARGIN_NM = 0.5
FAN_STEPS = (5, 10, 15, 20, 25, 30, 40)   # extra turn of each later aircraft over the one ahead, smallest first
FIRST_TURNS = (10, 15, 20, 25, 30, 40, 50)
MAX_TURN_BACK_DEG = 90.0   # at the end of a vector, the turn towards the point is at most this
MAX_VECTOR_S = 900.0       # a sequencing vector lasts at most 15 minutes: beyond, another solution (direct, level)


def members_of(w, T_name: str, only_assumed: bool = True) -> List[str]:
    out = []
    for cs, a in w.aircraft.items():
        if a.active and (a.assumed or not only_assumed) and T_name in traj.route_ahead(w, a):
            out.append(cs)
    return out


def merge_point(w, members: List[str], T_name: str) -> Optional[str]:
    """The first point that every member's route goes through, on the way to T_name."""
    routes = [traj.route_ahead(w, w.aircraft[cs]) for cs in members]
    routes = [r[:r.index(T_name) + 1] for r in routes if T_name in r]
    if not routes:
        return None
    for x in routes[0]:
        if all(x in r for r in routes[1:]):
            return x
    return T_name


def _route_dist(w, a_name: str, b_name: str) -> float:
    pa, pb = w.waypoints[a_name], w.waypoints[b_name]
    return math.hypot(pa[0] - pb[0], pa[1] - pb[1])


def _cross(Tr, p):
    c = exits.crossing(Tr, p)
    return None if c is None else c[0]


def plan(w, T_name: str, S: Optional[float] = None, members: Optional[List[str]] = None, shared_after_nm: float = 0.0,
         asked=None) -> Dict:
    """The sequencing plan at point T_name for the aircraft going there (members), or {"ok": False, "why": ...}."""
    from .resolve import departing, route_bearings, _angle
    S = float(S if S is not None else exits.required_nm(w))
    sh = float(w.sep_h)
    members = members or members_of(w, T_name)
    # where the routes join: the sequence is built there, the spacing kept on the common track to T_name
    merge = merge_point(w, members, T_name)
    checks = [T_name]                         # the spacing is checked where the routes join and at T itself, in the
    if merge and merge != T_name:             # trajectory model (the speeds do the catch-up arithmetic exactly)
        shared_after_nm += _route_dist(w, merge, T_name)
        checks = [merge, T_name]
        T_name = merge
    p = w.waypoints[T_name]
    check_pts = [w.waypoints[x] for x in checks]
    last_pt = check_pts[-1]
    base = traj.build_all(w, H_SEQ)
    cross = {cs: _cross(base[cs], p) for cs in members}
    seq = sorted([cs for cs in members if cross[cs] is not None], key=lambda c: cross[c])
    t_out = [c for c in (_cross(base[cs], last_pt) for cs in seq) if c is not None]
    CLEAR_UNTIL["t"] = (max(t_out) + 900.0) if t_out else None
    CLEAR_UNTIL["pt"] = last_pt
    if len(seq) < 2:
        return {"ok": True, "steps": [], "sequence": seq, "why": "nothing to sequence"}
    # aircraft at levels vertically separated at T from the one ahead need no in-trail spacing: groups by level at T
    planned = dict(base)                      # trajectories as the plan stands (others: as cleared now)
    steps, notes = [], []
    t_assigned = {seq[0]: cross[seq[0]]}
    fan_prev = None                           # (side, turn, resume time) of the last vectored aircraft
    for k in range(1, len(seq)):
        cs, ahead = seq[k], seq[k - 1]
        a = w.aircraft[cs]
        za = float(planned[cs].at(cross[cs])[2]) if cross[cs] is not None else a.alt_ft
        zb = float(planned[ahead].at(t_assigned[ahead])[2])
        if abs(za - zb) >= cpa._sv(za, zb, False, False) - 1:
            t_assigned[cs] = cross[cs]
            notes.append(f"{cs}: vertically separated from {ahead} at {T_name}")
            continue
        g = max(a.gs, 150.0)
        g_ahead = max(w.aircraft[ahead].gs, 150.0)
        S_k = S + MARGIN_NM
        t_need = t_assigned[ahead] + S_k / g * 3600.0
        delay = t_need - cross[cs]
        if delay <= 0:
            t_assigned[cs] = cross[cs]
            notes.append(f"{cs}: already {S:g} NM or more behind {ahead}")
            continue
        D = traj._along_to([a.pos] + [w.waypoints[x] for x in traj.route_ahead(w, a)], p) or math.hypot(p[0] - a.x, p[1] - a.y)
        # 3) speed: own Mach -0.03 at most, held from the latest moment that still absorbs the delay
        sol = None
        if not departing(w, a):
            mode, val = a.normal_speed()
            if mode == "mach":
                m_slow = round(val - 0.03, 2)
                g_slow = max(120.0, g + a.perf.tas_for("mach", m_slow, a.alt_ft) - a.tas)
                gain = 1.0 / g_slow - 1.0 / g                     # hours per NM held
                X = delay / 3600.0 / gain if gain > 0 else math.inf
                closing = (planned[ahead] is not None and g > g_ahead + 5)
                if X <= D * 0.95:
                    if closing:                                    # gaining on the one ahead now: at once, the least
                        for dm in (0.01, 0.02, 0.03):              # reduction that is enough over the whole way
                            m_try = round(val - dm, 2)
                            g_try = max(120.0, g + a.perf.tas_for("mach", m_try, a.alt_ft) - a.tas)
                            if D * (1.0 / g_try - 1.0 / g) * 3600.0 >= delay:
                                m_slow, X, start_s = m_try, D, 0.0
                                break
                        else:
                            start_s = 0.0
                    else:
                        start_s = max(0.0, (D - X) / g * 3600.0)
                    sp = f"M{int(round(m_slow * 100))}"
                    hold = (D / g) * 3600.0 + 60.0
                    Tr = traj.build(w, a, H_SEQ, speed=sp, speed_for_s=hold) if start_s < 1 else _speed_later(w, a, sp, start_s, hold)
                    spc = _trail(planned[ahead], Tr, check_pts)
                    if spc and spc["spacing_nm"] >= S - 0.2 and _clear(Tr, _ahead_only(planned, seq, k), cs, sh):
                        sol = {"kind": "speed", "traj": Tr, "steps": [{"at_s": w.t + start_s, "callsign": cs,
                               "action": {"type": "clear", "callsign": cs, "speed": sp, "limit": T_name},
                               "why": f"{cs} {sp} until {T_name}: {spc['spacing_nm']:.1f} NM behind {ahead}"
                                      + (" (gaining now: at once)" if start_s < 1 else f" (from T+{int((w.t + start_s) // 60):02d}:{int((w.t + start_s) % 60):02d}, the latest that still works)")}],
                               "spacing": spc["spacing_nm"]}
        # 4) the fan: same side, each further than the one ahead, the later turned first, resumed after the one ahead
        if sol is None:
            sol = _fan_member(w, a, cs, ahead, T_name, _ahead_only(planned, seq, k), t_assigned[ahead], S_k, fan_prev, sh,
                              planned[ahead], check_pts)
        if sol is None:
            return {"ok": False, "why": f"no speed or vector puts {cs} {S:g} NM behind {ahead} at {T_name}", "sequence": seq,
                    "steps": steps, "notes": notes}
        planned[cs] = sol["traj"]
        t_assigned[cs] = _cross(sol["traj"], p) or t_need
        steps += sol["steps"]
        if sol["kind"] == "fan":
            fan_prev = (sol["side"], sol["turn"], sol["resume_t"])
        notes.append(sol["steps"][0]["why"])
    # 5) the whole plan: everyone separated, each pair in trail at T
    final = []
    for k in range(1, len(seq)):
        spc = _trail(planned[seq[k - 1]], planned[seq[k]], check_pts)
        final.append({"ahead": seq[k - 1], "behind": seq[k], "spacing_nm": None if spc is None else round(spc["spacing_nm"], 1),
                      "where": None if spc is None else checks[spc["at"]]})
    worst = math.inf
    lim = max([c for c in (_cross(planned[x], last_pt) for x in seq) if c is not None] or [H_SEQ]) + 60.0
    for i, x in enumerate(seq):
        for o, To in planned.items():
            if o != x and (o not in seq or seq.index(o) > i):
                pc = cpa.pair(planned[x], To, sh)
                if pc.t_min is not None and pc.t_min <= lim:
                    worst = min(worst, pc.d_min)
                elif pc.t_first is not None and pc.t_first <= lim:
                    worst = min(worst, sh - 0.01)
    # the vectors are issued from the back of the sequence (the later aircraft turned first)
    now = [s_ for s_ in steps if s_["at_s"] <= w.t + 1]
    later = [s_ for s_ in steps if s_["at_s"] > w.t + 1]
    now.sort(key=lambda s_: -seq.index(s_["callsign"]))
    return {"ok": worst >= sh, "sequence": seq, "steps": now + sorted(later, key=lambda s_: s_["at_s"]), "spacing": final,
            "closest_nm": None if math.isinf(worst) else round(worst, 1), "notes": notes, "point": T_name, "S": S,
            "shared_after_nm": round(shared_after_nm, 1),
            "why": "" if worst >= sh else f"the plan leaves two aircraft {worst:.1f} NM apart"}


def _trail(T_lead, T_foll, pts) -> Optional[Dict]:
    """In-trail spacing over the check points: at each, the follower's distance to the point when the leader crosses
    it (negative if the follower crosses first); the smallest, and where."""
    worst = None
    for i, p in enumerate(pts):
        tl, tf = _cross(T_lead, p), _cross(T_foll, p)
        if tl is None or tf is None:
            continue
        if tf <= tl:
            d = -1.0
        else:
            x, y, _ = T_foll.at(tl)
            d = math.hypot(float(x) - p[0], float(y) - p[1])
        if worst is None or d < worst["spacing_nm"]:
            worst = {"spacing_nm": d, "at": i}
    return worst


def _ahead_only(planned: Dict, seq: List[str], k: int) -> Dict:
    """The trajectories an aircraft of the sequence is checked against: those ahead of it (planned) and the traffic
    outside the sequence - not those behind it, planned after it."""
    behind = set(seq[k + 1:])
    return {cs: T for cs, T in planned.items() if cs not in behind}


def _speed_later(w, a, sp: str, start_s: float, hold_s: float):
    """Trajectory with a speed given at start_s (s from now), held for hold_s."""
    from .. import atctools
    Tr0 = traj.build(w, a, H_SEQ)
    gs0 = a.gs
    tas1 = max(120.0, a.tas * atctools.speed_factor(a, sp))
    ent = dict(w.entries.get(a.callsign, {}))
    pts, hdg = atctools._path(w, a, ent, None, None)
    sched = [(0.0, a.tas), (start_s + traj.SPEED_DELAY_S, tas1), (start_s + traj.SPEED_DELAY_S + hold_s, traj.normal_tas(a, a.tas))]
    th, xh, yh = traj._horizontal(pts, sched, H_SEQ, hdg, tuple(a.wind_uv))
    return traj._merge(a.callsign, th, xh, yh, Tr0.t, Tr0.z, H_SEQ, Tr0.non_rvsm, Tr0.formation)


CLEAR_UNTIL = {"t": None}     # the check stops shortly after the sequence leaves the sector (the next unit's)


def _clear(Tr, planned: Dict, cs: str, sh: float) -> bool:
    lim = CLEAR_UNTIL["t"]
    if CLEAR_UNTIL.get("pt") is not None:              # until a minute after this aircraft leaves by the exit
        c_ = _cross(Tr, CLEAR_UNTIL["pt"])
        lim = None if c_ is None else c_ + 60.0
    for o, To in planned.items():
        if o == cs:
            continue
        pc = cpa.pair(Tr, To, sh)
        if lim is not None:
            if (pc.t_first is not None and pc.t_first <= lim) or (pc.d_min < sh + 1.0 and pc.t_min is not None and pc.t_min <= lim):
                return False
        elif pc.d_min < sh + 1.0:
            return False
    return True


def _fan_member(w, a, cs, ahead, T_name, planned, t_ahead, S_k, fan_prev, sh, T_lead=None, check_pts=None) -> Optional[Dict]:
    """The vector of one aircraft of the fan: on its ellipse (wind included), the time on the heading refined in the
    trajectory model to put it exactly S_k behind the one ahead at T; same side as the fan, a larger turn than the one
    ahead, resumed after it, separated from everyone, keeping its direction of flight."""
    from .resolve import route_bearings, _angle, MAX_OFF_ROUTE_DEG, MAX_OFF_EXIT_DEG
    p = w.waypoints[T_name]
    tas = a.tas if a.tas > 100 else max(a.gs, 150.0)
    wind = tuple(a.wind_uv)
    D = math.hypot(p[0] - a.x, p[1] - a.y)
    u = ((p[0] - a.x) / D, (p[1] - a.y) / D)
    hdg_T = traj.heading_for_track(tas, math.degrees(math.atan2(u[0], u[1])) % 360, wind)
    brg_r, brg_x = route_bearings(w, a)
    # the fan (same side, a larger turn than the one ahead) is for aircraft in the same stream; one converging from
    # another direction is turned whichever way diverges and keeps everyone separated
    b = w.aircraft[ahead]
    same_stream = (_angle(a.trk, b.trk) < 20 and traj.route_ahead(w, a)[:1] == traj.route_ahead(w, b)[:1])
    if fan_prev and same_stream:
        sides, turns = (fan_prev[0],), [fan_prev[1] + s_ for s_ in FAN_STEPS]
    else:
        sides, turns = (1, -1), list(FIRST_TURNS)
    cands = []
    for side in sides:
        for turn in turns:
            h = int(round(hdg_T + side * turn)) % 360 or 360
            trk_h = traj.air_to_ground(tas, h, wind)[0]
            if _angle(trk_h, brg_r) > MAX_OFF_ROUTE_DEG or _angle(trk_h, brg_x) > MAX_OFF_EXIT_DEG:
                continue
            geo = ellipse.with_wind(a.pos, p, t_ahead, S_k, tas, wind, h)     # the sequencing ellipse (wind included)
            if geo is None:
                continue
            ref = _refine_against(w, a, planned, cs, T_name, h, S_k, geo["time_s"], t_ahead, T_lead, check_pts)
            if ref is None or ref["time_s"] > MAX_VECTOR_S:
                continue
            Mx, My, _ = traj.build(w, a, H_SEQ, heading=h, for_s=ref["time_s"], then=T_name).at(ref["time_s"])
            back = _angle(trk_h, math.degrees(math.atan2(p[0] - float(Mx), p[1] - float(My))) % 360)
            if back > MAX_TURN_BACK_DEG:
                continue                                  # vectored past its point and turned back: not how it is done
            resume_t = w.t + ref["time_s"]
            if fan_prev and resume_t < fan_prev[2] + 30:
                continue                                  # resumes only after the one ahead (from the front)
            Tr = traj.build(w, a, H_SEQ, heading=h, for_s=ref["time_s"], then=T_name)
            if not _clear(Tr, {k_: v for k_, v in planned.items() if k_ != cs}, cs, sh):
                continue
            mins = max(1, int(round(ref["time_s"] / 60.0)))
            cands.append({"kind": "fan", "traj": Tr, "side": side, "turn": turn, "resume_t": resume_t, "spacing": ref["spacing_nm"],
                    "steps": [{"at_s": w.t, "callsign": cs,
                               "action": {"type": "clear", "callsign": cs, "heading": h, "limit": f"{ref['ground_nm']:.0f}NM", "then": T_name},
                               "why": (f"{cs} heading {h:03d} ({'right' if side > 0 else 'left'} {turn}°) for {ref['ground_nm']:.0f} NM "
                                       f"(about {mins} min), then direct {T_name}: {ref['spacing_nm']:.1f} NM behind {ahead}")}]})
            break                                     # the smallest turn that works on this side
    return min(cands, key=lambda c: c["turn"]) if cands else None


def _refine_against(w, a, planned, cs, T_name, h, S_k, t0_s, t_ahead, T_lead=None, check_pts=None) -> Optional[Dict]:
    """Secant steps on the time on the heading so that the aircraft crosses T S_k after the one ahead's assigned time
    there (S_k measured along its final leg): exact within the trajectory model (turns, delay, wind on each leg)."""
    p = w.waypoints[T_name]
    tas = a.tas if a.tas > 100 else max(a.gs, 150.0)

    def err(t):
        Tr = traj.build(w, a, H_SEQ, heading=h, for_s=max(30.0, t), then=T_name)
        c = _cross(Tr, p)
        if c is None:
            return None
        if T_lead is not None and check_pts:
            tr_ = _trail(T_lead, Tr, check_pts)
            return None if tr_ is None else tr_["spacing_nm"]
        # distance behind T when the one ahead crosses it = ground speed on the final leg * time difference
        x0, y0, _ = Tr.at(t_ahead)
        return math.hypot(float(x0) - p[0], float(y0) - p[1]) if c > t_ahead else -1.0
    ta, ea = t0_s, err(t0_s)
    if ea is None:
        return None
    tb = t0_s * 1.05 + 10.0
    eb = err(tb)
    for _ in range(6):
        if eb is None or abs(eb - ea) < 1e-6:
            break
        tn = max(30.0, tb + (S_k - eb) * (tb - ta) / (eb - ea))
        ta, ea, tb, eb = tb, eb, tn, err(tn)
        if eb is not None and abs(eb - S_k) < 0.05:
            break
    if eb is None or abs(eb - S_k) > 0.3:
        return None
    _, gs_h = traj.air_to_ground(tas, h, tuple(a.wind_uv))
    return {"time_s": tb, "ground_nm": gs_h * tb / 3600.0, "spacing_nm": eb}
