"""In-trail sequencing at a point: every aircraft at least S behind the one ahead, measured along the track, when it
crosses T (an exit, a merge point, an arrival fix), at levels that are not vertically separated.

How a controller does it, and how the planner does it:
  1. ORDER - first come, first served at T (by the time each would cross it flying as cleared now).
  2. TIMES - the spacing is measured when the one behind crosses T: the one ahead must then be S_k past it, so aircraft k
     crosses T no earlier than t_k = t_(k-1) + S_k / g_(k-1), g_(k-1) the ground speed of the one ahead after T, from
     the ASSIGNED time of the one ahead (so a delay given to one carries to the next); S_k = S plus a margin. The
     spacing is checked where the routes join and at T, in the trajectory model (the catch-up on a common track
     after the merge included).
  3. SPEED FIRST - when a speed within the aircraft's range (its own Mach -0.03 at most; none for a departure still
     climbing) can absorb the delay, held from the LATEST moment it still can, so not too early: the hold distance
     X = delay / (1/g' - 1/g). Except when the aircraft is gaining on the one ahead at the same level now: then at once.
  4. A FAN OF VECTORS otherwise - each aircraft on its sequencing ellipse (ellipse.py): heading psi for the time that
     puts it exactly S_k behind the one ahead at T, then direct T. The turn is not a fixed step: every heading (in 5
     degree units) on the ellipse is a solution with its own time on the heading; the one chosen is the cheapest by
     turn and time on the heading (a small turn held for long against a larger one held briefly: the delay still to
     absorb and the distance left decide), that keeps everyone separated. In a stream the turns are all to the same
     side, each larger than the one ahead, the later aircraft turned first; each resumes only after the one ahead has
     (from the front of the sequence), so that resuming it does not gain.
  4b. ARRIVALS (a sequencing fix with its letter of agreement: TERTO for GCRR, MARJA and SALCA for GMMN) - all at the
     LoA level at the fix, so in trail whatever their present levels. Each is cleared down when its separation is
     certain (after its speed or vector): at its top of descent if that is clear, else the earliest moment clear,
     the descent speed given with it (its Mach, then an IAS on conversion); traffic that is not descending and whose
     level the descent crosses is never moved: the descent is timed, its rate set ("at 2500 ft/min or greater" to be
     through first, "or less" to pass behind), or it is stopped above that level until clear.
  5. CHECK - the whole plan flown in the trajectory model: exact closest approach of every trajectory against every
     other, and the in-trail spacing at T of each pair.

The result is a plan: timed clearances (now, or at a later moment for a speed), and what each one achieves.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from . import cpa, ellipse, exits, traj

H_SEQ = 2400.0
MARGIN_NM = 0.5
HDG_UNIT = 5               # headings are given in 5 degree units
MAX_TURN_DEG = 70.0        # largest sequencing turn tried
TURN_PER_MIN = 4.0         # cost: one minute on a vector weighs as 4 degrees of turn (shorter vectors, less wind error)
MAX_REFINE = 10            # candidate headings refined in the trajectory model, cheapest first
CONVERSION_KT = 280        # descent speed after conversion given to arrivals (the same for all: the trail is kept)
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


def seqfix_of(w, T_name: str):
    """The sequencing fix (letter of agreement for arrivals) at T_name, or None."""
    return (getattr(w, "seq", None) or {}).get(T_name)


def arrivals_to(w, fix: str, only_assumed: bool = True) -> List[str]:
    """The arrivals going to a sequencing fix (still above its LoA level, or not yet across it)."""
    out = []
    for cs, a in w.aircraft.items():
        if (a.active and (a.assumed or not only_assumed) and a.destination and (a.arr or {}).get("fix") == fix
                and fix in traj.route_ahead(w, a)):
            out.append(cs)
    return out


def _gs_at(Tr, t: float) -> float:
    """Ground speed (kt) of a trajectory about time t."""
    t2 = min(float(t) + 30.0, float(Tr.t[-1]))
    t1 = max(t2 - 60.0, float(Tr.t[0]))
    if t2 - t1 < 1.0:
        return 400.0
    x1, y1, _ = Tr.at(t1)
    x2, y2, _ = Tr.at(t2)
    return max(120.0, math.hypot(float(x2 - x1), float(y2 - y1)) / (t2 - t1) * 3600.0)


def plan(w, T_name: str, S: Optional[float] = None, members: Optional[List[str]] = None, shared_after_nm: float = 0.0,
         asked=None, use_merge: bool = True, descents: Optional[bool] = None) -> Dict:
    """The sequencing plan at T_name: built where the routes join (spacing there and at T_name); when that is no longer
    possible (too close to the merge), at T_name alone, the vectors then direct to T_name (past the merge point:
    separated there, the agreed spacing only at T_name)."""
    sq = seqfix_of(w, T_name)
    if sq is not None and (descents is None or descents):
        DESCEND.update(fix=T_name, fl=int(sq.cross_at_fl or sq.cross_at_or_below_fl or sq.min_fl))
    try:
        P = _plan(w, T_name, S, members, shared_after_nm, asked, use_merge, descents)
        if not P.get("ok") and use_merge and P.get("point") not in (None, T_name):
            P2 = _plan(w, T_name, S, members, shared_after_nm, asked, False, descents)
        else:
            P2 = None
    finally:
        DESCEND.update(fix=None, fl=None)
    if P2 is not None:
        if P2.get("ok"):
            P2["notes"] = [f"too close to {P['point']} to sequence there: vectors direct {T_name}"] + P2.get("notes", [])
            return P2
    return P


def _plan(w, T_name: str, S: Optional[float] = None, members: Optional[List[str]] = None, shared_after_nm: float = 0.0,
          asked=None, use_merge: bool = True, descents: Optional[bool] = None) -> Dict:
    """The sequencing plan at point T_name for the aircraft going there (members), or {"ok": False, "why": ...}.
    At a sequencing fix (arrivals) the spacing is that of its letter of agreement, whatever the levels, and the
    descents to the LoA level are planned as well (descents=None: when T_name is a sequencing fix)."""
    from .resolve import departing
    sq = seqfix_of(w, T_name)
    arrival = sq is not None
    if descents is None:
        descents = arrival
    S = float(S if S is not None else (sq.spacing_nm if arrival else exits.required_nm(w)))
    sh = float(w.sep_h)
    members = members or (arrivals_to(w, T_name) if arrival else members_of(w, T_name))
    fix_name = T_name
    # where the routes join: the sequence is built there, the spacing kept on the common track to T_name
    merge = merge_point(w, members, T_name) if use_merge else T_name
    checks = [T_name]                         # the spacing is checked where the routes join and at T itself, in the
    if merge and merge != T_name:             # trajectory model (the speeds do the catch-up arithmetic exactly)
        shared_after_nm += _route_dist(w, merge, T_name)
        checks = [merge, T_name]
        T_name = merge
    p = w.waypoints[T_name]
    check_pts = [w.waypoints[x] for x in checks]
    last_pt = check_pts[-1]
    base = traj.build_all(w, H_SEQ)
    for cs in members:                        # an aircraft not yet active (the tool on any traffic): as it is
        if cs not in base and cs in w.aircraft:
            base[cs] = traj.build(w, w.aircraft[cs], H_SEQ)
    level = dict(base)                        # arrivals: their trajectories without the descent (base: with it)
    for cs in members:
        if cs in base:
            base[cs] = _adj(w, w.aircraft[cs], base[cs])
    cross = {cs: _cross(base[cs], p) for cs in members if cs in base}
    seq = sorted([cs for cs in cross if cross[cs] is not None], key=lambda c: cross[c])
    t_out = [c for c in (_cross(base[cs], last_pt) for cs in seq) if c is not None]
    CLEAR_UNTIL["t"] = (max(t_out) + 900.0) if t_out else None
    CLEAR_UNTIL["pt"] = last_pt
    steps, notes, targets = [], [], {}
    planned = dict(base)                      # trajectories as the plan stands (others: as cleared now)
    if len(seq) < 2 and not (descents and seq):
        return {"ok": True, "steps": [], "sequence": seq, "why": "nothing to sequence", "point": T_name, "S": S,
                "targets": {}, "spacing": [], "notes": [], "fix": fix_name}
    t_assigned = {seq[0]: cross[seq[0]]}
    fan_prev = None                           # (side, turn, resume time) of the last vectored aircraft
    for k in range(1, len(seq)):
        cs, ahead = seq[k], seq[k - 1]
        a = w.aircraft[cs]
        za = float(planned[cs].at(cross[cs])[2]) if cross[cs] is not None else a.alt_ft
        zb = float(planned[ahead].at(t_assigned[ahead])[2])
        if not arrival and abs(za - zb) >= cpa._sv(za, zb, False, False) - 1:
            t_assigned[cs] = cross[cs]
            notes.append(f"{cs}: vertically separated from {ahead} at {T_name}")
            continue
        g = max(a.gs, 150.0)
        g_lead = _gs_at(planned[ahead], t_assigned[ahead])     # the one ahead's ground speed after T
        S_k = S + MARGIN_NM
        t_need = t_assigned[ahead] + S_k / g_lead * 3600.0     # S_k past T when this one crosses it
        targets[cs] = {"ahead": ahead, "t_ahead_s": t_assigned[ahead], "S_nm": S_k, "g_lead_kt": g_lead}
        delay = t_need - cross[cs]
        spc0 = _trail(planned[ahead], planned[cs], check_pts)
        if (spc0 is not None and spc0["spacing_nm"] >= S + 0.2) or (spc0 is None and delay <= 0):
            t_assigned[cs] = cross[cs]
            notes.append(f"{cs}: already {S:g} NM or more behind {ahead}"
                         + (f" ({spc0['spacing_nm']:.1f} NM)" if spc0 else ""))
            continue
        D = traj._along_to([a.pos] + [w.waypoints[x] for x in traj.route_ahead(w, a)], p) or math.hypot(p[0] - a.x, p[1] - a.y)
        # 3) speed: own Mach -0.03 at most, held from the latest moment that still absorbs the delay
        sol = None
        if not departing(w, a) and delay > 0:
            mode, val = a.normal_speed()
            if mode == "mach":
                m_slow = round(val - 0.03, 2)
                g_slow = max(120.0, g + a.perf.tas_for("mach", m_slow, a.alt_ft) - a.tas)
                gain = 1.0 / g_slow - 1.0 / g                     # hours per NM held
                X = delay / 3600.0 / gain if gain > 0 else math.inf
                g_ahead_now = max(w.aircraft[ahead].gs, 150.0)
                closing = g > g_ahead_now + 5
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
                    Tr_l = traj.build(w, a, H_SEQ, speed=sp, speed_for_s=hold) if start_s < 1 else _speed_later(w, a, sp, start_s, hold)
                    Tr = _adj(w, a, Tr_l, mach=m_slow)
                    spc = _trail(planned[ahead], Tr, check_pts)
                    if spc and spc["spacing_nm"] >= S - 0.2 and _clear(Tr, _ahead_only(planned, seq, k), cs, sh):
                        sol = {"kind": "speed", "traj": Tr, "level": Tr_l, "mach": m_slow, "steps": [{"at_s": w.t + start_s, "callsign": cs,
                               "action": {"type": "clear", "callsign": cs, "speed": sp, "limit": T_name},
                               "why": f"{cs} {sp} until {T_name}: {spc['spacing_nm']:.1f} NM behind {ahead}"
                                      + (" (gaining now: at once)" if start_s < 1 else f" (from T+{int((w.t + start_s) // 60):02d}:{int((w.t + start_s) % 60):02d}, the latest that still works)")}],
                               "spacing": spc["spacing_nm"]}
        # 4) the fan: same side, each further than the one ahead, the later turned first, resumed after the one ahead
        if sol is None:
            sol = _fan_member(w, a, cs, ahead, T_name, _ahead_only(planned, seq, k), t_assigned[ahead], S_k, fan_prev, sh,
                              planned[ahead], check_pts, g_lead)
        if sol is None:
            return {"ok": False, "why": f"no speed or vector puts {cs} {S:g} NM behind {ahead} at {T_name}", "sequence": seq,
                    "steps": steps, "notes": notes, "point": T_name, "S": S, "targets": targets, "spacing": [], "fix": fix_name}
        planned[cs] = sol["traj"]
        level[cs] = sol.get("level", sol["traj"])
        targets[cs]["solution"] = sol["kind"]
        if sol["kind"] == "fan":
            targets[cs].update(heading=sol["heading"], time_s=sol["time_s"])
        t_assigned[cs] = _cross(sol["traj"], p) or t_need
        steps += sol["steps"]
        if sol["kind"] == "fan":
            fan_prev = (sol["side"], sol["turn"], sol["resume_t"])
        notes.append(sol["steps"][0]["why"])
    # 4b) arrivals: the descents to the LoA level, each once its separation is certain, crossing the levels of the
    # traffic that stays where it is without moving it
    if descents:
        fl_loa = int((sq.cross_at_fl or sq.cross_at_or_below_fl or sq.min_fl) if sq else 250)
        for k, cs in enumerate(seq):
            a = w.aircraft[cs]
            if a.cleared_alt_ft <= fl_loa * 100 + 50:
                continue
            mach = None
            for st_ in steps:
                if st_["callsign"] == cs and str(st_["action"].get("speed", "")).startswith("M"):
                    mach = int(str(st_["action"]["speed"])[1:3]) / 100.0
            trail = (planned[seq[k - 1]], check_pts, S) if k > 0 else None
            d = _descent_member(w, a, cs, level[cs], _ahead_only(planned, seq, k), fl_loa, fix_name, sh, mach, trail)
            if d is None:
                notes.append(f"{cs}: no descent to FL{fl_loa} found clear of the traffic: keep it level, coordinate")
                continue
            planned[cs] = d["traj"]
            steps += d["steps"]
            notes += [s_["why"] for s_ in d["steps"]]
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
    now.sort(key=lambda s_: (-seq.index(s_["callsign"]), "cfl" in s_["action"]))
    return {"ok": worst >= sh, "sequence": seq, "steps": now + sorted(later, key=lambda s_: s_["at_s"]), "spacing": final,
            "closest_nm": None if math.isinf(worst) else round(worst, 1), "notes": notes, "point": T_name, "S": S,
            "shared_after_nm": round(shared_after_nm, 1), "targets": targets, "fix": fix_name, "arrival": arrival,
            "trajs": {cs: planned[cs] for cs in seq},
            "why": "" if worst >= sh else f"the plan leaves two aircraft {worst:.1f} NM apart"}


def _trail(T_lead, T_foll, pts) -> Optional[Dict]:
    """In-trail spacing over the check points: at each, the distance of the one ahead from the point when the one
    behind crosses it (negative if the one behind crosses first); the smallest, and where."""
    worst = None
    for i, p in enumerate(pts):
        tl, tf = _cross(T_lead, p), _cross(T_foll, p)
        if tl is None or tf is None:
            continue
        if tf <= tl:
            d = -1.0
        else:
            x, y, _ = T_lead.at(tf)
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


DESCEND = {"fix": None, "fl": None}   # arrivals being planned: their trajectories with the planned descent


def _nominal_t(a, Tr, fix_pt, fl) -> Optional[float]:
    """When an arrival is planned to start down to fl (s from now): early enough for its idle descent (with a margin)
    to have it level at the fix - the same distance before the fix for all, so that the trail is kept."""
    t_fix = _cross(Tr, fix_pt)
    dz = a.alt_ft - fl * 100.0
    if t_fix is None or dz <= 100 or a.cleared_alt_ft <= fl * 100 + 50:
        return None
    try:
        r = max(1200.0, 0.8 * float(a.descent_rate_idle()))
    except Exception:  # noqa: BLE001
        r = 1800.0
    return max(0.0, t_fix - dz / r * 60.0 - 60.0)


def _adj(w, a, Tr, mach: Optional[float] = None):
    """An arrival's trajectory with its planned descent (when arrivals are being planned)."""
    fix = DESCEND["fix"]
    if fix is None or not a.destination or (a.arr or {}).get("fix") != fix:
        return Tr
    t = _nominal_t(a, Tr, w.waypoints[fix], DESCEND["fl"])
    return Tr if t is None else _with_descent(w, a, Tr, DESCEND["fl"], t, mach=mach)


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


