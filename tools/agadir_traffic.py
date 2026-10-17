"""Dense, realistic background traffic for the Agadir CTA scenarios, plus a de-confliction pass.

Each flow is a published route (airway, RAD routing or standard route) with an hourly rate, a level band, type
mix, operators and city pairs. Flights are spread in time with jitter (never two on the same route and level
closer than ~12 NM in trail), get a level of the right parity for their direction and within their performance,
and enter from 30 minutes before the start, so the sectors are already busy at T+0.

`deconflict()` then flies the scenario passively (weather included) and moves background flights that lose
separation to another level of the same parity, so that the conflicts left are the ones the scenario designer
planted. `sector_loads()` counts flights per sector per hour from a passive run (to compare with capacities).
"""
from __future__ import annotations

import json
import math
import random
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from atcsim import airspace as asp_mod, performance  # noqa: E402
from atcsim.aircraft import level_parity_ok  # noqa: E402
from atcsim.evaluate import run  # noqa: E402
from atcsim.geometry import bearing, dist, point_in_polygon  # noqa: E402
from atcsim.scenario import ScenarioError, build_world  # noqa: E402

UN857 = ["KORNO", "ABODA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"]
STD_RUNAX = ["RUNAX", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"]
UN858 = ["VASTO", "SULAM", "DIMSA", "IBLIV", "SUNID", "AKUDA"]          # northbound only
DESUM_AKUDA = ["DESUM", "SULAM", "DIMSA", "IBLIV", "SUNID", "AKUDA"]   # northbound only
UN873 = ["BAROK", "BENTU", "ABTIR", "MITLA", "SAMAR"]
UN866 = ["BEXAL", "KUBIL", "OSDIV", "KONBA"]

EU_SHORT = ["B738", "A320", "A321", "B38M", "A20N", "A21N", "A319", "B739"]
LONG = ["A332", "A333", "B77W", "B789", "B788", "A359", "B772", "A343"]
CANARIES = ["GCLP", "GCTS", "GCXO", "GCFV", "GCLA"]
EUROPE = ["LEMD", "LPPT", "LFPG", "EHAM", "EDDF", "EGLL", "EGKK", "EGCC", "EIDW", "EBBR", "LSZH", "EDDM", "ENGM",
          "ESSA", "EKCH", "LIMC", "LEBL", "LPPR"]
# destinations reached from AKUDA / TIGGI / BEXAL via the Mediterranean side (OBOLO-PESAS/MINTA, BALNO)
MED = ["LEMG", "LEAL", "LEVC", "LEPA", "LEZL", "LIRF", "LIMC", "LGAV", "LTFM", "LIPZ", "LEIB", "LMML"]
SAMERICA = ["SBGR", "SBGL", "SBKP", "SBRF", "SBFZ", "SKBO", "SVMI", "SAEZ", "SCEL"]
WAFRICA = ["GOBD", "GVAC", "DIAP", "DNMM", "GUCY", "GLRB"]
EU_CANARY_OPS = ["RYR", "EZY", "EXS", "TOM", "IBE", "VLG", "IBS", "TVF", "EWG", "CFG", "NAX", "BAW", "TAP", "AEA",
                 "SAS", "KLM", "DLH", "NOZ", "BTI", "SWR"]
LONG_OPS = ["AZU", "TAM", "GLO", "AVA", "AEA", "IBE", "TAP", "KLM", "AFR", "BAW", "DLH", "LAN", "ARG"]
AFRICA_OPS = ["BEL", "AFR", "TAP", "IBE", "SZN", "ASL", "KQA", "TVF"]

# flows: route, flights per hour, levels (parity is applied automatically), types, operators, origins, destinations
FLOWS = {
    "un857": dict(route=UN857, rate=8, levels=range(330, 400, 10), types=EU_SHORT, ops=EU_CANARY_OPS,
                  dep=EUROPE, dest=CANARIES),
    "un857_gcrr": dict(route=UN857, rate=2, levels=range(330, 400, 10), types=EU_SHORT, ops=["EXS", "RYR", "TOM", "EZY"],
                       dep=["EGCC", "EGNM", "EGBB", "EGGW", "EIDW"], dest=["GCRR"], arrival=True),
    "runax_std": dict(route=STD_RUNAX, rate=7, levels=range(330, 400, 10), types=EU_SHORT, ops=EU_CANARY_OPS,
                      dep=EUROPE, dest=CANARIES),
    "runax_gcrr": dict(route=STD_RUNAX, rate=2, levels=range(330, 400, 10), types=EU_SHORT,
                       ops=["EWG", "CFG", "TUI", "RYR"], dep=["EDDL", "EDDF", "EDDM", "LSZH"], dest=["GCRR"],
                       arrival=True),
    "un858": dict(route=UN858, rate=6, levels=range(300, 420, 10), types=EU_SHORT + ["A332"],
                  ops=EU_CANARY_OPS + AFRICA_OPS, dep=CANARIES + WAFRICA, dest=EUROPE + MED),
    "desum_akuda": dict(route=DESUM_AKUDA, rate=4, levels=range(300, 420, 10), types=EU_SHORT, ops=EU_CANARY_OPS,
                        dep=["GCLP", "GCTS", "GCRR", "GCFV"], dest=EUROPE + MED),
    "takav_vedod": dict(route=["TAKAV", "VEDOD", "TERTO"], rate=5, levels=range(330, 400, 10), types=EU_SHORT,
                        ops=EU_CANARY_OPS, dep=EUROPE, dest=CANARIES + ["GCRR"]),
    "un873": dict(route=UN873, rate=8, levels=range(310, 410, 10), types=EU_SHORT + LONG, ops=EU_CANARY_OPS + LONG_OPS,
                  dep=EUROPE, dest=SAMERICA + ["GCTS", "GCLA", "GVAC", "GCLP", "GCLP"]),
    "un866": dict(route=UN866, rate=6, levels=range(310, 410, 10), types=LONG, ops=LONG_OPS, dep=EUROPE,
                  dest=SAMERICA),
    "runax_samar": dict(route=["RUNAX", "IBALU", "SAMAR"], rate=4, levels=range(310, 400, 10), types=EU_SHORT + LONG,
                        ops=EU_CANARY_OPS + LONG_OPS, dep=["EDDF", "EDDM", "LIMC", "LSZH", "LOWW"],
                        dest=SAMERICA + ["GCTS"]),
    "takav_domog": dict(route=["TAKAV", "DOMOG"], rate=3, levels=range(290, 380, 10), types=EU_SHORT,
                        ops=["RAM", "TVF", "EZY", "RYR", "IBE"], dep=EUROPE, dest=["GMMX", "GMAD"]),
    "marja_arr": dict(route=["AMSEL", "ABODA", "MARJA"], rate=3, levels=range(290, 340, 10), types=["B738", "E190", "B739", "B788"],
                      ops=["RAM"], dep=["LPPT", "LPPR", "EGLL", "EHAM", "LFPG", "EBBR"], dest=["GMMN"], arrival=True),
    "marja_south": dict(route=["AMSEL", "ABODA", "MARJA"], rate=2, levels=range(290, 340, 10),
                        types=["B738", "A320", "B38M", "A20N"], ops=["RAM", "RYR", "EZY", "TVF", "TOM"],
                        dep=["LFPG", "EGKK", "EHAM", "EBBR", "LFLL", "EDDF"], dest=["GMMX", "GMMX", "GMAD"]),
    "salca_arr": dict(route=["OSLAD", "IBALU", "SALCA"], rate=2, levels=range(310, 380, 10), types=["B738", "B739", "E190"],
                      ops=["RAM", "TAP"], dep=["LPPT", "LPPR", "LEMD"], dest=["GMMN"], arrival=True),
    "gmmn_dep_nat": dict(route=["SALCA", "IBALU", "SUBAL"], rate=1, levels=[260, 280], types=["B788", "B789"],
                         ops=["RAM"], dep=["GMMN"], dest=["KJFK", "KMIA", "KIAD", "KBOS", "KORD"], climb=(340, 380)),
    "gmmn_dep_nat_b": dict(route=["SALCA", "IBALU", "BEXAL"], rate=1, levels=[260, 280], types=["B788", "B789"],
                           ops=["RAM"], dep=["GMMN"], dest=["CYUL", "CYYZ", "KJFK", "KIAD"], climb=(340, 380)),
    "subal_nat": dict(route=["SUBAL", "IBALU", "SALCA"], rate=1, levels=range(330, 400, 10), types=["B788", "B789"],
                      ops=["RAM"], dep=["KJFK", "KMIA", "KBOS", "KORD", "CYUL"], dest=["GMMN"], arrival=True),
    "salca_nat": dict(route=["BEXAL", "IBALU", "SALCA"], rate=2, levels=range(330, 400, 10), types=["B788", "B789"],
                      ops=["RAM"], dep=["KJFK", "KMIA", "KIAD", "CYUL", "KBOS"], dest=["GMMN"], arrival=True),
    "nipri_rolro": dict(route=["NIPRI", "ROLRO"], rate=2, levels=range(310, 400, 10), types=["B738", "A320", "B788"],
                        ops=["RAM", "TAP", "SAT"], dep=["LPPD", "LPAZ", "KJFK"], dest=["GMMX", "GMAD"]),
    # northbound
    "konba_bexal": dict(route=UN866[::-1], rate=3, levels=range(300, 420, 10), types=LONG, ops=LONG_OPS,
                        dep=SAMERICA, dest=EUROPE),
    "samar_bexal": dict(route=["SAMAR", "BEXAL"], rate=7, levels=range(300, 420, 10), types=LONG, ops=LONG_OPS,
                        dep=SAMERICA, dest=EUROPE),
    "samar_tiggi": dict(route=["SAMAR", "TIGGI"], rate=6, levels=range(300, 420, 10), types=LONG + EU_SHORT,
                        ops=LONG_OPS + EU_CANARY_OPS, dep=SAMERICA + ["GCTS", "GVAC"], dest=EUROPE),
    "vasto_barok": dict(route=["VASTO", "BAROK"], rate=6, levels=range(300, 420, 10), types=EU_SHORT + LONG,
                        ops=EU_CANARY_OPS + LONG_OPS, dep=CANARIES + WAFRICA + SAMERICA, dest=EUROPE),
    "vasto_tiggi": dict(route=["VASTO", "NIDEB", "TIGGI"], rate=6, levels=range(300, 420, 10), types=EU_SHORT,
                        ops=EU_CANARY_OPS, dep=CANARIES + ["GCRR"], dest=EUROPE),
    "gmmn_dep_amsel": dict(route=["MARJA", "ABODA", "AMSEL"], rate=2, levels=[240, 260], types=["B738", "E190", "B739"],
                           ops=["RAM"], dep=["GMMN"], dest=["LPPT", "LFPG", "EHAM", "EGLL"], climb=(340, 380)),
    "gmmn_dep_oslad": dict(route=["NISBA", "IBALU", "OSLAD"], rate=2, levels=[240, 260], types=["B738", "B788"],
                           ops=["RAM"], dep=["GMMN"], dest=["LPPT", "EGLL", "LEMD", "KJFK"], climb=(340, 380)),
}

# which flows run in which control area (a west run carries only traffic that crosses the west sectors)
WEST_FLOWS = ["un857", "un857_gcrr", "runax_std", "runax_gcrr", "un858", "desum_akuda", "takav_vedod", "runax_samar",
              "takav_domog", "marja_arr", "marja_south", "salca_arr", "salca_nat", "gmmn_dep_nat", "gmmn_dep_nat_b", "subal_nat", "nipri_rolro", "vasto_barok", "vasto_tiggi",
              "gmmn_dep_amsel", "gmmn_dep_oslad"]
OCEANIC_FLOWS = ["un873", "un866", "konba_bexal", "gmmn_dep_nat", "gmmn_dep_nat_b", "subal_nat", "runax_samar", "samar_bexal", "samar_tiggi", "vasto_barok", "vasto_tiggi",
                 "salca_nat", "nipri_rolro"]
ALL_FLOWS = list(FLOWS)


def _southbound(route):
    w = asp_mod.get("agadir").waypoints
    b = bearing(w[route[0]], w[route[-1]])
    return 90.0 <= b < 270.0


def _max_fl(typ, load):
    p = performance.get(typ)
    m = p.oew + load * (p.mtow - p.oew)
    return p.max_fl("mach", p.cruise_mach, m)


def generate(seed: int, flows, scale: float = 1.0, t0: float = -1700.0, t1: float = 3500.0, used=None,
             rates=None) -> list:
    rng = random.Random(seed)
    used = set(used or [])
    out = []
    for name in flows:
        f = FLOWS[name]
        rate = (rates or {}).get(name, f["rate"]) * scale
        n = max(1, int(round(rate * (t1 - t0) / 3600.0)))
        gap = (t1 - t0) / n
        south = _southbound(f["route"])
        last_at_level = {}
        for k in range(n):
            t = t0 + (k + 0.5) * gap + rng.uniform(-0.35, 0.35) * gap
            typ = rng.choice(f["types"])
            load = round(rng.uniform(0.3, 0.6), 2)
            mx = _max_fl(typ, load)
            levels = [l for l in f["levels"] if level_parity_ok(l, south) and l <= mx]
            if not levels:
                continue
            rng.shuffle(levels)
            # avoid the same level as a flight on this route less than ~100 s ahead
            fl = next((l for l in levels if t - last_at_level.get(l, -1e9) > 100), levels[0])
            last_at_level[fl] = t
            for _ in range(50):
                cs = rng.choice(f["ops"]) + str(rng.randint(10, 9899)) + rng.choice(["", "", "", "A", "K", "X", "P"])
                if cs not in used:
                    break
            used.add(cs)
            a = {"callsign": cs, "type": typ, "route": list(f["route"]), "fl": fl, "load": load,
                 "entry_time": round(t), "dep": rng.choice(f["dep"]), "dest": rng.choice(f["dest"]),
                 "direction": "southbound" if south else "northbound", "flow": name}
            if not f.get("arrival"):
                a["arrival"] = False
            if f.get("climb"):
                a["_climb"] = rng.choice([c for c in range(f["climb"][0], f["climb"][1] + 1, 20)
                                          if level_parity_ok(c, south) and c <= mx] or [fl])
            out.append(a)
    return out


def prune(scn: dict) -> dict:
    """Drop generated flights that cannot exist in this scenario (already gone at T+0, or never in the area)."""
    for _ in range(400):
        try:
            build_world(scn)
            return scn
        except ScenarioError as e:
            cs = str(e).split(":")[0].strip()
            before = len(scn["aircraft"])
            scn["aircraft"] = [a for a in scn["aircraft"] if a["callsign"] != cs or not a.get("flow")]
            scn["requests"] = [r for r in scn.get("requests", []) if r["callsign"] in {a["callsign"] for a in scn["aircraft"]}]
            if len(scn["aircraft"]) == before:
                raise
    return scn


def departure_requests(scn: dict, delay=(60, 180), seed=0) -> None:
    """GMMN departures call climbing-capable at FL240/260 and ask for their cruise level shortly after check-in."""
    rng = random.Random(seed)
    w = build_world(scn)
    for a in scn["aircraft"]:
        if a.get("_climb"):
            ac = w.aircraft[a["callsign"]]
            t = max(5.0, ac.boundary_time - 60) + rng.uniform(*delay)
            scn.setdefault("requests", []).append({"time": round(t), "callsign": a["callsign"], "type": "altitude",
                                                   "value": a["_climb"], "reason": "requesting cruise level"})
    for a in scn["aircraft"]:
        a.pop("_climb", None)


def deconflict(scn: dict, keep: set, rounds: int = 6, verbose: bool = True) -> dict:
    """Move background flights that lose separation (passive run) to another level of the same parity."""
    for rnd in range(rounds):
        r = run(scn, "passive")
        pairs = [tuple(p) for p in r["los_pairs"]]
        bad = [p for p in pairs if not (p[0] in keep and p[1] in keep)]
        if verbose:
            print(f"    deconflict round {rnd}: {len(pairs)} losses, {len(bad)} unplanned")
        if not bad:
            break
        by = {a["callsign"]: a for a in scn["aircraft"]}
        moved = set()
        for a_cs, b_cs in bad:
            cs = b_cs if b_cs not in keep and b_cs not in moved else a_cs
            if cs in keep or cs in moved or cs not in by:
                continue
            other = by.get(a_cs if cs == b_cs else b_cs)
            a = by[cs]
            south = a.get("direction", "southbound") == "southbound"
            mx = _max_fl(a["type"], a.get("load", 0.5))
            cands = [a["fl"] + d for d in (20, -20, 40, -40, 60, -60)
                     if level_parity_ok(a["fl"] + d, south) and 290 <= a["fl"] + d <= min(mx, 410)]
            if other is not None:
                cands = [c for c in cands if abs(c - other["fl"]) >= 20]
            if cands:
                a["fl"] = cands[(rnd + hash(cs)) % len(cands)]
                moved.add(cs)
    return scn


def sector_loads(scn: dict, t0: float = 0.0, t1: float = 3600.0) -> dict:
    """Flights per sector between t0 and t1 (passive run, sampled every 20 s)."""
    asp = asp_mod.get("agadir")
    r = run(scn, "passive", trace=True)
    counts = {s.id: 0 for s in asp.sectors}
    for cs, pts in r["trace"].items():
        seen = set()
        for (t, x, y, *_r) in pts[::20]:
            if t0 <= t <= t1:
                s = asp.sector_of((x, y))
                if s:
                    seen.add(s)
        for s in seen:
            counts[s] += 1
    return counts, r


def quick_loads(aircraft: list, t0: float = 0.0, t1: float = 3600.0, gs_kt: float = 460.0) -> dict:
    """Fast geometric estimate of flights per sector (constant ground speed along the filed route)."""
    asp = asp_mod.get("agadir")
    w = asp.waypoints
    counts = {s.id: 0 for s in asp.sectors}
    for a in aircraft:
        pts = [w[n] for n in a["route"]]
        t = a.get("entry_time", 0.0)
        seen = set()
        for p, q in zip(pts, pts[1:]):
            L = dist(p, q)
            n = max(1, int(L / 10))
            for k in range(n):
                tt = t + (k + 0.5) / n * L / gs_kt * 3600
                if t0 <= tt <= t1:
                    s = asp.sector_of((p[0] + (q[0] - p[0]) * (k + 0.5) / n, p[1] + (q[1] - p[1]) * (k + 0.5) / n))
                    if s:
                        seen.add(s)
            t += L / gs_kt * 3600
        for s in seen:
            counts[s] += 1
    return counts
