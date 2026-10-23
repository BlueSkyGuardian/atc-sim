"""The Casablanca FIR in the Agadir airspace: CTA Agadir unchanged, the four Casablanca sectors, the ENR 5.1 areas.
   python -m tests.test_fir"""
import json
import os

from atcsim import airspace as A
from atcsim.geometry import point_in_polygon
from atcsim.scenario import build_world, load_scenario


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


a = A.get("agadir")
ok(a.controlled == ["ON", "WN", "WS", "OS"] and [s.id for s in a.sectors] == ["ON", "WN", "WS", "OS"],
   "default: the four Agadir sectors, ours")
ok(abs(abs(A._signed_area(a.polygon)) - 73867.553) < 0.01, "CTA Agadir area unchanged")
ok([round(v, 2) for v in a.bbox] == [-217.38, -179.79, 245.75, 181.95], "CTA Agadir bbox unchanged (view, weather)")
ok({"DOMOG", "SALCA", "MARJA", "TERTO", "ROLRO"} <= set(a.boundary_points), "Agadir transfer points")
ok([s.id for s, _ in a.fir_sectors] == ["N", "E", "C", "SS"], "Casablanca sectors drawn as the rest of the FIR")
ok(a.point_info["FES"].get("external") and a.point_info["LAKAM"].get("external"),
   "Casablanca points are neighbouring points for Agadir")
ok(not a.point_info["IBALU"].get("external"), "Agadir points are ours")
aw = a.airways
ok(aw["UN857"]["one_way"] and aw["UN857"]["points"][0] == "KORNO", "UN857 southbound only (ENR 3.2)")
ok(aw["UH899"]["one_way"] and aw["UH899"]["points"] == ["SAK", "ODAXA", "LAPNO", "IBALU"], "UH899 SAK to IBALU only (ENR 3.1)")
ok(not aw["UA411"]["one_way"] and aw["UA411"]["points"][0] == "ORSUP", "UA411 both ways, ORSUP to IBALU (ENR 3.1)")
ok("SAK" in aw["UR722"]["points"] and aw["UM240"]["points"][-1] == "ERA", "NDB and VOR points kept in the sequences")
ok(a.against_one_way(["VEDOD", "TOVRA"]) == ["UN857"], "flying UN857 northbound is flagged")
ok(not a.against_one_way(["PELAX", "SLK"]), "a leg also on a two-way route (UB11) is not")
d = a.describe()
ok(len(d["fir"]["sectors"]) == 4 and len(d["fir"]["outline"]) > 50, "describe: fir outline and sectors")
dom = a.waypoints["DOMOG"]
ok(any(point_in_polygon((dom[0] + 0.3, dom[1]), s.polygon) for s, _ in a.fir_sectors), "DOMOG on the Agadir/Casablanca line")

fr = {s.id: s for s, _ in a.fir_sectors}
ok([fr[k].freq for k in ("N", "E", "C", "SS")] == ["125.500", "125.100", "133.100", "126.700"], "ENR 2.1 frequencies")
for k, pt in (("N", "TNR"), ("N", "RBT"), ("E", "FES"), ("E", "OJD"), ("C", "MAK"), ("SS", "ADM"), ("SS", "BULIS")):
    p = a.waypoints[pt]
    near = [s for s in fr.values() if point_in_polygon(p, s.polygon)]
    ok(fr[k] in near or (pt == "BULIS" and pt in A.get("agadir", ["SS"]).boundary_points), f"{pt} in sector {k}")

tot = sum(abs(A._signed_area(s.polygon)) for s in fr.values()) + sum(abs(A._signed_area(s.polygon)) for s in a.sectors)
ok(abs(tot - abs(A._signed_area(a.fir_outline))) < 1.0, f"the eight sectors tile the FIR ({tot:.0f} NM²)")

c = A.get("agadir", ["N", "E", "C", "SS"])
ok(c.unit == "ACC CASABLANCA" and c.notes["cpdlc"]["address"] == "GMMM" and "GMAC" in c.notes["cpdlc"]["units"],
   "Casablanca control: own unit Casablanca, Agadir adjacent")
ok(c.point_info["IBALU"].get("external") and not c.point_info["LAKAM"].get("external"), "points swap sides")
ok(not {"TERTO", "MARJA", "SALCA"} & {q["fix"] for q in c.default_sequencing}, "no Agadir arrival sequencing from Casablanca")
try:
    A.get("agadir", ["WN", "N"])
    ok(False, "mixed control areas refused")
except ValueError:
    ok(True, "mixed control areas refused")

w = build_world(load_scenario("s20_casablanca_north"))
ok(w.env.airspace.controlled == ["N"], "s20 controls Casablanca North")
x = w.aircraft["RAM970"]
ok(x.prev_unit == "GMAC" and x.next_unit == "E", f"from Agadir, to East: {x.prev_unit} {x.next_unit}")
ok(len([k for k in w.areas if k.startswith("GM")]) == 45, "the 45 ENR 5.1 areas are in the scenario")
ok(w.areas["GMP02"].active and w.areas["GMP02"].relevant and not w.areas["GMD21"].active, "GMP active, GMD inactive")
for _ in range(620):
    w.step(1)
ok(w.areas["GMD21"].active, "GMD 21 activates on its schedule")
snap = w.areas["GMD21"].snapshot(w.t)
ok(snap["designator"] == "GMD 21" and snap["published"], "designator and published flag")

