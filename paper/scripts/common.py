import os, sys, warnings, math
warnings.filterwarnings("ignore")
os.environ.setdefault("ATC_DATA_DIR", "/home/claude/paper/hjdata")
sys.path.insert(0, "/home/claude/atc-repo"); os.chdir("/home/claude/atc-repo")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
C1, C2, C3, C4, C7 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#6250d6"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
plt.rcParams.update({"font.family": "serif", "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
    "axes.spines.right": False, "legend.frameon": False, "savefig.dpi": 220, "savefig.bbox": "tight", "lines.linewidth": 1.6,
    "mathtext.fontset": "cm"})
FIG = "/home/claude/paper/figs"

def situation(scn="p02_wn_merges_and_climbs", t=300):
    from atcsim.session import Session
    s = Session(scn, mode="stepped", autostart=False); s.stop(); s.solo = True
    while s.world.t < t:
        s._tick()
    for a in s.world.aircraft.values():
        if a.active and a.on_frequency:
            a.assumed = True
    return s
