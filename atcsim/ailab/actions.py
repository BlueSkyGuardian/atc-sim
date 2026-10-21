"""The instruction set of the AI controller, and how it is applied.

The model answers with JSON: {"reasoning": "...", "actions": [ ... ]}, each action one of
  {"type": "assume",    "callsign": "RAM123"}
  {"type": "clear",     "callsign": "RAM123", "cfl": 370, "heading": 250, "direct": "ROLRO", "speed": "M76", "xfl": 350}
                        (any of cfl / heading / direct / speed / xfl; heading and direct exclude each other)
  {"type": "resume",    "callsign": "RAM123"}                     back on its route after a heading
  {"type": "reply",     "request_id": "R004", "decision": "approve" | "unable"}
  {"type": "decontrol", "callsign": "RAM123"}
An empty list is a valid answer (nothing to do). Actions go through the simulation's logged actions (inputs.py),
exactly as a controller's label entries: a synthetic pilot flies them and reads them back, or says unable.
"""
from __future__ import annotations

import json
import re
from typing import List, Optional, Tuple

SCHEMA = """Answer with ONE JSON object and nothing else:
{"reasoning": "<one or two short sentences>", "actions": [<actions>]}
Actions (use only these; an empty list means nothing to do now):
  {"type": "assume", "callsign": "RAM123"}                 answer an aircraft calling you (radar contact): it is then yours
  {"type": "clear", "callsign": "RAM123", "cfl": 370}      cleared flight level (multiple of 10)
  {"type": "clear", "callsign": "RAM123", "heading": 250}  fly heading (degrees)
  {"type": "clear", "callsign": "RAM123", "direct": "ROLRO"}  proceed direct to a point of its route or the map
  {"type": "clear", "callsign": "RAM123", "speed": "M76"}  Mach (M76) or indicated speed in knots ("280"); "SN" = resume normal speed
  {"type": "clear", "callsign": "RAM123", "xfl": 350}      exit level coordinated with the next unit
  {"type": "resume", "callsign": "RAM123"}                 resume own navigation after a heading
  {"type": "reply", "request_id": "R004", "decision": "approve"}   or "unable"
  {"type": "ask", "callsign": "RAM123", "cfl": 370}        ask the pilot if able / interested in a level (answer at the next decision)
  {"type": "decontrol", "callsign": "RAM123"}              transfer an aircraft to the next unit: when it leaves your sector
Several fields may go in one "clear" (e.g. cfl and heading)."""


def parse_full(text: str) -> Tuple[List[dict], str, str, List[dict]]:
    """(actions, reasoning, error, queries): an answer may hold queries instead of actions (queries.py)."""
    acts, reasoning, err = parse(text)
    qs = []
    if not err:
        for c in _candidates(text):
            try:
                d = json.loads(c)
            except ValueError:
                continue
            if isinstance(d, dict) and isinstance(d.get("queries"), list):
                qs = [q for q in d["queries"] if isinstance(q, dict)]
            break
    return acts, reasoning, err, qs


def _candidates(text: str) -> List[str]:
    s = (text or "").split("</think>")[-1].strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    cand = [m.group(1)] if m else []
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        cand.append(s[i:j + 1])
    return cand


def parse(text: str) -> Tuple[List[dict], str, str]:
    """The model's answer -> (actions, reasoning, error). Tolerates text around the JSON and ```json fences."""
    if not text:
        return [], "", "empty answer"
    if "</think>" in text:                                  # a reasoning model's thinking before its answer
        text = text.split("</think>")[-1]
    s = text.strip()
    if not s:
        return [], "", "empty answer (only thinking)"
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    cand = [m.group(1)] if m else []
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        cand.append(s[i:j + 1])
    for c in cand:
        try:
            d = json.loads(c)
        except ValueError:
            continue
        if isinstance(d, dict):
            acts = d.get("actions")
            if acts is None:
                acts = []
            if not isinstance(acts, list):
                return [], str(d.get("reasoning") or ""), "actions is not a list"
            return [a for a in acts if isinstance(a, dict)], str(d.get("reasoning") or ""), ""
        if isinstance(d, list):
            return [a for a in d if isinstance(a, dict)], "", ""
    return [], "", "no JSON object in the answer"


ALLOWED_CLEAR = ("cfl", "xfl", "heading", "direct", "speed")


def _parity_levels(fl: int, southbound: bool) -> str:
    from ..aircraft import level_parity_ok
    near = [x for x in range(fl - 30, fl + 31, 10) if x != fl and level_parity_ok(x, southbound)][:4]
    near.sort(key=lambda x: abs(x - fl))
    return ", ".join(str(x) for x in near[:2])