def _fan_member(w, a, cs, ahead, T_name, planned, t_ahead, S_k, fan_prev, sh, T_lead=None, check_pts=None,
                g_lead=None) -> Optional[Dict]:
    """The vector of one aircraft of the fan: on its ellipse (wind included), the time on the heading refined in the
    trajectory model to put it exactly S_k behind the one ahead at T (measured when it crosses T); same side as the
    fan and a larger turn than the one ahead in a stream, resumed after it, separated from everyone, keeping its
    direction of flight. The turn is the situation's: every heading on the ellipse is a solution, the cheapest by
    turn and time on the heading that passes every check is taken."""
    cands = fan_candidates(w, a, cs, ahead, T_name, t_ahead, S_k, fan_prev, g_lead)
    p = w.waypoints[T_name]
    tried = 0
    for c in cands:
        if tried >= MAX_REFINE:
            break
        tried += 1
        h, side, turn = c["heading"], c["side"], c["turn"]
        ref = _refine_against(w, a, planned, cs, T_name, h, S_k, c["time_s"], t_ahead, T_lead, check_pts)
        if ref is None or ref["time_s"] > MAX_VECTOR_S:
            continue
        Tr_l = traj.build(w, a, H_SEQ, heading=h, for_s=ref["time_s"], then=T_name)
        Tr = _adj(w, a, Tr_l)
        Mx, My, _ = Tr_l.at(ref["time_s"])
        from .resolve import _angle
        if _angle(c["track"], math.degrees(math.atan2(p[0] - float(Mx), p[1] - float(My))) % 360) > MAX_TURN_BACK_DEG:
            continue                                  # vectored past its point and turned back: not how it is done
        resume_t = w.t + ref["time_s"]
        if fan_prev and c["same_stream"] and resume_t < fan_prev[2] + 30:
            continue                                  # resumes only after the one ahead (from the front)
        if not _inside(w, Tr, _cross(Tr, p)):
            continue                                  # the vector would take it out of the sector
        if not _clear(Tr, {k_: v for k_, v in planned.items() if k_ != cs}, cs, sh):
            continue
        mins = max(1, int(round(ref["time_s"] / 60.0)))
        return {"kind": "fan", "traj": Tr, "level": Tr_l, "side": side, "turn": turn, "resume_t": resume_t, "spacing": ref["spacing_nm"],
                "heading": h, "time_s": ref["time_s"],
                "steps": [{"at_s": w.t, "callsign": cs,
                           "action": {"type": "clear", "callsign": cs, "heading": h, "limit": f"{ref['ground_nm']:.0f}NM", "then": T_name},
                           "why": (f"{cs} heading {h:03d} ({'right' if side > 0 else 'left'} {turn}°) for {ref['ground_nm']:.0f} NM "
                                   f"(about {mins} min), then direct {T_name}: {ref['spacing_nm']:.1f} NM behind {ahead}")}]}
    return None


