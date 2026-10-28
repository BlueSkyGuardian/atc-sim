"""Vertical rates (at X or greater, at X or less, exactly X) from every input - the API, the label entries, the command
line, CPDLC - and flown by the model; the radar picture once per sweep, with everything else at once.
   python -m tests.test_rates_sweep   (throw-away data folder)"""
import asyncio
import json
import os
import tempfile
import threading
import time
import warnings

warnings.filterwarnings("ignore")
os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-rates-")
os.environ["ATC_AUTH"] = "off"
os.environ["ATC_NEWS"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from atcsim import cpdlc  # noqa: E402
from atcsim.scenario import build_world, load_scenario  # noqa: E402
from atcsim.session import parse_command  # noqa: E402
from atcsim.world import parse_rate  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m, flush=True)
    if not c:
        raise SystemExit(1)


def world():
    w = build_world(load_scenario("s03_descent_through_opposite"))
    for _ in range(200):
        w.step(1.0)
    return w


def fly(w, cs, fl, rate=None, mode=None, secs=90):
    ins = {"callsign": cs, "type": "altitude", "fl": fl}
    if rate:
        ins["rate_fpm"] = rate
    if mode:
        ins["rate_mode"] = mode
    r = w.issue(ins, by_pilot=True)
    for _ in range(30):
        w.step(1.0)
    vs = []
    for _ in range(secs - 30):
        w.step(1.0)
        vs.append(w.aircraft[cs].vs_fpm)
    return r, vs


# ---------------------------------------------------------------- the model
w = world()
cs = "RAM304"
idle = w.aircraft[cs].descent_rate_idle()
r, vs = fly(w, cs, 300, 1000, "exact")
ok(r["status"] == "executed" and all(abs(v + 1000) < 1 for v in vs), f"exactly 1000 ft/min in the descent (idle about {int(idle)}): held")
w = world()
r, vs = fly(w, cs, 300, 3500, "exact")
ok(all(abs(v + 3500) < 1 for v in vs), "exactly 3500 ft/min: more than idle, with the speed brakes")
w = world()
r, vs = fly(w, cs, 300, 1000, "min")
ok(min(vs) < -1000 - 500, f"1000 or greater: it descends faster if it can ({int(min(vs))})")
w = world()
r, vs = fly(w, cs, 300, 2000, "max")
ok(all(-2000 - 1 <= v < 0 for v in vs), "2000 or less: never more")
w = world()
a = w.aircraft["DLH303"]
a.alt_ft = a.cleared_alt_ft = 30000.0
r, vs = fly(w, "DLH303", 340, 800, "exact")
ok(r["status"] == "executed" and all(abs(v - 800) < 1 for v in vs[-30:]), "exactly 800 ft/min in the climb")
w = world()
r = w.issue({"callsign": cs, "type": "altitude", "fl": 300, "rate_fpm": 6000, "rate_mode": "exact"}, by_pilot=True)
ok(r["status"] == "rejected" and "unable" in r.get("reason", ""), f"a rate it cannot fly is refused: {r.get('reason')}")
r = w.issue({"callsign": cs, "type": "altitude", "fl": 300, "rate_fpm": 1500, "rate_mode": "sideways"}, by_pilot=True)
ok(r["status"] == "rejected" and "rate_mode" in r.get("reason", ""), "an unknown mode is refused")
ok(w.phrase({"callsign": cs, "type": "altitude", "fl": 300, "rate_fpm": 1500, "rate_mode": "exact"}) == "RAM304 descend FL300 at 1500 ft/min"
   and w.phrase({"callsign": cs, "type": "altitude", "fl": 300, "rate_fpm": 1500, "rate_mode": "max"}).endswith("1500 ft/min or less"),
   "the read-back: 'at 1500 ft/min', '1500 ft/min or less'")

# ---------------------------------------------------------------- what the people type
ok(parse_rate("1500") == (1500, "min") and parse_rate("1500-") == (1500, "max") and parse_rate("1500=") == (1500, "exact")
   and parse_rate("1500+") == (1500, "min"), "the rate as typed in a label: 1500, 1500-, 1500=")
w = world()
pc = parse_command(f"{cs} DES300 V1800=", w)
alt = next(x for x in pc if x["type"] == "altitude")
ok(alt["rate_fpm"] == 1800 and alt["rate_mode"] == "exact", "the command line: DES300 V1800= is exactly 1800")
p = cpdlc.parse(w, "DES300 V1500=", cs)
lvl = next(e for e in p["elements"] if e.get("kind") == "level")
ok(lvl["instr"]["rate_mode"] == "exact" and any(e["code"] == "UM169" and "AT 1500 FT/MIN" in e["text"] for e in p["elements"]),
   "CPDLC: V1500= sends the exact rate with the level (free text: no standard element for it)")
