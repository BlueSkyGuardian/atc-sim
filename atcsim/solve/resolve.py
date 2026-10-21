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


def _candidates(w, a, st: dict, asked: Optional[dict]):
    """(kind, params, actions, cost, needs_ask) for aircraft a: only clearances its pilot would fly."""
    from ..aircraft import level_parity_ok
    from ..ailab import actions as act_mod
    out = []
    cs = a.callsign
    # speed
    if st.get("speed_mode") == "mach" and st.get("mach"):
        m0 = round(st["mach"], 2)
        mmo = getattr(a.perf, "mmo", 0.86)
        for d in (-0.02, -0.03, -0.04, -0.05, -0.06, +0.01, +0.02, +0.03):
            m = round(m0 + d, 2)
            if 0.68 <= m <= min(0.86, mmo - 0.01) and _flyable(w, a, {"type": "speed", "mach": m}):
                sp = f"M{int(round(m * 100))}"
                out.append(("speed", {"speed": sp}, [{"type": "clear", "callsign": cs, "speed": sp}], abs(d) / 0.01 * 0.08, False))
    elif st.get("ias_kt"):
        v0 = int(round(st["ias_kt"] / 10) * 10)
        for d in (-20, -30, -40, -50, +20, +30):
            v = v0 + d
            if 210 <= v <= 340 and _flyable(w, a, {"type": "speed", "ias_kt": v}):
                out.append(("speed", {"speed": str(v)}, [{"type": "clear", "callsign": cs, "speed": str(v)}], abs(d) / 10 * 0.08, False))
    # direct (shortcut to a later point of its route)
    ahead = traj.route_ahead(w, a)
    for k, p in enumerate(ahead[1:4], start=1):
        out.append(("direct", {"direct": p}, [{"type": "clear", "callsign": cs, "direct": p}], 1.0 + 0.05 * k, False))
    # heading: a delaying vector of 2, 4 or 6 minutes then resume
    trk = a.trk
    for dpsi in (10, -10, 20, -20, 30, -30, 45, -45, 60, -60, 90, -90):
        h = int(round(trk + dpsi)) % 360 or 360
        if not _flyable(w, a, {"type": "heading", "heading": h}):
            continue                                  # e.g. into weather it cannot fly through
        for for_s in (120.0, 240.0, 360.0):
            out.append(("heading", {"heading": h, "for_s": for_s},
                        [{"type": "clear", "callsign": cs, "heading": h}],
                        1.2 + abs(dpsi) / 10 * 0.06 + for_s / 120 * 0.05, False))
    # level
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
        out.append(("level", {"cfl": fl}, [{"type": "clear", "callsign": cs, "cfl": fl}],
                    3.0 + abs(d) / 10 * 0.15 + (0.5 if ask else 0.0), ask))
    return out


def _text(kind: str, cs: str, prm: dict) -> str:
    if kind == "speed":
        return f"{cs} speed {prm['speed']}"
    if kind == "direct":
        return f"{cs} direct {prm['direct']}"
    if kind == "heading":
        return f"{cs} heading {prm['heading']:03d} for {int(prm['for_s'] // 60)} min, then resume"
    if kind == "level":
        return f"{cs} FL{prm['cfl']:03d}"
    return cs


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
    return w2


def confirm(w, sol: Solution, H: float = H_DEFAULT) -> Solution:
    """Run a copy of the simulation ahead with the clearance(s) given: the closest the moved aircraft come to anyone."""
    from ..ailab import actions as act_mod
    w2 = clone(w)
    actor = {"name": "solver", "seat": "atc"}
    acts = list(sol.actions)
    resume_at = {}
    for a_ in acts:
        if a_.get("heading") is not None:
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
          max_solutions: int = 12, only_assumed: bool = True) -> Dict:
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
            for kind, prm, acts, cost, ask in _candidates(w, a, st[cs], asked):
                if not lateral_ok and kind in ("heading", "direct"):
                    continue                      # Hamilton-Jacobi: no turn of this aircraft can separate it in time
                Tm = traj.build(w, a, H, **prm)
                ok, dmin = _check(Tm, trajs, {cs}, sh)
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
    return {"t": w.t, "conflicts": confs, "sets": sets}


def describe(res: Dict, limit: int = 3) -> List[str]:
    out = []
    for s in res["sets"]:
        a, b = s["pair"]
        head = f"{a}×{b} (closest {s['d_min']} NM in {int((s['t_first'] or 0) // 60):02d}:{int((s['t_first'] or 0) % 60):02d})"
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
