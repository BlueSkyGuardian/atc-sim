"""Bilateral level coordination between the West (WN, WS) and the Oceanic (ON, OS) sectors.

Every flight whose route goes from one to the other - the climbs to SUBAL / BEXAL (North America), IBALU - SAMAR,
and the traffic the other way - is coordinated: the sector it leaves gives the next one the aircraft, its level and
its requested final level, and asks what level it can take (OLDI RAP, or by phone); the next sector answers - the
level asked (ACP), another level (CDN) or no climb (RJC) - and the aircraft is given that level before the boundary.
The next sector then takes it at that level, and climbs it to its final level when its traffic allows.

Both seats play it:
* the aircraft comes from the simulated side: its RAP comes about REQ_S before the boundary (OLDI window, a
  flashing tag on the label); the trainee answers (POST /sessions/{id}/oldi/answer). No answer ANSWER_BY_S before
  the boundary: the other sector levels the aircraft off where it is (a task not handled).
* the aircraft goes to the simulated side: entering an exit level (XFL) on it sends the RAP; the simulated sector
  answers after a few tens of seconds from its traffic around the aircraft's way. A level other than the one
  asked replaces the XFL (red on the label, CDN tag) until the trainee enters it again (green). Handing it over at
  another level than the one agreed (or changed without any agreement) is counted.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional

WEST = ("WN", "WS")
OCEANIC = ("ON", "OS")


def side(unit) -> Optional[str]:
    return "west" if unit in WEST else "oceanic" if unit in OCEANIC else None


def _sides(ctl) -> set:
    return {side(u) for u in ctl} - {None}
REQ_S = 900.0              # West asks this long before the boundary
ANSWER_BY_S = 300.0        # no answer by then: West levels off
SIM_DELAY_S = (20.0, 55.0)
CHECK_NM = 15.0            # the simulated Oceanic keeps this much from other traffic at the level, ...
CHECK_S = 600.0            # ... within this much time along the way to the exit


def rfl_of(a) -> Optional[int]:
    if getattr(a, "rfl", None):
        return int(a.rfl)
    c = str((a.flight_plan or {}).get("cruise", "")).upper().lstrip("FL").lstrip("F")
    return int(c) if c.isdigit() else None


def _to(a) -> str:
    """Where it is going (for the messages): the first point after the boundary, else the exit."""
    return (a.route_after or [a.planned_exit])[0] if a.assumed else a.planned_exit


def _level(a) -> int:
    return int(round((a.cleared_alt_ft if a.active else a.fl * 100) / 100.0))


def _state(w) -> Dict[str, dict]:
    if not hasattr(w, "lvlreq"):
        w.lvlreq = {}
    return w.lvlreq


def _m(w, k, inc=1):
    w.m[k] = w.m.get(k, 0) + inc


def _send(w, kind, a, direction, unit, level, note):
    """An OLDI message about the aircraft's boundary: its exit when it is ours, its entry when it is coming."""
    from . import oldi
    tte = oldi.time_to_exit(w, a) if a.assumed else oldi.time_to_entry(w, a)
    point = oldi.cop(w, a, "exit" if a.assumed else "entry")
    return oldi._send(w, kind, a, direction, unit, point, tte or 0.0, int(level), note=note)


# ------------------------------------------------------------------------------------------- simulated Oceanic
def _path(w, a, horizon_s: float, step_s: float = 60.0) -> List[tuple]:
    """(t, x, y, fl) along the aircraft's route from now (straight legs at its ground speed)."""
    wps = w.waypoints
    names = [n for n in (a.route[a.next_index:] if a.active else a.route) if n in wps] + [n for n in a.route_after if n in wps]
    if a.active:
        pos, t0 = a.pos, 0.0
    else:
        if not names:
            return []
        pos, t0 = wps[names[0]], max(0.0, getattr(a, "boundary_time", w.t) - w.t)
    gs = max(a.gs if a.active and a.gs > 100 else 450.0, 200.0) / 3600.0
    out, t, p = [], t0, pos
    for n in names:
        q = wps[n]
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        k = 0.0
        while k < L and t <= horizon_s:
            out.append((t, p[0] + (q[0] - p[0]) * k / max(L, 1e-6), p[1] + (q[1] - p[1]) * k / max(L, 1e-6)))
            k += gs * step_s
            t += step_s
        p = q
        if t > horizon_s:
            break
    return out


