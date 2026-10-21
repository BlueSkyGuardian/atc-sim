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
from typing import List, Tuple

SCHEMA = """Answer with ONE JSON object and nothing else:
{"reasoning": "<one or two short sentences>", "actions": [<actions>]}
Actions (use only these; an empty list means nothing to do now):
  {"type": "assume", "callsign": "RAM123"}                 answer an aircraft calling you (radar contact): it is then yours
  {"type": "clear", "callsign": "RAM123", "cfl": 370}      cleared flight level (multiple of 10)
  {"type": "clear", "callsign": "RAM123", "heading": 250}  fly heading (degrees)
  {"type": "clear", "callsign": "RAM123", "direct": "ROLRO"}  proceed direct to a point of its route or the map
  {"type": "clear", "callsign": "RAM123", "speed": "M76"}  Mach (M76) or indicated speed in knots ("280")
  {"type": "clear", "callsign": "RAM123", "xfl": 350}      exit level coordinated with the next unit
  {"type": "resume", "callsign": "RAM123"}                 resume own navigation after a heading
  {"type": "reply", "request_id": "R004", "decision": "approve"}   or "unable"
  {"type": "decontrol", "callsign": "RAM123"}              transfer an aircraft to the next unit: when it leaves your sector
Several fields may go in one "clear" (e.g. cfl and heading)."""


def parse(text: str) -> Tuple[List[dict], str, str]:
    """The model's answer -> (actions, reasoning, error). Tolerates text around the JSON and ```json fences."""
    if not text:
        return [], "", "empty answer"
    s = text.strip()
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


def apply(w, actions: List[dict], actor: dict, max_actions: int = 12, force: bool = False) -> List[dict]:
    """Apply the actions in order; one result per action: {"action", "ok", "text"}."""
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
                extra = sorted(k for k in a if k not in ("type", "callsign", "cfl", "xfl", "heading", "direct", "speed"))
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
                txt = " · ".join(x.get("text", "") for x in rb) or "entered"
                if extra:
                    txt += f" (ignored unknown field{'s' if len(extra) > 1 else ''}: {', '.join(extra)})"
                out.append({"action": a, "ok": ok, "text": txt})
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