w2 = build_world(load_scenario("s18_exit_sequence_three"))
ok(not w2.areas["GMP02"].relevant, "a Casablanca area does not count for an Agadir sector")
s = load_scenario("s18_exit_sequence_three")
s["published_areas"] = False
ok(not build_world(s).areas, "published_areas false leaves them out")

# arrivals handed down into a TMA at a fix inside the sector, departures released climbing
w = build_world(load_scenario("s21_casablanca_north_gmmn_gmme"))
a, d0 = w.aircraft["RAM763"], w.aircraft["RAM1474"]
ok(a.arr.get("fix") == "LAKAM" and a.destination == "GMMN" and a.next_unit == "GMMN", "GMMN arrival to LAKAM, next unit GMMN approach")
ok(d0.prev_unit == "GMMN" and d0.rfl == 300 and d0.initial_cfl == 190, "GMMN departure released by approach climbing FL190, asks FL300")
for _ in range(1200):
    w.step(1)
    for x in w.aircraft.values():
        if x.active and x.on_frequency and not x.assumed:
            w.issue({"callsign": x.callsign, "type": "assume"})
    if w.t == 900:
        ok(d0.cleared_alt_ft == 19000 and d0.alt_ft > 15500, f"climbing to FL190 after its release ({d0.alt_ft:.0f} ft)")
ok(a.status == "exited" and a.exit_point == "LAKAM", "the arrival handed over at LAKAM")
cr = w.metrics()["sequencing"]["crossings"]
ok("LAKAM" in cr and any(c["callsign"] == "RAM763" for c in cr["LAKAM"]), "and scored at the fix")
s2 = build_world(load_scenario("s22_casablanca_south_gmmx_gmad"))
ok(s2.env.airspace.controlled == ["C", "SS"] and {"MABAP", "AGALI", "TEKNA", "MAXOR"} <= set(s2.seq),
   "South (C+SS): GMMX / GMAD / GMMZ sequencing fixes")

# after the handover: the next units bring arrivals down and land them; a reroute into the TMA keeps its end
w = build_world(load_scenario("s21_casablanca_north_gmmn_gmme"))
sent = False
w._check_finished = lambda: None
for _ in range(3300):
    w.step(1)
    if w.t == 1300:
        held = [x.callsign for x in w.aircraft.values() if x.outside and getattr(x, "app", None)]
        ok(not held or all(w.aircraft[c].released for c in held), "not taken over before they are decontrolled")
    for x in w.aircraft.values():
        if x.active and x.on_frequency and not x.assumed:
            w.issue({"callsign": x.callsign, "type": "assume"})
    for r in list(w.requests.values()):
        if r.status == "pending" and r.kind == "descent":
            w.issue({"callsign": r.callsign, "type": "altitude", "fl": r.value})
    for x in w.aircraft.values():               # the controller decontrols what it has transferred
        if x.outside and x.assumed and getattr(x, "exit_t", 0) and w.t - x.exit_t > 60:
            w.issue({"callsign": x.callsign, "type": "release"})
    t = w.aircraft["TAP1032"]
    if t.assumed and t.entered and not sent:
        w.issue({"callsign": "TAP1032", "type": "reroute", "points": ["BIRVO", "DEVNO"]})
        sent = True
ok(w.aircraft["TAP1032"].exit_point == "DEVNO" and any(e.get("type") == "fix_crossing" and e.get("via") == "DEVNO"
   for e in w.events), "rerouted via DEVNO: handed over there (LAKAM not added back), judged against the LAKAM LoA")
landed = [e["callsign"] for e in w.events if e.get("type") == "landed"]
ok({"RAM763", "RAM203", "RAM977"} <= set(landed), f"arrivals land after the handover ({len(landed)} landed)")
ok(all(w.aircraft[c].gone for c in landed), "and leave the picture")
gm = [e["time_s"] for e in w.events if e.get("type") == "landed" and e.get("aerodrome") == "GMMN"]
ok(all(b - a >= 140 for a, b in zip(gm, gm[1:])), f"landing intervals kept by approach: {[(e["callsign"], e["time_s"]) for e in w.events if e.get("type") == "landed"]}")
hold_ev = [e for e in w.events if e.get("type") == "approach_holding"]
ok(all(e.get("fix") in ("KOTAG", "DEVNO", "NASRO", "SLK", "BIRVO") for e in hold_ev if e.get("fix") is not None),
   f"holding at GMMN's published holds: {[e['text'] for e in hold_ev]}")

# published holds: loaded with the aerodromes, one lap is a racetrack on the holding side of the inbound track
from atcsim import approach as _app
ads = {a.id: a for a in w.aerodromes.values()}
ok({h["fix"] for h in ads["GMMN"].holdings} == {"KOTAG", "DEVNO", "NASRO", "SLK", "BIRVO"}
   and {h["fix"] for h in ads["GCLP"].holdings} >= {"ENETA", "LALTO", "TOMOS", "LPC"}, "published holds loaded")
h = next(h for h in ads["GCLP"].holdings if h["fix"] == "ENETA")
pts, lap = _app._lap(h, 220.0, 8000.0)
import math as _m
th = _m.radians(h["inbound_true"])
side = [(p[0] - h["x"]) * _m.cos(th) - (p[1] - h["y"]) * _m.sin(th) for p in pts[:-1]]
ok(all(v > -0.01 for v in side) and 10 < lap < 25, f"ENETA right-hand racetrack, lap {lap:.1f} NM")
print("FIR ok")
