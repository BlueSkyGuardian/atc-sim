"""CPDLC: equipage, logon and data authority, the command line, uplinks and answers, downlinks, transfer.
   python -m tests.test_cpdlc"""
import warnings

warnings.filterwarnings("ignore")
from atcsim import cpdlc  # noqa: E402
from atcsim.scenario import build_world, load_scenario  # noqa: E402


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


# ---- equipage from item 10
w = build_world(load_scenario("p01_ws_two_merges"))
a0 = next(iter(w.aircraft.values()))
eq = a0.flight_plan["equipment"]
ok(cpdlc.equipped(a0) == ("J" in eq.split("/")[0]), f"equipage from item 10 ({eq})")
a0.flight_plan["equipment"] = "SDE2FGHIRY/LB1"
ok(not cpdlc.equipped(a0), "no J code: not CPDLC equipped")
a0.flight_plan["equipment"] = eq

# ---- connected exercise: logon before entry, CDA at the transfer of communication, NDA at the activation
w = build_world(load_scenario("p01_ws_two_merges"))
w.multi = True
for _ in range(3):
    w.step(1)
states = {c["state"] for c in w.cpdlc.values()}
ok("cda" in states and "logon" in states, f"aircraft in the sector are connected, inbound ones logged on: {sorted(states)}")
inb = next(cs for cs, c in w.cpdlc.items() if c["state"] == "logon")
ok(w.cpdlc[inb]["cda"].endswith(w.aircraft[inb].prev_unit), f"{inb} logged on while {w.cpdlc[inb]['cda']} is the current data authority")
for _ in range(1200):
    w.step(1)
    if w.aircraft[inb].on_frequency:
        break
w.step(1)
ok(w.cpdlc[inb]["state"] == "cda" and w.cpdlc[inb]["cda"] == "GMAC", f"{inb}: GMAC current data authority at the transfer of communication")

cs = next(c for c, v in w.cpdlc.items() if v["state"] == "cda" and c != inb and w.aircraft[c].active)
a = w.aircraft[cs]
a.assumed = True

