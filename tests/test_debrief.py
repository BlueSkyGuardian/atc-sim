"""Debrief on the simulator: the exercise reproduced 1:1 (control states, entries, positions) by re-simulating it,
any action creating a new timeline (synthetic pilot, extrapolated to the end), timelines of timelines, the session's
shared player, each member's own player afterwards, rights, and the timelines kept on disk.
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
from atcsim.platform import debriefs  # noqa: E402


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

ex = ins.post("/api/exercises", json={"scenario": "p02_wn_merges_and_climbs", "name": "debrief test"}).json()
eid = ex["id"]
ins.post(f"/api/exercises/{eid}/members", json={"user_id": users["atc@gmail.com"]["id"], "seat": "atc"})
ins.post(f"/api/exercises/{eid}/members", json={"user_id": users["pil@gmail.com"]["id"], "seat": "pilot"})


def view(c, sid):
    st = c.get(f"/sessions/{sid}/state").json()
    return st


def sig(st):
    return {a["callsign"]: (a["assumed"], a["fl"], round(a["x_nm"], 2), round(a["y_nm"], 2), a.get("cleared_fl"))
            for a in st["aircraft"]}, {k: {f: v for f, v in e.items() if f not in ("t", "by", "time_s")} for k, e in (st.get("entries") or {}).items()}


# ---- the exercise: the controller assumes and enters clearances, the pseudo-pilot flies them
cap = {}
ins.post(f"/sessions/{eid}/control", json={"action": "step", "seconds": 120})
done = set()
for t in range(120, 720, 30):
    st = view(atc, eid)
    for a in st["aircraft"]:
        cs = a["callsign"]
        if a["on_frequency"] and not a["assumed"]:
            atc.post(f"/sessions/{eid}/assume/{cs}")
        elif a["assumed"] and cs not in done and len(done) < 4:
            fl = a["fl"] // 10 * 10 + (20 if len(done) % 2 else -20)
            atc.post(f"/sessions/{eid}/entries", json={"callsign": cs, "cfl": fl})
            pil.post(f"/sessions/{eid}/instructions", json={"callsign": cs, "type": "fl", "value": fl})
            done.add(cs)
    if t in (300, 450, 600):
        cap[t] = sig(view(atc, eid))
    ins.post(f"/sessions/{eid}/control", json={"action": "step", "seconds": 30})
ins.post(f"/sessions/{eid}/bookmark", json={"note": "watch the merge"})
ok(len(done) >= 2 and any(v[0] for v in cap[600][0].values()), f"exercise: {len(done)} clearances, aircraft assumed")
ins.post(f"/api/exercises/{eid}/end")

# ---- afterwards, the controller's own player: the exercise reproduced 1:1
d = atc.get(f"/api/debriefs/{eid}").json()
ok(d["debrief_status"] == "ready" and d["timelines"][0]["id"] == "orig", "the debrief: the exercise as flown")
pl = atc.post(f"/api/debriefs/{eid}/player").json()
sid = pl["sid"]
ok(not pl["shared"] and pl["can_write"] and pl["can_control"], f"the controller gets their own player ({sid}): play and branch")
for t in (300, 600):
    atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "t": t})
    got = sig(view(atc, sid))
    ok(got == cap[t], f"T+{t}: control states, levels, positions and label entries exactly as in the exercise")
atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "t": 300})
atc.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 150})
ok(sig(view(atc, sid)) == cap[450], "played from T+300 to T+450 (re-simulated with the logged actions): identical")
st = view(atc, sid)
ok(st["session"]["debrief"]["timeline"] == "orig", "on the exercise's timeline")

# ---- an action creates a new timeline, flown by a synthetic pilot, extrapolated
cs = next(cs for cs, v in cap[450][0].items() if v[0])
r = atc.post(f"/sessions/{sid}/entries", json={"callsign": cs, "heading": 90})
ok(r.status_code == 200, f"the controller gives {cs} heading 090 at T+450")
st = view(atc, sid)
tl1 = st["session"]["debrief"]["timeline"]
ok(tl1 == 1 and st["session"]["debrief"]["t0"] == 450, "a new timeline from T+450, the player on it")
r = atc.post(f"/sessions/{sid}/entries", json={"callsign": cs, "cfl": 330})
tls = atc.get(f"/api/debriefs/{eid}").json()["timelines"]
ok(len(tls) == 2 and len(tls[1]["changes"]) == 2, "a second change at the same second: part of the same timeline")
for _ in range(120):
    tls = atc.get(f"/api/debriefs/{eid}").json()["timelines"]
    if tls[1]["status"] != "computing":
        break
    time.sleep(0.5)
ok(tls[1]["status"] == "ready" and tls[1]["end_t"] > 900 and "losses" in tls[1]["summary"],
   f"extrapolated to T+{tls[1]['end_t']:.0f}: {tls[1]['summary'].get('losses')} loss(es)")
atc.post(f"/sessions/{sid}/control", json={"action": "step", "seconds": 180})
a = next(a for a in view(atc, sid)["aircraft"] if a["callsign"] == cs)
ok(abs(((a["track_deg"] - 90 + 540) % 360) - 180) < 15, f"the synthetic pilot flew it: {cs} on track {a['track_deg']:.0f}")
ev = atc.get(f"/api/debriefs/{eid}/timelines/1").json()
ok(any(e["time_s"] < 450 for e in ev["events"]) and ev["parent"] == "orig", "the new timeline's markers include the history before it")
# a timeline from that timeline
cs2 = next(c for c, v in cap[450][0].items() if v[0] and c != cs)
atc.post(f"/sessions/{sid}/entries", json={"callsign": cs2, "cfl": 250})
tls = atc.get(f"/api/debriefs/{eid}").json()["timelines"]
ok(len(tls) == 3 and tls[2]["parent"] == 1 and tls[2]["t0"] == 630, "a change later on it: a timeline branched from it (T+630)")
# while it plays: the action does not pause it; another one within a minute joins the same timeline
atc.post(f"/sessions/{sid}/control", json={"action": "resume"})
for _ in range(60):
    st = view(atc, sid)
    if st["sim_time_s"] >= 640:
        break
    time.sleep(0.5)
ok(st["session"]["status"] == "running" and st["sim_time_s"] >= 640, f"the new timeline plays on while it is extrapolated (T+{st['sim_time_s']:.0f})")
cs3 = next(c for c, v in cap[450][0].items() if v[0] and c not in (cs, cs2))
r = atc.post(f"/sessions/{sid}/entries", json={"callsign": cs3, "speed": "M76"})
st = view(atc, sid)
tls = atc.get(f"/api/debriefs/{eid}").json()["timelines"]
ok(r.status_code == 200 and st["session"]["status"] == "running" and len(tls) == 3 and len(tls[2]["changes"]) == 2,
   "acting while it runs: not paused, and within a minute of the last change it joins that timeline")
atc.post(f"/sessions/{sid}/control", json={"action": "pause"})
t0 = time.time()
atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "timeline": "orig", "t": 534})
ok(time.time() - t0 < 1.0 and view(atc, sid)["sim_time_s"] == 530, f"seeking is instant: to the 10-second point (T+530, {time.time() - t0:.2f} s)")
# the original is untouched
atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "timeline": "orig", "t": 600})
ok(sig(view(atc, sid)) == cap[600], "back on the exercise's timeline: unchanged")
atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "timeline": "1", "t": 600})
ok(view(atc, sid)["session"]["debrief"]["timeline"] == 1, "switch to timeline 1 at T+600")

# ---- rights
ok(nosy.get(f"/api/debriefs/{eid}").status_code == 403, "not for others")
isid = ins.post(f"/api/debriefs/{eid}/player").json()["sid"]
ok(isid != sid and atc.post(f"/sessions/{isid}/control", json={"action": "resume"}).status_code == 403, "nobody runs another member's player")
ok(atc.post(f"/sessions/{isid}/entries", json={"callsign": cs, "cfl": 300}).status_code == 403, "nor acts on it")

# ---- the session: one shared player, the instructor runs it, everybody may act
ok(ins.post(f"/api/debriefs/{eid}/start").json()["debrief_status"] == "live", "the instructor starts the debrief session")
p1, p2 = ins.post(f"/api/debriefs/{eid}/player").json(), atc.post(f"/api/debriefs/{eid}/player").json()
ok(p1["sid"] == p2["sid"] == eid + "00" and p1["can_control"] and not p2["can_control"] and p2["can_write"],
   "a shared player: the instructor runs it, the controller may act on it")
ok(atc.post(f"/sessions/{p1['sid']}/control", json={"action": "resume"}).status_code == 403, "the controller does not run it")
ins.post(f"/api/debriefs/{eid}/seek", json={"sid": p1["sid"], "t": 300})
r = atc.post(f"/sessions/{p1['sid']}/release/{next(c for c, v in cap[300][0].items() if v[0])}")
ok(r.status_code == 200 and len(atc.get(f"/api/debriefs/{eid}").json()["timelines"]) == 4, "the controller decontrols an aircraft: another timeline")
ok(ins.post(f"/api/debriefs/{eid}/end").json()["debrief_status"] == "done", "the instructor ends it")
ok(atc.post(f"/api/debriefs/{eid}/player").json()["sid"] == sid, "afterwards: back to one's own player")

# ---- kept on disk
for _ in range(120):
    if all(t["status"] != "computing" for t in atc.get(f"/api/debriefs/{eid}").json()["timelines"]):
        break
    time.sleep(0.5)
debriefs.unload(eid)
debriefs.DEBRIEFS.clear()
tls = atc.get(f"/api/debriefs/{eid}").json()["timelines"]
ok(len(tls) == 4 and tls[1]["status"] == "ready" and tls[2]["parent"] == 1, "the timelines are kept (reloaded from disk)")
sid = atc.post(f"/api/debriefs/{eid}/player").json()["sid"]
atc.post(f"/api/debriefs/{eid}/seek", json={"sid": sid, "t": 600})
ok(sig(view(atc, sid)) == cap[600], "and the exercise still reproduces 1:1")
print("debrief checks passed")
