"""Flight plans (ICAO FPL) for every aircraft, correlated with its radar track.

Each aircraft gets a complete filed flight plan: item 7 (ACID, SSR code), 8 (rules, type of flight), 9 (number, type,
wake), 10 (equipment / surveillance), 13 (departure, EOBT), 15 (speed, level, route), 16 (destination, total EET,
alternates), 18 (STS, PBN, DOF, REG, EET, SEL, CODE, OPR, PER, RMK) and 19 (endurance, persons on board).
Whatever the scenario gives in its "fpl" object (or dep/dest/equipment/remarks) is kept; the rest is filled in a
deterministic way from the callsign (operator, registration, Mode S address, SSR code, ...).

The track is correlated with the plan by its SSR code and Mode S address. The surveillance part of item 10 says what
the radar gets from the transponder: Mode A/C only, Mode S elementary surveillance (aircraft identification, 25 ft
altitude) or enhanced surveillance (downlinked aircraft parameters: selected level, heading, speeds, roll, rates).
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------ reference data
# aerodromes (lat, lon), enough for the flows of the Agadir scenarios and for the training sector
AERODROMES: Dict[str, Tuple[float, float]] = {
    "CYUL": (45.47, -73.74), "CYYZ": (43.68, -79.63), "DIAP": (5.26, -3.93), "DNMM": (6.58, 3.32),
    "EBBR": (50.90, 4.48), "EDDF": (50.03, 8.57), "EDDK": (50.87, 7.14), "EDDL": (51.29, 6.77),
    "EDDM": (48.35, 11.79), "EGBB": (52.45, -1.75), "EGCC": (53.35, -2.27), "EGGW": (51.87, -0.37),
    "EGKK": (51.15, -0.19), "EGLL": (51.47, -0.46), "EGNM": (53.87, -1.66), "EGPH": (55.95, -3.37),
    "EGSS": (51.89, 0.24), "EHAM": (52.31, 4.76), "EIDW": (53.42, -6.27), "EKCH": (55.62, 12.66),
    "ENGM": (60.19, 11.10), "ESSA": (59.65, 17.92), "GCFV": (28.45, -13.86), "GCLA": (28.63, -17.76),
    "GCLP": (27.93, -15.39), "GCRR": (28.95, -13.61), "GCTS": (28.04, -16.57), "GCXO": (28.48, -16.34),
    "GLRB": (6.23, -10.36), "GMAD": (30.33, -9.41), "GMME": (34.05, -6.75), "GMML": (27.15, -13.22),
    "GMMN": (33.37, -7.59), "GMMX": (31.61, -8.04), "GMMI": (31.40, -9.68), "GMMW": (34.99, -2.96),
    "GMFF": (33.93, -4.98), "GMTT": (35.73, -5.92), "GMMH": (23.72, -15.93), "GOBD": (14.67, -17.07),
    "GUCY": (9.58, -13.61), "GVAC": (16.74, -22.95), "GQNO": (18.31, -15.97), "KIAD": (38.94, -77.46),
    "KJFK": (40.64, -73.78), "KMIA": (25.79, -80.29), "KORD": (41.98, -87.90), "KBOS": (42.36, -71.01),
    "LEAL": (38.28, -0.56), "LEBL": (41.30, 2.08), "LEIB": (38.87, 1.37), "LEMD": (40.47, -3.56),
    "LEMO": (37.17, -5.62), "LEPA": (39.55, 2.74), "LEVC": (39.49, -0.48), "LEZL": (37.42, -5.89),
    "LEMG": (36.67, -4.50), "LFPG": (49.01, 2.55), "LFPO": (48.72, 2.38), "LFML": (43.44, 5.22),
    "LFLL": (45.73, 5.08), "LGAV": (37.94, 23.94), "LIMC": (45.63, 8.72), "LIPZ": (45.51, 12.35),
    "LIRF": (41.80, 12.24), "LMML": (35.86, 14.48), "LOWW": (48.11, 16.57), "LPAZ": (36.97, -25.17),
    "LPPD": (37.74, -25.70), "LPPR": (41.24, -8.68), "LPPT": (38.78, -9.14), "LPFR": (37.01, -7.97),
    "LPMA": (32.70, -16.77), "LSZH": (47.46, 8.55), "LSGG": (46.24, 6.11), "LTFM": (41.26, 28.74),
    "SAEZ": (-34.82, -58.54), "SBFZ": (-3.78, -38.53), "SBGL": (-22.81, -43.25), "SBGR": (-23.43, -46.47),
    "SBKP": (-23.01, -47.13), "SBRF": (-8.13, -34.92), "SCEL": (-33.39, -70.79), "SKBO": (4.70, -74.15),
    "SVMI": (10.60, -66.99), "OMDB": (25.25, 55.36), "OTHH": (25.27, 51.61), "OEJN": (21.68, 39.16),
    "HECA": (30.12, 31.41), "HAAB": (8.98, 38.80), "DAAG": (36.69, 3.22), "LYBE": (44.82, 20.31),
    "EVRA": (56.92, 23.97), "OMAA": (24.43, 54.65),
}
FIR_OF_UNIT = {"LPPC": "LPPC", "MADEIRA": "LPPC", "LECS": "LECM", "GCCC": "GCCC", "GMMM": "GMMM"}
REGION_UNIT = (("madeira", "MADEIRA"), ("lisboa", "LPPC"), ("sevilla", "LECS"), ("madrid", "LECS"),
               ("santiago", "LPPC"), ("canarias", "GCCC"), ("casablanca", "GMMM"), ("marrakech", "GMMM"))

# operator: telephony, registration pattern (? letter, # digit), Mode S block, type of flight, operator name
_OPS = {
    "RAM": ("ROYALAIR MAROC", "CN-R??", (0x020000, 0x023FFF), "S", "ROYAL AIR MAROC"),
    "RXP": ("ROYAL MAROC EXPRESS", "CN-C??", (0x020000, 0x023FFF), "S", "RAM EXPRESS"),
    "IBE": ("IBERIA", "EC-???", (0x340000, 0x37FFFF), "S", "IBERIA"),
    "IBS": ("IBEREXPRES", "EC-???", (0x340000, 0x37FFFF), "S", "IBERIA EXPRESS"),
    "AEA": ("EUROPA", "EC-???", (0x340000, 0x37FFFF), "S", "AIR EUROPA"),
    "VLG": ("VUELING", "EC-???", (0x340000, 0x37FFFF), "S", "VUELING"),
    "DLH": ("LUFTHANSA", "D-A???", (0x3C0000, 0x3FFFFF), "S", "DEUTSCHE LUFTHANSA"),
    "EWG": ("EUROWINGS", "D-A???", (0x3C0000, 0x3FFFFF), "S", "EUROWINGS"),
    "CFG": ("CONDOR", "D-A???", (0x3C0000, 0x3FFFFF), "N", "CONDOR"),
    "TUI": ("TUI JET", "D-A???", (0x3C0000, 0x3FFFFF), "N", "TUIFLY"),
    "TAP": ("AIR PORTUGAL", "CS-T??", (0x490000, 0x497FFF), "S", "TAP AIR PORTUGAL"),
    "SAT": ("SATA", "CS-T??", (0x490000, 0x497FFF), "S", "SATA AIR ACORES"),
    "RZO": ("AIR AZORES", "CS-T??", (0x490000, 0x497FFF), "S", "AZORES AIRLINES"),
    "KLM": ("KLM", "PH-???", (0x480000, 0x487FFF), "S", "KLM"),
    "TRA": ("TRANSAVIA", "PH-???", (0x480000, 0x487FFF), "N", "TRANSAVIA"),
    "EZY": ("EASY", "G-E???", (0x400000, 0x43FFFF), "S", "EASYJET"),
    "BAW": ("SPEEDBIRD", "G-????", (0x400000, 0x43FFFF), "S", "BRITISH AIRWAYS"),
    "EXS": ("CHANNEX", "G-J???", (0x400000, 0x43FFFF), "N", "JET2"),
    "TOM": ("TOMSON", "G-T???", (0x400000, 0x43FFFF), "N", "TUI AIRWAYS"),
    "AFR": ("AIRFRANS", "F-G???", (0x380000, 0x3BFFFF), "S", "AIR FRANCE"),
    "TVF": ("FRANCE SOLEIL", "F-H???", (0x380000, 0x3BFFFF), "N", "TRANSAVIA FRANCE"),
    "RYR": ("RYANAIR", "EI-???", (0x4CA000, 0x4CAFFF), "S", "RYANAIR"),
    "SWR": ("SWISS", "HB-J??", (0x4B0000, 0x4B7FFF), "S", "SWISS"),
    "ARG": ("ARGENTINA", "LV-???", (0xE00000, 0xE3FFFF), "S", "AEROLINEAS ARGENTINAS"),
    "LAN": ("LAN CHILE", "CC-B??", (0xE80000, 0xE80FFF), "S", "LATAM CHILE"),
    "GLO": ("GOL TRANSPORTE", "PR-G??", (0xE40000, 0xE7FFFF), "S", "GOL"),
    "AZU": ("AZUL", "PR-A??", (0xE40000, 0xE7FFFF), "S", "AZUL"),
    "TAM": ("TAM", "PT-M??", (0xE40000, 0xE7FFFF), "S", "LATAM BRASIL"),
    "AVA": ("AVIANCA", "N###AV", (0xA00000, 0xAFFFFF), "S", "AVIANCA"),
    "NOZ": ("NORDIC", "LN-N??", (0x478000, 0x47FFFF), "S", "NORWEGIAN"),
    "NAX": ("NOR SHUTTLE", "LN-K??", (0x478000, 0x47FFFF), "S", "NORWEGIAN AIR SHUTTLE"),
    "SAS": ("SCANDINAVIAN", "SE-R??", (0x4A8000, 0x4AFFFF), "S", "SAS"),
    "BTI": ("AIRBALTIC", "YL-A??", (0x502C00, 0x502FFF), "S", "AIRBALTIC"),
    "UAE": ("EMIRATES", "A6-E??", (0x896000, 0x896FFF), "S", "EMIRATES"),
    "ETD": ("ETIHAD", "A6-B??", (0x896000, 0x896FFF), "S", "ETIHAD"),
    "QTR": ("QATARI", "A7-B??", (0x06A000, 0x06AFFF), "S", "QATAR AIRWAYS"),
    "SVA": ("SAUDIA", "HZ-A??", (0x710000, 0x717FFF), "S", "SAUDIA"),
    "MSR": ("EGYPTAIR", "SU-G??", (0x010000, 0x017FFF), "S", "EGYPTAIR"),
    "ETH": ("ETHIOPIAN", "ET-A??", (0x040000, 0x040FFF), "S", "ETHIOPIAN AIRLINES"),
    "DAH": ("AIR ALGERIE", "7T-V??", (0x0A0000, 0x0A7FFF), "S", "AIR ALGERIE"),
    "BEL": ("BEELINE", "OO-S??", (0x448000, 0x44FFFF), "S", "BRUSSELS AIRLINES"),
    "JAF": ("BEAUTY", "OO-J??", (0x448000, 0x44FFFF), "N", "TUI FLY BELGIUM"),
    "ASL": ("AIR SERBIA", "YU-A??", (0x4C0000, 0x4C7FFF), "S", "AIR SERBIA"),
    "AMC": ("AIR MALTA", "9H-A??", (0x4D2000, 0x4D23FF), "S", "AIR MALTA"),
    "RMF": ("ROYAL MOROCCAN", "CN-A??", (0x020000, 0x023FFF), "M", "ROYAL MOROCCAN AIR FORCE"),
}
_TYPE_PAX = {"A319": 140, "A320": 174, "A20N": 180, "A321": 200, "A21N": 210, "B737": 140, "B738": 186,
             "B739": 189, "B38M": 186, "E190": 100, "E75L": 78, "A332": 260, "A333": 290, "A343": 270, "A359": 320,
             "B772": 300, "B773": 360, "B77W": 380, "B788": 250, "B789": 290, "A388": 500, "B748": 380, "C550": 7}
_MODERN = {"A20N", "A21N", "B38M", "B788", "B789", "A359", "B77W", "A388", "B748", "A339", "A35K", "B39M"}
_ICAO_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _h(*parts) -> int:
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _gc_nm(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 60.0


def hhmm(minutes: float) -> str:
    m = int(round(minutes)) % (24 * 60)
    return f"{m // 60:02d}{m % 60:02d}"


def parse_hhmm(s, default: float = 12 * 60) -> float:
    """'14:05' / '1405' -> minutes of the day."""
    s = str(s or "").replace(":", "").strip()
    if not re.fullmatch(r"\d{3,4}", s):
        return default
    s = s.zfill(4)
    return int(s[:2]) * 60 + int(s[2:])


def operator(cs: str) -> Optional[str]:
    m = re.match(r"^([A-Z]{3})\d", cs)
    return m.group(1) if m else None


def _registration(pattern: str, seed: int) -> str:
    out = []
    for ch in pattern:
        if ch == "?":
            out.append(_ICAO_ALPHA[seed % len(_ICAO_ALPHA)]); seed //= len(_ICAO_ALPHA)
        elif ch == "#":
            out.append(str(seed % 10)); seed //= 10
        else:
            out.append(ch)
    return "".join(out)


def surveillance(equipment: str) -> dict:
    """What the radar gets from the transponder, from item 10b: Mode A/C, Mode S ELS, Mode S EHS, ADS-B."""
    sur = equipment.split("/", 1)[1] if "/" in equipment else ""
    codes = set(re.findall(r"[A-Z]\d?", sur))
    letters = {c for c in codes if len(c) == 1}
    ehs = bool(letters & {"H", "L"})
    els = ehs or bool(letters & {"E", "I", "P", "S", "X"})
    return {"mode_c": bool(letters & {"A", "C"}) or els, "els": els, "ehs": ehs,
            "adsb": bool(codes & {"B1", "B2", "U1", "U2", "V1", "V2"}),
            "text": "Mode S EHS" if ehs else "Mode S ELS" if els else "Mode A/C" if letters & {"A", "C"} else "none"}


def _equipment(ac, ops: Optional[tuple], seed: int) -> str:
    if ac.military:
        base = "SDFGHIUY" if not ac.rvsm_approved else "SDFGHIRUWY"
        return base + ("/C" if seed % 3 == 0 else "/S")
    t = ac.icao
    if t in ("C550", "E75L", "E190", "E195"):
        com = "SDE2FGHIRWY" if ac.rvsm_approved else "SDE2FGHIRY"
        return com + ("/LB1" if seed % 2 else "/S")
    if t in _MODERN:
        return "SDE1E2E3FGHIJ1J3J4J5M1P2RWXYZ/LB1D1"
    old = (ops is not None and ops[3] == "N" and seed % 4 == 0) or seed % 9 == 0
    com = "SDE2E3FGHIJ1RWY"
    if not ac.rvsm_approved:
        com = com.replace("W", "")
    return com + ("/S" if old else "/LB1")


def _pbn(equipment: str, military: bool) -> str:
    if military or "R" not in equipment.split("/")[0]:
        return ""
    return "A1B1C1D1L1O1S2T1" if "J4" in equipment else "A1B1C1D1O1S1"


def _per(perf) -> str:
    return "D" if perf.mtow >= 150000 else "C" if perf.mtow > 7000 else "B"


def unit_for_region(region: str) -> Optional[str]:
    r = (region or "").lower()
    for k, u in REGION_UNIT:
        if k in r:
            return u
    return None


def adjacent_units(asp, names: List[str], idx_entry: int, idx_exit: int, waypoints) -> Tuple[str, str]:
    """(previous unit, next unit) for a route: the Agadir sector not controlled on the other side of the boundary,
    else the unit of the first neighbouring-region point before the entry / after the exit."""
    from .geometry import bearing
    if not asp.sectors:
        return "ADJ", "ADJ"

    def side(i0: int, step: int) -> str:
        a, b = names[i0], names[i0 + step] if 0 <= i0 + step < len(names) else None
        pa = waypoints[a]
        if b is not None:
            brg = bearing(pa, waypoints[b])
        else:
            prv = waypoints[names[i0 - step]] if 0 <= i0 - step < len(names) else None
            brg = bearing(prv, pa) if prv is not None else 0.0
        probe = (pa[0] + 5 * math.sin(math.radians(brg)), pa[1] + 5 * math.cos(math.radians(brg)))
        sec = asp.sector_of(probe)
        if sec and sec not in asp.controlled:
            return sec
        rng = range(i0 + step, len(names) if step > 0 else -1, step)
        for j in rng:
            info = asp.point_info.get(names[j], {})
            if info.get("external"):
                u = unit_for_region(info.get("region", ""))
                if u:
                    return u
        # by geography: north = Casablanca, south-west = Canarias, west = Lisboa (oceanic)
        if probe[1] > waypoints[names[i0]][1] + 2 and abs(probe[0] - waypoints[names[i0]][0]) < 4:
            return "GMMM"
        return "GCCC" if probe[1] < waypoints[names[i0]][1] else "GMMM"
    return side(idx_entry, -1), side(idx_exit, 1)


# ------------------------------------------------------------------ building the plan
def complete(ac, spec: dict, asp, start_min: float, tas: float, route_names: List[str], filed: List[str]) -> dict:
    """Fill in the aircraft's flight plan (ac.flight_plan already has the basics from the scenario)."""
    fp = dict(ac.flight_plan)
    given = {k.lower(): v for k, v in (spec.get("fpl") or {}).items()}
    cs = ac.callsign
    ops_code = operator(cs)
    ops = _OPS.get(ops_code or "")
    seed = _h(cs, ac.icao)
    # departure / destination: the scenario's, else plausible ones for the direction of flight
    dep, dest = fp.get("departure", "ZZZZ"), fp.get("destination", "ZZZZ")
    if dep == "ZZZZ" or dest == "ZZZZ":
        north = ["EGLL", "EHAM", "EDDF", "LFPG", "LEMD", "LPPT", "LSZH", "EBBR", "EGKK", "LEBL", "EIDW", "EDDM"]
        south = ["GCLP", "GCTS", "GCRR", "GCFV", "GOBD", "GVAC", "DNMM", "DIAP", "SBGR", "GMAD", "GMMX", "GMMN"]
        if dep == "ZZZZ":
            pool = north if ac.southbound else south
            dep = given.get("dep") or pool[seed % len(pool)]
        if dest == "ZZZZ":
            pool = south if ac.southbound else north
            dest = given.get("dest") or pool[(seed // 7) % len(pool)]
            if dest == dep:
                dest = pool[((seed // 7) + 1) % len(pool)]
    # SSR code: given, else from the airspace's code block (octal, never a special code)
    ssr = str(given.get("ssr") or "").strip().upper().lstrip("A")
    if not re.fullmatch(r"[0-7]{4}", ssr or ""):
        base = 0o2000 + (seed % 0o3700)
        ssr = f"{base:04o}"
        if ssr in ("2000", "7000", "7500", "7600", "7700", "1200", "1000"):
            ssr = "2" + ssr[1:3] + "1"
    # registration, Mode S address
    reg = str(given.get("reg") or "")
    if not reg:
        if ops:
            reg = _registration(ops[1], seed // 13)
        elif re.fullmatch(r"[A-Z]{5}", cs) and cs.startswith(("CN", "EC", "CS", "PH")):
            reg = cs[:2] + "-" + cs[2:]
        elif cs.startswith("N") and re.fullmatch(r"N\d+[A-Z]{0,2}", cs):
            reg = cs
        else:
            reg = _registration("CN-??" + "?", seed // 13) if ac.military else _registration("N###??", seed // 13)
    block = ops[2] if ops else ((0xA00000, 0xAFFFFF) if reg.startswith("N") else (0x020000, 0x023FFF))
    code = str(given.get("code") or f"{block[0] + (seed // 31) % (block[1] - block[0] + 1):06X}")
    # equipment: the scenario's (already checked against RVSM), else by type and operator
    equipment = ac.equipment if spec.get("equipment") or given.get("equipment") else _equipment(ac, ops, seed // 3)
    if given.get("equipment"):
        equipment = str(given["equipment"]).upper()
    if ac.rvsm_approved and "W" not in equipment.split("/")[0]:
        equipment = equipment.split("/")[0] + "W" + ("/" + equipment.split("/")[1] if "/" in equipment else "")
    ac.equipment = equipment
    # times: EOBT so that the aircraft is at its entry point at the planned time; total EET to the destination
    entry_pt = asp.waypoints.get(ac.entry_wp)
    exit_pt = asp.waypoints.get(ac.planned_exit)
    ll_entry = asp.to_latlon(*entry_pt) if (entry_pt and asp.projection is not None) else None
    ll_exit = asp.to_latlon(*exit_pt) if (exit_pt and asp.projection is not None) else None
    spd_kt = max(tas, 250.0)
    if ll_entry and dep in AERODROMES:
        to_entry_min = _gc_nm(AERODROMES[dep], ll_entry) * 1.04 / spd_kt * 60 + 12
    else:
        to_entry_min = 35 + seed % 150
    in_sector_min = ac.planned_length_nm / spd_kt * 60 if ac.planned_length_nm else 25
    if ll_exit and dest in AERODROMES:
        after_min = _gc_nm(ll_exit, AERODROMES[dest]) * 1.04 / spd_kt * 60 + 12
    else:
        after_min = 30 + (seed // 11) % 120
    if not ll_entry and dep in AERODROMES and dest in AERODROMES:     # no geography (training sector): share of the trip
        trip = _gc_nm(AERODROMES[dep], AERODROMES[dest]) * 1.05 / spd_kt * 60 + 25
        frac = 0.3 + (seed % 41) / 100
        to_entry_min = max(20.0, frac * trip - in_sector_min / 2)
        after_min = max(20.0, trip - to_entry_min - in_sector_min)
    boundary_min = start_min + ac.boundary_time / 60.0
    eobt_min = parse_hhmm(given["eobt"]) if given.get("eobt") else boundary_min - to_entry_min - 15
    eet_total = to_entry_min + in_sector_min + after_min
    # alternate: nearest other aerodrome of the table
    altn = str(given.get("altn") or "")
    if not altn and dest in AERODROMES:
        cand = sorted((k for k in AERODROMES if k != dest and k != dep), key=lambda k: _gc_nm(AERODROMES[k], AERODROMES[dest]))
        altn = cand[0] if cand else ""
    # item 18
    fir_eet = []
    prev_fir, next_fir = FIR_OF_UNIT.get(getattr(ac, "prev_unit", ""), "GMMM"), FIR_OF_UNIT.get(getattr(ac, "next_unit", ""), "")
    if asp.projection is not None:
        if dep[:2] != "GM":
            fir_eet.append(f"GMMM{hhmm(to_entry_min - (20 if prev_fir == 'GMMM' else 0))}")
        if next_fir and next_fir != "GMMM":
            fir_eet.append(f"{next_fir}{hhmm(to_entry_min + in_sector_min)}")
    sel = given.get("sel") or "".join("ABCDEFGHJKLMPQRS"[(seed >> (4 * i)) % 16] for i in range(4))
    rmk = str(given.get("rmk") or fp.get("remarks") or "").replace("STS/STATE", "").strip()
    if not rmk and not ac.military and seed % 5 == 0:
        rmk = "TCAS"
    sts = str(given.get("sts") or ("STATE" if ac.military else ""))
    item18 = []
    if sts:
        item18.append(f"STS/{sts}")
    pbn = given.get("pbn", _pbn(equipment, ac.military))
    if pbn:
        item18.append(f"PBN/{pbn}")
    if ac.rvsm_status != "approved" and not ac.military:
        item18.append("RMK/NON RVSM")
    dof = given.get("dof") or time.strftime("%y%m%d", time.gmtime())
    item18 += [f"DOF/{dof}", f"REG/{reg.replace('-', '')}"]
    if fir_eet:
        item18.append("EET/" + " ".join(fir_eet))
    if surveillance(equipment)["els"]:
        item18.append(f"SEL/{sel}")
    item18.append(f"CODE/{code}")
    opr = given.get("opr") or (ops[4] if ops else "")
    if opr:
        item18.append(f"OPR/{opr}")
    item18.append(f"PER/{_per(ac.perf)}")
    if ac.formation:
        item18.append(f"TYP/{ac.formation['types']}")
    if rmk and rmk != "NON RVSM":
        item18.append(f"RMK/{rmk}")
    pax = _TYPE_PAX.get(ac.icao, 120)
    pob = int(given.get("pob") or (2 if ac.military else max(3, int(pax * (0.62 + (seed % 33) / 100)) + 6)))
    endurance = eet_total + 75 + (seed % 4) * 10
    fp.update({
        "callsign": cs, "ssr": ssr, "flight_rules": "I",
        "flight_type": given.get("type") or ("M" if ac.military else ("G" if not ops else ops[3])),
        "number": ac.formation["size"] if ac.formation else 1,
        "departure": dep, "destination": dest, "eobt": hhmm(eobt_min), "eet": hhmm(eet_total),
        "alternate": altn, "equipment": equipment, "registration": reg, "mode_s_address": code,
        "selcal": sel if surveillance(equipment)["els"] else "", "operator": opr,
        "telephony": ops[0] if ops else ("" if ac.military else reg.replace("-", "")),
        "rfl": fp.get("cruise"), "item18": " ".join(item18),
        "item19": f"E/{hhmm(endurance)} P/{pob:03d} R/{'UE' if ac.military else 'E'} S/M J/L",
        "pob": pob, "surveillance": surveillance(equipment)["text"],
        "prev_unit": getattr(ac, "prev_unit", ""), "next_unit": getattr(ac, "next_unit", ""),
        "eto_entry": hhmm(boundary_min), "entry_point": ac.entry_wp, "exit_point": ac.planned_exit,
        "remarks": rmk,
    })
    if fp.get("rvsm") is None:
        fp["rvsm"] = "approved (W)" if ac.rvsm_approved else "non-RVSM"
    fp["message"] = message(fp)
    return fp


def message(fp: dict) -> str:
    """The ICAO FPL message, as it would come on the flight-data system."""
    num = f"{fp['number']}" if fp.get("number", 1) > 1 else ""
    altn = f" {fp['alternate']}" if fp.get("alternate") else ""
    return (f"(FPL-{fp['callsign']}/A{fp['ssr']}-{fp['flight_rules']}{fp['flight_type']}\n"
            f"-{num}{fp['aircraft_type']}/{fp['wake']}-{fp['equipment']}\n"
            f"-{fp['departure']}{fp['eobt']}\n"
            f"-{fp['speed']}{fp['cruise']} {fp['route']}\n"
            f"-{fp['destination']}{fp['eet']}{altn}\n"
            f"-{fp['item18']}\n"
            f"-{fp['item19']})")


def fields(fp: dict) -> List[List[str]]:
    """Decoded items for the flight-plan window."""
    return [["7", "Aircraft identification / SSR", f"{fp.get('callsign')}  ·  A{fp.get('ssr')}"],
            ["8", "Flight rules / type of flight", f"{fp.get('flight_rules')} / {fp.get('flight_type')}"],
            ["9", "Number / type / wake", f"{fp.get('number', 1)} × {fp.get('aircraft_type')} / {fp.get('wake')}"],
            ["10", "Equipment / surveillance", f"{fp.get('equipment')}  ({fp.get('surveillance')})"],
            ["13", "Departure / EOBT", f"{fp.get('departure')} {fp.get('eobt')}"],
            ["15", "Speed / level / route", f"{fp.get('speed')} {fp.get('cruise')}  {fp.get('route')}"],
            ["16", "Destination / EET / alternate", f"{fp.get('destination')} {fp.get('eet')}  {fp.get('alternate') or ''}"],
            ["18", "Other information", fp.get("item18", "")],
            ["19", "Supplementary", fp.get("item19", "")]]


# ------------------------------------------------------------------ Mode S downlinked data
def mode_s(ac, variation_deg: float = 0.0) -> Optional[dict]:
    """What the radar decodes from the transponder (None: no Mode S). EHS gives the downlinked aircraft parameters."""
    sv = surveillance(ac.equipment)
    if not sv["els"]:
        return None
    out = {"address": (ac.flight_plan or {}).get("mode_s_address"), "aircraft_id": ac.callsign, "ehs": sv["ehs"],
           "fl25": round(ac.alt_ft / 25) * 25}
    if sv["ehs"]:
        from . import atmosphere as atm
        tas_ms = max(ac.tas, 100.0) * atm.KT
        rate = math.degrees(atm.G0 * math.tan(math.radians(ac.bank)) / tas_ms)
        out.update({
            "selected_fl": int(round(ac.cleared_alt_ft / 100.0)),
            "baro_setting": "STD" if ac.alt_ft >= 6000 else "QNH",
            "mag_heading": int(round((ac.hdg - variation_deg) % 360)) or 360,
            "ias_kt": int(round(ac.cas())), "mach": round(ac.mach(), 3), "tas_kt": int(round(ac.tas)),
            "gs_kt": int(round(ac.gs)), "track_deg": int(round(ac.trk)) % 360 or 360,
            "roll_deg": round(ac.bank, 1), "track_rate_deg_s": round(rate, 2),
            "baro_rate_fpm": int(round(ac.vs_fpm, -1)),
        })
    return out
