"""Flight plans, MTCD, probe, special-track alerts, early assume.   python -m tests.test_atctools"""
import re
import warnings

warnings.filterwarnings("ignore")
from atcsim import atctools, flightplan  # noqa: E402
from atcsim.scenario import build_world, load_scenario  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


# ---- flight plans: every aircraft, complete, unique SSR codes, correlated
for name in ("s10_busy_mixed", "c01_cta_moderate_wx"):
    w = build_world(load_scenario(name))
    fps = [a.flight_plan for a in w.aircraft.values()]
    ok(all(re.fullmatch(r"[0-7]{4}", f["ssr"]) for f in fps), f"{name}: an SSR code for each of {len(fps)} flights")
    ok(len({f["ssr"] for f in fps}) == len(fps), f"{name}: SSR codes unique")
    ok(all(f["departure"] != "ZZZZ" and f["destination"] != "ZZZZ" for f in fps), f"{name}: departure and destination")
    ok(all(f["message"].startswith(f"(FPL-{f['callsign']}/A{f['ssr']}-I") and "DOF/" in f["message"] and "REG/" in f["message"]
           for f in fps), f"{name}: ICAO FPL messages")
    ok(all(f["eobt"] and f["eet"] and f["registration"] and f["mode_s_address"] for f in fps), f"{name}: EOBT, EET, REG, Mode S")
    ok(all(("W" in f["equipment"].split("/")[0]) == (a.rvsm_approved) for a, f in zip(w.aircraft.values(), fps)),
       f"{name}: equipment W = RVSM approval")
w = build_world(load_scenario("c01_cta_moderate_wx"))
units = {a.next_unit for a in w.aircraft.values()}
ok(units <= {"GMMM", "LPPC", "GCCC", "LECS", "MADEIRA"} and len(units) >= 3, f"next units {sorted(units)}")
ok(flightplan.surveillance("SDFGRY/S") == {"mode_c": True, "els": True, "ehs": False, "adsb": False, "text": "Mode S ELS"},
   "surveillance: ELS")
ok(flightplan.surveillance("SDE2E3FGHIJ1RWY/LB1")["ehs"], "surveillance: EHS (L)")
ok(not flightplan.surveillance("SDFGHIUY/C")["els"], "surveillance: Mode C only")
spec = load_scenario("s01_basic_crossing")
spec["aircraft"][0]["fpl"] = {"ssr": "4321", "reg": "CN-ROA", "eobt": "0915", "altn": "GMMX", "rmk": "TEST FLIGHT"}
w = build_world(spec)
f = w.aircraft[spec["aircraft"][0]["callsign"]].flight_plan
ok(f["ssr"] == "4321" and f["registration"] == "CN-ROA" and f["eobt"] == "0915" and f["alternate"] == "GMMX"
   and "RMK/TEST FLIGHT" in f["item18"], "scenario values kept (fpl object)")

# ---- state: Mode S, MTCD, special tracks, flight-plan views
w = build_world(load_scenario("c01_cta_moderate_wx"))
for _ in range(600):
    w.step(1)
st = w.state()
ehs = [a for a in st["aircraft"] if a["mode_s"] and a["mode_s"]["ehs"]]
els = [a for a in st["aircraft"] if a["mode_s"] and not a["mode_s"]["ehs"]]
ok(ehs and all({"selected_fl", "mag_heading", "ias_kt", "mach", "roll_deg", "baro_rate_fpm"} <= set(a["mode_s"]) for a in ehs),
   f"Mode S EHS parameters ({len(ehs)} EHS, {len(els)} ELS only)")
ok("utc" in st and re.fullmatch(r"\d\d:\d\d", st["utc"]), f"UTC clock {st['utc']}")
ok(isinstance(st["mtcd"], list) and isinstance(st["special"], dict), f"MTCD ({len(st['mtcd'])}) and special tracks in the state")
ok(all(c["time_to_conflict_s"] <= 1200 and c["min_horizontal_nm"] < c["required_horizontal_nm"] for c in st["mtcd"]),
   "MTCD conflicts within 20 min and below the minimum")
