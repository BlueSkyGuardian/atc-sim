"""The solution set of a situation.

For each predicted conflict between aircraft (at least one of them yours), every single clearance of the aircraft
allowed to move is tried on its predicted trajectory (traj.py) and checked leg by leg against EVERY other aircraft
(cpa.py): a clearance is a solution only if it leaves no conflict with anyone (with a margin) over the horizon, so a
resolution that creates a new conflict is never offered. Solutions are costed in the controller's order:

  speed  <  direct / heading (a delaying vector, then resume)  <  level (asked to the pilot first unless urgent)

smaller changes before larger ones, and the aircraft with priority (maintaining its level; else the first to enter
the sector) is moved only if nothing else works (a heavy extra cost). When no single clearance works, pairs of
clearances (one for each aircraft) are tried. The best ones are confirmed by running a copy of the simulation ahead
with the clearance given (the pilots, turns, accelerations as the simulator flies them).

solve(w, ...) -> {"conflicts": [...], "sets": [{"pair", "solutions": [...], "optimal": [...]}]}
"""
from __future__ import annotations

import math
import types
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from . import cpa, hj, traj

MARGIN_NM = 1.0
H_DEFAULT = 600.0
URGENT_S = 120.0
OPT_TOL = 0.3


@dataclass
class Solution:
    mover: str
    kind: str                       # speed | direct | heading | level | pair
    actions: List[dict]
    text: str
    cost: float
    d_min: float                    # closest distance to anyone (vertically too close) afterwards, NM
    needs_ask: bool = False
    priority_moved: bool = False
    confirmed: Optional[bool] = None
    confirmed_min_nm: Optional[float] = None
    params: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d["d_min"] = None if math.isinf(self.d_min) else round(self.d_min, 1)
        d["cost"] = round(self.cost, 2)
        return d


def _flyable(w, a, instr: dict) -> bool:
    """The pilot's own check (performance at its level and weight, weather, RVSM): would it fly this?"""
    from ..world import InstructionError
    if not a.assumed:
        return True
    try:
        w.validate(dict(instr, callsign=a.callsign))
        return True
    except InstructionError:
        return False
    except Exception:  # noqa: BLE001
        return True


MAX_OFF_ROUTE_DEG = 60.0   # a vector keeps the aircraft's direction: within this of the bearing to where it rejoins
MAX_OFF_EXIT_DEG = 90.0    # and never turns it away from its exit
LAST_REJECTED: Dict[str, Dict[str, int]] = {}   # per aircraft: candidates rejected by each constraint (last call)


def departing(w, a) -> bool:
    """A departure still climbing to its final level: no speed control (it climbs at its own speed schedule)."""
    rfl = getattr(a, "rfl", None)
    if rfl and a.alt_ft < rfl * 100 - 300:
        return True
    dep = str((a.flight_plan or {}).get("departure") or "").upper()
    return a.vs_fpm > 300 and (dep in getattr(w, "aerodromes", {}) or dep in ("GMMN", "GMMX", "GMAD", "GMMW"))


def route_bearings(w, a) -> tuple:
    """(bearing to the point it rejoins after a vector, bearing to its exit): the aircraft's direction of flight."""
    ahead = traj.route_ahead(w, a)
    if not ahead:
        return a.trk, a.trk
    pos = a.pos
    pts = [w.waypoints[p] for p in ahead]
    rej = pts[1] if len(pts) > 1 else pts[0]
    if math.hypot(rej[0] - pos[0], rej[1] - pos[1]) < 15 and len(pts) > 2:
        rej = pts[2]
    ext = w.waypoints.get(getattr(a, "planned_exit", None) or "", None) or pts[-1]
    brg = lambda p: math.degrees(math.atan2(p[0] - pos[0], p[1] - pos[1])) % 360
    return brg(rej), brg(ext)


def _angle(a_, b_) -> float:
    return abs((a_ - b_ + 180) % 360 - 180)