# ---- command line
P = lambda t: cpdlc.parse(w, t, cs)  # noqa: E731
fl = int(a.alt_ft // 1000) * 10 + 20
p = P(f"CL{fl} M78")
ok([e["code"] for e in p["elements"]] == ["UM20", "UM106"] and p["elements"][0]["instr"].get("mach") == 0.78,
   f"CL{fl} M78 = CLIMB TO FL{fl} / MAINTAIN M.78 (one clearance)")
ok(P(f"{fl}")["elements"][0]["code"] == "UM20" and P(f"FL{fl - 40}")["elements"][0]["code"] == "UM23",
   "a bare level climbs or descends from the present level")
nxt = next(n for n in a.route[a.next_index + 1:] if not n.startswith("@"))
for t in (f"D {nxt}", f"D{nxt}", f"DCT {nxt}", nxt):
    ok(P(t)["elements"][0]["text"] == f"PROCEED DIRECT TO {nxt}", f"'{t}' = PROCEED DIRECT TO {nxt}")
ok(P("L270")["elements"][0]["text"] == "TURN LEFT HEADING 270" and P("H090")["elements"][0]["code"] == "UM190", "headings")
ok(P("DEVR15")["elements"][0]["text"] == "CLEARED TO DEVIATE UP TO 15 NM RIGHT OF ROUTE", "weather deviation")
ok(P("CT")["elements"][0]["text"].startswith("CONTACT CANARIAS CONTROL") or P("CT")["elements"][0]["code"] == "UM117",
   "CT = CONTACT the next unit with its frequency: " + P("CT")["elements"][0]["text"])
ok(P('TXT EXPECT DIRECT LATER')["elements"][0]["code"] == "UM169" and P('"WHEN READY"')["elements"][0]["text"] == "WHEN READY", "free text")
ok(P(f"{cs} H180")["callsign"] == cs, "callsign first works too")
ok("which point" in raises(P, "D"), "D alone asks for the point")
p = P("CL370 WHEN READY")
ok([e["code"] for e in p["elements"]] == ["UM20", "UM169"] and p["elements"][1]["text"] == "WHEN READY",
   "unknown words: free text after the clearance (CL370 WHEN READY)")
ok(cpdlc.preview(w, "EXPECT LOWER IN 10 MIN", cs)["elements"] == ['"EXPECT LOWER IN 10 MIN" (free text)'], "a free-text uplink, shown as such")

# ---- uplink, WILCO by the pseudo-pilot: the aircraft executes, the system takes it
r = cpdlc.command(w, f"CL{fl} M78", cs)
m = r["message"]
ok(r["kind"] == "uplink" and m["status"] == "open" and m["resp"] == "WU", "uplink sent, waiting for WILCO / UNABLE")
ok(cpdlc.state_view(w)[cs]["open"][0]["id"] == m["id"], "open in the state (the label shows it)")
cpdlc.respond(w, m["id"], "WILCO")
ok(m["status"] == "wilco" and w.entries[cs]["cfl"] == fl and w.entries[cs]["speed"] == "M78", "WILCO: cleared level and speed in the system")
ok(any(p_["instruction"].get("fl") == fl for p_ in w.pending), "WILCO: the aircraft executes the climb")
ok(cpdlc.state_view(w)[cs]["recent"][-1]["status"] == "wilco", "the label shows the WILCO for a few seconds")
m2 = cpdlc.command(w, "CL600", cs)["message"]
ok("UNABLE" in (raises(cpdlc.respond, w, m2["id"], "WILCO") or "").upper(), "WILCO refused for a level the aircraft cannot fly: answer UNABLE")
cpdlc.respond(w, m2["id"], "UNABLE", reason="performance")
ok(m2["status"] == "unable" and w.cpdlc_msgs[-1]["text"] == "UNABLE / DUE TO AIRCRAFT PERFORMANCE", "UNABLE due to aircraft performance")
m3 = cpdlc.command(w, "H200", cs)["message"]
for _ in range(int(cpdlc.LATE_S) + 2):
    w.step(1)
ok(m3.get("late"), "unanswered: flagged late")
cpdlc.respond(w, m3["id"], "STANDBY")
ok(m3["status"] == "standby", "STANDBY keeps it open")
cpdlc.respond(w, m3["id"], "WILCO")
ok(w.entries[cs].get("heading") == 200, "WILCO after STANDBY")

m4 = cpdlc.command(w, "REPORT RIDE CONDITIONS", cs)["message"]
ok(m4["elements"][0]["code"] == "UM169" and m4["resp"] == "R", "a free-text uplink waits for ROGER")
cpdlc.respond(w, m4["id"], "ROGER", text="smooth at FL370")
ok(w.cpdlc_msgs[-1]["text"] == "ROGER / SMOOTH AT FL370", "the pilot answers with free text: " + w.cpdlc_msgs[-1]["text"])

# ---- voice: only the system
cs2 = next(c for c, v in w.cpdlc.items() if v["state"] == "cda" and c not in (cs, inb) and w.aircraft[c].active)
n0 = len(w.cpdlc_msgs)
r = cpdlc.command(w, "H270 S280", cs2, voice=True)
ok(r["kind"] == "entry" and w.entries[cs2]["heading"] == 270 and len(w.cpdlc_msgs) == n0, "Shift+Enter: entered for a voice clearance, nothing sent")

# ---- downlink requests, answers
a.assumed = True
d = cpdlc.downlink(w, cs, "level", fl + 20, reason="weather")
ok(d["text"].startswith("REQUEST CLIMB TO FL") and d["text"].endswith("DUE TO WEATHER"), d["text"])
ok(cpdlc.preview(w, "APP", cs)["elements"] == [f"CLIMB TO FL{fl + 20:03d}"], "APP previews the clearance as asked")
r = cpdlc.command(w, "SBY", cs)
ok(d["status"] == "standby" and r["message"]["text"] == "STANDBY", "SBY answers STANDBY")
r = cpdlc.command(w, "APP", cs)
ok(d["status"] == "answered" and r["message"]["ref"] == d["id"], "APP: the clearance answers the request")
cpdlc.respond(w, r["message"]["id"], "WILCO")
ok(w.entries[cs]["cfl"] == fl + 20, "WILCO to the approval: in the system")
d2 = cpdlc.downlink(w, cs, "direct", nxt)
cpdlc.command(w, "UNA", cs)
ok(d2["status"] == "unable", "UNA: unable")
# a request of the pilot model, sent by data link
rid = w.add_dynamic_request(cs, "altitude", fl + 40, "turbulence")
d3 = cpdlc.downlink(w, cs, None, request_id=rid)
ok(d3["request_id"] == rid and "FL" in d3["text"], "a pending request sent by CPDLC: " + d3["text"])
ok("already sent" in raises(cpdlc.downlink, w, cs, None, request_id=rid), "not twice")
cpdlc.command(w, "UNA", cs)
ok(w.requests[rid].status == "unable", "UNA by data link answers the request")

# ---- transfer: NDA, CONTACT, WILCO -> decontrolled, the connection goes to the next unit
u = cpdlc.unit_info(w, a.next_unit)
if not u["internal"]:
    w.cpdlc[cs]["nda"] = None
    cpdlc.command(w, f"NDA {a.next_unit}", cs)
    ok(w.cpdlc[cs]["nda"] == u["address"], f"NDA {u['address']}")
r = cpdlc.command(w, "CT", cs)
cpdlc.respond(w, r["message"]["id"], "WILCO")
ok(not a.assumed and w.cpdlc[cs]["state"] == "transferred", f"CONTACT WILCO: decontrolled, connection with {w.cpdlc[cs]['cda']}")
r = cpdlc.command(w, "H100", cs)
ok(r["kind"] == "entry", "no connection any more: Enter puts it in the system (voice)")

# ---- solo radar: the simulated pilot answers
w = build_world(load_scenario("s01_basic_crossing"))
for _ in range(3):
    w.step(1)
cs = next(c for c, v in w.cpdlc.items() if v["state"] == "cda" and w.aircraft[c].assumed)
a = w.aircraft[cs]
fl = int(a.alt_ft // 1000) * 10 + 20
m = cpdlc.command(w, f"CL{fl}", cs)["message"]
m2 = cpdlc.command(w, "CL600", cs)["message"]
for _ in range(60):
    w.step(1)
ok(m["status"] == "wilco" and a.cleared_alt_ft == fl * 100, "solo: the pilot answers WILCO and climbs")
ok(m2["status"] == "unable", "solo: the pilot answers UNABLE to a level it cannot fly")

# ---- coordination chat: sender and destination on every message
from atcsim import chat  # noqa: E402
w = build_world(load_scenario("p01_ws_two_merges"))
st = chat.stations(w)
own = chat.own_station(w)
ok(own == "GMAC WS" and st["GMAC WN"]["kind"] == "sector" and st["GCCC"]["kind"] == "unit", f"stations: {', '.join(st)}")
cs = next(iter(w.aircraft))
m = chat.send(w, "GCCC", own, "request FL360 at GUNET", callsign=cs)
ok(m["from"] == "GCCC" and m["to"] == own and m["callsign"] == cs and m["text"] == "request FL360 at GUNET", "a message names its sender and destination")
r = chat.send(w, own, "GCCC", "APPROVED", ref=m["id"])
ok(r["ref"] == m["id"] and r["callsign"] == cs, "an answer refers to the message (and its aircraft)")
ok(chat.resolve(w, "WN") == "GMAC WN", "a sector by its id")
ok("same station" in raises(chat.send, w, own, own, "x"), "not to itself")
ok("unknown station" in raises(chat.send, w, "XXXX", own, "x"), "unknown station refused")
ok([x["id"] for x in chat.messages(w, station="GCCC")] == [m["id"], r["id"]] and not chat.messages(w, station="LPPC"), "one station's messages")
ok(any(e["type"] == "chat" and "GCCC → GMAC WS" in e["text"] for e in w.events), "recorded in the events (replay, recording)")
print("cpdlc and chat checks passed")
