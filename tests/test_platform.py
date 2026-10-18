"""Platform test: accounts, roles, scenario access, an exercise with three seats, seat rights, radio and phone
relay, recording -> mp4.  Run:  python -m tests.test_platform   (uses a throw-away data folder)."""
import json
import os
import struct
import sys
import tempfile
import time

os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-test-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app, manager  # noqa: E402
from atcsim.platform import db  # noqa: E402


def client(email):
    c = TestClient(app)
    r = c.post("/auth/dev", json={"email": email})
    assert r.status_code == 200, r.text
    return c


def ok(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


COPY = f"zz_test_{int(time.time())}"
anon = TestClient(app)
ok(anon.get("/", follow_redirects=False).status_code == 303, "signed out: pages redirect to /login")
ok(anon.get("/api/exercises").status_code == 401, "signed out: API refused")

boss, ins, atc, pil, other = (client(e) for e in ("boss@gmail.com", "ins@gmail.com", "atc@gmail.com", "pil@gmail.com",
                                                  "other@gmail.com"))
ok(boss.get("/api/me").json()["role"] == "admin", "ATC_ADMIN_EMAILS account is admin")
ok(ins.get("/api/me").json()["role"] == "atc", "new accounts start as atc")
users = {u["email"]: u for u in boss.get("/api/users").json()}
ok(boss.patch(f"/api/users/{users['ins@gmail.com']['id']}", json={"role": "instructor"}).status_code == 200, "admin sets a role")
boss.patch(f"/api/users/{users['pil@gmail.com']['id']}", json={"role": "pilot"})
ok(atc.patch(f"/api/users/{users['atc@gmail.com']['id']}", json={"role": "admin"}).status_code == 403, "non-admin cannot change roles")
for c, (f, l, lic) in ((ins, ("Ina", "Structor", "MA-I-1")), (atc, ("Alaa", "Hamza", "MA-ATC-77")), (pil, ("Pi", "Lot", "MA-P-3"))):
    ok(c.put("/api/me", json={"first_name": f, "last_name": l, "licence": lic}).json()["profile_complete"], f"profile {f}")

# scenarios: library read-only for instructors, none for controllers
lib = ins.get("/api/scenarios").json()
ok(len(lib) > 5 and all(s["access"] == "read" for s in lib), "instructor sees library scenarios read-only")
ok(atc.get("/api/scenarios").status_code == 403, "controller has no scenario list")
ok(ins.post("/api/scenarios/s01_basic_crossing/copy", json={"new_name": COPY}).status_code == 200, "copy to own")
ok(next(s for s in ins.get("/api/scenarios").json() if s["name"] == COPY)["access"] == "write", "own copy writable")
raw = ins.get("/scenarios/s01_basic_crossing/raw").json()
raw.pop("_access", None)
ok(ins.post("/designer/save", json={"scenario": raw, "name": "s01_basic_crossing", "overwrite": True}).status_code == 403,
   "library scenario cannot be overwritten")
# author, complexity, density: set in the designer (the author defaults to whoever saves), else estimated
s01 = next(s for s in lib if s["name"] == "s01_basic_crossing")
ok(s01["complexity_estimated"] and 1 <= s01["complexity"] <= 5 and 1 <= s01["density"] <= 5, f"library: estimated ratings {s01['complexity']}/{s01['density']}")
cp = ins.get(f"/scenarios/{COPY}/raw").json(); cp.pop("_access", None); cp.pop("author", None)
cp.update(complexity=4, density=2)
ok(ins.post("/designer/save", json={"scenario": cp, "name": COPY, "overwrite": True}).status_code == 200, "designer saves ratings")
mine = next(s for s in ins.get("/api/scenarios").json() if s["name"] == COPY)
ok(mine["author"] == "Ina Structor" and mine["complexity"] == 4 and not mine["complexity_estimated"] and mine["density"] == 2,
   f"author defaults to the designer, ratings kept: {mine['author']} {mine['complexity']}/{mine['density']}")
ok(ins.post("/designer/save", json={"scenario": dict(cp, complexity=9), "name": COPY, "overwrite": True}).status_code == 400,
   "complexity outside 1-5 refused")
# documentation pages
pages = atc.get("/api/help").json()
ok({"controller", "pilot", "designer", "weather", "api"} <= {p["slug"] for p in pages}, "documentation index")
for pg in pages:
    md = atc.get(f"/api/help/{pg['slug']}").json()["markdown"]
    ok(md.startswith("# ") and len(md) > 1500, f"doc page {pg['slug']} ({len(md)} chars)")
ok(atc.get("/help/designer").status_code == 200 and anon.get("/help", follow_redirects=False).status_code == 200
   and anon.get("/help/api").status_code == 200 and anon.get("/api/help/controller").status_code == 200,
   "help pages are public")
ok(atc.get("/api/help/nope").status_code == 404, "unknown doc page")
lg = anon.get("/api/landing").json()
ok(lg.get("coast") and len(lg["coast"]["lines"]) > 5 and len(lg["coast"]["land"]) > 3, "landing scene: land and coastlines")
from atcsim import airspace as _asp  # noqa: E402
_c = _asp.get("agadir").describe().get("coast")
_ag = _asp.get("agadir").waypoints
# Agadir (GMAD) is on the Moroccan coast: a coastline passes within 15 NM of it; the training sector has none
_gmad = _asp.get("agadir").projection.forward(30.33, -9.41)
ok(_c and min(((p[0] - _gmad[0]) ** 2 + (p[1] - _gmad[1]) ** 2) ** 0.5 for ln in _c["lines"] for p in ln) < 15,
   "the coastline passes by Agadir")
ok(not _asp.get("training").describe().get("coast"), "no coastline on the fictional training sector")
ok(len(lg["outline"]) > 10 and "IBALU" in lg["points"] and len(lg["sectors"]) == 4 and anon.get("/login").status_code == 200,
   "sign-in page and its public map")
sj = anon.get("/api/help/scenario-json").json()["markdown"]
ok("<!-- REFERENCE -->" not in sj and "| VEDOD |" in sj and "| B738 |" in sj and "Prompt template" in sj,
   "scenario JSON reference with the generated appendix (points, types)")
sch = anon.get("/api/help/scenario.schema.json").json()
ok(sch["required"] == ["name", "aircraft"] and "aircraft" in sch["$defs"], "JSON Schema served")
ref = anon.get("/api/help/reference.json").json()
ok(any(p["name"] == "IBALU" for p in ref["airspaces"]["agadir"]["points"]) and
   ref["airspaces"]["agadir"]["capacity_per_hour"]["WN+WS"] == 48 and len(ref["aircraft_types"]) >= 40,
   "reference data served")
ins2 = client("ins2@gmail.com")
boss.patch(f"/api/users/{db.user_by_email('ins2@gmail.com')['id']}", json={"role": "instructor"})
ok(not any(s["name"] == COPY for s in ins2.get("/api/scenarios").json()), "other instructor does not see it")
ins.post(f"/api/scenarios/{COPY}/shares", json={"user_id": db.user_by_email('ins2@gmail.com')['id'], "perm": "read"})
ok(next(s for s in ins2.get("/api/scenarios").json() if s["name"] == COPY)["access"] == "read", "shared read-only")

# exercise
ex = ins.post("/api/exercises", json={"scenario": "s01_basic_crossing", "name": "Lesson 1"}).json()
eid = ex["id"]
det = ins.get(f"/api/exercises/{eid}").json()
ok(det["seat"] == "instructor" and "atc" in det["links"], "instructor gets invitation links")
ok(atc.get(f"/x/{eid}").status_code == 403, "not yet a member")
tok = det["links"]["atc"].rsplit("/", 1)[1]
r = atc.get(f"/join/{tok}", follow_redirects=False)
ok(r.status_code == 303 and r.headers["location"] == f"/x/{eid}", "join link adds the controller")
ins.post(f"/api/exercises/{eid}/members", json={"user_id": users["pil@gmail.com"]["id"], "seat": "pilot"})
ok(pil.get(f"/api/exercises/{eid}").json()["seat"] == "pilot", "invited from the list as pseudo-pilot")
ok(other.get(f"/sessions/{eid}/state").status_code == 403, "outsider refused")

# rights
ok(atc.post(f"/sessions/{eid}/control", json={"action": "resume"}).status_code == 403, "controller cannot run the sim")
ok(ins.post(f"/sessions/{eid}/control", json={"action": "resume"}).status_code == 200, "instructor runs it")
s = manager.get(eid)
with s.lock:
    s.world.step(1)
    for _ in range(3000):
        if any(a.on_frequency and not a.assumed for a in s.world.active_aircraft()):
            break
        s.world.step(1)
    cs = next(a.callsign for a in s.world.active_aircraft() if a.on_frequency and not a.assumed)
ok(atc.post(f"/sessions/{eid}/instructions", json={"callsign": cs, "type": "altitude", "fl": 300}).status_code == 403,
   "controller cannot move aircraft directly")
r = atc.post(f"/sessions/{eid}/entries", json={"callsign": cs, "cfl": 300, "speed": "M78"})
ok(r.status_code == 200 and r.json()["cfl"] == 300, "controller enters CFL in the label")
ok(atc.get(f"/sessions/{eid}/state").json()["entries"][cs]["cfl"] == 300, "entry visible in the state")
r = pil.post(f"/sessions/{eid}/instructions", json={"callsign": cs, "type": "altitude", "fl": 300}).json()[0]
ok(r["status"] != "rejected", f"pilot executes before the controller assumed ({r.get('reason', r['status'])})")
ok(pil.post(f"/sessions/{eid}/instructions", json={"callsign": cs, "type": "assume"}).status_code == 403, "pilot cannot assume")
r = atc.post(f"/sessions/{eid}/assume/{cs}"); print(r.text); ok(r.status_code == 200, "controller assumes")
ok(pil.post(f"/sessions/{eid}/control", json={"action": "pause"}).status_code == 403, "pilot cannot pause")
r = atc.post(f"/sessions/{eid}/mtcd/settings", json={"look_ahead_min": 35})
ok(r.status_code == 200 and r.json()["look_ahead_s"] == 2100 and ins.get(f"/sessions/{eid}/mtcd").json()["look_ahead_s"] == 2100,
   "controller sets the MTCD look-ahead (35 min), every seat sees it")
ok(atc.post(f"/sessions/{eid}/mtcd/settings", json={"look_ahead_min": 90}).status_code == 400, "MTCD look-ahead over 60 min refused")
ok(pil.post(f"/sessions/{eid}/mtcd/settings", json={"look_ahead_min": 10}).status_code == 403, "pilot cannot change the MTCD look-ahead")
ok(ins.post(f"/sessions/{eid}/mtcd/settings", json={"look_ahead_min": 20}).status_code == 200, "instructor resets it to 20 min")
# CPDLC: the controller types, the pseudo-pilot answers
dl = ins.get(f"/sessions/{eid}/cpdlc").json()
conn_cs = [c for c, d in dl["connections"].items() if d["state"] == "cda"]
ok(dl["own"]["address"] and isinstance(dl["messages"], list), f"CPDLC: {len(conn_cs)} connected aircraft")
if conn_cs:
    ccs = conn_cs[0]
    atc.post(f"/sessions/{eid}/assume/{ccs}")
    ok(atc.post(f"/sessions/{eid}/cpdlc/parse", json={"text": "H250", "callsign": ccs}).json()["elements"] == ["FLY HEADING 250"],
       "command line preview")
    r = atc.post(f"/sessions/{eid}/cpdlc/command", json={"text": "H250", "callsign": ccs})
    ok(r.status_code == 200 and r.json()["kind"] == "uplink", "controller uplinks FLY HEADING 250")
    mid = r.json()["message"]["id"]
    ok(atc.post(f"/sessions/{eid}/cpdlc/respond", json={"message_id": mid, "answer": "WILCO"}).status_code == 403,
       "the controller cannot answer for the pilot")
    ok(pil.post(f"/sessions/{eid}/cpdlc/command", json={"text": "H250", "callsign": ccs}).status_code == 403,
       "the pilot cannot uplink")
    ok(pil.post(f"/sessions/{eid}/cpdlc/respond", json={"message_id": mid, "answer": "WILCO"}).status_code == 200
       and atc.get(f"/sessions/{eid}/state").json()["entries"][ccs]["heading"] == 250, "pilot WILCO: the heading is in the system")
    ok(pil.post(f"/sessions/{eid}/cpdlc/downlink", json={"callsign": ccs, "kind": "text", "value": "REQUEST HIGHER WHEN AVAILABLE"}).status_code == 200,
       "pilot sends free text")
    ok(atc.post(f"/sessions/{eid}/cpdlc/command", json={"text": "ROG", "callsign": ccs}).status_code == 200, "controller ROGER")
# coordination chat
ch = atc.get(f"/sessions/{eid}/chat").json()
own = ch["own"]
other = next(k for k, v in ch["stations"].items() if v["kind"] != "own")
r = pil.post(f"/sessions/{eid}/chat", json={"from": other, "to": own, "text": "REQUEST FL360"})
ok(r.status_code == 200 and r.json()["from"] == other and r.json()["to"] == own, f"pilot writes as {other} to {own}")
ok(pil.post(f"/sessions/{eid}/chat", json={"from": own, "to": other, "text": "x"}).status_code == 403, "the pilot cannot write as our sector")
ok(atc.post(f"/sessions/{eid}/chat", json={"from": other, "to": own, "text": "x"}).status_code == 403, "the controller writes only as our sector")
r = atc.post(f"/sessions/{eid}/chat", json={"to": other, "text": "APPROVED", "ref": r.json()["id"]})
ok(r.status_code == 200 and r.json()["from"] == own, "controller answers (from = our sector)")
ok(len(ins.get(f"/sessions/{eid}/chat").json()["messages"]) == 2, "the instructor sees the chat")
ok(pil.post(f"/sessions/{eid}/release/{cs}").status_code == 403, "pilot cannot decontrol")
ok(atc.post(f"/sessions/{eid}/release/{cs}").status_code == 200, "controller decontrols by hand")
snap = atc.get(f"/sessions/{eid}/aircraft/{cs}").json()
ok(snap["released"] and not snap["assumed"] and "assignable_fl" in snap, "decontrolled; performance ranges served")
r = pil.post(f"/sessions/{eid}/instructions", json={"callsign": cs, "type": "heading", "heading": 200}).json()[0]
ok(r["status"] != "rejected", f"pilot still flies a decontrolled aircraft ({r.get('reason', r['status'])})")
ok(atc.post(f"/sessions/{eid}/assume/{cs}").status_code == 200, "controller can assume it again")

# dashboard data
lst = ins.get("/api/exercises?pictures=true").json()
ok(lst[0]["id"] == eid and "picture" in lst[0] and lst[0]["status"] == "running", "dashboard: picture and status")
ok(atc.get("/api/exercises").json()[0]["seat"] == "atc", "controller's exercise list")
ok(atc.get("/api/exercises").json()[0]["scenario_info"].get("complexity") in range(1, 6), "controller's list shows the scenario's complexity")

# voice
frame = bytes(np.full(640, 0x10, np.uint8))           # some mu-law samples
with atc.websocket_connect(f"/sessions/{eid}/comms") as wa, pil.websocket_connect(f"/sessions/{eid}/comms") as wp, \
        ins.websocket_connect(f"/sessions/{eid}/comms") as wi:
    def until(ws, pred, n=200):
        for _ in range(n):
            m = ws.receive()
            if m.get("text"):
                j = json.loads(m["text"])
                if pred(j):
                    return j
            elif m.get("bytes") is not None and pred(m["bytes"]):
                return m["bytes"]
        raise AssertionError("message not received")
    for w in (wa, wp, wi):
        until(w, lambda j: isinstance(j, dict) and j.get("type") == "hello")
    wa.send_text(json.dumps({"op": "ptt", "on": True}))
    until(wp, lambda j: isinstance(j, dict) and j.get("type") == "ptt" and j["on"])
    for _ in range(25):
        wa.send_bytes(frame)
        time.sleep(0.02)
    b = until(wp, lambda j: isinstance(j, bytes))
    ok(b[0] == 0 and len(b) == 3 + 640, "radio: controller heard by the pilot")
    until(wi, lambda j: isinstance(j, bytes))
    ok(True, "radio: instructor hears it")
    wa.send_text(json.dumps({"op": "ptt", "on": False}))
    # phone: the pilot calls the controller as GMMM
    wp.send_text(json.dumps({"op": "call", "unit": "GMMM"}))
    c = until(wa, lambda j: isinstance(j, dict) and j.get("type") == "calls" and j["calls"])
    ok(c["calls"][0]["state"] == "ringing" and c["calls"][0]["unit"] == "GMMM", "phone rings at the controller")
    wa.send_text(json.dumps({"op": "answer", "call": c["calls"][0]["id"]}))
    until(wp, lambda j: isinstance(j, dict) and j.get("type") == "calls" and j["calls"] and j["calls"][0]["state"] == "active")
    for _ in range(25):
        wp.send_bytes(frame)
        time.sleep(0.02)
    b = until(wa, lambda j: isinstance(j, bytes) and j[0] == 1)
    ok(len(b) == 643, "phone: controller hears the unit")
    until(wi, lambda j: isinstance(j, bytes) and j[0] == 1)
    ok(True, "phone: instructor listens in")
    wa.send_text(json.dumps({"op": "hangup", "call": c["calls"][0]["id"]}))
    until(wp, lambda j: isinstance(j, dict) and j.get("type") == "calls" and not j["calls"])
    ok(True, "hang up")
    time.sleep(2.5)

r = ins.post(f"/api/exercises/{eid}/end").json()
ok(r["recording_id"], "ending renders the recording")
for _ in range(240):
    rec = ins.get("/api/recordings").json()[0]
    if rec["status"] != "rendering":
        break
    time.sleep(0.5)
ok(rec["status"] == "ready", f"mp4 rendered ({rec.get('error')})")
f = atc.get(rec["download"])
ok(f.status_code == 200 and f.content[4:8] == b"ftyp", f"controller downloads it ({len(f.content)} bytes)")
ok(ins.post(f"/api/recordings/{rec['id']}/archive", json={"archived": True}).status_code == 200, "archive")
ok(not ins.get("/api/recordings").json() and ins.get("/api/recordings?archived=true").json(), "archived list")
ok(atc.post(f"/api/recordings/{rec['id']}/archive", json={"archived": False}).status_code == 403, "only the instructor archives")
ok(ins.delete(f"/api/scenarios/{COPY}").status_code == 200, "owner deletes the copy")

# ---- solo radar: a controller flies a scenario alone; simulated pilots fly what is entered
ok(atc.get("/solo").status_code == 200, "the solo radar page is open to controllers")
lib = atc.get("/api/solo/scenarios").json()
ok(any(x["name"] == "s01_basic_crossing" for x in lib), f"controllers see the library scenarios ({len(lib)})")
ok(atc.post("/api/solo", json={"scenario": "nope"}).status_code in (400, 403, 404), "unknown scenario refused")
so = atc.post("/api/solo", json={"scenario": "s01_basic_crossing"}).json()
sid = so["id"]
ok(so["solo"] and so["seat"] == "atc", "solo run: the controller's seat")
ok(atc.post(f"/sessions/{sid}/control", json={"action": "resume"}).status_code == 200, "the controller runs the solo simulation")
ok(atc.post(f"/sessions/{eid}/control", json={"action": "resume"}).status_code in (403, 404), "but not an instructor's exercise")
ok(pil.get(f"/sessions/{sid}/state").status_code == 403, "nobody else in it")
s_ = manager.sessions[sid]
with s_.lock:
    for _ in range(5):
        s_.world.step(1)
st = atc.get(f"/sessions/{sid}/state").json()
scs = next(a["callsign"] for a in st["aircraft"] if a["assumed"])
fl = st["aircraft"][[a["callsign"] for a in st["aircraft"]].index(scs)]["fl"] // 10 * 10 + 20
r = atc.post(f"/sessions/{sid}/entries", json={"callsign": scs, "cfl": fl}).json()
ok(r["cfl"] == fl and r["_readback"] and r["_readback"][0]["status"] == "accepted", "an entry is flown and read back: " + r["_readback"][0]["text"])
r = atc.post(f"/sessions/{sid}/entries", json={"callsign": scs, "cfl": 600}).json()
ok(r["_readback"][0]["status"] == "rejected" and "unable" in r["_readback"][0]["text"].lower(), "the pilot says unable: " + r["_readback"][0]["text"])
r = atc.post(f"/sessions/{sid}/cpdlc/command", json={"callsign": scs, "text": "H250", "voice": True}).json()
ok(r["kind"] == "entry" and "heading 250" in r["note"], "command line, voice: flown and read back")
ok(any(x["id"] == sid and x["solo"] for x in atc.get("/api/exercises").json()), "listed in the controller's exercises")
ok(atc.post(f"/api/exercises/{sid}/end").status_code == 200, "the controller ends it")
ss = [x for x in atc.get("/api/stats").json().get("controller", {}).get("sessions", []) if x["exercise_id"] == sid]
ok(len(ss) == 1 and ss[0]["solo"], "in the controller's statistics, once, as solo")
print("all platform checks passed")
