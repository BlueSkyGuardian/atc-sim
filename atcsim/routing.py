"""Flight-plan routes beyond the controlled airspace.

Each aircraft's flight plan continues after its exit point (and starts before its entry point) through points of the
neighbouring sectors, chosen by transfer point and destination (or departure) from the airspace's `onward_routes`
rules. The simulation flies only the controlled part, but the points before/after are in the flight plan, painted on
the radar, used to place the aircraft before it enters, and valid targets for direct-to clearances.
"""
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional, Sequence, Tuple


def _matches(code: str, entries: Sequence[str]) -> bool:
    code = (code or "").upper()
    if not code:
        return False
    for e in entries:
        e = e.upper()
        if code == e or (len(e) <= 2 and code.startswith(e)):
            return True
    return False


def _cond(rule: dict, groups: dict, key: str, code: str) -> bool:
    """dest/dep (exact codes) and dest_group/dep_group (named prefix groups) conditions."""
    exact = rule.get(key)
    if exact is not None:
        lst = [exact] if isinstance(exact, str) else exact
        if not _matches(code, lst):
            return False
    grp = rule.get(f"{key}_group")
    if grp is not None:
        names = [grp] if isinstance(grp, str) else grp
        if not any(_matches(code, groups.get(g, [])) for g in names):
            return False
    return True


def _pick(options: List[dict], seed: str) -> List[str]:
    tot = sum(float(o.get("w", 1.0)) for o in options)
    h = int(hashlib.md5(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * tot
    for o in options:
        h -= float(o.get("w", 1.0))
        if h <= 0:
            return list(o["points"])
    return list(options[-1]["points"])


def rule_for(asp, point: str, side: str, dest: str = "", dep: str = "") -> Optional[dict]:
    spec = (asp.notes or {}).get("onward_routes") or {}
    groups = spec.get("groups", {})
    for r in spec.get("rules", []):
        at = [r["at"]] if isinstance(r["at"], str) else r["at"]
        if point not in at or r.get("side", "exit") != side:
            continue
        if side == "exit" and not _cond(r, groups, "dest", dest):
            continue
        if side == "entry" and not _cond(r, groups, "dep", dep):
            continue
        return r
    return None


def options_for(asp, point: str, side: str, dest: str = "", dep: str = "",
                runways: Optional[Dict[str, str]] = None) -> List[List[str]]:
    """Every alternative for this transfer point (for the designer's route picker)."""
    r = rule_for(asp, point, side, dest, dep)
    if r is None:
        return []
    if "by_runway" in r:
        rw = (runways or {}).get((dest or "").upper())
        if rw in r["by_runway"]:
            return [list(r["by_runway"][rw])]
        return [list(v) for v in r["by_runway"].values()]
    return [list(o["points"]) for o in r.get("options", [])]


def complete(asp, names: Sequence[str], dest: str = "", dep: str = "", key: str = "",
             runways: Optional[Dict[str, str]] = None) -> Tuple[List[str], List[str]]:
    """Points to fly before the first and after the last point of `names` (empty when the route already starts
    or ends outside the controlled airspace, or when no rule applies)."""
    names = [n.upper() for n in names]
    if not names or not (asp.notes or {}).get("onward_routes"):
        return [], []
    ext = lambda n: bool(asp.point_info.get(n, {}).get("external"))
    before, after = [], []
    if not ext(names[0]):
        r = rule_for(asp, names[0], "entry", dest, dep)
        if r is not None:
            before = _pick(r["options"], f"{key}|{names[0]}|in")
    if not ext(names[-1]):
        r = rule_for(asp, names[-1], "exit", dest, dep)
        if r is not None:
            if "by_runway" in r:
                rw = (runways or {}).get((dest or "").upper())
                choices = r["by_runway"]
                after = list(choices[rw]) if rw in choices else list(next(iter(choices.values())))
            else:
                after = _pick(r["options"], f"{key}|{names[-1]}|out")
    before = [p for p in before if p in asp.waypoints and p not in names]
    after = [p for p in after if p in asp.waypoints and p not in names]
    return before, after
