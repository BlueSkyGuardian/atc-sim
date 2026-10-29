"""The scenario generator: the traffic asked for (no more), the routes as the RAD has them, the Canaries traffic from
its real aerodromes, entry waves at VASTO, cruise levels by flight length (none low at TAKAV), airlines and types
that fit each flight; and an aircraft level at its cruise level flies its Mach. Run:  python -m tests.test_generator"""
import collections
import sys
import warnings

warnings.filterwarnings("ignore")

from atcsim import generator as g  # noqa: E402
from atcsim.capacity import traffic_load  # noqa: E402
from atcsim.scenario import build_world  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m, flush=True)
    if not c:
        sys.exit(1)


ALL = ["WN", "WS", "ON", "OS"]
runs = [g.generate({"control": ALL, "seed": s, "aircraft_per_hour": 30}) for s in (1, 2)]
ac = [a for s in runs for a in s["aircraft"]]

# ---------------------------------------------------------------- how much traffic
ok(all(len(s["aircraft"]) == 30 for s in runs), f"30 flights/h for an hour: {[len(s['aircraft']) for s in runs]} aircraft, planted ones and those in at the start included")
load = [traffic_load(s)["flights_per_hour"] for s in runs]
ok(all(abs(x - 30) <= 3 for x in load), f"the exercise's load reads as asked: {load} flights/h")
s45 = g.generate({"control": ["WS"], "seed": 3, "aircraft_per_hour": 20, "duration_min": 45})
ok(len(s45["aircraft"]) == 15, f"20/h over 45 min: {len(s45['aircraft'])} aircraft")

# ---------------------------------------------------------------- routes (RAD)
def follows(r, a, b):
    return a in r and r.index(a) + 1 < len(r) and r[r.index(a) + 1] == b


desum = [a for a in ac if "DESUM" in a["route"]]
ok(all(follows(a["route"], "DESUM", "AKUDA") and a["dep"] in ("GCRR", "GCFV") for a in desum),
   f"DESUM DCT AKUDA, departures GCRR / GCFV only ({len(desum)} flights)")
samar_tiggi = [a for a in ac if "SAMAR" in a["route"] and "TIGGI" in a["route"]]
ok(all("NEVTU" in a["route"] for a in samar_tiggi), f"SAMAR NEVTU TIGGI ({len(samar_tiggi)} flights)")
takav_vedod = [a for a in ac if follows(a["route"], "TAKAV", "VEDOD")]
ok(all(a["dest"] in ("GCRR", "GCFV") for a in takav_vedod), f"TAKAV VEDOD: arrivals GCRR / GCFV only ({len(takav_vedod)})")
ib_sa = [a for a in ac if follows(a["route"], "IBALU", "SAMAR")]
ok(all(a["dest"] in ("GCLP", "GCXO") for a in ib_sa), f"IBALU DCT SAMAR: arrivals GCLP / GCXO only ({len(ib_sa)})")
c = g._Ctx({**g.DEFAULTS, "control": ALL, "seed": 1})
ok(not c.flow_errors and {"eur_canaries_ibalu_samar", "med_canaries_ibalu_samar"} <= {r["flow"] for r in c.routes},
   "every flow is built over the airspace's points and airways")

# ---------------------------------------------------------------- the Canaries and VASTO
ends = collections.Counter(x for a in ac for x in (a["dep"], a["dest"]))
ok(ends["GCLP"] >= 3 and ends["GCXO"] >= 1 and ends["GCTS"] >= 1, f"Canaries traffic: GCLP {ends['GCLP']}, GCTS {ends['GCTS']}, GCXO {ends['GCXO']}")
on = [g.generate({"control": ["ON", "OS"], "seed": s, "aircraft_per_hour": 30}) for s in (4, 5)]
ac_o = [a for s in on for a in s["aircraft"]]
vasto = [a for a in ac_o if "VASTO" in a["route"]]
exits = collections.Counter(next(x for x in ("AKUDA", "TIGGI", "BAROK") if x in a["route"]) for a in vasto
                            if any(x in a["route"] for x in ("AKUDA", "TIGGI", "BAROK")))
