"""Every action that changes a simulation (a clearance entered, an assume, a pilot's instruction, a CPDLC message, a
live action...) goes through apply(): it is carried out on the world and logged in world.inputs with its sim time.

The log travels with the world (it is in every snapshot), so a run can be reproduced exactly: load the snapshot
before a moment, step second by second and apply the logged actions at their times (the simulation is deterministic).
The debrief replays exercises that way, and branches them when somebody changes something.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional


class Refused(Exception):
    """The seat may not do this (HTTP 403)."""


def log(w) -> list:
    return w.__dict__.setdefault("inputs", [])


def callsign_of(kind: str, body: dict) -> Optional[str]:
    """The aircraft an action is about (None for chat, areas, settings...)."""
    if kind == "instructions":
        items = body.get("items") or []
        return (items[0].get("callsign") or "").upper() if items else None
    if kind == "command":
        t = (body.get("text") or "").split()
        return t[0].upper() if t else None
    cs = body.get("callsign")
    return str(cs).upper() if cs else None


def describe(kind: str, body: dict) -> str:
    """A short text for an action, e.g. 'RAM12 CFL 330 H 250', 'assume TAP1478'."""
    cs = callsign_of(kind, body) or ""
    if kind == "entries":
        parts = []
        if body.get("force"):
            parts.append("(forced)")
        for k in ("cfl", "xfl", "heading", "direct", "speed", "wx", "route", "coord", "note"):
            if k in body and body[k] not in (None,):
                v = body[k]
                v = " ".join(map(str, v)) if isinstance(v, list) else v
                parts.append(f"{k.upper()} {v}" if v != "" else f"{k.upper()} cleared")
        return f"{cs} " + " ".join(parts)
    if kind in ("assume", "release"):
        return f"{'assume' if kind == 'assume' else 'decontrol'} {cs}"
    if kind == "instructions":
        it = (body.get("items") or [{}])[0]
        return f"{cs} {it.get('type', '')} {it.get('value', '') if it.get('value') is not None else ''}".strip()
    if kind in ("command", "cpdlc_command"):
        return (f"{cs} " if kind == "cpdlc_command" and cs else "") + (body.get("text") or "")
    if kind == "live":
        return f"{body.get('action')} {cs}".strip()
    if kind == "area":
        return f"area {body.get('area')} {'on' if body.get('active') else 'off'}"
    if kind == "runway":
        return f"{body.get('aerodrome')} runway {body.get('runway')}"
    return kind.replace("_", " ") + (f" {cs}" if cs else "")


# ------------------------------------------------------------------ the actions
def _instructions(w, body, actor):
    pilot = bool(w.multi) and actor.get("seat") in ("pilot", "instructor")
    if bool(w.multi) and actor.get("seat") == "pilot" and any(i.get("type") in ("assume", "release") for i in body["items"]):
        raise Refused("the controller assumes aircraft (label or double-click), not the pilot")
    return [w.issue(dict(i), source="pilot" if pilot else "api", by_pilot=pilot) for i in body["items"]]


def _command(w, body, actor):
    from .session import parse_command
    pilot = bool(w.multi) and actor.get("seat") in ("pilot", "instructor")
    parsed = parse_command(body["text"], w)
    if bool(w.multi) and actor.get("seat") == "pilot" and any(p.get("type") in ("assume", "release") for p in parsed):
        raise Refused("the controller assumes aircraft, not the pilot")
    return [w.issue(p, source="pilot" if pilot else "command", by_pilot=pilot) for p in parsed]


def _entries(w, body, actor):
    fields = {k: v for k, v in body.items() if k != "callsign"}
    e = w.enter(body["callsign"], fields, who=actor.get("name") or "atc")
    return {**e, "_readback": getattr(w, "last_readback", [])}


def _assume(w, body, actor):
    return w.issue({"callsign": body["callsign"].upper(), "type": "assume"}, source="atc")


def _release(w, body, actor):
    return w.issue({"callsign": body["callsign"].upper(), "type": "release"}, source="atc")


def _mtcd_settings(w, body, actor):
    from . import atctools
    v = atctools.set_look_ahead(w, float(body["look_ahead_min"]) * 60)
    return {"look_ahead_s": v, "conflicts": atctools.mtcd(w)}


def _cpdlc_command(w, body, actor):
    from . import cpdlc
    return cpdlc.command(w, body["text"], body.get("callsign"), voice=bool(body.get("voice")), who=actor.get("name") or "atc",
                         force=bool(body.get("force")))


def _cpdlc_respond(w, body, actor):
    from . import cpdlc
    return cpdlc.respond(w, body["message_id"], body["answer"], body.get("reason"), who=actor.get("name") or "pilot",
                         text=body.get("text"))


def _cpdlc_downlink(w, body, actor):
    from . import cpdlc
    return cpdlc.downlink(w, body["callsign"], body.get("kind") or "", body.get("value"), body.get("reason"),
                          body.get("request_id"), who=actor.get("name") or "pilot")


def _chat(w, body, actor):
    from . import chat
    seat = actor.get("seat") if w.multi else "instructor"
    own = chat.own_station(w)
    frm = chat.resolve(w, body["from"]) if body.get("from") else (own if seat == "atc" else None)
    if frm is None:
        raise ValueError("from: which station are you writing as?")
    to = chat.resolve(w, body["to"]) if body.get("to") else (own if frm != own else None)
    if to is None:
        raise ValueError("to: which station?")
    if seat == "atc" and frm != own:
        raise Refused(f"the controller writes as {own}")
    if seat == "pilot" and frm == own:
        raise Refused(f"the pseudo-pilot writes as another sector or unit, not as {own}")
    return chat.send(w, frm, to, body["text"], body.get("callsign"), body.get("ref"), by=actor.get("name") or seat or "")


def _area(w, body, actor):
    a = w.areas.get(body["area"])
    if a is None:
        raise KeyError("unknown area")
    if a.active != bool(body["active"]):
        a.active = bool(body["active"])
        w._event("area_activated" if a.active else "area_deactivated", area_id=a.id, name=a.name, kind=a.kind, manual=True)
    return a.snapshot(w.t)


def _runway(w, body, actor):
    ad = w.aerodromes.get(body["aerodrome"])
    if ad is None:
        raise KeyError("unknown aerodrome")
    if body["runway"] not in ad.runways:
        raise ValueError(f"runways: {list(ad.runways)}")
    if ad.in_use != body["runway"]:
        ad.in_use = body["runway"]
        w._event("runway_change", aerodrome=ad.id, runway=body["runway"], manual=True,
                 text=f"{ad.id} runway in use now {body['runway']}")
        for a in w.aircraft.values():
            a.arr.pop("_last", None)
    return ad.snapshot()


def _live(w, body, actor):
    from . import live
    who = actor.get("name") or "instructor"
    act = body.get("action")
    if act == "aircraft":
        return live.add_aircraft(w, body, by=who)
    if act == "emergency":
        return live.emergency(w, body.get("callsign") or "", body.get("kind") or "general", body.get("aerodrome"), body.get("why"), by=who)
    if act == "clear_emergency":
        return live.clear_emergency(w, body.get("callsign") or "", by=who)
    if act == "request":
        return live.request(w, body.get("callsign") or "", body.get("kind") or "", body.get("value"), body.get("reason") or "", by=who)
    if act == "readback_rate":
        return live.set_readback_rate(w, body.get("rate") or 0.0, by=who)
    if act == "readback_result":
        return live.readback_result(w, body.get("trap") or 0, bool(body.get("caught")), by=who)
    return live.set_area(w, body.get("area") or "", bool(body.get("active")), by=who)


def _bookmark(w, body, actor):
    from . import debrief
    return debrief.bookmark(w, body.get("note") or "", by=actor.get("name") or "")


def _auto_wx(w, body, actor):
    w.auto_wx = bool(body.get("value", True))
    return {"auto_wx": w.auto_wx}


ACTIONS: Dict[str, Callable] = {
    "instructions": _instructions, "command": _command, "entries": _entries, "assume": _assume, "release": _release,
    "mtcd_settings": _mtcd_settings, "cpdlc_command": _cpdlc_command, "cpdlc_respond": _cpdlc_respond,
    "cpdlc_downlink": _cpdlc_downlink, "chat": _chat, "area": _area, "runway": _runway, "live": _live,
    "bookmark": _bookmark, "auto_wx": _auto_wx,
}
# what a branch of a debrief does not take over from the run it was branched from (notes, not traffic)
NOT_REPLAYED_IN_BRANCHES = {"bookmark", "chat", "mtcd_settings"}


def apply(w, kind: str, body: dict, actor: dict, logged: bool = True):
    """Carry out an action on the world now (the caller holds the session's lock) and log it."""
    out = ACTIONS[kind](w, body, actor)
    if logged:
        log(w).append({"n": len(log(w)), "t": round(w.t, 1), "kind": kind, "body": body,
                       "by": actor.get("name") or "", "seat": actor.get("seat") or ""})
    return out


def run(s, kind: str, body: dict, actor: dict):
    """An action on a session (from the API): a debrief's player branches its timeline first if needed."""
    hook = getattr(s, "on_input", None)
    try:
        if hook is not None:
            return hook(kind, body, actor)
        with s.lock:
            return apply(s.world, kind, body, actor)
    finally:
        s.version = getattr(s, "version", 0) + 1        # the stream sends the new state even when paused
