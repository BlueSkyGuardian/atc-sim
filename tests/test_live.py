"""Live actions: pop-up aircraft, emergencies, pilot requests, areas; kept through a rewind and a restart.
   python -m tests.test_live"""
import tempfile
import warnings

warnings.filterwarnings("ignore")
from atcsim import atctools, live  # noqa: E402
from atcsim.scenario import build_world, load_scenario  # noqa: E402
from atcsim.session import Session  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


def raises(f, *a, **k):
    try:
        f(*a, **k)
    except ValueError as e:
        return str(e)
    return None


w = build_world(load_scenario("p01_ws_two_merges"))
w.multi = True
for _ in range(20):
    w.step(1)

# ---- pop-up aircraft
r = live.add_aircraft(w, {"callsign": "TST101", "type": "B738", "fl": 340, "route": ["NIPRI", "ROLRO"], "appear": "now"})
for _ in range(3):
    w.step(1)
a = w.aircraft["TST101"]
ok(a.active and r["exit"] == "ROLRO" and a.flight_plan["ssr"] not in ("7700", "7600", "2000"), f"pop-up now: active, {r['entry']} → {r['exit']}, SSR {r['ssr']}")
r2 = live.add_aircraft(w, {"callsign": "TST202", "type": "A320", "fl": 360, "route": ["IBLIV", "TERTO"], "appear": 300})
ok(not w.aircraft["TST202"].active and abs(w.aircraft["TST202"].boundary_time - (w.t + 300)) < 30, "pop-up in 5 min: enters at IBLIV about T+5 min from now")
ok("already" in raises(live.add_aircraft, w, {"callsign": "TST101", "route": ["NIPRI", "ROLRO"]}), "no duplicate callsign")
ok("unknown point" in raises(live.add_aircraft, w, {"callsign": "TST303", "route": ["NIPRI", "XXXXX"]}), "unknown point refused")
ok("unknown aircraft type" in raises(live.add_aircraft, w, {"callsign": "TST304", "type": "Z999", "route": ["NIPRI", "ROLRO"]}), "unknown type refused")

# ---- emergencies
cs = next(c for c, x in w.aircraft.items() if x.active and x.alt_ft > 30000 and c != "TST101")
e = live.emergency(w, cs, "descent", why="rapid decompression")
ok(w.aircraft[cs].snapshot()["squawk"] == "7700" and e["descending_to"] == 100 and w.aircraft[cs].cleared_alt_ft < 31000 or
   any(p["instruction"].get("fl") == 100 for p in w.pending), "emergency descent: 7700, the aircraft starts down to FL100")
tags = atctools.special_tracks(w)[cs]
ok(tags[0][0] == "EMG" and tags[0][1] == "alert", "EMG alert on the label")
ok(any(q.kind == "emergency" and q.phrase().startswith("MAYDAY") for q in w.requests.values() if q.callsign == cs), "the MAYDAY call to answer (To do, radio log)")
m = live.emergency(w, "TST101", "medical")
ok(m["aerodrome"] in w.waypoints and any(q.type == "direct" and q.value == m["aerodrome"] for q in w.requests.values() if q.callsign == "TST101"),
   f"medical: PAN PAN and a request direct to {m['aerodrome']} (the nearest aerodrome)")
live.clear_emergency(w, cs)
ok(w.aircraft[cs].emergency is None and w.aircraft[cs].snapshot()["squawk"] == w.aircraft[cs].flight_plan["ssr"], "cleared: its own squawk again")
ok("no emergency" in raises(live.clear_emergency, w, cs), "nothing to clear")

# ---- requests, areas
rq = live.request(w, cs, "level", 380, "turbulence")
ok(w.requests[rq["request_id"]].type == "altitude" and w.requests[rq["request_id"]].value == 380, "the pilot asks for FL380 now")
rq = live.request(w, cs, "report", "moderate turbulence at FL350")
ok(w.requests[rq["request_id"]].phrase() == f"{cs} moderate turbulence at FL350", "a report in free words")
w2 = build_world(load_scenario("s07_restricted_area_activation"))
aid = next(iter(w2.areas))
st0 = w2.areas[aid].active
ok(live.set_area(w2, aid, not st0)["active"] == (not st0) and any(ev["type"] in ("area_activated", "area_deactivated") for ev in w2.events),
   f"area {aid} switched by hand")

# ---- solo radar: a radio failure does not answer
ws = build_world(load_scenario("s01_basic_crossing"))
ws.multi, ws.auto_pilot = True, True
for _ in range(3):
    ws.step(1)
scs = next(c for c, x in ws.aircraft.items() if x.assumed)
live.emergency(ws, scs, "radio")
ws.enter(scs, {"heading": 200})
ok(ws.last_readback and "no reply" in ws.last_readback[0]["text"] and ws.aircraft[scs].snapshot()["squawk"] == "7600",
   "solo: radio failure (7600), no reply to a clearance")

# ---- kept through a rewind and a restart
s = Session("p01_ws_two_merges", mode="stepped", autostart=False)
s.multi = True
s.persist_dir = tempfile.mkdtemp(prefix="atcsim-live-")
s.control("step", seconds=30)
live.add_aircraft(s.world, {"callsign": "POP1", "type": "A320", "fl": 350, "route": ["NIPRI", "ROLRO"], "appear": "now"})
s.control("step", seconds=2)                      # it appears at the next second
live.emergency(s.world, "POP1", "general")
s.control("step", seconds=40)
s.rewind(55)
ok("POP1" in s.world.aircraft and s.world.aircraft["POP1"].emergency, "rewind after the action: the pop-up and its emergency are there")
s.rewind(20)
ok("POP1" not in s.world.aircraft, "rewind before it: not there")
s.control("step", seconds=15)
live.add_aircraft(s.world, {"callsign": "POP2", "type": "A320", "fl": 350, "route": ["NIPRI", "ROLRO"], "appear": "now"})
s.control("step", seconds=25)
d = s.persist_dir
s.stop()
r = Session.restore(d)
ok("POP2" in r.world.aircraft and r.world.aircraft["POP2"].active, "restart: the pop-up aircraft is back")
r.control("step", seconds=60)
ok(r.world.aircraft["POP2"].active or r.world.aircraft["POP2"].status in ("exited",), "and flies on")
r.stop()
print("live actions checks passed")
