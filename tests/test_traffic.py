"""Live traffic: a fake ADS-B network (tests/fake_adsb.py) read by the server; the aircraft on the radar's session
(position, type, level, the selected level as the cleared level, origin and destination), view only, the coverage
map, a feed that is down. Run:  python -m tests.test_traffic   (throw-away data folder)."""
import os
import socket
import sys
import tempfile
import threading
import time
import warnings

warnings.filterwarnings("ignore")
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); PORT = s_.getsockname()[1]; s_.close()
os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-live-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"
os.environ["ATC_NEWS"] = "off"
os.environ["ATC_LIVE_URL"] = f"http://127.0.0.1:{PORT}/v2/point/{{lat}}/{{lon}}/{{nm}}"
os.environ["ATC_LIVE_ROUTES_URL"] = f"http://127.0.0.1:{PORT}/api/0/routeset"
os.environ["ATC_LIVE_EVERY_S"] = "2"
os.environ["ATC_LIVE_HEADERS"] = "X-Api-Key: test-key-123; X-Other: 2"

from tests import fake_adsb  # noqa: E402

threading.Thread(target=fake_adsb.serve, args=(PORT,), daemon=True).start()

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app  # noqa: E402
from atcsim.platform import traffic  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m, flush=True)
    if not c:
        sys.exit(1)


def client(email):
    c = TestClient(app)
    assert c.post("/auth/dev", json={"email": email}).status_code == 200
    return c


# ---------------------------------------------------------------- types
ok(traffic.perf_type("B738") == "B738" and traffic.perf_type("A21N") == "A21N" and traffic.perf_type("B78X") == "B789"
   and traffic.perf_type("AT76") == "E145" and traffic.perf_type("ZZZZ", "A5") == "B77W" and traffic.perf_type("", "") == "A320",
   "a type the simulator does not know flies as the nearest it does")

# ---------------------------------------------------------------- the page and the feed
with TestClient(app) as anon:                                # (the startup: the coverage measurement begins)
    r = anon.get("/live", follow_redirects=False)
    ok(r.status_code == 303 and "/login" in r.headers["location"], "signed out: /live asks to sign in")
    ok(anon.get("/api/live").status_code == 401, "and its API too")
boss, atc = client("boss@gmail.com"), client("atc@gmail.com")
ok(atc.get("/live").status_code == 200 and "radar" in atc.get("/live").text.lower(), "signed in: the radar page")
info = atc.get("/api/live").json()
ok(info["sid"] == traffic.LIVE_SID and info["circles"] >= 1 and "adsb.lol" in info["credit"], f"the live session, read in {info['circles']} circles")
sid = info["sid"]
t0 = time.time()
while time.time() - t0 < 20:
    info = atc.get("/api/live").json()
    st = atc.get(f"/sessions/{sid}/state").json()
    if info["polls"] and info["polls"] >= 2 and len(st["aircraft"]) >= 10 and any((a.get("live") or {}).get("orig") for a in st["aircraft"]):
        break
    time.sleep(1)
ok(info["mode"] == "watched" and info["last_ok"] and not info["last_error"], f"the feed is read while watched ({info['polls']} readings)")
hd = {k.lower(): v for k, v in getattr(fake_adsb.H, "last_headers", {}).items()}
ok(hd.get("x-api-key") == "test-key-123" and hd.get("x-other") == "2", "the key (ATC_LIVE_HEADERS) is sent with every request")
by = {a["callsign"]: a for a in st["aircraft"] + st["transferred"]}
ok(len(st["aircraft"]) >= 10 and "RAM201" in by, f"the aircraft on the picture: {len(st['aircraft'])} in the CTA, {len(st['transferred'])} around")
a = by["RAM201"]
ok(a["type"] == "B738" and a["fl"] == 370 and a["gs_kt"] == 450 and round(a["track_deg"]) == 200 and a["squawk"] == "2101"
   and a["assumed"] and a["status"] == "active", "RAM201: type, level, speed, track, squawk; inside the CTA: ours")
b = by["EZY73AD"]
ok(b["cleared_fl"] == 250 and b["vertical_speed_fpm"] < -1000 and b["live"]["selected_fl"] == 250,
   f"EZY73AD descending: the level selected in the cockpit is the cleared level (CFL {b['cleared_fl']})")
