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


def _candidates(w, a, st: dict, asked: Optional[dict], t_pass: Optional[float] = None):
    """(kind, params, actions, cost, needs_ask) for aircraft a: only clearances its pilot would fly, each heading and
    speed with its limit. t_pass: seconds until the encounter is passed - a speed is held that long (rounded up to the
    minute, at least 4), so the aircraft goes back to its normal speed only once the traffic is passed."""
    from ..aircraft import level_parity_ok
    from ..ailab import actions as act_mod
    out = []
    cs = a.callsign
    lims = getattr(a, "clr_limits", None) or {}
    # changing a clearance in force (a speed or a heading still within its limit) costs: it is what makes a solver
    # thrash, clearing and undoing; the same clearance given again is not a candidate
    re_spd = 0.6 if ("speed" in lims or getattr(a, "speed_assigned", False)) else 0.0
    re_hdg = 0.6 if a.nav_mode == "heading" else 0.0
    hold_s = max(240.0, math.ceil(((t_pass or 300.0) + 60.0) / 60.0) * 60.0)
    lim_txt = f"{int(hold_s // 60)}MIN"
    # speed
    if st.get("speed_mode") == "mach" and st.get("mach"):
        m0 = round(st["mach"], 2)
        mmo = getattr(a.perf, "mmo", 0.86)
        for d in (-0.02, -0.03, -0.04, -0.05, -0.06, +0.01, +0.02, +0.03):
            m = round(m0 + d, 2)
            if 0.68 <= m <= min(0.86, mmo - 0.01) and _flyable(w, a, {"type": "speed", "mach": m}):
                sp = f"M{int(round(m * 100))}"
                out.append(("speed", {"speed": sp, "speed_for_s": hold_s},
                            [{"type": "clear", "callsign": cs, "speed": sp, "limit": lim_txt}], abs(d) / 0.01 * 0.08 + re_spd, False))
    elif st.get("ias_kt"):
        v0 = int(round(st["ias_kt"] / 10) * 10)
        for d in (-20, -30, -40, -50, +20, +30):
            v = v0 + d
            if 210 <= v <= 340 and _flyable(w, a, {"type": "speed", "ias_kt": v}):
                out.append(("speed", {"speed": str(v), "speed_for_s": hold_s},
                            [{"type": "clear", "callsign": cs, "speed": str(v), "limit": lim_txt}], abs(d) / 10 * 0.08 + re_spd, False))
    # direct (shortcut to a later point of its route)
    ahead = traj.route_ahead(w, a)
    for k, p in enumerate(ahead[1:4], start=1):
        out.append(("direct", {"direct": p}, [{"type": "clear", "callsign": cs, "direct": p}], 1.0 + 0.05 * k, False))
    # heading: a delaying vector of 2, 4 or 6 minutes, then direct to the route point after the next one
    trk = a.trk
    then = ahead[1] if len(ahead) > 1 else (ahead[0] if ahead else None)
    for dpsi in (10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 90, -90):
        h = int(round(trk + dpsi)) % 360 or 360
        if not _flyable(w, a, {"type": "heading", "heading": h}):
            continue                                  # e.g. into weather it cannot fly through
        for for_s in (120.0, 240.0, 360.0):
            act = {"type": "clear", "callsign": cs, "heading": h, "limit": f"{int(for_s // 60)}MIN"}
            if then:
                act["then"] = then
            out.append(("heading", {"heading": h, "for_s": for_s},
                        [act], 1.2 + abs(dpsi) / 10 * 0.06 + for_s / 120 * 0.05 + re_hdg, False))
    # level, at the aircraft's own rate, or at a rate no greater than 1000 / 2000 ft/min (to climb or descend
    # together with another aircraft, or to pass another's level later)
    base = int(round((st.get("cleared_fl") or st["fl"])))
    for d in (10, -10, 20, -20, 30, -30, 40, -40):
        fl = base + d
        if not (100 <= fl <= w.upper_fl):
            continue
        down_arr = fl * 100 < a.alt_ft - 300 and w.descending_to_destination(a, fl)
        if not (down_arr or level_parity_ok(fl, a.southbound)):
            continue
        if fl * 100 > getattr(a.perf, "ceiling_ft", 1e9) or not _flyable(w, a, {"type": "altitude", "fl": fl}):
            continue
        ask = bool(act_mod.level_needs_asking(w, a, fl, asked or {})) if a.assumed else False
        cost = 3.0 + abs(d) / 10 * 0.15 + (0.5 if ask else 0.0)
        out.append(("level", {"cfl": fl}, [{"type": "clear", "callsign": cs, "cfl": fl}], cost, ask))
        own = traj._perf_rate(a, fl * 100 > a.alt_ft)
        for r in (1000, 2000):
            if own and own > r + 200:
                out.append(("level", {"cfl": fl, "rate": float(r)},
                            [{"type": "clear", "callsign": cs, "cfl": fl, "rate": f"{r}-"}], cost + 0.1, ask))
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
        return f"{cs} FL{prm['cfl']:03d}{rate}"
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
        sets.append({"pair": [p.a, p.b], "t_first": p.t_first, "d_min": round(p.d_min, 1), "priority": why, "move": mv, "hj": hjv,
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
