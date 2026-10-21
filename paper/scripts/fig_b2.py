from common import *
import json, time, numpy as np
from atcsim.session import Session
from atcsim.solve import traj, cpa, resolve
SC = ["s02_climb_request_into_crossing", "s03_descent_through_opposite", "s08_simultaneous_level_requests", "s10_busy_mixed",
      "p01_ws_two_merges", "p02_wn_merges_and_climbs", "p05_ws_storms_arrivals", "p06_wn_capacity_sequencing", "c01_cta_moderate_wx", "w01_west_scattered_cb"]
TIMES = list(range(120, 1561, 120))
DT = [1, 5, 10, 20, 30, 60]
def sampled(A, B, dt, H):
    tt = np.arange(0, H + 1e-6, dt)
    xa, ya, za = A.at(tt); xb, yb, zb = B.at(tt)
    sv = np.array([cpa._sv(p, q, A.non_rvsm or B.non_rvsm, False) for p, q in zip(za, zb)])
    m = np.abs(za - zb) < sv
    d = np.hypot(xa - xb, ya - yb)
    return float(d[m].min()) if m.any() else None
rows, sits, confs = [], [], []
for sc in SC:
    try:
        s = Session(sc, mode="stepped", autostart=False)
    except Exception as e:
        print("skip", sc, e); continue
    s.stop(); s.solo = True; w = s.world
    for t in TIMES:
        if t > w.duration_s: break
        while w.t < t and not w.finished:
            s._tick()
        if w.finished: break
        for a in w.aircraft.values():
            if a.active and a.on_frequency:
                a.assumed = True
        H = 600.0
        t0 = time.perf_counter(); T = traj.build_all(w, H); t1 = time.perf_counter()
        M = cpa.matrix(T, float(w.sep_h)); t2 = time.perf_counter()
        cs = sorted(T); n = len(cs)
        for i in range(n):
            for j in range(i + 1, n):
                A, B = T[cs[i]], T[cs[j]]
                p = cpa.pair(A, B, float(w.sep_h))
                if p.d_min_h > 60: continue
                climbing = bool(np.ptp(A.z) > 500 or np.ptp(B.z) > 500)
                rows.append({"d": None if math.isinf(p.d_min) else p.d_min, "dh": p.d_min_h, "conf": p.conflict, "sh": float(w.sep_h), "vert": climbing,
                             **{f"s{dt}": sampled(A, B, dt, H) for dt in DT}})
        t3 = time.perf_counter()
        r = resolve.solve(w, None, H, rollout=2); t4 = time.perf_counter()
        sits.append({"sc": sc, "t": t, "n": n, "pairs": n * (n - 1) // 2, "traj_ms": (t1 - t0) * 1e3, "matrix_ms": (t2 - t1) * 1e3,
                     "solve_s": t4 - t3, "conflicts": len(r["sets"])})
        for x in r["sets"]:
            sols = x["solutions"]
            confs.append({"sc": sc, "t": t, "pair": x["pair"], "t_first": x["t_first"], "d_min": x["d_min"], "move": x["move"],
                          "n_sol": len(sols), "kinds": [s_.kind for s_ in sols], "best": sols[0].kind if sols else None,
                          "confirmed": sum(1 for s_ in sols if s_.confirmed), "conf_min": [s_.confirmed_min_nm for s_ in sols if s_.confirmed],
                          "pred_min": [None if math.isinf(s_.d_min) else s_.d_min for s_ in sols if s_.confirmed],
                          "hj": {k: v["turn_enough"] for k, v in x["hj"].items()}, "priority_moved": [s_.priority_moved for s_ in sols[:1]]})
        print(sc, t, n, len(r["sets"]), round(t4 - t3, 2), flush=True)
    s.stop()
json.dump({"rows": rows, "sits": sits, "confs": confs}, open("/home/claude/paper/data/fig_b2.json", "w"), indent=1, default=str)
print("done")
