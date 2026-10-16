"""Fill every scenario with complete flight plans: a UTC start time for the scenario, and for each aircraft its
departure and destination (training scenarios had none) and an "fpl" object (SSR code, registration, Mode S address,
EOBT, alternate, equipment, operator, persons on board, remarks). The values come from the generator
(atcsim/flightplan.py) and can then be edited in the designer.   python -m tools.fill_flightplans [names...]"""
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from atcsim.scenario import SCENARIO_DIR, build_world  # noqa: E402

START = {"c01": "13:00", "c02": "15:30", "o01": "02:00", "o02": "21:00", "w01": "10:00", "w02": "17:00"}


def fill(path: str) -> int:
    with open(path) as f:
        scn = json.load(f)
    name = scn.get("name", os.path.basename(path)[:-5])
    scn.setdefault("start_utc", START.get(name[:3], "%02d:00" % (8 + sum(map(ord, name)) % 11)))
    for a in scn.get("aircraft", []):
        a.pop("fpl", None)
    w = build_world(scn)
    n = 0
    for a in scn.get("aircraft", []):
        ac = w.aircraft[a["callsign"].upper()]
        fp = ac.flight_plan
        if not a.get("dep"):
            a["dep"] = fp["departure"]
        if not a.get("dest") and not (ac.destination and fp["destination"] == ac.destination):
            a["dest"] = fp["destination"]
        a["fpl"] = {"ssr": fp["ssr"], "reg": fp["registration"], "code": fp["mode_s_address"], "eobt": fp["eobt"],
                    "altn": fp["alternate"], "equipment": fp["equipment"], "opr": fp["operator"], "pob": fp["pob"]}
        if fp.get("remarks"):
            a["fpl"]["rmk"] = fp["remarks"]
        n += 1
    build_world(scn)                         # still valid
    with open(path, "w") as f:
        json.dump(scn, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return n


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(fn[:-5] for fn in os.listdir(SCENARIO_DIR) if fn.endswith(".json"))
    for nm in names:
        print(nm, fill(os.path.join(SCENARIO_DIR, nm + ".json")), "flight plans")
