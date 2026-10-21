"""Joint heading space of the conflicting pair: both aircraft given a delaying vector (then resume); the closest
any aircraft of the two comes to anyone, as a surface over (dpsi_A, dpsi_B)."""
from common import *
import json, time, numpy as np
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
from atcsim.solve import traj, cpa, resolve
import sys
SCN, TT, A, B = (sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else ("p02_wn_merges_and_climbs", 300, "EWG3KP", "IBE3247")
s = situation(SCN, TT); w = s.world
H = 600.0; sh = float(w.sep_h); thr = sh + resolve.MARGIN_NM
T = traj.build_all(w, H)
a, b = w.aircraft[A], w.aircraft[B]
TAU = 240.0
D = np.arange(-90, 91, 5)
def tr(ac, d):
    if d == 0:
        return T[ac.callsign]
    return traj.build(w, ac, H, heading=int(round(ac.trk + d)) % 360 or 360, for_s=TAU)
TA = {d: tr(a, d) for d in D}; TB = {d: tr(b, d) for d in D}
others = [c for c in T if c not in (A, B)]
# each one's distance to the third aircraft does not depend on the other's vector: compute once
oa = {d: min(cpa.pair(TA[d], T[o], sh).d_min for o in others) for d in D}
ob = {d: min(cpa.pair(TB[d], T[o], sh).d_min for o in others) for d in D}
t0 = time.time()
PAIRD = np.zeros((len(D), len(D))); PHI = np.zeros_like(PAIRD)
for i, da in enumerate(D):
    for j, db in enumerate(D):
        p = cpa.pair(TA[da], TB[db], sh).d_min
        PAIRD[j, i] = p
        PHI[j, i] = min(p, oa[da], ob[db])
dt = time.time() - t0
CAP = 16.0
Z = np.minimum(PHI, CAP); Zp = np.minimum(PAIRD, CAP)
X, Y = np.meshgrid(D, D)
fig = plt.figure(figsize=(7.2, 3.5))
ax = fig.add_subplot(1, 2, 1, projection="3d")
ax.plot_surface(X, Y, Z, cmap="Blues", vmin=-4, vmax=CAP, linewidth=0, antialiased=True, alpha=0.95, rcount=37, ccount=37)
ax.plot_surface(X, Y, np.full_like(Z, thr), color=C2, alpha=0.18, linewidth=0)
ax.contour(X, Y, Z, levels=[thr], zdir="z", offset=0, colors=[C2], linewidths=1.0)
ax.plot(D, np.zeros_like(D), Z[D.tolist().index(0), :] if False else Z[list(D).index(0), :], color=INK, lw=1.4)
ax.plot(np.zeros_like(D), D, Z[:, list(D).index(0)], color=INK, lw=1.4, ls="--")
ax.set_xlabel(f"$\\Delta\\psi$ {A} (°)", fontsize=7.5, labelpad=-2); ax.set_ylabel(f"$\\Delta\\psi$ {B} (°)", fontsize=7.5, labelpad=-2)
ax.tick_params(labelsize=6.5, pad=-2); ax.view_init(elev=34, azim=-58)
ax.set_title(r"joint separation surface, $\Phi$ (NM)", fontsize=8.5)
ax2 = fig.add_subplot(1, 2, 2)
lv = np.arange(0, CAP + 0.1, 1)
im = ax2.contourf(X, Y, Z, levels=lv, cmap="Blues")
ax2.contour(X, Y, Z, levels=[thr], colors=[C2], linewidths=1.5)
ax2.contour(X, Y, Zp, levels=[thr], colors=[INK2], linewidths=0.8, linestyles=":")
ax2.axhline(0, color=INK, lw=1.2); ax2.axvline(0, color=INK, lw=1.2, ls="--")
ax2.plot(0, 0, "x", color=INK, ms=6)
ax2.set_xlabel(f"$\\Delta\\psi$ {A} (°)"); ax2.set_ylabel(f"$\\Delta\\psi$ {B} (°)"); ax2.set_aspect("equal"); ax2.grid(False)
ax2.set_title("top view", fontsize=8.5)
cb = fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.03); cb.set_label(r"$\Phi$: closest to anyone (NM)", fontsize=7.5)
ax2.plot([], [], color=C2, lw=1.5, label=r"$\Phi = S_h+m$ (everyone)")
ax2.plot([], [], color=INK2, lw=0.8, ls=":", label="the pair alone")
ax2.legend(fontsize=6.5, loc="lower left", bbox_to_anchor=(-0.05, -0.3), ncol=2)
fig.subplots_adjust(wspace=0.38)
fig.savefig(f"{FIG}/f10_joint_heading.pdf"); plt.close(fig)
ok = PHI >= thr
on_a = ok[list(D).index(0), :]; on_b = ok[:, list(D).index(0)]
pair_only = (PAIRD >= thr) & ~ok
res = {"tau": TAU, "n": int(ok.size), "ok": int(ok.sum()), "ok_pct": float(100 * ok.mean()),
       "pair_ok_pct": float(100 * (PAIRD >= thr).mean()), "third_party": int(pair_only.sum()),
       "single_A": [int(d) for d in D[on_a]], "single_B": [int(d) for d in D[on_b]], "ms_per_point": 1e3 * dt / ok.size,
       "phi0": float(PHI[list(D).index(0), list(D).index(0)])}
res.update(scn=SCN, t=TT, A=A, B=B, sh=sh)
json.dump(res, open("/home/claude/paper/data/fig_e.json", "w"), indent=1)
print(res)
