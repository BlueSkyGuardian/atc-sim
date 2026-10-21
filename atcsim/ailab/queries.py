"""Questions the model may ask before it gives its instructions (the agent profile's "queries"): the simulator
answers them with the same tools the controller's radar uses (conflict probe, trajectories, sequencing), then the
model answers again. At most a few rounds per decision; the simulation waits meanwhile.

The model answers {"queries": [ ... ]} instead of instructions; each query is one of QUERY_HELP. answer(w, queries)
-> list of {"query", "ok", "result"} (result: short text).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

MAX_PER_ROUND = 8

QUERY_HELP = """QUERIES (optional): before giving instructions you may ask the simulator questions. Answer with
{"reasoning": "<why>", "queries": [<queries>]} (no actions in that answer); you get the answers, then you answer again
with your instructions (or more queries, up to {rounds} rounds per decision, {per} queries per round). The simulation waits.
  {"q": "probe", "callsign": "RAM123", "cfl": 370}           conflicts it would have with this clearance (any of cfl, heading, direct; together allowed)
  {"q": "probe", "callsign": "RAM123", "heading": 250}
  {"q": "levels", "callsign": "RAM123"}                     its conflict-free levels (with the direction rule)
  {"q": "geometry", "a": "RAM123", "b": "IBE45"}            distance and bearing now, tracks, closing speed, closest point of approach if both keep going straight
  {"q": "distance", "from": "RAM123", "to": "IBALU"}       distance and bearing between aircraft and/or points (and time, from an aircraft)
  {"q": "route", "callsign": "RAM123"}                      its route ahead: points with distances and times
  {"q": "near", "callsign": "RAM123", "radius_nm": 30}     aircraft within a radius of an aircraft (or "point": "IBALU"), with level and distance
  {"q": "sequence", "fix": "MARJA"}                         arrivals to that sequencing fix in order, spacing, the agreed crossing level
