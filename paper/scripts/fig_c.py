from common import *
import json, numpy as np
from atcsim.solve import hj
t = hj.table(1.0, 5.0, build_if_missing=False)
V, xs, th = t["V"], t["xs"], t["th"]
sh = 5.0; v = hj.V1_NM_S; T = hj.T_S
fig, axs = plt.subplots(1, 4, figsize=(7.0, 2.35), sharey=True)
X, Y = np.meshgrid(xs, xs, indexing="ij")
for ax, deg in zip(axs, (180, 135, 90, 45)):
    k = int(round(math.radians(deg) / (th[1] - th[0]))) % len(th)
    # straight flight: no turn, the unavoidable set is min over [0,T] of distance < S
    the = th[k]
    rvx, rvy = -v + v * math.cos(the), v * math.sin(the)
    vv = rvx ** 2 + rvy ** 2
    ts = np.clip(-(X * rvx + Y * rvy) / max(vv, 1e-12), 0, T)
    D0 = np.hypot(X + rvx * ts, Y + rvy * ts) - sh
    ax.contourf(Y, X, V[:, :, k], levels=[-10, 0], colors=["#cfe0f5"])
    ax.contour(Y, X, V[:, :, k], levels=[0], colors=[C1], linewidths=1.5)
    ax.contour(Y, X, D0, levels=[0], colors=[C2], linewidths=1.1, linestyles="--")
    ax.add_patch(plt.Circle((0, 0), sh, fill=False, ec=MUTED, lw=0.8))
    ax.annotate("", xy=(0, 6), xytext=(0, 0), arrowprops=dict(arrowstyle="-|>", color=INK, lw=1))
    ax.set_title(rf"$\theta = {deg}^\circ$", fontsize=9)
    ax.set_xlim(-18, 18); ax.set_ylim(-10, 26); ax.set_aspect("equal"); ax.set_xlabel("y, right (NM)")
axs[0].set_ylabel("x, ahead (NM)")
axs[0].plot([], [], color=C1, lw=1.5, label="unavoidable with turns ($V=0$)")
axs[0].plot([], [], color=C2, lw=1.1, ls="--", label="conflict without turning")
fig.legend(loc="lower center", ncol=2, fontsize=7.5, bbox_to_anchor=(0.5, -0.07))
fig.tight_layout(); fig.savefig(f"{FIG}/f5_hj_slices.pdf"); plt.close(fig)
# validation
o = np.array(json.load(open("/home/claude/paper/data/hjval.json")))
Vv, E = o[:, 3], o[:, 5]
fig, axs = plt.subplots(1, 2, figsize=(6.4, 2.8))
ax = axs[0]
ax.scatter(E, Vv, s=4, color=C1, alpha=0.45, lw=0)
lim = [-6, 30]
ax.plot(lim, lim, color=MUTED, lw=0.8)
ax.axhline(hj.MARGIN, color=C2, lw=1, ls="--"); ax.axvline(0, color=INK2, lw=0.6)
ax.text(29, hj.MARGIN + 0.6, r"$\mu$", color=C2, ha="right", fontsize=8)
ax.set_xlim(lim); ax.set_ylim(lim); ax.set_xlabel("simulated best escape $-\\,S_h$ (NM)"); ax.set_ylabel("table value $V$ (NM)")
ax.set_title("Table vs simulated turns", fontsize=8.5)
ax = axs[1]
m = E < 8
ax.scatter(E[m], (Vv - E)[m], s=5, color=C1, alpha=0.5, lw=0)
ax.axhline(0, color=MUTED, lw=0.8); ax.axvline(0, color=INK2, lw=0.6)
ax.set_xlabel("simulated best escape $-\\,S_h$ (NM)"); ax.set_ylabel("$V$ $-$ simulation (NM)")
ax.set_title("Difference near the boundary", fontsize=8.5)
fig.tight_layout(); fig.savefig(f"{FIG}/f6_hj_validation.pdf"); plt.close(fig)