ok(a["flight_plan"]["departure"] == "GMMN" and a["flight_plan"]["destination"] == "GCLP", "origin and destination from the route lookup")
ok(a["mode_s"] and a["mode_s"]["ehs"] and a["mode_s"]["selected_fl"] == 370 and a["mode_s"]["address"] == "02A1B1",
   "Mode S: enhanced (selected level broadcast), its 24-bit address")
ok(a["route"] == [] and a["next_waypoint"] is None and a["live"]["hex"] == "02a1b1", "no route shown (not known): the look-ahead's points stay internal")
n = by["NOCALL1"] if "NOCALL1" in by else next((x for x in by.values() if x["live"]["hex"] == "0a1b2c"), None)
ok(n is not None and n["cleared_fl"] == n["fl"], f"no callsign: shown by its registration ({n and n['callsign']}); no selected level: its level")
sw = atc.get(f"/sessions/{sid}/state?sweep=true").json()
ok(sw["radar_t"] is not None and sw["live"], "the radar picture per sweep, as for the exercises")

# view only
for path, body in (("control", {"action": "pause"}), ("instructions", {"callsign": "RAM201", "type": "heading", "heading": 100}),
                   ("entries", {"callsign": "RAM201", "cfl": 350}), ("assume/RAM201", {})):
    r = atc.post(f"/sessions/{sid}/{path}", json=body)
    ok(r.status_code == 403 and "view only" in r.text, f"view only: {path} refused")
ok(atc.post(f"/sessions/{sid}/connect?client=radar").status_code == 200, "but the radar connects")
ok(traffic.LIVE_SID not in [x["session_id"] for x in boss.get("/sessions").json()], "not among anybody's sessions")
try:
    with atc.websocket_connect(f"/sessions/{sid}/comms") as ws_:
        ws_.receive_json()
    voice = True
except Exception:                                            # noqa: BLE001 - closed: refused
    voice = False
ok(not voice, "no voice on it")
with atc.websocket_connect(f"/sessions/{sid}/stream?client=radar&separation=false") as ws:
    m = ws.receive_json()
ok(m["type"] == "tick" and len(m["state"]["aircraft"]) >= 10 and m["state"]["radar_t"] is not None,
   f"and its stream comes, the traffic on its first picture ({len(m['state']['aircraft'])} aircraft)")

# ---------------------------------------------------------------- the coverage map
g = atc.get("/api/live/coverage").json()
cells = sum(1 for c in g["counts"] if c)
ok(g["polls"] >= 2 and cells >= 10 and g["cell"] == 10 and g["nx"] * g["ny"] == len(g["counts"]), f"coverage: positions in {cells} cells of 10 NM over {g['polls']} readings")
ok(atc.post("/api/live/measure", json={"hours": 24}).status_code == 403 and boss.post("/api/live/measure", json={"hours": 24}).json()["measure_until"],
   "measuring for 24 h: admins only")
traffic.FEED.watched_at = 0                      # nobody watches: read slowly while measuring, else not at all
ok(traffic.FEED.mode() == "measuring", "nobody watching, measuring: slow readings")
boss.post("/api/live/measure", json={"hours": 0})
ok(traffic.FEED.mode() == "idle", "nobody watching, not measuring: the feed is not read")
traffic.FEED.save_grid()
ok(os.path.exists(os.path.join(traffic.LIVE_DIR, "coverage.json")), "the map is kept across restarts")
ok(boss.post("/api/live/coverage/reset").status_code == 200 and sum(atc.get("/api/live/coverage").json()["counts"]) == 0, "an admin may start it again")

# ---------------------------------------------------------------- the feed down
traffic.SOURCE = "http://127.0.0.1:9/v2/point/{lat}/{lon}/{nm}"
atc.get("/api/live")
t0 = time.time()
while time.time() - t0 < 15 and not atc.get("/api/live").json()["last_error"]:
    time.sleep(0.5)
info = atc.get("/api/live").json()
ok(info["last_error"] and atc.get("/live").status_code == 200, f"the feed down: said on the page ({info['last_error'][:50]}), the page still opens")
print("live traffic checks passed")