def check_rules(w, ac, a: dict) -> List[str]:
    """The rules a clearance must respect (semicircular rule for cruising and exit levels): refusal texts."""
    from ..aircraft import level_parity_ok
    out = []
    side = "southbound: odd levels" if ac.southbound else "northbound: even levels"
    for k in ("cfl", "xfl"):
        if a.get(k) in (None, ""):
            continue
        try:
            fl = int(str(a[k]).upper().lstrip("FL").lstrip("F"))
        except ValueError:
            out.append(f"{k} {a[k]!r} is not a flight level (e.g. 350)")
            continue
        going_down = fl * 100 < ac.alt_ft - 300 and w.descending_to_destination(ac, fl)
        if k == "cfl" and going_down:
            continue                                   # arrivals descending: the rule does not apply
        if not level_parity_ok(fl, ac.southbound):
            out.append(f"FL{fl} is not a level of {ac.callsign}'s direction ({side}; e.g. {_parity_levels(fl, ac.southbound)}): "
                       f"semicircular rule, nothing was given")
    return out


ASK_VALID_S = 900           # a pilot's "affirm" to "are you able / interested FLxxx?" holds this long
URGENT_S = 120              # a conflict this close (or a loss): a level may be given without asking


def _fl(v) -> int:
    return int(str(v).upper().lstrip("FL").lstrip("F"))


def pilot_level_answer(w, ac, fl: int) -> Tuple[bool, str]:
    """What the pilot answers to "are you able / interested FLxxx?": unable (performance, weather, RVSM) by the
    simulator's own checks; otherwise affirm, unless far from its requested cruise level (then it may prefer to stay)."""
    from ..world import InstructionError
    try:
        w.validate({"callsign": ac.callsign, "type": "altitude", "fl": fl})
    except InstructionError as e:
        return False, f"negative, unable FL{fl:03d} ({str(e).split(':')[-1].strip()[:80]})"
    cruise = str((ac.flight_plan or {}).get("cruise") or "").upper().lstrip("F")
    cur = int(round(ac.alt_ft / 100))
    if cruise.isdigit():
        c = int(cruise)
        far = fl > c + 20 or (fl < c - 40 and not w.descending_to_destination(ac, fl))
        if far and (sum(map(ord, f"{ac.callsign}{fl}")) % 10) < 6:      # reproducible: the same answer each time
            return False, f"negative, we prefer to maintain FL{cur:03d} (requested cruise FL{c:03d})"
    return True, f"affirm, able FL{fl:03d}"


def level_needs_asking(w, ac, fl: int, asked: dict) -> str:
    """'' when the level may be given now; else why it may not (the rule: ask an aircraft before giving it a level
    it did not ask for)."""
    from .. import atctools
    if fl * 100 < ac.alt_ft - 300 and w.descending_to_destination(ac, fl):
        return ""                                          # arrivals descending
    if any(r.callsign == ac.callsign and r.status == "pending" and str(r.value or "").upper().lstrip("F").lstrip("L").isdigit()
           and _fl(r.value) == fl for r in w.requests.values()):
        return ""                                          # it asked for this level
    cruise = str((ac.flight_plan or {}).get("cruise") or "").upper().lstrip("F")
    if cruise.isdigit() and int(cruise) == fl:
        return ""                                          # back to its requested cruise level
    if abs(fl * 100 - ac.alt_ft) < 300 and abs(fl * 100 - ac.cleared_alt_ft) < 300:
        return ""                                          # the level it is at
    q = (asked.get(ac.callsign) or {}).get(fl)
    if q and q["ok"] and q["t"] < w.t and w.t - q["t"] <= ASK_VALID_S:
        return ""                                          # asked earlier, and it said affirm
    if q and q["t"] >= w.t:
        return f"FL{fl:03d}: you have just asked {ac.callsign}; give the level at the next decision, once it has answered"
    if q and not q["ok"]:
        return f"FL{fl:03d}: the pilot said {q['text']}"
    for c in atctools.mtcd(w):
        if ac.callsign in c["aircraft"] and (c.get("current") or c["time_to_conflict_s"] <= URGENT_S):
            return ""                                      # urgent: give it, the pilot complies
    return (f"FL{fl:03d} was not requested by {ac.callsign}: ask the pilot first "
            f'({{"type": "ask", "callsign": "{ac.callsign}", "cfl": {fl}}}), or solve with speed or a heading')


def early_descent(w, ac, a: dict) -> str:
    """An arrival cleared down long before its top of descent (it has not asked): a note (not refused)."""
    from .observe import arrival_info
    arr = arrival_info(w, ac)
    if a.get("cfl") in (None, "") or not arr.get("fix"):
        return ""
    try:
        fl = int(str(a["cfl"]).upper().lstrip("FL").lstrip("F"))
    except ValueError:
        return ""
    tod = arr.get("tod_in_nm")
    asked = any(r.callsign == ac.callsign and r.status == "pending" and r.kind == "descent" for r in w.requests.values())
    if fl * 100 < ac.alt_ft - 300 and tod is not None and tod > 25 and not asked:
        return f" (early: its top of descent is in {tod:.0f} NM and it has not asked — arrivals are descended when they ask, or for a conflict)"
    return ""


