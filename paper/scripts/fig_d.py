"""The solution space of the worked example: every family of clearances for both aircraft, and cost against Phi."""
from common import *
import json, numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from atcsim.solve import traj, resolve
from atcsim.ailab.observe import priority
s = situation(); w = s.world
H = 600.0; sh = float(w.sep_h); thr = sh + resolve.MARGIN_NM
T = traj.build_all(w, H)
st = {a["callsign"]: a for a in w.state()["aircraft"]}
PAIR = ("EWG3KP", "IBE3247")
CAP = 15.0
KC = {"speed": C1, "direct": C2, "heading": C3, "level": C4}
KM = {"speed": "o", "direct": "s", "heading": "^", "level": "D"}
cmap = plt.get_cmap("Blues"); norm = matplotlib.colors.Normalize(0, CAP)
mv, why = priority(st[PAIR[0]], st[PAIR[1]], w)
cands, out = [], {}
for cs in PAIR:
    a = w.aircraft[cs]
    rows = []
    for kind, prm, acts, cost, ask in resolve._candidates(w, a, st[cs], {}):
        ok, d, _ = resolve.check_clearance(w, a, H, prm, T, sh)
        if not ok:   # _check stops early on failure: get the true minimum for the picture
            Tm = traj.build(w, a, H, **prm)
            d = min(__import__("atcsim.solve.cpa", fromlist=["pair"]).pair(Tm, T[o], sh).d_min for o in T if o != cs)
        prio = bool(mv) and cs != mv
        rows.append({"cs": cs, "kind": kind, "prm": prm, "cost": cost + (5 if prio else 0), "ask": ask, "phi": float(min(d, 40)), "ok": ok})
    cands += rows
# --- figure 8: control space per family
fig = plt.figure(figsize=(7.2, 4.6))
gs = fig.add_gridspec(2, 5, width_ratios=[1.0, 0.9, 1.6, 1.0, 0.07], wspace=0.75, hspace=0.6)
for r, cs in enumerate(PAIR):
    a = w.aircraft[cs]; c = [x for x in cands if x["cs"] == cs]
    # speed
    ax = fig.add_subplot(gs[r, 0])
    m0 = st[cs].get("mach") or 0.78
    ms = np.round(np.arange(max(0.68, m0 - 0.08), m0 + 0.05, 0.005), 3)
    ph = []
    for m in ms:
        Tm = traj.build(w, a, H, speed=f"M{int(round(m*100))}" if abs(m*100-round(m*100))<1e-6 else f"{m:.3f}")
        ph.append(min(resolve._check(Tm, T, {cs}, 1e9)[1], CAP))
    ax.plot(ms, ph, color=MUTED, lw=1)
    for x in c:
        if x["kind"] == "speed":
            mm = float(x["prm"]["speed"].lstrip("M")) / 100 if x["prm"]["speed"].startswith("M") else None
            if mm: ax.plot(mm, min(x["phi"], CAP), KM["speed"], ms=5, mfc=KC["speed"] if x["ok"] else "white", mec=KC["speed"])
    ax.axhline(thr, color=INK, lw=0.8, ls="--"); ax.axvline(m0, color=MUTED, lw=0.6, ls=":")
    ax.set_ylim(0, CAP); ax.set_xlabel("Mach", fontsize=8); ax.set_ylabel(f"{cs}\n$\\Phi$ (NM)", fontsize=8)
    if r == 0: ax.set_title("speed", fontsize=8.5)
    # direct
    ax = fig.add_subplot(gs[r, 1])
    dd = [x for x in c if x["kind"] == "direct"]
    xs_ = np.arange(len(dd))
    ax.bar(xs_, [min(x["phi"], CAP) for x in dd], color=[KC["direct"] if x["ok"] else "white" for x in dd], edgecolor=KC["direct"], width=0.6)
    ax.set_xticks(xs_, [x["prm"]["direct"] for x in dd], rotation=45, fontsize=7)
    ax.axhline(thr, color=INK, lw=0.8, ls="--"); ax.set_ylim(0, CAP); ax.grid(axis="x", visible=False)
    if r == 0: ax.set_title("direct", fontsize=8.5)
    # delaying vectors
    ax = fig.add_subplot(gs[r, 2])
    offs = np.arange(-90, 91, 5); durs = np.arange(60, 481, 30)
    Phi = np.zeros((len(durs), len(offs)))
    for i, fs in enumerate(durs):
        for j, d in enumerate(offs):
            h = int(round(a.trk + d)) % 360 or 360
            Phi[i, j] = min(resolve._check(traj.build(w, a, H, heading=h, for_s=float(fs)), T, {cs}, 1e9)[1], CAP)
    im = ax.contourf(offs, durs, Phi, levels=np.arange(0, CAP + 0.1, 1), cmap=cmap, norm=norm)
    ax.contour(offs, durs, Phi, levels=[thr], colors=[INK], linewidths=1.0, linestyles="--")
    for x in c:
        if x["kind"] == "heading":
            dpsi = (x["prm"]["heading"] - a.trk + 180) % 360 - 180
            ax.plot(dpsi, x["prm"]["for_s"], KM["heading"], ms=4.5, mfc=KC["heading"] if x["ok"] else "white", mec=INK, mew=0.5)
    ax.set_xlabel(r"$\Delta\psi$ (°)", fontsize=8); ax.set_ylabel(r"$\tau$ (s)", fontsize=8); ax.grid(False)
    if r == 0: ax.set_title("delaying vector, then resume", fontsize=8.5)
    # level
    ax = fig.add_subplot(gs[r, 3])
    ll = sorted([x for x in c if x["kind"] == "level"], key=lambda x: x["prm"]["cfl"])
    ys = [x["prm"]["cfl"] for x in ll]
    ax.barh(range(len(ll)), [min(x["phi"], CAP) for x in ll], color=[KC["level"] if x["ok"] else "white" for x in ll],
            edgecolor=KC["level"], hatch=None, height=0.6)
    for k, x in enumerate(ll):
        if x["ask"]: ax.text(min(x["phi"], CAP) + 0.3, k, "ask", va="center", fontsize=6.5, color=INK2)
    ax.set_yticks(range(len(ll)), [str(y) for y in ys], fontsize=7); ax.set_ylabel("FL", fontsize=8, labelpad=1)
    ax.axvline(thr, color=INK, lw=0.8, ls="--"); ax.set_xlim(0, CAP); ax.set_xlabel(r"$\Phi$ (NM)", fontsize=8); ax.grid(axis="y", visible=False)
    if r == 0: ax.set_title(f"level (now FL{int(round(a.alt_ft/100))})", fontsize=8.5)