def _inside(w, Tr, t_end: Optional[float], margin_s: float = 45.0) -> bool:
    """The trajectory stays in the controlled airspace (from when it is in it) until t_end (its crossing of the
    sequencing point, which may be on the boundary)."""
    asp = getattr(getattr(w, "env", None), "airspace", None)
    if asp is None or t_end is None:
        return True
    import numpy as np
    seen = False
    for t in np.arange(0.0, max(0.0, t_end - margin_s), 20.0):
        x, y, _ = Tr.at(t)
        ins = asp.inside((float(x), float(y)))
        if ins:
            seen = True
        elif seen:
            return False
    return True


def fan_candidates(w, a, cs, ahead, T_name, t_ahead, S_k, fan_prev=None, g_lead=None, all_headings=False) -> List[Dict]:
    """Every heading (5 degree units) that puts the aircraft S_k behind the one ahead at T on its sequencing ellipse
    (closed form, wind included), within the direction-of-flight rules and the 15 minute limit: the assignable
    headings, cheapest first (turn + TURN_PER_MIN per minute on the heading). In a stream behind a vectored aircraft:
    its side, and a larger turn. all_headings: every heading of the ellipse with "ok" and why not (for display)."""
    from .resolve import route_bearings, _angle, MAX_OFF_ROUTE_DEG, MAX_OFF_EXIT_DEG
    p = w.waypoints[T_name]
    tas = a.tas if a.tas > 100 else max(a.gs, 150.0)
    wind = tuple(a.wind_uv)
    D = math.hypot(p[0] - a.x, p[1] - a.y)
    if D < 1e-6:
        return []
    u = ((p[0] - a.x) / D, (p[1] - a.y) / D)
    hdg_T = traj.heading_for_track(tas, math.degrees(math.atan2(u[0], u[1])) % 360, wind)
    brg_r, brg_x = route_bearings(w, a)
    b = w.aircraft.get(ahead)
    same_stream = bool(b is not None and _angle(a.trk, b.trk) < 20
                       and traj.route_ahead(w, a)[:1] == traj.route_ahead(w, b)[:1])
    fan = fan_prev if (fan_prev and same_stream) else None
    out = []
    for hh in range(0, 360, HDG_UNIT):
        h = hh or 360
        dlt = (h - hdg_T + 180) % 360 - 180
        side, turn = (1 if dlt >= 0 else -1), int(round(abs(dlt)))
        why = None
        if turn < 3 or turn > MAX_TURN_DEG:
            why = "turn"
        elif fan and (side != fan[0] or turn < fan[1] + HDG_UNIT):
            why = "fan"                                   # same side as the one ahead, a larger turn
        trk_h, _ = traj.air_to_ground(tas, h, wind)
        if why is None and (_angle(trk_h, brg_r) > MAX_OFF_ROUTE_DEG or _angle(trk_h, brg_x) > MAX_OFF_EXIT_DEG):
            why = "direction"
        geo = ellipse.with_wind(a.pos, p, t_ahead, S_k, tas, wind, h, g_lead=g_lead)
        if geo is None:
            why = why or "ellipse"
        elif geo["time_s"] > MAX_VECTOR_S:
            why = why or "too long"
        if why and not all_headings:
            continue
        c = {"heading": h, "side": side, "turn": turn, "track": trk_h, "same_stream": same_stream, "ok": why is None,
             "why_not": why}
        if geo is not None:
            # already on a vector: the change from its present heading is what it costs (a new plan keeps it if it can)
            chg = _angle(h, a.hdg) if a.nav_mode == "heading" else turn
            c.update(time_s=geo["time_s"], leg_nm=geo["ground_nm"], turn_point=tuple(map(float, geo["turn_point"])),
                     cost=chg + TURN_PER_MIN * geo["time_s"] / 60.0)
        out.append(c)
    out.sort(key=lambda c: (not c["ok"], c.get("cost", 1e9)))
    return out


