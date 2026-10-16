"""Headless fast-time evaluation of reference policies (used to verify scenario traps).

  passive        : never says anything. Reveals the conflicts built into the traffic itself (pilots still deviate
                   around weather on their own authority when their requests go unanswered).
  naive_approve  : approves every pilot request the moment it arrives, without checking.
  scripted       : a reference solution, given as a list of steps:
                     {"at": 120, "do": [instruction, ...]}
                     {"on_request": "RAM101", "type": "weather_deviation", "do": [instruction, ...]}
                     {"on_checkin": "RAM101", "delay_s": 30, "do": [instruction, ...]}   (after it calls you)
                   inside "do", {"reply": "approved"|"unable"} answers the request that triggered the step.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .scenario import build_world
from .world import World


def assume_calling(w: World) -> None:
    """Assume every aircraft that has called (what any controller does on first contact)."""
    for a in w.aircraft.values():
        if a.active and a.on_frequency and not a.assumed:
            w.issue({"callsign": a.callsign, "type": "assume"}, source="policy")


def _passive(w: World) -> None:
    assume_calling(w)                  # assumes aircraft on first contact, gives no other instruction


def _naive(w: World) -> None:
    assume_calling(w)
    for r in list(w.requests.values()):
        if r.status == "pending" and r.kind != "checkin":
            w.issue({"callsign": r.callsign, "type": "reply", "request_id": r.id, "decision": "approved"},
                    source="naive")


POLICIES: Dict[str, Callable[[World], None]] = {"passive": _passive, "naive_approve": _naive}


def _scripted(script: List[dict]) -> Callable[[World], None]:
    done_at = set()
    handled = set()
    checkin: Dict[str, float] = {}

    def run(w: World) -> None:
        assume_calling(w)
        for a in w.aircraft.values():
            if a.on_frequency and a.callsign not in checkin:
                checkin[a.callsign] = w.t
        for i, step in enumerate(script):
            due = ("at" in step and w.t >= step["at"]) or (
                "on_checkin" in step and step["on_checkin"] in checkin
                and w.t >= checkin[step["on_checkin"]] + float(step.get("delay_s", 0)))
            if due and i not in done_at:
                done_at.add(i)
                for ins in step["do"]:
                    w.issue(dict(ins), source="script")
        for r in list(w.requests.values()):
            if r.status != "pending" or r.id in handled:
                continue
            for step in script:
                if step.get("on_request") == r.callsign and step.get("type", r.type) == r.type:
                    handled.add(r.id)
                    for ins in step["do"]:
                        if "reply" in ins:
                            w.issue({"callsign": r.callsign, "type": "reply", "request_id": r.id,
                                     "decision": ins["reply"]}, source="script")
                        else:
                            w.issue(dict(ins), source="script")
                    break
    return run


def run(scn: dict, policy: str = "passive", script: Optional[List[dict]] = None, trace: bool = False) -> dict:
    w = build_world(scn)
    fn = _scripted(script or []) if policy == "scripted" else POLICIES[policy]
    tr: Dict[str, list] = {}
    while not w.finished:
        fn(w)
        w.step(1.0)
        if trace:
            for a in w.aircraft.values():
                if a.active:
                    tr.setdefault(a.callsign, []).append((w.t, a.x, a.y, a.alt_ft, a.on_frequency))
    m = w.metrics()
    return {
        "los_events": m["separation"]["loss_events"],
        "los_pairs": sorted({tuple(p["aircraft"]) for p in m["separation"]["losses"]}),
        "min_h": min((p["min_horizontal_nm"] for p in m["separation"]["losses"]), default=None),
        "infringements": m["airspace"]["infringement_events"],
        "infringed": sorted({(x["callsign"], x["area_id"]) for x in m["airspace"]["log"]}),
        "closest": m["separation"]["closest_same_level_approach"][:3],
        "left_sector": m["traffic"]["left_sector_off_route"],
        "rejected": [(i["request"].get("callsign"), i.get("reason")) for i in w.instructions
                     if i["status"] == "rejected"],
        "wx_penetrations": [(p["callsign"], p.get("max_dbz")) for p in m["weather"]["penetrations"]],
        "wx_self_deviations": m["weather"]["deviations_without_clearance"],
        "wx_requests": [(r.callsign, r.type, r.phrase()) for r in w.requests.values() if r.origin == "pilot"],
        "requests": [r.snapshot() for r in w.requests.values()],
        "rvsm_violations": [(v["callsign"], v["max_fl"]) for v in m["rvsm"]["violations"]],
        "wrong_direction_exits": [(v["callsign"], v["fl"]) for v in m["semicircular"]["wrong_direction_exits"]],
        "metrics": m,
        "trace": tr,
    }