a = next(x for x in w.aircraft.values() if x.active and x.assumed)
d = atctools.flight_detail(w, a.callsign)
ok(d["etos"] and d["coordination"]["exit"]["to"] == a.next_unit and "FPL-" in d["message"], "flight plan detail with estimates")
ok(len(atctools.flight_list(w)) == len(w.aircraft), "flight list: every flight")
lv = [int(round(a.alt_ft / 100)) + k for k in (-20, -10, 10, 20)]
p = atctools.probe(w, a.callsign, levels=lv)
ok(set(p["levels"]) == {str(x) for x in lv}, f"level probe {a.callsign}: " + ", ".join(f"{k}:{len(v)}" for k, v in p["levels"].items()))

# MTCD sees a conflict the controller creates by entering a level, long before the STCA
w = build_world(load_scenario("s01_basic_crossing"))
w.multi = True
for _ in range(120):
    w.step(1)
cs = sorted(c for c in w.aircraft if w.aircraft[c].active)
conf = atctools.mtcd(w)
ok(any(set(c["aircraft"]) == {"RAM201", "AFR452"} and c["time_to_conflict_s"] > 180 for c in conf),
   "MTCD: RAM201 × AFR452 crossing, beyond the STCA horizon: " + str([(c['aircraft'], c['time_to_conflict_s']) for c in conf]))
w.enter("AFR452", {"cfl": 360})
w._mtcd_cache = None
ok(not any(set(c["aircraft"]) == {"RAM201", "AFR452"} for c in atctools.mtcd(w)), "MTCD uses the level the controller entered")

# special tracks: level bust, route deviation (connected exercise)
w = build_world(load_scenario("s10_busy_mixed"))
w.multi = True
for _ in range(300):
    w.step(1)
a = next(x for x in w.aircraft.values() if x.active and x.assumed)
fl = int(round(a.alt_ft / 100))
w.issue({"callsign": a.callsign, "type": "altitude", "fl": fl + 20}, by_pilot=True)
seen = set()
for _ in range(60):
    w.step(1)
    seen |= {t[0] for t in atctools.special_tracks(w).get(a.callsign, [])}
ok("LVL" in seen, f"level bust: {a.callsign} leaves FL{fl} without a clearance entered")
w.enter(a.callsign, {"cfl": fl + 20})
w.issue({"callsign": a.callsign, "type": "heading", "heading": int(a.hdg + 70) % 360 or 360}, by_pilot=True)
for _ in range(240):
    w.step(1)
    atctools.special_tracks(w)
tags = {t[0] for t in atctools.special_tracks(w).get(a.callsign, [])}
ok("RDEV" in tags and "LVL" not in tags, f"route deviation without a heading entered, no level bust once CFL entered: {tags}")
w.enter(a.callsign, {"heading": int(a.hdg)})
ok("RDEV" not in {t[0] for t in atctools.special_tracks(w).get(a.callsign, [])}, "no route deviation once the heading is entered")
w.enter(a.callsign, {"wx": "L20"})
ok(w.entries[a.callsign]["wx"] == "L20", "weather deviation entry")
mil = [t for c, ts in atctools.special_tracks(w).items() for t in ts if t[0] in ("MIL", "NRVSM")]
ok(isinstance(mil, list), f"special tracks MIL/NRVSM: {len(mil)}")

# any track can be assumed, even before it calls
w = build_world(load_scenario("c01_cta_moderate_wx"))
for _ in range(60):
    w.step(1)
pre = next(x for x in w.aircraft.values() if x.active and not x.on_frequency)
r = w.issue({"callsign": pre.callsign, "type": "assume"})
ok(r["status"] != "rejected" and pre.assumed, f"{pre.callsign} assumed before it calls")
for _ in range(900):
    w.step(1)
    if pre.on_frequency:
        break
ok(pre.on_frequency and pre.assumed and not any(q.callsign == pre.callsign and q.kind == "checkin" and q.status == "pending"
                                                for q in w.requests.values()), "it calls already assumed: nothing to answer")
print("atc tools checks passed")
