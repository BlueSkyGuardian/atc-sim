"""After the handover: the next units (the sector after ours, then approach) bring arrivals down to land.

Every aircraft that has left our sectors (transferred or handed down into a TMA) and whose destination is one of the
airspace's aerodromes is flown to its runway by simulated controllers:

  lateral   : the rest of its flight plan towards the aerodrome (onward points that still bring it closer), then the
              approach path from there (straight-in, or the reversal procedure when it comes from the far side):
              intermediate fix, FAF on the extended centreline, threshold - the ILS is intercepted at the FAF
  vertical  : cleared down to the FAF altitude at its own top of descent (idle descent with its speed schedule),
              then the 3 degree glide path from the FAF (318 ft per NM) to the threshold
  speed     : its descent schedule (250 kt below FL100), 210 kt inside 25 NM to go, 180 kt from 4 NM before the
              FAF, final approach speed on the glide path
  sequence  : the arrivals to one aerodrome keep the order they come in (distance to touchdown); a follower closer than
              the approach spacing (6 NM, 7 NM behind a heavy) to the one ahead is slowed (never below its final
              approach speed until the FAF) - the next units keep the sequence the controller built
  landing   : at the threshold the aircraft lands and leaves the picture ("landed" event)

The aircraft stays an "outside" aircraft: the controller sees it on the scope, and may still instruct it (with a
reminder to coordinate); the simulated units take it back on their next update.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Dict, List

from .arrivals import approach_path, descent_distance
from .geometry import dist, polyline_length

if TYPE_CHECKING:
    from .aircraft import Aircraft
    from .world import World

PERIOD_S = 2.0
GLIDE_FT_PER_NM = 318.0              # 3 degrees
APP_SPACING_NM = 6.0
HEAVY_SPACING_NM = 7.0
FINAL_KT = 145.0
LANDED_NM = 0.4
HOLD_LAP_NM = 21.0                 # a 1-minute racetrack at about 220 kt
HOLD_AGL_FT = 6000.0


def _dest(world: "World", ac: "Aircraft"):
    code = (ac.destination or ac.flight_plan.get("destination") or "").upper()
    return world.aerodromes.get(code)


def candidates(world: "World") -> List["Aircraft"]:
    return [a for a in world.aircraft.values()
            if a.outside and not a.gone and not getattr(a, "landed", False) and _dest(world, a) is not None]


def _start(world: "World", ac: "Aircraft") -> None:
    """The path to the runway: onward points that still bring it closer, then the approach procedure."""
    ad = _dest(world, ac)
    ahead = [w for w in ac.route[ac.next_index:] if w in world.waypoints and not w.startswith("@")]
    pts, prev = [], ac.pos
    for w in ahead:
        p = world.waypoints[w]
        if dist(p, ad.pos) >= dist(prev, ad.pos) - 1.0 or dist(p, ad.pos) < ad.final_nm + 8:
            break                              # past the aerodrome, or already close: the approach takes over
        pts.append(w)
        prev = p
    start = world.waypoints[pts[-1]] if pts else ac.pos
    path = approach_path(start, ad, ad.in_use)
    names = []
    for k, p in enumerate(path["points"][1:], 1):
        n = f"@{ac.callsign}~A{k}"
        world.waypoints[n] = (float(p[0]), float(p[1]))
        names.append(n)
    ac.route = ac.route[:ac.next_index] + pts + names
    ac.leg_from = ac.pos
    ac.nav_mode, ac.target_hdg, ac.turn_dir = "lnav", None, None
    ac.app = {"ad": ad.id, "runway": ad.in_use, "type": path["type"], "thr": tuple(path["threshold"]),
              "faf": tuple(path["faf"]), "names": names, "last": -1e9, "established": False, "cleared_down": False}
    seqs = getattr(world, "_landing_seq", None)
    if seqs is None:
        seqs = world._landing_seq = {}
    seqs.setdefault(ad.id, []).append(ac.callsign)     # the order the controller handed them over in
    world._event("approach_started", callsign=ac.callsign, aerodrome=ad.id, runway=ad.in_use, approach=path["type"],
                 text=f"{ac.callsign} with the next units: {ad.id} runway {ad.in_use} ({path['type']})")


def _to_go(world: "World", ac: "Aircraft") -> float:
    pts = [ac.pos] + [world.waypoints[w] for w in ac.route[ac.next_index:] if w in world.waypoints]
    return polyline_length(pts)


def _eta(world: "World", ac: "Aircraft", to_go: float) -> float:
    """Seconds to the threshold with the approach speed profile: its own speed beyond 25 NM to go, about 230 kt of
    ground speed down to 4 NM before the FAF, 180 kt to the FAF, the final approach speed on the glide path."""
    final = world.aerodromes[ac.app["ad"]].final_nm
    legs = [(max(0.0, to_go - 25.0), max(ac.gs, 200.0)), (max(0.0, min(to_go, 25.0) - final - 4.0), 250.0),
            (max(0.0, min(to_go, final + 4.0) - final), 180.0), (min(to_go, final), FINAL_KT)]
    return sum(d / v for d, v in legs) * 3600.0


def _sep_s(ahead: "Aircraft") -> float:
    heavy = str(ahead.flight_plan.get("wake", "")) in ("H", "J")
    return (HEAVY_SPACING_NM if heavy else APP_SPACING_NM) / FINAL_KT * 3600.0


def _schedule(world: "World", ac: "Aircraft") -> None:
    """Landing time slot: its own estimate, or the slot behind the last one handed over before it (6 NM, 7 NM
    behind a heavy); the delay is absorbed now - holding at the start of the approach when it is long, then a dogleg."""
    st = ac.app
    last = getattr(world, "_landing_sta", {}).get(st["ad"])
    eta0 = world.t + _eta(world, ac, _to_go(world, ac))
    sta = eta0 if last is None else max(eta0, last[0] + _sep_s(last[1]))
    st["sta"] = sta
    world.__dict__.setdefault("_landing_sta", {})[st["ad"]] = (sta, ac)
    delay_nm = (sta - eta0) * 220.0 / 3600.0
    if delay_nm > 30.0:
        laps = int((delay_nm - 8.0) // HOLD_LAP_NM)
        _hold(world, ac, laps)
        delay_nm -= laps * HOLD_LAP_NM
    if delay_nm > 2.0:
        _stretch(world, ac, delay_nm)


def update(world: "World") -> None:
    arr = candidates(world)
    if not arr:
        return
    by_ad: Dict[str, list] = {}
    for ac in arr:
        if getattr(ac, "app", None) is None:
            _start(world, ac)
            _schedule(world, ac)
        if world.t - ac.app["last"] < PERIOD_S:
            continue
        ac.app["last"] = world.t
        by_ad.setdefault(ac.app["ad"], []).append(ac)
    for ad_id, lst in by_ad.items():
        ad = world.aerodromes[ad_id]
        order = [cs for cs in world._landing_seq.get(ad_id, []) if cs in world.aircraft
                 and not getattr(world.aircraft[cs], "landed", False) and not world.aircraft[cs].gone]
        prev = None
        for cs in order:
            ac = world.aircraft[cs]
            to_go = _to_go(world, ac)
            eta = world.t + _eta(world, ac, to_go)
            if ac in lst:
                _fly(world, ac, ad, to_go, eta, prev)
            prev = (to_go, eta, ac)


def _fly(world: "World", ac: "Aircraft", ad, to_go: float, eta: float, ahead) -> None:
    st = ac.app
    thr_d = dist(ac.pos, st["thr"])
    if to_go <= LANDED_NM or (thr_d <= LANDED_NM + 0.6 and ac.next_index >= len(ac.route) - 1):
        ac.landed = True
        ac.gone = True
        ac.vs_fpm = 0.0
        world._event("landed", callsign=ac.callsign, aerodrome=ad.id, runway=st["runway"],
                     text=f"{ac.callsign} landed {ad.id} runway {st['runway']}")
        for n in st["names"]:
            world.waypoints.pop(n, None)
        return
    final_nm = ad.final_nm
    faf_alt = ad.faf_alt_ft
    # vertical: down to the FAF altitude at its top of descent (the holding level first), then the glide path
    if to_go <= final_nm + 0.5:
        st["established"] = True
    if st["established"]:
        # on the ILS: gear and flaps out (drag), descending with the glide path
        gp = ad.elev_ft + max(0.0, to_go - LANDED_NM) * GLIDE_FT_PER_NM
        ac.cleared_alt_ft = max(ad.elev_ft, min(ac.cleared_alt_ft, gp))
        ac.speedbrake = True
        ac.vs_cmd_fpm = max(300.0, ac.gs * GLIDE_FT_PER_NM / 60.0 + max(0.0, ac.alt_ft - gp) * 1.5)
        ac.vs_cmd_mode = "min"
    else:
        floor = faf_alt
        if st.get("hold_names") and any(n in ac.route[ac.next_index:] for n in st["hold_names"][1:]):
            floor = max(faf_alt, ad.elev_ft + HOLD_AGL_FT)           # holding: no lower than the holding level
        if ac.cleared_alt_ft > floor + 50:
            need = descent_distance(ac, ac.alt_ft, floor, lambda a: 0.0)
            if to_go - final_nm <= need + 3.0 or st["cleared_down"] or floor > faf_alt:
                ac.cleared_alt_ft = floor
                st["cleared_down"] = True
                ac.speedbrake = to_go - final_nm < need - 4.0
        elif ac.cleared_alt_ft > faf_alt + 50 and floor == faf_alt:
            ac.cleared_alt_ft = faf_alt
    # speed: the schedule, then 210 / 180 kt, final approach speed on the glide path
    want = None
    if st["established"]:
        want = FINAL_KT
    elif to_go <= final_nm + 4.0:
        want = 180.0
    elif to_go <= 25.0 or st.get("hold_names"):
        want = 210.0
    # its landing slot: never earlier than the one ahead (as it really flies) plus the approach spacing
    sta = st["sta"]
    if ahead is not None:
        sta = max(sta, ahead[1] + _sep_s(ahead[2]))
    early = sta - eta                                   # seconds too early for its slot
    if early > 40 and not st["established"] and to_go > final_nm + 3 and world.t - st.get("stretch_t", -1e9) > 60:
        _stretch(world, ac, 0.8 * early * max(ac.gs, 180.0) / 3600.0)
    elif early > 10:
        floor_kt = FINAL_KT if st["established"] or to_go <= final_nm + 4.0 else 180.0
        cur = ac.cas()
        want = max(floor_kt, min(want or cur, cur - (20.0 if early > 30 else 10.0)))
    if want is not None and ac.alt_ft < 26000:
        ac.spd_cas, ac.spd_ias_now, ac.spd_mach = float(want), True, None


def _hold(world: "World", ac: "Aircraft", laps: int) -> None:
    """Holding at the first point of the approach: a racetrack (1 minute legs) away from the aerodrome, flown `laps`
    times before going on."""
    st = ac.app
    if laps <= 0 or not st["names"]:
        return
    first = st["names"][0]
    fx = world.waypoints[first]
    ad = world.aerodromes[st["ad"]]
    L = max(dist(fx, ad.pos), 1.0)
    ux, uy = (fx[0] - ad.pos[0]) / L, (fx[1] - ad.pos[1]) / L          # outbound: away from the aerodrome
    nx, ny = uy, -ux                                                 # right turns
    pts = [(fx[0] + nx * 3.5, fx[1] + ny * 3.5), (fx[0] + nx * 3.5 + ux * 7, fx[1] + ny * 3.5 + uy * 7),
           (fx[0] + ux * 7, fx[1] + uy * 7), fx]
    names = []
    for k in range(laps):
        for j, p in enumerate(pts):
            n = f"@{ac.callsign}~H{k}{j}"
            world.waypoints[n] = p
            names.append(n)
    i = ac.route.index(first, ac.next_index)
    ac.route = ac.route[:i + 1] + names + ac.route[i + 1:]
    st["hold_names"] = [first] + names
    st["names"] = names + st["names"]
    world._event("approach_holding", callsign=ac.callsign,
                 text=f"{ac.callsign} to hold at the approach entry, {laps} lap{'s' if laps > 1 else ''}")


def _stretch(world: "World", ac: "Aircraft", extra_nm: float) -> None:
    """A dogleg on the next leg (before the final approach fix) adding extra_nm to the path."""
    st = ac.app
    rest = ac.route[ac.next_index:]
    nxt = next((w for w in rest if w in world.waypoints), None)
    if nxt is None or nxt == st["names"][-1]:
        return                                   # already on final
    a, b = ac.pos, world.waypoints[nxt]
    L = dist(a, b)
    extra = min(extra_nm, 25.0)
    if L < 4.0:
        return
    h = math.sqrt(max(((L + extra) / 2) ** 2 - (L / 2) ** 2, 0.0))
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    side = 1.0 if (st.get("stretches", 0) % 2 == 0) else -1.0
    mid = (a[0] + ux * L / 2 + side * uy * h, a[1] + uy * L / 2 - side * ux * h)
    st["stretches"] = st.get("stretches", 0) + 1
    name = f"@{ac.callsign}~D{st['stretches']}"
    world.waypoints[name] = mid
    st["names"].insert(0, name)
    ac.route = ac.route[:ac.next_index] + [name] + rest
    ac.leg_from = ac.pos
    st["stretch_t"] = world.t
    world._event("approach_vector", callsign=ac.callsign,
                 text=f"{ac.callsign} vectored by approach for spacing (+{extra:.0f} NM)")
