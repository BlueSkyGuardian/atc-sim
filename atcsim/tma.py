"""The simulated approach units keep the traffic they work separated - from each other and from everyone else.

Traffic "with approach": a departure on its SID before its release to us, and an arrival the next units have taken
over (approach.py). Every few seconds each one is checked against every other aircraft around (its own traffic, our
traffic, the en-route traffic, the next sectors') over the next two and a half minutes (present positions, tracks,
ground speeds, rates of climb or descent up to the cleared levels). When a pair would come within the minima:

  - a departure stops its climb below the other (or levels where it is when it is already close under it);
  - an arrival stops its descent above the other (in a hold: the stack, 1000 ft above the one below);
  - between two of approach's own: the departure under an arrival stays under it, the departure above one keeps the
    arrival above... the one that can give way without a turn does;
  - with our traffic or the en-route traffic, approach's aircraft always gives way (never ours).

The restriction ("approach_restriction" event) holds until the pair is clear with the aircraft's own clearance; a
departure still restricted when it is released calls us at that level, separated. Minima: 3 NM between approach's own
traffic within 25 NM of the aerodrome, otherwise the airspace's radar minimum; the vertical minimum of the pair.

Also here: the departure interval off the same aerodrome (2 minutes between take-offs, none while an arrival is
within 4 NM of touchdown), and the change of runway (departures take the SID of the runway in use when they take
off; approach re-plans the arrivals not yet established).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .geometry import dist

if TYPE_CHECKING:
    from .aircraft import Aircraft
    from .world import World

PERIOD_S = 4.0
HORIZON_S = 150.0
STEP_S = 10.0
TMA_H_NM = 3.0                # between approach's own traffic near the aerodrome
TMA_RADIUS_NM = 25.0
DEP_INTERVAL_S = 120.0        # between departures off the same aerodrome
ARR_SHORT_FINAL_NM = 4.0      # no take-off with an arrival this close to touchdown


def _dep(a) -> bool:
    s = getattr(a, "sid", None)
    return bool(s) and not s.get("released") and a.active


def _arr(a) -> bool:
    return bool(getattr(a, "app", None)) and not getattr(a, "landed", False) and not a.gone


def with_approach(a) -> bool:
    return _dep(a) or _arr(a)


def _intended(a) -> float:
    """The level the aircraft would go to without approach's restriction."""
    r = getattr(a, "tma_cap", None)
    if r is not None and "own_ft" in r:
        return float(r["own_ft"])
    return float(a.cleared_alt_ft)


def _track(a, cleared: float) -> List[Tuple[float, float, float]]:
    """Straight-line prediction (x, y, alt) every STEP_S over HORIZON_S, the level change at its present rate (or
    1500 ft/min) up to the cleared level."""
    r = math.radians(a.trk)
    vx, vy = a.gs * math.sin(r) / 3600.0, a.gs * math.cos(r) / 3600.0
    rate = abs(a.vs_fpm) if abs(a.vs_fpm) > 300 else 1500.0
    out = []
    t = 0.0
    while t <= HORIZON_S + 1e-6:
        dz = cleared - a.alt_ft
        z = a.alt_ft + math.copysign(min(abs(dz), rate * t / 60.0), dz)
        out.append((a.x + vx * t, a.y + vy * t, z))
        t += STEP_S
    return out


def _minima(world, a, b) -> Tuple[float, float]:
    from .world import pair_minima
    h, v = pair_minima(a, b, h_nm=world.sep_h)
    if with_approach(a) and with_approach(b):
        ads = [world.aerodromes.get(x) for x in (_ad_of(a), _ad_of(b))]
        if all(ad is not None and dist(ad.pos, p.pos) <= TMA_RADIUS_NM for ad, p in zip(ads, (a, b))):
            h = min(h, TMA_H_NM)
    return h, v


def _unit(world, a) -> str:
    """Who is working it: the aerodrome's approach near the field, the previous sector further out."""
    ad = world.aerodromes.get(_ad_of(a) or "")
    if ad is not None and dist(ad.pos, a.pos) <= TMA_RADIUS_NM:
        return f"{ad.id} approach"
    pu = getattr(a, "prev_unit", "") or ""
    return f"{pu} control" if pu and pu != (_ad_of(a) or "") else "Casablanca control"


def _ad_of(a) -> Optional[str]:
    if getattr(a, "app", None):
        return a.app.get("ad")
    s = getattr(a, "sid", None)
    return s.get("aerodrome") if s else None


def _conflict(world, a, b, alt_a: float) -> Optional[float]:
    """Time (s) of the first predicted loss between a (going to alt_a) and b, or None."""
    h, v = _minima(world, a, b)
    ta = _track(a, alt_a)
    tb = _track(b, _intended(b) if with_approach(b) else float(b.cleared_alt_ft))
    for k, (pa, pb) in enumerate(zip(ta, tb)):
        if abs(pa[2] - pb[2]) < v - 50 and math.hypot(pa[0] - pb[0], pa[1] - pb[1]) < h:
            return k * STEP_S
    return None


