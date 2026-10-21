"""The traffic of the joint-heading example: s10 at T+360, predicted trajectories, the pair and the third aircraft."""
from common import *
import numpy as np
from atcsim.solve import traj, cpa
s = situation("s10_busy_mixed", 360); w = s.world
H = 600.0; sh = float(w.sep_h)
T = traj.build_all(w, H)
A, B = "AFR11", "RAM602"; THIRD = ["RYR3", "TAP7", "SWR40"]
fig, ax = plt.subplots(figsize=(6.2, 4.4))
poly = np.array(w.airspace.polygon + [w.airspace.polygon[0]])
ax.plot(poly[:, 0], poly[:, 1], color=MUTED, lw=0.8)
for cs, tr in T.items():
    if cs in [A, B] + THIRD: continue
    ax.plot(tr.x, tr.y, color="#d6d5cf", lw=0.8); ax.plot(tr.x[0], tr.y[0], "o", ms=2.5, color="#b9b8b2")
cols = {A: C1, B: C2, "RYR3": C3, "TAP7": C7, "SWR40": C4}
for cs in [A, B] + THIRD:
    tr = T[cs]; a = w.aircraft[cs]
    vs = "" if abs(a.vs_fpm) < 300 else ("↑" if a.vs_fpm > 0 else "↓")
    lw = 2.0 if cs in (A, B) else 1.3
    ax.plot(tr.x, tr.y, color=cols[cs], lw=lw, ls="-" if cs in (A, B) else (0, (4, 2)),
            label=f"{cs}  FL{int(round(a.alt_ft/100))}{vs}" + ("" if cs in (A, B) else "  (third)"))
    ax.plot(tr.x[0], tr.y[0], "o", ms=5 if cs in (A, B) else 4, color=cols[cs])
    tk = np.arange(60, 601, 60); xk, yk, _ = tr.at(tk); ax.plot(xk, yk, ".", ms=3.5, color=cols[cs])
p = cpa.pair(T[A], T[B], sh)
xa, ya, _ = T[A].at(p.t_min); xb, yb, _ = T[B].at(p.t_min)
for x, y, cs in ((xa, ya, A), (xb, yb, B)):
    ax.plot(x, y, "s", ms=6, mfc="white", mec=cols[cs], mew=1.5, zorder=6)
ax.text(0.02, 0.03, f"CPA of {A}×{B} (squares): {p.d_min:.1f} NM at {p.t_min:.0f} s\n$S_h$ = {sh:g} NM; dots every 60 s over 10 min",
        transform=ax.transAxes, fontsize=7.5, va="bottom", bbox=dict(fc="white", ec=GRID))
pts = np.concatenate([np.c_[T[c].x, T[c].y] for c in [A, B] + THIRD])
ax.set_xlim(pts[:, 0].min() - 25, pts[:, 0].max() + 25); ax.set_ylim(pts[:, 1].min() - 25, pts[:, 1].max() + 15)
ax.set_aspect("equal"); ax.set_xlabel("x (NM, east)"); ax.set_ylabel("y (NM, north)")
ax.legend(fontsize=7.5, loc="upper left", bbox_to_anchor=(1.01, 1.0))
fig.tight_layout(); fig.savefig(f"{FIG}/f10a_s10_traffic.pdf"); plt.close(fig)
print({c: (round(w.aircraft[c].alt_ft), round(w.aircraft[c].vs_fpm), round(w.aircraft[c].trk)) for c in [A, B] + THIRD}, p.d_min, p.t_min)
