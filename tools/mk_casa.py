"""Build the Casablanca ACC scenarios: s21 (North, GMMN / GMME) and s22 (South = Central + South-South band-boxed,
GMMX / GMAD / GMMZ).   python tools/mk_casa.py"""
import json
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scenarios")

WX_W = {"seed": 211, "temperature": {"surface_c": 21, "tropopause_fl": 380},
        "wind": {"profile": [{"fl": 0, "dir": 300, "kt": 12}, {"fl": 200, "dir": 285, "kt": 40},
                             {"fl": 350, "dir": 275, "kt": 60}, {"fl": 450, "dir": 270, "kt": 40}], "noise_kt": 5},
        # morning showers building over the Rabat - Fes corridor and the Middle Atlas foothills
        "convection": [{"regime": "isolated", "intensity": "strong", "count": 5, "region": [205, 25, 325, 120]},
                       {"regime": "isolated", "intensity": "moderate", "count": 3, "region": [150, 60, 230, 140]}]}
WX_S = {"seed": 222, "temperature": {"surface_c": 27, "tropopause_fl": 390},
        "wind": {"profile": [{"fl": 0, "dir": 20, "kt": 10}, {"fl": 200, "dir": 330, "kt": 30},
                             {"fl": 350, "dir": 300, "kt": 45}, {"fl": 450, "dir": 290, "kt": 30}], "noise_kt": 4},
        # afternoon convection over the High Atlas (Marrakech - Ouarzazate - Beni Mellal), tops to FL450
        "convection": [{"regime": "scattered", "intensity": "strong", "count": 9, "region": [190, -150, 340, -40]},
                       {"regime": "isolated", "intensity": "moderate", "count": 3, "region": [120, -120, 190, -60]}]}


def ac(cs, t, route, fl, dep, dest, at=None, time=None, **kw):
    d = {"callsign": cs, "type": t, "route": route, "fl": fl, "dep": dep, "dest": dest}
    if at is not None:
        d["reach"] = {"waypoint": at, "time": time}
    d.update(kw)
    return d


def dep(cs, t, start, route, rfl, ad, dest, time, fl=150, cfl=190, **kw):
    """A departure released by approach climbing to FL190 at a TMA point, asking for its final level."""
    return {"callsign": cs, "type": t, "route": [start] + route, "fl": fl, "cfl": cfl, "rfl": rfl, "dep": ad,
            "dest": dest, "start": {"point": start, "time": time}, **kw}


