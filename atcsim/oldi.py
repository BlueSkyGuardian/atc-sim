"""Three levels and OLDI coordination with the adjacent units.

* Current level: what the transponder gives (Mode C / Mode S altitude).
* Cleared level (CFL): what the controller entered (connected exercise), else the coordinated level; solo, the
  aircraft's own cleared level.
* Exit level (XFL): what the controller entered for the coordination with the next unit; empty = the cleared level.

OLDI (on-line data interchange) messages are sent automatically for every aircraft we control, to the unit it goes
to next, at the coordination point (the exit point) - and received from the previous unit for every aircraft coming:
* ABI (advance boundary information) ABI_S before the boundary, again if the exit level changes before the ACT;
* ACT (activation) ACT_S before the boundary: the coordination (point, estimate, level) is now binding;
* REV (revision) when the exit level, or the estimate by more than 3 minutes, changes after the ACT - but only up to
  REV_LIMIT_S before the boundary: closer than that the system refuses, and the coordination must be done by phone;
* LAM (logical acknowledgement) from the receiving system a couple of seconds after each message.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .aircraft import level_parity_ok
from .flightplan import hhmm

ABI_S = 1200.0
ACT_S = 600.0
REV_LIMIT_S = 180.0
ETO_REV_S = 180.0
LAM_DELAY_S = 2.0
MAX_MESSAGES = 2000


def settings(w) -> Tuple[float, float, float]:
    o = getattr(w, "oldi_cfg", None) or {}
    return float(o.get("abi_s", ABI_S)), float(o.get("act_s", ACT_S)), float(o.get("rev_limit_s", REV_LIMIT_S))


def cfl(w, a) -> int:
    from .atctools import _system_cfl
    return int(round(_system_cfl(w, a, w.entries.get(a.callsign, {})) / 100))


def xfl(w, a) -> Tuple[int, bool]:
    """(exit level, entered by the controller?)"""
    e = w.entries.get(a.callsign, {})
    if e.get("xfl") is not None:
        return int(e["xfl"]), True
    return cfl(w, a), False


def cop(w, a, side: str = "exit") -> str:
    """Coordination point: the exit (entry) point, or the nearest published point when it is a generated COP."""
    cops = getattr(w.airspace, "cop_points", set())
    p = a.planned_exit if side == "exit" else a.entry_wp
    if p not in cops:
        return p
    if side == "exit":
        nxt = [n for n in a.route_after if n not in cops]
        if nxt:
            return nxt[0]
        prev = [n for n in a.route if n not in cops and not n.startswith("@")]
        return prev[-1] if prev else p
    before = [n for n in a.route_before if n not in cops]
    return before[-1] if before else p


def time_to_exit(w, a) -> Optional[float]:
    nm = a.to_exit_nm(w.waypoints)
    if nm is None or a.gs < 50:
        return None
    return nm / a.gs * 3600.0


def time_to_entry(w, a) -> Optional[float]:
    if a.status == "scheduled":
        return a.boundary_time - w.t
    if a.active and not a.entered and a.entry_wp in w.waypoints and a.gs > 50:
        from .geometry import dist
        return dist(a.pos, w.waypoints[a.entry_wp]) / a.gs * 3600.0
    return None


def level_warning(w, a, fl: int) -> Optional[str]:
    """Exit level against the letter of agreement (arrivals) or the semicircular rule."""
    if a.destination and a.arr.get("fix") in w.seq:
        tgt = w.seq[a.arr["fix"]].target_fl
        if tgt and fl > tgt:
            return f"LoA {a.arr['fix']}: FL{tgt} or below"
        return None
    if fl >= 200 and not level_parity_ok(fl, a.southbound) and not w.descending_to_destination(a, fl):
        return f"FL{fl}: wrong direction for a {'south' if a.southbound else 'north'}bound flight"
    return None


def _send(w, kind: str, a, direction: str, unit: str, point: str, eto_s: float, fl: int, note: str = "") -> dict:
    fp = a.flight_plan or {}
    eto = hhmm(getattr(w, "start_utc_min", 720.0) + (w.t + eto_s) / 60.0)
    w._oldi_seq = getattr(w, "_oldi_seq", 0) + 1
    m = {"id": w._oldi_seq, "t": round(w.t, 1), "utc": hhmm(getattr(w, "start_utc_min", 720.0) + w.t / 60.0),
         "dir": direction, "type": kind, "callsign": a.callsign, "unit": unit, "point": point, "eto": eto,
         "level": fl, "ssr": fp.get("ssr"), "status": "sent", "note": note,
         "text": f"{kind} {a.callsign}/A{fp.get('ssr', '----')} {a.icao}/{fp.get('wake', '')} "
                 f"{fp.get('departure', '')}-{fp.get('destination', '')} {point} {eto} F{fl:03d}"}
    w.oldi.append(m)
    if len(w.oldi) > MAX_MESSAGES:
        w.oldi = w.oldi[-MAX_MESSAGES:]
    w._event("oldi", callsign=a.callsign, message=kind, direction=direction, unit=unit, point=point, eto=eto,
             level=fl, text=("→ " if direction == "out" else "← ") + f"{unit} " + m["text"])
    return m


def update(w) -> None:
    """Called every second of the real (recorded) world."""
    if not hasattr(w, "oldi"):
        w.oldi, w.coord, w.coord_in = [], {}, {}
    abi_s, act_s, rev_s = settings(w)
    for m in w.oldi[-60:]:                                   # the receiving system acknowledges
        if m["status"] == "sent" and w.t - m["t"] >= LAM_DELAY_S:
            m["status"] = "LAM"
    for a in w.aircraft.values():
        cs = a.callsign
        # ---- outbound: aircraft under our control, to the next unit
        if a.active and a.assumed and a.next_unit:
            c = w.coord.setdefault(cs, {"state": None, "level": None, "eto_s": None, "manual": None, "warn": None})
            if c["state"] == "transferred":
                c["state"] = None                            # taken back
            tte = time_to_exit(w, a)
            if tte is None:
                continue
            lvl, entered = xfl(w, a)
            c["warn"] = level_warning(w, a, lvl)
            point = cop(w, a)
            eto_abs = w.t + tte
            if c["state"] in (None, "abi") and tte <= act_s:
                _send(w, "ACT", a, "out", a.next_unit, point, tte, lvl)
                c.update(state="act", level=lvl, eto_s=eto_abs, manual=None, act_t=w.t)
            elif c["state"] is None and tte <= abi_s:
                _send(w, "ABI", a, "out", a.next_unit, point, tte, lvl)
                c.update(state="abi", level=lvl, eto_s=eto_abs)
            elif c["state"] == "abi" and lvl != c["level"]:
                _send(w, "ABI", a, "out", a.next_unit, point, tte, lvl, note="level changed")
                c.update(level=lvl, eto_s=eto_abs)
            elif c["state"] == "act" and w.entries.get(cs, {}).get("coord") == "phone":
                # coordinated by telephone: the system takes the level / estimate as agreed
                w.entries[cs].pop("coord", None)
                m = _send(w, "PHONE", a, "out", a.next_unit, point, tte, lvl, note="coordinated by telephone")
                m["status"] = "manual"
                c.update(level=lvl, eto_s=eto_abs, manual=None)
            elif c["state"] == "act":
                why = []
                if lvl != c["level"]:
                    why.append(f"F{c['level']:03d}→F{lvl:03d}")
                if abs(eto_abs - c["eto_s"]) > ETO_REV_S:
                    why.append("estimate " + hhmm(getattr(w, "start_utc_min", 720.0) + eto_abs / 60.0))
                if why:
                    if tte > rev_s:
                        _send(w, "REV", a, "out", a.next_unit, point, tte, lvl, note=", ".join(why))
                        c.update(level=lvl, eto_s=eto_abs, manual=None)
                    else:
                        c["manual"] = f"too close to {point} for a REV ({', '.join(why)}): coordinate by phone with {a.next_unit}"
                elif c.get("manual") and lvl == c["level"]:
                    c["manual"] = None
            c["xfl"], c["entered"], c["tte_s"] = lvl, entered, round(tte)
        elif cs in w.coord and (a.outside or a.released or not a.assumed):
            if w.coord[cs]["state"] not in (None, "transferred"):
                w.coord[cs]["state"] = "transferred"
        # ---- inbound: the previous unit coordinates the aircraft with us
        if a.prev_unit and not a.assumed and not a.entered and (a.status == "scheduled" or (a.active and not a.on_frequency)):
            tte = time_to_entry(w, a)
            if tte is None:
                continue
            ci = w.coord_in.setdefault(cs, {"abi": False, "act": False})
            lvl = int(round((a.cleared_alt_ft if a.active else a.fl * 100) / 100))
            point = cop(w, a, "entry")
            if not ci["act"] and tte <= act_s:
                _send(w, "ACT", a, "in", a.prev_unit, point, tte, lvl)
                ci.update(act=True, abi=True, level=lvl)
            elif not ci["abi"] and tte <= abi_s:
                _send(w, "ABI", a, "in", a.prev_unit, point, tte, lvl)
                ci.update(abi=True, level=lvl)


def coord_view(w) -> Dict[str, dict]:
    """Per aircraft on the picture: exit level and coordination state, for the labels."""
    out = {}
    for a in w.aircraft.values():
        if not (a.active or a.outside):
            continue
        c = getattr(w, "coord", {}).get(a.callsign)
        lvl, entered = xfl(w, a)
        d = {"cfl": cfl(w, a), "xfl": lvl, "xfl_entered": entered, "next_unit": a.next_unit,
             "state": c["state"] if c else None}
        if c:
            if c.get("level") is not None:
                d["coordinated"] = c["level"]
            if c.get("manual"):
                d["manual"] = c["manual"]
            if c.get("warn"):
                d["warn"] = c["warn"]
            if c.get("tte_s") is not None and c["state"] in (None, "abi"):
                d["act_in_s"] = max(0, round(c["tte_s"] - settings(w)[1]))
        e = w.entries.get(a.callsign, {})
        if e.get("route"):
            from .atctools import entered_route
            er = entered_route(w, a, e)
            if er is not None:
                d["route_ahead"] = er[0][:15]
        ci = getattr(w, "coord_in", {}).get(a.callsign)
        if ci and ci.get("level") is not None and not a.assumed:
            d["entry_level"] = ci["level"]
            d["in_state"] = "act" if ci.get("act") else "abi"
        out[a.callsign] = d
    return out


def messages(w, since: int = 0, callsign: Optional[str] = None) -> List[dict]:
    return [m for m in getattr(w, "oldi", []) if m["id"] > since and (not callsign or m["callsign"] == callsign.upper())]
