"""Aircraft that leave the sector keep flying (onward route), stay assumed until the controller decontrols them,
and the pseudo-pilot keeps flying them.   python -m tests.test_outside"""
from atcsim.scenario import build_world, load_scenario


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


w = build_world(load_scenario("o01_oceanic_light_wx"))
ex = None
for _ in range(4000):
    w.step(1)
    for a in w.aircraft.values():
        if a.active and a.on_frequency and not a.assumed:
            w.issue({"callsign": a.callsign, "type": "assume"})
        if a.status == "exited" and ex is None and a.route_after:
            ex, t_ex = a.callsign, w.t
    if ex and w.t - t_ex > 5:
        break
a = w.aircraft[ex]
ok(a.outside and a.assumed, f"{ex} left at {a.exit_point}, still assumed")
p0 = a.pos
for _ in range(60):
    w.step(1)
ok(((a.x - p0[0]) ** 2 + (a.y - p0[1]) ** 2) ** .5 > 5, "keeps flying in the next sector")
ok(a.next_wp in a.route_after or a.nav_mode == "heading", f"follows its onward route ({a.next_wp})")
ok(ex in [t["callsign"] for t in w.state()["transferred"]], "still on the picture")
ok(w.issue({"callsign": ex, "type": "heading", "heading": 90})["status"] == "rejected", "controller instructions refused")
ok(w.issue({"callsign": ex, "type": "heading", "heading": 90}, by_pilot=True)["status"] != "rejected", "pilot still flies it")
ok(w.issue({"callsign": ex, "type": "release"})["status"] == "executed" and a.released and not a.assumed, "decontrolled")
r = w.issue({"callsign": ex, "type": "altitude", "fl": a.fl // 10 * 10 - 20}, by_pilot=True)
ok(r["status"] != "rejected", f"pilot still flies it after ({r.get('reason')})")
for _ in range(4000):
    w.step(1)
    if a.gone or w.finished:
        break
ok(a.gone or w.finished, "eventually leaves the picture")
print("outside checks passed")
