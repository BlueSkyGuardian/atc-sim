"""Debrief on the simulator: the run archived at the end, replayed read-only, tries branched from a moment (simulated
pilots), the session (open call, sync), rights, and the debrief kept afterwards.
   python -m tests.test_debrief   (uses a throw-away data folder)"""
import os
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-debrief-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app, manager  # noqa: E402


def client(email):
    c = TestClient(app)
    assert c.post("/auth/dev", json={"email": email}).status_code == 200
    return c


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


boss, ins, atc, pil, nosy = (client(e) for e in ("boss@gmail.com", "ins@gmail.com", "atc@gmail.com", "pil@gmail.com", "nosy@gmail.com"))
users = {u["email"]: u for u in boss.get("/api/users").json()}
boss.patch(f"/api/users/{users['ins@gmail.com']['id']}", json={"role": "instructor"})
boss.patch(f"/api/users/{users['pil@gmail.com']['id']}", json={"role": "pilot"})
for c, n in ((ins, "Ina"), (atc, "Alaa"), (pil, "Pi"), (nosy, "No")):
    c.put("/api/me", json={"first_name": n, "last_name": "X", "licence": "L-" + n})

ex = ins.post("/api/exercises", json={"scenario": "p01_ws_two_merges", "name": "debrief test"}).json()
eid = ex["id"]
ins.post(f"/api/exercises/{eid}/members", json={"user_id": users["atc@gmail.com"]["id"], "seat": "atc"})
ins.post(f"/api/exercises/{eid}/members", json={"user_id": users["pil@gmail.com"]["id"], "seat": "pilot"})
ins.post(f"/sessions/{eid}/control", json={"action": "step", "seconds": 300})
ins.post(f"/sessions/{eid}/bookmark", json={"note": "watch the merge"})
ins.post(f"/sessions/{eid}/control", json={"action": "step", "seconds": 400})
t_end = ins.get(f"/sessions/{eid}/state").json()["sim_time_s"]
ok(ins.get(f"/api/debriefs/{eid}").json()["debrief_status"] == "none", "no debrief while the exercise runs")
ins.post(f"/api/exercises/{eid}/end")
d = atc.get(f"/api/debriefs/{eid}").json()
ok(d["debrief_status"] == "ready" and not d["leader"] and d["seat"] == "atc", "ended: the debrief is ready (controller: not the leader)")
ok(ins.get(f"/api/debriefs/{eid}").json()["leader"], "the instructor leads it")
ok(nosy.get(f"/api/debriefs/{eid}").status_code == 403, "not for others")
ok(any(x["id"] == eid and x["debrief_status"] == "ready" for x in atc.get("/api/debriefs").json()), "listed on the controller's debriefs page")
ok(atc.get(f"/debrief/{eid}").status_code == 200 and "cv" in atc.get(f"/debrief/{eid}").text, "the debrief page is the simulator screen")

# ---- the original run, read-only
o = atc.post(f"/api/debriefs/{eid}/open", json={"src": "orig"}).json()
sid = o["sid"]
ok(o["kind"] == "orig" and not o["writable"], f"original run opened ({sid}), read-only")
st = atc.get(f"/sessions/{sid}/state").json()
ok(abs(st["sim_time_s"] - t_end) <= 10 and st["aircraft"], f"it ends where the exercise ended (T+{st['sim_time_s']:.0f})")
rec = atc.get(f"/sessions/{sid}/recording").json()
ok(rec["frames"][0]["t"] <= 5 and rec["frames"][-1]["t"] >= t_end - 10, f"the whole replay: {len(rec['frames'])} frames")
ok(any(e["type"] == "bookmark" for e in rec["events"]), "with the bookmarks on the timeline")
cs = st["aircraft"][0]["callsign"]
ok(ins.post(f"/sessions/{sid}/entries", json={"callsign": cs, "cfl": 300}).status_code == 403, "the original cannot be changed, even by the instructor")
ok(ins.post(f"/sessions/{sid}/control", json={"action": "resume"}).status_code == 403, "nor run")
ok(nosy.get(f"/sessions/{sid}/state").status_code == 403, "others cannot see it")