# ============================================================ NORTH: GMMN / GMME
north = [
    # --- GMMN arrivals, first wave at LAKAM: from Agadir West North via MARJA (UR722) and SALCA (UA411)
    ac("RAM763", "B738", ["AMSEL", "ABODA", "MARJA", "LAKAM"], 330, "LPPT", "GMMN", "LAKAM", 1050),
    ac("TAP1032", "A20N", ["IBALU", "SALCA", "LAKAM"], 350, "LPMA", "GMMN", "LAKAM", 1075),
    ac("RAM203", "B788", ["IBALU", "SALCA", "LAKAM"], 370, "KJFK", "GMMN", "LAKAM", 1110, load=0.55),
    ac("EZY9113", "A320", ["AMSEL", "ABODA", "MARJA", "LAKAM"], 330, "EGKK", "GMMN", "LAKAM", 1150),
    # departure to Lisbon on UR722 northbound, climbing against the MARJA arrivals
    dep("RAM1474", "E190", "MOKDA", ["LAKAM", "MARJA", "ABODA", "AMSEL"], 300, "GMMN", "LPPT", 840),
    # --- GMMN arrivals at RAVOL: from the west (UH500 via NISBA) and from the south (UR724 via MOKRI)
    ac("IBE3920", "A321", ["IBALU", "NISBA", "RAVOL"], 350, "LEMD", "GMMN", "RAVOL", 1640),
    ac("RAM1407", "B738", ["ESS", "MOKRI", "RAVOL"], 330, "GOOY", "GMMN", "RAVOL", 1665),
    # --- GMMN arrivals at BISMI: from the east (UR975 via SADIC) and from the north (UB11 via DIRAD)
    ac("RAM571", "B738", ["FES", "POBUB", "SADIC", "DONAS", "BISMI"], 330, "LFML", "GMMN", "BISMI", 1950),
    ac("AFR1396", "A320", ["GALTO", "TTN", "BARIS", "TUSOR", "DIRAD", "BISMI"], 350, "LFPO", "GMMN", "BISMI", 1985),
    ac("SVA285", "B77W", ["FES", "POBUB", "SADIC", "DONAS", "BISMI"], 370, "OEJN", "GMMN", "BISMI", 2030, load=0.6),
    # --- GMMN second wave at LAKAM
    ac("RAM1425", "E190", ["AMSEL", "ABODA", "MARJA", "LAKAM"], 310, "LPPT", "GMMN", "LAKAM", 2700),
    ac("DLH4AM", "A20N", ["IBALU", "SALCA", "LAKAM"], 350, "EDDF", "GMMN", "LAKAM", 2725),
    ac("RAM211", "B789", ["IBALU", "SALCA", "LAKAM"], 330, "KMIA", "GMMN", "LAKAM", 2760, load=0.5),
    # --- GMME (Rabat) arrivals: FAHIM from the north, ENNAV from the east
    ac("RAM977", "B738", ["GALTO", "FAHIM"], 330, "LFPO", "GMME", "FAHIM", 1400),
    ac("VLG8541", "A320", ["FES", "DEMUM", "IPLOS", "VARAS", "ENNAV"], 310, "LEBL", "GMME", "ENNAV", 2250),
    # --- departures from GMMN / GMME to Spain on UN871 (BAKIM is the departure junction), into the UN871 stream
    dep("RAM970", "B738", "REVMI", ["BAKIM", "OSDAM", "ADUBI"], 360, "GMMN", "LEMD", 1320),
    dep("RAM1352", "A21N", "MOKIB", ["BAKIM", "OSDAM", "ADUBI"], 380, "GMME", "LFPO", 1450),
    # GMMN departure to the north on UB11, climbing into the northbound UB11 traffic
    dep("RAM756", "B738", "ESALA", ["TUSOR", "BARIS", "TTN", "GALTO"], 360, "GMMN", "EBBR", 2050),
    # GMMN departure south on UN869 under the UB11 southbound pair
    dep("RAM1505", "B738", "KOTAG", ["PELAX", "MABAP"], 350, "GMMN", "GOOY", 2350),
    # --- transits
    # crossing over RBT at FL370: UN869 southbound x UA411 eastbound
    ac("IBE3871", "A321", ["GALTO", "RBT", "SLK", "PELAX", "MABAP"], 370, "LEMD", "GOOY", "RBT", 1250),
    ac("RAM1110", "B738", ["SALCA", "LAKAM", "RBT", "VARAS", "IPLOS", "DEMUM", "FES"], 370, "GMAD", "GMFO", "RBT", 1262),
    # UN871 northbound: a fast wide-body catches a slower A320 at the same level before ADUBI (overtake)
    ac("ETH712", "B77W", ["ESS", "NIRAL", "OSDAM", "ADUBI"], 360, "GOOY", "LIMC", "OSDAM", 1525, mach=0.85, load=0.7),
    ac("VLG8122", "A320", ["ESS", "NIRAL", "OSDAM", "ADUBI"], 360, "GMAD", "LEBL", "OSDAM", 1500, mach=0.76),
    # UB11 southbound: same level, the follower faster (speed trap before FACAS)
    ac("RYR4CW", "B738", ["GALTO", "TTN", "BARIS", "TUSOR", "DIRAD", "BISMI", "SLK", "PELAX", "FACAS"], 350, "EGSS",
       "GMMX", "BARIS", 700, mach=0.76),
    ac("EZY62LK", "A320", ["GALTO", "TTN", "BARIS", "TUSOR", "DIRAD", "BISMI", "SLK", "PELAX", "FACAS"], 350, "EGKK",
       "GMMX", "BARIS", 780, mach=0.80),
    # UB11 northbound (opposite to the pair), the level RAM756 asks for
    ac("TVF3302", "B738", ["FACAS", "PELAX", "SLK", "BISMI", "DIRAD", "TUSOR", "BARIS", "TTN", "GALTO"], 360, "GMMX",
       "LFPO", "TUSOR", 2280),
    # UG5 both ways: the northbound one asks to climb through the southbound one's level
    ac("TAP1652", "A20N", ["KORIS", "VAGNO", "ROMSO", "TOLSI", "AGDAL", "SAK", "RALEK", "VONCI"], 390, "LPPT", "GMMX",
       "TOLSI", 1800),
    ac("AFR1450", "A320", ["VONCI", "RALEK", "SAK", "AGDAL", "TOLSI", "ROMSO", "VAGNO", "KORIS"], 380, "GMMX", "LFPO",
       "TOLSI", 1830),
    # UN726 eastbound towards Al Hoceima when GMD 21 is active (TTN-ALM runs into it in the East sector)
    ac("RAM517", "B738", ["BAMBA", "ABIRO", "TNR", "NARMI", "TTN", "ALM"], 370, "LPPT", "GMMW", "TNR", 1700),
    ac("IBE3301", "A320", ["TNR", "MADOV", "IXODA", "ATVAR", "FES"], 350, "LEMD", "GMFF", "MADOV", 1200),
]
north_req = [
    {"time": 1690, "callsign": "AFR1450", "type": "altitude", "value": 400, "reason": "better ride reported at FL400"},
]
s21 = {
    "name": "s21_casablanca_north_gmmn_gmme",
    "title": "Casablanca North: GMMN and GMME arrivals and departures",
    "description": "Casablanca ACC, North sector (125.500) open alone, one hour. GMMN arrivals in three streams - from "
                   "Agadir West North via MARJA and SALCA to LAKAM, from the west via NISBA and from the south via MOKRI "
                   "to RAVOL, from the east on UR975 and from the north on UB11 to BISMI - and GMME arrivals to FAHIM and "
                   "ENNAV. Letter of agreement with approach: cleared FL200 at the fix (still descending is fine), 10 NM "
                   "in trail. GMMN and GMME departures are released climbing FL190 and ask for their cruising level "
                   "(UN871 via BAKIM, UR722 via LAKAM, UB11 via TUSOR, UN869 south). Transit traffic on UB11, UN869, "
                   "UA411, UN871, UG5, UN726 and UM240. GMD 21 (FL150-FL460, Al Hoceima) is notified active from T+15 "
                   "min. Westerly flow; isolated thunderstorms building between Rabat, Fes and the Middle Atlas.",
    "design_notes": "LAKAM wave 1 (T+17 to T+19): RAM763 (MARJA FL330), TAP1032 and the heavy RAM203 behind it (SALCA, "
                    "FL350 / FL370), EZY9113 (MARJA FL330) - four within 100 s; RAM203 is fast and must not be first by "
                    "default, the order and 10 NM come from speed and early vectors. RAM1474 (GMMN to LPPT, released "
                    "FL190 at T+14) asks FL300 and climbs straight up UR722 against the MARJA arrivals: give the climb "
                    "only above / after them or vector it. RAVOL (T+27): IBE3920 from NISBA and RAM1407 from MOKRI "
                    "converge 25 s apart. BISMI (T+32 to T+34): RAM571 and the B77W SVA285 on UR975, AFR1396 on UB11 "
                    "behind the UB11 southbound pair. Crossings: IBE3871 (UN869) and RAM1110 (UA411) at FL370 over "
                    "RBT at T+21 (1 NM if nothing is done). Overtake: ETH712 (B77W M0.85) catches VLG8122 (A320 M0.76) "
                    "on UN871 at FL360 before ADUBI, and the BAKIM departures RAM970 (asks FL360) and RAM1352 (asks "
                    "FL380) join that stream. Speed trap: EZY62LK (M0.80) behind RYR4CW (M0.76), both FL350 on UB11 "
                    "southbound, 10 NM apart at BARIS, closing. RAM756 (GMMN departure on UB11 north at T+34) asks "
                    "FL360 = the level of TVF3302, northbound on UB11 ahead of it: a climb into its trail. AFR1450 northbound on UG5 at FL380 asks "
                    "FL400 at T+28 while TAP1652 southbound at FL390 is opposite on the same airway: unable until "
                    "passed. GMD 21 activates at T+15: RAM517 (UN726 east, TNR at T+28) must be rerouted before TTN-ALM "
                    "(coordinate with East). Second LAKAM wave at T+45: RAM1425 (MARJA FL310), DLH4AM and the heavy "
                    "RAM211 (SALCA, FL350 / FL330).",
    "airspace": "agadir", "control": ["N"], "duration_s": 3600, "complexity": 4, "start_utc": "09:00",
    "weather": WX_W,
    "published_areas": {"GMD21": {"active": False, "schedule": [{"time": 900, "active": True}]}},
    "areas": [], "requests": north_req, "aircraft": north, "expected": {},
}

