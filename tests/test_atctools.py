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

# MTCD look-ahead: set per exercise, shortens or widens the list, bounded 5-60 min
w = build_world(load_scenario("s01_basic_crossing"))
w.multi = True
for _ in range(120):
    w.step(1)
c20 = [c for c in atctools.mtcd(w) if set(c["aircraft"]) == {"RAM201", "AFR452"}]
t = c20[0]["time_to_conflict_s"]
short = max(300, (int(t) // 60 - 1) * 60)
atctools.set_look_ahead(w, short)
ok(atctools.look_ahead(w) == short and not any(set(c["aircraft"]) == {"RAM201", "AFR452"} for c in atctools.mtcd(w))
   and w.state()["mtcd_look_s"] == short, f"MTCD look-ahead {short // 60} min hides the conflict {t // 60:.0f} min away")
atctools.set_look_ahead(w, 2400)
ok(any(set(c["aircraft"]) == {"RAM201", "AFR452"} for c in atctools.mtcd(w)) and all(c["time_to_conflict_s"] <= 2400 for c in atctools.mtcd(w)),
   "MTCD look-ahead 40 min shows it again")
for bad in (60, 7200):
    try:
        atctools.set_look_ahead(w, bad); ok(False, f"look-ahead {bad} s refused")
    except ValueError:
        pass
ok(atctools.look_ahead(w) == 2400, "out-of-range look-ahead refused, setting kept")

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
# ---- three levels and OLDI: ABI, ACT, REV, refused REV close to the boundary, inbound messages
from atcsim import oldi  # noqa: E402
w = build_world(load_scenario("c01_cta_moderate_wx"))
w.multi = True
for _ in range(900):
    w.step(1)
kinds = {(m["dir"], m["type"]) for m in w.oldi}
ok({("out", "ABI"), ("out", "ACT"), ("in", "ABI"), ("in", "ACT")} <= kinds, f"OLDI messages {sorted(kinds)}")
ok(all(m["status"] == "LAM" for m in w.oldi if w.t - m["t"] > 3), "every message acknowledged (LAM)")
cv = oldi.coord_view(w)
cs = next(c for c, v in cv.items() if v["state"] == "act" and oldi.time_to_exit(w, w.aircraft[c]) > 400)
a = w.aircraft[cs]
ok(cv[cs]["xfl"] == cv[cs]["cfl"] and not cv[cs]["xfl_entered"], f"{cs}: exit level empty = cleared level F{cv[cs]['cfl']}")
w.enter(cs, {"xfl": cv[cs]["cfl"] - 20})
w.step(1)
rev = [m for m in oldi.messages(w, callsign=cs) if m["type"] == "REV"]
ok(rev and rev[-1]["level"] == cv[cs]["cfl"] - 20 and rev[-1]["unit"] == a.next_unit, f"REV to {a.next_unit}: {rev[-1]['text']}")
w.enter(cs, {"cfl": cv[cs]["cfl"] - 40})
w.step(1)
ok(oldi.coord_view(w)[cs]["xfl"] == cv[cs]["cfl"] - 20, "a new cleared level does not change an entered exit level")
w.enter(cs, {"xfl": ""})
w.step(1)
ok(oldi.messages(w, callsign=cs)[-1]["level"] == cv[cs]["cfl"] - 40, "exit level cleared: the cleared level is coordinated (REV)")
while oldi.time_to_exit(w, a) > oldi.REV_LIMIT_S - 20:
    w.step(1)
n = len(oldi.messages(w, callsign=cs))
w.enter(cs, {"xfl": cv[cs]["cfl"]})
w.step(1)
co = oldi.coord_view(w)[cs]
ok(len(oldi.messages(w, callsign=cs)) == n and co.get("manual"), "too close to the point: no REV, phone coordination: " + str(co.get("manual")))
w.enter(cs, {"coord": "phone"})
w.step(1)
ok(not oldi.coord_view(w)[cs].get("manual") and oldi.messages(w, callsign=cs)[-1]["type"] == "PHONE", "coordinated by phone: recorded, alert cleared")
d = atctools.flight_detail(w, cs)
ok(d["coordination"]["exit"]["level"] == cv[cs]["cfl"] and d["oldi"], "flight plan shows the exit level and the messages")
inb = [m for m in w.oldi if m["dir"] == "in" and m["type"] == "ACT"]
ok(all(m["unit"] in ("GMMM", "LPPC", "GCCC", "LECS", "MADEIRA") for m in inb), "inbound ACTs from the adjacent units")
# ---- re-routing: the route entered by the controller (system), the route flown (pseudo-pilot)
w = build_world(load_scenario("c01_cta_moderate_wx"))
w.multi = True
for _ in range(400):
    w.step(1)
a = next(x for x in w.aircraft.values() if x.active and x.assumed and len(x.route[x.next_index:]) >= 2)
pos = [a.x + 20, a.y - 10]
w.enter(a.callsign, {"route": [pos, a.route[-1]]})
rt = w.entries[a.callsign]["route"]
ok(re.fullmatch(r"\d{4}[NS]\d{5}[EW]", rt[0]) and rt[0] in w.dyn_points, f"a position becomes a named point: {rt}")
ok(oldi.coord_view(w)[a.callsign]["route_ahead"][:2] == rt, "system route ahead = the route entered")
for _ in range(150):
    w.step(1)
ok("RDEV" in {t[0] for t in atctools.special_tracks(w).get(a.callsign, [])}, "route deviation while the aircraft does not fly the route entered")
r = w.issue({"callsign": a.callsign, "type": "reroute", "points": rt}, by_pilot=True)
for _ in range(10):
    w.step(1)
ok(r["status"] != "rejected" and a.route[a.next_index] == rt[0], f"the pilot flies it: {a.route[a.next_index:]}")
for _ in range(200):
    w.step(1)
    atctools.special_tracks(w)
ok("RDEV" not in {t[0] for t in atctools.special_tracks(w).get(a.callsign, [])}, "no route deviation once flying the route entered")
ok(w.issue({"callsign": a.callsign, "type": "reroute", "points": ["NOWHERE"]}, by_pilot=True)["status"] == "rejected", "unknown point refused")
from atcsim import pilot as pilot_mod  # noqa: E402
w.auto_wx = False
nav = a.nav_mode
pilot_mod._self_deviate(w, a, 5.0, None)
ok(a.nav_mode == nav, "automatic weather deviations switched off: the aircraft does not deviate by itself")
# ---- weather in exercises: no automatic deviation; an unanswered request is a task not handled; an aircraft
# dealing with weather is not counted as penetrating it until it reports clear of weather
w = build_world(load_scenario("s11_weather_deviation_into_traffic"))
w.multi = True
w.auto_wx = False
for _ in range(1500):
    w.step(1)
mw = w.metrics()["weather"]
ok(mw["requests_unanswered_before_weather"] and mw["deviations_without_clearance"] == 0,
   f"unanswered weather request recorded, no deviation by itself: {mw['requests_unanswered_before_weather']}")
cs_req = mw["requests_unanswered_before_weather"][0]["callsign"]
ok(cs_req not in [p["callsign"] for p in mw["penetrations"]] and cs_req in [p["callsign"] for p in mw["not_counted_while_deviating"]],
   f"{cs_req} asked for a deviation: its weather kept apart, not a penetration")
st = w.state()
ac = w.aircraft[cs_req]
if ac.active:
    ok(any(x["callsign"] == cs_req and x["wx_handling"] for x in st["aircraft"]), "state: dealing with weather")
r = w.issue({"callsign": cs_req, "type": "wx_clear"}, by_pilot=True)
ok(r["status"] == "executed" and not w.wx_handling(ac), "pilot reports clear of weather")
# ---- declared capacities and traffic density
from atcsim import capacity, airspace as asp_mod  # noqa: E402
from atcsim.scenario import ratings  # noqa: E402
ag = asp_mod.get("agadir")
ok(capacity.capacity(ag, ["WN", "WS"]) == 48 and capacity.capacity(ag, ["ON", "OS"]) == 51
   and capacity.capacity(ag, ["OS", "ON", "WN", "WS"]) == 47 and capacity.capacity(ag, ["WS"]) == 43,
   "declared capacities: WW 48, OCE 51, WW-OCE 47, WS 43")
ok([capacity.density_level(r) for r in (0.4, 0.6, 0.8, 1.0, 1.12)] == [1, 2, 3, 4, 5], "density bands")
p1 = load_scenario("p01_ws_two_merges")
tl = capacity.traffic_load(p1)
ok(tl["capacity"] == 43 and tl["unit_name"] == "WS" and 0.45 <= tl["ratio"] <= 0.65 and tl["density"] == 2,
   f"p01: about half the WS capacity ({tl})")
r1 = ratings(p1)
ok(r1["density"] == 2 and not r1["density_estimated"] and r1["complexity"] == 2 and r1["capacity"] == 43,
   "p01 ratings set by the builder")
loads = [load_scenario(n)["traffic_load"]["ratio"] for n in
         ("p01_ws_two_merges", "p02_wn_merges_and_climbs", "p03_ocn_isolated_storms", "p04_ocs_embedded_unable_rvsm",
          "p05_ws_storms_arrivals", "p06_wn_capacity_sequencing")]
ok(loads == sorted(loads) and loads[-1] <= 1.05, f"progression: traffic rises step by step {loads}")
for n in ("w01_west_scattered_cb", "w02_west_squall_line", "o01_oceanic_light_wx", "o02_oceanic_embedded_cb",
          "c01_cta_moderate_wx", "c02_cta_severe_wx"):
    r = load_scenario(n)["traffic_load"]["ratio"]
    ok(0.9 <= r <= 1.15, f"{n}: {r:.0%} of the declared capacity (slight overshoot at most)")
try:                                                       # the documented JSON Schema accepts the whole library
    import glob as _glob, json as _json, jsonschema
    from atcsim.docs_gen import SCHEMA
    _v = jsonschema.Draft202012Validator(SCHEMA)
    bad = [f for f in _glob.glob("scenarios/*.json") if list(_v.iter_errors(_json.load(open(f))))]
    ok(not bad, f"every library scenario matches the JSON Schema {bad}")
    ok(list(_v.iter_errors({"name": "x", "aircraft": [{"callsign": "A1", "type": "B738", "route": ["N1", "S1"], "fl": 350}]})),
       "the schema refuses an aircraft without timing")
except ImportError:
    print("  (jsonschema not installed: schema check skipped)")
print("atc tools checks passed")
