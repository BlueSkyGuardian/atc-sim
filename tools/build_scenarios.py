"""Generates the scenario library in ../scenarios/.

Each trap is built geometrically (crossing points, timings) and then CHECKED in fast time:
  * 'passive'       – nobody says anything
  * 'naive_approve' – every pilot request is approved immediately, unchecked
The `expected` block records what each reference policy must produce; the build fails if the
simulation disagrees, so the traps are guaranteed to bite.

Run:  python tools/build_scenarios.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from atcsim.evaluate import run  # noqa: E402
from atcsim.geometry import BOUNDARY_WAYPOINTS as WP, segment_intersection  # noqa: E402

OUT = os.path.join(ROOT, "scenarios")


def X(r1, r2):
    """Crossing point of two published routes, rounded."""
    p = segment_intersection(WP[r1[0]], WP[r1[1]], WP[r2[0]], WP[r2[1]])
    assert p, f"{r1} and {r2} do not cross"
    return [round(p[0], 3), round(p[1], 3)]


def meet(scn, mover, route, policy="naive_approve", script=None, after=0.0, shift_s=0.0):
    """Where and when `mover`, flown alone under `policy`, first crosses the line of `route` (two waypoints)
    after `after` s. Used to put a second aircraft exactly there (a trap) or deliberately off it."""
    s2 = json.loads(json.dumps(scn))
    s2["aircraft"] = [a for a in s2["aircraft"] if a["callsign"] == mover]
    s2["requests"] = [r for r in s2.get("requests", []) if r["callsign"] == mover]
    r = run(s2, policy, script, trace=True)
    pts = r["trace"][mover]
    from atcsim.scenario import build_world
    wps = build_world(s2).waypoints
    A, B = wps[route[0]], wps[route[1]]
    for (t0, x0, y0, *_), (t1, x1, y1, *_) in zip(pts, pts[1:]):
        if t1 < after:
            continue
        p = segment_intersection((x0, y0), (x1, y1), A, B)
        if p:
            return [round(p[0], 3), round(p[1], 3)], round(t0 + shift_s)
    raise AssertionError(f"{mover} never crosses {route}")


def put(scn, cs, **kw):
    """Replace the timing of aircraft `cs` in a scenario dict (reach a point at a time)."""
    for a in scn["aircraft"]:
        if a["callsign"] == cs:
            a.pop("entry_time", None)
            a["reach"] = {"point": kw["at"], "time": kw["t"]}
    return scn


def XA(r1, r2, airspace="agadir"):
    """Crossing point of two legs (each two waypoint names) of a real airspace."""
    from atcsim import airspace as asp_mod
    w = asp_mod.get(airspace).waypoints
    p = segment_intersection(w[r1[0]], w[r1[1]], w[r2[0]], w[r2[1]])
    assert p, f"{r1} and {r2} do not cross"
    return [round(p[0], 3), round(p[1], 3)]


def ac(cs, typ, route, fl, mach=None, *, entry=None, at=None, t=None, load=None, overflight=False, **fp):
    """mach=None -> the type's typical cruise Mach (OpenAP WRAP statistics).
    fp: flight-plan / category fields passed through: dep, dest, rvsm, military, formation, equipment, remarks."""
    d = {"callsign": cs, "type": typ, "route": route, "fl": fl}
    d.update(fp)
    if mach is not None:
        d["mach"] = mach
    if load is not None:
        d["load"] = load
    if overflight:
        d["arrival"] = False
    if entry is not None:
        d["entry_time"] = entry
    else:
        d["reach"] = ({"waypoint": at} if isinstance(at, str) else {"point": at}) | {"time": t}
    return d


def req(t, cs, typ, val, reason):
    return {"time": t, "callsign": cs, "type": typ, "value": val, "reason": reason}


# Winter westerly regime over Morocco / the Canaries: subtropical jet north of the sector (wind FROM, kt)
WEST_JET = {
    "seed": 21,
    "temperature": {"surface_c": 13, "tropopause_fl": 340},
    "wind": {"profile": [{"fl": 0, "dir": 250, "kt": 12}, {"fl": 100, "dir": 260, "kt": 28},
                         {"fl": 200, "dir": 265, "kt": 42}, {"fl": 300, "dir": 270, "kt": 55},
                         {"fl": 400, "dir": 275, "kt": 42}, {"fl": 500, "dir": 280, "kt": 25},
                         {"fl": 650, "dir": 90, "kt": 8}, {"fl": 800, "dir": 90, "kt": 20}],
             "jet": {"core_fl": 340, "dir": 268, "kt": 65, "axis_point": [0, 30], "half_width_nm": 90,
                     "depth_below_ft": 11000, "depth_above_ft": 8000, "streak_length_nm": 600,
                     "streak_speed_kt": 30, "meander_amp_nm": 10, "meander_wavelength_nm": 500},
             "noise_kt": 5, "noise_scale_nm": 45},
}
# Summer northerly regime (Azores high to the north-west), warm, no jet over the sector
NORTHERLY = {
    "seed": 22,
    "temperature": {"surface_c": 25, "tropopause_fl": 380},
    "wind": {"profile": [{"fl": 0, "dir": 20, "kt": 18}, {"fl": 100, "dir": 10, "kt": 22},
                         {"fl": 250, "dir": 355, "kt": 35}, {"fl": 350, "dir": 350, "kt": 55},
                         {"fl": 450, "dir": 340, "kt": 35}, {"fl": 600, "dir": 90, "kt": 10},
                         {"fl": 800, "dir": 90, "kt": 22}],
             "noise_kt": 4, "noise_scale_nm": 40},
}
# Spring convective regime: moist, unstable, south-westerly flow aloft
CONVECTIVE_SW = {
    "seed": 31,
    "temperature": {"surface_c": 24, "tropopause_fl": 390, "lapse_c_per_kft": 2.1},   # unstable: steep lapse
    "wind": {"profile": [{"fl": 0, "dir": 200, "kt": 10}, {"fl": 100, "dir": 220, "kt": 22},
                         {"fl": 250, "dir": 240, "kt": 40}, {"fl": 350, "dir": 250, "kt": 55},
                         {"fl": 450, "dir": 255, "kt": 40}, {"fl": 600, "dir": 90, "kt": 8},
                         {"fl": 800, "dir": 90, "kt": 20}],
             "noise_kt": 5, "noise_scale_nm": 40},
}


def wx(base: dict, **extra) -> dict:
    d = json.loads(json.dumps(base))
    d.update(extra)
    return d


SCENARIOS = []


def scenario(fn):
    SCENARIOS.append(fn)
    return fn


# ---------------------------------------------------------------------------------------------
@scenario
def s00():
    return {
        "name": "s00_free_flow",
        "title": "Free flow – nothing to do",
        "description": "Seven flights, all procedurally separated (different levels or well spaced). "
                       "Three routine requests.",
        "design_notes": "Tests over-control. No conflicts exist and every request can be approved safely. "
                        "A good agent approves all three and issues no other instruction.",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": WEST_JET, "areas": [],
        "aircraft": [
            ac("RAM210", "B738", ["N1", "S3"], 350, None, entry=30),
            ac("AFR611", "A320", ["N4", "S2"], 370, None, entry=60),
            ac("DLH4KL", "A21N", ["W1", "E2"], 390, None, entry=120, load=0.3),
            ac("IBE3271", "A320", ["E1", "W2"], 350, None, entry=200),
            ac("TAP1462", "E190", ["S1", "N2"], 380, None, entry=90),
            ac("EZY45QF", "A320", ["S4", "N4"], 360, None, entry=400),
            ac("CNTKA", "C550", ["E2", "W1"], 400, None, entry=150),
        ],
        "requests": [
            req(250, "TAP1462", "speed", 0.74, "early arrival at destination"),
            req(420, "DLH4KL", "direct", "E1", "company reroute, destination change"),
            req(600, "EZY45QF", "altitude", 380, "better ride reported"),
        ],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": False}},
    }


@scenario
def s01():
    c = X(["N1", "S4"], ["N4", "S1"])
    return {
        "name": "s01_basic_crossing",
        "title": "Basic crossing at the same level",
        "description": "Two flights at FL350 on crossing routes, plus an overflight at FL370.",
        "design_notes": "RAM201 and AFR452 reach the centre crossing point at the same second (t=660). "
                        "Classic solution: one level change (e.g. AFR452 FL340 or FL360 - NOT 370, DLH82 is there) "
                        "or a heading for one of them. Passive => loss of separation.",
        "duration_s": 1500, "pilot_delay_s": 4, "areas": [],
        "aircraft": [
            ac("RAM201", "B738", ["N1", "S4"], 350, None, at=c, t=660),
            ac("AFR452", "A320", ["N4", "S1"], 350, None, at=c, t=660),
            ac("DLH82", "A343", ["W1", "E1"], 370, None, entry=180, load=0.4),
        ],
        "requests": [],
        "expected": {"passive": {"los": True}, "naive_approve": {"los": True}},
    }


@scenario
def s02():
    rA, rB, rC = ["N2", "S3"], ["W1", "E2"], ["E1", "W2"]
    c_ab = X(rA, rB)
    c_ac = X(rA, rC)
    return {
        "name": "s02_climb_request_into_crossing",
        "title": "Climb request into crossing traffic",
        "description": "A southbound flight at FL350 asks for FL370 for a better ride. Two crossing flights at FL370 "
                       "and FL390 over the centre.",
        "design_notes": "RAM101 (FL350) and AFR202 (FL370) cross at t=600 - separated by 2000 ft, so harmless. "
                        "At t=330 RAM101 requests FL370. Approving it immediately puts both at FL370 at the crossing. "
                        "The 'clever' amendment FL390 also fails: EZY77 at FL390 crosses the same point 15 s later. "
                        "Correct: 'unable, expect higher after crossing traffic', then FL370 once AFR202 is >5 NM "
                        "and diverging (after ~t=640).",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": WEST_JET, "areas": [],
        "aircraft": [
            ac("RAM101", "B738", rA, 350, None, at=c_ab, t=600),
            ac("AFR202", "A320", rB, 370, None, at=c_ab, t=600),
            ac("EZY77", "A320", rC, 390, None, at=c_ac, t=615),
        ],
        "requests": [req(330, "RAM101", "altitude", 370, "smoother ride reported at FL370")],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True, "pairs": [["AFR202", "RAM101"]]}},
    }


@scenario
def s03():
    return {
        "name": "s03_descent_through_opposite",
        "title": "Descent request through opposite-direction traffic",
        "description": "Two flights on the same route in opposite directions, 3000 ft apart. "
                       "The upper one reports turbulence and asks to descend.",
        "design_notes": "DLH303 FL390 southbound and RAM304 FL360 northbound on the N3-S2 route meet head-on at the "
                        "centre at t=600 (closing speed 900 kt). DLH303 asks for FL350 at t=520, when they are "
                        "20 NM apart: the descent would cross FL360 roughly where they pass. Correct: unable until "
                        "passed: 'unable, opposite-direction traffic, expect lower in 2 minutes'. FL370 is an acceptable "
                        "interim step (1000 ft above RAM304).",
        "duration_s": 1400, "pilot_delay_s": 4, "weather": NORTHERLY, "areas": [],
        "aircraft": [
            ac("DLH303", "A21N", ["N3", "S2"], 390, None, at=[0.0, 0.0], t=600, load=0.35),
            ac("RAM304", "B738", ["S2", "N3"], 360, None, at=[0.0, 0.0], t=600),
            ac("KLM52M", "B738", ["W2", "E2"], 330, None, entry=700),
        ],
        "requests": [req(520, "DLH303", "altitude", 350, "moderate turbulence at FL390")],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True, "pairs": [["DLH303", "RAM304"]]}},
    }


@scenario
def s04():
    rA, rB = ["N1", "S1"], ["W1", "E2"]
    p = X(rA, ["W2", "E1"])
    scn = {
        "name": "s04_direct_request_reroute",
        "title": "Direct-to request that crosses traffic",
        "description": "A flight asks for a different exit (company reroute). Its new track would cut across "
                       "a south-eastbound flight at the same level.",
        "design_notes": "AFR404 (N1->S1, FL350) and TVF88 (W1->E2, FL350, both southbound): on their filed routes "
                        "TVF88 crosses AFR404's track well before AFR404 gets there - no conflict. At t=360 AFR404 "
                        "requests direct S3. The new diagonal track meets TVF88 further east. Correct: approve "
                        "with a level change (FL330/FL370 are free; FL340/FL360 would be wrong-direction levels), "
                        "delay the direct, or unable.",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": WEST_JET, "areas": [],
        "aircraft": [
            ac("AFR404", "A320", rA, 350, None, at=p, t=820),
            ac("TVF88", "B738", rB, 350, None, entry=0),
            ac("BAW9J", "B772", ["S4", "N3"], 380, None, entry=150, load=0.35),
        ],
        "requests": [req(360, "AFR404", "direct", "S3", "company reroute, destination change")],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True}},
    }
    pt, t = meet(scn, "AFR404", rB, after=370)
    return put(scn, "TVF88", at=pt, t=t)


@scenario
def s05():
    return {
        "name": "s05_slowdown_on_converging_exit",
        "title": "Speed request on routes converging to the same exit",
        "description": "Two flights at FL370 converge on exit S2, about 10 NM in trail. The leader asks to slow down.",
        "design_notes": "RAM740 (E190, N1->S2) leads SVA112 (B738, N3->S2) by ~80 s at the merge on S2: about 10 NM, "
                        "comfortable at constant speed. At t=420 RAM740 asks for 380 kt (early arrival). Approving "
                        "it lets SVA112 run into it before S2. Correct: unable (or give SVA112 a matching speed / a "
                        "level change - FL360 and FL380 are free).",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": WEST_JET, "areas": [],
        "aircraft": [
            ac("RAM740", "E190", ["N1", "S2"], 370, None, at="S2", t=1060),
            ac("SVA112", "B738", ["N3", "S2"], 370, None, at="S2", t=1140),
            ac("UAE751", "B773", ["W1", "E2"], 390, None, entry=300, load=0.3),
        ],
        "requests": [req(420, "RAM740", "speed", 0.70, "early arrival, holding expected at destination")],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True, "pairs": [["RAM740", "SVA112"]]}},
    }


S06_DT = 700


@scenario
def s06():
    rA, rB = ["W1", "E2"], ["N2", "S3"]
    p = X(rA, rB)
    return {
        "name": "s06_speed_up_into_crossing",
        "title": "Sleeper conflict activated by a speed request",
        "description": "Two crossing southbound flights at FL370 pass about 8 NM apart. A routine-looking speed "
                       "request arrives at the same time as a harmless level request from another flight.",
        "design_notes": "IBE612 (W1->E2) crosses KLM88B's route behind KLM88B: about 8 NM, legal. At t=300 IBE612 "
                        "asks for Mach .81; that erases the margin (LoS at the crossing). At t=305 BAW15 asks FL380, "
                        "which is safe - the distraction. Correct: approve BAW15, unable/amend IBE612 (or approve it "
                        "with a level change: FL350/FL390 free).",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": NORTHERLY, "areas": [],
        "aircraft": [
            ac("KLM88B", "B738", rB, 370, None, at=p, t=640),
            ac("IBE612", "A320", rA, 370, 0.74, at=p, t=S06_DT, overflight=True),
            ac("BAW15", "B772", ["E2", "W1"], 400, None, entry=100, load=0.3),
        ],
        "requests": [
            req(300, "IBE612", "speed", 0.81, "making up a delay"),
            req(305, "BAW15", "altitude", 380, "better winds reported at FL380"),
        ],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True, "pairs": [["IBE612", "KLM88B"]]}},
    }


@scenario
def s07():
    return {
        "name": "s07_restricted_area_activation",
        "title": "Restricted area activates across a route",
        "description": "A military restricted area (surface to FL450) activates at T+05:00 across the W1-E2 route. "
                       "A danger area is active below FL300 and a small prohibited area sits south-east.",
        "design_notes": "R12 activates at t=300 across W1->E2. AMC31 (FL350) will fly straight into it unless vectored. "
                        "Avoiding NORTH runs into JAF412 (FL350, N2->S3) crossing just north of the area; avoiding SOUTH "
                        "is clear of traffic but must stay clear of prohibited P7. D20 is irrelevant at FL350 (tests "
                        "that the agent reads vertical limits). Passive => restricted area infringement.",
        "duration_s": 1700, "pilot_delay_s": 4, "weather": WEST_JET,
        "areas": [
            {"id": "R12", "name": "Military exercise", "kind": "restricted",
             "polygon": [[-38, -2], [-18, -2], [-18, 16], [-38, 16]], "lower_fl": 0, "upper_fl": 450,
             "active": False, "schedule": [{"time": 300, "active": True}, {"time": 1500, "active": False}]},
            {"id": "D20", "name": "Glider area", "kind": "danger",
             "circle": {"center": [20, 30], "radius_nm": 10}, "lower_fl": 0, "upper_fl": 300, "active": True},
            {"id": "P7", "name": "Protected site", "kind": "prohibited",
             "circle": {"center": [-14, -22], "radius_nm": 5}, "lower_fl": 0, "upper_fl": 660, "active": True},
        ],
        "aircraft": [
            ac("AMC31", "A320", ["W1", "E2"], 350, None, entry=200),
            ac("JAF412", "B738", ["N2", "S3"], 350, None, at=[-11.6, 46.4], t=420),
            ac("RAM9F", "B788", ["S1", "N1"], 380, None, entry=250),
        ],
        "requests": [],
        "expected": {"passive": {"los": False, "infringement": True}, "naive_approve": {"los": False, "infringement": True}},
    }


@scenario
def s08():
    rA, rB = ["N2", "S4"], ["N3", "S1"]
    p = X(rA, rB)
    return {
        "name": "s08_simultaneous_level_requests",
        "title": "Two simultaneous requests for the same level",
        "description": "Two southbound crossing flights at FL350 and FL390 both ask for FL370 within seconds of each "
                       "other.",
        "design_notes": "UAE91 (FL350) and QTR22 (FL390) cross at t=620. At t=420 UAE91 asks FL370; at t=422 QTR22 asks "
                        "FL370. Each request checked alone against the *current* picture looks safe; approving both "
                        "puts them at FL370 at the crossing. Correct: approve one, unable the other (until past). "
                        "An agent that probes each request against current levels instead of cleared levels fails.",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": WEST_JET, "areas": [],
        "aircraft": [
            ac("UAE91", "B77W", rA, 350, None, at=p, t=620),
            ac("QTR22", "A359", rB, 390, None, at=p, t=620, load=0.4),
            ac("MSR77", "A320", ["E1", "W1"], 380, None, entry=200),
        ],
        "requests": [
            req(420, "UAE91", "altitude", 370, "optimum level"),
            req(422, "QTR22", "altitude", 370, "optimum level"),
        ],
        "expected": {"passive": {"los": False}, "naive_approve": {"los": True, "pairs": [["QTR22", "UAE91"]]}},
    }


@scenario
def s09():
    return {
        "name": "s09_area_vertical_limits",
        "title": "Descent request into an active restricted area",
        "description": "A restricted area is active from the surface to FL360. Two flights overfly it; both ask for "
                       "level changes.",
        "design_notes": "R40 (FL000-FL360) is active on the N4-S2 route. ARN501 overflies at FL370 (clear above the "
                        "inclusive upper limit) and asks FL350 at t=240 - approving drops it into "
                        "the area. ETD33 at FL390 asks FL410 at t=250 - harmless (distractor). Correct: unable ARN501 "
                        "until clear of R40, approve ETD33.",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": NORTHERLY,
        "areas": [
            {"id": "R40", "name": "Air defence training", "kind": "restricted",
             "polygon": [[10, 22], [34, 22], [34, -6], [10, -6]], "lower_fl": 0, "upper_fl": 360, "active": True},
        ],
        "aircraft": [
            ac("ARN501", "B738", ["N4", "S2"], 370, None, entry=60),
            ac("ETD33", "B789", ["N3", "S1"], 390, None, entry=90, load=0.3),
            ac("RAM330", "E75L", ["W2", "E2"], 250, None, entry=30),
        ],
        "requests": [
            req(240, "ARN501", "altitude", 350, "descent planning"),
            req(250, "ETD33", "altitude", 410, "fuel"),
        ],
        "expected": {"passive": {"los": False, "infringement": False},
                     "naive_approve": {"los": False, "infringement": True}},
    }


@scenario
def s10():
    """Busy mixed scenario: two built-in conflicts, four requests (two traps), area activation."""
    c1 = X(["N1", "S3"], ["W1", "E2"])
    c2 = X(["S4", "N2"], ["E2", "W1"])
    c3 = X(["N4", "S1"], ["N3", "S2"])
    return {
        "name": "s10_busy_mixed",
        "title": "Busy sector - mixed traffic, requests and an area",
        "description": "Eleven flights, a danger area activating mid-session, several pilot requests.",
        "design_notes": "Built-in conflicts: RAM602/AFR11 at FL370 (crossing t=700), EZY8/VLG33 at FL340 "
                        "(northbound, crossing t=900); with everyone southbound at FL370, AFR11 also meets TAP7 and "
                        "RAM602 meets SWR40 later on. TAP7 follows RYR3 on N2->S1 at FL370, 7 NM in trail at the same "
                        "speed (legal). D55 (danger, FL000-FL400) activates at t=800 under "
                        "IBE9's route - it needs a vector or a climb above FL400. Traps: KLM6 (FL350) asks FL370 at "
                        "t=520 (conflicts with SWR40 converging at the centre at t=760, and with RAM602); TAP7 asks "
                        "Mach .81 at t=600 (runs it into RYR3 before S1); UAE4 asks direct E1 at t=650 (new track "
                        "crosses D55 after activation). Safe: DLH5 FL360 -> FL380 at t=400.",
        "duration_s": 1800, "pilot_delay_s": 4, "weather": WEST_JET,
        "areas": [
            {"id": "D55", "name": "Live firing", "kind": "danger",
             "circle": {"center": [27, 2], "radius_nm": 7}, "lower_fl": 0, "upper_fl": 400,
             "active": False, "schedule": [{"time": 800, "active": True}]},
            {"id": "P3", "name": "Nuclear plant", "kind": "prohibited",
             "circle": {"center": [-48, -45], "radius_nm": 4}, "lower_fl": 0, "upper_fl": 660, "active": True},
        ],
        "aircraft": [
            ac("RAM602", "B738", ["N1", "S3"], 370, None, at=c1, t=700),
            ac("AFR11", "A320", ["W1", "E2"], 370, None, at=c1, t=700),
            ac("EZY8", "A320", ["S4", "N2"], 340, None, at=c2, t=900),
            ac("VLG33", "A320", ["E2", "W1"], 340, None, at=c2, t=900),
            ac("KLM6", "B738", ["N4", "S1"], 350, None, at=c3, t=760),
            ac("SWR40", "A21N", ["N3", "S2"], 370, None, at=c3, t=760),
            ac("RYR3", "B738", ["N2", "S1"], 370, 0.76, at="S1", t=1150),
            ac("TAP7", "A320", ["N2", "S1"], 370, 0.76, entry=150),
            ac("DLH5", "A21N", ["E2", "W1"], 360, None, entry=250, load=0.35),
            ac("UAE4", "A388", ["W2", "E2"], 390, None, entry=400, load=0.4),
            ac("IBE9", "A320", ["S3", "N4"], 380, None, entry=500),
        ],
        "requests": [
            req(400, "DLH5", "altitude", 380, "fuel"),
            req(520, "KLM6", "altitude", 370, "smoother ride reported"),
            req(600, "TAP7", "speed", 0.81, "making up time"),
            req(650, "UAE4", "direct", "E1", "company reroute"),
        ],
        "expected": {"passive": {"los": True}, "naive_approve": {"los": True}},
    }


S11_AFTER = 200


@scenario
def s11():
    scn = {
        "name": "s11_weather_deviation_into_traffic",
        "title": "Weather deviation request into crossing traffic",
        "description": "A mature thunderstorm (tops FL460) sits on the N1-S4 route in the middle of the sector, "
                       "drifting east at 20 kt. Westerly jet north of the sector. Pilots will ask for weather "
                       "deviations.",
        "design_notes": "RAM8TJ (B738, FL370, N1->S4) sees CB1 on its route and asks for a heading for ~65 miles. "
                        "On its filed route it is never close to IBE45 (A320, FL370, N2->S1), but the deviation it "
                        "asks for runs straight into IBE45. Approving it blindly loses separation. Saying unable is "
                        "not a solution either: the pilot will deviate anyway when the cell is 15 NM away. Correct: "
                        "approve the deviation with a level change (RAM8TJ FL390, or IBE45 FL330), or vector IBE45.",
        "duration_s": 1500, "pilot_delay_s": 4,
        "weather": wx(WEST_JET, cells=[{"id": "CB1", "x": -5, "y": 0, "at_time": 300, "top_fl": 460, "dbz": 59,
                                        "radius_nm": 5.0, "lifetime_s": 6000, "age_s": 1800,
                                        "motion": {"toward": 85, "kt": 20}}]),
        "areas": [],
        "aircraft": [
            ac("RAM8TJ", "B738", ["N1", "S4"], 370, None, entry=0),
            ac("IBE45", "A320", ["N2", "S1"], 370, None, entry=0),
            ac("TVF2", "B738", ["E1", "W2"], 350, None, entry=120),
        ],
        "requests": [],
        "expected": {"naive_approve": {"los": True, "pairs": [["IBE45", "RAM8TJ"]], "wx_request": "RAM8TJ"},
                     "passive": {"wx_request": "RAM8TJ"}},
        "solution": [{"on_request": "RAM8TJ", "type": "weather_deviation",
                      "do": [{"reply": "approved"}, {"callsign": "RAM8TJ", "type": "altitude", "fl": 390}]},
                     {"on_request": "TVF2", "type": "weather_deviation", "do": [{"reply": "approved"}]},
                     # RAM8TJ is an S4 arrival: down to the LoA level once clear of IBE45
                     {"on_request": "RAM8TJ", "type": "altitude", "do": [{"reply": "approved"}]},
                     {"at": 600, "do": [{"callsign": "RAM8TJ", "type": "altitude", "fl": 250}]}],
    }
    pt, t = meet(scn, "RAM8TJ", ["N2", "S1"], after=S11_AFTER)
    return put(scn, "IBE45", at=pt, t=t)


@scenario
def s12():
    return {
        "name": "s12_squall_line_gap",
        "title": "Squall line with a single gap",
        "description": "A severe squall line (tops FL450-500) lies NNW-SSE across the western half of the sector, "
                       "moving east-north-east at about 23 kt, with one gap about 22 NM wide near y = -10. "
                       "Trailing stratiform rain behind it. Every east-west flight has to get through the gap.",
        "design_notes": "AFR71 (A320, W1->E1) and RAM44 (B738, W2->E2) are both at FL350 on parallel routes 40 NM "
                        "apart, so they are never a factor for each other on their filed routes. The line forces "
                        "both of them through the same gap: AFR71 asks for ~140 for 40 miles, RAM44 for ~075 for "
                        "35 miles, and approving both funnels them into the gap together (LoS ~3 NM). UAE61 (A388, "
                        "FL370) and ETH5 (B788, FL380) come westbound through the same gap, so the level options "
                        "are limited. Correct: approve both deviations and separate the eastbound pair vertically "
                        "(e.g. RAM44 FL330, the next level in its direction), or sequence them through the gap.",
        "duration_s": 1800, "pilot_delay_s": 4,
        "weather": wx(CONVECTIVE_SW, convection={"regime": "squall_line", "intensity": "severe",
                                                 "line": {"center": [-30, 0], "length_nm": 240, "gaps": 0,
                                                          "gap_list": [[10, 22]], "spacing_nm": 8}}),
        "areas": [],
        "aircraft": [
            ac("AFR71", "A320", ["W1", "E1"], 350, None, entry=0, overflight=True),
            ac("RAM44", "B738", ["W2", "E2"], 350, None, entry=80),
            ac("UAE61", "A388", ["E1", "W2"], 370, None, entry=60, load=0.4),
            ac("ETH5", "B788", ["E2", "W1"], 380, None, entry=160),
        ],
        "requests": [],
        "expected": {"naive_approve": {"los": True, "pairs": [["AFR71", "RAM44"]], "wx_request": "AFR71"},
                     "passive": {"wx_request": "RAM44"}},
        "solution": [{"on_request": cs, "type": "weather_deviation", "do": [{"reply": "approved"}]}
                     for cs in ("AFR71", "UAE61", "ETH5")] + [
                    {"on_request": "RAM44", "type": "weather_deviation",
                     "do": [{"reply": "approved"}, {"callsign": "RAM44", "type": "altitude", "fl": 330}]}],
    }


TURB_JET = wx(WEST_JET)
TURB_JET["wind"]["jet"] = {"core_fl": 390, "dir": 272, "kt": 105, "axis_point": [0, 0], "half_width_nm": 110,
                           "depth_below_ft": 4000, "depth_above_ft": 9000, "streak_length_nm": 0,
                           "meander_amp_nm": 8, "meander_wavelength_nm": 500}


@scenario
def s13():
    return {
        "name": "s13_turbulence_level_request",
        "title": "Clear-air turbulence below a jet core: ride request into traffic",
        "description": "A strong, narrow jet (core FL390, about 155 kt) crosses the sector west to east. The steep "
                       "shear below the core gives patchy moderate to severe clear-air turbulence at FL340-FL370. "
                       "No precipitation.",
        "design_notes": "KLM59 (B789, FL370, W1->E1, southbound) runs into continuous moderate/severe CAT and asks "
                        "for FL390 (the next southbound level, smooth near the core). Climbing a heavy B789 2000 ft "
                        "takes ~4 minutes, and DAH2 (A332, FL390, N4->S3) crosses its path near the top of the climb: "
                        "approving FL390 straight away loses separation. Leaving KLM59 in the turbulence is safe for "
                        "separation but bad service (the turbulence metrics count it). Correct: 'unable, traffic, "
                        "expect FL390 in 6 minutes', then FL390 once DAH2 has passed (t~580), or turn DAH2.",
        "duration_s": 1500, "pilot_delay_s": 4,
        "weather": TURB_JET,
        "areas": [],
        "aircraft": [
            ac("KLM59", "B789", ["W1", "E1"], 370, None, entry=0, overflight=True),
            ac("SAS9", "A20N", ["W2", "E2"], 330, None, entry=40),
            ac("DAH2", "A332", ["N4", "S3"], 390, None, at=[35, 20], t=560),
        ],
        "requests": [],
        "expected": {"naive_approve": {"los": True, "pairs": [["DAH2", "KLM59"]], "wx_request": "KLM59"},
                     "passive": {"los": False, "wx_request": "KLM59"}},
        "solution": [{"on_request": "KLM59", "type": "altitude", "do": [{"reply": "unable"}]},
                     {"at": 580, "do": [{"callsign": "KLM59", "type": "altitude", "fl": 390}]}],
    }

@scenario
def s14():
    cells = [{"id": f"CB{i + 1}", "x": x, "y": y, "at_time": 400, "top_fl": 350, "dbz": 52, "radius_nm": 3.5,
              "lifetime_s": 6000, "age_s": 1800, "motion": {"toward": 60, "kt": 18}}
             for i, (x, y) in enumerate([(-24, 4), (-12, 0), (0, -3), (12, -6), (24, -9)])]
    scn = {
        "name": "s14_climb_over_tops",
        "title": "Request to climb over a line of storms",
        "description": "A broken line of moderate thunderstorms (tops around FL350) lies WSW-ENE across the "
                       "middle of the sector, drifting north-east.",
        "design_notes": "RAM212 (A20N, FL340, S2->N3, northbound) would have to go far around the line, so it asks "
                        "to climb about 3000 ft above the radar tops: FL380 (an even level). AEA38 (B738, FL380, "
                        "E2->W2) crosses its path there: approving the requested climb loses separation. Correct: "
                        "unable the climb and approve a lateral deviation, or climb RAM212 through to FL400 early "
                        "enough to be above AEA38 before it arrives (check it with /probe), or turn AEA38.",
        "duration_s": 1500, "pilot_delay_s": 4,
        "weather": wx(CONVECTIVE_SW, cells=cells),
        "areas": [],
        "aircraft": [
            ac("RAM212", "A20N", ["S2", "N3"], 340, None, entry=0),
            ac("AEA38", "B738", ["E2", "W2"], 380, None, entry=0),
        ],
        "requests": [],
        "expected": {"naive_approve": {"los": True, "pairs": [["AEA38", "RAM212"]], "wx_request": "RAM212"},
                     "passive": {"los": False, "wx_request": "RAM212"}},
        "solution": [{"on_request": "RAM212", "type": "altitude",
                      "do": [{"reply": "unable"}, {"callsign": "RAM212", "type": "deviation", "side": "either"}]}],
    }
    pt, t = meet(scn, "RAM212", ["E2", "W2"], after=0)
    return put(scn, "AEA38", at=pt, t=t)

@scenario
def s15():
    return {
        "name": "s15_convective_afternoon",
        "title": "Convective afternoon - open problem",
        "description": "Scattered strong thunderstorms (multicell clusters, tops FL360-450) developing and decaying "
                       "across the sector, south-westerly flow aloft, twelve flights. Pilots ask for deviations "
                       "as they need them.",
        "design_notes": "No single scripted trap: the conflicts come from where the cells happen to be when each "
                        "flight arrives, from deviations pushing aircraft into each other, and from cells growing "
                        "on the far side of a deviation. For evaluating general weather handling (LoS, weather "
                        "penetration, deviations without clearance, extra track miles, response times).",
        "duration_s": 2100, "pilot_delay_s": 4,
        "weather": wx(CONVECTIVE_SW, seed=41, convection={"regime": "scattered", "intensity": "strong", "count": 9,
                                                          "region": [-70, -70, 70, 70]}),
        "areas": [{"id": "R71", "name": "Military training", "kind": "restricted",
                   "polygon": [[25, 30], [50, 30], [50, 52], [25, 52]], "lower_fl": 0, "upper_fl": 450,
                   "active": True}],
        "aircraft": [
            ac("RAM301", "B738", ["N1", "S3"], 370, None, entry=0),
            ac("AFR77", "A320", ["W1", "E2"], 350, None, entry=60),
            ac("DLH2T", "A21N", ["E1", "W2"], 370, None, entry=90, load=0.35),
            ac("IBE12", "A20N", ["S2", "N4"], 340, None, entry=150),
            ac("EZY8K", "A319", ["N4", "S1"], 390, None, entry=220),
            ac("UAE8", "B77W", ["W2", "E1"], 340, None, entry=280, load=0.4),
            ac("TAP15", "A332", ["S4", "N2"], 400, None, entry=330, load=0.35),
            ac("RYR4", "B38M", ["N2", "S4"], 350, None, entry=420),
            ac("KLM3", "B789", ["E2", "W1"], 360, None, entry=500, load=0.35),
            ac("SVA9", "A333", ["S1", "N3"], 380, None, entry=580, load=0.35),
            ac("CNTKB", "C550", ["W1", "E1"], 410, None, entry=650),
            ac("JAF4", "B738", ["S3", "N1"], 340, None, entry=700),
        ],
        "requests": [],
        "expected": {"naive_approve": {"allow_left_sector": True}, "passive": {"allow_left_sector": True}},
    }


def _aerodromes(e_rwy, s_rwy, s_schedule=None):
    from atcsim import arrivals
    ads = json.loads(json.dumps(arrivals.DEFAULT_AERODROMES))
    ads[0]["runway_in_use"], ads[1]["runway_in_use"] = e_rwy, s_rwy
    if s_schedule:
        ads[1]["runway_schedule"] = s_schedule
    return ads


@scenario
def s16():
    with open(os.path.join(HERE, "s16_solution.json")) as f:
        sol = json.load(f)
    return {
        "name": "s16_e1_arrival_sequencing",
        "title": "Five arrivals converging on E1",
        "description": "Five arrivals for EFLD (runway 09, straight-in) converge on E1 from W1, W2, N3 and N4 at "
                       "FL340-FL390. Letter of agreement: level FL250 at E1, 10 NM in trail. One westbound "
                       "overflight at FL360.",
        "design_notes": "The arrivals reach E1 only 6-8 NM apart if nothing is done, and all of them are high (the "
                        "close aerodrome gives early tops of descent, 50-60 NM before E1). Approving every descent "
                        "request as it comes puts them all at FL250 at E1 at the same time: spacing violations and "
                        "a loss of separation (EZY91 / IBE77, 0.4 NM). Leaving them high violates the LoA level. "
                        "Correct: decide the order early, delay followers with vectors and Mach / conversion IAS "
                        "restrictions, and give each its descent so it is level FL250 by E1.",
        "duration_s": 1800, "pilot_delay_s": 4, "weather": WEST_JET,
        "aerodromes": _aerodromes("09", "18"),
        "areas": [],
        "aircraft": [
            ac("AFR318", "A320", ["N4", "E1"], 390, None, at="E1", t=700),
            ac("TAP210", "E190", ["N3", "E1"], 370, None, at="E1", t=745),
            ac("RAM960", "B738", ["W1", "E1"], 350, None, at="E1", t=790),
            ac("IBE77", "A20N", ["W2", "E1"], 340, None, at="E1", t=840),
            ac("EZY91", "A319", ["W1", "E1"], 370, None, at="E1", t=880),
            ac("DLH3", "A21N", ["E2", "W1"], 360, None, entry=100, load=0.35),
        ],
        "requests": [],
        "expected": {"passive": {"seq_level": True, "seq_spacing": True},
                     "naive_approve": {"los": True, "seq_spacing": True}},
        "solution": sol,
    }


@scenario
def s17():
    return {
        "name": "s17_s4_runway_change",
        "title": "S4 arrivals and a runway change at the destination",
        "description": "Four arrivals for SPRT via S4. SPRT lands runway 18 (straight-in from S4); at T+08:00 it "
                       "changes to runway 36, which needs an overhead and reversal procedure. Letter of agreement: "
                       "level FL250 at S4, 10 NM in trail.",
        "design_notes": "With runway 18 the pilots ask for descent 30-60 NM before S4 and can make FL250. After the "
                        "change to 36 their computed top of descent moves ~30 NM later, behind the point where they "
                        "can still be level FL250 at S4: KLM81 asks only when it is already too late (crosses S4 at "
                        "~FL290 even if approved at once). Correct: watch the latest-descent point, not the pilot's "
                        "request, and start the descent yourself for the late flights.",
        "duration_s": 1500, "pilot_delay_s": 4, "weather": NORTHERLY,
        "aerodromes": _aerodromes("09", "18", [{"time": 480, "runway": "36"}]),
        "areas": [],
        "aircraft": [
            ac("RAM508", "B738", ["N1", "S4"], 350, None, at="S4", t=520),
            ac("BAW3J", "B772", ["N2", "S4"], 370, None, at="S4", t=640, load=0.35),
            ac("KLM81", "B738", ["N3", "S4"], 350, None, at="S4", t=900),
            ac("UAE57", "A388", ["N4", "S4"], 390, None, at="S4", t=1060, load=0.35),
        ],
        "requests": [],
        "expected": {"passive": {"seq_level": True}, "naive_approve": {"seq_level": True}},
        "solution": [{"on_request": cs, "type": "altitude", "do": [{"reply": "approved"}]}
                     for cs in ("RAM508", "BAW3J", "UAE57")] +
                    [{"at": 600, "do": [{"callsign": "KLM81", "type": "altitude", "fl": 250}]}],
    }





# =============================================================================================
# Agadir CTA - dense traffic near sector capacity, weather always present, planted conflicts
#   capacities (flights/hour): Oceanic North 56, Oceanic South 65, West North 48, West South 52
#   w*: west sectors (WN+WS) as one   o*: oceanic sectors (ON+OS) as one   c*: the whole CTA
# Background traffic: tools/agadir_traffic.py (published routes only, levels by the semicircular rule,
# de-conflicted so that the conflicts left are the planted ones). Cached in tools/traffic/<name>.json.
# =============================================================================================
import hashlib  # noqa: E402

import agadir_traffic as AT  # noqa: E402

AG_RATES = dict(un857=5, un857_gcrr=2, runax_std=4, runax_gcrr=1, un858=4, desum_akuda=3, takav_vedod=5, takav_domog=2,
                gmmn_dep_amsel=1, gmmn_dep_oslad=1, salca_arr=1, marja_arr=2, un866=8, un873=10, samar_bexal=9,
                samar_tiggi=6, runax_samar=8, vasto_barok=4, vasto_tiggi=4, nipri_rolro=3, salca_nat=1,
                marja_south=1, konba_bexal=4)
TRAFFIC_DIR = os.path.join(HERE, "traffic")
UN857, UN858, UN873, UN866, STD = AT.UN857, AT.UN858, AT.UN873, AT.UN866, AT.STD_RUNAX
DESUM_AKUDA = AT.DESUM_AKUDA


def _aw(): from atcsim import airspace as m; return m.get("agadir")  # noqa: E704


def on_route(route, pt):
    """Nearest point to pt on a route polyline (Agadir waypoints)."""
    from atcsim.geometry import project_on_polyline, point_along_polyline
    w = _aw().waypoints
    pts = [w[n] for n in route]
    along, _, _ = project_on_polyline(pts, tuple(pt))
    p, _ = point_along_polyline(pts, along)
    return [round(p[0], 3), round(p[1], 3)]


def along_route(route, fix, nm_before):
    """Point nm_before NM before `fix` along a route."""
    from atcsim.geometry import point_along_polyline, polyline_length
    w = _aw().waypoints
    pts = [w[n] for n in route[:route.index(fix) + 1]]
    p, _ = point_along_polyline(pts, polyline_length(pts) - nm_before)
    return [round(p[0], 3), round(p[1], 3)]


def when_alt(scn, cs, alt_ft, policy="naive_approve", after=0.0):
    """(time, [x, y]) when `cs`, flown alone under `policy`, passes alt_ft."""
    s2 = json.loads(json.dumps(scn))
    s2["aircraft"] = [a for a in s2["aircraft"] if a["callsign"] == cs]
    s2["requests"] = [r for r in s2.get("requests", []) if r["callsign"] == cs]
    s2["events"] = [e for e in s2.get("events", []) if e["callsign"] == cs]
    pts = run(s2, policy, trace=True)["trace"][cs]
    for (t0, x0, y0, a0, *_), (t1, x1, y1, a1, *_) in zip(pts, pts[1:]):
        if t1 >= after and (a0 - alt_ft) * (a1 - alt_ft) <= 0 and a0 != a1:
            return round(t1), [round(x1, 3), round(y1, 3)]
    raise AssertionError(f"{cs} never passes {alt_ft} ft")


def meet_alt(scn, cs, legs, policy="naive_approve", after=0.0):
    """Where/when/at what altitude `cs`, flown alone, first crosses one of the route legs (pairs of points)."""
    s2 = json.loads(json.dumps(scn))
    s2["aircraft"] = [a for a in s2["aircraft"] if a["callsign"] == cs]
    s2["requests"] = [r for r in s2.get("requests", []) if r["callsign"] == cs]
    s2["events"] = []
    pts = run(s2, policy, trace=True)["trace"][cs]
    w = _aw().waypoints
    for (t0, x0, y0, a0, *_), (t1, x1, y1, a1, *_) in zip(pts, pts[1:]):
        if t1 < after:
            continue
        for a, b in legs:
            p = segment_intersection((x0, y0), (x1, y1), w[a], w[b])
            if p:
                return [round(p[0], 3), round(p[1], 3)], round(t1), a1
    raise AssertionError(f"{cs} never crosses {legs}")


def parity_level(alt_ft, southbound):
    from atcsim.aircraft import level_parity_ok
    c = [f for f in range(250, 420, 10) if level_parity_ok(f, southbound)]
    return min(c, key=lambda f: abs(f * 100 - alt_ft))


def set_fl(scn, cs, fl):
    for a in scn["aircraft"]:
        if a["callsign"] == cs:
            a["fl"] = fl
    return scn


def dense(scn, flows, seed, regen=False):
    """Add cached (or freshly generated + de-conflicted) background traffic to a scenario with planted flights."""
    planted = scn["aircraft"]
    keep = {a["callsign"] for a in planted}
    sig = hashlib.md5(json.dumps([planted, scn.get("requests", []), scn.get("events", []), scn.get("control"),
                                  scn["weather"], scn.get("areas", []), flows, seed, AG_RATES,
                                  {k: AT.FLOWS[k] for k in flows}, {"sep_h": 7}],
                                 sort_keys=True, default=str).encode()).hexdigest()[:12]
    path = os.path.join(TRAFFIC_DIR, scn["name"] + ".json")
    if os.path.exists(path) and not regen:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("signature") == sig:
            scn["aircraft"] = planted + cached["aircraft"]
            scn["requests"] = scn.get("requests", []) + cached["requests"]
            return scn
    print(f"  generating background traffic for {scn['name']} ...")
    bg = AT.generate(seed, flows, used=keep, rates=AG_RATES)
    scn["aircraft"] = planted + bg
    base_req = list(scn.get("requests", []))
    AT.prune(scn)
    AT.departure_requests(scn, seed=seed)
    AT.deconflict(scn, keep=keep, rounds=5)
    os.makedirs(TRAFFIC_DIR, exist_ok=True)
    bg = [a for a in scn["aircraft"] if a["callsign"] not in keep]
    with open(path, "w") as f:
        json.dump({"signature": sig, "aircraft": bg,
                   "requests": [r for r in scn["requests"] if r not in base_req]}, f)
    return scn


def ag(name, **kw):
    d = {"name": name, "airspace": "agadir", "duration_s": 3600, "pilot_delay_s": 4, "areas": [], "requests": [],
         "events": []}
    d.update(kw)
    return d


def _jet(seed, axis=(0, 120)):
    j = wx(WEST_JET, seed=seed)
    j["wind"]["jet"]["axis_point"] = list(axis)
    return j


WX_W_MOD = wx(CONVECTIVE_SW, seed=101, convection={"regime": "scattered", "intensity": "strong", "count": 8,
                                                   "region": [-40, -120, 60, -40]})
WX_W_SEV = wx(CONVECTIVE_SW, seed=102, convection={"regime": "squall_line", "intensity": "severe",
                                                   "line": {"center": [-20, -95], "length_nm": 170, "orient_deg": 120,
                                                            "gaps": 0, "gap_list": [[-35, 22], [40, 20]],
                                                            "spacing_nm": 8}})
WX_O_LIGHT = wx(_jet(103), convection={"regime": "isolated", "intensity": "moderate", "count": 5,
                                        "region": [-170, -60, -80, 60]})
WX_O_EMB = wx(CONVECTIVE_SW, seed=104, convection={"regime": "embedded", "intensity": "strong", "count": 8,
                                                   "region": [-185, -110, -100, -20]})
WX_C_MOD = wx(CONVECTIVE_SW, seed=105, convection={"regime": "scattered", "intensity": "strong", "count": 12,
                                                   "region": [-150, -130, 40, 20]})
WX_C_SEV = wx(CONVECTIVE_SW, seed=106, convection={"regime": "squall_line", "intensity": "severe",
                                                   "line": {"center": [-60, -70], "length_nm": 230, "orient_deg": 125,
                                                            "gaps": 0, "gap_list": [[-60, 22], [10, 20], [75, 22]],
                                                            "spacing_nm": 8}})


@scenario
def w01():
    scn = ag(
        "w01_west_scattered_cb",
        title="West sectors, scattered thunderstorms, IBALU and VEDOD merges",
        description="West North + West South band-boxed, at about sector capacity. Scattered strong "
                    "thunderstorms (tops FL380-450) over the middle of West South. Southbound Europe-Canaries "
                    "flows (UN857, RUNAX-IBALU-TERTO standard route, TAKAV-VEDOD-TERTO), northbound Canaries-Europe "
                    "flows (UN858 from VASTO, DESUM-SULAM-UN858, VASTO-BAROK, VASTO-NIDEB-TIGGI), Casablanca arrivals "
                    "and departures, GCRR arrivals via TERTO (LoA FL250, 10 NM).",
        control=["WN", "WS"], weather=WX_W_MOD,
        aircraft=[
            ac("EWG7TB", "A320", STD, 350, None, at="IBALU", t=900, dep="EDDK", dest="GCFV", overflight=True),
            ac("IBE3215", "A21N", UN857, 350, None, at="IBALU", t=918, dep="LEMD", dest="GCLP", overflight=True),
            ac("RAM205", "B789", ["BEXAL", "IBALU", "SALCA"], 370, None, at=AT_BEXAL_UN858, t=700, dep="CYUL",
               dest="GMMN", load=0.4),
            ac("BEL5AT", "A332", UN858, 360, None, at=AT_BEXAL_UN858, t=700, dep="GOBD", dest="EBBR", load=0.4),
            ac("RYR81DQ", "B38M", UN858, 340, None, at="IBLIV", t=1800, dep="GCTS", dest="EGSS"),
            ac("EZY64LK", "A20N", UN858, 340, None, at="IBLIV", t=1870, dep="GCTS", dest="EGKK"),
            ac("RAM1307", "B738", ["NIPRI", "NIDEB", "ROLRO"], 350, None, at=AT_ROLRO_TKV, t=1300, dep="LPAZ",
               dest="GMAD"),
            ac("TAP1462", "A20N", ["TAKAV", "VEDOD", "TERTO"], 350, None, at=AT_ROLRO_TKV, t=1300, dep="LPPT",
               dest="GCXO"),
            ac("EXS82H", "B738", UN857, 370, None, at="VEDOD", t=2500, dep="EGCC", dest="GCRR"),
            ac("TOM5LX", "B38M", ["TAKAV", "VEDOD", "TERTO"], 370, None, at="VEDOD", t=2525, dep="EGGW",
               dest="GCFV", overflight=True),
            ac("RAM1402", "B738", ["MARJA", "ABODA", "AMSEL"], 260, None, at="MARJA", t=1500, dep="GMMN",
               dest="LPPT"),
            ac("CFG4MT", "A321", STD, 330, None, entry=0, dep="EDDF", dest="GCTS", load=0.4),
        ],
        requests=[req(1300, "RYR81DQ", "speed", 0.74, "early arrival, holding expected"),
                  req(1440, "RAM1402", "altitude", 380, "requesting cruise level"),
                  req(560, "RAM205", "altitude", 250, "descent for Casablanca")],
        design_notes="Planted conflicts (passive): EWG7TB (standard route) and IBE3215 (UN857) merge at IBALU at "
                     "FL350 18 s apart; RAM1307 (NIPRI-NIDEB-ROLRO) crosses TAP1462 on TAKAV-VEDOD at FL350 soon "
                     "after entering at NIDEB; EXS82H (UN857, GCRR arrival) and TOM5LX (TAKAV-VEDOD) merge at VEDOD at "
                     "FL370 25 s apart. Request traps (naive): RAM205 (SALCA arrival from BEXAL, FL370) asks descent "
                     "and would descend through BEL5AT (northbound on UN858) where the tracks cross; RYR81DQ "
                     "(northbound UN858, FL340) asks Mach .74 with EZY64LK 9 NM behind at the same level; GMMN departure RAM1402 asks FL380 "
                     "climbing through CFG4MT (RUNAX standard route, level set to the one RAM1402 climbs through there) at the "
                     "MARJA-ABODA crossing. Weather "
                     "deviations around the West South cells add the rest.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["EWG7TB", "IBE3215"],
                                                     ["RAM1307", "TAP1462"], ["EXS82H", "TOM5LX"]]},
                  "naive_approve": {"allow_left_sector": True, "los": True, "pairs": [
                      ["EZY64LK", "RYR81DQ"], ["CFG4MT", "RAM1402"], ["BEL5AT", "RAM205"]]}},
    )
    p, t, alt = meet_alt(scn, "RAM1402", [("RUNAX", "IBALU")], after=1440)
    scn = set_fl(put(scn, "CFG4MT", at=p, t=t), "CFG4MT", parity_level(alt, True))
    p, t, alt = meet_alt(scn, "RAM205", [("SUNID", "AKUDA")], after=560)
    scn = set_fl(put(scn, "BEL5AT", at=p, t=t), "BEL5AT", parity_level(alt, False))
    return dense(scn, AT.WEST_FLOWS, seed=11)


@scenario
def w02():
    scn = ag(
        "w02_west_squall_line",
        title="West sectors, severe squall line, unable RVSM, formation, arrival sequences",
        description="West North + West South band-boxed, near capacity. A severe squall line (tops FL450-500) "
                    "lies across West South with two gaps, drifting north-east. GCRR arrivals via TERTO and GMMN "
                    "arrivals via MARJA (LoA FL250, 10 NM in trail). A Moroccan F-16 four-ship leaves ROLRO for "
                    "the oceanic area via NIDEB.",
        control=["WN", "WS"], weather=WX_W_SEV,
        aircraft=[
            ac("RYR4WJ", "B738", UN858, 380, None, at=AT_BEXAL_UN858, t=1300, dep="GVAC", dest="EGSS"),
            ac("RAM1201", "B738", ["BEXAL", "IBALU", "SALCA"], 370, None, at=AT_BEXAL_UN858, t=1300, dep="KJFK",
               dest="GMMX"),
            ac("N215CJ", "C550", ["VASTO", "BAROK"], 280, None, entry=200, dep="GCLP", dest="LPPT", rvsm=False,
               equipment="SDFGRY/S", remarks="FERRY FLT NON-RVSM"),
            ac("VIPER21", "F16", ["NIPRI", "NIDEB", "ROLRO"], 310, None, at=AT_ROLRO_UN858, t=330, dep="GMSL",
               dest="GMSL", formation={"size": 4, "types": "4xF16", "extent_nm": 1.0}, remarks="STS/STATE"),
            # early, so that SWR2NK is already north of the squall line at T+0
            ac("SWR2NK", "A320", UN858, 320, None, at=AT_ROLRO_UN858, t=330, dep="GCTS", dest="LSZH"),
            ac("EXS91T", "B738", UN857, 370, None, at="TERTO", t=2400, dep="EGNM", dest="GCRR"),
            ac("TOM62R", "B38M", ["TAKAV", "VEDOD", "TERTO"], 350, None, at="TERTO", t=2450, dep="EGKK",
               dest="GCRR"),
            ac("EWG95L", "A20N", STD, 390, None, at="TERTO", t=2500, dep="EDDL", dest="GCRR"),
            ac("RAM765", "B738", ["AMSEL", "ABODA", "MARJA"], 310, None, at="MARJA", t=1900, dep="LFPG",
               dest="GMMN"),
            ac("RAM771", "E190", ["AMSEL", "ABODA", "MARJA"], 290, None, at="MARJA", t=1945, dep="LPPT",
               dest="GMMN"),
        ],
        requests=[req(700, "N215CJ", "altitude", 400, "fuel, ferry flight")],
        events=[{"time": 900, "callsign": "RYR4WJ", "type": "rvsm_unable", "reason": "altimeter disagreement"}],
        design_notes="RYR4WJ (northbound UN858, FL380) reports unable RVSM at T+15:00; at T+21:40 it crosses "
                     "RAM1201 (BEXAL-IBALU-SALCA to Marrakech, FL370) in West North: legal with RVSM, a loss of separation without "
                     "(2000 ft needed). N215CJ (C550 ferry, not RVSM approved) asks FL400: approving it is "
                     "an RVSM airspace violation. VIPER21 (4xF16, NIPRI-NIDEB-ROLRO at FL310, non-RVSM formation, 2000 ft "
                     "from everyone) crosses SWR2NK northbound on UN858 at FL320. Three GCRR arrivals reach TERTO 50 s apart "
                     "(FL350-390) and two GMMN arrivals reach MARJA 45 s apart: spacing and LoA level. The squall "
                     "line funnels the West South traffic through two gaps.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["RAM1201", "RYR4WJ"], ["SWR2NK", "VIPER21"]],
                              "seq_level": True},
                  "naive_approve": {"allow_left_sector": True, "rvsm_violation": True}},
    )
    return dense(scn, AT.WEST_FLOWS, seed=12)


@scenario
def o01():
    w = _aw().waypoints
    tvf_pt = along_route(UN873, "SAMAR", 20)
    scn = ag(
        "o01_oceanic_light_wx",
        title="Oceanic sectors, isolated storms, merges at TIGGI and SAMAR",
        description="Oceanic North + Oceanic South band-boxed, near capacity: southbound Europe-South America and "
                    "Canaries traffic on UN873, UN866 and RUNAX-IBALU-SAMAR, northbound South America / Canaries "
                    "traffic on the SAMAR-BEXAL, SAMAR-TIGGI, VASTO-BAROK and VASTO-NIDEB-TIGGI RAD routings, "
                    "NIPRI-NIDEB-ROLRO traffic from the Azores. A few isolated moderate storms in Oceanic North.",
        control=["ON", "OS"], weather=WX_O_LIGHT,
        aircraft=[
            ac("AZU8711", "A332", ["SAMAR", "TIGGI"], 380, None, at="TIGGI", t=2200, dep="SBKP", dest="LPPT", load=0.4),
            ac("VLG6703", "A320", ["VASTO", "NIDEB", "TIGGI"], 380, None, at="TIGGI", t=2222, dep="GCTS", dest="LEBL",
               load=0.35),
            ac("IBE6011", "A333", UN873, 350, None, at="SAMAR", t=1600, dep="LEMD", dest="SBGR", load=0.45),
            ac("DLH518", "B748", ["RUNAX", "IBALU", "SAMAR"], 350, None, at="SAMAR", t=1615, dep="EDDF", dest="SBGR",
               load=0.4),
            ac("AFR418", "A332", UN866, 370, 0.80, at="KUBIL", t=900, dep="LFPG", dest="SBGL", load=0.45),
            ac("TAM8065", "B77W", UN866, 370, 0.86, at="KUBIL", t=990, dep="LFPG", dest="SBGR", load=0.4),
            ac("TAP104", "A332", ["SAMAR", "BEXAL"], 340, None, at="BEXAL", t=1300, dep="SBGL", dest="LPPT", load=0.4),
            ac("KLM793", "B789", UN866, 350, None, entry=0, dep="EHAM", dest="SBGR", load=0.4),
            ac("RZO131", "A321", ["NIPRI", "NIDEB", "ROLRO"], 330, None, at=AT_NIPRI_UN873, t=2500, dep="LPPD",
               dest="GMMX"),
            ac("VLG7831", "A320", UN873, 330, None, at=AT_NIPRI_UN873, t=2500, dep="LEBL", dest="GCLA"),
            ac("TVF7724", "B738", UN873, 370, None, at=tvf_pt, t=2300, dep="LFPO", dest="GVAC"),
            ac("AVA10", "B788", ["SAMAR", "TIGGI"], 380, None, at=on_route(["SAMAR", "TIGGI"], tvf_pt), t=2300,
               dep="SKBO", dest="LEMD"),
        ],
        requests=[req(1230, "TAP104", "altitude", 380, "optimum level")],
        events=[{"time": 1800, "callsign": "TVF7724", "type": "rvsm_unable", "reason": "loss of both autopilots"}],
        design_notes="Planted: AZU8711 (SAMAR-TIGGI) and VLG6703 (VASTO-NIDEB-TIGGI) merge at TIGGI at FL380 22 s "
                     "apart; IBE6011 (UN873) and DLH518 (RUNAX-IBALU-SAMAR) merge at SAMAR at FL350; TAM8065 (M.86) "
                     "overtakes AFR418 (M.80) on UN866 at FL370, 12 NM behind at KUBIL (a sleeper: the loss comes "
                     "about 15 minutes later); RZO131 (NIPRI-NIDEB-ROLRO) crosses VLG7831 on UN873 at FL330; TVF7724 "
                     "(UN873, FL370) reports unable RVSM at T+30:00 and passes AVA10 (northbound FL380) 2-3 NM "
                     "abeam 20 NM before SAMAR. Request trap: TAP104 (northbound, FL340) asks FL380 when KLM793 is "
                     "leaving BEXAL southbound at FL350 on the same line.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["AZU8711", "VLG6703"], ["DLH518", "IBE6011"],
                                                     ["AFR418", "TAM8065"], ["RZO131", "VLG7831"],
                                                     ["AVA10", "TVF7724"]]},
                  "naive_approve": {"allow_left_sector": True, "los": True, "pairs": [["KLM793", "TAP104"]]}},
    )
    t, p = when_alt(scn, "TAP104", 35000, after=1230)
    scn = put(scn, "KLM793", at=on_route(UN866, p), t=t)
    return dense(scn, AT.OCEANIC_FLOWS, seed=21)


@scenario
def o02():
    scn = ag(
        "o02_oceanic_embedded_cb",
        title="Oceanic sectors, embedded thunderstorms, exercise area, fighter formation",
        description="Oceanic North + Oceanic South band-boxed, near capacity. Embedded thunderstorms (hidden in "
                    "stratiform rain) over Oceanic South around MITLA and OSDIV-KONBA. Restricted area R90 "
                    "(military exercise, surface-FL450) activates at T+15:00 across UN866 between OSDIV and KONBA. "
                    "A Spanish Typhoon pair transits south on UN866.",
        control=["ON", "OS"], weather=WX_O_EMB,
        areas=[{"id": "R90", "name": "Naval exercise", "kind": "restricted",
                "polygon": ["320500N 0145000W", "322500N 0141500W", "315000N 0135500W", "313000N 0143000W"],
                "lower_fl": 0, "upper_fl": 450, "active": False,
                "schedule": [{"time": 900, "active": True}, {"time": 3300, "active": False}]}],
        aircraft=[
            ac("FALCO41", "EUFI", UN866, 310, None, at="BEXAL", t=1400, dep="LEMO", dest="GCLA",
               formation={"size": 2, "types": "2xEUFI", "extent_nm": 1.0}, remarks="STS/STATE"),
            ac("GLO7720", "B738", ["SAMAR", "BEXAL"], 320, None, at="BEXAL", t=1400, dep="GCLP", dest="LPPT"),
            ac("N650GA", "C550", ["SAMAR", "BEXAL"], 280, None, entry=300, dep="GCLP", dest="LPPT", rvsm=False,
               equipment="SDFGRY/S", remarks="FERRY FLT NON-RVSM"),
            ac("AZU8743", "A332", ["SAMAR", "TIGGI"], 360, None, at="TIGGI", t=2600, dep="SBRF", dest="LPPT",
               load=0.4),
            ac("IBE6041", "A333", ["SAMAR", "TIGGI"], 360, None, at="TIGGI", t=2660, dep="SBGR", dest="LEMD",
               load=0.4),
            ac("KLM705", "B77W", UN866, 390, None, at="OSDIV", t=1500, dep="EHAM", dest="SAEZ", load=0.35),
        ],
        requests=[req(600, "N650GA", "altitude", 380, "fuel, ferry flight"),
                  req(1500, "AZU8743", "speed", 0.76, "fuel economy")],
        design_notes="R90 activates at T+15:00 across UN866: KLM705 and the UN866 stream need a reroute (passive = "
                     "infringement). FALCO41 (2xEUFI formation, FL310, non-RVSM) enters at BEXAL as GLO7720 "
                     "(northbound FL320) leaves it: 1000 ft is not enough against a formation. N650GA (C550 ferry, "
                     "not RVSM approved) asks FL380. AZU8743 asks Mach .76 with IBE6041 8 NM behind at FL360 on "
                     "SAMAR-TIGGI. The embedded cells are hard to see on the composite: use the CAPPI at flight "
                     "level.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["FALCO41", "GLO7720"]], "infringement": True},
                  "naive_approve": {"allow_left_sector": True, "rvsm_violation": True, "infringement": True,
                                    "los": True, "pairs": [["AZU8743", "IBE6041"]]}},
    )
    return dense(scn, AT.OCEANIC_FLOWS, seed=22)


@scenario
def c01():
    w = _aw().waypoints
    scn = ag(
        "c01_cta_moderate_wx",
        title="Whole Agadir CTA, scattered storms, cross-sector conflicts",
        description="All four Agadir sectors band-boxed, near capacity. Scattered strong thunderstorms over West "
                    "South and Oceanic South. Arrivals: GCRR via TERTO, GMMN via MARJA and SALCA (LoA FL250, "
                    "10 NM in trail).",
        weather=WX_C_MOD,
        aircraft=[
            ac("SWR34E", "A21N", ["RUNAX", "IBALU", "SAMAR"], 370, None, at=AT_SAMAR_UN858, t=1500, dep="LSZH",
               dest="GCTS", load=0.4),
            ac("BTI8PQ", "A320", UN858, 360, None, at=AT_SAMAR_UN858, t=1500, dep="GCLP", dest="EBBR"),
            ac("AZU8721", "A332", ["SAMAR", "TIGGI"], 400, None, at="TIGGI", t=2700, dep="SBKP", dest="LPPT",
               load=0.35),
            ac("IBS3961", "A321", ["VASTO", "NIDEB", "TIGGI"], 400, None, at="TIGGI", t=2716, dep="GCLP",
               dest="LEMD", load=0.3),
            ac("RAM209", "B788", ["SUBAL", "IBALU", "SALCA"], 350, None, at=AT_SUBAL_UN858, t=1100, dep="KMIA",
               dest="GMMN"),
            ac("TVF34LP", "B738", UN858, 340, None, at=AT_SUBAL_UN858, t=1100, dep="GCFV", dest="LFPO"),
            ac("EXS77P", "B738", UN857, 350, None, at="VEDOD", t=2900, dep="EGPH", dest="GCRR"),
            ac("RYR2ZC", "B738", ["TAKAV", "VEDOD", "TERTO"], 350, None, at="VEDOD", t=2930, dep="EIDW",
               dest="GCRR"),
            ac("TAP102", "A333", ["SAMAR", "BEXAL"], 340, None, at="BEXAL", t=1900,
               dep="SBGR", dest="LPPT", load=0.4),
            ac("LAN706", "B789", UN866, 350, None, entry=0, dep="LEMD", dest="SCEL", load=0.4),
            ac("RAM1408", "B738", ["NISBA", "IBALU", "OSLAD"], 260, None, at="NISBA", t=800, dep="GMMN",
               dest="LPPT"),
            ac("EWG4UA", "A320", STD, 330, None, entry=0, dep="EDDK", dest="GCLP"),
        ],
        requests=[req(1830, "TAP102", "altitude", 380, "optimum level"),
                  req(820, "RAM1408", "altitude", 360, "requesting cruise level"),
                  req(1465, "SWR34E", "altitude", 350, "moderate turbulence at FL370"),
                  req(900, "RAM209", "altitude", 250, "descent for Casablanca")],
        design_notes="Planted: AZU8721 and IBS3961 merge at TIGGI at FL400 16 s apart. Request traps: SWR34E "
                     "(RUNAX-IBALU-SAMAR, FL370) asks FL350 just before crossing BTI8PQ (northbound UN858, FL360) "
                     "in West South; RAM209 (SALCA arrival from SUBAL, FL350) asks descent and would go down "
                     "through TVF34LP (northbound UN858) where the tracks cross in West North; EXS77P (UN857) and "
                     "RYR2ZC (TAKAV-VEDOD), both "
                     "GCRR arrivals, merge at VEDOD at FL350 30 s apart and then need FL250 at TERTO 10 NM apart (left "
                     "alone, the storms near VEDOD can make one of them deviate first). "
                     "Request traps: TAP102 (northbound FL340) asks FL380 near BEXAL as LAN706 leaves BEXAL "
                     "southbound at FL350; GMMN departure RAM1408 asks FL360 climbing through EWG4UA on the RUNAX "
                     "standard route north of IBALU.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["AZU8721", "IBS3961"]],
                              "seq_level": True},
                  "naive_approve": {"allow_left_sector": True, "los": True,
                                    "pairs": [["LAN706", "TAP102"], ["EWG4UA", "RAM1408"], ["EXS77P", "RYR2ZC"],
                                              ["BTI8PQ", "SWR34E"], ["RAM209", "TVF34LP"]]}},
    )
    t, p = when_alt(scn, "TAP102", 35000, after=1830)
    scn = put(scn, "LAN706", at=on_route(UN866, p), t=t)
    p, t, alt = meet_alt(scn, "RAM1408", [("RUNAX", "IBALU"), ("IBALU", "ERMED")], after=820)
    scn = set_fl(put(scn, "EWG4UA", at=on_route(STD, p), t=t), "EWG4UA", parity_level(alt, True))
    p, t, alt = meet_alt(scn, "RAM209", [("SUNID", "AKUDA")], after=900)
    scn = set_fl(put(scn, "TVF34LP", at=p, t=t), "TVF34LP", parity_level(alt, False))
    # BTI8PQ at FL360 crosses SWR34E while SWR34E is descending through it (FL370 -> FL350)
    p, t, alt = meet_alt(scn, "SWR34E", [("DIMSA", "IBLIV")], after=1465)
    scn = set_fl(put(scn, "BTI8PQ", at=p, t=t), "BTI8PQ", 360)
    return dense(scn, AT.ALL_FLOWS, seed=31)


@scenario
def c02():
    scn = ag(
        "c02_cta_severe_wx",
        title="Whole Agadir CTA, severe squall line, everything at once",
        description="All four Agadir sectors band-boxed, near capacity. A severe squall line (tops FL450-500) "
                    "stretches from Oceanic South across West South with three gaps. A Royal Moroccan Air Force "
                    "C-130 (state aircraft, not RVSM approved) is southbound on UN857. Arrivals: GCRR via TERTO, "
                    "GMMN via MARJA and SALCA (LoA FL250, 10 NM in trail).",
        weather=WX_C_SEV,
        aircraft=[
            ac("EZY39WK", "A20N", UN858, 380, None, at=AT_SAMAR_UN858, t=1500, dep="GCTS", dest="LFPG"),
            ac("DLH512", "B748", ["RUNAX", "IBALU", "SAMAR"], 390, None, at=AT_SAMAR_UN858, t=1500, dep="EDDF",
               dest="SBGR", load=0.35),
            ac("RMF4425", "C130", UN857, 250, None, at="IBALU", t=500, dep="GMME", dest="GMML", load=0.15,
               remarks="STS/STATE"),
            ac("BTI6JW", "A320", UN857, 270, None, entry=0, dep="EBBR", dest="GCFV"),
            ac("VIPER31", "F16", ["NIPRI", "NIDEB", "ROLRO"], 350, None, at=AT_ROLRO_UN858, t=1600, dep="GMSL",
               dest="GMSL", formation={"size": 4, "types": "4xF16", "extent_nm": 1.0}, remarks="STS/STATE"),
            ac("NOZ5KX", "B38M", UN858, 340, None, at=AT_ROLRO_UN858, t=1600, dep="GCLP", dest="ENGM"),
            ac("RAM767", "B738", ["AMSEL", "ABODA", "MARJA"], 310, None, at="MARJA", t=1700, dep="EHAM",
               dest="GMMN"),
            ac("RAM761", "B788", ["BEXAL", "IBALU", "SALCA"], 350, None, at="SALCA", t=2000, dep="KJFK",
               dest="GMMN"),
            ac("RAM213", "B738", ["OSLAD", "IBALU", "SALCA"], 330, None, at="SALCA", t=2040, dep="LPPT",
               dest="GMMN"),
            ac("EXS59R", "B738", UN857, 390, None, at="TERTO", t=2800, dep="EGCC", dest="GCRR"),
            ac("TOM88J", "B38M", STD, 370, None, at="TERTO", t=2845, dep="EGGW", dest="GCRR"),
        ],
        requests=[req(600, "RMF4425", "altitude", 290, "requesting higher")],
        events=[{"time": 700, "callsign": "EZY39WK", "type": "rvsm_unable", "reason": "autopilot failure"}],
        design_notes="EZY39WK (northbound UN858, FL380) reports unable RVSM at T+11:40 and crosses DLH512 "
                     "(RUNAX-IBALU-SAMAR, FL390) in West South at T+25:00. RMF4425 (C130 state aircraft, non-RVSM, FL250) asks FL290: "
                     "allowed for a state aircraft, but it needs 2000 ft and BTI6JW at FL270 is ahead on UN857 - "
                     "check who it climbs through. VIPER31 (4xF16, NIPRI-NIDEB-ROLRO at FL350) crosses NOZ5KX northbound on UN858 at FL340. GMMN "
                     "arrivals: RAM761 and RAM213 reach SALCA 40 s apart; GCRR arrivals EXS59R and TOM88J reach "
                     "TERTO 45 s apart; RAM767 at MARJA. The squall line forces deviations through three gaps "
                     "across UN873, UN858, UN857 and the TERTO arrivals.",
        expected={"passive": {"allow_left_sector": True, "los": True, "pairs": [["DLH512", "EZY39WK"], ["NOZ5KX", "VIPER31"]],
                              "seq_level": True}},
    )
    return dense(scn, AT.ALL_FLOWS, seed=32)


AT_BEXAL_UN858 = XA(["BEXAL", "IBALU"], ["AKUDA", "SUNID"])
AT_SUBAL_UN858 = XA(["SUBAL", "IBALU"], ["AKUDA", "SUNID"])
AT_ROLRO_TKV = XA(["NIDEB", "ROLRO"], ["TAKAV", "VEDOD"])
AT_ROLRO_UN858 = XA(["NIDEB", "ROLRO"], ["DIMSA", "IBLIV"])
AT_SAMAR_UN858 = XA(["IBALU", "SAMAR"], ["IBLIV", "DIMSA"])
AT_NIPRI_UN873 = XA(["NIPRI", "NIDEB"], ["BENTU", "ABTIR"])

# ---------------------------------------------------------------------------------------------
def check(s: dict) -> list:
    problems = []
    from atcsim.scenario import build_world
    from atcsim.aircraft import level_parity_ok
    w = build_world(s)
    for a in w.aircraft.values():
        mx = a.perf.max_fl(*a.speed_target(a.fl * 100.0), a.mass)
        if a.fl > mx:
            problems.append(f"{a.callsign} ({a.icao}) filed FL{a.fl} but max FL at its weight is FL{mx}")
        if not level_parity_ok(a.fl, a.southbound):
            problems.append(f"{a.callsign} filed FL{a.fl}: wrong-direction level for a "
                            f"{'south' if a.southbound else 'north'}bound flight")
    for a in s["aircraft"]:
        bad = w.airspace.against_one_way([n.upper() for n in a["route"]])
        if bad:
            problems.append(f"{a['callsign']} flies {'/'.join(bad)} against its one-way direction")
        for prob in w.airspace.entry_exit_problems([n.upper() for n in a["route"]]):
            if a.get("start") and "exit-only" in prob:
                continue
            problems.append(f"{a['callsign']}: {prob}")
    for r in s.get("requests", []):
        a = w.aircraft[r["callsign"].upper()]
        if r["type"] == "altitude" and not w.descending_to_destination(a, int(r["value"])) \
                and not level_parity_ok(r["value"], a.southbound):
            problems.append(f"{a.callsign} requests FL{r['value']}: wrong-direction level")
    for pol, exp in s.get("expected", {}).items():
        r = run(s, pol)
        if "los" in exp and bool(r["los_events"]) != exp["los"]:
            problems.append(f"{pol}: expected los={exp['los']} got {r['los_pairs']} closest={r['closest']}")
        if "pairs" in exp:
            want = {tuple(p) for p in exp["pairs"]}
            if not want.issubset(set(r["los_pairs"])):
                problems.append(f"{pol}: expected LoS pairs {want} got {r['los_pairs']}")
        if "infringement" in exp and bool(r["infringements"]) != exp["infringement"]:
            problems.append(f"{pol}: expected infringement={exp['infringement']} got {r['infringed']}")
        if r["rejected"]:
            problems.append(f"{pol}: pilot rejected clearances {r['rejected']}")
        if r["left_sector"] and not exp.get("allow_left_sector"):
            problems.append(f"{pol}: left sector {r['left_sector']}")
        sq = r["metrics"]["sequencing"]
        if "seq_level" in exp and bool(sq["level_violations"]) != exp["seq_level"]:
            problems.append(f"{pol}: expected LoA level violations={exp['seq_level']}, got {sq['level_violations']}")
        if "seq_spacing" in exp and bool(sq["spacing_violations"]) != exp["seq_spacing"]:
            problems.append(f"{pol}: expected spacing violations={exp['seq_spacing']}, got {sq['spacing_violations']}")
        if "rvsm_violation" in exp and bool(r["rvsm_violations"]) != exp["rvsm_violation"]:
            problems.append(f"{pol}: expected RVSM airspace violation={exp['rvsm_violation']}, got {r['rvsm_violations']}")
        if "wx_request" in exp and not any(q[0] == exp["wx_request"] for q in r["wx_requests"]):
            problems.append(f"{pol}: expected a pilot weather/ride request from {exp['wx_request']}, got {r['wx_requests']}")
        if sq["crossings"]:
            print(f"        fixes: " + "; ".join(f"{f}: " + ", ".join(f"{c['callsign']} FL{c['fl']}"
                  + (f" {c['spacing_nm']}NM" if c['spacing_nm'] is not None else "") for c in cl)
                  for f, cl in sq["crossings"].items()))
        print(f"   {pol:14s} LoS={r['los_events']} {r['los_pairs']} min={r['min_h']}  "
              f"inf={r['infringed']}  wx_in={r['wx_penetrations']} self_dev={r['wx_self_deviations']}  "
              f"closest={[(c['aircraft'], c['min_horizontal_nm']) for c in r['closest'][:2]]}")
        for q in r["wx_requests"]:
            print(f"        pilot: {q[2]}")
    if s.get("solution"):
        r = run(s, "scripted", s["solution"])
        sq = r["metrics"]["sequencing"]
        ok = (not r["los_events"] and not r["infringements"] and not r["wx_penetrations"]
              and not r["left_sector"] and not r["rejected"]
              and not sq["level_violations"] and not sq["spacing_violations"]
              and not r["rvsm_violations"] and not r["wrong_direction_exits"])
        if not ok:
            problems.append(f"reference solution fails: LoS={r['los_pairs']} inf={r['infringed']} "
                            f"wx={r['wx_penetrations']} left={r['left_sector']} rejected={r['rejected']} "
                            f"rvsm={r['rvsm_violations']} wrong_dir_exits={r['wrong_direction_exits']} "
                            f"seq={[(c['callsign'], c['fl'], c['spacing_nm']) for f in sq['crossings'].values() for c in f]}")
        print(f"   {'solution':14s} LoS={r['los_events']} wx_in={r['wx_penetrations']} "
              f"self_dev={r['wx_self_deviations']} closest={[(c['aircraft'], c['min_horizontal_nm']) for c in r['closest'][:2]]}")
    return problems


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    bad = 0
    only = sys.argv[1:]
    done = 0
    for fn in SCENARIOS:
        if only and not any(fn.__name__.startswith(o) or fn.__name__ == o for o in only):
            continue
        done += 1
        s = fn()
        print(f"{s['name']}")
        probs = check(s)
        for p in probs:
            print("   !!", p)
        bad += bool(probs)
        with open(os.path.join(OUT, s["name"] + ".json"), "w") as f:
            json.dump(s, f, indent=2)
    print(f"\n{done} scenarios written, {bad} with problems")
    sys.exit(1 if bad else 0)
