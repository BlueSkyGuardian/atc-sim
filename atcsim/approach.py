"""After the handover: the next units (the sector after ours, then approach) bring arrivals down to land.

Every aircraft that has left our sectors (transferred or handed down into a TMA) and whose destination is one of the
airspace's aerodromes is flown to its runway by simulated controllers - once it is 30 NM past its transfer point and
the controller has decontrolled it and every other transferred arrival to that aerodrome (until then it flies its
flight plan at its level, still the controller's to act on with coordination):

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
  holding   : a long delay (its landing slot behind the ones ahead), or too much height for the miles left, is taken
              in the aerodrome's published approach holding pattern (fix, inbound track, turn direction, 1 minute legs
              or the published leg length, no lower than its minimum holding altitude, its maximum speed): the hold
              that suits the runway in use and lies on the way; laps are cut short when its slot comes. Without
              published holds, a racetrack at the approach entry; small delays: a dogleg before the final approach fix
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
EXPEDITE_RATIO = 0.65             # miles to lose a given height expediting (idle, speedbrakes) / on its own profile
STRETCH_MAX_NM = 25.0             # the most a single dogleg vector adds; more delay than that: the hold


def _dest(world: "World", ac: "Aircraft"):
    code = (ac.destination or ac.flight_plan.get("destination") or "").upper()
    return world.aerodromes.get(code)


def lands_here(world: "World", ac: "Aircraft") -> bool:
    """An arrival to one of our aerodromes: it stays on the picture until it lands."""
    return _dest(world, ac) is not None and not getattr(ac, "landed", False)


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
    # the whole way from where it is (its own points, then the procedure) kept clear of the active areas
    full = [ac.pos] + [world.waypoints[w] for w in pts] + [tuple(p) for p in path["points"][1:]]
    full = _clear_of_areas(world, ac, ad, full)
    by_pos = {(round(world.waypoints[w][0], 2), round(world.waypoints[w][1], 2)): w for w in pts}
    names, route = [], []
    for k, p in enumerate(full[1:], 1):
        w = by_pos.get((round(p[0], 2), round(p[1], 2)))
        if w is not None:
            route.append(w)                            # one of its flight plan points
            continue
        n = f"@{ac.callsign}~A{k}"
        world.waypoints[n] = (float(p[0]), float(p[1]))
        names.append(n)
        route.append(n)
    ac.route = ac.route[:ac.next_index] + route
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
    h = _pick_hold(world, ac) if delay_nm > STRETCH_MAX_NM else None
    lap_nm = _published_hold(world, ac, h, 0, "") if h is not None else 0.0
    laps = int((delay_nm - _hold_detour(world, ac, h)) / lap_nm + 0.3) if h is not None else 0
    if laps >= 1:
        detour = _hold_detour(world, ac, h)
        _published_hold(world, ac, h, laps, "delay")
        delay_nm -= laps * lap_nm + detour
    elif h is None and delay_nm > 30.0 and not getattr(world.aerodromes[st["ad"]], "holdings", None):
        laps = int((delay_nm - 8.0) // HOLD_LAP_NM)          # no published hold: a racetrack at the approach entry
        _hold(world, ac, laps)
        delay_nm -= laps * HOLD_LAP_NM
    if delay_nm > 2.0:
        _stretch(world, ac, delay_nm)


def update(world: "World") -> None:
    arr = candidates(world)
    if not arr:
        return
    # the next units (approach, or the next sector for it) take an arrival over as soon as it is transferred: a late
    # transfer leaves it high and close, and it is up to them to lose the height (expedite, vectors, the hold)
    waiting = [a for a in arr if getattr(a, "app", None) is None]
    if waiting:
        ready = waiting
        for a in sorted(ready, key=lambda a: dist(a.pos, _dest(world, a).pos)):   # closest first: the order
            _start(world, a)
            _schedule(world, a)
    by_ad: Dict[str, list] = {}
    for ac in arr:
        if getattr(ac, "app", None) is None:
            continue
        if world.t - ac.app["last"] < PERIOD_S:
            continue
        ac.app["last"] = world.t
        by_ad.setdefault(ac.app["ad"], []).append(ac)
    for ad_id, lst in by_ad.items():
        ad = world.aerodromes[ad_id]
        order = [cs for cs in world._landing_seq.get(ad_id, []) if cs in world.aircraft
                 and not getattr(world.aircraft[cs], "landed", False) and not world.aircraft[cs].gone]
        # approach re-sequences when one has clearly got ahead of the one before it (out of a hold, a shorter path)
        etas = {cs: _eta(world, world.aircraft[cs], _to_go(world, world.aircraft[cs])) for cs in order
                if getattr(world.aircraft[cs], "app", None)}
        for k in range(1, len(order)):
            a_, b_ = order[k - 1], order[k]
            if a_ in etas and b_ in etas and not _holding(world.aircraft[b_]) \
                    and not world.aircraft[a_].app["established"] and etas[b_] < etas[a_] - 60.0:
                order[k - 1], order[k] = b_, a_
                seq = world._landing_seq[ad_id]
                i, j = seq.index(a_), seq.index(b_)
                seq[i], seq[j] = b_, a_
                world._event("approach_resequenced", callsign=b_, aerodrome=ad_id,
                             text=f"{b_} now before {a_} in the {ad_id} sequence")
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
    low = ac.alt_ft <= ad.elev_ft + 1000.0
    if low and (to_go <= LANDED_NM or (thr_d <= LANDED_NM + 0.6 and ac.next_index >= len(ac.route) - 1)):
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
    gp_here = ad.elev_ft + max(0.0, to_go - LANDED_NM) * GLIDE_FT_PER_NM
    unstable = to_go <= final_nm * 0.6 and ac.alt_ft > gp_here + 1500.0
    if not low and (to_go <= LANDED_NM or ac.nav_mode != "lnav" or unstable
                    or (thr_d <= 1.0 and ac.next_index >= len(ac.route) - 1)):
        _go_around(world, ac, ad)             # too high on final, or past the runway: another approach
        return
    if (st["established"] and ahead is not None and ahead[2].app and ahead[2].app.get("established")
            and not ahead[2].gone and dist(ac.pos, ahead[2].pos) < 3.0 and to_go > ahead[0]):
        world._event("go_around", callsign=ac.callsign, aerodrome=ad.id,
                     text=f"{ac.callsign} sent around: under 3 NM behind {ahead[2].callsign} on final")
        _go_around(world, ac, ad, quiet=True)
        return
    # vertical: down to the FAF altitude at its top of descent (the holding level first), then the glide path
    if to_go <= final_nm + 0.5 and ac.alt_ft <= gp_here + 1500.0:
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
        if _holding(ac):
            hmin = (st.get("hold") or {}).get("min_alt_ft") or (ad.elev_ft + HOLD_AGL_FT)
            floor = max(faf_alt, hmin)                               # holding: no lower than the holding level
            if ac.cleared_alt_ft < floor - 50 and ac.alt_ft > floor - 50:
                ac.cleared_alt_ft = floor
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
    elif _holding(ac):
        want = min(210.0, (st.get("hold") or {}).get("max_kt") or 210.0)
    elif to_go <= 25.0 or st.get("hold_names"):
        want = 210.0
    # its landing slot: never earlier than the one ahead (as it really flies) plus the approach spacing
    sta = st["sta"]
    if ahead is not None:
        sta = max(sta, ahead[1] + _sep_s(ahead[2]))
    if ahead is not None and not st["established"] and to_go < final_nm + 20:
        # turning in: the one ahead must be at least the approach spacing (plus a margin) ahead along the track,
        # whatever the time estimates said - else one more dogleg before the final approach fix
        need_nm = (HEAVY_SPACING_NM if str(ahead[2].flight_plan.get("wake", "")) in ("H", "J") else APP_SPACING_NM) + 2.5
        gap_nm = to_go - ahead[0]
        if _holding(ahead[2]):
            gap_nm = need_nm                          # the one ahead is holding: its slot (time) decides, not miles
        elif ahead[2].app and ahead[2].app.get("established") and gap_nm < need_nm + 8.0:
            # joining from another side: the distance in a straight line counts too (not when it is far behind
            # along the track, e.g. going round again past the one on final)
            gap_nm = min(gap_nm, dist(ac.pos, ahead[2].pos) + 1.5)
        if gap_nm < need_nm and world.t - st.get("stretch_t", -1e9) > 40 and not _holding(ac):
            done = _delay(world, ac, need_nm - gap_nm + 2.0, spacing=True)
            if not done and gap_nm < APP_SPACING_NM - 0.5 and to_go - final_nm < 8.0:
                # too close to the one ahead and nothing left to vector before the FAF: out of the sequence
                world._event("approach_breakoff", callsign=ac.callsign, aerodrome=ad.id,
                             text=f"{ac.callsign} taken out of the sequence (too close behind {ahead[2].callsign}), "
                                  f"vectored for another approach")
                _go_around(world, ac, ad, quiet=True)
                return
    if not st["established"] and not _holding(ac):
        # too high for the miles left (a late transfer, high and close): 1) expedite - idle with speedbrakes,
        # 2) vectors for more track miles (a dogleg, up to 25 NM), 3) laps in the published hold when even that is
        # not enough
        need_dn = descent_distance(ac, ac.alt_ft, faf_alt, lambda a: 0.0)      # on its own descent profile
        avail = to_go - final_nm - 1.0
        if need_dn > avail:
            ac.cleared_alt_ft = min(ac.cleared_alt_ft, faf_alt)
            st["cleared_down"] = True
            if not ac.speedbrake and not st.get("expedited"):
                st["expedited"] = True
                world._event("approach_expedite", callsign=ac.callsign,
                             text=f"{ac.callsign} expedite descent to {int(faf_alt)} ft (high for the approach)")
            ac.speedbrake = True
            excess = need_dn * EXPEDITE_RATIO - avail + 2.0
            if excess > 3.0 and world.t - st.get("stretch_t", -1e9) > 45 and not _dogleg_ahead(ac):
                h = _pick_hold(world, ac) if excess > STRETCH_MAX_NM and not st.get("hold") else None
                if h is not None:
                    lap_nm = _published_hold(world, ac, h, 0, "")
                    _published_hold(world, ac, h, max(1, math.ceil((excess - _hold_detour(world, ac, h)) / lap_nm)),
                                    "height")
                    st["stretch_t"] = world.t
                else:
                    _stretch(world, ac, min(excess + 4.0, STRETCH_MAX_NM))   # one vector, with a margin
        elif ac.speedbrake and need_dn * EXPEDITE_RATIO < avail - 6.0 and need_dn < avail + 2.0:
            ac.speedbrake = False                       # back on its profile
    if (_holding(ac) and st.get("hold") and st["hold"].get("why") == "delay"
            and eta - sta > 0.5 * st["hold"].get("lap_nm", 16.0) / max(ac.gs, 180.0) * 3600.0):
        _leave_hold(world, ac)                          # its slot has come: out of the hold at the end of this lap
    early = sta - eta                                   # seconds too early for its slot
    if (early > 40 and not st["established"] and to_go > final_nm + 3 and world.t - st.get("stretch_t", -1e9) > 60
            and not _holding(ac)):
        _delay(world, ac, 0.8 * early * max(ac.gs, 180.0) / 3600.0)
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


def _area_hit(world: "World", ad, a, b, alt0: float, rem_b: float, skip=()):
    """The first active area (prohibited, restricted, danger) the leg a->b comes closer to than its lateral spacing
    (3 / 5 NM) at the levels it is flown (the
    altitude estimated from the miles left to the threshold: the FAF altitude, the glide path, no higher than now),
    as (area, middle of the part inside), or None."""
    L = dist(a, b)
    n = max(2, int(L / 0.5))
    areas = [x for x in getattr(world, "areas", {}).values() if getattr(x, "active", False) and x.id not in skip]
    for ar in areas:
        inside = []
        for k in range(n + 1):
            f = k / n
            p = (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            rem = rem_b + L * (1.0 - f)
            alt = min(alt0, max(ad.elev_ft + rem * GLIDE_FT_PER_NM, ad.faf_alt_ft if rem > ad.final_nm else 0.0))
            if ar.infringed(p, min(max(alt, ar.lower_fl * 100), ar.upper_fl * 100) if
                            ar.lower_fl * 100 - 500 <= alt <= ar.upper_fl * 100 + 500 else -1e9, margin_nm=1.5):
                inside.append(p)
        if inside:
            m = inside[len(inside) // 2]
            return ar, m
    return None


def _clear_of_areas(world: "World", ac: "Aircraft", ad, pts) -> list:
    """The approach path with a point added around each active area a leg would come closer to than its lateral
    spacing (3 NM prohibited, 5 NM danger / restricted) at the levels flown there (the final approach itself is not
    moved)."""
    pts = [tuple(p) for p in pts]
    # an area it is already next to (its own route, as transferred): not held against the leg it is flying
    near = {x.id for x in getattr(world, "areas", {}).values()
            if getattr(x, "active", False) and x.infringed(pts[0], ac.alt_ft, margin_nm=1.5)}
    unfixable = set()
    for _ in range(8):
        hit_at = None
        rem = 0.0
        rems = [0.0] * len(pts)
        for i in range(len(pts) - 2, -1, -1):
            rem += dist(pts[i], pts[i + 1])
            rems[i] = rem
        for i in range(0, max(0, len(pts) - 3)):          # not the legs from the FAF on
            if (pts[i], pts[i + 1]) in unfixable:
                continue
            h = _area_hit(world, ad, pts[i], pts[i + 1], ac.alt_ft, rems[i + 1], near if i == 0 else ())
            if h is not None:
                hit_at = (i, h)
                break
        if hit_at is None:
            break
        i, (ar, m) = hit_at
        a, b = pts[i], pts[i + 1]
        L = max(dist(a, b), 1e-6)
        nx, ny = (b[1] - a[1]) / L, -(b[0] - a[0]) / L
        best = None
        for off in range(1, 26):
            for sd in (1.0, -1.0):
                c = (m[0] + nx * off * sd, m[1] + ny * off * sd)
                if (_area_hit(world, ad, a, c, ac.alt_ft, rems[i + 1] + dist(c, b), near if i == 0 else ()) is None
                        and _area_hit(world, ad, c, b, ac.alt_ft, rems[i + 1]) is None):
                    best = c
                    break
            if best is not None:
                break
        if best is None:
            unfixable.add((a, b))                          # nothing within 25 NM: leave that leg, look at the others
            continue
        best = (best[0] + nx * (1.0 if dist(best, (m[0] + nx, m[1] + ny)) < dist(best, (m[0] - nx, m[1] - ny)) else -1.0),
                best[1] + ny * (1.0 if dist(best, (m[0] + nx, m[1] + ny)) < dist(best, (m[0] - nx, m[1] - ny)) else -1.0))
        pts.insert(i + 1, best)                            # a mile more of margin
    return [[round(p[0], 2), round(p[1], 2)] for p in pts]


def _delay(world: "World", ac: "Aircraft", extra_nm: float, spacing: bool = False) -> bool:
    """More track miles for spacing: a dogleg when it is a few miles, laps in the published hold (still on the way)
    when it is more - not one dogleg after another."""
    st = ac.app
    if extra_nm > STRETCH_MAX_NM and st.get("holds_n", 0) < 3:
        h = _pick_hold(world, ac)
        if h is not None:
            lap_nm = _published_hold(world, ac, h, 0, "")
            laps = int((extra_nm - _hold_detour(world, ac, h)) / lap_nm + 0.3)
            if laps >= 1:
                _published_hold(world, ac, h, laps, "delay")
                st["holds_n"] = st.get("holds_n", 0) + 1
                st["stretch_t"] = world.t
                return True
    if _dogleg_ahead(ac) and not spacing:
        return True                                   # the vector it was given is still being flown
    if not spacing and (extra_nm < 3.0 or st.get("stretches", 0) >= 4):
        st["stretch_t"] = world.t                     # a few miles: the speed does it, no dogleg after dogleg
        return True
    return _stretch(world, ac, extra_nm)


def _hold_detour(world: "World", ac: "Aircraft", h: dict) -> float:
    """Extra track miles of going to the hold fix and starting the approach from there (no lap)."""
    ad = world.aerodromes[ac.app["ad"]]
    F = (h["x"], h["y"])
    path = approach_path(F, ad, ad.in_use)
    return dist(ac.pos, F) + polyline_length([F] + [tuple(p) for p in path["points"][1:]]) - _to_go(world, ac)


def _dogleg_ahead(ac: "Aircraft") -> bool:
    """A vector for track miles it has been given and not yet flown."""
    return any("~D" in n for n in ac.route[ac.next_index:])


def _holding(ac: "Aircraft") -> bool:
    """In (or on its way into) its holding pattern: lap points still ahead."""
    st = ac.app or {}
    names = st.get("hold_names") or []
    return bool(names) and any(n in ac.route[ac.next_index:] for n in names[1:])


def _pick_hold(world: "World", ac: "Aircraft"):
    """The published hold to use: for the runway in use (else any of the aerodrome's), on the way (no further from
    the aerodrome than the aircraft), the least detour to the final approach fix."""
    ad = world.aerodromes[ac.app["ad"]]
    hs = list(getattr(ad, "holdings", []) or [])
    if not hs:
        return None
    hs = [h for h in hs if not h["runways"] or ad.in_use in h["runways"]] or hs
    d_now = dist(ac.pos, ad.pos)
    hs = [h for h in hs if dist((h["x"], h["y"]), ad.pos) <= d_now + 5.0]
    if not hs:
        return None
    # the arrival holds (STAR IAFs) before the procedural ones close in on the final (e.g. GMAD ALS NDB)
    hs = [h for h in hs if dist((h["x"], h["y"]), ad.pos) > ad.final_nm + 2.0] or hs
    faf = ac.app["faf"]
    return min(hs, key=lambda h: dist(ac.pos, (h["x"], h["y"])) + dist((h["x"], h["y"]), faf))


def _lap(h: dict, tas: float, alt_ft: float):
    """One lap of a holding pattern from its fix: the turn to the holding side, the outbound leg (1 minute, 1.5 above
    14000 ft, or the published distance), the turn back, the inbound leg to the fix. Returns (points, length)."""
    th = math.radians(h["inbound_true"])
    u = (math.sin(th), math.cos(th))                              # inbound direction
    n = (u[1], -u[0]) if h["turn"] == "R" else (-u[1], u[0])      # the holding side
    leg = float(h["leg_nm"]) if h.get("leg_nm") else \
        float(h.get("leg_min") or (1.0 if alt_ft <= 14000 else 1.5)) * max(tas, 180.0) / 60.0
    w = max(3.0, 2.0 * max(tas, 180.0) * 120.0 / 3600.0 / (2.0 * math.pi))   # rate one turn diameter
    F = (h["x"], h["y"])

    def P(a, b):
        return (F[0] + n[0] * a + u[0] * b, F[1] + n[1] * a + u[1] * b)
    pts = [P(w / 2, w / 2), P(w, 0.0), P(w, -leg), P(w / 2, -leg - w / 2), P(0.0, -leg), F]
    return pts, 2.0 * leg + math.pi * w


def _published_hold(world: "World", ac: "Aircraft", h: dict, laps: int, why: str) -> float:
    """Cleared to the published hold h (direct to its fix), `laps` laps, then the approach from the fix. Returns the
    length of one lap (NM); laps=0 only measures it."""
    st = ac.app
    ad = world.aerodromes[st["ad"]]
    tas = min(ac.tas if ac.tas > 100 else 230.0, 260.0)
    pts, lap_nm = _lap(h, tas, min(ac.alt_ft, 14000.0))
    if laps <= 0:
        return lap_nm
    for n in st["names"]:
        world.waypoints.pop(n, None)
    F = (h["x"], h["y"])
    hf = f"@{ac.callsign}~HF"
    world.waypoints[hf] = F
    names = []
    for k in range(laps):
        for j, p in enumerate(pts):
            n = f"@{ac.callsign}~H{k}.{j}"
            world.waypoints[n] = (float(p[0]), float(p[1]))
            names.append(n)
    path = approach_path(F, ad, ad.in_use)
    path["points"] = _clear_of_areas(world, ac, ad, path["points"])
    app_names = []
    for k, p in enumerate(path["points"][1:], 1):
        n = f"@{ac.callsign}~A{k}"
        world.waypoints[n] = (float(p[0]), float(p[1]))
        app_names.append(n)
    ac.route = ac.route[:ac.next_index] + [hf] + names + app_names
    ac.leg_from = ac.pos
    ac.nav_mode, ac.target_hdg, ac.turn_dir = "lnav", None, None
    st.update(names=[hf] + names + app_names, hold_names=[hf] + names, type=path["type"], faf=tuple(path["faf"]),
              thr=tuple(path["threshold"]), hold={"fix": h["fix"], "min_alt_ft": h["min_alt_ft"], "max_kt": h["max_kt"],
                                                  "why": why, "laps": laps, "lap_nm": lap_nm})
    side = "right" if h["turn"] == "R" else "left"
    world._event("approach_holding", callsign=ac.callsign, fix=h["fix"], laps=laps,
                 text=(f"{ac.callsign} to hold at {h['fix']}, inbound {int(round(h['inbound_true'])):03d} (true), {side} "
                       f"turns, not below {int(h['min_alt_ft'])} ft, {laps} lap{'s' if laps > 1 else ''} "
                       f"({'delay' if why == 'delay' else 'to lose height'})"))
    return lap_nm


def _leave_hold(world: "World", ac: "Aircraft") -> None:
    """Out of the hold at the end of the present lap (its expected approach time has come)."""
    st = ac.app
    hn = st.get("hold_names") or []
    ahead = ac.route[ac.next_index:]
    laps_ahead = [n for n in ahead if n in hn[1:]]
    if not laps_ahead:
        return
    if hn and hn[0] in ahead:
        keep = []                                     # not there yet: straight on from the fix
    else:
        end_of_lap = next((n for n in laps_ahead if n.endswith(".5")), None)
        keep = laps_ahead[:laps_ahead.index(end_of_lap) + 1] if end_of_lap else laps_ahead[:1]
    drop = [n for n in laps_ahead if n not in keep]
    if not drop:
        return
    ac.route = ac.route[:ac.next_index] + [n for n in ahead if n not in drop]
    for n in drop:
        world.waypoints.pop(n, None)
    st["names"] = [n for n in st["names"] if n not in drop]
    st["hold_names"] = [n for n in hn if n not in drop]
    world._event("approach_leave_hold", callsign=ac.callsign,
                 text=f"{ac.callsign} leaving the hold at {(st.get('hold') or {}).get('fix', '')} for the approach")


def _stretch(world: "World", ac: "Aircraft", extra_nm: float) -> bool:
    """A dogleg on the next leg (before the final approach fix) adding extra_nm to the path; False when there is no
    room for one (on final, or too close to the next point)."""
    st = ac.app
    rest = ac.route[ac.next_index:]
    nxt = next((w for w in rest if w in world.waypoints), None)
    if nxt is None or nxt == st["names"][-1]:
        return False                             # already on final
    a, b = ac.pos, world.waypoints[nxt]
    L = dist(a, b)
    extra = min(extra_nm, 25.0)
    if L < 4.0:
        return False
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    ad = world.aerodromes[st["ad"]]
    rem_b = _to_go(world, ac) - L
    first = 1.0 if (st.get("stretches", 0) % 2 == 0) else -1.0
    mid = None
    for ex in (extra, extra * 0.7, extra * 0.5):          # the side and size that keep clear of the active areas
        h = math.sqrt(max(((L + ex) / 2) ** 2 - (L / 2) ** 2, 0.0))
        for side in (first, -first):
            m = (a[0] + ux * L / 2 + side * uy * h, a[1] + uy * L / 2 - side * ux * h)
            if _area_hit(world, ad, a, m, ac.alt_ft, rem_b + dist(m, b)) is None and \
                    _area_hit(world, ad, m, b, ac.alt_ft, rem_b) is None:
                mid, extra = m, ex
                break
        if mid is not None:
            break
    if mid is None:
        return False
    st["stretches"] = st.get("stretches", 0) + 1
    name = f"@{ac.callsign}~D{st['stretches']}"
    world.waypoints[name] = mid
    st["names"].insert(0, name)
    ac.route = ac.route[:ac.next_index] + [name] + rest
    ac.leg_from = ac.pos
    st["stretch_t"] = world.t
    world._event("approach_vector", callsign=ac.callsign,
                 text=f"{ac.callsign} vectored by approach for spacing (+{extra:.0f} NM)")
    return True


def runway_changed(world: "World", ad) -> None:
    """A change of runway: the arrivals approach is working and that are not yet established on the old runway's
    final are re-planned for the new one (its procedure, joining route, holds); the established ones land on the
    old runway."""
    for ac in list(world.aircraft.values()):
        st = getattr(ac, "app", None)
        if not st or st.get("ad") != ad.id or st.get("established") or getattr(ac, "landed", False) or ac.gone:
            continue
        for n in st["names"]:
            world.waypoints.pop(n, None)
        ac.route = ac.route[:ac.next_index]
        ac.next_index = len(ac.route)
        ac.app = None
        seq = world._landing_seq.get(ad.id, [])
        pos = seq.index(ac.callsign) if ac.callsign in seq else None
        _start(world, ac)
        if pos is not None:                            # it keeps its place in the sequence
            while ac.callsign in seq:
                seq.remove(ac.callsign)
            seq.insert(pos, ac.callsign)
        if ac.alt_ft < ad.faf_alt_ft - 100:
            ac.cleared_alt_ft = ad.faf_alt_ft          # below the new runway's procedure: up to its FAF altitude
        _schedule(world, ac)
        world._event("approach_runway_change", callsign=ac.callsign, aerodrome=ad.id, runway=ad.in_use,
                     text=f"{ac.callsign} re-planned for runway {ad.in_use}")


def _go_around(world: "World", ac: "Aircraft", ad, quiet: bool = False) -> None:
    """Too high over the runway: missed approach, back to the end of the sequence for another approach."""
    st = ac.app
    for n in st["names"]:
        world.waypoints.pop(n, None)
    seq = world._landing_seq.get(ad.id, [])
    if ac.callsign in seq:
        seq.remove(ac.callsign)
    ac.route = ac.route[:ac.next_index]
    ac.next_index = len(ac.route)
    ac.cleared_alt_ft = max(ac.alt_ft, ad.faf_alt_ft + 1000.0)
    ac.speedbrake, ac.vs_cmd_fpm = False, None
    ac.app = None
    if not quiet:
        world._event("go_around", callsign=ac.callsign, aerodrome=ad.id,
                     text=f"{ac.callsign} going around {ad.id} (too high on the approach)")
    _start(world, ac)
    _schedule(world, ac)