def _both_on_final(a, b) -> bool:
    return (_arr(a) and _arr(b) and a.app.get("established") and b.app.get("established")
            and a.app.get("ad") == b.app.get("ad"))


def _restrict(world, a, kind: str, alt: float, other, own: float) -> None:
    alt = float(int(alt // 1000 * 1000) if kind == "ceiling" else int(math.ceil(alt / 1000.0) * 1000))
    if _dep(a) and kind == "ceiling" and alt < a.alt_ft - 150:
        alt = float(round(a.alt_ft, -1))                             # a departure above it already: level off now
    # (an arrival is given the level: down under the traffic, or up above it)
    old = getattr(a, "tma_cap", None)
    if old and old["kind"] == kind and old["vs"] == other.callsign and abs(old["alt"] - alt) < 600:
        old["t"] = world.t
        old["alt"] = alt                                  # a small adjustment: no new instruction
        return
    a.tma_cap = {"kind": kind, "alt": alt, "vs": other.callsign, "t": world.t, "own_ft": own}
    unit = _unit(world, a)
    verb = "stop climb at" if kind == "ceiling" else "stop descent at"
    lvl = f"FL{int(alt // 100):03d}" if alt >= 6000 else f"{int(alt)} ft"
    world._event("approach_restriction", callsign=a.callsign, traffic=other.callsign, kind=kind, alt_ft=alt,
                 text=f"{unit}: {a.callsign} {verb} {lvl}, traffic {other.callsign}")


def _apply(a) -> None:
    r = getattr(a, "tma_cap", None)
    if not r:
        return
    if r["kind"] == "ceiling":
        a.cleared_alt_ft = min(a.cleared_alt_ft, r["alt"])
    else:
        a.cleared_alt_ft = max(a.cleared_alt_ft, r["alt"])


def update(world: "World") -> None:
    ctl = [a for a in world.aircraft.values() if (a.active or a.outside) and not a.gone and with_approach(a)]
    # the level each would go to on its own (approach.py may have set a new one this step)
    for a in ctl:
        r = getattr(a, "tma_cap", None)
        if r is not None:
            if _dep(a):
                r["own_ft"] = float(getattr(a, "initial_cfl", 0) or 0) * 100.0 or r.get("own_ft", a.cleared_alt_ft)
            elif abs(a.cleared_alt_ft - r["alt"]) > 1:
                r["own_ft"] = float(a.cleared_alt_ft)
    for a in world.aircraft.values():                  # restrictions of aircraft no longer with approach
        if getattr(a, "tma_cap", None) and not with_approach(a) and not (a.active and getattr(a, "sid", None)):
            a.tma_cap = None
    if world.t - getattr(world, "_tma_last", -1e9) >= PERIOD_S:
        world._tma_last = world.t
        others = [b for b in world.aircraft.values()
                  if (b.active or b.outside) and not b.gone and not getattr(b, "landed", False)]
        for a in sorted(ctl, key=lambda x: x.callsign):
            r = getattr(a, "tma_cap", None)
            own = _intended(a)
            worst = None
            for b in others:
                if b is a or _both_on_final(a, b):
                    continue
                if abs(b.alt_ft - a.alt_ft) > 8000 and abs(_intended(b) - own) > 8000:
                    continue
                if dist(a.pos, b.pos) > 40.0:
                    continue
                t = _conflict(world, a, b, own)
                if t is not None and (worst is None or t < worst[0]):
                    worst = (t, b)
            if worst is None:
                if r is not None:
                    a.tma_cap = None                     # clear with its own clearance: restriction lifted
                    world._event("approach_restriction_lifted", callsign=a.callsign,
                                 text=f"{a.callsign} restriction lifted, {'climb' if _dep(a) else 'descend'} as cleared")
                continue
            _, b = worst
            if _arr(a) and _arr(b) and not a.app.get("established") and not b.app.get("established"):
                _pair_arrivals(world, a, b)              # two of approach's arrivals: one down, the other stops
                continue
            if not _gives_way(a, b):
                continue                                 # the other one (also approach's) gives way
            bz = min(b.alt_ft, b.cleared_alt_ft) if b.cleared_alt_ft < b.alt_ft else b.alt_ft
            bz_hi = max(b.alt_ft, b.cleared_alt_ft) if b.cleared_alt_ft > b.alt_ft else b.alt_ft
            _, vmin = _minima(world, a, b)
            lowest = _lowest(world, a)
            under, over = bz - vmin, bz_hi + vmin
            if _dep(a):
                # a departure: stop its climb under the traffic (it is not sent down again)
                if a.alt_ft <= under + 150 and under >= lowest:
                    _restrict(world, a, "ceiling", under, b, own)
                    continue
                if a.alt_ft > b.alt_ft and a.alt_ft >= over - 150:
                    _restrict(world, a, "floor", over, b, own)
                    continue
            else:
                # an arrival is given a level: under the traffic when it is under it and there is room above the
                # FAF altitude, else above it (the later of two in the sequence climbs over the other)
                if a.alt_ft <= b.alt_ft + 300 and under >= lowest:
                    _restrict(world, a, "ceiling", under, b, own)
                    continue
                if a.alt_ft > b.alt_ft - 300 and over <= max(a.alt_ft + 3000.0, own):
                    _restrict(world, a, "floor", over, b, own)
                    continue
                if over <= a.alt_ft + 3000.0 and with_approach(b) and _later(world, a, b):
                    _restrict(world, a, "floor", over, b, own)
                    continue
            _vector_away(world, a, b)                    # no level keeps the minimum: a turn away
        # a level restriction that leaves it in conflict with another one (between two aircraft): a turn away
        for a in ctl:
            r = getattr(a, "tma_cap", None)
            if not r or world.t - r["t"] > PERIOD_S + 0.5:
                continue
            for b in others:
                if b is a or b.callsign == r["vs"] or _both_on_final(a, b) or dist(a.pos, b.pos) > 25.0:
                    continue
                if _conflict(world, a, b, r["alt"]) is not None and (not with_approach(b) or _gives_way(a, b)):
                    _vector_away(world, a, b)
                    break
    for a in ctl:
        _apply(a)


VECTOR_NM = 6.0
VECTOR_EVERY_S = 60.0


def _pair_arrivals(world, a, b) -> None:
    """Two arrivals of approach closing at about the same level: the lower one continues down to the minimum under
    the other (when the FAF altitude leaves room), the upper one stops its descent where it is; else the later one in
    the sequence is kept above."""
    lo, hi = (a, b) if a.alt_ft <= b.alt_ft else (b, a)
    _, vmin = _minima(world, a, b)
    under = hi.alt_ft - vmin
    if under >= _lowest(world, lo):
        _restrict(world, lo, "ceiling", under, hi, _intended(lo))
        _restrict(world, hi, "floor", max(hi.alt_ft, lo.alt_ft + vmin) if lo.alt_ft + vmin <= hi.alt_ft + 150
                  else hi.alt_ft, lo, _intended(hi))
        return
    first, later = (a, b) if _later(world, b, a) else (b, a)
    _restrict(world, later, "floor", first.alt_ft + vmin, first, _intended(later))


def _later(world, a, b) -> bool:
    """a lands after b (or b is not one of approach's arrivals)."""
    if not (_arr(a) and _arr(b)) or a.app.get("ad") != b.app.get("ad"):
        return True
    seq = getattr(world, "_landing_seq", {}).get(a.app["ad"], [])
    return a.callsign in seq and b.callsign in seq and seq.index(a.callsign) > seq.index(b.callsign)


def _lowest(world, a) -> float:
    """The lowest a restriction may keep it: 1500 ft above the field for a departure, the FAF altitude (or the
    holding level) for an arrival."""
    ad = world.aerodromes.get(_ad_of(a) or "")
    if ad is None:
        return 0.0
    if _dep(a):
        return ad.elev_ft + 1500.0
    return ad.faf_alt_ft


def _vector_away(world, a, b) -> None:
    """A turn away from the traffic: a point ahead and to the side away from it, then its route (the approach path,
    or the SID) again."""
    if world.t - getattr(a, "_tma_vec_t", -1e9) < VECTOR_EVERY_S:
        return
    if _arr(a) and a.app.get("established"):
        return
    a._tma_vec_t = world.t
    r = math.radians(a.trk)
    ux, uy = math.sin(r), math.cos(r)
    dx, dy = a.x - b.x, a.y - b.y
    side = 1.0 if (dx * uy - dy * ux) >= 0 else -1.0          # b on the left: turn right (and the reverse)
    nx, ny = uy * side, -ux * side
    p = (a.x + ux * 3.0 + nx * VECTOR_NM, a.y + uy * 3.0 + ny * VECTOR_NM)
    for w in [w for w in a.route[a.next_index:] if w.startswith(f"@{a.callsign}~V")]:
        a.route.remove(w)                                 # the new vector replaces the last one
        world.waypoints.pop(w, None)
        if _arr(a) and w in a.app["names"]:
            a.app["names"].remove(w)
    k = getattr(a, "_tma_vec_n", 0) + 1
    a._tma_vec_n = k
    name = f"@{a.callsign}~V{k}"
    world.waypoints[name] = p
    a.route = a.route[:a.next_index] + [name] + a.route[a.next_index:]
    a.leg_from = a.pos
    a._dct = True
    a.nav_mode, a.target_hdg, a.turn_dir = "lnav", None, None
    if _arr(a):
        a.app["names"].insert(0, name)
        a.app["stretch_t"] = world.t
    hdg = int(round(math.degrees(math.atan2(p[0] - a.x, p[1] - a.y)) % 360 / 5.0) * 5) % 360 or 360
    unit = _unit(world, a)
    world._event("approach_vector", callsign=a.callsign, traffic=b.callsign,
                 text=f"{unit}: {a.callsign} turn {'right' if side > 0 else 'left'} heading {hdg:03d}, "
                      f"traffic {b.callsign}")


def _gives_way(a, b) -> bool:
    """Which of the pair moves: approach's aircraft against anyone else; between two of approach's, the departure
    under an arrival (or the higher of two arrivals, the lower of two departures)."""
    if _arr(a) and a.app.get("established"):
        return False                                     # on the glide path: the other one moves
    if not with_approach(b):
        return True
    if _dep(a) and _arr(b):
        return True
    if _arr(a) and _dep(b):
        return a.alt_ft > b.alt_ft + 500                 # the arrival above stays above; else the departure stops
    if _arr(a) and _arr(b):
        if getattr(a, "app", {}).get("established"):
            return False
        return a.alt_ft >= b.alt_ft                      # the higher one stops its descent
    return a.alt_ft <= b.alt_ft                          # two departures: the lower one stops its climb


# ----------------------------------------------------------------------------- take-offs and runway changes
def may_take_off(world: "World", ac: "Aircraft") -> Tuple[bool, str]:
    """Whether a SID departure can take off now: 2 minutes after the last one off the aerodrome, and no arrival
    within 4 NM of touchdown."""
    s = getattr(ac, "sid", None)
    if not s:
        return True, ""
    adid = s["aerodrome"]
    last = getattr(world, "_last_takeoff", {}).get(adid)
    if last is not None and world.t - last < DEP_INTERVAL_S:
        return False, "departure interval"
    from .approach import _to_go
    for b in world.aircraft.values():
        if _arr(b) and b.app.get("ad") == adid and b.app.get("established"):
            try:
                if _to_go(world, b) < ARR_SHORT_FINAL_NM:
                    return False, f"{b.callsign} on short final"
            except Exception:  # noqa: BLE001
                pass
    return True, ""


def took_off(world: "World", ac: "Aircraft") -> None:
    world.__dict__.setdefault("_last_takeoff", {})[ac.sid["aerodrome"]] = world.t


def resid(world: "World", ac: "Aircraft") -> None:
    """At take-off: the SID of the runway in use (it may have changed since the scenario was built) - the one to the
    same end, the release point kept."""
    from .arrivals import sid_path
    from .geometry import project_on_polyline, point_along_polyline
    s = ac.sid
    ad = world.aerodromes.get(s["aerodrome"])
    if ad is None or ad.in_use == s["runway"]:
        return
    cands = [x for x in ad.sids if ad.in_use in x["runways"]]
    old = next((x for x in ad.sids if x["name"] == s["name"]), None)
    if not cands:
        return
    end = old["end"] if old else None
    new = next((x for x in cands if x["end"] == end), None)
    rel = world.waypoints.get(ac.entry_wp)
    if new is None and rel is not None:
        new = min(cands, key=lambda x: dist(x["via"][-1][1], rel))
    if new is None or rel is None:
        return
    path = sid_path(ad, new, ad.in_use)
    xy = [p for _, p in path]
    along, off, _ = project_on_polyline(xy, rel)
    k = ac.route.index(ac.entry_wp) if ac.entry_wp in ac.route else 0
    for w in ac.route[:k]:
        if w.startswith(f"@{ac.callsign}~S"):
            world.waypoints.pop(w, None)
    _, nxt = point_along_polyline(xy, along) if off <= 8.0 else (None, len(xy))
    pre = []
    for i, (nm, p) in enumerate(path[:nxt]):
        w = nm or f"@{ac.callsign}~S{i}"
        if nm is None:
            world.waypoints[w] = p
        pre.append(w)
    ac.route = pre + ac.route[k:]
    world._event("sid_changed", callsign=ac.callsign, sid=new["name"], runway=ad.in_use,
                 text=f"{ac.callsign} departing runway {ad.in_use}: {new['name']} (was {s['name']})")
    s.update(name=new["name"], runway=ad.in_use, max_kt=new["max_kt"])
    if ac.flight_plan.get("sid"):
        ac.flight_plan["route"] = str(ac.flight_plan.get("route", "")).replace(ac.flight_plan["sid"], new["name"], 1)
        ac.flight_plan["sid"] = new["name"]