Ask only what helps the decision; most decisions need no query."""


def help_text(rounds: int) -> str:
    return QUERY_HELP.replace("{rounds}", str(rounds)).replace("{per}", str(MAX_PER_ROUND))


def _t(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _brg(p, q) -> int:
    return int(round(math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360)) or 360


def _pos(w, name: str) -> Tuple[Optional[tuple], Optional[object]]:
    n = str(name or "").upper()
    ac = w.aircraft.get(n)
    if ac is not None and (ac.active or ac.outside):
        return (ac.x, ac.y), ac
    if n in w.waypoints:
        p = w.waypoints[n]
        return (p[0], p[1]), None
    raise ValueError(f"unknown aircraft or point '{name}'")


def _ac(w, cs: str):
    ac = w.aircraft.get(str(cs or "").upper())
    if ac is None or not (ac.active or ac.outside):
        raise ValueError(f"unknown aircraft '{cs}'")
    return ac


def _conf_text(cs: str, cl: List[dict]) -> str:
    if not cl:
        return "no conflict predicted"
    return "; ".join(f"conflict with {[x for x in c['aircraft'] if x != cs][0]} in {_t(c['time_to_conflict_s'])}, "
                     f"closest {c['min_horizontal_nm']} NM / {c['vertical_ft']} ft" for c in cl[:4])


def _one(w, q: dict) -> str:
    from .. import arrivals, atctools
    from ..aircraft import level_parity_ok
    kind = str(q.get("q") or q.get("type") or "").lower()
    if kind == "probe":
        ac = _ac(w, q.get("callsign"))
        ov = {k: q[k] for k in ("cfl", "heading", "direct") if q.get(k) not in (None, "")}
        if not ov:
            raise ValueError("probe: give cfl, heading and/or direct")
        if "cfl" in ov:
            ov["cfl"] = int(ov["cfl"])
        if "heading" in ov:
            ov["heading"] = float(ov["heading"])
        if "direct" in ov:
            ov["direct"] = str(ov["direct"]).upper()
            if ov["direct"] not in w.waypoints:
                raise ValueError(f"unknown point {ov['direct']}")
        cl = atctools.probe(w, ac.callsign, **ov).get("conflicts") or []
        lab = " ".join(f"{k} {v}" for k, v in ov.items())
        extra = ""
        if "cfl" in ov and not level_parity_ok(ov["cfl"], ac.southbound):
            extra = f" (FL{ov['cfl']} is not a level of its direction)"
        return f"{ac.callsign} {lab}: {_conf_text(ac.callsign, cl)}{extra} (next {int(atctools.look_ahead(w) // 60)} min)"
    if kind == "levels":
        ac = _ac(w, q.get("callsign"))
        base = int(round((ac.cleared_alt_ft or ac.alt_ft) / 100))
        cand = [base + d for d in (10, -10, 20, -20, 30, -30, 40, -40, 50, -50, 60, -60) if 100 <= base + d <= w.upper_fl]
        p = atctools.probe(w, ac.callsign, levels=cand)["levels"]
        ok = [f"{fl}" for fl in cand if not p.get(str(fl)) and (level_parity_ok(fl, ac.southbound)
                                                                    or (fl * 100 < ac.alt_ft - 300 and w.descending_to_destination(ac, fl)))]
        if not ok:
            from .observe import regain
            rg = regain(w, ac.callsign, cand)
            if rg:
                return f"{ac.callsign} at FL{base}: in conflict at every level now; separation comes back soonest at " + ", ".join(rg)
        bad = [f"{fl}(conflict {p[str(fl)][0]['with']} in {_t(p[str(fl)][0]['in_s'])})" for fl in cand if p.get(str(fl))]
        return f"{ac.callsign} at FL{base}: conflict-free (of its direction) {', '.join(ok) or 'none'}; not free: {', '.join(bad) or 'none'}"
    if kind == "geometry":
        a, b = _ac(w, q.get("a") or q.get("callsign")), _ac(w, q.get("b") or q.get("other"))
        pa, pb = (a.x, a.y), (b.x, b.y)
        d = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        ta, tb = math.radians(a.trk), math.radians(b.trk)
        va = (a.gs * math.sin(ta) / 3600, a.gs * math.cos(ta) / 3600)
        vb = (b.gs * math.sin(tb) / 3600, b.gs * math.cos(tb) / 3600)
        rx, ry, vx, vy = pb[0] - pa[0], pb[1] - pa[1], vb[0] - va[0], vb[1] - va[1]
        vv = vx * vx + vy * vy
        tc = max(0.0, -(rx * vx + ry * vy) / vv) if vv > 1e-12 else 0.0
        dc = math.hypot(rx + vx * tc, ry + vy * tc)
        closing = -(rx * vx + ry * vy) / d * 3600 if d > 0 else 0.0
        ang = abs((math.degrees(ta) - math.degrees(tb) + 180) % 360 - 180)
        dv = abs(a.alt_ft - b.alt_ft)
        return (f"{a.callsign}×{b.callsign}: {d:.1f} NM apart, {b.callsign} bears {_brg(pa, pb):03d} from {a.callsign}; "
                f"tracks {math.degrees(ta) % 360:03.0f}/{math.degrees(tb) % 360:03.0f} ({ang:.0f}° apart); "
                f"{'closing' if closing > 0 else 'opening'} at {abs(closing):.0f} kt; vertical {dv:.0f} ft "
                f"(FL{a.alt_ft / 100:.0f}/FL{b.alt_ft / 100:.0f}); straight ahead: closest {dc:.1f} NM in {_t(tc)}")
    if kind == "distance":
        pa, aa = _pos(w, q.get("from"))
        pb, _ = _pos(w, q.get("to"))
        d = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        txt = f"{str(q.get('from')).upper()} to {str(q.get('to')).upper()}: {d:.1f} NM, bearing {_brg(pa, pb):03d}"
        if aa is not None and aa.gs > 50:
            txt += f", {_t(d / aa.gs * 3600)} at its groundspeed {aa.gs:.0f} kt (direct)"
        return txt
    if kind == "route":
        ac = _ac(w, q.get("callsign"))
        pts = [p for p in ac.route[ac.next_index:] if not str(p).startswith("@") and p in w.waypoints]
        pos, dsum, out = (ac.x, ac.y), 0.0, []
        for p in pts[:12]:
            qx = w.waypoints[p]
            dsum += math.hypot(qx[0] - pos[0], qx[1] - pos[1])
            pos = qx
            out.append(f"{p} {dsum:.0f}NM" + (f" {_t(dsum / ac.gs * 3600)}" if ac.gs > 50 else ""))
        mode = "own navigation" if ac.nav_mode == "lnav" else f"{ac.nav_mode} (distances are along the route once it is back on it)"
        return f"{ac.callsign} ({mode}): " + (", ".join(out) or "no point ahead")
    if kind == "near":
        if q.get("point"):
            c, _ = _pos(w, q["point"])
            ref = str(q["point"]).upper()
        else:
            ac = _ac(w, q.get("callsign"))
            c, ref = (ac.x, ac.y), ac.callsign
        r = float(q.get("radius_nm") or 30)
        rows = []
        for o in w.aircraft.values():
            if not o.active or o.callsign == ref:
                continue
            d = math.hypot(o.x - c[0], o.y - c[1])
            if d <= r:
                rows.append((d, f"{o.callsign} {d:.0f}NM brg {_brg(c, (o.x, o.y)):03d} FL{o.alt_ft / 100:.0f}"
                                f"{'' if o.assumed else ' (not yours)'}"))
        rows.sort()
        return f"within {r:.0f} NM of {ref}: " + (", ".join(x[1] for x in rows[:15]) or "nobody")
    if kind == "sequence":
        fix = str(q.get("fix") or "").upper()
        st = [f for f in arrivals.sequence_status(w) if not fix or f["fix"] == fix]
        if not st:
            raise ValueError(f"no sequencing fix {fix}" + (f" (fixes: {', '.join(f['fix'] for f in arrivals.sequence_status(w))})" if arrivals.sequence_status(w) else ""))
        out = []
        for f in st:
            inb = "; ".join(f"{i['callsign']} FL{i.get('fl', 0)} at the fix in {_t((i.get('eta_s') or 0) - w.t)}"
                            + (f", {i['spacing_to_preceding_nm']:.0f} NM behind the one ahead" if i.get("spacing_to_preceding_nm") is not None else "")
                            + ("" if i.get("spacing_ok", True) else " (TOO CLOSE)") for i in f.get("inbound") or [])
            out.append(f"{f['fix']} for {f['aerodrome']}: {f.get('letter_of_agreement') or ''}; in order: {inb or 'none'}")
        return " | ".join(out)
    raise ValueError(f"unknown query '{kind}' (probe, levels, geometry, distance, route, near, sequence)")


def answer(w, queries: List[dict]) -> List[dict]:
    out = []
    for q in queries[:MAX_PER_ROUND]:
        try:
            out.append({"query": q, "ok": True, "result": _one(w, q if isinstance(q, dict) else {})})
        except Exception as e:  # noqa: BLE001
            out.append({"query": q, "ok": False, "result": f"error: {e}"})
    for q in queries[MAX_PER_ROUND:]:
        out.append({"query": q, "ok": False, "result": f"not answered: more than {MAX_PER_ROUND} queries in one round"})
    return out


def as_text(results: List[dict]) -> str:
    import json
    return "\n".join(f"- {json.dumps(r['query'])} -> {r['result']}" for r in results)