def _refine_against(w, a, planned, cs, T_name, h, S_k, t0_s, t_ahead, T_lead=None, check_pts=None) -> Optional[Dict]:
    """Secant steps on the time on the heading so that the one ahead is S_k past T when this one crosses it (and
    likewise at the other check points): exact within the trajectory model (turns, delay, wind on each leg)."""
    p = w.waypoints[T_name]
    tas = a.tas if a.tas > 100 else max(a.gs, 150.0)

    def err(t):
        Tr = _adj(w, a, traj.build(w, a, H_SEQ, heading=h, for_s=max(30.0, t), then=T_name))
        c = _cross(Tr, p)
        if c is None:
            return None
        if T_lead is not None and check_pts:
            tr_ = _trail(T_lead, Tr, check_pts)
            return None if tr_ is None else tr_["spacing_nm"]
        return (c - t_ahead) * _gs_at(Tr, c) / 3600.0 if c > t_ahead else -1.0
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


# ----------------------------------------------------------------------------- arrivals: the descents
def _with_descent(w, a, Tr, fl: int, t_d: float, rate: Optional[float] = None, rates: str = "expected",
                  stop_fl: Optional[int] = None, t_d2: Optional[float] = None, mach: Optional[float] = None):
    """Trajectory Tr (its lateral part kept) with a descent to fl given t_d s from now (at a fixed rate when given,
    else the aircraft's own, or its fast / slow bound); stop_fl: first to stop_fl at t_d, then to fl at t_d2."""
    import numpy as np
    H = float(Tr.t[-1])
    d0 = t_d + traj.PILOT_DELAY_S
    if stop_fl is None:
        tv, zv = traj._vertical(a.alt_ft, fl * 100.0, 0.0, d0, H, a, rates, fixed_rate=rate)
    else:
        tv1, zv1 = traj._vertical(a.alt_ft, stop_fl * 100.0, 0.0, d0, H, a, rates, fixed_rate=rate)
        z_at = float(np.interp(t_d2, tv1, zv1))
        tv2, zv2 = traj._vertical(z_at, fl * 100.0, 0.0, traj.PILOT_DELAY_S, H - t_d2, a, rates, fixed_rate=rate)
        tv = [t_ for t_ in tv1 if t_ < t_d2] + [t_d2 + t_ for t_ in tv2]
        zv = [z_ for t_, z_ in zip(tv1, zv1) if t_ < t_d2] + list(zv2)
    # the speed in the descent: its Mach, then the IAS on conversion - the true airspeed falls as it goes down, so the
    # lateral path is flown more slowly (the trail behind an aircraft that descended earlier shrinks)
    tg = np.arange(0.0, H + 1e-6, 5.0)
    X, Y, _ = Tr.at(tg)
    Z = np.interp(tg, tv, zv)
    m = mach
    if m is None:
        mode, val = a.normal_speed()
        m = val if mode == "mach" else None
    try:
        def tas_d(z):
            v = a.perf.tas_for("cas", CONVERSION_KT, z)
            return min(v, a.perf.tas_for("mach", m, z)) if m else v
        top = tas_d(a.alt_ft)
        f = np.array([1.0 if z > a.alt_ft - 50 else tas_d(float(z)) / top for z in Z])
    except Exception:  # noqa: BLE001
        f = np.ones_like(tg)
    seg = np.hypot(np.diff(X), np.diff(Y))
    s_old = np.concatenate([[0.0], np.cumsum(seg)])
    s_new = np.concatenate([[0.0], np.cumsum(seg * 0.5 * (f[1:] + f[:-1]))])
    Xn, Yn = np.interp(s_new, s_old, X), np.interp(s_new, s_old, Y)
    return traj._merge(a.callsign, tg, Xn, Yn, tv, zv, H, Tr.non_rvsm, Tr.formation)


