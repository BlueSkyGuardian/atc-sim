from common import *
import json, time, numpy as np
from atcsim.solve import traj, cpa, vo, resolve
s = situation(); w = s.world
H = 600.0
T = traj.build_all(w, H)
T9 = traj.build_all(w, 900.0)
M = cpa.matrix(T, float(w.sep_h), only=sorted(c for c, a in w.aircraft.items() if a.active and a.assumed))
info = {"t": w.t, "n_active": len(T), "sep_h": w.sep_h, "conflicts": [(p.a, p.b, round(p.d_min, 2), p.t_first, p.t_min, p.sv) for p in M["conflicts"]]}
print(info)
A, B = M["conflicts"][0].a, M["conflicts"][0].b
pc = cpa.pair(T9[A], T9[B], float(w.sep_h))
info["cpa900"] = (pc.d_min, pc.t_min, pc.t_first)
# ---- F1: trajectories, CPA
fig, ax = plt.subplots(figsize=(6.2, 4.6))
poly = np.array(w.airspace.polygon + [w.airspace.polygon[0]])
ax.plot(poly[:, 0], poly[:, 1], color=MUTED, lw=0.8, zorder=1)
for cs, tr in T9.items():
    if cs in (A, B): continue
    ax.plot(tr.x, tr.y, color="#c9c8c2", lw=0.8, zorder=2)
    ax.plot(tr.x[0], tr.y[0], "o", ms=2.5, color="#a9a8a2", zorder=2)
for cs, col in ((A, C1), (B, C2)):
    tr = T9[cs]
    ax.plot(tr.x, tr.y, color=col, lw=1.8, zorder=4, label=f"{cs}  FL{int(tr.z[0]/100)}→FL{int(tr.z[-1]/100)}")
    ax.plot(tr.x[0], tr.y[0], "o", ms=5, color=col, zorder=5)
    tk = np.arange(0, 901, 60); xk, yk, _ = tr.at(tk)
    ax.plot(xk, yk, ".", ms=4, color=col, zorder=4)
    xf, yf, _ = tr.at(pc.t_first); ax.plot(xf, yf, "^", ms=5, color=col, zorder=6)
    xm, ym, _ = tr.at(pc.t_min)
    ax.plot(xm, ym, "s", ms=6, mfc="white", mec=col, mew=1.5, zorder=6)
xa, ya, _ = T9[A].at(pc.t_min); xb, yb, _ = T9[B].at(pc.t_min)
ax.plot([xa, xb], [ya, yb], color=INK, lw=0.9, ls="--", zorder=6)
ax.text(0.97, 0.04, f"CPA (squares): $t^*$ = {pc.t_min:.0f} s, $d^*$ = {pc.d_min:.1f} NM\nfirst infringement (triangles): {pc.t_first:.0f} s\n$S_h$ = {w.sep_h:g} NM, same level; dots every 60 s", transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=INK, bbox=dict(fc="white", ec=GRID))
xs = np.concatenate([T9[A].x, T9[B].x]); ys = np.concatenate([T9[A].y, T9[B].y])
ax.set_xlim(xs.min() - 50, xs.max() + 30); ax.set_ylim(ys.min() - 30, ys.max() + 20)
ax.set_aspect("equal"); ax.set_xlabel("x (NM, east)"); ax.set_ylabel("y (NM, north)")
ax.legend(loc="upper left", fontsize=8)
fig.savefig(f"{FIG}/f1_trajectories.pdf"); plt.close(fig)
# ---- F3: velocity obstacles for the aircraft to move
res = resolve.solve(w, (A, B), H, rollout=2)
st = res["sets"][0]
mv = st["optimal"][0].mover if st["optimal"] else A
gs = w.aircraft[mv].gs
speeds = [gs - 40, gs - 20, gs, gs + 20]
O = vo.obstacles(w, mv, 600.0, speeds)
fig = plt.figure(figsize=(6.2, 3.3))
ax = fig.add_subplot(121, projection="polar"); ax.set_theta_zero_location("N"); ax.set_theta_direction(-1)
hd = np.radians(O["headings"])
cols = [C2, C3, C7, C4, C1]
for k, (oc, m) in enumerate(O["forbidden"].items()):
    sel = m[2]
    r = np.where(sel, 1.0 + 0.12 * k, np.nan)
    ax.plot(hd, r, color=cols[k % len(cols)], lw=3, label=oc)