def _busy(w, a, fl: int, horizon_s: float) -> bool:
    """Other traffic at that level near the aircraft's way to the exit, at about the same time."""
    mine = _path(w, a, horizon_s)
    if not mine:
        return False
    for b in w.aircraft.values():
        if b is a or not (b.active or b.status == "scheduled"):
            continue
        levels = {_level(b), int(round(b.alt_ft / 100.0))} if b.active else {int(b.fl)}
        if all(abs(v - fl) >= 10 for v in levels):
            continue
        theirs = {int(t // 60): (x, y) for t, x, y in _path(w, b, horizon_s)}
        for t, x, y in mine:
            for dt in range(-int(CHECK_S // 60), int(CHECK_S // 60) + 1, 2):
                q = theirs.get(int(t // 60) + dt)
                if q and math.hypot(q[0] - x, q[1] - y) < CHECK_NM:
                    return True
    return False


def sim_answer(w, a, asked: int) -> Optional[int]:
    """The level the simulated Oceanic gives: the one asked if free, else the nearest free level of the right
    direction below (then above) it, within the aircraft's performance; None: no climb (RJC)."""
    from .aircraft import level_parity_ok
    top = min(asked + 40, a.max_fl() if hasattr(a, "max_fl") else 410)
    # the levels of the right direction around the one asked, nearest first (below before above)
    cands = [fl for fl in range(max(100, asked - 60), top + 1, 10) if fl < 290 or level_parity_ok(fl, a.southbound)]
    cands.sort(key=lambda fl: (abs(fl - asked), fl > asked))
    horizon = (oldi_tte(w, a) or 0.0) + 1800.0
    for fl in cands[:6]:
        if not _busy(w, a, fl, horizon):
            return fl
    return None


def oldi_tte(w, a):
    from . import oldi
    return oldi.time_to_exit(w, a) if a.assumed else oldi.time_to_entry(w, a)


# ------------------------------------------------------------------------------------------- answers
def answer(w, cs: str, level: Optional[int], by: str = "") -> dict:
    """The trainee on Oceanic answers West's level request: the level given, or None for no climb (RJC)."""
    cs = cs.upper()
    st = _state(w).get(cs)
    if not st or st["dir"] != "in":
        raise ValueError(f"no level request for {cs}")
    if st["state"] != "pending":
        raise ValueError(f"{cs}: the level request was already {st['state']}")
    a = w.aircraft[cs]
    kind = "RJC" if level is None else ("ACP" if int(level) == st["rfl"] else "CDN")
    _send(w, kind, a, "out", a.prev_unit, level if level is not None else _level(a),
          {"ACP": f"requested F{st['rfl']:03d} accepted", "CDN": f"F{int(level):03d} instead of F{st['rfl']:03d}",
           "RJC": "no climb"}[kind])
    st.update(state="answered", kind=kind, level=None if level is None else int(level), t_answer=w.t)
    _m(w, "lvlreq_answered")
    w.m["lvlreq_answer_s"] = w.m.get("lvlreq_answer_s", 0.0) + (w.t - st["t"])
    if level is not None and int(level) > _level(a):
        _climb(a, int(level))
    w._event("level_coordination", callsign=cs, kind=kind, level=level, by=by,
             text=f"{cs}: {kind} {'F%03d' % int(level) if level is not None else 'no climb'} to {a.prev_unit}"
                  f" (requested F{st['rfl']:03d})")
    return {"callsign": cs, "kind": kind, "level": level}


def _climb(a, fl: int) -> None:
    """The adjacent (simulated) West climbs the aircraft to the level agreed."""
    if a.status == "scheduled":
        a.fl = int(fl)
        a.initial_cfl = None
    else:
        a.cleared_alt_ft = fl * 100.0


# ------------------------------------------------------------------------------------------- the loop
def update(w) -> None:
    """Every second of the recorded world."""
    ctl = set(getattr(w.airspace, "controlled", []) or [])
    mine = _sides(ctl)
    if not mine or mine == {"west", "oceanic"}:
        return                                         # neither, or both band-boxed: nothing to coordinate
    S = _state(w)
    if not hasattr(w, "_lvl_t0"):
        w._lvl_t0 = w.t                                # flights already close at the start: coordinated before it
    for a in w.aircraft.values():
        cs = a.callsign
        if a.status in ("exited", "left_sector") or not (a.active or a.status == "scheduled" or a.outside):
            continue
        st = S.get(cs)
        cur = _level(a)
        rfl = rfl_of(a) or cur
        asked = rfl if rfl > cur else cur
        # ---- coming from the simulated side
        ps = side(a.prev_unit)
        if ps and ps not in mine and not a.assumed and not a.entered:
            tte = oldi_tte(w, a)
            if tte is None:
                continue
            if st is None and tte <= REQ_S and w.t - w._lvl_t0 < 2.0:
                S[cs] = {"dir": "in", "state": "none", "rfl": asked, "t": w.t, "unit": a.prev_unit, "exit": _to(a)}
                continue
            if st is None and tte <= REQ_S:
                S[cs] = st = {"dir": "in", "state": "pending", "rfl": asked, "t": w.t, "entry": cur,
                              "unit": a.prev_unit, "exit": _to(a)}
                what = f"requested final F{asked:03d}, now F{cur:03d}" if asked > cur else f"at F{cur:03d}"
                _send(w, "RAP", a, "in", a.prev_unit, asked, f"level coordination: {what} - what level can you give?")
                _m(w, "lvlreq_received")
                w._event("level_request", callsign=cs, unit=a.prev_unit, rfl=asked, direction="in",
                         text=f"{a.prev_unit}: {cs} " + (f"requesting F{asked:03d}" if asked > cur else f"at F{cur:03d}")
                              + " - what level?")
            elif st and st["state"] == "pending" and tte <= ANSWER_BY_S:
                st.update(state="unanswered")
                _climb(a, cur)
                _m(w, "lvlreq_unanswered")
                w._event("level_request_unanswered", callsign=cs, unit=a.prev_unit,
                         text=f"{cs}: no answer to {a.prev_unit}'s level coordination - kept at F{cur:03d}")
        # ---- going to the simulated side: the request goes with the exit level entered
        ns = side(a.next_unit)
        if ns and ns not in mine and a.assumed and a.active:
            if st is None or st["dir"] != "out":
                st = None if (st and st["dir"] != "out") else st
                if cs in S and S[cs]["dir"] != "out":
                    S.pop(cs)
                if "entry_lvl" not in getattr(a, "__dict__", {}):
                    a.entry_lvl = cur
            x = w.entries.get(cs, {}).get("xfl")
            if st and st["state"] == "pending" and w.t >= st["due"]:
                lvl = sim_answer(w, a, st["asked"])
                kind = "RJC" if lvl is None else ("ACP" if lvl == st["asked"] else "CDN")
                given = lvl if lvl is not None else cur
                _send(w, kind, a, "in", a.next_unit, given,
                      {"ACP": f"F{given:03d} accepted", "CDN": f"F{given:03d} instead of F{st['asked']:03d}",
                       "RJC": f"no climb: keep F{given:03d}"}[kind])
                st.update(state="answered", kind=kind, level=given, acked=(kind == "ACP"), t_answer=w.t)
                if kind != "ACP":
                    w.entries.setdefault(cs, {})["xfl"] = given          # replaces the exit level (red until re-entered)
                w._event("level_coordination", callsign=cs, kind=kind, level=given, unit=a.next_unit,
                         text=f"{a.next_unit}: {cs} {kind} F{given:03d}" + (f" (asked F{st['asked']:03d})" if kind != "ACP" else ""))
            elif st and st["state"] == "answered" and st["kind"] != "ACP" and not st.get("acked"):
                if x is not None and int(x) == st["level"] and w.entries.get(cs, {}).get("_xfl_t", -1) >= st["t_answer"]:
                    st["acked"] = True                                     # re-entered by the controller: agreed
                elif x is not None and int(x) != st["level"]:
                    S.pop(cs, None)                                        # a new exit level: a new request
                    st = None
            if x is not None and (st is None or st["state"] == "none" or (st["state"] == "answered" and (st.get("acked") or st["kind"] == "ACP")
                                                 and int(x) != st["level"])):
                S[cs] = st = {"dir": "out", "state": "pending", "rfl": rfl, "asked": int(x), "t": w.t,
                              "due": w.t + random.Random(hash((cs, int(w.t)))).uniform(*SIM_DELAY_S),
                              "unit": a.next_unit, "exit": _to(a), "entry_lvl": getattr(a, "entry_lvl", cur)}
                _send(w, "RAP", a, "out", a.next_unit, int(x),
                      f"level coordination: F{int(x):03d}" + (f" (requested final F{rfl:03d})" if rfl > int(x) else ""))
                _m(w, "lvlreq_sent")
            elif st is None and cs not in S:
                S[cs] = {"dir": "out", "state": "none", "rfl": rfl, "t": w.t, "unit": a.next_unit, "exit": _to(a),
                         "entry_lvl": getattr(a, "entry_lvl", cur)}
    for cs, st in list(S.items()):
        a = w.aircraft.get(cs)
        if a is None or st["dir"] != "out" or st.get("checked"):
            continue
        if a.status == "exited" or a.outside:
            st["checked"] = True
            agreed = st.get("level") if st["state"] == "answered" and (st.get("acked") or st.get("kind") == "ACP") else None
            lvl = int(round(a.alt_ft / 100.0))
            changed = abs(lvl - st.get("entry_lvl", lvl)) >= 10          # climbed / descended while ours
            if (agreed is not None and abs(lvl - agreed) >= 10) or (agreed is None and changed):
                _m(w, "lvlreq_uncoordinated")
                w._event("level_coordination_bust", callsign=cs,
                         text=f"{cs} into {st['unit']} at F{lvl:03d}" + (f" (agreed F{agreed:03d})" if agreed else " without an agreed level"))


def view(w) -> Dict[str, dict]:
    """For the labels and the OLDI window."""
    out = {}
    for cs, st in _state(w).items():
        if st["state"] == "none":
            continue
        d = {"dir": st["dir"], "state": st["state"], "rfl": st["rfl"], "unit": st["unit"], "exit": st.get("exit")}
        for k in ("kind", "level", "asked", "acked"):
            if st.get(k) is not None:
                d[k] = st[k]
        if st["dir"] == "in" and st["state"] == "pending":
            a = w.aircraft.get(cs)
            tte = oldi_tte(w, a) if a else None
            if tte is not None:
                d["answer_within_s"] = max(0, round(tte - ANSWER_BY_S))
        out[cs] = d
    return out


def metrics(w) -> dict:
    m = w.m
    n = m.get("lvlreq_answered", 0)
    return {"level_requests_received": m.get("lvlreq_received", 0), "answered": n,
            "unanswered": m.get("lvlreq_unanswered", 0),
            "mean_answer_s": round(m.get("lvlreq_answer_s", 0.0) / n, 1) if n else None,
            "level_requests_sent": m.get("lvlreq_sent", 0),
            "into_next_sector_above_agreed": m.get("lvlreq_uncoordinated", 0)}
