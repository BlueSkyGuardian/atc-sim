"""The situation as the controller's screen shows it, written as compact text for a language model.

observe(w, opts) -> (text, struct). The struct is what was shown, kept with the decision (experience data).

The text starts with TO DO NOW: the work waiting (aircraft calling to assume, aircraft that left to transfer,
requests to answer, conflicts to solve, aircraft on a heading that can resume), then the details: every aircraft
with its position, level, route, RVSM status, the last instruction it was given and, for an aircraft on a heading,
whether resuming its route now is conflict-free; the conflicts with their geometry (distance and bearing now, angle
between the tracks, a common point and both times to it) and the levels that are conflict-free for each aircraft
(the conflict probe, as the controller's label menu has it).

Options (agent profile): conflicts (MTCD), weather, inbound, routes, positions, todo, hints (probe: free levels,
resume check, requests), max_aircraft.
"""
from __future__ import annotations

import json

import math
from typing import Dict, List, Optional, Tuple

DEFAULTS = {"conflicts": True, "weather": True, "inbound": True, "routes": True, "positions": True, "todo": True,
            "hints": True, "solver": True, "max_aircraft": 40}
SOLVE_S = 600            # conflicts listed in TO DO under this time


def _t(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _dist_xy(p, q) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _brg(p, q) -> int:
    return int(round(math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360)) or 360


def _ahead(w, cs: str) -> List[str]:
    ac = w.aircraft.get(cs)
    if ac is None:
        return []
    return [p for p in ac.route[ac.next_index:] if not str(p).startswith("@") and p in w.waypoints]


def _eta(w, a: dict, point: str) -> Optional[float]:
    """Seconds to a point along the route (own navigation only)."""
    if a.get("nav_mode") not in ("lnav", None) or not a.get("gs_kt"):
        return None
    pos, d = (a["x_nm"], a["y_nm"]), 0.0
    for p in _ahead(w, a["callsign"]):
        q = w.waypoints[p]
        d += _dist_xy(pos, q)
        pos = q
        if p == point:
            return d / a["gs_kt"] * 3600
    return None


def _last_instructions(w) -> Dict[str, Tuple[float, str]]:
    from .. import inputs
    out = {}
    for x in inputs.log(w):
        if x.get("kind") == "entries":
            cs = str((x.get("body") or {}).get("callsign", "")).upper()
            b = x.get("body") or {}
            parts = []
            for k in ("cfl", "xfl", "heading", "direct", "speed"):
                v = b.get(k)
                if v is None or (v == "" and k != "heading"):
                    continue
                parts.append("RESUME own navigation" if k == "heading" and v == "" else f"{k.upper()} {v}")
            if parts:
                out[cs] = (float(x.get("t", 0)), " ".join(parts))
    return out


def _rvsm(a: dict) -> str:
    r = a.get("rvsm")
    st = r.get("status") if isinstance(r, dict) else r
    if st in (None, "approved", True):
        return ""
    return "NON-RVSM" if st in ("non_approved", False) else f"RVSM {str(st).upper()}"


def _ac_line(w, a: dict, ent: dict, co: dict, opts: dict, last: Optional[Tuple[float, str]], resume: Optional[dict]) -> Tuple[str, dict]:
    cs = a["callsign"]
    vs = a.get("vertical_speed_fpm") or 0
    trend = "↑" if vs > 200 else "↓" if vs < -200 else "="
    lvl = f"FL{a['fl']:03d}{trend}"
    if a.get("cleared_fl") != a.get("fl") or vs:
        lvl += f" cleared FL{a['cleared_fl']:03d}"
    if a.get("speed_mode") == "mach":
        spd = f"M{int(round((a.get('mach') or 0) * 100)):02d}"
    else:
        spd = f"{a.get('ias_kt')}kt"
    nav = a.get("nav_mode")
    ahead = _ahead(w, cs)
    lims = a.get("clearance_limits") or {}

    def _left(L):
        if L.get("until_t") is not None:
            r = max(0, int(L["until_t"] - w.t))
            return f"{r // 60:02d}:{r % 60:02d} left"
        if L.get("nm_left") is not None:
            return f"{max(0, L['nm_left']):.0f}NM left"
        return f"until {L.get('until_wp')}" if L.get("until_wp") else ""
    if a.get("speed_assigned_by_atc"):
        L = lims.get("speed")
        spd += (f" (assigned, {_left(L)}, then normal speed)" if L else " (assigned, NO LIMIT)")
    if nav == "heading":
        lat = f"ON HEADING {int(a.get('assigned_heading') or a['heading']):03d} (off its route"
        lat += f", route continues {' '.join(ahead[:4])})" if ahead else ")"
        L = lims.get("heading")
        if L:
            lat += f" — limit {_left(L)}, then " + (f"direct {L['then']}" if L.get("then") else "own navigation")
        else:
            lat += " — NO LIMIT"
    elif nav == "wxdev":
        lat = "deviating for weather"
    else:
        nxt = ahead[0] if ahead else a.get("next_waypoint")
        d = _dist_xy((a["x_nm"], a["y_nm"]), w.waypoints[nxt]) if nxt in w.waypoints else None
        lat = f"direct {nxt}" + (f" {d:.0f}NM" if d is not None else "") if nxt else "own navigation"
        if opts.get("routes") and len(ahead) > 1:
            lat += " then " + " ".join(ahead[1:5])
    fp = a.get("flight_plan") or {}
    parts = [cs, a.get("type", "")]
    if opts.get("positions"):
        parts.append(f"at ({a['x_nm']:.0f},{a['y_nm']:.0f})")
    parts += [lvl, f"trk {int(a['track_deg']):03d} gs {a['gs_kt']}", spd, lat]
    rv = _rvsm(a)
    if rv:
        parts.append(rv)
    if a.get("formation"):
        parts.append("MILITARY FORMATION (2000 ft from everyone)")
    if fp.get("destination"):
        parts.append(f"to {fp.get('destination')}")
    acx = w.aircraft.get(cs)
    arr = arrival_info(w, acx)
    if arr.get("fix") and arr["fix"] in getattr(w, "seq", {}):
        sq = w.seq[arr["fix"]]
        txt = f"ARRIVAL via {arr['fix']}: {arr.get('letter_of_agreement') or sq.describe()}"
        tod = arr.get("tod_in_nm")
        if tod is not None:
            txt += (f", top of descent in {tod:.0f}NM (it will ask for descent there: keep it at its level until then)" if tod > 0
                    else f", past its top of descent by {-tod:.0f}NM")
        if arr.get("loa_latest_descent_in_nm") is not None:
            txt += f", latest descent for the agreed level in {arr['loa_latest_descent_in_nm']:.0f}NM"
        parts.append(txt)
    xfl, nu = co.get("xfl"), co.get("next_unit") or a.get("next_unit")
    if xfl and nu:
        parts.append(f"exit FL{xfl:03d} to {nu}")
    if a.get("to_exit_nm") is not None and a.get("assumed"):
        parts.append(f"leaves your sector in {a['to_exit_nm']:.0f}NM")
    if a.get("weather_ahead_nm") is not None and opts.get("weather"):
        parts.append(f"WEATHER {a['weather_ahead_nm']:.0f}NM ahead")
    if a.get("emergency"):
        parts.append(f"EMERGENCY {a['emergency']}")
    if last:
        parts.append(f"your last instruction: {last[1]} ({_t(w.t - last[0])} ago)")
    for fl_, q in sorted(((opts.get("_asked") or {}).get(cs) or {}).items()):
        if w.t - q["t"] <= 900:
            parts.append(f"asked FL{fl_:03d}: pilot said {q['text'].split(',')[0]} ({_t(w.t - q['t'])} ago)")
    if resume is not None:
        if resume["ok"]:
            parts.append("resuming its route now: no conflict")
        else:
            fl = resume.get("levels")
            parts.append(f"resuming now would conflict with {resume['with']} in {_t(resume['in_s'])}"
                         + (f" (resumed at FL{fl[0]}: no conflict)" if fl else ""))
    st = {"callsign": cs, "type": a.get("type"), "fl": a["fl"], "cleared_fl": a.get("cleared_fl"), "vs": vs,
          "trk": int(a["track_deg"]), "gs": a["gs_kt"], "x": round(a["x_nm"], 1), "y": round(a["y_nm"], 1),
          "nav": nav, "next": ahead[0] if ahead else None, "assumed": a.get("assumed"), "on_frequency": a.get("on_frequency"),
          "xfl": xfl, "dest": fp.get("destination"), "rvsm": rv or "approved"}
    if resume is not None:
        st["resume_ok"] = resume["ok"]
    return " · ".join(str(p) for p in parts if p), st


def arrival_info(w, ac) -> dict:
    """An arrival's descent figures (top of descent, latest descent for the agreed level), computed now if the
    simulator has not yet."""
    arr = dict(getattr(ac, "arr", None) or {})
    if arr.get("fix") and arr.get("tod_in_nm") is None and arr["fix"] in getattr(w, "seq", {}):
        try:
            from .. import arrivals
            if arr["fix"] in [p for p in ac.route if not str(p).startswith("@")]:
                arr.update(arrivals.compute(w, ac))
        except Exception:  # noqa: BLE001
            pass
    return arr


def _moving(a: dict) -> bool:
    return abs(a.get("vertical_speed_fpm") or 0) > 200 or (a.get("cleared_fl") or a["fl"]) != a["fl"]


def _entered_t(w, cs: str) -> float:
    ac = w.aircraft.get(cs)
    t = getattr(ac, "first_contact_t", None)
    return float(t) if t is not None else 1e9


def priority(A: dict, B: dict, w=None) -> Tuple[Optional[str], str]:
    """Which aircraft to move. An aircraft maintaining its level has priority in it over one climbing or descending;
    between two level aircraft, the one that entered the sector first keeps its level (first come). (None, reason)
    when they cannot be told apart."""
    ma, mb = _moving(A), _moving(B)
    if ma != mb:
        mv, keep = (A, B) if ma else (B, A)
        return mv["callsign"], f"{keep['callsign']} is level at FL{keep['fl']:03d} and keeps priority; {mv['callsign']} is changing level"
    if w is not None:
        ta, tb = _entered_t(w, A["callsign"]), _entered_t(w, B["callsign"])
        if abs(ta - tb) >= 5:
            mv, keep = (B, A) if ta < tb else (A, B)
            return mv["callsign"], (f"{keep['callsign']} entered your sector first and keeps priority"
                                    f"{' in its level' if not ma else ''}; {mv['callsign']} entered after it")
    return None, "no priority between them (entered together)"


def solutions(w, X: dict, sep_s: float = 600) -> Dict[str, List[str]]:
    """Clearances for aircraft X that the probe finds conflict-free (no conflict in the next 10 min), in the order
    to prefer them: speed, then heading (then resume), then level (ask the pilot first)."""
    from .. import atctools
    from ..aircraft import level_parity_ok
    cs = X["callsign"]
    ac = w.aircraft.get(cs)
    out: Dict[str, List[str]] = {"speed": [], "heading": [], "level": []}
    if ac is None:
        return out

    def free(**kw) -> bool:
        try:
            c = atctools.probe(w, cs, **kw).get("conflicts") or []
        except Exception:  # noqa: BLE001
            return False
        return not [x for x in c if x["time_to_conflict_s"] <= sep_s]
    # a speed takes time to act (the aircraft accelerates or slows a few knots a second) and a turn takes a minute:
    # an option counts only if it still works with part of its effect (a margin), and only when there is time
    soon = min([c["time_to_conflict_s"] for c in atctools.mtcd(w) if cs in c["aircraft"]] or [1e9])
    mode_n, val_n = ac.normal_speed()                 # within ±0.03 Mach / ±20 kt of its own speed
    if mode_n == "mach" and soon >= 180:
        m0 = round(val_n, 2)
        for d in (-0.02, -0.03, +0.02, +0.03):
            m = round(m0 + d, 2)
            if m <= 0.86 and free(speed=f"M{m0 + 0.6 * d:.3f}"):
                out["speed"].append(f"M{int(round(m * 100))}")
                if len(out["speed"]) == 2:
                    break
    elif mode_n != "mach" and soon >= 180:
        v0 = int(round(val_n / 10) * 10)
        for d in (-10, -20, +10, +20):
            v = v0 + d
            if 210 <= v <= 340 and free(speed=str(v0 + 0.6 * d)):
                out["speed"].append(str(v))
                if len(out["speed"]) == 2:
                    break
    trk = int(X["track_deg"])
    for d in (20, -20, 30, -30, 45, -45, 60, -60):
        h = (trk + d) % 360 or 360
        if free(heading=h) and free(heading=(trk + 0.7 * d) % 360 or 360):
            out["heading"].append(f"{h:03d}")
            if len(out["heading"]) == 2:
                break
    base = int(X.get("cleared_fl") or X["fl"])
    for d in (20, -20, 40, -40, 10, -10):
        fl = base + d
        down_arr = fl * 100 < ac.alt_ft - 300 and w.descending_to_destination(ac, fl)
        if 100 <= fl <= w.upper_fl and (down_arr or level_parity_ok(fl, ac.southbound)) and free(cfl=fl):
            out["level"].append(str(fl))
            if len(out["level"]) == 2:
                break
    return out


def speed_fix(w, A: dict, B: dict, common: str, ea: float, eb: float, sep_h: float) -> Optional[str]:
    """In trail to a common point: the speed that gives the spacing there (lead faster / follower slower)."""
    lead, fol, el, ef = (A, B, ea, eb) if ea <= eb else (B, A, eb, ea)
    gs_f = fol.get("gs_kt") or 0
    if gs_f < 150:
        return None
    need = (sep_h + 2) / gs_f * 3600 - (ef - el)          # seconds still missing at the point (2 NM margin)
    if need <= 0:
        return f"in trail via {common}: {fol['callsign']} {abs(ef - el) * gs_f / 3600:.0f} NM behind {lead['callsign']} there — enough, keep the speeds"
    D = ef * gs_f / 3600                                   # follower's distance to the point
    if D < 5:
        return None
    gs_new = D / (D / gs_f + need / 3600)
    d_gs = gs_f - gs_new
    m = fol.get("mach") or 0
    tas = max(1.0, (fol.get("tas_kt") or gs_f))
    d_m = d_gs * (m / tas) if m else 0
    if fol.get("speed_mode") == "mach" and m:
        new_m = math.floor((m - d_m) * 100) / 100
        if m - new_m <= 0.06 and new_m >= 0.70:
            return (f"speed works: {fol['callsign']} Mach {new_m:.2f} (now {m:.2f}) gives {sep_h + 2:g} NM behind {lead['callsign']} at {common}"
                    f"; or {lead['callsign']} faster")
        return f"speed alone is not enough ({fol['callsign']} would need Mach {m - d_m:.2f}): a level change, or speed and a short vector"
    ias = fol.get("ias_kt") or 0
    if ias:
        new_ias = int(round((ias - d_gs * ias / tas) / 10) * 10)
        if ias - new_ias <= 40 and new_ias >= 250:
            return f"speed works: {fol['callsign']} {new_ias} kt IAS (now {ias}) gives {sep_h + 2:g} NM behind {lead['callsign']} at {common}"
    return f"speed alone is not enough: a level change, or speed and a short vector"


def _free_levels(w, a: dict) -> List[str]:
    """Conflict-free levels near the aircraft's (the probe), with the direction rule."""
    from .. import atctools
    from ..aircraft import level_parity_ok
    ac = w.aircraft.get(a["callsign"])
    base = int(a.get("cleared_fl") or a["fl"])
    cand = [base + d for d in (10, -10, 20, -20, 30, -30, 40, -40) if 100 <= base + d <= w.upper_fl]
    try:
        p = atctools.probe(w, a["callsign"], levels=cand)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for fl in cand:
        down_arr = ac is not None and fl * 100 < ac.alt_ft - 300 and w.descending_to_destination(ac, fl)
        if not p["levels"].get(str(fl)) and (ac is None or down_arr or level_parity_ok(fl, ac.southbound)):
            out.append(f"{fl}")
    if not out:
        out = regain(w, a["callsign"], cand)
    return out[:4]


def regain(w, cs: str, cand: List[int]) -> List[str]:
    """In a loss of separation (every level 'conflicts now'): the levels that give separation back soonest."""
    from .. import atctools
    from ..aircraft import level_parity_ok
    ac = w.aircraft.get(cs)
    try:
        p = atctools.probe(w, cs, levels=cand)["levels"]
    except Exception:  # noqa: BLE001
        return []
    ends = []
    for fl in cand:
        cl = p.get(str(fl)) or []
        if cl and all(c["in_s"] <= 1 for c in cl):
            full = atctools.probe(w, cs, cfl=fl).get("conflicts") or []
            if full and all(c["time_to_conflict_s"] <= 1 for c in full):
                end = max(c["time_to_conflict_s"] + c.get("duration_s", 0) for c in full)
                ends.append((end, abs(fl * 100 - (ac.alt_ft if ac else 0)), fl))
    ends.sort()
    return [f"{fl} (separated again in {_t(e)}{'' if ac is None or level_parity_ok(fl, ac.southbound) else ', wrong direction'})" for e, _, fl in ends[:3]]


def observe(w, opts: Dict = None) -> Tuple[str, dict]:
    from .. import atctools
    o = dict(DEFAULTS, **(opts or {}))
    st = w.state()
    sep_h = st.get("separation_minimum_nm") or 5
    coord, entries = st.get("coord") or {}, st.get("entries") or {}
    acs = [a for a in st["aircraft"] if a.get("status") == "active"]
    by_cs = {a["callsign"]: a for a in acs}
    mine = [a for a in acs if a.get("assumed")]
    calling = [a for a in acs if a.get("on_frequency") and not a.get("assumed")]
    gone = [a for a in st.get("transferred") or [] if a.get("assumed")]
    reqs = [r for r in st.get("pending_requests") or [] if r.get("kind") != "checkin"]
    last = _last_instructions(w)
    hints = bool(o.get("hints"))
    struct = {"t": round(w.t, 1), "mine": [], "calling": [], "requests": [], "conflicts": [], "losses": [], "to_decontrol": [],
              "todo": {}}

    # what the probe says: resuming the aircraft on a heading, the levels requested
    resume = {}
    if hints:
        for a in mine:
            if a.get("nav_mode") == "heading" and "heading" not in (a.get("clearance_limits") or {}):
                ah = _ahead(w, a["callsign"])                # (with a limit, the pilot resumes at it)
                if ah:
                    try:
                        c = atctools.probe(w, a["callsign"], direct=ah[0]).get("conflicts") or []
                    except Exception:  # noqa: BLE001
                        c = []
                    c = [x for x in c if x["time_to_conflict_s"] <= SOLVE_S]
                    resume[a["callsign"]] = {"ok": not c, "with": c and [y for y in c[0]["aircraft"] if y != a["callsign"]][0],
                                             "in_s": c and c[0]["time_to_conflict_s"]}
                    if c:                                   # a level that would let it resume
                        from ..aircraft import level_parity_ok
                        acx = w.aircraft[a["callsign"]]
                        base = int(a.get("cleared_fl") or a["fl"])
                        for d in (20, -20, 40, -40, 10, -10):
                            fl = base + d
                            if not (100 <= fl <= w.upper_fl) or not level_parity_ok(fl, acx.southbound):
                                continue
                            try:
                                cc = atctools.probe(w, a["callsign"], cfl=fl, direct=ah[0]).get("conflicts") or []
                            except Exception:  # noqa: BLE001
                                cc = []
                            if not [x for x in cc if x["time_to_conflict_s"] <= SOLVE_S]:
                                resume[a["callsign"]]["levels"] = [fl]
                                break
    solver = {}
    if hints and o.get("solver", True):
        try:
            from ..solve import resolve
            res = resolve.solve(w, asked=o.get("_asked") or {}, rollout=1)
            for st_ in res["sets"]:
                solver["×".join(sorted(st_["pair"])) + ("@exit" if st_.get("kind") == "exit" else "")] = st_
        except Exception:  # noqa: BLE001 - the hints are a help, the situation goes on without them
            solver = {}
    struct["solver"] = {k: {"optimal": [x.public() for x in v["optimal"]], "solutions": [x.public() for x in v["solutions"][:8]],
                            "move": v["move"]} for k, v in solver.items()}
    cf = []
    if o.get("conflicts"):
        relevant = {a["callsign"] for a in mine + calling}
        cf = [c for c in atctools.mtcd(w) if set(c["aircraft"]) & relevant][:12]

    # ---- TO DO NOW
    lines = [f"TIME T+{_t(w.t)} (exercise {_t(w.duration_s)}), UTC {st.get('utc', '')}",
             f"SEPARATION {sep_h:g} NM or 1000 ft (2000 ft with a non-RVSM aircraft in FL290-410, and above FL410). "
             f"Levels you can assign: FL100-FL{w.upper_fl}."]
    todo = []
    los_now = st.get("current_losses") or []
    if los_now:
        todo.append("LOSS OF SEPARATION NOW, act first (a level change, vertical speed matters): " + ", ".join(
            "×".join(p.get("aircraft", [])) for p in los_now))
    if calling:
        todo.append("ASSUME (they are calling you; answer them all now): " + ", ".join(
            f"{a['callsign']} (waiting {_t(w.t - (getattr(w.aircraft.get(a['callsign']), 'first_contact_t', None) or w.t))})" for a in calling))
    if gone:
        todo.append("TRANSFER = decontrol (they have left your sector): " + ", ".join(a["callsign"] for a in gone))
    if reqs:
        todo.append("ANSWER the pilot requests: " + ", ".join(r["id"] for r in reqs))
    urgent = [c for c in cf if c["time_to_conflict_s"] <= SOLVE_S and not c.get("current")]
    if urgent:
        seen = o.get("_seen") or {}

        def since(c):
            k = "×".join(sorted(c["aircraft"]))
            return f", shown since T+{_t(seen[k])} and still unsolved" if k in seen and w.t - seen[k] >= 25 else ""
        todo.append("SOLVE the conflicts NOW, while there is time (do not wait for them to get close): " + ", ".join(
            f"{c['aircraft'][0]}×{c['aircraft'][1]} in {_t(c['time_to_conflict_s'])}{since(c)}" for c in urgent))
    exq = [v for k, v in solver.items() if k.endswith("@exit")]
    if exq:
        todo.append("SPACE at the exit (letter of agreement): " + "; ".join(
            f"{v['pair'][1]} {v['d_min']} NM behind {v['pair'][0]} at {v['exit']} (needs {v['required_nm']:g})" for v in exq))
    seq_rows, seq_todo = [], []
    if getattr(w, "seq", None):
        from .. import arrivals
        mine_cs = {a["callsign"] for a in mine + calling}
        for f in arrivals.sequence_status(w):
            rows = [r for r in f.get("inbound") or [] if r.get("status") != "not yet visible"]
            if not rows:
                continue
            seq_rows.append(f)
            for r in rows:
                if r["eta_s"] - w.t > 900:                   # sequencing starts about 15 minutes from the fix
                    continue
                if r["callsign"] in mine_cs and r.get("spacing_ok") is False:
                    seq_todo.append(f"{r['callsign']} only {r['spacing_to_preceding_nm']:.0f} NM behind the one ahead at {f['fix']} "
                                    f"(needs {f['spacing_nm']:g})")
                ld = r.get("loa_latest_descent_in_nm")
                tgt = f.get("cross_at_fl")
                if r["callsign"] in mine_cs and tgt and ld is not None and ld < 20 and (r.get("cleared_fl") or 0) > tgt:
                    seq_todo.append(f"{r['callsign']} must descend to FL{tgt} for {f['fix']} (latest descent in {max(0, ld):.0f} NM)")
    if seq_todo:
        todo.append("SEQUENCE the arrivals: " + "; ".join(seq_todo))
    # requests refused earlier that nothing stands against any more: to be given now (the pilot does not ask again)
    if hints:
        try:
            from . import actions as act_mod
            from ..aircraft import level_parity_ok
            from ..solve import resolve as _rs
            pend = {r["callsign"] for r in st.get("pending_requests") or []}
            acc = []
            for q in act_mod.refused_requests(w):
                acx = w.aircraft[q.callsign]
                if q.callsign in pend:
                    continue
                if q.type == "altitude":
                    fl = act_mod._fl(q.value)
                    clr, txt = {"cfl": fl}, f"FL{fl}"
                    if not (level_parity_ok(fl, acx.southbound) or (fl * 100 < acx.alt_ft and w.descending_to_destination(acx, fl))):
                        continue
                    instr = {"type": "altitude", "fl": fl}
                    probe_kw = {"cfl": fl}
                else:
                    clr, txt = {"direct": str(q.value).upper()}, f"direct {str(q.value).upper()}"
                    instr = {"type": "direct", "waypoint": clr["direct"]}
                    probe_kw = {"direct": clr["direct"]}
                if not act_mod._flyable_now(w, acx, instr) or (atctools.probe(w, q.callsign, **probe_kw).get("conflicts") or []):
                    continue
                if not _rs.positive(w, q.callsign, _rs.clearance_params(w, acx, clr))["positive"]:
                    continue
                t_ = q.resolved_time or q.time
                acc.append(f"{q.callsign} {txt} (its request {q.id}, refused at T+{_t(t_)}: nothing stands against it now)")
            if acc:
                todo.append("ACCOMMODATE the requests you refused earlier, now possible (give them without waiting to be asked again): " + "; ".join(acc))
                struct["accommodate"] = acc
        except Exception:  # noqa: BLE001
            pass
    rs = [cs for cs, r in resume.items() if r["ok"]]
    if rs:
        todo.append("RESUME (on a heading, now clear of conflict): " + ", ".join(rs))
    hold = [cs for cs, r in resume.items() if not r["ok"]]
    if hold:
        todo.append("WATCH (on a heading, not clear yet: keep the heading or solve otherwise, then resume): " + ", ".join(hold))
    if o.get("todo"):
        lines.append("\nTO DO NOW:")
        lines += ["- " + x for x in todo] or ["- nothing pending: check the conflicts and the aircraft on headings"]
    struct["todo"] = {"assume": [a["callsign"] for a in calling], "transfer": [a["callsign"] for a in gone],
                      "answer": [r["id"] for r in reqs], "solve": [c["aircraft"] for c in urgent], "resume": rs, "hold": hold}

    # ---- the aircraft
    lines.append(f"\nYOUR AIRCRAFT (assumed) — {len(mine)}:")
    for a in mine[:o["max_aircraft"]]:
        txt, s = _ac_line(w, a, entries.get(a["callsign"], {}), coord.get(a["callsign"], {}), o, last.get(a["callsign"]),
                          resume.get(a["callsign"]))
        lines.append("- " + txt)
        struct["mine"].append(s)
    if not mine:
        lines.append("- none")
    lines.append(f"\nCALLING YOU (first call on your frequency, waiting for you: answer with assume) — {len(calling)}:")
    for a in calling[:o["max_aircraft"]]:
        txt, s = _ac_line(w, a, {}, coord.get(a["callsign"], {}), o, None, None)
        ac = w.aircraft.get(a["callsign"])
        fc = getattr(ac, "first_contact_t", None)
        if fc is not None:
            txt += f" · called {_t(w.t - fc)} ago"
            s["waiting_s"] = round(w.t - fc)
        lines.append("- " + txt)
        struct["calling"].append(s)
    if not calling:
        lines.append("- none")
    if gone:
        lines.append(f"\nLEFT YOUR SECTOR, still assumed (transfer them: decontrol) — {len(gone)}:")
        for a in gone[:o["max_aircraft"]]:
            ac = w.aircraft.get(a["callsign"])
            out_t = getattr(ac, "ghost_until", None)
            since = None if out_t is None or out_t < 0 else max(0.0, w.t - (out_t - w.persist_s))
            lines.append(f"- {a['callsign']} FL{a['fl']:03d}" + (f", left {_t(since)} ago" if since is not None else ""))
            struct["to_decontrol"].append({"callsign": a["callsign"], "since_s": None if since is None else round(since)})
    if o.get("inbound"):
        inb = st.get("inbound") or []
        if inb:
            lines.append(f"\nINBOUND (not on frequency yet: plan for them, you cannot instruct them) — {len(inb)}:")
            for i in sorted(inb, key=lambda i: i.get("time_to_entry_s") or 0)[:12]:
                lines.append(f"- {i['callsign']} {i.get('type', '')} FL{i.get('fl', 0):03d} entering at {i.get('entry_waypoint')}"
                             f" in {_t(i.get('time_to_entry_s') or 0)}, route {' '.join(i.get('route') or [])}"
                             + (f" to {i['destination']}" if i.get("destination") else ""))

    # ---- arrival sequences
    if seq_rows:
        lines.append("\nARRIVAL SEQUENCES (in order at each fix; act on the spacing only within about 15 minutes of the fix):")
        for f in seq_rows:
            rows = [r for r in f.get("inbound") or [] if r.get("status") != "not yet visible"]
            lines.append(f"- {f['fix']} for {f['aerodrome']} ({f.get('letter_of_agreement') or ''}): " + "; ".join(
                f"{r['callsign']} FL{r.get('fl', 0):03d} at the fix in {_t(r['eta_s'] - w.t)}"
                + (f", {r['spacing_to_preceding_nm']:.0f} NM behind the one ahead" if r.get("spacing_to_preceding_nm") is not None else "")
                + (" (TOO CLOSE)" if r.get("spacing_ok") is False else "")
                + ("" if r.get("status") == "on frequency" else " (not on your frequency yet)") for r in rows))
            struct.setdefault("sequences", []).append({"fix": f["fix"], "order": [r["callsign"] for r in rows],
                                                       "too_close": [r["callsign"] for r in rows if r.get("spacing_ok") is False]})

    # ---- requests
    lines.append(f"\nPILOT REQUESTS waiting for your answer — {len(reqs)}:")
    for r in reqs:
        txt = f"- {r['id']}: {r['phraseology']}" + (f" ({r['reason']})" if r.get("reason") else "") + f", waiting {_t(w.t - r['time_s'])}"
        if hints and r.get("type") in ("altitude", "climb", "descent") and str(r.get("value", "")).upper().lstrip("F").lstrip("L").isdigit():
            fl = int(str(r["value"]).upper().lstrip("F").lstrip("L"))
            try:
                c = atctools.probe(w, r["callsign"], levels=[fl])["levels"].get(str(fl)) or []
                txt += " · probe: " + ("no conflict at FL%03d" % fl if not c else f"conflict with {c[0]['with']} in {_t(c[0]['in_s'])}")
            except Exception:  # noqa: BLE001
                pass
        if hints and o.get("solver", True) and r.get("type") in ("altitude", "direct") and r.get("value"):
            # with the solver: the request as asked if separated, else with a speed / vector that makes it so
            try:
                from ..solve import resolve as _rs
                v = str(r["value"]).upper()
                req = {"cfl": int(v.lstrip("F").lstrip("L"))} if r["type"] == "altitude" else {"direct": v}
                sol = _rs.accommodate(w, r["callsign"], req, o.get("_asked") or {})
                if sol is None:
                    txt += " · SOLVER: nothing makes it separated now: unable (give it later when possible)"
                elif len(sol["actions"]) > 1 or set(sol["actions"][0]) - {"type", "callsign", "cfl", "direct"}:
                    txt += " · SOLVER: approve WITH " + "; ".join(json.dumps(x) for x in sol["actions"])
                else:
                    txt += " · SOLVER: separated as asked: approve"
            except Exception:  # noqa: BLE001
                pass
        lines.append(txt)
        struct["requests"].append({"id": r["id"], "callsign": r["callsign"], "type": r["type"], "value": r.get("value"),
                                   "reason": r.get("reason"), "age_s": round(w.t - r["time_s"])})
    if not reqs:
        lines.append("- none")

    # ---- conflicts, with their geometry
    if o.get("conflicts"):
        lines.append(f"\nPREDICTED CONFLICTS (system trajectories as cleared now, next {int(atctools.look_ahead(w) // 60)} min) — {len(cf)}:")
        for c in cf:
            a, b = c["aircraft"]
            A, B = by_cs.get(a), by_cs.get(b)
            txt = (f"- {a} × {b}: {'NOW' if c.get('current') else 'in ' + _t(c['time_to_conflict_s'])}, closest {c['min_horizontal_nm']} NM / "
                   f"{c['vertical_ft']} ft (levels {c['levels'][0]}/{c['levels'][1]}; needs {c.get('required_horizontal_nm', sep_h):g} NM or "
                   f"{c.get('required_vertical_ft', 1000)} ft)")
            geo = {}
            if A and B:
                pa, pb = (A["x_nm"], A["y_nm"]), (B["x_nm"], B["y_nm"])
                ang = abs((A["track_deg"] - B["track_deg"] + 180) % 360 - 180)
                kind = "same direction" if ang < 45 else "opposite" if ang > 135 else "crossing"
                txt += f"; now {_dist_xy(pa, pb):.0f} NM apart, {b} bears {_brg(pa, pb):03d} from {a}; tracks {int(A['track_deg']):03d}/{int(B['track_deg']):03d} ({kind}, {ang:.0f}°)"
                common = next((p for p in _ahead(w, a) if p in set(_ahead(w, b))), None)
                if common:
                    ea, eb = _eta(w, A, common), _eta(w, B, common)
                    if ea is not None and eb is not None:
                        txt += f"; both route via {common}: {a} in {_t(ea)}, {b} in {_t(eb)} ({abs(ea - eb):.0f} s apart)"
                        geo["common"] = common
                if hints:
                    mv, why = priority(A, B, w)
                    txt += f"; PRIORITY: {why}" + (f" → move {mv}, not the other" if mv else "")
                    geo["move"] = mv
                sv_ = solver.get("×".join(sorted([a, b])))
                if hints and sv_ is not None and not c.get("current"):
                    opt = sv_["optimal"] or sv_["solutions"][:1]
                    rest = [x for x in sv_["solutions"] if x not in opt][:3]
                    if opt:
                        txt += ("; SOLVER (conflict-free with everyone, in the order speed, heading, level): BEST "
                                + " or ".join(x.text + (" (ask the pilot first)" if x.needs_ask else "") for x in opt[:3])
                                + (f"; other options: {'; '.join(x.text for x in rest)}" if rest else ""))
                    else:
                        txt += "; SOLVER: no single clearance leaves everyone separated: combine, or act on other traffic"
                    for m_, hv in (sv_.get("hj") or {}).items():
                        if not hv["turn_enough"]:
                            txt += f"; {m_}: no turn can separate it in time (only a level)"
                elif hints and not c.get("current"):
                    for X in ((by_cs.get(geo["move"]),) if geo.get("move") else (A, B)):
                        if X and X.get("assumed"):
                            so = solutions(w, X)
                            parts = []
                            if so["speed"]:
                                parts.append("1) speed " + " or ".join(so["speed"]))
                            if so["heading"]:
                                parts.append("2) heading " + " or ".join(so["heading"]) + " then resume")
                            if so["level"]:
                                parts.append("3) level " + " or ".join(so["level"]) + " (ask the pilot first)")
                            txt += (f"; SOLUTIONS for {X['callsign']} (conflict-free, in order of preference): "
                                    + ("; ".join(parts) if parts else "none found alone: combine, or move the other"))
                            geo["solutions_" + X["callsign"]] = so
                elif hints:
                    for X in (A, B):
                        if X and X.get("assumed"):
                            fl = _free_levels(w, X)
                            txt += f"; to regain separation fastest, {X['callsign']}: {', '.join(fl) if fl else 'no level'}"
            lines.append(txt)
            struct["conflicts"].append({"aircraft": [a, b], "in_s": c["time_to_conflict_s"], "min_nm": c["min_horizontal_nm"],
                                        "vertical_ft": c["vertical_ft"], **geo})
        if not cf:
            lines.append("- none")
        # spacing at the exit (letter of agreement): same exit, levels not separated vertically
        ex = [v for k, v in solver.items() if k.endswith("@exit")]
        if ex:
            lines.append(f"\nEXIT SPACING (same exit point, levels not vertically separated: {ex[0]['required_nm']:g} NM in trail at the exit, before transfer) — {len(ex)}:")
            for v in ex:
                a_, b_ = v["pair"]
                txt = (f"- {b_} will cross {v['exit']} only {v['d_min']} NM behind {a_} (in {_t(v['t_first'])}); {v['priority']}")
                opt = v["optimal"] or v["solutions"][:1]
                if hints:
                    txt += ("; SOLVER: BEST " + " or ".join(x.text for x in opt[:3])) if opt else "; SOLVER: nothing found alone"
                lines.append(txt)
                struct.setdefault("exit_spacing", []).append({"leader": a_, "follower": b_, "exit": v["exit"], "spacing_nm": v["d_min"],
                                                              "in_s": v["t_first"]})
    los = st.get("current_losses") or []
    if los:
        lines.append("\nLOSS OF SEPARATION NOW: " + "; ".join(" × ".join(p.get("aircraft", [])) for p in los))
        struct["losses"] = [p.get("aircraft") for p in los]
    areas = [a for a in st.get("areas") or [] if a.get("active") and a.get("relevant", True)]
    if areas:
        lines.append("\nACTIVE AREAS (keep traffic out, and 3 NM clear of a prohibited area, 5 NM of a danger or "
                     "restricted one): " + "; ".join(
            f"{a.get('id')} {a.get('kind', '')} FL{a.get('lower_fl', '')}-{a.get('upper_fl', '')}" for a in areas))
    return "\n".join(lines), struct