ax.plot([math.radians(w.aircraft[mv].trk)] * 2, [0, 1.6], color=INK, lw=1.2)
ax.text(math.radians(w.aircraft[mv].trk) + 0.25, 1.62, "track", ha="center", fontsize=7, color=INK)
ax.set_rticks([]); ax.set_ylim(0, 1.9); ax.grid(color=GRID)
ax.set_title(f"Forbidden headings of {mv}\n(present ground speed {gs:.0f} kt)", fontsize=8.5)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.32), fontsize=7, ncol=2)
ax2 = fig.add_subplot(122)
U = O["union"].astype(float)
ax2.imshow(U, aspect="auto", origin="lower", extent=[0, 360, 0, len(speeds)], cmap=matplotlib.colors.ListedColormap(["#f3f2ee", "#d4553a"]), interpolation="nearest")
ax2.set_yticks(np.arange(len(speeds)) + 0.5, [f"{v:.0f}" for v in speeds]); ax2.set_ylabel("ground speed (kt)")
ax2.set_xlabel("heading (°)"); ax2.set_xticks(range(0, 361, 60)); ax2.grid(False)
ax2.axvline(w.aircraft[mv].trk, color=INK, lw=1)
ax2.set_title("Union of the obstacles (red) per speed", fontsize=8.5)
fig.tight_layout(); fig.savefig(f"{FIG}/f3_vo.pdf"); plt.close(fig)
# ---- F4: solution set in control space (heading offset x vector duration), and speeds
a = w.aircraft[mv]
sh = float(w.sep_h)
offs = np.arange(-90, 91, 5); durs = np.arange(60, 481, 30)
Phi = np.zeros((len(durs), len(offs)))
t0 = time.time()
for i, fs in enumerate(durs):
    for j, d in enumerate(offs):
        h = int(round(a.trk + d)) % 360 or 360
        Tm = traj.build(w, a, H, heading=h, for_s=float(fs))
        ok, dmin = resolve._check(Tm, T, {mv}, sh)
        Phi[i, j] = min(dmin, 15)
t_grid = time.time() - t0
fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.9), gridspec_kw={"width_ratios": [1.6, 1]})
ax = axs[0]
im = ax.contourf(offs, durs, Phi, levels=np.arange(0, 15.1, 1), cmap="Blues")
cs_ = ax.contour(offs, durs, Phi, levels=[sh + resolve.MARGIN_NM], colors=[C2], linewidths=1.6)
ax.plot([], [], color=C2, lw=1.6, label=r"$\Phi = S_h + m$"); ax.plot([], [], "o", ms=4, mfc="white", mec=INK, label="candidates kept"); ax.legend(loc="upper right", fontsize=7)
cb = fig.colorbar(im, ax=ax, pad=0.02); cb.set_label(r"$\Phi(u)$: closest to anyone (NM)", fontsize=8)
for s_ in st["solutions"]:
    if s_.kind == "heading" and s_.mover == mv:
        dpsi = (s_.params["heading"] - a.trk + 180) % 360 - 180
        ax.plot(dpsi, s_.params["for_s"], "o", ms=4, mfc="white", mec=INK, mew=0.8)
ax.set_xlabel(r"heading change $\Delta\psi$ (°)"); ax.set_ylabel(r"vector duration $\tau$ (s)")
ax.set_title(f"{mv}: delaying vectors, then resume", fontsize=8.5); ax.grid(False)
ax = axs[1]
st_ = {x["callsign"]: x for x in w.state()["aircraft"]}[mv]
m0 = st_.get("mach") or 0.78
ms = np.round(np.arange(m0 - 0.07, m0 + 0.045, 0.01), 2)
dd = []
for m in ms:
    Tm = traj.build(w, a, H, speed=f"M{int(round(m*100))}")
    ok, dmin = resolve._check(Tm, T, {mv}, sh)
    dd.append(min(dmin, 25))
ax.plot(ms, dd, color=C1, marker="o", ms=3)
ax.axhline(sh + resolve.MARGIN_NM, color=C2, lw=1.2, ls="--"); ax.text(ms[0], sh + resolve.MARGIN_NM - 0.7, r"$S_h + m$", fontsize=7, color=INK2)
ax.axvline(m0, color=MUTED, lw=0.8); ax.set_xlabel("Mach"); ax.set_ylabel(r"$\Phi$ (NM)")
ax.set_title(f"{mv}: speed alone", fontsize=8.5)
fig.tight_layout(); fig.savefig(f"{FIG}/f4_solutionset.pdf"); plt.close(fig)
info.update(mover=mv, other=B if mv == A else A, gs=gs, t_grid=t_grid, n_grid=Phi.size,
            sols=[(x.kind, x.text, round(x.cost, 2), None if math.isinf(x.d_min) else round(x.d_min, 1), x.confirmed, x.confirmed_min_nm) for x in st["solutions"]],
            optimal=[x.text for x in st["optimal"]], move=st["move"], priority=st["priority"], mach0=m0,
            speed_curve=list(zip(ms.tolist(), dd)), vo={k: int(v[2].sum()) for k, v in O["forbidden"].items()},
            free_arcs=vo.free_arcs(O["union"][2], O["headings"]), trk=a.trk)
json.dump(info, open("/home/claude/paper/data/fig_a.json", "w"), indent=1, default=str)
print(json.dumps(info, default=str)[:2500])
