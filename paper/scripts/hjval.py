"""Validate the HJ lookup (as the solver calls it: hj.relative_state + hj.value) against direct simulation in the
world frame (x east, y north, compass tracks)."""
from common import *
import json, numpy as np, sys
from types import SimpleNamespace as NS
from atcsim.solve import hj
sh = 5.0
t = hj.table(1.0, sh, build_if_missing=False)
assert t is not None
v = hj.V1_NM_S; om = math.degrees(hj.OMEGA); T = hj.T_S
def best_escape(px, py, trk2, v2):
    """mover at 0,0 track 000; other at px,py, track trk2: max over turn-then-straight strategies of min distance."""
    tt = np.arange(0, T + 1e-6, 2.0)
    ox = px + v2 * np.sin(np.radians(trk2)) * tt; oy = py + v2 * np.cos(np.radians(trk2)) * tt
    best = -1
    for sgn in (-1, 1):
        for tau in np.arange(0, T + 1, 10):
            hdg = sgn * om * np.minimum(tt, tau)
            dx = np.concatenate([[0], np.cumsum(v * np.sin(np.radians(hdg[:-1])) * 2.0)])
            dy = np.concatenate([[0], np.cumsum(v * np.cos(np.radians(hdg[:-1])) * 2.0)])
            d = np.hypot(ox - dx, oy - dy).min()
            best = max(best, d)
    return best
rng = np.random.default_rng(1)
out = []
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
for k in range(N):
    px, py = rng.uniform(-25, 25, 2); trk2 = rng.uniform(0, 360)
    if math.hypot(px, py) < sh: continue
    a = NS(x=0.0, y=0.0, trk=0.0); b = NS(x=px, y=py, trk=trk2)
    x, y, th = hj.relative_state(a, b)
    V = hj.value(t, x, y, th); Vm = hj.value(t, x, -y, th)
    e = best_escape(px, py, trk2, v)
    straight = best_escape.__wrapped__ if False else None
    out.append((px, py, trk2, V, Vm, e - sh))
o = np.array(out)
def agree(col):
    m = np.abs(o[:, 5]) > 0.5
    return float(np.mean(np.sign(o[m, col]) == np.sign(o[m, 5])))
print("n", len(o), "sign agreement as called:", agree(3), " with y mirrored:", agree(4))
print("mean (V - sim):", np.mean(o[:, 3] - o[:, 5]), np.mean(o[:, 4] - o[:, 5]))
mu = hj.MARGIN
print("false 'a turn is enough' (V > mu, sim < 0):", int(np.sum((o[:, 3] > mu) & (o[:, 5] < 0))), "of", int(np.sum(o[:, 5] < 0)),
      "| conservative (V <= mu, sim > 0):", int(np.sum((o[:, 3] <= mu) & (o[:, 5] > 0))))
json.dump(o.tolist(), open("/home/claude/paper/data/hjval.json", "w"))
fin = np.isfinite(o[:, 3])
for lo, hi in ((-5, 0), (0, 3), (3, 8), (8, 40)):
    m = fin & (o[:, 5] >= lo) & (o[:, 5] < hi)
    print(lo, hi, m.sum(), "V-E", np.mean(o[m, 3] - o[m, 5]).round(2), np.median(o[m, 3] - o[m, 5]).round(2), "| mirrored", np.mean(o[m, 4] - o[m, 5]).round(2), np.median(o[m, 4] - o[m, 5]).round(2))