def generate(w, a, st: dict, t_pass: Optional[float] = None, target_fl: Optional[int] = None) -> List[dict]:
    """Layer 1 - the plausible clearances for aircraft a, built around its route: speeds about its own speed, directs
    to points ahead, vectors around the direction it is going (the bearing to where it rejoins its route) and around its
    present track, levels about its cleared level, and the intermediate levels between its present level and the level
    it is going to (or asks for: target_fl). Each heading and speed with its limit."""
    out = []
    cs = a.callsign
    lims = getattr(a, "clr_limits", None) or {}
    re_spd = 0.6 if ("speed" in lims or getattr(a, "speed_assigned", False)) else 0.0
    re_hdg = 0.6 if a.nav_mode == "heading" else 0.0
    hold_s = max(240.0, math.ceil(((t_pass or 300.0) + 60.0) / 60.0) * 60.0)
    lim_txt = f"{int(hold_s // 60)}MIN"
    mode_n, val_n = a.normal_speed()
    cur_m = st.get("mach") if st.get("speed_mode") == "mach" else None
    if mode_n == "mach":
        for d in (-0.01, -0.02, -0.03, +0.01, +0.02, +0.03):
            m = round(val_n + d, 2)
            if cur_m and abs(m - cur_m) < 0.005:
                continue
            sp = f"M{int(round(m * 100))}"
            out.append({"kind": "speed", "prm": {"speed": sp, "speed_for_s": hold_s}, "delta": d,
                        "acts": [{"type": "clear", "callsign": cs, "speed": sp, "limit": lim_txt}],
                        "cost": abs(d) / 0.01 * 0.08 + re_spd, "check": {"type": "speed", "mach": m}})
    else:
        v0 = int(round(val_n / 10) * 10)
        for d in (-10, -20, +10, +20):
            v = v0 + d
            out.append({"kind": "speed", "prm": {"speed": str(v), "speed_for_s": hold_s}, "delta_kt": v - val_n,
                        "acts": [{"type": "clear", "callsign": cs, "speed": str(v), "limit": lim_txt}],
                        "cost": abs(d) / 10 * 0.08 + re_spd, "check": {"type": "speed", "ias_kt": v}})
    ahead = traj.route_ahead(w, a)
    for k, p in enumerate(ahead[1:4], start=1):
        out.append({"kind": "direct", "prm": {"direct": p}, "acts": [{"type": "clear", "callsign": cs, "direct": p}],
                    "cost": 1.0 + 0.05 * k, "check": {"type": "direct", "waypoint": p}})
    brg_r, brg_x = route_bearings(w, a)
    then = ahead[1] if len(ahead) > 1 else (ahead[0] if ahead else None)
    seen = set()
    for base, offs in ((a.trk, (10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 90, -90)), (brg_r, (0, 10, -10, 20, -20, 30, -30, 45, -45))):
        for dpsi in offs:
            h = int(round((base + dpsi) / 5.0) * 5) % 360 or 360
            if h in seen or _angle(h, a.trk) < 5:
                continue
            seen.add(h)
            turn = _angle(h, a.trk)
            for for_s in (120.0, 240.0, 360.0):
                act = {"type": "clear", "callsign": cs, "heading": h, "limit": f"{int(for_s // 60)}MIN"}
                if then:
                    act["then"] = then
                out.append({"kind": "heading", "prm": {"heading": h, "for_s": for_s}, "acts": [act], "off_route": _angle(h, brg_r),
                            "off_exit": _angle(h, brg_x), "cost": 1.2 + turn / 10 * 0.06 + for_s / 120 * 0.05 + re_hdg,
                            "check": {"type": "heading", "heading": h}})
    # levels: about the cleared level; and the intermediate levels on the way to it (or to the level asked for)
    base = int(round((st.get("cleared_fl") or st["fl"])))
    f0 = int(round(a.alt_ft / 100))
    tgt = int(target_fl) if target_fl else base
    inter = []
    if tgt > f0 + 5:
        inter = list(range((f0 // 10 + 1) * 10, tgt, 10))
    elif tgt < f0 - 5:
        inter = list(range((f0 // 10) * 10, tgt, -10))
    levels = {}
    for d in (10, -10, 20, -20, 30, -30, 40, -40):
        levels[base + d] = 3.0 + abs(d) / 10 * 0.15
    for fl in inter:                                   # stop the climb / descent there for now: cheaper than moving it
        levels[fl] = min(levels.get(fl, 9.0), 0.9 + abs(tgt - fl) / 10 * 0.05)
    for fl, cost in levels.items():
        out.append({"kind": "level", "prm": {"cfl": fl}, "acts": [{"type": "clear", "callsign": cs, "cfl": fl}], "cost": cost,
                    "intermediate": fl in inter, "target": tgt, "check": {"type": "altitude", "fl": fl}})
        own = traj._perf_rate(a, fl * 100 > a.alt_ft)
        for r in (1000, 2000):
            if own and own > r + 200:
                out.append({"kind": "level", "prm": {"cfl": fl, "rate": float(r)}, "intermediate": fl in inter, "target": tgt,
                            "acts": [{"type": "clear", "callsign": cs, "cfl": fl, "rate": f"{r}-"}], "cost": cost + 0.1,
                            "check": {"type": "altitude", "fl": fl}})
    return out


def constraint(w, a, c: dict, asked: Optional[dict]) -> Optional[str]:
    """Layer 2 - the rules a plausible clearance must keep (the name of the first one broken, or None); also sets
    c["ask"] for a level the pilot did not request."""
    from ..aircraft import level_parity_ok
    from ..ailab import actions as act_mod
    from ..world import MAX_MACH_CHANGE, MAX_IAS_CHANGE_KT
    k = c["kind"]
    if k == "speed":
        if departing(w, a):
            return "no speed control for a departure still climbing"
        if abs(c.get("delta", 0.0)) > MAX_MACH_CHANGE + 1e-6 or abs(c.get("delta_kt", 0.0)) > MAX_IAS_CHANGE_KT + 5:
            return "speed more than 0.03 Mach / 20 kt from its own"
        if c["check"].get("mach") is not None and c["check"]["mach"] > min(0.86, getattr(a.perf, "mmo", 0.86) - 0.01):
            return "above its maximum Mach"
        if c["check"].get("ias_kt") is not None and not (210 <= c["check"]["ias_kt"] <= 340):
            return "outside the usable IAS"
    if k == "heading":
        if c["off_route"] > MAX_OFF_ROUTE_DEG:
            return "away from its direction of flight"
        if c["off_exit"] > MAX_OFF_EXIT_DEG:
            return "turns it away from its exit"
    if k == "level":
        fl = c["prm"]["cfl"]
        if not (100 <= fl <= w.upper_fl):
            return "outside the sector's levels"
        down_arr = fl * 100 < a.alt_ft - 300 and w.descending_to_destination(a, fl)
        if not (down_arr or level_parity_ok(fl, a.southbound)):
            return "semicircular rule"
        if fl * 100 > getattr(a.perf, "ceiling_ft", 1e9):
            return "above its ceiling"
        c["ask"] = bool(act_mod.level_needs_asking(w, a, fl, asked or {})) if a.assumed and not c.get("intermediate") else False
        if c["ask"]:
            c["cost"] += 0.5
    if not _flyable(w, a, c["check"]):
        return "the pilot would refuse it (performance, weather, RVSM)"
    return None


def _candidates(w, a, st: dict, asked: Optional[dict], t_pass: Optional[float] = None, target_fl: Optional[int] = None):
    """(kind, params, actions, cost, needs_ask) of the plausible clearances that keep every rule (layers 1 and 2);
    what was rejected, by rule, is kept in LAST_REJECTED[callsign]."""
    out, rej = [], {}
    for c in generate(w, a, st, t_pass, target_fl):
        why = constraint(w, a, c, asked)
        if why:
            rej[why] = rej.get(why, 0) + 1
            continue
        prm = dict(c["prm"])
        if c.get("intermediate"):
            prm["intermediate"] = c["target"]
        out.append((c["kind"], prm, c["acts"], c["cost"], bool(c.get("ask"))))
    LAST_REJECTED[a.callsign] = rej
    return out


def _text(kind: str, cs: str, prm: dict) -> str:
    if kind == "speed":
        lim = f" for {int(prm['speed_for_s'] // 60)} min" if prm.get("speed_for_s") else ""
        return f"{cs} speed {prm['speed']}{lim}, then normal speed"
    if kind == "direct":
        return f"{cs} direct {prm['direct']}"
    if kind == "heading":
        return f"{cs} heading {prm['heading']:03d} for {int(prm['for_s'] // 60)} min, then resume"
    if kind == "level":
        rate = f" at {int(prm['rate'])} ft/min or less" if prm.get("rate") else ""
        later = f" for now (FL{prm['intermediate']:03d} when clear)" if prm.get("intermediate") else ""
        return f"{cs} FL{prm['cfl']:03d}{rate}{later}"
    return cs


def check_clearance(w, a, H: float, prm: dict, trajs: Dict, sh: float):
    """(ok, d_min, trajectory) of aircraft a with the clearance prm against everyone else. A level change is checked
    at the fastest and the slowest rate it may climb or descend at (both must be clear)."""
    Tm = traj.build(w, a, H, **prm)
    ok, dmin = _check(Tm, trajs, {a.callsign}, sh)
    if ok and prm.get("cfl") is not None and prm.get("rate") is None and abs(int(prm["cfl"]) * 100 - a.alt_ft) >= 50:
        for r in ("fast", "slow"):
            ok2, d2 = _check(traj.build(w, a, H, rates=r, **prm), trajs, {a.callsign}, sh)
            ok, dmin = ok and ok2, min(dmin, d2)
            if not ok:
                break
    return ok, dmin, Tm


def _check(Tm, trajs: Dict, skip: set, sh: float):
    """(ok, d_min) of a trajectory against every other trajectory but `skip`."""
    dmin = math.inf
    for o, To in trajs.items():
        if o in skip:
            continue
        p = cpa.pair(Tm, To, sh)
        dmin = min(dmin, p.d_min)
        if p.d_min < sh + MARGIN_NM:
            return False, dmin
    return True, dmin


def clone(w):
    from ..session import Session
    from ..platform.debriefs import blob_of
    w2 = Session._load_world(types.SimpleNamespace(world=w), blob_of(w))
    w2.record = False
    w2.auto_pilot = True          # the pilots fly what is entered (as on the solo radar), whatever the session's mode
    w2.readback_rate = 0.0        # and read it back right: the rollout judges the clearance, not a mishearing
    return w2


def confirm(w, sol: Solution, H: float = H_DEFAULT) -> Solution:
    """Run a copy of the simulation ahead with the clearance(s) given: the closest the moved aircraft come to anyone."""
    from ..ailab import actions as act_mod
    w2 = clone(w)
    actor = {"name": "solver", "seat": "atc"}
    acts = list(sol.actions)
    resume_at = {}                                    # a vector without a limit is resumed here; one with its limit,
    for a_ in acts:                                   # by the pilot at the limit
        if a_.get("heading") is not None and not a_.get("limit"):
            prm = sol.params.get(a_["callsign"], sol.params) if sol.kind == "pair" else sol.params
            if prm.get("for_s"):
                resume_at[a_["callsign"]] = float(prm["for_s"])
    try:
        res = act_mod.apply(w2, acts, actor, rules=False)
        if not all(r_["ok"] for r_ in res):
            sol.confirmed = False                     # the pilot would not fly it
            return sol
    except Exception:  # noqa: BLE001
        sol.confirmed = False
        return sol
    movers = {a_["callsign"] for a_ in acts}
    t0, dmin = w2.t, math.inf
    while w2.t - t0 < H:
        w2.step(1.0)
        el = w2.t - t0
        for cs_, ts in list(resume_at.items()):
            if el >= ts:
                act_mod.apply(w2, [{"type": "resume", "callsign": cs_}], actor, rules=False)
                resume_at.pop(cs_)
        if int(el) % 2:
            continue
        for m in movers:
            am = w2.aircraft.get(m)
            if am is None or not am.active:
                continue
            for o in w2.aircraft.values():
                if not o.active or o.callsign == m:
                    continue
                sv = cpa._sv(am.alt_ft, o.alt_ft, False, False)
                if abs(am.alt_ft - o.alt_ft) < sv - 1:
                    dmin = min(dmin, math.hypot(am.x - o.x, am.y - o.y))
    sol.confirmed = dmin >= w.sep_h
    sol.confirmed_min_nm = None if math.isinf(dmin) else round(dmin, 1)
    return sol


def solve(w, pair: Optional[tuple] = None, H: float = H_DEFAULT, asked: Optional[dict] = None, rollout: int = 2,
          max_solutions: int = 12, only_assumed: bool = True, exit_spacing: bool = True) -> Dict:
    from ..ailab.observe import priority
    sh = float(w.sep_h)
    trajs = traj.build_all(w, H)
    st = {a["callsign"]: a for a in w.state()["aircraft"]}
    mine = {c for c, a in w.aircraft.items() if a.active and a.assumed}
    M = cpa.matrix(trajs, sh, only=sorted(mine) if only_assumed else None)
    confs = [p for p in M["conflicts"] if (not only_assumed or p.a in mine or p.b in mine)]
    if pair:
        confs = [p for p in confs if {p.a, p.b} == set(pair)]
    sets = []
    for p in confs:
        A, B = st.get(p.a), st.get(p.b)
        mv, why = priority(A, B, w) if A and B else (None, "")
        order = [x for x in ([mv, p.b if mv == p.a else p.a] if mv else [p.a, p.b]) if x in mine or not only_assumed]
        sols: List[Solution] = []
        partial = {}
        hjv = {}
        for k, cs in enumerate(order):
            a = w.aircraft[cs]
            moved_prio = bool(mv) and cs != mv
            scored = []
            other_cs = p.b if cs == p.a else p.a
            lateral_ok = True
            if p.t_first is not None and p.t_first <= hj.T_S and abs(a.vs_fpm) < 300 and abs(w.aircraft[other_cs].vs_fpm) < 300:
                try:
                    hv = hj.can_turn_away(w, cs, other_cs)
                except Exception:  # noqa: BLE001
                    hv = None
                if hv is not None:
                    hjv[cs] = hv
                    lateral_ok = hv["turn_enough"]
            t_pass = (p.t_min if p.t_min is not None else (p.t_first or 0) + 120.0)
            for kind, prm, acts, cost, ask in _candidates(w, a, st[cs], asked, t_pass):
                if not lateral_ok and kind in ("heading", "direct"):
                    continue                      # Hamilton-Jacobi: no turn of this aircraft can separate it in time
                ok, dmin, Tm = check_clearance(w, a, H, prm, trajs, sh)
                scored.append((ok, dmin, kind, prm, acts, cost, ask, Tm))
                if ok:
                    sols.append(Solution(cs, kind, acts, _text(kind, cs, prm), cost + (5.0 if moved_prio else 0.0),
                                         dmin, ask, moved_prio, params=prm))
            # best partial moves (largest gain against the other aircraft of the pair), for pairs of clearances
            other = p.b if cs == p.a else p.a
            partial[cs] = sorted(scored, key=lambda s: -cpa.pair(s[7], trajs[other], sh).d_min)[:6]
        if not sols and len(partial) == 2:
            (ca, la), (cb, lb) = list(partial.items())
            for sa in la:
                for sb in lb:
                    ok, dmin = _check(sa[7], {**trajs, cb: sb[7]}, {ca}, sh)
                    if not ok:
                        continue
                    ok2, dmin2 = _check(sb[7], {**trajs, ca: sa[7]}, {cb}, sh)
                    if ok2:
                        sols.append(Solution(f"{ca}+{cb}", "pair", sa[4] + sb[4], f"{_text(sa[2], ca, sa[3])} and {_text(sb[2], cb, sb[3])}",
                                             sa[5] + sb[5] + 1.0, min(dmin, dmin2), sa[6] or sb[6], False,
                                             params={ca: sa[3], cb: sb[3]}))
        sols.sort(key=lambda s: s.cost)
        sols = sols[:max_solutions]
        good, tried = 0, 0
        for s in sols:
            if good >= rollout or tried >= rollout + 4:
                break
            confirm(w, s, H)
            tried += 1
            good += bool(s.confirmed)
        sols = [s for s in sols if s.confirmed is not False]
        best = next((s for s in sols if s.confirmed is not False), None)
        optimal = [s for s in sols if best and s.kind == best.kind and s.cost <= best.cost + OPT_TOL and s.confirmed is not False]
        rejected = {}
        for cs_ in order:
            for k_, n_ in LAST_REJECTED.get(cs_, {}).items():
                rejected[k_] = rejected.get(k_, 0) + n_
        sets.append({"pair": [p.a, p.b], "t_first": p.t_first, "d_min": round(p.d_min, 1), "priority": why, "move": mv, "hj": hjv,
                     "rejected": rejected,
                     "solutions": sols, "optimal": optimal})
    if exit_spacing:
        sets += _exit_sets(w, H, asked, st, mine, only_assumed, trajs, sh, max_solutions)
    return {"t": w.t, "conflicts": confs, "sets": sets}


def _exit_sets(w, H, asked, st, mine, only_assumed, trajs, sh, max_solutions) -> List[dict]:
    """Spacing at the exit (exits.py): the aircraft behind gives the spacing (the one ahead keeps its speed and
    level), by a speed held until it crosses the exit, a vector, or another level with the exit level coordinated;
    each solution also checked against everyone (CPA)."""
    from . import exits
    out = []
    try:
        viol = exits.conflicts(w, only_assumed=only_assumed)
    except Exception:  # noqa: BLE001
        return out
    longs_cache: Dict[str, object] = {}
    for v in viol:
        x, lead, foll = v["exit"], v["leader"], v["follower"]
        members = [cs for cs, a in w.aircraft.items() if a.active and exits.exit_of(w, a) == x]
        for cs in members:
            if cs not in longs_cache:
                longs_cache[cs] = traj.build(w, w.aircraft[cs], exits.H_EXIT)
        longs = {cs: longs_cache[cs] for cs in members}
        sols: List[Solution] = []
        for cs, extra in ((foll, 0.0), (lead, 0.8)):          # the one behind first; speeding up the one ahead next
            if cs not in mine and only_assumed:
                continue
            a = w.aircraft[cs]
            t_pass = v["t_follower"] if cs == foll else v["t_leader"]
            for kind, prm, acts, cost, ask in _candidates(w, a, st[cs], asked, t_pass):
                ok, dmin, Tm = check_clearance(w, a, H, prm, trajs, sh)
                if not ok:
                    continue
                TL = traj.build(w, a, exits.H_EXIT, **prm)
                ok2, sp = exits.check(w, TL, x, longs, MARGIN_NM)
                if not ok2:
                    continue
                if kind == "level":
                    acts = [dict(acts[0], xfl=prm["cfl"])]           # leaves at that level: coordinated with the next unit
                    cost += 0.3
                if kind == "speed":
                    acts = [dict(acts[0], limit=x)]                   # held until the exit point
                sols.append(Solution(cs, kind, acts, _text(kind, cs, prm) + (f" (exit FL{prm['cfl']:03d} coordinated)" if kind == "level" else ""),
                                     cost + extra, min(dmin, sp), ask, False, params=prm))
        sols.sort(key=lambda s_: s_.cost)
        sols = sols[:max_solutions]
        best = sols[0] if sols else None
        optimal = [s_ for s_ in sols if best and s_.kind == best.kind and s_.cost <= best.cost + OPT_TOL]
        out.append({"pair": [lead, foll], "kind": "exit", "exit": x, "t_first": v["t_follower"], "d_min": round(v["spacing_nm"], 1),
                    "required_nm": v["required_nm"], "priority": f"{lead} is ahead at {x} and keeps its speed; {foll}, behind, gives the spacing",
                    "move": foll, "hj": {}, "solutions": sols, "optimal": optimal})
    return out


def describe(res: Dict, limit: int = 3) -> List[str]:
    out = []
    for s in res["sets"]:
        a, b = s["pair"]
        tf = s["t_first"] or 0
        if s.get("kind") == "exit":
            head = (f"EXIT {s['exit']}: {b} {s['d_min']} NM behind {a} when it crosses (needs {s['required_nm']:g} NM) "
                    f"in {int(tf // 60):02d}:{int(tf % 60):02d}")
        else:
            head = f"{a}×{b} (closest {s['d_min']} NM in {int(tf // 60):02d}:{int(tf % 60):02d})"
        for m, hv in (s.get("hj") or {}).items():
            if not hv["turn_enough"]:
                head += f" [{m}: no turn can separate it in time]"
        if not s["solutions"]:
            out.append(f"{head}: no single clearance or pair found that leaves everyone separated — combine, or act on other traffic")
            continue
        opt = s["optimal"] or s["solutions"][:1]
        rest = [x for x in s["solutions"] if x not in opt][:limit]
        txt = "; ".join(x.text + (" (ask first)" if x.needs_ask else "") + (" [moves the aircraft with priority]" if x.priority_moved else "")
                        + (f" [confirmed: {x.confirmed_min_nm} NM]" if x.confirmed else "") for x in opt[:limit])
        alt = "; ".join(x.text for x in rest)
        out.append(f"{head}: BEST {txt}" + (f" — other options: {alt}" if alt else ""))
    return out


def clearance_params(w, a, action: dict) -> dict:
    """The trajectory parameters of a clearance as given (AI-lab action or label fields): the limits in seconds."""
    prm = {}
    gs = max(a.gs, 150.0)

    def secs(lim):
        if lim in (None, ""):
            return None
        try:
            L = w.parse_limit(lim, allow_point=True)
        except Exception:  # noqa: BLE001
            return None
        if L.get("min") is not None:
            return float(L["min"]) * 60.0
        if L.get("nm") is not None:
            return float(L["nm"]) / gs * 3600.0
        if L.get("until") in w.waypoints:
            pts = [a.pos] + [w.waypoints[p] for p in traj.route_ahead(w, a)]
            d = traj._along_to(pts, w.waypoints[L["until"]])
            return None if d is None else d / gs * 3600.0
        return None
    if action.get("cfl") not in (None, ""):
        prm["cfl"] = int(str(action["cfl"]).upper().lstrip("FL").lstrip("F"))
        rt = action.get("rate")
        if rt not in (None, "") and str(rt).endswith("-"):
            prm["rate"] = float(str(rt).rstrip("+-"))
    if action.get("heading") not in (None, ""):
        prm["heading"] = int(action["heading"])
        prm["for_s"] = secs(action.get("limit") or action.get("hlim"))
    elif action.get("direct") not in (None, ""):
        prm["direct"] = str(action["direct"]).upper()
    sp = action.get("speed")
    if sp not in (None, "") and str(sp).upper() != "SN":
        prm["speed"] = str(sp).upper()
        prm["speed_for_s"] = secs(action.get("limit") or action.get("slim"))
    return prm


def positive(w, cs: str, prm: dict, trajs: Optional[Dict] = None, H: float = H_DEFAULT) -> dict:
    """Positive separation: is separation assured for the whole manoeuvre this clearance starts? The aircraft's
    trajectory with it (a level change at the fastest and slowest rate it may fly) against everyone's: assured when
    it stays at or beyond the minimum from all; in an urgent situation (a loss, or a conflict under 2 minutes) a
    clearance that opens the aircraft's predicted closest approach by a mile or more counts as positive too."""
    a = w.aircraft.get(cs)
    if a is None or not prm:
        return {"positive": True}
    sh = float(w.sep_h)
    trajs = trajs or traj.build_all(w, H)
    _, before = _check(trajs.get(cs) or traj.build(w, a, H), trajs, {cs}, 1e9)
    Tm = traj.build(w, a, H, **prm)
    _, after = _check(Tm, trajs, {cs}, 1e9)
    if prm.get("cfl") is not None and prm.get("rate") is None and abs(int(prm["cfl"]) * 100 - a.alt_ft) >= 50:
        for r in ("fast", "slow"):
            after = min(after, _check(traj.build(w, a, H, rates=r, **prm), trajs, {cs}, 1e9)[1])
    who = None
    if after < sh:
        for o, To in trajs.items():
            if o != cs and cpa.pair(Tm, To, sh).d_min <= after + 0.05:
                who = o
                break
    urgent = False
    if before < sh:                                  # already in a conflict: is it a loss, or under 2 minutes?
        T0 = trajs.get(cs) or traj.build(w, a, H)
        for o, To in trajs.items():
            if o != cs:
                pc = cpa.pair(T0, To, sh)
                if pc.conflict and pc.t_first is not None and pc.t_first <= URGENT_S:
                    urgent = True
                    break
    ok = after >= sh or (urgent and after >= before + 1.0)
    return {"positive": bool(ok), "after_nm": None if math.isinf(after) else round(after, 1),
            "before_nm": None if math.isinf(before) else round(before, 1), "with": who}


def accommodate(w, cs: str, req: dict, asked: Optional[dict] = None, H: float = H_DEFAULT) -> Optional[dict]:
    """A pilot's request (req: {"cfl": 370} or {"direct": "IBALU"}) made possible: the request alone if it is clear of
    everyone; else with a speed or a vector of the requesting aircraft, or of the aircraft in the way (an aircraft that
    did nothing is moved only when the requester cannot be); None when nothing makes it separated - then it is refused.
    Close in (a conflict under 5 minutes, both level), a vector is tried only if the reachability tables say a turn can
    still separate the pair. Returns {"actions": [...], "text": "...", "cost": c}."""
    a = w.aircraft.get(cs)
    if a is None:
        return None
    sh = float(w.sep_h)
    trajs = traj.build_all(w, H)
    st = {x["callsign"]: x for x in w.state()["aircraft"]}
    base_act = {"type": "clear", "callsign": cs, **({"cfl": int(req["cfl"])} if req.get("cfl") is not None else {"direct": req["direct"]})}
    ok, _, Tm = check_clearance(w, a, H, req, trajs, sh)
    if ok:
        return {"actions": [base_act], "text": f"{cs}: request given as asked", "cost": 0.0}
    # who is in the way of the request (at either rate bound for a level)
    variants = [Tm] + ([traj.build(w, a, H, rates=r, **req) for r in ("fast", "slow")] if req.get("cfl") is not None else [])
    blocking = {}
    for o, To in trajs.items():
        if o == cs:
            continue
        for V in variants:
            p = cpa.pair(V, To, sh)
            if p.d_min < sh + MARGIN_NM:
                blocking[o] = min(blocking.get(o, 1e9), p.t_first if p.t_first is not None else (p.t_min or 0.0))
    if not blocking:
        return None

    def turn_ok(mover, other):
        t_f = blocking.get(other)
        if t_f is None or t_f > hj.T_S or abs(w.aircraft[mover].vs_fpm) > 300 or abs(w.aircraft[other].vs_fpm) > 300:
            return True
        try:
            hv = hj.can_turn_away(w, mover, other)
        except Exception:  # noqa: BLE001
            hv = None
        return True if hv is None else hv["turn_enough"]
    best = None
    # 1) the requesting aircraft: the request with a speed (or, for a level, a vector)
    lateral_ok = all(turn_ok(cs, o) for o in blocking)
    for kind, prm, acts, cost, ask in _candidates(w, a, st[cs], asked, max(blocking.values()) + 120.0):
        if kind not in ("speed", "heading") or (kind == "heading" and (req.get("direct") or not lateral_ok)):
            continue
        ok2, d2, _ = check_clearance(w, a, H, {**req, **prm}, trajs, sh)
        if ok2 and (best is None or cost < best["cost"]):
            act = {**base_act, **{k: v for k, v in acts[0].items() if k not in ("type", "callsign")}}
            best = {"actions": [act], "cost": cost, "text": f"{cs}: request given with {_text(kind, cs, prm).replace(cs + ' ', '')}"}
    # 1b) a level asked for: an intermediate level now (the climb or descent stopped short of the conflict), the
    #     level asked for later, when separation is assured - the request stays open
    if req.get("cfl") is not None:
        for kind, prm, acts, cost, ask in _candidates(w, a, st[cs], asked, max(blocking.values()) + 120.0, target_fl=int(req["cfl"])):
            if kind != "level" or not prm.get("intermediate") or (best is not None and cost >= best["cost"]):
                continue
            ok2, d2, _ = check_clearance(w, a, H, prm, trajs, sh)
            if ok2:
                best = {"actions": acts, "cost": cost, "intermediate": prm["cfl"],
                        "text": f"{cs}: FL{prm['cfl']:03d} for now, FL{int(req['cfl']):03d} when separation is assured"}
    # 2) the aircraft in the way (ours only), the request given as asked
    if len(blocking) == 1:
        o = next(iter(blocking))
        ao = w.aircraft[o]
        if ao.active and ao.assumed and o in st:
            T2 = dict(trajs)
            T2[cs] = Tm
            lat_o = turn_ok(o, cs)
            for kind, prm, acts, cost, ask in _candidates(w, ao, st[o], asked, blocking[o] + 120.0):
                if kind not in ("speed", "heading", "direct") or (kind == "heading" and not lat_o):
                    continue
                cost += 2.0                                   # moving an aircraft that asked for nothing
                if best is not None and cost >= best["cost"]:
                    continue
                ok_o, _, To_new = check_clearance(w, ao, H, prm, T2, sh)
                if not ok_o:
                    continue
                ok_r, _, _ = check_clearance(w, a, H, req, {**trajs, o: To_new}, sh)
                if ok_r:
                    best = {"actions": [acts[0], base_act], "cost": cost,
                            "text": f"{cs}: request given, {_text(kind, o, prm)} to make room"}
    return best