cax = fig.add_subplot(gs[:, 4]); cb = fig.colorbar(im, cax=cax); cb.set_label(r"$\Phi(u)$ (NM, capped at 15)", fontsize=8)
fig.savefig(f"{FIG}/f8_space.pdf"); plt.close(fig)
# --- figure 9: cost against Phi, every candidate
fig, ax = plt.subplots(figsize=(6.2, 3.1))
ax.axhspan(thr, 14, color="#eef5ea", zorder=0)
ok_ = [x for x in cands if x["ok"]]
best = min(ok_, key=lambda x: x["cost"]) if ok_ else None
optimal = [x for x in ok_ if best and x["kind"] == best["kind"] and x["cost"] <= best["cost"] + resolve.OPT_TOL]
for kind in ("speed", "direct", "heading", "level"):
    for cs, fill in ((PAIR[0], True), (PAIR[1], False)):
        xx = [x for x in cands if x["kind"] == kind and x["cs"] == cs]
        if not xx: continue
        ax.scatter([x["cost"] for x in xx], [x["phi"] for x in xx], marker=KM[kind], s=22,
                   facecolors=KC[kind] if fill else "white", edgecolors=KC[kind], lw=1.1, zorder=3,
                   label=f"{kind}, {cs}")
for x in optimal:
    ax.scatter([x["cost"]], [x["phi"]], s=140, facecolors="none", edgecolors=INK, lw=1.2, zorder=4)
ax.axhline(thr, color=INK, lw=0.8, ls="--")
ax.text(2.6, 12.6, r"solution set: $\Phi \geq S_h + m$", fontsize=7.5, color=INK2, ha="center")
ax.set_xlabel(r"cost $J(u)$"); ax.set_ylabel(r"$\Phi(u)$: closest to anyone (NM)"); ax.set_ylim(0, 14)
ax.legend(fontsize=6.5, ncol=2, loc="upper left", bbox_to_anchor=(1.01, 1.0), handletextpad=0.3)
fig.tight_layout(); fig.savefig(f"{FIG}/f9_cost_phi.pdf"); plt.close(fig)
n = {k: (sum(1 for x in cands if x["kind"] == k), sum(1 for x in cands if x["kind"] == k and x["ok"])) for k in KC}
json.dump({"counts": n, "total": len(cands), "feasible": len(ok_), "optimal": [(x["cs"], x["kind"], x["prm"]) for x in optimal],
           "priority": why, "mover": mv}, open("/home/claude/paper/data/fig_d.json", "w"), indent=1, default=str)
print(n, len(cands), len(ok_), [(x["cs"], x["prm"]) for x in optimal])
