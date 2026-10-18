"""CPDLC (controller-pilot data link communications).

* Equipage: the aircraft's flight plan item 10 has a data link capability, J1 (ATN VDL mode 2) to J7 (FANS 1/A).
* Connection management: an equipped aircraft logs on to us (CM logon) LOGON_S before it enters, while the previous
  unit is still the current data authority (CDA); we become the CDA at the transfer of communication. The system
  sends NEXT DATA AUTHORITY (the next unit) at the OLDI activation, and the connection passes to it when the aircraft
  answers WILCO to CONTACT / MONITOR (the transfer), with END SERVICE. An aircraft decontrolled by voice has its
  connection transferred by the system.
* Dialogues: an uplink (one or more message elements) waits for the pilot's answer - WILCO / UNABLE / STANDBY for a
  clearance, ROGER for free text. WILCO makes the aircraft execute it and puts it in the system (cleared level,
  heading, direct, speed, route, weather deviation), like a label entry. A downlink request (level, direct, speed,
  weather deviation, free text) waits for the controller: a clearance, UNABLE, STANDBY or ROGER.
* In a connected exercise the pseudo-pilot answers; on the solo radar the simulated pilot answers by itself.

Message elements carry the ICAO message numbers (FANS 1/A numbering) where there is one.
The controller types them on the command line (parse()): with an aircraft selected,

  CL350 / C350 / FL350 / DES240 / 350   level (climb / descend / maintain from the present level), V1500 = rate
  H270, L270 / R270 (turn left / right)   heading         D ROLRO, DROLRO, ROLRO   direct
  M78, S280, K280 (on conversion), SN     speed           VIA IBALU TERTO          route
  DEV, DEVL20, DEVR                      weather deviation  RES                    resume own navigation
  CT [unit] [freq], MON [unit] [freq]    contact / monitor (default: the next unit and its frequency)
  APP (the request as asked), UNA, SBY, ROG, AFF, NEG      answers to a downlink
  TXT any words (or "any words")         free text        NDA GCCC, EOS            next data authority, end service
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .flightplan import hhmm

LOGON_S = 900.0          # logon this long before the boundary
LATE_S = 120.0           # an uplink unanswered this long is flagged
RECENT_S = 40.0          # closed dialogues stay in the state (labels) this long
AUTO_REPLY_S = (8.0, 25.0)   # solo radar: the simulated pilot answers after this long
MAX_MESSAGES = 3000
OWN_DEFAULT = {"address": "ATSU", "name": "CONTROL"}

RESPONSES_UP = {"UNA": ("UM0", "UNABLE"), "UNABLE": ("UM0", "UNABLE"), "SBY": ("UM1", "STANDBY"),
                "STANDBY": ("UM1", "STANDBY"), "ROG": ("UM3", "ROGER"), "ROGER": ("UM3", "ROGER"),
                "AFF": ("UM4", "AFFIRM"), "AFFIRM": ("UM4", "AFFIRM"), "NEG": ("UM5", "NEGATIVE"),
                "NEGATIVE": ("UM5", "NEGATIVE")}
RESPONSES_DOWN = {"WILCO": "DM0", "UNABLE": "DM1", "STANDBY": "DM2", "ROGER": "DM3", "AFFIRM": "DM4", "NEGATIVE": "DM5"}
REASONS = {"weather": ("DM65", "DUE TO WEATHER"), "performance": ("DM66", "DUE TO AIRCRAFT PERFORMANCE")}
_RANK = {"WU": 3, "AN": 2, "R": 1, "N": 0}


# ------------------------------------------------------------------ set-up
def equipped(a) -> bool:
    eq = str((a.flight_plan or {}).get("equipment") or getattr(a, "equipment", "") or "").split("/")[0]
    return bool(re.search(r"J[1-7]", eq))


def own(w) -> dict:
    cfg = (getattr(w.airspace, "notes", {}) or {}).get("cpdlc") or {}
    return {"address": cfg.get("address") or OWN_DEFAULT["address"],
            "name": cfg.get("name") or (getattr(w.airspace, "unit", "") or w.airspace.name).upper()}


def unit_info(w, unit: str) -> dict:
    """Name (for CONTACT / MONITOR), frequency and data link address of a unit or of one of our sectors."""
    u = str(unit or "").upper()
    for s in w.airspace.sectors:
        if s.id.upper() == u:
            return {"id": s.id, "name": f"{own(w)['name']} CONTROL", "freq": s.freq or "", "address": own(w)["address"],
                    "internal": True}
    cfg = ((getattr(w.airspace, "notes", {}) or {}).get("cpdlc") or {}).get("units") or {}
    d = cfg.get(u) or {}
    return {"id": u, "name": d.get("name") or f"{u} CONTROL", "freq": d.get("freq") or "", "address": d.get("address") or u,
            "internal": False}


def _init(w) -> None:
    if not hasattr(w, "cpdlc"):
        w.cpdlc = {}                         # callsign -> connection
        w.cpdlc_msgs = []                    # every message, in order
        w._cpdlc_seq = 0


def _utc(w, t: Optional[float] = None) -> str:
    t = w.t if t is None else t
    h = hhmm(getattr(w, "start_utc_min", 720.0) + t / 60.0)
    return f"{h[:2]}:{h[2:]}:{int(t) % 60:02d}"


def conn(w, cs: str) -> Optional[dict]:
    _init(w)
    return w.cpdlc.get(cs.upper())


# ------------------------------------------------------------------ messages
def _msg(w, cs: str, direction: str, elements: List[dict], ref: Optional[int] = None, by: str = "",
         request_id: Optional[str] = None, response: bool = False) -> dict:
    _init(w)
    w._cpdlc_seq += 1
    resp = "N" if response else max((e.get("resp", "N") for e in elements), key=lambda r: _RANK[r])
    m = {"id": w._cpdlc_seq, "t": round(w.t, 1), "utc": _utc(w), "callsign": cs, "dir": direction,
         "elements": elements, "text": " / ".join(e["text"] for e in elements), "resp": resp, "ref": ref,
         "status": "open" if resp != "N" else "closed", "by": by}
    if request_id:
        m["request_id"] = request_id
    w.cpdlc_msgs.append(m)
    if len(w.cpdlc_msgs) > MAX_MESSAGES:
        w.cpdlc_msgs = w.cpdlc_msgs[-MAX_MESSAGES:]
    w._event("cpdlc", callsign=cs, direction=direction, message_id=m["id"], ref=ref,
             text=("↑ " if direction == "up" else "↓ ") + f"{cs} " + m["text"])
    cnt = w.m.setdefault("cpdlc", {"uplinks": 0, "downlinks": 0, "unable": 0, "late": 0, "wilco_s": []})
    if not response:
        cnt["uplinks" if direction == "up" else "downlinks"] += 1
    return m


def get_msg(w, mid: int) -> Optional[dict]:
    _init(w)
    for m in reversed(w.cpdlc_msgs):
        if m["id"] == mid:
            return m
    return None


def _close(w, m: dict, status: str, t: Optional[float] = None) -> None:
    m["status"] = status
    m["closed_t"] = round(w.t if t is None else t, 1)


# ------------------------------------------------------------------ connection management
def update(w) -> None:
    """Called every second of the real (recorded) world: logons, data authority, timers, solo answers."""
    _init(w)
    from . import oldi
    oname = own(w)
    for a in w.aircraft.values():
        if a.status == "scheduled" and not equipped(a):
            continue
        if not equipped(a):
            continue
        cs = a.callsign
        c = w.cpdlc.get(cs)
        if c is None:
            if not (a.active or a.status == "scheduled"):
                continue
            tte = oldi.time_to_entry(w, a)
            at_start = a.active and a.on_frequency
            if not at_start and (tte is None or tte > LOGON_S):
                continue
            pu = unit_info(w, a.prev_unit) if a.prev_unit else None
            c = w.cpdlc[cs] = {"state": "logon", "logon_t": round(w.t, 1), "nda": None,
                               "cda": (f"{oname['address']} {pu['id']}" if pu and pu["internal"] else a.prev_unit or ""),
                               "address": oname["address"]}
            w._event("cpdlc", callsign=cs, text=f"{cs} logon {oname['address']}" + (f" (CDA {c['cda']})" if c["cda"] else ""))
        if c["state"] == "logon" and a.active and a.on_frequency:
            c.update(state="cda", cda=oname["address"], cda_t=round(w.t, 1))
            w._event("cpdlc", callsign=cs, text=f"{cs} CPDLC connection active with {oname['address']} (current data authority)")
        if c["state"] != "cda":
            continue
        # next data authority: sent by the system at the OLDI activation, for a unit outside our ATSU
        co = getattr(w, "coord", {}).get(cs) or {}
        if a.assumed and c.get("nda") is None and a.next_unit and co.get("state") == "act":
            nu = unit_info(w, a.next_unit)
            if not nu["internal"]:
                set_nda(w, cs, a.next_unit, by="system")
        # decontrolled without a CPDLC transfer, or gone: the system transfers / ends the connection
        if (a.released and not a.assumed) or a.outside and not a.assumed or a.gone or a.status in ("exited", "landed"):
            _transfer(w, a, c, by="system")
    # timers: late uplinks, downlink requests resolved some other way (on the frequency), solo answers
    for m in w.cpdlc_msgs[-200:]:
        if m["status"] not in ("open", "standby"):
            continue
        if m["dir"] == "up" and not m.get("late") and w.t - m["t"] >= LATE_S and m["status"] == "open":
            m["late"] = True
            w.m.setdefault("cpdlc", {}).setdefault("late", 0)
            w.m["cpdlc"]["late"] += 1
        if m["dir"] == "down" and m.get("request_id"):
            req = w.requests.get(m["request_id"])
            if req is not None and req.status != "pending":
                _close(w, m, "answered" if req.status in ("approved", "unable", "acknowledged", "amended") else req.status)
        if m["dir"] == "up" and not getattr(w, "multi", False):
            auto = m.setdefault("auto_at", round(m["t"] + w.pilot_delay_s + _jitter(m["id"]), 1))
            if w.t >= auto and m["status"] == "open":
                _auto_answer(w, m)


def _jitter(mid: int) -> float:
    lo, hi = AUTO_REPLY_S
    return lo + (mid * 7919 % 1000) / 1000.0 * (hi - lo)


def set_nda(w, cs: str, unit: str, by: str = "atc") -> dict:
    cs = cs.upper()
    c = conn(w, cs)
    if c is None or c["state"] != "cda":
        raise ValueError(f"{cs} has no active CPDLC connection with us")
    u = unit_info(w, unit)
    if u["internal"]:
        raise ValueError(f"{u['id']} is one of our sectors: the connection stays with {own(w)['address']}")
    c["nda"] = u["address"]
    _msg(w, cs, "up", [{"code": "UM160", "text": f"NEXT DATA AUTHORITY {u['address']}", "resp": "N"}], by=by)
    return c


def _transfer(w, a, c: dict, by: str, unit: Optional[str] = None) -> None:
    cs = a.callsign
    u = unit_info(w, unit or c.get("nda") or a.next_unit or "")
    if u["internal"]:
        c.update(state="transferred", cda=f"{u['address']} {u['id']}", end_t=round(w.t, 1), by=by)
        w._event("cpdlc", callsign=cs, text=f"{cs} CPDLC connection handed to sector {u['id']} ({by})")
    else:
        _msg(w, cs, "up", [{"code": "UM161", "text": "END SERVICE", "resp": "N"}], by=by)
        c.update(state="transferred", cda=u["address"] if (c.get("nda") or a.next_unit) else "", end_t=round(w.t, 1), by=by)
        w._event("cpdlc", callsign=cs, text=f"{cs} CPDLC connection transferred to {c['cda'] or 'none'} ({by})")
    for m in w.cpdlc_msgs[-200:]:                       # dialogues still open are closed by the transfer
        if m["callsign"] == cs and m["status"] in ("open", "standby"):
            _close(w, m, "closed")


def end_service(w, cs: str, by: str = "atc") -> dict:
    a = w.aircraft.get(cs.upper())
    c = conn(w, cs)
    if a is None or c is None or c["state"] != "cda":
        raise ValueError(f"{cs.upper()} has no active CPDLC connection with us")
    _transfer(w, a, c, by=by)
    return c


# ------------------------------------------------------------------ the controller's command line
_LEVEL = re.compile(r"(?:FL|F|CL|C|DES|DE)?(\d{2,3})")


def parse(w, text: str, callsign: Optional[str] = None) -> dict:
    """Parse a command line into {'callsign', 'elements': [...], 'answer': ..., 'control': ...}. Raises ValueError.
    Elements: {'code', 'text', 'resp', 'instr' (for the aircraft), 'entry' (for the system)}."""
    raw = text.strip()
    quoted = None
    mq = re.search(r'"([^"]*)"?\s*$', raw)
    if mq:
        quoted, raw = mq.group(1).strip().upper(), raw[:mq.start()]
    toks = raw.upper().split()
    cs = (callsign or "").upper()
    if toks and toks[0] in w.aircraft and (not cs or toks[0] != cs and toks[0] not in w.waypoints):
        cs = toks.pop(0)
    elif toks and toks[0] == cs:
        toks.pop(0)
    if not cs:
        raise ValueError("select an aircraft, or start with its callsign")
    a = w.aircraft.get(cs)
    if a is None:
        raise ValueError(f"unknown callsign {cs}")
    out = {"callsign": cs, "elements": [], "answer": None, "control": None}
    els = out["elements"]
    i = 0

    def nxt(k=1):
        return toks[i + k] if i + k < len(toks) else None

    while i < len(toks):
        tk = toks[i]
        if tk in ("TXT", "FREE", "FT"):
            words = " ".join(toks[i + 1:])
            if not words and not quoted:
                raise ValueError("free text: TXT then the words")
            els.append({"code": "UM169", "text": words or quoted, "resp": "R"})
            quoted = None
            break
        if tk == "NDA":
            if not nxt():
                raise ValueError("NDA then the unit, e.g. NDA GCCC")
            out["control"] = {"op": "nda", "unit": nxt()}
            i += 2
            continue
        if tk in ("EOS", "ENDSERVICE", "TERM"):
            out["control"] = {"op": "end"}
            i += 1
            continue
        if tk in RESPONSES_UP:
            out["answer"] = RESPONSES_UP[tk]
            i += 1
            continue
        if tk in ("APP", "APPROVE", "APPROVED"):
            out["answer"] = ("APPROVE", "APPROVE")
            i += 1
            continue
        m = re.fullmatch(r"(?:FL|F|CL|C|DES)(\d{2,3})", tk) or (re.fullmatch(r"(\d{3})", tk) if not els or els[-1].get("kind") != "contact" else None)
        if m:
            fl = int(m.group(1))
            if fl % 10:
                raise ValueError(f"FL{fl}: a flight level, multiple of 10")
            cur = a.alt_ft / 100.0
            if tk.startswith("DES") or (not tk.startswith(("C", "CL")) and fl < cur - 2):
                code, verb = "UM23", "DESCEND TO"
            elif tk.startswith(("C", "CL")) or fl > cur + 2:
                code, verb = "UM20", "CLIMB TO"
            else:
                code, verb = "UM19", "MAINTAIN"
            els.append({"code": code, "text": f"{verb} FL{fl:03d}", "resp": "WU", "kind": "level",
                        "instr": {"type": "altitude", "fl": fl}, "entry": {"cfl": fl}})
            i += 1
            continue
        m = re.fullmatch(r"V(\d{3,4})", tk)
        if m and els and els[-1].get("kind") == "level":
            r = int(m.group(1))
            up = els[-1]["code"] == "UM20"
            els[-1]["instr"]["rate_fpm"] = r
            els.append({"code": "UM171" if up else "UM173", "text": f"{'CLIMB' if up else 'DESCEND'} AT {r} FT/MIN MINIMUM",
                        "resp": "WU", "kind": "rate"})
            i += 1
            continue
        m = re.fullmatch(r"(H|HDG|L|R|HL|HR|TL|TR)(\d{1,3})", tk)
        if m:
            p, h = m.group(1), int(m.group(2))
            if not 1 <= h <= 360:
                raise ValueError("heading 1-360")
            turn = "left" if p in ("L", "HL", "TL") else "right" if p in ("R", "HR", "TR") else None
            els.append({"code": "UM94" if turn else "UM190",
                        "text": (f"TURN {turn.upper()} HEADING {h:03d}" if turn else f"FLY HEADING {h:03d}"), "resp": "WU",
                        "instr": {"type": "heading", "heading": h, "turn": turn}, "entry": {"heading": h, "direct": ""}})
            i += 1
            continue
        mm = re.fullmatch(r"M\.?0?(\d{2})", tk)
        ms = re.fullmatch(r"(?:S|SPD|IAS)(\d{3})", tk)
        mk = re.fullmatch(r"K(\d{3})", tk)
        if mm or ms or mk:
            host = next((e for e in reversed(els) if e.get("kind") in ("level", "speed")), None)
            if mm:
                key, val, txt, ent = "mach", int(mm.group(1)) / 100.0, f"MAINTAIN M.{mm.group(1)}", f"M{mm.group(1)}"
            elif ms:
                key, val, txt, ent = "ias_kt", int(ms.group(1)), f"MAINTAIN {ms.group(1)} KT", ms.group(1)
            else:
                key, val, txt, ent = "conversion_kt", int(mk.group(1)), f"MAINTAIN {mk.group(1)} KT ON CONVERSION", f"K{mk.group(1)}"
            if host is not None and host.get("kind") == "level":
                host["instr"][key] = val
                host.setdefault("entry", {})["speed"] = ent
                els.append({"code": "UM106", "text": txt, "resp": "WU", "kind": "speed_of_level"})
            else:
                els.append({"code": "UM106", "text": txt, "resp": "WU", "kind": "speed",
                            "instr": {"type": "speed", key: val}, "entry": {"speed": ent}})
            i += 1
            continue
        if tk in ("SN", "RNS", "NORMAL"):
            els.append({"code": "UM116", "text": "RESUME NORMAL SPEED", "resp": "WU", "kind": "speed",
                        "instr": {"type": "speed", "resume_normal": True}, "entry": {"speed": "SN"}})
            i += 1
            continue
        if tk in ("RES", "RESUME", "RON"):
            els.append({"code": None, "text": "RESUME OWN NAVIGATION", "resp": "WU",
                        "instr": {"type": "resume"}, "entry": {"heading": "", "wx": ""}})
            i += 1
            continue
        m = re.fullmatch(r"DEV([LRY]?)(\d{0,3})", tk)
        if m:
            side = {"L": "left", "R": "right"}.get(m.group(1), "either")
            nm = int(m.group(2)) if m.group(2) else 20
            instr = {"type": "deviation", "side": side, "max_nm": nm}
            els.append({"code": "UM82", "text": f"CLEARED TO DEVIATE UP TO {nm} NM {'EITHER SIDE' if side == 'either' else side.upper()} OF ROUTE",
                        "resp": "WU", "instr": instr, "entry": {"wx": {"left": "L", "right": "R"}.get(side, "Y") + str(nm)}})
            i += 1
            continue
        if tk == "VIA":
            pts = []
            i += 1
            while i < len(toks) and (toks[i] in w.waypoints or toks[i] == "DCT"):
                if toks[i] != "DCT":
                    pts.append(toks[i])
                i += 1
            if not pts:
                raise ValueError("VIA then the points, e.g. VIA IBALU TERTO")
            els.append({"code": "UM79", "text": f"CLEARED TO {pts[-1]} VIA {' '.join(pts)}", "resp": "WU",
                        "instr": {"type": "reroute", "points": pts}, "entry": {"route": pts, "direct": "", "heading": ""}})
            continue
        if tk in ("CT", "CONTACT", "MON", "MONITOR"):
            unit = None
            if nxt() and not re.fullmatch(r"\d{3}\.\d{1,3}", nxt()):
                unit = nxt()
                i += 1
            u = unit_info(w, unit or a.next_unit or "")
            if not u["id"]:
                raise ValueError(f"{cs} has no next unit: CT then the unit, e.g. CT GCCC")
            freq = u["freq"]
            if nxt() and re.fullmatch(r"\d{3}\.\d{1,3}", nxt()):
                freq = nxt()
                i += 1
            mon = tk.startswith("MON")
            els.append({"code": "UM120" if mon else "UM117", "text": f"{'MONITOR' if mon else 'CONTACT'} {u['name']}" + (f" {freq}" if freq else ""),
                        "resp": "WU", "kind": "contact", "unit": u["id"]})
            i += 1
            continue
        m = re.fullmatch(r"(?:D|DCT)([A-Z][A-Z0-9]{1,5})", tk)
        pt = m.group(1) if m and m.group(1) in w.waypoints else None
        if pt is None and tk in ("D", "DCT") and nxt() in w.waypoints:
            pt = nxt()
            i += 1
        if pt is None and tk in w.waypoints and not tk.startswith("@"):
            pt = tk
        if pt is None and tk in ("D", "DCT"):
            raise ValueError("D then the point: direct to which point?")
        if pt:
            els.append({"code": "UM74", "text": f"PROCEED DIRECT TO {pt}", "resp": "WU",
                        "instr": {"type": "direct", "waypoint": pt}, "entry": {"direct": pt, "heading": "", "wx": ""}})
            i += 1
            continue
        raise ValueError(f"cannot read '{tk}'")
    if quoted:
        els.append({"code": "UM169", "text": quoted, "resp": "R"})
    if not els and not out["answer"] and not out["control"]:
        raise ValueError("nothing to send")
    return out


def preview(w, text: str, callsign: Optional[str] = None) -> dict:
    """What the command line would do, for the suggestions under it."""
    try:
        p = parse(w, text, callsign)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    cs = p["callsign"]
    c = conn(w, cs)
    down = open_downlink(w, cs)
    txt = [e["text"] for e in p["elements"]]
    if p["answer"]:
        if p["answer"][0] == "APPROVE":
            txt = [e["text"] for e in _approval(w, down)] if down else ["(no request to approve)"]
        else:
            txt = [p["answer"][1]] + txt
    if p["control"]:
        txt.append("NEXT DATA AUTHORITY " + p["control"]["unit"] if p["control"]["op"] == "nda" else "END SERVICE")
    return {"ok": True, "callsign": cs, "elements": txt, "connected": bool(c and c["state"] == "cda"),
            "equipped": equipped(w.aircraft[cs]), "state": c["state"] if c else None,
            "answers": down["id"] if down and (p["answer"] or p["elements"]) else None,
            "voice_entry": [e for e in p["elements"] if e.get("entry")] != []}


def open_downlink(w, cs: str) -> Optional[dict]:
    _init(w)
    for m in w.cpdlc_msgs:
        if m["callsign"] == cs and m["dir"] == "down" and m["status"] in ("open", "standby") and m["resp"] != "N":
            return m
    return None


def _approval(w, down: dict) -> List[dict]:
    """The clearance answering a downlink request as asked."""
    k = down.get("kind")
    v = down.get("value")
    a = w.aircraft.get(down["callsign"])
    if k == "level":
        cur = a.alt_ft / 100.0 if a else 0
        code, verb = ("UM20", "CLIMB TO") if v > cur + 2 else ("UM23", "DESCEND TO") if v < cur - 2 else ("UM19", "MAINTAIN")
        return [{"code": code, "text": f"{verb} FL{v:03d}", "resp": "WU", "kind": "level",
                 "instr": {"type": "altitude", "fl": v}, "entry": {"cfl": v}}]
    if k == "direct":
        return [{"code": "UM74", "text": f"PROCEED DIRECT TO {v}", "resp": "WU",
                 "instr": {"type": "direct", "waypoint": v}, "entry": {"direct": v, "heading": "", "wx": ""}}]
    if k == "speed":
        s = str(v)
        if s.startswith("M"):
            instr, txt = {"type": "speed", "mach": int(s[1:]) / 100.0}, f"MAINTAIN M.{s[1:]}"
        else:
            instr, txt = {"type": "speed", "ias_kt": int(s)}, f"MAINTAIN {s} KT"
        return [{"code": "UM106", "text": txt, "resp": "WU", "kind": "speed", "instr": instr, "entry": {"speed": s}}]
    if k == "deviation":
        side, nm = v.get("side", "either"), int(v.get("nm", 20))
        return [{"code": "UM82", "text": f"CLEARED TO DEVIATE UP TO {nm} NM {'EITHER SIDE' if side == 'either' else side.upper()} OF ROUTE",
                 "resp": "WU", "instr": {"type": "deviation", "side": side, "max_nm": nm},
                 "entry": {"wx": {"left": "L", "right": "R"}.get(side, "Y") + str(nm)}, "approves_request": True}]
    raise ValueError("this request is answered with ROGER, UNABLE or a clearance, not APP")


def command(w, text: str, callsign: Optional[str] = None, voice: bool = False, who: str = "atc") -> dict:
    """The controller's command line: an uplink (or an answer to a downlink) to a connected aircraft; with voice=True,
    or to an aircraft without an active connection, only what it puts in the system (a voice clearance)."""
    p = parse(w, text, callsign)
    cs = p["callsign"]
    a = w.aircraft[cs]
    c = conn(w, cs)
    if p["control"]:
        if p["control"]["op"] == "nda":
            set_nda(w, cs, p["control"]["unit"], by=who)
        else:
            end_service(w, cs, by=who)
        if not p["elements"] and not p["answer"]:
            return {"callsign": cs, "kind": "control", "connection": conn(w, cs)}
    linked = not voice and c is not None and c["state"] == "cda"
    if not linked:
        ent = {}
        for e in p["elements"]:
            ent.update(e.get("entry") or {})
        if p["answer"] and p["answer"][0] == "APPROVE":
            raise ValueError("APP answers a CPDLC request: nothing to approve here")
        if not ent:
            raise ValueError(f"{cs} has no active CPDLC connection" if not voice else "nothing to enter")
        w.enter(cs, ent, who=who)
        why = "entered" if voice else ("not CPDLC equipped" if not equipped(a) else
                                       "no active CPDLC connection" if not c or c["state"] != "cda" else "")
        return {"callsign": cs, "kind": "entry", "entry": ent, "note": f"{why}: give it by voice" if not voice else "entered"}
    if not a.assumed:
        raise ValueError(f"{cs} is not assumed: assume it first")
    down = open_downlink(w, cs)
    elements = list(p["elements"])
    ref = None
    if p["answer"]:
        if p["answer"][0] == "APPROVE":
            if not down:
                raise ValueError(f"{cs} has no request to approve")
            elements = _approval(w, down) + elements
        else:
            code, word = p["answer"]
            if down is None:
                raise ValueError(f"{cs} has no open downlink to answer {word}")
            elements = [{"code": code, "text": word, "resp": "N", "kind": "answer"}] + elements
    if down is not None and elements:
        ref = down["id"]
    for e in elements:
        if e.get("kind") == "contact" and not e.get("unit"):
            raise ValueError("CONTACT: which unit?")
    resp_only = all(e.get("kind") == "answer" for e in elements)
    m = _msg(w, cs, "up", [{k: v for k, v in e.items()} for e in elements], ref=ref, by=who, response=resp_only)
    if down is not None and ref is not None:
        words = [e["text"] for e in elements if e.get("kind") == "answer"]
        if words and words[0] == "STANDBY" and len(elements) == 1:
            down["status"] = "standby"
        else:
            _close(w, down, "unable" if words and words[0] in ("UNABLE", "NEGATIVE") else "answered")
            if down.get("request_id") and words and words[0] in ("UNABLE", "NEGATIVE"):
                _reply_request(w, down, "unable")
            elif down.get("request_id") and words and words[0] in ("ROGER", "AFFIRM"):
                req = w.requests.get(down["request_id"])
                if req is not None and req.type == "report":
                    _reply_request(w, down, "roger")
            m["answers_request"] = down.get("request_id")
    return {"callsign": cs, "kind": "uplink", "message": m}


def _reply_request(w, down: dict, decision: str) -> None:
    req = w.requests.get(down.get("request_id") or "")
    if req is None or req.status != "pending":
        return
    w.issue({"callsign": down["callsign"], "type": "reply", "request_id": req.id, "decision": decision},
            source="cpdlc", by_pilot=getattr(w, "multi", False))


# ------------------------------------------------------------------ the pilot's side
def respond(w, mid: int, answer: str, reason: Optional[str] = None, who: str = "pilot") -> dict:
    """The pilot answers an uplink: WILCO (the aircraft executes it), UNABLE [reason], STANDBY, ROGER, AFFIRM,
    NEGATIVE. Raises ValueError if WILCO cannot be flown (answer UNABLE instead)."""
    m = get_msg(w, int(mid))
    if m is None or m["dir"] != "up":
        raise ValueError(f"no uplink {mid}")
    if m["status"] not in ("open", "standby"):
        raise ValueError(f"uplink {mid} is already {m['status']}")
    ans = answer.upper()
    if ans not in RESPONSES_DOWN:
        raise ValueError(f"answer one of {', '.join(RESPONSES_DOWN)}")
    if m["resp"] == "WU" and ans not in ("WILCO", "UNABLE", "STANDBY"):
        raise ValueError("a clearance is answered WILCO, UNABLE or STANDBY")
    if m["resp"] == "R" and ans not in ("ROGER", "UNABLE", "STANDBY"):
        raise ValueError("answer ROGER (or UNABLE / STANDBY)")
    if m["resp"] == "AN" and ans not in ("AFFIRM", "NEGATIVE", "STANDBY"):
        raise ValueError("answer AFFIRM or NEGATIVE")
    cs = m["callsign"]
    multi = getattr(w, "multi", False)
    if ans == "WILCO":
        instrs = [e for e in m["elements"] if e.get("instr")]
        for e in instrs:                                      # check first: all or nothing
            try:
                w.validate(dict(e["instr"], callsign=cs), by_pilot=multi)
            except Exception as ex:
                raise ValueError(f"cannot fly {e['text']}: {ex} - answer UNABLE")
        entry = {}
        for e in instrs:
            if e.get("approves_request") and m.get("answers_request"):
                req = w.requests.get(m["answers_request"])
                if req is not None and req.status == "pending":
                    r = w.issue({"callsign": cs, "type": "reply", "request_id": req.id, "decision": "approved"},
                                source="cpdlc", by_pilot=multi)
                    if r["status"] != "rejected":
                        entry.update(e.get("entry") or {})
                        continue
            r = w.issue(dict(e["instr"], callsign=cs), source="cpdlc", by_pilot=multi)
            if r["status"] == "rejected":
                raise ValueError(f"cannot fly {e['text']}: {r['reason']}")
            entry.update(e.get("entry") or {})
        if multi and entry:
            w.enter(cs, entry, who="cpdlc")
        _close(w, m, "wilco")
        w.m.setdefault("cpdlc", {}).setdefault("wilco_s", []).append(round(w.t - m["t"], 1))
    elif ans == "STANDBY":
        m["status"] = "standby"
    else:
        _close(w, m, {"UNABLE": "unable", "ROGER": "roger", "AFFIRM": "affirm", "NEGATIVE": "negative"}[ans])
        if ans == "UNABLE":
            w.m.setdefault("cpdlc", {}).setdefault("unable", 0)
            w.m["cpdlc"]["unable"] += 1
    els = [{"code": RESPONSES_DOWN[ans], "text": ans, "resp": "N"}]
    if ans == "UNABLE" and reason in REASONS:
        els.append({"code": REASONS[reason][0], "text": REASONS[reason][1], "resp": "N"})
    r = _msg(w, cs, "down", els, ref=m["id"], by=who, response=True)
    contact = next((e for e in m["elements"] if e.get("kind") == "contact"), None)
    if ans == "WILCO" and contact is not None:          # the transfer: decontrol, the connection goes to the next unit
        a = w.aircraft[cs]
        if a.assumed:
            w.issue({"callsign": cs, "type": "release"}, source="cpdlc")
        c = conn(w, cs)
        if c is not None and c["state"] == "cda":
            _transfer(w, a, c, by="contact", unit=contact.get("unit"))
    return {"uplink": m, "response": r}


def downlink(w, cs: str, kind: str, value=None, reason: Optional[str] = None, request_id: Optional[str] = None,
             who: str = "pilot") -> dict:
    """A pilot request by data link: kind level | climb | descent | direct | speed | deviation | text."""
    cs = cs.upper()
    a = w.aircraft.get(cs)
    c = conn(w, cs)
    if a is None or c is None or c["state"] != "cda":
        raise ValueError(f"{cs} has no active CPDLC connection with {own(w)['address']}")
    if request_id:
        req = w.requests.get(request_id)
        if req is None or req.callsign != cs or req.status != "pending":
            raise ValueError(f"no pending request {request_id} for {cs}")
        if any(x.get("request_id") == request_id and x["status"] in ("open", "standby") for x in w.cpdlc_msgs):
            raise ValueError(f"request {request_id} is already sent by CPDLC")
        kind, value, reason = _from_request(w, a, req)
    k = str(kind).lower()
    els: List[dict] = []
    if k in ("level", "climb", "descent"):
        fl = int(str(value).upper().lstrip("FL").lstrip("F"))
        if fl % 10:
            raise ValueError("a flight level, multiple of 10")
        if k == "level":
            k = "climb" if fl * 100 > a.alt_ft + 200 else "descent" if fl * 100 < a.alt_ft - 200 else "level"
        code, txt = {"climb": ("DM9", f"REQUEST CLIMB TO FL{fl:03d}"), "descent": ("DM10", f"REQUEST DESCENT TO FL{fl:03d}"),
                     "level": ("DM6", f"REQUEST FL{fl:03d}")}[k]
        els.append({"code": code, "text": txt, "resp": "Y"})
        kind_s, val = "level", fl
    elif k == "direct":
        pt = str(value).upper()
        if pt not in w.waypoints or pt.startswith("@"):
            raise ValueError(f"unknown point {pt}")
        els.append({"code": "DM22", "text": f"REQUEST DIRECT TO {pt}", "resp": "Y"})
        kind_s, val = "direct", pt
    elif k == "speed":
        s = str(value).upper().replace(".", "").replace(" ", "")
        mm = re.fullmatch(r"M?0?(\d{2})", s) if s.startswith("M") or len(s) == 2 else None
        if mm:
            val, txt = f"M{mm.group(1)}", f"REQUEST M.{mm.group(1)}"
        elif re.fullmatch(r"\d{3}(KT)?", s):
            val, txt = s[:3], f"REQUEST {s[:3]} KT"
        else:
            raise ValueError("speed like M78 or 280")
        els.append({"code": "DM18", "text": txt, "resp": "Y"})
        kind_s = "speed"
    elif k == "deviation":
        v = value if isinstance(value, dict) else {}
        if not isinstance(value, dict):
            mt = re.fullmatch(r"([LRY]?)(\d{0,3})", str(value or "").upper().replace(" ", ""))
            if not mt:
                raise ValueError("deviation like L20, R15 or 20 (either side)")
            v = {"side": {"L": "left", "R": "right"}.get(mt.group(1), "either"), "nm": int(mt.group(2) or 20)}
        side, nm = v.get("side", "either"), int(v.get("nm", 20))
        els.append({"code": "DM27", "text": f"REQUEST WEATHER DEVIATION UP TO {nm} NM {'EITHER SIDE' if side == 'either' else side.upper()} OF ROUTE",
                    "resp": "Y"})
        kind_s, val = "deviation", {"side": side, "nm": nm}
        reason = reason or "weather"
    elif k == "text":
        t = str(value or "").strip().upper()
        if not t:
            raise ValueError("free text: type the message")
        els.append({"code": "DM67", "text": t[:160], "resp": "Y"})
        kind_s, val = "text", t
    else:
        raise ValueError("kind: level, climb, descent, direct, speed, deviation or text")
    if reason in REASONS and k != "text":
        els.append({"code": REASONS[reason][0], "text": REASONS[reason][1], "resp": "N"})
    for e in els:                                  # downlinks wait for an answer ("Y" in the message set)
        if e["resp"] == "Y":
            e["resp"] = "WU"
    m = _msg(w, cs, "down", els, by=who, request_id=request_id)
    m["kind"], m["value"] = kind_s, val
    return m


def _from_request(w, a, req) -> Tuple[str, object, Optional[str]]:
    why = "weather" if "weather" in (req.reason or "").lower() or "turbulence" in (req.reason or "").lower() else \
        "performance" if any(x in (req.reason or "").lower() for x in ("weight", "performance", "ceiling", "fuel")) else None
    if req.type == "altitude":
        return "level", int(req.value), why
    if req.type == "direct":
        return "direct", str(req.value), why
    if req.type == "speed":
        v = req.value
        return "speed", (f"M{int(round(float(v) * 100))}" if float(v) < 2 else str(int(v))), why
    if req.type == "weather_deviation":
        v = req.value or {}
        dh = (float(v.get("heading", a.trk)) - a.trk + 540.0) % 360.0 - 180.0
        return "deviation", {"side": "right" if dh > 0 else "left", "nm": int(round(float(v.get("distance_nm", 20)) / 5) * 5) or 10}, "weather"
    if req.type == "report":
        return "text", req.phrase().replace(req.callsign + " ", "", 1), None
    raise ValueError(f"request {req.id} cannot be sent by CPDLC")


def _auto_answer(w, m: dict) -> None:
    """Solo radar: the simulated pilot answers the uplink."""
    if m["resp"] == "R":
        respond(w, m["id"], "ROGER", who="pilot")
        return
    if m["resp"] == "AN":
        respond(w, m["id"], "AFFIRM", who="pilot")
        return
    try:
        respond(w, m["id"], "WILCO", who="pilot")
    except ValueError as e:
        why = "weather" if "weather" in str(e).lower() else "performance"
        respond(w, m["id"], "UNABLE", reason=why, who="pilot")
        m["unable_why"] = str(e)


# ------------------------------------------------------------------ views
def _brief(m: dict) -> dict:
    d = {"id": m["id"], "dir": m["dir"], "text": m["text"], "status": m["status"], "t": m["t"], "utc": m["utc"],
         "resp": m["resp"], "ref": m.get("ref")}
    for k in ("late", "request_id", "kind", "value", "closed_t", "unable_why", "answers_request"):
        if m.get(k) is not None:
            d[k] = m[k]
    d["elements"] = [{"code": e.get("code"), "text": e["text"]} for e in m["elements"]]
    return d


def state_view(w) -> dict:
    """Per callsign: connection and the dialogues still open (or closed in the last RECENT_S), for the labels."""
    _init(w)
    out: Dict[str, dict] = {}
    for cs, c in w.cpdlc.items():
        out[cs] = {"state": c["state"], "cda": c.get("cda"), "nda": c.get("nda"), "address": c.get("address"), "open": [], "recent": []}
    for m in w.cpdlc_msgs[-300:]:
        d = out.get(m["callsign"])
        if d is None:
            continue
        if m["status"] in ("open", "standby") and m["resp"] != "N":
            d["open"].append(_brief(m))
        elif m.get("closed_t") is not None and w.t - m["closed_t"] <= RECENT_S and m["resp"] != "N":
            d["recent"].append(_brief(m))
        elif m["resp"] == "N" and m["dir"] == "up" and w.t - m["t"] <= RECENT_S:
            d["recent"].append(_brief(m))           # the controller's answers, next data authority, end service
    for a in w.aircraft.values():
        if equipped(a) and a.callsign not in out and (a.active or a.outside):
            out[a.callsign] = {"state": "none", "open": [], "recent": []}
    return out


def messages(w, since: int = 0, callsign: Optional[str] = None) -> List[dict]:
    _init(w)
    cs = (callsign or "").upper()
    return [_brief(m) | {"callsign": m["callsign"]} for m in w.cpdlc_msgs if m["id"] > since and (not cs or m["callsign"] == cs)]