ok(len(vasto) >= 8 and len(exits) >= 2, f"VASTO is busy, its traffic split between exits: {dict(exits)}")
waves = [n for s in runs + on for n in s["design_notes"].split(". ") if n.startswith("Wave at")]
ok(len(waves) >= 3, "entry waves planted: " + waves[0][:120])
tiggi_barok = sum(1 for a in ac_o if "TIGGI" in a["route"] or "BAROK" in a["route"])
ok(tiggi_barok >= len(ac_o) * 0.25, f"TIGGI and BAROK carry a good share of the oceanic traffic ({tiggi_barok} of {len(ac_o)})")

# ---------------------------------------------------------------- levels
tk = [a for a in ac + ac_o if "TAKAV" in a["route"]]
ok(tk and min(a["fl"] for a in tk) >= 300, f"at TAKAV at cruise: FL{sorted(a['fl'] for a in tk)}")
jets = [a["fl"] for a in ac + ac_o if a["type"] not in ("C130", "A400") and not a.get("rfl")]
mid = sum(1 for x in jets if 330 <= x <= 390) / len(jets)
ok(mid >= 0.6, f"most cruise between FL330 and FL390 ({mid:.0%})")
mil = [a for a in ac + ac_o if a.get("military")]
ok(all(a["dep"].startswith("GM") or a["dest"].startswith("GM") for a in mil), f"military transports only to / from Morocco ({len(mil)})")
low = [a for a in ac + ac_o if a["fl"] < 250 and not a.get("rfl") and not a.get("military")]
ok(not low, f"no airliner or business jet low over the ocean: {[(a['callsign'], a['fl']) for a in low][:5]}")

# ---------------------------------------------------------------- airlines and types
nam = [a for a in ac + ac_o if any(x.startswith(("K", "CY")) for x in (a["dep"], a["dest"]))]
bad = [(a["callsign"], a["type"], a["dep"], a["dest"]) for a in nam if not (a["callsign"][:3] in ("RAM", "ACA", "TSC") and a["type"] in g.LONG)]
ok(not bad and nam, f"North America - Casablanca: Royal Air Maroc (or Air Canada / Transat from Canada) on wide-bodies ({len(nam)})"
   + (f" - not: {bad}" if bad else ""))
lp = [a for a in ac + ac_o if g._long_pair(a["dep"], a["dest"])]
ok(all(a["type"] in g.LONG for a in lp), f"long-haul flights on wide-bodies ({len(lp)})")
ok(all(x in g.HUBS or g._area(x) in g.LONG_AREAS for a in lp for x in (a["dep"], a["dest"])),
   "and between hubs: " + ", ".join(sorted({f"{a['dep']}-{a['dest']}" for a in lp})[:8]))
mad = [a for a in ac + ac_o if "GMMN" in (a["dep"], a["dest"]) and not a.get("military")]
ok(sum(a["callsign"].startswith("RAM") for a in mad) >= len(mad) * 0.4, f"Casablanca flights mostly Royal Air Maroc ({sum(a['callsign'].startswith('RAM') for a in mad)} of {len(mad)})")
uk_can = [a for a in ac + ac_o if {g._area(a["dep"]), g._area(a["dest"])} == {"UK", "CAN"}]
ok(all(a["type"] not in ("E190", "E195", "CRJ9") for a in uk_can), f"UK - Canaries: no regional jets ({len(uk_can)})")

# ---------------------------------------------------------------- Mach at cruise
w = build_world(runs[0])
seen = 0
for a in w.aircraft.values():
    if a.perf.icao in ("C130", "A400", "C550"):
        continue
    a.alt_ft = a.cleared_alt_ft = 31000.0
    a.spd_cas = a.spd_mach = None
    a.spd_ias_now = False
    if a.perf.crossover_ft > 31000.0:
        seen += 1
        ok(a.speed_target()[0] == "mach", f"{a.callsign} ({a.perf.icao}, crossover FL{int(a.perf.crossover_ft // 100)}) level at FL310: flies its Mach {a.speed_target()[1]:.2f}")
        a.cleared_alt_ft = 37000.0
        ok(a.speed_target()[0] == "cas", "  climbing through FL310 (below its crossover): its IAS")
        a.cleared_alt_ft, a.spd_cas = 31000.0, 280.0
        ok(a.speed_target() == ("cas", 280.0), "  level with an IAS assigned by ATC: that IAS")
        if seen >= 2:
            break
ok(seen >= 1, "aircraft whose climb crossover is above their cruise level checked")
print("generator checks passed")
