"""Greedy reference-solution finder for arrival-sequencing scenarios (fast time, uses the real simulator).

Arrivals are taken in the order they reach their fix. Each one, in turn, gets the first candidate that gives the
required spacing behind the previous arrival at the same fix with no loss of separation involving it:
    nothing / Mach + conversion-IAS restriction / dogleg (heading off track for T s, then direct to the fix)
Descent requests are approved; arrivals still too high at their latest-descent point are cleared to the LoA level.
The script found is a *proof of solvability*, not the best plan.
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from atcsim.evaluate import run  # noqa: E402


def _crossings(r):
    out = {}
    for fix, cl in r["metrics"]["sequencing"]["crossings"].items():
        for c in cl:
            out[c["callsign"]] = (fix, c)
    return out


def _track_after_checkin(trace, cs, delay):
    pts = trace[cs]
    t_on = next((p[0] for p in pts if p[4]), pts[0][0])
    for (t0, x0, y0, *_), (t1, x1, y1, *_) in zip(pts, pts[1:]):
        if t1 >= t_on + delay:
            return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360
    return None


def solve(scn: dict, spacing: float = 11.0, verbose: bool = True, extra_base=None) -> list:
    arrivals = []
    base = list(extra_base or [])
    r0 = run(scn, "scripted", base + [{"on_request": a["callsign"], "type": "altitude", "do": [{"reply": "approved"}]}
                                      for a in scn["aircraft"]], trace=True)
    cr = _crossings(r0)
    order = sorted(cr, key=lambda cs: cr[cs][1]["time_s"])
    script = base + [{"on_request": cs, "type": "altitude", "do": [{"reply": "approved"}]} for cs in order]
    # check-in time per aircraft (from the trace: first sample after it calls is not recorded, use events instead)
    for k, cs in enumerate(order):
        fix = cr[cs][0]
        prev = [c for c in order[:k] if cr[c][0] == fix]

        def ok(res):
            c = _crossings(res)
            if cs not in c:
                return False, 0.0
            sp = c[cs][1].get("spacing_nm")
            later = set(order[k + 1:])          # later arrivals get fixed in their own turn
            los = [p for p in res["los_pairs"] if cs in p and not (set(p) - {cs}) & later]
            lvl = c[cs][1].get("level_ok", True)
            good = (sp is None or sp >= spacing) and not los and lvl and not res["rejected"]
            return good, sp or 99

        res = run(scn, "scripted", script, trace=True)
        good, sp = ok(res)
        if verbose:
            print(f"  {cs} -> {fix}: as is spacing {sp} NM, ok={good}")
        if good:
            continue
        cands = []
        for m in (0.76, 0.74, 0.72):
            cands.append([{"on_checkin": cs, "delay_s": 15, "do": [{"callsign": cs, "type": "speed", "mach": m,
                                                                   "conversion_kt": 250}]}])
        trk = None
        for side in (1, -1):
            for off in (30, 45, 60):
                for T in (120, 180, 240, 300, 360, 420, 480, 600):
                    cands.append(("dog", side, off, T))
        found = False
        for c in cands:
            if isinstance(c, tuple):
                _, side, off, T = c
                if trk is None:
                    trk = _track_after_checkin(res["trace"], cs, 20)
                hdg = int(round((trk + side * off) % 360)) or 360
                steps = [{"on_checkin": cs, "delay_s": 20, "do": [{"callsign": cs, "type": "heading", "heading": hdg}]},
                         {"on_checkin": cs, "delay_s": 20 + T, "do": [{"callsign": cs, "type": "direct",
                                                                      "waypoint": fix}]}]
            else:
                steps = c
            res2 = run(scn, "scripted", script + steps, trace=False)
            good, sp = ok(res2)
            if good:
                script += steps
                found = True
                if verbose:
                    print(f"     fixed with {steps}: spacing {sp}")
                break
        if not found:
            print(f"  !! no candidate fixes {cs}")
    return script


if __name__ == "__main__":
    import build_scenarios as B
    names = sys.argv[1:]
    for fn in B.SCENARIOS:
        s = fn()
        if s["name"].split("_")[0] in names or s["name"] in names:
            print(s["name"])
            sol = solve(s, extra_base=s.get("solution_base"))
            with open(os.path.join(HERE, "solutions", s["name"] + ".json"), "w") as f:
                json.dump(sol, f)
