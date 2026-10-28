"""Exercises survive a server restart.   python tests/test_restart.py
Starts a server, runs an exercise and a solo run, kills the server (SIGKILL), starts it again."""
# start a server, create an exercise with members, run it, kill the server, start again: the exercise is back, paused
import os, subprocess, sys, time, signal, requests
import tempfile
S = tempfile.mkdtemp(prefix="atcsim-restart-"); os.makedirs(S + "/rsdata", exist_ok=True); env = dict(os.environ, ATC_DATA_DIR=S + "/rsdata", ATC_DEV_LOGIN="1", ATC_ADMIN_EMAILS="boss@gmail.com", PYTHONPATH=".")
def up():
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "atcsim.server:app", "--port", "8779"], env=env, stdout=open(S + "/rs.log", "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            requests.get("http://127.0.0.1:8779/api/health", timeout=1); return p
        except Exception: time.sleep(0.5)
    raise SystemExit("no server")
def login(email):
    c = requests.Session(); r = c.post("http://127.0.0.1:8779/auth/dev", json={"email": email}); assert r.ok, r.text; return c
B = "http://127.0.0.1:8779"


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        p.kill(); raise SystemExit(1)


p = up()
boss = login("boss@gmail.com"); ins = login("ins@gmail.com"); atc = login("atc@gmail.com")
uid = {u["email"]: u["id"] for u in boss.get(B + "/api/users").json()}
boss.patch(B + f"/api/users/{uid['ins@gmail.com']}", json={"role": "instructor"})
e = ins.post(B + "/api/exercises", json={"scenario": "p01_ws_two_merges", "name": "Restart test"}).json(); eid = e["id"]
ins.post(B + f"/api/exercises/{eid}/members", json={"user_id": uid["atc@gmail.com"], "seat": "atc"})
so = atc.post(B + "/api/solo", json={"scenario": "s01_basic_crossing"}).json()
ins.post(B + f"/sessions/{eid}/control", json={"action": "resume"}); ins.post(B + f"/sessions/{eid}/control", json={"action": "set_speed", "time_factor": 20})
atc.post(B + f"/sessions/{so['id']}/control", json={"action": "resume"})
time.sleep(6)
st = ins.get(B + f"/sessions/{eid}/state").json(); t_before = st["sim_time_s"]
cs = next(a["callsign"] for a in st["aircraft"] if a["assumed"])
atc.post(B + f"/sessions/{eid}/entries", json={"callsign": cs, "cfl": 310})
time.sleep(1.5)
t_kill = ins.get(B + f"/sessions/{eid}/state").json()["sim_time_s"]
ok(sorted(os.listdir(S + f"/rsdata/live/{eid}")) == ["frames.jsonl", "meta.json", "scenario.json", "snaps.bin"], f"saved while running (T+{t_kill:.0f})")
p.send_signal(signal.SIGKILL); p.wait(); time.sleep(1)
p = up()
ins = login("ins@gmail.com"); atc = login("atc@gmail.com")
d = ins.get(B + f"/api/exercises/{eid}").json()
st = ins.get(B + f"/sessions/{eid}/state").json()
ok(d["live"] and st["session"]["status"] == "paused" and t_kill - 12 <= st["sim_time_s"] <= t_kill, f"after the restart: live, paused at T+{st['sim_time_s']:.0f}")
ok(st["session"]["time_factor"] == 20 and st["entries"].get(cs, {}).get("cfl") == 310, "speed and the controller's entries kept")
ok(len(ins.get(B + f"/sessions/{eid}/recording").json()["frames"]) > 5 and len(ins.get(B + f"/sessions/{eid}/timeline").json()["points"]) > 3, "replay frames and rewind points kept")
ok(atc.get(B + f"/api/exercises/{so['id']}").json()["live"], "the solo run is back too")
ins.post(B + f"/sessions/{eid}/control", json={"action": "resume"}); time.sleep(2)
ok(ins.get(B + f"/sessions/{eid}/state").json()["sim_time_s"] > st["sim_time_s"], "it runs on")
r = ins.post(B + f"/api/exercises/{eid}/end")
ok(r.status_code == 200 and not os.path.exists(S + f"/rsdata/live/{eid}"), "ended: its save is removed")
p.terminate()

# the simulation's Restart: from the world saved at T+0 (milliseconds; building it again took seconds in which the
# server did little else, voice included), the same start exactly
import json, warnings  # noqa: E401,E402
warnings.filterwarnings("ignore")
from atcsim.session import SessionManager  # noqa: E402
sm = SessionManager()
ss = sm.create(scenario_name="c02_cta_severe_wx", mode="stepped", time_factor=1, autostart=False)
pic0 = json.dumps(ss.world.state(False), sort_keys=True, default=str)
ss.control("step", seconds=120)
t_ = time.perf_counter(); ss.control("restart"); dt_ = time.perf_counter() - t_
ok(dt_ < 1.0 and json.dumps(ss.world.state(False), sort_keys=True, default=str) == pic0 and ss.world.t == 0
   and ss.world.recording["frames"] == [], f"Restart: back to T+0 exactly, in {dt_ * 1000:.0f} ms")
print("restart checks passed")
