"""Numbers, tables and the last figures of the paper, from the data files (so the text matches the data)."""
from common import *
import json, numpy as np
D = "/home/claude/paper/data"
A = json.load(open(f"{D}/fig_a.json"))
B = json.load(open(f"{D}/fig_b2.json"))
HV = np.array(json.load(open(f"{D}/hjval.json")))
R = json.load(open(f"{D}/runs2.json"))
from atcsim.solve import hj, resolve
N = {}
def put(k, v):
    N[k] = v
import time as _t
_s = situation(); _w = _s.world
_r = resolve.solve(_w, None, 600.0, rollout=0)
_sol = _r["sets"][0]["solutions"][0]
_ts = []
for _ in range(3):
    _sol.confirmed = None; _t0 = _t.perf_counter(); resolve.confirm(_w, _sol, 600.0); _ts.append(_t.perf_counter() - _t0)
put("ROLLval", f"{np.median(_ts):.1f}")
put("Hval", "600"); put("Mval", f"{resolve.MARGIN_NM:g}"); put("MUval", f"{hj.MARGIN:g}")

# ---- worked example
put("exN", A["n_active"]); put("exSh", f"{A['sep_h']:g}"); put("exT", int(A["t"]))
put("exA", A["mover"]); put("exB", A["other"])
put("exTf", f"{A['cpa900'][2]:.0f}"); put("exTmin", f"{A['cpa900'][1]:.0f}"); put("exDmin", f"{A['cpa900'][0]:.1f}")
sc = A["speed_curve"]; put("exSpeedMax", f"{max(d for _, d in sc):.1f}"); put("exMachLo", f"{sc[0][0]:.2f}"); put("exMachHi", f"{sc[-1][0]:.2f}")
put("exNsol", len(A["sols"])); put("exGrid", A["n_grid"]); put("exGridMs", f"{1e3 * A['t_grid'] / A['n_grid']:.1f}")
arcs = A["free_arcs"]; put("exArcs", ", ".join(f"{a:03d}--{b:03d}" for a, b in arcs))
put("exTrk", f"{A['trk']:03.0f}")
best = [s for s in A["sols"] if s[1] in A["optimal"]]
put("exBest", " and ".join(f"\\emph{{{s[1].replace(A['mover'] + ' ', '')}}} (confirmed {s[5]}\\NM)" for s in best))
vos = A["vo"]; put("exVO", ", ".join(f"{k} {v}$^\\circ$" for k, v in vos.items()))

# ---- CPA vs sampling
rows = B["rows"]
DT = [1, 5, 10, 20, 30, 60]
lines = []
for dt in DT:
    e = np.array([r[f"s{dt}"] - r["d"] for r in rows if r["d"] is not None and r[f"s{dt}"] is not None])
    lost = sum(1 for r in rows if r["d"] is not None and r[f"s{dt}"] is None)
    miss = sum(1 for r in rows if r["conf"] and not (r[f"s{dt}"] is not None and r[f"s{dt}"] < r["sh"]))
    lines.append((dt, np.median(e), np.percentile(e, 95), e.max(), lost, miss))
nconf = sum(1 for r in rows if r["conf"]); put("cpaPairs", len(rows)); put("cpaConf", nconf)
put("cpaSits", len(B["sits"]))
tab = "\n".join(f"{dt} & {md:.2f} & {p95:.2f} & {mx:.2f} & {lost} & {miss} \\\\" for dt, md, p95, mx, lost, miss in lines)
open("/home/claude/paper/tab_sampling.tex", "w").write(tab + "\n\\bottomrule\n")
put("cpaMaxTwenty", f"{lines[3][3]:.2f}"); put("cpaMaxSixty", f"{lines[5][3]:.2f}")
fig, ax = plt.subplots(figsize=(3.3, 2.5))
ax.plot(DT, [l[3] for l in lines], marker="o", ms=3, color=C1, label="maximum")
ax.plot(DT, [l[2] for l in lines], marker="o", ms=3, color=C2, label="95th percentile")
ax.set_xlabel(r"sampling step $\Delta$ (s)"); ax.set_ylabel(r"$d_\Delta - d^*$ (NM)"); ax.legend(fontsize=7.5)
fig.tight_layout(); fig.savefig(f"{FIG}/f2_sampling.pdf"); plt.close(fig)

# ---- HJ validation
Vv, E = HV[:, 3], HV[:, 5]
m = np.abs(E) > 0.5
put("hjN", len(HV)); put("hjAgree", f"{100 * np.mean(np.sign(Vv[m]) == np.sign(E[m])):.1f}")
put("hjUnav", int(np.sum(E < 0))); put("hjFalse", int(np.sum((Vv > hj.MARGIN) & (E < 0)))); put("hjCons", int(np.sum((Vv <= hj.MARGIN) & (E > 0))))
nb = (E > -5) & (E < 3)
put("hjMedNear", f"{np.median(Vv[nb] - E[nb]):.2f}"); put("hjMeanNear", f"{np.mean(Vv[nb] - E[nb]):.2f}")
mir = np.abs(HV[:, 3] - HV[:, 4]) > 1.5
put("hjMirN", int(mir.sum())); put("hjMirOk", f"{np.mean(np.abs(HV[mir, 3] - E[mir])):.2f}"); put("hjMirBad", f"{np.mean(np.abs(HV[mir, 4] - E[mir])):.2f}")