# ============================================================ SOUTH (Central + South-South): GMMX / GMAD / GMMZ
south = [
    # --- GMMX arrivals at MABAP from the north: UR722 via FOBAC, UB11 via FACAS, UN869 via PELAX
    ac("RAM1203", "B738", ["CSD", "FOBAC", "MABAP"], 330, "LFPO", "GMMX", "MABAP", 1000),
    ac("EZY7311", "A320", ["SLK", "PELAX", "FACAS", "MABAP"], 350, "EGGW", "GMMX", "MABAP", 1025),
    ac("TOM6KJ", "B789", ["SLK", "PELAX", "MABAP"], 370, "EGKK", "GMMX", "MABAP", 1060, load=0.6),
    ac("RYR77QA", "B738", ["CSD", "FOBAC", "MABAP"], 310, "EIDW", "GMMX", "MABAP", 1110),
    # GMMX departure to the north on UB11, climbing against the MABAP arrivals
    dep("RAM1300", "B738", "MABAP", ["FACAS", "PELAX", "SLK"], 360, "GMMX", "LFPO", 900),
    # --- GMMX arrival from the east on UG850 at OBOGA
    ac("RAM1561", "E190", ["FES", "NEVEK", "ERLAM", "TIXAL", "VALBA", "OBOGA"], 330, "GMFF", "GMMX", "OBOGA", 1700),
    # --- GMAD arrivals at AGALI: UR724 from ESS and UH400 from MAK merge
    ac("TOM3NP", "B738", ["MOKRI", "ESS", "DENOB", "AGALI"], 350, "EGCC", "GMAD", "AGALI", 1550),
    ac("TVF7720", "A320", ["MAK", "GOSNO", "MIMRO", "AGALI"], 330, "LFPO", "GMAD", "AGALI", 1570),
    ac("CFG1KR", "A332", ["MOKRI", "ESS", "DENOB", "AGALI"], 370, "EDDF", "GMAD", "AGALI", 1615, load=0.55),
    # GMAD departure north on UR724, against the AGALI arrivals
    dep("RAM1006", "B738", "AGALI", ["DENOB", "ESS", "MOKRI"], 340, "GMAD", "LFPO", 1380),
    # --- GMAD arrival from the south on UA600 (TEKNA) and a GMAD departure south on the same airway
    ac("BIN420", "B738", ["LAY", "SOLNA", "AMLOK", "TEKNA"], 340, "GMML", "GMAD", "TEKNA", 2050),
    dep("RAM1407", "E190", "TEKNA", ["AMLOK", "SOLNA", "LAY"], 330, "GMAD", "GMML", 1900),
    # --- GMAD arrival on UG850 (OLOMA) and a GMMX departure south on UG850 ahead of it
    ac("RYR2AG", "B738", ["MAK", "LISRA", "BIDLA", "OLOMA"], 310, "LEBL", "GMAD", "OLOMA", 2400),
    dep("RAM1622", "B738", "LISRA", ["BIDLA", "OLOMA", "ADM", "BULOK", "SOMOM", "DEVLA"], 350, "GMMX", "GCLP", 2120),
    # --- GMMZ (Ouarzazate): an arrival on UJ744, a departure on UQ401 then UM372 to Fes
    ac("RAM1717", "E190", ["MAK", "MOKIR", "MAXOR"], 290, "GMMN", "GMMZ", "MAXOR", 1350, rfl=290),
    dep("RAM1718", "E190", "RATVA", ["RUBAB", "BML", "DIDOV", "SODSA", "FES"], 280, "GMMZ", "GMFF", 2500, fl=150, cfl=190),
    # --- transits
    # UM372 southbound x UW900 southbound at AGOVA, FL370
    ac("SWR254", "A333", ["FES", "SODSA", "DIDOV", "BML", "RAVMA", "AGOVA", "ABDOR", "KUDAX", "MAK", "TOPMA", "DIVUX",
                          "BULIS"], 370, "LSZH", "GOOY", "AGOVA", 1300),
    ac("RAM287", "B738", ["SAK", "SLK", "PIXOV", "MOGBI", "TIXAL", "AGOVA", "MOGVA", "OZT"], 370, "GMMN", "GMMZ", "AGOVA",
       1312, arrival=False),
    # UG664 south-west x UM372 at TOPMA, FL370 (SWR254 again)
    ac("AFR1580", "A20N", ["ERA", "LARAP", "OZT", "LINVA", "TOPMA", "ABATO", "KEGAG", "ADM", "TAGOG", "DOGAN", "SONSO"],
       370, "LFML", "GCLP", "TOPMA", 2240),
    # UR975 southbound: a fast B77W behind a B738 at the same level (overtake before ECHED)
    ac("RAM1527", "B738", ["CSD", "NIKZO", "BENOV", "MAGAV", "NAMIR", "EKTAL", "ADM", "ARSOL", "DIDAN", "MOROX", "ECHED"],
       350, "GMMN", "GOOY", "MAGAV", 1150, mach=0.76, rfl=350),
    ac("UAE751", "B77W", ["CSD", "NIKZO", "BENOV", "MAGAV", "NAMIR", "EKTAL", "ADM", "ARSOL", "DIDAN", "MOROX", "ECHED"],
       350, "OMDB", "SBGR", "MAGAV", 1235, mach=0.85, load=0.7),
    # UG850 northbound (opposite to the southbound UG850 traffic) at FL360
    ac("TVF4410", "B738", ["DEVLA", "SOMOM", "BULOK", "ADM", "OLOMA", "BIDLA", "LISRA", "MAK", "OBOGA", "VALBA", "TIXAL",
                           "ERLAM", "NEVEK", "FES"], 360, "GCLP", "LFPO", "OLOMA", 2300),
    # UN869 southbound under the GMAD arrivals' descent
    ac("IBE3871", "A321", ["PELAX", "MABAP", "ADM", "RUSIK"], 390, "LEMD", "GCLP", "ADM", 1750),
    # UL102 westbound through GMD 30 once it is active
    ac("RAM1891", "B738", ["ERA", "RUBAB", "AGOVA"], 360, "GMFK", "GMMX", "RUBAB", 2000, arrival=False),
    # UA600 southbound above the TEKNA arrival
    ac("RAM1291", "B788", ["ADM", "TEKNA", "AMLOK", "SOLNA", "LAY"], 350, "GMMN", "GOOY", "AMLOK", 2150, rfl=350),
    # UJ744 eastbound to the Algerian boundary
    ac("RAM1855", "B738", ["MAK", "MOKIR", "MAXOR", "OZT", "BAGBO"], 330, "GMMX", "DAAG", "MAXOR", 2650, arrival=False),
]
south_req = [
    {"time": 1650, "callsign": "IBE3871", "type": "altitude", "value": 350, "reason": "turbulence reported at FL390"},
]
s22 = {
    "name": "s22_casablanca_south_gmmx_gmad",
    "title": "Casablanca South: GMMX, GMAD and GMMZ arrivals and departures",
    "description": "Casablanca ACC, Central (133.100) and South-South (126.700) band-boxed, one hour. GMMX arrivals "
                   "from the north converge on MABAP (UR722 via FOBAC, UB11 via FACAS, UN869 via PELAX), one from the "
                   "east at OBOGA. GMAD arrivals merge at AGALI (UR724 from ESS, UH400 from MAK), one from Laayoune at "
                   "TEKNA, one on UG850 at OLOMA. Occasional Ouarzazate traffic (MAXOR in, RATVA out). Letter of "
                   "agreement with the approaches: cleared FL200 at the fix (still descending is fine), 10 NM in trail. "
                   "Departures are released climbing FL190 and ask for their cruising level. Transit traffic on UM372, "
                   "UW900, UG664, UR975, UG850, UN869, UL102, UA600 and UJ744. GMD 30 (GND-FL460, Midelt) is notified "
                   "active from T+15 min. Northerly low-level wind, westerlies aloft; afternoon thunderstorms over the High "
                   "Atlas between Marrakech, Beni Mellal and Ouarzazate (scattered, strong, tops to FL450), a few "
                   "isolated cells west of Marrakech.",
    "design_notes": "MABAP (T+16.5 to T+18.5): RAM1203 (FOBAC FL330), EZY7311 (FACAS FL350), the heavy TOM6KJ (PELAX "
                    "FL370), RYR77QA (FOBAC FL310) - three entries, four aircraft in 110 s. RAM1300 (GMMX departure "
                    "released FL190 at MABAP at T+15) asks FL360 and climbs north on UB11 through the arrivals' "
                    "descents. AGALI (T+26 to T+27): TOM3NP and the A332 CFG1KR on UR724, TVF7720 on UH400 from MAK; "
                    "RAM1006 (GMAD to LFPO, released at AGALI at T+23) climbs north on UR724 against them. TEKNA: "
                    "RAM1407 departs south on UA600 (T+31.5, asks FL330) head-on with the arrival BIN420 (FL340, TEKNA "
                    "at T+34) under RAM1291 (UA600 south FL350). Watch ADM: RAM1291 (UA600) leaves it southbound at FL350 just as the UR975 pair (FL350) passes it - an unplanned-looking convergence. OLOMA: RAM1622 (GMMX departure on UG850 south, asks "
                    "FL350) ahead of the arrival RYR2AG (FL310) and opposite TVF4410 (UG850 north FL360). Crossings at "
                    "FL370: SWR254 (UM372) and RAM287 (UW900) at AGOVA at T+22; SWR254 again with AFR1580 (UG664) at "
                    "TOPMA at T+37. Overtake: UAE751 (B77W M0.85) catches RAM1527 (B738 M0.76) on UR975 at FL350 before "
                    "the GCCC boundary. IBE3871 (UN869 FL390) asks FL350 at T+27.5 while the AGALI and MABAP traffic is "
                    "around. GMD 30 activates at T+15: RAM1891 (UL102 westbound FL360, entering from East about T+21 "
                    "straight into the area, RUBAB at T+33) needs a reroute coordinated with East before it enters. Ouarzazate: RAM1717 to MAXOR (FL290), RAM1718 out of RATVA at T+41.5 on "
                    "UQ401 / UM372 to Fes.",
    "airspace": "agadir", "control": ["C", "SS"], "duration_s": 3600, "complexity": 4, "start_utc": "14:00",
    "weather": WX_S,
    "published_areas": {"GMD30": {"active": False, "schedule": [{"time": 900, "active": True}]}},
    "areas": [], "requests": south_req, "aircraft": south, "expected": {},
}

for s in (s21, s22):
    with open(os.path.join(ROOT, s["name"] + ".json"), "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(s["name"], len(s["aircraft"]), "aircraft")