def _descent_ok(w, a, Tr, fl, t_d, t_fix, others, cs, sh, rate=None, stop_fl=None, t_d2=None, mach=None,
                trail=None) -> bool:
    """Clear of everyone with the aircraft's fast and slow rates (or the rate given), at fl by the fix, and still the
    spacing behind the one ahead (trail = (its trajectory, check points, S)): slower in the descent, it must not
    descend later than the one ahead did."""
    for rr in ((None,) if rate else ("fast", "slow")):
        Td = _with_descent(w, a, Tr, fl, t_d, rate=rate, rates=rr or "expected", stop_fl=stop_fl, t_d2=t_d2, mach=mach)
        tf = _cross(Td, w.waypoints[(a.arr or {}).get("fix")]) if (a.arr or {}).get("fix") in w.waypoints else t_fix
        if tf is not None and float(Td.at(tf)[2]) > fl * 100 + 200:
            return False
        if trail is not None:
            spc = _trail(trail[0], Td, trail[1])
            if spc is not None and spc["spacing_nm"] < trail[2] - 0.1:
                return False
        if not _clear(Td, others, cs, sh):
            return False
    return True


def _descent_member(w, a, cs, Tr, others: Dict, fl: int, fix: str, sh: float, mach: Optional[float] = None,
                    trail=None) -> Optional[Dict]:
    """The descent of one arrival to the LoA level fl, once its separation is certain: at its top of descent when that
    is clear of everyone (the traffic that stays at its level is never moved), else the earliest moment clear, else
    with a rate (through the other's level first, or behind it), else stopped above the level in the way and
    continued once clear. The descent speed given with it: its Mach (the sequencing one if any) then an IAS on
    conversion, the same for every arrival so that the trail is kept."""
    p = w.waypoints[fix]
    t_fix = _cross(Tr, p)
    dz = a.alt_ft - fl * 100.0
    t_nom = _nominal_t(a, Tr, p, fl)
    if t_nom is None:
        t_nom = 0.0
    try:
        r_fast = 1.3 * float(a.descent_rate_idle())
    except Exception:  # noqa: BLE001
        r_fast = 2500.0
    t_late = max(0.0, (t_fix if t_fix is not None else 0.0) - dz / max(r_fast, 1500.0) * 60.0 - 30.0)
    mode, val = a.normal_speed()
    m = mach if mach is not None else (val if mode == "mach" else None)
    sp = f"M{int(round(m * 100))}/K{CONVERSION_KT}" if m else f"K{CONVERSION_KT}"
    fixt = None if t_fix is None else t_fix

    def step(t_d, extra=None, text=""):
        act = {"type": "clear", "callsign": cs, "cfl": fl, "speed": sp, "limit": fix}
        act.update(extra or {})
        act = {k_: v_ for k_, v_ in act.items() if v_ is not None}
        return {"at_s": w.t + t_d, "callsign": cs, "action": act, "why": text}
    # preferred: the planned descent point (the same distance before the fix for every arrival: the trail is kept);
    # then earlier (its separation is certain: nothing gained by waiting), then later
    t_tod = t_nom
    times = [t_tod] + [t_tod - 60.0 * k for k in range(1, 30) if t_tod - 60.0 * k >= 0.0] \
        + [t_tod + 60.0 * k for k in range(1, 30) if t_tod + 60.0 * k <= t_late]
    phr = (f"M.{int(round(m * 100))} then {CONVERSION_KT} kt on conversion" if m else f"{CONVERSION_KT} kt on conversion")
    # what is in the way at the planned point (traffic that is left as it is)
    Tn = _with_descent(w, a, Tr, fl, t_nom, mach=m)
    way = [o for o, To in others.items() if o != cs and not _clear(Tn, {o: To}, cs, sh)]
    because = (" (" + ", ".join(f"{o} FL{int(round(float(others[o].at(0.0)[2]) / 100))}" for o in way[:2])
               + " in the way at its planned descent point, left as it is)") if way else ""
    for t_d in times:
        if _descent_ok(w, a, Tr, fl, t_d, fixt, others, cs, sh, mach=m, trail=trail):
            when = "now" if t_d < 1 else f"at T+{int((w.t + t_d) // 60):02d}:{int((w.t + t_d) % 60):02d}"
            ref_ = "its planned descent point"
            tag = ref_ if abs(t_d - t_tod) < 1 else (f"{int(round((t_tod - t_d) / 60))} min before {ref_}" if t_d < t_tod
                                                    else f"{int(round((t_d - t_tod) / 60))} min after {ref_}")
            return {"traj": _with_descent(w, a, Tr, fl, t_d, mach=m), "steps": [step(t_d, None,
                    f"{cs} descend FL{fl} {when} ({tag}), {phr}, level by {fix}" + ("" if abs(t_d - t_tod) < 1 else because))]}
    # a rate: through the level in the way first (or greater), or behind (or less)
    try:
        fast = int(min(3000.0, 1.4 * a.descent_rate_idle()) // 500 * 500)
    except Exception:  # noqa: BLE001
        fast = 2500
    for t_d in sorted(times):
        for rv, rm in ((fast, "min"), (1000, "max")):
            if rv < 1000 or (rm == "max" and dz / rv * 60.0 > ((fixt - t_d) if fixt else 1e9)):
                continue
            if _descent_ok(w, a, Tr, fl, t_d, fixt, others, cs, sh, rate=float(rv), mach=m, trail=trail):
                when = "now" if t_d < 1 else f"at T+{int((w.t + t_d) // 60):02d}:{int((w.t + t_d) % 60):02d}"
                return {"traj": _with_descent(w, a, Tr, fl, t_d, rate=float(rv), mach=m), "steps": [step(t_d, {"rate": f"{rv}{'+' if rm == 'min' else '-'}"},
                        f"{cs} descend FL{fl} {when} at {rv} ft/min or {'greater' if rm == 'min' else 'less'}, {phr}, level by {fix}{because}")]}
    # stopped above the level in the way, the rest once clear of it
    blockers = sorted({int(round(float(To.at(0.0)[2]) / 100.0)) for o, To in others.items() if o != cs
                       and fl * 100 < float(To.at(0.0)[2]) < a.alt_ft - 500}, reverse=True)
    for lb in blockers:
        stop = lb + 10
        if stop * 100 >= a.alt_ft - 300:
            continue
        for t_d in sorted(times)[:6]:
            for t_d2 in [t_d + 60.0 * k for k in range(2, 25)]:
                if fixt is not None and t_d2 > fixt - 60:
                    break
                if _descent_ok(w, a, Tr, fl, t_d, fixt, others, cs, sh, stop_fl=stop, t_d2=t_d2, mach=m, trail=trail):
                    w1 = "now" if t_d < 1 else f"at T+{int((w.t + t_d) // 60):02d}:{int((w.t + t_d) % 60):02d}"
                    w2 = f"T+{int((w.t + t_d2) // 60):02d}:{int((w.t + t_d2) % 60):02d}"
                    s1 = step(t_d, {"cfl": stop}, f"{cs} descend FL{stop} {w1}, {phr} (FL{lb} traffic below, left as it is)")
                    s2 = step(t_d2, {"speed": None, "limit": None}, f"{cs} descend FL{fl} at {w2}, once clear of the FL{lb} traffic, level by {fix}")
                    return {"traj": _with_descent(w, a, Tr, fl, t_d, stop_fl=stop, t_d2=t_d2, mach=m), "steps": [s1, s2]}
    return None


def descent_now(w, cs: str, fix: str, mach: Optional[float] = None) -> Optional[Dict]:
    """The descent of an arrival re-planned against the traffic as it is now (just before it is given: traffic that
    was not there when the plan was made is taken into account)."""
    sq = seqfix_of(w, fix)
    a = w.aircraft.get(cs)
    if sq is None or a is None or not a.active:
        return None
    fl = int(sq.cross_at_fl or sq.cross_at_or_below_fl or sq.min_fl)
    if a.cleared_alt_ft <= fl * 100 + 50:
        return None
    allT = traj.build_all(w, H_SEQ)
    p = w.waypoints[fix]
    CLEAR_UNTIL["t"], CLEAR_UNTIL["pt"] = None, p
    t_me = _cross(allT[cs], p)
    ahead = [(c_, _cross(allT[c_], p)) for c_ in arrivals_to(w, fix, only_assumed=False) if c_ != cs and c_ in allT]
    ahead = [x for x in ahead if x[1] is not None and t_me is not None and x[1] < t_me]
    trail = None
    if ahead:
        lead = max(ahead, key=lambda x: x[1])[0]
        trail = (allT[lead], [p], float(sq.spacing_nm))
    return _descent_member(w, a, cs, allT[cs], {k: v for k, v in allT.items() if k != cs}, fl, fix, float(w.sep_h), mach,
                           trail)


# ----------------------------------------------------------------------------- the controller's tool
def tool(w, callsigns: List[str], point: str, S: Optional[float] = None) -> Dict:
    """For the controller on the radar: the aircraft chosen, sequenced in trail at the point chosen (first come,
    first served), each follower with its sequencing ellipse (the turn points that put it exactly S behind the one
    ahead, wind included) and every heading on it: its time and distance on the heading, its turn point, whether the
    rules allow it (direction of flight, 15 minutes at most, the side and size of the fan), the one the plan takes.
    The plan itself (speed first, then the fan) is checked against all the traffic."""
    point = point.upper()
    if point not in w.waypoints:
        raise ValueError(f"unknown point {point}")
    members = [c for c in callsigns if c in w.aircraft and w.aircraft[c].active and point in traj.route_ahead(w, w.aircraft[c])]
    off = [c for c in callsigns if c not in members]
    P = plan(w, point, S=S, members=members, use_merge=False, descents=False)
    p = w.waypoints[point]
    out = {"point": point, "point_xy": [round(p[0], 2), round(p[1], 2)], "ok": P.get("ok"), "why": P.get("why", ""),
           "sequence": P.get("sequence", []), "spacing_nm": P.get("S"), "notes": P.get("notes", []),
           "spacing": P.get("spacing", []), "closest_nm": P.get("closest_nm"), "not_via_point": off,
           "steps": [{"at_s": round(s_["at_s"], 1), "callsign": s_["callsign"], "action": s_["action"], "why": s_["why"]}
                     for s_ in P.get("steps", [])], "aircraft": {}}
    trs = P.get("trajs") or {}
    for cs in P.get("sequence", []):
        a = w.aircraft[cs]
        info = {"x": round(a.x, 2), "y": round(a.y, 2), "track": round(a.trk), "heading": round(a.hdg), "gs": round(a.gs),
                "fl": int(round(a.alt_ft / 100)), "eta_s": None}
        if cs in trs:
            c = _cross(trs[cs], p)
            info["eta_s"] = None if c is None else round(c)
            tt = np.arange(0.0, (c or 600.0) + 1.0, 20.0)
            X, Y, _ = trs[cs].at(tt)
            info["path"] = [[round(float(x_), 2), round(float(y_), 2)] for x_, y_ in zip(X, Y)]
        tg = (P.get("targets") or {}).get(cs)
        if tg:
            info.update(ahead=tg["ahead"], solution=tg.get("solution"), chosen_heading=tg.get("heading"))
            cands = fan_candidates(w, a, cs, tg["ahead"], point, tg["t_ahead_s"], tg["S_nm"], None, tg["g_lead_kt"],
                                   all_headings=True)
            info["headings"] = [{"heading": c_["heading"], "ok": c_["ok"], "why_not": c_["why_not"], "turn": c_["turn"],
                                 "side": "R" if c_["side"] > 0 else "L",
                                 "time_s": round(c_["time_s"]) if c_.get("time_s") is not None else None,
                                 "leg_nm": round(c_["leg_nm"], 1) if c_.get("leg_nm") is not None else None,
                                 "turn_point": [round(c_["turn_point"][0], 2), round(c_["turn_point"][1], 2)] if c_.get("turn_point") else None}
                                for c_ in sorted(cands, key=lambda c_: c_["heading"])]
            # the ellipse: the turn points of every heading (2 degree steps), over the ground (wind included)
            tas = a.tas if a.tas > 100 else max(a.gs, 150.0)
            pts = []
            for h in range(0, 360, 2):
                g = ellipse.with_wind(a.pos, p, tg["t_ahead_s"], tg["S_nm"], tas, tuple(a.wind_uv), h, g_lead=tg["g_lead_kt"])
                pts.append(None if g is None else [round(float(g["turn_point"][0]), 2), round(float(g["turn_point"][1]), 2)])
            info["ellipse"] = pts
        out["aircraft"][cs] = info
    return out