# ---- solver statistics on frozen situations
sits, confs = B["sits"], B["confs"]
ns = np.array([s["n"] for s in sits]); mt = np.array([s["matrix_ms"] for s in sits])
put("stN", f"{ns.min()}--{ns.max()}"); put("stMatMed", f"{np.median(mt):.0f}"); put("stMatMax", f"{mt.max():.0f}")
fut = [c for c in confs if c["t_first"] is not None and c["t_first"] > 0]
cur = [c for c in confs if not (c["t_first"] is not None and c["t_first"] > 0)]
solv = [c for c in fut if c["n_sol"] > 0]
put("stDistinct", len({(c["sc"], tuple(c["pair"])) for c in confs})); put("stScn", len({c["sc"] for c in confs}))
put("stConf", len(confs)); put("stFut", len(fut)); put("stCur", len(cur)); put("stSolved", len(solv))
put("stSolvedPct", f"{100 * len(solv) / max(1, len(fut)):.0f}")
kinds = {}
for c in solv:
    kinds[c["best"]] = kinds.get(c["best"], 0) + 1
put("stKinds", ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
cm = [x for c in solv for x in c["conf_min"]]; pm = [x for c in solv for x in c["pred_min"]]
put("stConfirmed", len(cm))
sv = np.array([s["solve_s"] for s in sits if s["conflicts"]])
put("stSolveMed", f"{np.median(sv):.1f}" if len(sv) else "--"); put("stSolveMax", f"{sv.max():.1f}" if len(sv) else "--")
unsolved_close = [c for c in fut if c["n_sol"] == 0]
put("stUnsolved", len(unsolved_close)); put("stUnsolvedTf", f"{np.median([c['t_first'] for c in unsolved_close]):.0f}" if unsolved_close else "--")
fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.5))
ax = axs[0]
ax.scatter(ns * (ns - 1) / 2, mt, s=8, color=C1, alpha=0.7, lw=0)
ax.set_xlabel("pairs $n(n-1)/2$"); ax.set_ylabel("trajectories + matrix (ms)")
ax.set_title("Conflict matrix of a situation", fontsize=8.5)
ax = axs[1]
from atcsim.session import Session as _S
_sh = {}
for c in solv:
    if c["sc"] not in _sh:
        _x = _S(c["sc"], mode="stepped", autostart=False); _x.stop(); _sh[c["sc"]] = float(_x.world.sep_h)
pmx = [p - _sh[c["sc"]] for c in solv for p in c["pred_min"]]
cmx = [q - _sh[c["sc"]] for c in solv for q in c["conf_min"]]
if pmx:
    ax.scatter(pmx, cmx, s=10, color=C1, alpha=0.7, lw=0)
    hi = max(pmx + cmx) + 1
    ax.plot([0, hi], [0, hi], color=MUTED, lw=0.8)
    ax.axhline(0, color=C2, lw=0.8, ls="--"); ax.axvline(resolve.MARGIN_NM, color=C2, lw=0.8, ls=":")
    put("stBelowPred", sum(1 for a_, b_ in zip(pmx, cmx) if b_ < a_ - 0.5))
    put("stWorstGap", f"{-min(b_ - a_ for a_, b_ in zip(pmx, cmx)):.1f}")
ax.set_xlabel(r"predicted $\Phi(u) - S_h$ (NM)"); ax.set_ylabel(r"rollout closest $-\ S_h$ (NM)")
ax.set_title("Confirmed solutions: model vs simulation", fontsize=8.5)
fig.tight_layout(); fig.savefig(f"{FIG}/f7_solver_stats.pdf"); plt.close(fig)

# ---- closed loop
res = {}
for r in R.values():
    res.setdefault(r["scenario"], {})[r["kind"]] = r
order = sorted(res)
tl = []
tot = {"baseline": [0, 0, 0, []], "solver": [0, 0, 0, []]}
for scn in order:
    cells = []
    for k in ("baseline", "solver"):
        r = res[scn].get(k)
        if not r or r["status"] != "done":
            cells += ["--"] * 4; continue
        e = r["evaluation"]
        lv = sum(1 for c in r["clearances"] if c.get("cfl") is not None)
        lat = sum(1 for c in r["clearances"] if c.get("heading") is not None or c.get("direct") is not None)
        sp = sum(1 for c in r["clearances"] if c.get("speed") is not None)
        cells += [f"{r['score']:.1f}", str(e["losses"]), f"{sp}/{lat}/{lv}", f"{e['min_sep_nm']:.1f}" if e.get("min_sep_nm") is not None else "--"]
        tot[k][0] += r["score"]; tot[k][1] += e["losses"]; tot[k][3].append(r["score"])
    tl.append(scn.replace("_", "\\_") + " & " + " & ".join(cells) + " \\\\")
TOT = f"Mean & {np.mean(tot['baseline'][3]):.1f} & {tot['baseline'][1]} & & & {np.mean(tot['solver'][3]):.1f} & {tot['solver'][1]} & & \\\\"
open("/home/claude/paper/tab_runs.tex", "w").write("\n".join(tl) + "\n\\midrule\n" + TOT + "\n\\bottomrule\n")
for k in tot:
    put(f"cl{k.capitalize()}Mean", f"{np.mean(tot[k][3]):.1f}" if tot[k][3] else "--"); put(f"cl{k.capitalize()}Loss", tot[k][1])
put("clN", len(order))
grades = {}
for r in R.values():
    if r["kind"] == "baseline":
        for g in r.get("grades") or []:
            grades[g["grade"]] = grades.get(g["grade"], 0) + 1
put("clBaseGrades", ", ".join(f"{k} {v}" for k, v in sorted(grades.items(), key=lambda kv: -kv[1])) or "none")

with open("/home/claude/paper/numbers.tex", "w") as f:
    for k, v in N.items():
        f.write(f"\\newcommand{{\\{k}}}{{{v}}}\n")
print(json.dumps(N, indent=0, default=str))