p = cpdlc.parse(w, "DES300 V1500-", cs)
ok(any(e["code"] == "UM174" for e in p["elements"]), "CPDLC: V1500- is UM174 DESCEND AT 1500 FT/MIN MAXIMUM")

# ---------------------------------------------------------------- the API
from atcsim.server import app  # noqa: E402

c = TestClient(app)
sid = c.post("/sessions", json={"scenario": "s03_descent_through_opposite", "mode": "stepped", "autostart": False}).json()["session"]["session_id"]
c.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 200})
r = c.post(f"/sessions/{sid}/instructions", json={"callsign": cs, "type": "altitude", "fl": 300, "rate_fpm": 1200, "rate_mode": "exact"})
ok(r.status_code == 200 and r.json()[0]["status"] != "rejected", f"the pilot deck's instruction carries the mode ({r.status_code})")
c.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 40})
st = c.get(f"/sessions/{sid}/state").json()
a = next(x for x in st["aircraft"] if x["callsign"] == cs)
ok(a["assigned_rate_fpm"] == 1200 and a["assigned_rate_mode"] == "exact" and a["vertical_speed_fpm"] == -1200, "and the aircraft flies exactly 1200")
r = c.post(f"/sessions/{sid}/entries", json={"callsign": cs, "cfl": 280, "rate": "2000="})
ok(r.status_code == 200, f"the controller's entry: rate 2000= ({r.status_code} {r.text[:80]})")

# ---------------------------------------------------------------- the radar sweep
st_live = c.get(f"/sessions/{sid}/state").json()
ok(st_live["radar_t"] is None, "the state API is live by default (scripts, agents)")
c.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 1})     # t = 241: last sweep at 240
sw = c.get(f"/sessions/{sid}/state?sweep=true").json()
live = c.get(f"/sessions/{sid}/state").json()
a_sw = next(x for x in sw["aircraft"] if x["callsign"] == cs)
a_lv = next(x for x in live["aircraft"] if x["callsign"] == cs)
ok(sw["radar_sweep_s"] == 4 and sw["radar_t"] == 240 and sw["sim_time_s"] == 241, f"sweep=true: the picture of the last sweep (T+{sw['radar_t']}, now T+{sw['sim_time_s']})")
ok((a_sw["x_nm"], a_sw["y_nm"]) != (a_lv["x_nm"], a_lv["y_nm"]) and a_sw["cleared_fl"] == a_lv["cleared_fl"]
   and sw["entries"][cs]["cfl"] == 280 and sw["entries"][cs]["rate"] == "2000=",
   f"positions as painted at the sweep ({a_sw['x_nm']} vs {a_lv['x_nm']} now); the system data (entries, cleared level) live")
c.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 3})     # t = 244: a new sweep
sw2 = c.get(f"/sessions/{sid}/state?sweep=true").json()
a2 = next(x for x in sw2["aircraft"] if x["callsign"] == cs)
ok(sw2["radar_t"] == 244 and abs(a2["x_nm"] - next(x for x in c.get(f"/sessions/{sid}/state").json()["aircraft"] if x["callsign"] == cs)["x_nm"]) < 1e-9,
   "at the next sweep the picture is the aircraft's position then")

# the stream: radar screens get one picture per sweep, actions at once
sid2 = c.post("/sessions", json={"scenario": "s10_busy_mixed", "mode": "realtime", "time_factor": 4, "autostart": True}).json()["session"]["session_id"]
time.sleep(3)
got = []
with c.websocket_connect(f"/sessions/{sid2}/stream?client=radar&separation=false") as ws:
    t0 = time.time()
    first = ws.receive_json()
    cs2 = first["state"]["aircraft"][0]["callsign"]
    sent = None
    while time.time() - t0 < 6:
        m = ws.receive_json()
        got.append((time.time() - t0, m["state"]["radar_t"], m["state"]["sim_time_s"], [e["type"] for e in m["events"]]))
        if sent is None and time.time() - t0 > 2:
            threading.Thread(target=lambda: c.post(f"/sessions/{sid2}/instructions", json={"callsign": cs2, "type": "heading", "heading": 200}), daemon=True).start()
            sent = time.time()
pics = sorted({g[1] for g in got})
ok(all(p % 4 == 0 for p in pics) and len(pics) >= 4, f"radar screens: pictures on the 4 s sweeps only ({pics[:6]}... at x4)")
ans = next((g for g in got if "instruction" in g[3]), None)
ok(ans is not None and ans[0] - (sent - t0) < 1.0, f"a clearance is on the screens at once ({(ans[0] - (sent - t0)) * 1000:.0f} ms), not at the next sweep" if ans else "no clearance seen")
with c.websocket_connect(f"/sessions/{sid2}/stream?separation=false") as ws:
    ts = {ws.receive_json()["state"]["radar_t"] for _ in range(4)}
ok(ts == {None}, "other clients (scripts, agents): live, every simulation second")
print("rates and sweep checks passed")
