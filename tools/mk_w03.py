"""Builds scenarios/w03_ww_arrival_waves.json (waves of TERTO and MARJA arrivals): python3 tools/mk_w03.py"""
import json
import os
K = ["KORNO", "ABODA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"]
T = ["TAKAV", "VEDOD", "TERTO"]
RT = ["RUNAX", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"]
MA = {"A": ["AMSEL", "ABODA", "MARJA"], "K": ["KORNO", "ABODA", "MARJA"], "R": ["RUNAX", "ABODA", "MARJA"]}
ac, rq = [], []

def add(cs, typ, route, fl, dest, dep, reach, **kw):
    a = {"callsign": cs, "type": typ, "route": route, "fl": fl, "dep": dep, "dest": dest, "reach": reach}
    a.update(kw); ac.append(a); return a

# GCRR arrivals via TERTO, in waves of 4, 5, 6 (time at VEDOD), descent asked near VEDOD at varied moments
gcrr = [  # (callsign, type, stream, FL, at VEDOD, descent request relative to VEDOD, dep)
    ("RYR72TK", "B738", "K", 370, 1500, -200, "EGSS"), ("EZY43RF", "A320", "T", 350, 1575, -290, "EGGW"),
    ("EXS21LZ", "B738", "K", 390, 1580, -110, "EGCC"), ("TOM4HB", "B788", "T", 370, 1540, -230, "EGKK", 0.85),
    ("VLG81KP", "A320", "T", 370, 2628, -150, "LEBL"), ("RYR5WD", "B738", "K", 350, 2612, -260, "EIDW"),
    ("EZY88QP", "A20N", "K", 370, 2650, -90, "LFPG"), ("CFG1W", "A333", "T", 390, 2690, -310, "EDDF", 0.84),
    ("BAW2RF", "A321", "K", 350, 2722, -180, "EGLL"),
    ("RYR33VN", "B738", "K", 370, 3600, -120, "EGPH"), ("TOM9KX", "B789", "T", 350, 3632, -240, "EGBB", 0.85),
    ("EXS7NC", "B738", "T", 370, 3625, -300, "EGNM"), ("IBS41D", "A332", "K", 390, 3702, -60, "LEMD", 0.83),
    ("EZY12AJ", "A320", "K", 350, 3744, -200, "EGGD"), ("TRA5TP", "B738", "T", 370, 3780, -160, "EHAM"),
]
# heavies (8th item: their Mach, faster than the mediums around them) sandwiched in each wave: the order is not the
# order of today's positions once the speeds play
gcrr = [(c, ty, st, fl, t + 600, dr, dep, *m) for c, ty, st, fl, t, dr, dep, *m in gcrr]   # waves at VEDOD ~2100, 3200, 4300 s
for cs, typ, st, fl, t, dr, dep, *m in gcrr:
    add(cs, typ, K if st == "K" else T, fl, "GCRR", dep, {"waypoint": "VEDOD", "time": t}, auto_descent_request=False,
        **({"mach": m[0]} if m else {}))
    rq.append({"time": t + dr, "callsign": cs, "type": "descent", "reason": "descent for Lanzarote"})
# GMMN arrivals via MARJA (then LAKAM), waves of 2, 3, 4 (time at ABODA), descent asked near ABODA
gmmn = [
    ("RAM761", "B738", "A", 350, 700, -120, "LFPO"), ("RAM973", "B788", "K", 370, 735, -200, "EGLL", 0.85),
    ("RAM1347", "B738", "R", 370, 1750, -60, "LFPG"), ("RAM803", "B739", "A", 350, 1820, -180, "EBBR"),
    ("RAM1123", "B789", "K", 390, 1785, -40, "EDDF", 0.85),
    ("RAM949", "B738", "K", 370, 2850, -150, "LSZH"), ("RAM655", "B738", "R", 350, 2882, -240, "LFML"),
    ("RAM1471", "B788", "A", 370, 2875, -90, "LEMD", 0.85), ("RAM519", "B738", "K", 350, 2962, -30, "LIRF"),
]
for cs, typ, st, fl, t, dr, dep, *m in gmmn:
    add(cs, typ, MA[st], fl, "GMMN", dep, {"waypoint": "ABODA", "time": t}, auto_descent_request=False,
        **({"mach": m[0]} if m else {}))
    rq.append({"time": t + dr, "callsign": cs, "type": "descent", "reason": "descent for Casablanca"})
# overflights on the same axes, Canaries (no descent), in the way of the descents
add("IBE3518", "A320", K, 310, "GCLP", "LEMD", {"waypoint": "ABODA", "time": 760})      # under RAM761/973 at ABODA, then GCRR wave 3
add("BAW2581", "A321", RT, 330, "GCLP", "EGLL", {"point": [184.0, 131.0], "time": 835})  # crosses ABODA-MARJA under wave 1
add("TOM67G", "B738", T, 390, "GCFV", "EGCC", {"waypoint": "VEDOD", "time": 2260})      # above / among GCRR wave 1
add("EZY91WP", "A20N", K, 330, "GCFV", "EGGW", {"waypoint": "ABODA", "time": 1845})
add("NOZ418", "B738", RT, 310, "GCLP", "ENGM", {"point": [184.0, 131.0], "time": 1890})
add("RYR8C", "B738", K, 330, "GCLP", "EIDW", {"waypoint": "VEDOD", "time": 3140})       # ahead of GCRR wave 2, FL330 below it
add("TOM2DM", "B738", T, 330, "GCFV", "EGNX", {"waypoint": "VEDOD", "time": 3360})
add("SAS77K", "A21N", RT, 330, "GCLP", "ESSA", {"point": [184.0, 131.0], "time": 2990})
add("EWG14T", "A320", K, 310, "GCFV", "EDDK", {"waypoint": "ABODA", "time": 2905})
add("TAP1129", "A21N", T, 390, "GCLP", "LPPT", {"waypoint": "VEDOD", "time": 4430})
# slow traffic low, well ahead of each GCRR wave: caught up when the descents start near VEDOD
add("AME4521", "C130", K, 270, "GCLP", "LETO", {"waypoint": "VEDOD", "time": 1950}, military=True)     # ahead of wave 1
add("AME3302", "A400", T, 290, "GCLP", "LEZG", {"waypoint": "VEDOD", "time": 3060}, military=True)     # ahead of wave 2
add("IBB8790", "E195", K, 310, "GCRR", "LEMD", {"waypoint": "VEDOD", "time": 4150}, mach=0.74,          # ahead of wave 3,
    auto_descent_request=False)                                                                           # an arrival too
rq.append({"time": 3980, "callsign": "IBB8790", "type": "descent", "reason": "descent for Lanzarote"})
# northbound and departures: head-on and crossing the descents and climbs
add("TAP1066", "A20N", ["MARJA", "ABODA", "AMSEL"], 320, "LPPT", "GCLP", {"waypoint": "MARJA", "time": 2960})   # head-on with GMMN wave 3
add("RAM1290", "B738", ["MARJA", "ABODA", "AMSEL"], 260, "LFPO", "GMMN", {"waypoint": "MARJA", "time": 1700}, rfl=360)
add("RAM2210", "B738", ["SALCA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"], 260, "GCLP", "GMMN",
    {"waypoint": "SALCA", "time": 1890}, rfl=350)
add("BCS51", "A332", ["VASTO", "SULAM", "DIMSA", "IBLIV", "SUNID", "AKUDA"], 380, "EGKK", "GCTS",
    {"waypoint": "SUNID", "time": 560})
rq.append({"time": 420, "callsign": "BCS51", "type": "altitude", "value": 400, "reason": "requesting FL400, better winds"})
scn = {
    "name": "w03_ww_arrival_waves",
    "title": "West sectors: waves of arrivals for TERTO and MARJA",
    "airspace": "agadir", "control": ["WN", "WS"], "duration_s": 4800, "complexity": 5, "start_utc": "07:30",
    "description": ("West sectors band-boxed (WW). Three waves of Lanzarote (GCRR) arrivals for TERTO - 4, then 5, then 6 "
                    "aircraft - from KORNO via ABODA-IBALU-ERMED-TOVRA-VEDOD and from TAKAV direct VEDOD, merging at VEDOD; "
                    "and three waves of Casablanca (GMMN) arrivals for MARJA (then LAKAM) - 2, then 3, then 4 - from AMSEL, "
                    "KORNO and RUNAX via ABODA. Letters of agreement: cleared FL250 at TERTO / MARJA, 10 NM in trail. "
                    "The arrivals ask for descent near VEDOD / ABODA, not always in the order of the sequence. Heavies (B788, B789, "
                    "A332, A333), faster, are sandwiched between the mediums; slow traffic low on the same route (a C130 at "
                    "FL270, an A400 at FL290, an E195 arrival at FL310) is well ahead of each wave and is caught up when "
                    "the descents start near VEDOD. Overflights "
                    "for Gran Canaria and Fuerteventura on the same axes, and traffic crossing the ABODA-MARJA leg, stay "
                    "at their levels in the way of the descents; GMMN departures climb against the arrivals."),
    "design_notes": ("GCRR waves at VEDOD about 2100, 3200, 4300 s; GMMN waves at ABODA about 700, 1800, 2900 s. "
                     "Heavies sandwiched: TOM4HB B788 M.85 between RYR72TK and EZY43RF; CFG1W A333 M.84 between EZY88QP and BAW2RF; "
                     "TOM9KX B789 and IBS41D A332 in wave 3; RAM1123 B789 between RAM1347 and RAM803; RAM1471 B788 between "
                     "RAM949 and RAM655. Slow and low ahead: AME4521 C130 FL270 at VEDOD 2.5 min before wave 1, AME3302 "
                     "A400 FL290 2.5 min before wave 2, IBB8790 E195 (M.74, itself a GCRR arrival) FL310 before wave 3. "
                     "Same-level merges (loss if left alone): RYR72TK/TOM4HB at VEDOD FL370 40 s apart, VLG81KP/EZY88QP FL370 22 s, "
                     "RYR33VN/EXS7NC FL370 25 s, RAM949/RAM1471 at ABODA FL370 25 s. "
                     "Descent requests come between 30 s and 5 min before the merge point, sometimes the follower first. "
                     "In the way: IBE3518/EZY91WP/EWG14T (KORNO-ABODA at FL310/330 just after each GMMN wave), "
                     "BAW2581/NOZ418/SAS77K (RUNAX-IBALU crossing ABODA-MARJA at FL330/310), TOM67G/RYR8C/TOM2DM/TAP1129 "
                     "(GCLP/GCFV on the TERTO axes), TAP1066 head-on on MARJA-ABODA at FL320, RAM1290 GMMN departure "
                     "climbing to FL360 against GMMN wave 2, RAM2210 (SALCA-IBALU) climbing to FL350 into GCRR wave 3 at IBALU, BCS51 northbound "
                     "asking FL400 as the TAKAV stream at FL390 crosses it."),
    "weather": {"seed": 31, "wind": {"climatology": {"month": 1}, "noise_kt": 4}},   # January, ERA5
    "aircraft": ac, "requests": sorted(rq, key=lambda r: r["time"]),
    "expected": {"passive": {"allow_left_sector": True, "los": True,
                             "pairs": [["RYR72TK", "TOM4HB"], ["RAM1471", "RAM949"], ["EZY88QP", "VLG81KP"], ["EXS7NC", "RYR33VN"]]}},
}
json.dump(scn, open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios", "w03_ww_arrival_waves.json"), "w"), indent=1)
print(len(ac), "aircraft", len(rq), "requests")