# ---- the session: start, open call
ok(atc.post(f"/api/debriefs/{eid}/start").status_code == 403, "the controller does not start it")
ok(ins.post(f"/api/debriefs/{eid}/start").json()["debrief_status"] == "live", "the instructor starts the debrief session")
ok(any(x["id"] == eid and x["debrief_status"] == "live" for x in atc.get("/api/debriefs").json()), "live on the controller's debriefs page")
with ins.websocket_connect(f"/debriefs/{eid}/comms") as wi, atc.websocket_connect(f"/debriefs/{eid}/comms") as wa:
    hi = wi.receive_json()
    while hi.get("type") != "hello":
        hi = wi.receive_json()
    ha = wa.receive_json()
    while ha.get("type") != "hello":
        ha = wa.receive_json()
    ok(hi["open"] and hi["me"]["leader"] and not ha["me"]["leader"], "open call: the instructor leads")
    wi.send_json({"op": "sync", "s": {"src": "orig", "t": 120, "play": True}})
    m = wa.receive_json()
    while m.get("type") != "sync":
        m = wa.receive_json()
    ok(m["s"]["t"] == 120, "the controller follows the instructor's replay")
    wa.send_json({"op": "sync", "s": {"src": "orig", "t": 999}})
    wa.send_json({"op": "cursor", "x": 10.5, "y": -3})
    m = wi.receive_json()
    while m.get("type") not in ("cursor", "sync"):
        m = wi.receive_json()
    ok(m["type"] == "cursor" and m["x"] == 10.5, "the controller's sync is ignored, their cursor is shown")
    wa.send_bytes(bytes([0x7f]) * 320)                      # open call: no push-to-talk needed
    m = wi.receive_bytes()
    ok(m[0] == 2 and len(m) == 323, "the controller is heard without push-to-talk")
    wa.send_json({"op": "mute", "on": True})
    m = wi.receive_json()
    while m.get("type") != "roster":
        m = wi.receive_json()
    ok(any(r["muted"] for r in m["roster"]), "muted: shown to the others")
ok(nosy.get(f"/api/debriefs/{eid}").status_code == 403, "outsiders kept out")

# ---- try from here
ok(atc.post(f"/api/debriefs/{eid}/branches", json={"t": 200}).status_code == 403, "the controller does not branch")
b = ins.post(f"/api/debriefs/{eid}/branches", json={"t": 205, "name": "turn earlier"}).json()
bsid = b["sid"]
ok(b["n"] == 1 and b["t0"] == 200 and b["name"] == "turn earlier", "Try from here: from the 10-second point before (T+200)")
bst = ins.get(f"/sessions/{bsid}/state").json()
ok(bst["sim_time_s"] == 200 and bst["session"]["status"] == "paused", "the try starts there, paused")
brec = ins.get(f"/sessions/{bsid}/recording").json()
ok(brec["frames"][-1]["t"] <= 200 and brec["frames"][0]["t"] <= 5, "with the history before it on its timeline")
c2 = next(a for a in bst["aircraft"] if a.get("assumed")) if any(a.get("assumed") for a in bst["aircraft"]) else bst["aircraft"][0]
cs2 = c2["callsign"]
r = ins.post(f"/sessions/{bsid}/entries", json={"callsign": cs2, "heading": 270})
ok(r.status_code == 200, "the instructor gives another clearance in the try")
ok(ins.post(f"/sessions/{bsid}/control", json={"action": "step", "seconds": 60}).status_code == 200, "and runs it")
ok(atc.post(f"/sessions/{bsid}/control", json={"action": "resume"}).status_code == 403, "the controller does not run it")
ok(atc.post(f"/sessions/{bsid}/entries", json={"callsign": cs2, "speed": "M78"}).status_code == 200, "but may try a clearance during the session")
ok(abs(atc.get(f"/sessions/{sid}/state").json()["sim_time_s"] - t_end) <= 10, "the original run is unchanged")
lst = atc.get(f"/api/debriefs/{eid}").json()["branches"]
ok(len(lst) == 1 and lst[0]["by"].startswith("Ina"), "the try is listed for everybody")

# ---- end of the session: everything kept, the controller opens it again
ok(ins.post(f"/api/debriefs/{eid}/end").json()["debrief_status"] == "done", "the instructor ends the debrief")
from atcsim.platform import debriefs  # noqa: E402
debriefs.unload(eid)
ok(bsid not in manager.sessions, "runs unloaded")
o2 = atc.post(f"/api/debriefs/{eid}/open", json={"src": "1"}).json()
ok(o2["sid"] == bsid and not o2["writable"], "afterwards the controller reopens the try (read-only)")
st2 = atc.get(f"/sessions/{bsid}/state").json()
ok(st2["sim_time_s"] >= 250, f"as it was left (T+{st2['sim_time_s']:.0f})")
ok(atc.post(f"/sessions/{bsid}/entries", json={"callsign": cs2, "cfl": 300}).status_code == 403, "not changed by the controller afterwards")
ok(ins.post(f"/sessions/{bsid}/control", json={"action": "step", "seconds": 10}).status_code == 200, "the instructor can still go on with it")
o3 = atc.post(f"/api/debriefs/{eid}/open", json={"src": "orig"}).json()
ok(atc.get(f"/sessions/{o3['sid']}/recording").json()["frames"], "and the original replay")
ok(any(x["id"] == eid and x["debrief_status"] == "done" for x in atc.get("/api/debriefs").json()), "the debrief stays with the exercise")
print("debrief checks passed")
