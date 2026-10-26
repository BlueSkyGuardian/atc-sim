"""Backward sequencing: decide the end state first, then how each aircraft gets there.

The end state is the stream in trail at the point T, each aircraft S behind the one ahead when it crosses T. Fixing it
turns one joint problem into one small problem per aircraft:

  1. SLOTS - the order and each aircraft's time over T: t_k = max(eta_k, t_(k-1) + S / g_(k-1)), g the ground speed
     of the one ahead after T. The order may differ from first come, first served by at most one place (constrained
     position shifting), and is chosen by what it costs the aircraft to make their slots (below), not by delay alone.
  2. DELAY TO ABSORB - each aircraft's d_k = t_k - eta_k: the one number its clearances must produce.
  3. THE DELAY TABLE - what each family of clearances can absorb, for this aircraft, here (its route, speed, type and
     the rules), and what it costs:

        family         instructions   absorbs
        speed          1              0 .. its Mach -0.03 (not below the type's margin) held to T
        dogleg         2              a heading off the axis then direct T: one ellipse (ellipse.py)
        dogleg+speed   3              both
        two legs       3              a heading, then a heading to the other side, then direct T: the second turn
                                      point on the ellipse of foci (first turn point, T) - concatenated ellipses
        two legs+speed 4              both
        n legs (3-6)   n+1            a zigzag: out to one side of a corridor along the axis, then across to the other
                                      side and back, n headings, then direct T - a long delay in short legs, not one
                                      heading held for a long way
        hold           advisory       laps of a hold at T (coordinate: not a clearance the tool gives)

     A family is feasible when d lies in its range. The rules bound every vector: turns of 70 degrees at most, the
     direction of flight kept, never more than 25 NM off the axis of the route, 15 minutes on a heading at most, back on
     the direct to T at least S before it.
  4. CHOICE - the cheapest feasible family: fewer instructions first, then the least distance off the axis, then the
     most slack (d well inside the range: the speed left as a trim either way, robust to the wind and to a late
     joiner). The exact clearances (headings, times) are then refined in the trajectory model (sequence.py).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

MAX_SHIFT = 1                    # an aircraft moves at most one place from first come, first served
MAX_TWO_LEG_S = 1200.0           # a two-leg vector: at most 20 minutes on the headings in all
HOLD_LAP_S = 300.0               # a lap of an en-route hold (1.5 minute legs at cruise speed)
ZIGZAG_LEGS = (3, 4, 5, 6)       # zigzags of 3 to 6 legs, left and right across the axis
ZIGZAG_MAX_S = 1800.0            # at most 30 minutes on the headings of a zigzag
FAMILIES = ("speed", "dogleg", "dogleg+speed", "two legs", "two legs+speed") + tuple(f"{n} legs" for n in ZIGZAG_LEGS) + ("hold",)
INSTR = {"none": 0, "speed": 1, "dogleg": 2, "dogleg+speed": 3, "two legs": 3, "two legs+speed": 4,
         **{f"{n} legs": n + 1 for n in ZIGZAG_LEGS}, "hold": 9}


def _rules(w, a):
    from .sequence import MAX_TURN_DEG, MAX_OFF_AXIS_NM, MAX_VECTOR_S
    from .resolve import route_bearings, MAX_OFF_ROUTE_DEG, MAX_OFF_EXIT_DEG
    brg_r, brg_x = route_bearings(w, a)
    return MAX_TURN_DEG, MAX_OFF_AXIS_NM, MAX_VECTOR_S, brg_r, brg_x, MAX_OFF_ROUTE_DEG, MAX_OFF_EXIT_DEG


def _ang(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _unit(deg: float) -> Tuple[float, float]:
    r = math.radians(deg)
    return math.sin(r), math.cos(r)


def speed_range_s(w, a, D_nm: float) -> Tuple[float, Optional[float]]:
    """The most delay a speed reduction absorbs over the D_nm to T: own Mach -0.03 (not below the type's margin), or
    -20 kt on an IAS. (seconds, the speed) - 0 for a departure still climbing."""
    from .resolve import departing
    from .sequence import MAX_MACH_CHANGE, _mach_floor, MIN_IAS
    if departing(w, a) or D_nm <= 1.0:
        return 0.0, None
    g = max(a.gs, 150.0)
    mode, val = a.normal_speed()
    if mode == "mach":
        m = round(max(val - MAX_MACH_CHANGE, _mach_floor(a, val)), 2)
        if m >= val - 1e-6:
            return 0.0, None
        dg = a.perf.tas_for("mach", m, a.alt_ft) - a.tas
    else:
        ias = max(MIN_IAS, val - 20.0)
        if ias >= val - 1e-6:
            return 0.0, None
        m = ias
        dg = a.perf.tas_for("ias", ias, a.alt_ft) - a.tas
    g2 = max(120.0, g + dg)
    return max(0.0, D_nm * (1.0 / g2 - 1.0 / g) * 3600.0), m


def _vector_samples(w, a, T, S_nm: float, L_route: float, two: bool):
    """Feasible vectors under the rules: (stretch NM, max off-axis NM, turn deg) for one leg, or two legs."""
    from .sequence import off_axis_nm
    turn_max, off_max, vec_max, brg_r, brg_x, off_route, off_exit = _rules(w, a)
    g = max(a.gs, 150.0)
    P = a.pos
    brg_T = math.degrees(math.atan2(T[0] - P[0], T[1] - P[1])) % 360.0
    ok_dir = lambda trk: _ang(trk, brg_r) <= off_route and _ang(trk, brg_x) <= off_exit
    out = []
    r_max = vec_max * g / 3600.0
    legs1 = [float(r) for r in range(5, int(r_max) + 1, 5)] if not two else [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 55.0, 70.0, 90.0]
    for side in (-1, 1):
        for th in range(5 if not two else 10, int(turn_max) + 1, 5):
            h1 = (brg_T + side * th) % 360.0
            if not ok_dir(h1):
                continue
            u1 = _unit(h1)
            for r1 in legs1:
                if r1 / g * 3600.0 > vec_max:
                    break
                M1 = (P[0] + u1[0] * r1, P[1] + u1[1] * r1)
                o1 = off_axis_nm(w, a, M1)
                if o1 > off_max:
                    break
                d1 = math.hypot(T[0] - M1[0], T[1] - M1[1])
                if not two:
                    back = math.degrees(math.atan2(T[0] - M1[0], T[1] - M1[1])) % 360.0
                    if d1 >= S_nm and _ang(back, h1) <= 90.0:
                        out.append((r1 + d1 - L_route, o1, th))
                    continue
                # second leg: to the other side of the bearing from M1 to T (the S-turn)
                b1 = math.degrees(math.atan2(T[0] - M1[0], T[1] - M1[1])) % 360.0
                for th2 in range(20, int(turn_max) + 1, 10):
                    h2 = (b1 - side * th2) % 360.0
                    if not ok_dir(h2) or _ang(h2, h1) < 30.0:
                        continue
                    u2 = _unit(h2)
                    for r2 in (10, 20, 30, 45, 60, 80):
                        if (r1 + r2) / g * 3600.0 > MAX_TWO_LEG_S:
                            break
                        M2 = (M1[0] + u2[0] * r2, M1[1] + u2[1] * r2)
                        o2 = off_axis_nm(w, a, M2)
                        if o2 > off_max:
                            break
                        d2 = math.hypot(T[0] - M2[0], T[1] - M2[1])
                        back = math.degrees(math.atan2(T[0] - M2[0], T[1] - M2[1])) % 360.0
                        if d2 >= S_nm and _ang(back, h2) <= 90.0:
                            out.append((r1 + r2 + d2 - L_route, max(o1, o2), max(th, th2)))
    return out


def zigzag(P, T, n: int, theta: float, h: float, side: int) -> Optional[Dict]:
    """A zigzag of n legs in the frame of the axis P -> T: out to offset side*h at theta to the axis, then n-1 legs
    across the corridor (-side*h, side*h, ...), then direct T. {tracks: [(track, NM)], points, stretch_nm, along_nm
    (how far along the axis the last turn point is), D} - None when the zigzag would reach T."""
    D = math.hypot(T[0] - P[0], T[1] - P[1])
    if D < 1e-6 or not (0 < theta < 90) or h <= 0:
        return None
    ax = math.degrees(math.atan2(T[0] - P[0], T[1] - P[1])) % 360.0
    ux, uy = _unit(ax)                                  # along the axis
    nx, ny = uy, -ux                                    # to its right
    t = math.radians(theta)
    along, off, path = 0.0, 0.0, 0.0
    tracks, pts = [], []
    for k in range(n):
        tgt = side * h * (1 if k % 2 == 0 else -1)
        d_off = tgt - off
        L = abs(d_off) / math.sin(t)
        along += abs(d_off) / math.tan(t)
        off = tgt
        path += L
        trk = (ax + math.degrees(math.atan2(1.0 if d_off > 0 else -1.0, 1.0 / math.tan(t)))) % 360.0
        tracks.append((trk, L))
        pts.append((P[0] + ux * along + nx * off, P[1] + uy * along + ny * off))
    rest = math.hypot(D - along, off)
    if along >= D:
        return None
    return {"tracks": tracks, "points": pts, "stretch_nm": path + rest - D, "along_nm": along, "D": D,
            "back_deg": math.degrees(math.atan2(abs(off), D - along))}


def zigzag_range(w, a, T, S_nm: float, n: int) -> Optional[Tuple[float, float]]:
    """The delay (s) a zigzag of n legs can absorb within the rules (corridor of 25 NM each side at most, turns of
    70 degrees, direction of flight, back on the direct S before T, 30 minutes): (least, most) - None if none fits."""
    turn_max, off_max, _vec, brg_r, brg_x, off_route, off_exit = _rules(w, a)
    g = max(a.gs, 150.0)
    P = a.pos
    ax = math.degrees(math.atan2(T[0] - P[0], T[1] - P[1])) % 360.0
    lo, hi = None, None
    for theta in (30, 40, 50, 60, 70):
        if theta > turn_max or _ang(ax + theta, brg_r) > off_route or _ang(ax - theta, brg_r) > off_route \
                or _ang(ax + theta, brg_x) > off_exit or _ang(ax - theta, brg_x) > off_exit:
            continue
        for h in (4.0, 8.0, 12.0, 16.0, 20.0, off_max):
            z = zigzag(P, T, n, theta, h, 1)
            if z is None or z["D"] - z["along_nm"] < S_nm or z["back_deg"] > 90:
                continue
            if sum(L for _, L in z["tracks"]) / g * 3600.0 > ZIGZAG_MAX_S:
                continue
            d = z["stretch_nm"] / g * 3600.0
            lo = d if lo is None else min(lo, d)
            hi = d if hi is None else max(hi, d)
    return None if lo is None else (lo, hi)


_CACHE: Dict[tuple, Dict] = {}


def delay_table(w, a, T_name: str, S_nm: float) -> Dict[str, Dict]:
    """What each family of clearances can absorb for aircraft a before T_name: {family: {lo_s, hi_s, instructions,
    off_axis_nm(d) estimate}} - the families it cannot use are left out. Kept for the same moment and position."""
    key = (id(w), round(w.t), a.callsign, T_name, round(S_nm, 1), round(a.x, 2), round(a.y, 2))
    if key in _CACHE:
        return _CACHE[key]
    if len(_CACHE) > 400:
        _CACHE.clear()
    _CACHE[key] = out = _delay_table(w, a, T_name, S_nm)
    return out


def _delay_table(w, a, T_name: str, S_nm: float) -> Dict[str, Dict]:
    from . import traj
    T = w.waypoints[T_name]
    pts = [a.pos] + [w.waypoints[x] for x in traj.route_ahead(w, a)]
    L_route = traj._along_to(pts, T) or math.hypot(T[0] - a.x, T[1] - a.y)
    g = max(a.gs, 150.0)
    out: Dict[str, Dict] = {}
    spd_s, spd = speed_range_s(w, a, L_route)
    if spd_s > 5.0:
        out["speed"] = {"lo_s": 0.0, "hi_s": spd_s, "instructions": 1, "speed": spd, "axis": []}
    for fam, two in (("dogleg", False), ("two legs", True)):
        smp = [s_ for s_ in _vector_samples(w, a, T, S_nm, L_route, two) if s_[0] > 0.3]
        if not smp:
            continue
        ds = [s_[0] / g * 3600.0 for s_ in smp]
        # the least off-axis distance that gives each amount of delay (for the choice): sorted by delay
        axis = sorted(((d, o) for d, (_, o, _) in zip(ds, smp)), key=lambda x: x[0])
        out[fam] = {"lo_s": min(ds), "hi_s": max(ds), "instructions": INSTR[fam], "axis": axis}
        if spd_s > 5.0:
            out[fam + "+speed"] = {"lo_s": min(ds), "hi_s": max(ds) + spd_s, "instructions": INSTR[fam + "+speed"],
                                   "axis": axis, "speed": spd}
    for n in ZIGZAG_LEGS:
        rg = zigzag_range(w, a, T, S_nm, n)
        if rg is not None and rg[1] > 5.0:
            out[f"{n} legs"] = {"lo_s": rg[0], "hi_s": rg[1], "instructions": INSTR[f"{n} legs"], "axis": [(rg[1], 25.0)]}
    out["hold"] = {"lo_s": HOLD_LAP_S * 0.6, "hi_s": HOLD_LAP_S * 4, "instructions": INSTR["hold"], "axis": [],
                   "advisory": True}
    return out


def _axis_for(row: Dict, d: float, spd_s: float = 0.0) -> float:
    """The least distance off the axis a vector of this family needs for d seconds (the speed doing its share)."""
    need = max(0.0, d - spd_s)
    cands = [o for dd, o in row.get("axis", []) if dd >= need]
    return min(cands) if cands else (row["axis"][-1][1] if row.get("axis") else 0.0)


def choose(table: Dict[str, Dict], d: float, tol_s: float = 8.0) -> List[Dict]:
    """The families that absorb d seconds, best first: fewer instructions, less off the axis, more slack."""
    if d <= tol_s:
        return [{"family": "none", "instructions": 0, "off_axis_nm": 0.0, "slack": 1.0}]
    spd_s = table.get("speed", {}).get("hi_s", 0.0)
    out = []
    for fam, row in table.items():
        lo, hi = row["lo_s"], row["hi_s"]
        if not (lo - tol_s <= d <= hi):
            continue
        span = max(hi - lo, 1.0)
        slack = min(d - lo, hi - d) / span
        off = 0.0 if fam in ("speed", "hold") else _axis_for(row, d, spd_s if fam.endswith("+speed") else 0.0)
        out.append({"family": fam, "instructions": row["instructions"], "off_axis_nm": round(off, 1),
                    "slack": round(slack, 2), "lo_s": round(lo), "hi_s": round(hi), "advisory": row.get("advisory", False)})
    out.sort(key=lambda c: (c["instructions"], round(c["off_axis_nm"] / 5.0), -c["slack"]))
    return out


def cost(table: Dict[str, Dict], d: float) -> Tuple[int, int, float]:
    """(infeasible?, instructions, off-axis) of the best way to absorb d - for comparing orders."""
    c = choose(table, d)
    if not c:
        return (1, 9, 99.0)
    return (0, c[0]["instructions"], c[0]["off_axis_nm"])


def orders(seq: List[str], max_shift: int = MAX_SHIFT):
    """Every order within one place of seq (constrained position shifting): seq with any set of non-overlapping
    adjacent swaps - Fibonacci(n+1) orders (89 for 10 aircraft, 1597 for 16)."""
    n = len(seq)

    def rec(i):
        if i >= n:
            yield []
            return
        for rest in rec(i + 1):
            yield [seq[i]] + rest
        if max_shift >= 1 and i + 1 < n:
            for rest in rec(i + 2):
                yield [seq[i + 1], seq[i]] + rest
    if n > 18:
        yield list(seq)
        return
    yield from rec(0)


def slots(order: List[str], eta: Dict[str, float], gap_s: Dict[str, float]) -> Dict[str, float]:
    """Each aircraft's time over T: no earlier than its estimate, and the gap behind the one ahead."""
    t, out = None, {}
    for cs in order:
        t = eta[cs] if t is None else max(eta[cs], t + gap_s[prev])
        out[cs] = t
        prev = cs
    return out


def schedule(order0: List[str], eta: Dict[str, float], gap_s: Dict[str, float], tables: Dict[str, Dict]) -> Dict:
    """The best order within MAX_SHIFT of order0 by what the delays cost (infeasible ones, instructions, off-axis
    distance, then total delay), with its slots and delays; order0 wins ties."""
    best = None
    for od in orders(order0):
        sl = slots(od, eta, gap_s)
        dl = {cs: sl[cs] - eta[cs] for cs in od}
        cs_ = [cost(tables[cs], dl[cs]) for cs in od]
        key = (sum(c[0] for c in cs_), sum(c[1] for c in cs_), round(sum(c[2] for c in cs_)), round(sum(dl.values())),
               od != order0)
        if best is None or key < best[0]:
            best = (key, od, sl, dl)
    key, od, sl, dl = best
    return {"order": od, "slots": sl, "delays": dl, "cost": {"infeasible": key[0], "instructions": key[1],
                                                             "off_axis_nm": key[2], "total_delay_s": key[3]},
            "shifted": od != order0}