def apply(w, actions: List[dict], actor: dict, max_actions: int = 12, force: bool = False, rules: bool = True,
          asked: Optional[dict] = None) -> List[dict]:
    """Apply the actions in order; one result per action: {"action", "ok", "text"}. With rules, a clearance that
    breaks the semicircular rule is refused (with the levels to use)."""
    from .. import inputs
    from ..world import InstructionError
    out = []
    for a in actions[:max_actions]:
        t = str(a.get("type", "")).lower()
        cs = str(a.get("callsign", "")).upper()
        try:
            if t == "assume":
                r = inputs.apply(w, "assume", {"callsign": cs}, actor)
                ok = r.get("status") != "rejected"
                out.append({"action": a, "ok": ok, "text": r.get("reason") or f"{cs} assumed"})
            elif t == "ask":
                ac = w.aircraft.get(cs)
                if ac is None or not ac.active:
                    raise ValueError(f"{cs}: no such aircraft in your traffic (check the callsign)")
                if not ac.assumed:
                    raise ValueError(f'{cs} is not assumed: assume it first ({{"type": "assume", "callsign": "{cs}"}})')
                if a.get("cfl") in (None, ""):
                    raise ValueError('ask: give the level, e.g. {"type": "ask", "callsign": "%s", "cfl": 370}' % cs)
                fl = _fl(a["cfl"])
                ok_, txt = pilot_level_answer(w, ac, fl)
                if asked is not None:
                    asked.setdefault(cs, {})[fl] = {"t": w.t, "ok": ok_, "text": txt}
                out.append({"action": a, "ok": True, "text": f"{cs}: {txt}", "answer": ok_})
            elif t == "decontrol":
                r = inputs.apply(w, "release", {"callsign": cs}, actor)
                ok = r.get("status") != "rejected"
                out.append({"action": a, "ok": ok, "text": r.get("reason") or f"{cs} decontrolled"})
            elif t in ("clear", "resume"):
                ac = w.aircraft.get(cs)
                if ac is None or not (ac.active or ac.outside):
                    raise ValueError(f"{cs}: no such aircraft in your traffic (check the callsign)")
                if not ac.assumed:
                    where = ("it is calling you: answer it first" if ac.on_frequency else
                             "it is not on your frequency yet (INBOUND): wait until it calls you, then assume it")
                    raise ValueError(f"{cs} is not assumed, nothing was given to the pilot; {where} with "
                                     f'{{"type": "assume", "callsign": "{cs}"}}, then clear it')
                extra = sorted(k for k in a if k not in ("type", "callsign") + ALLOWED_CLEAR)
                if rules and t == "clear" and a.get("cfl") not in (None, "") and asked is not None:
                    why = level_needs_asking(w, ac, _fl(a["cfl"]), asked)
                    if why:
                        raise ValueError(why + "; nothing was given")
                if rules and t == "clear":
                    bad = check_rules(w, ac, a)
                    if bad:
                        raise ValueError("; ".join(bad))
                note = early_descent(w, ac, a) if t == "clear" else ""
                body = {"callsign": cs}
                if t == "resume":
                    body["heading"] = ""
                else:
                    for k in ("cfl", "xfl", "heading", "direct", "speed"):
                        if a.get(k) not in (None, ""):
                            body[k] = a[k]
                    if "heading" in body and "direct" in body:
                        raise ValueError("heading and direct together: choose one")
                    if len(body) == 1:
                        raise ValueError("clear: nothing to clear (cfl, heading, direct, speed or xfl)")
                    if "heading" in body:
                        body["direct"] = ""
                if force:
                    body["force"] = True
                r = inputs.apply(w, "entries", body, actor)
                rb = r.get("_readback") or []
                ok = not any(x.get("status") == "rejected" for x in rb)
                txt = (" · ".join(x.get("text", "") for x in rb) or "entered") + note
                if extra:
                    txt += (f" (IGNORED: {', '.join(repr(e) for e in extra)} {'is not a field' if len(extra) == 1 else 'are not fields'}"
                            f" — a clear takes only {', '.join(ALLOWED_CLEAR)})")
                res = {"action": a, "ok": ok, "text": txt}
                if note:
                    res["early"] = True
                if extra:
                    res["unknown_fields"] = extra
                out.append(res)
            elif t == "reply":
                rid = str(a.get("request_id", ""))
                q = w.requests.get(rid)
                if q is None or q.status != "pending":
                    raise ValueError(f"no pending request {rid}")
                dec = str(a.get("decision", "")).lower()
                code = "ROG" if q.type == "report" else "APP" if dec.startswith("app") else "UNA"
                r = inputs.apply(w, "cpdlc_command", {"text": code, "callsign": q.callsign, "voice": True}, actor)
                out.append({"action": a, "ok": True, "text": r.get("note") or code})
            else:
                raise ValueError(f"unknown action type '{t}'")
        except (InstructionError, ValueError, KeyError) as e:
            out.append({"action": a, "ok": False, "text": str(e).strip("'")})
    for a in actions[max_actions:]:
        out.append({"action": a, "ok": False, "text": f"not given: more than {max_actions} instructions in one answer (give it next time)"})
    return out
