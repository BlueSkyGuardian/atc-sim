"""The ERA5 climatology wind field (climate.py) on the simulator plane, with a small made-up data file.
   python -m tests.test_climate"""
import os
import tempfile

import numpy as np

from atcsim import climate
from atcsim.scenario import build_world, load_scenario


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


lat, lon = np.arange(25.0, 38.01, 0.5), np.arange(-20.0, 0.01, 0.5)
fl = np.array([183, 236, 266, 301, 340, 362, 387, 414, 446], float)
sh = (12, len(fl), len(lat), len(lon))
L = np.broadcast_to(lat[None, None, :, None], sh)
u = 20 + 40 * np.exp(-((L - 31.0) / 3.0) ** 2)          # a westerly jet along 31N
d = {"lat": lat, "lon": lon, "fl": fl, "p_hpa": fl * 0, "u": u, "v": np.zeros(sh), "t": np.full(sh, -50.0),
     "u_sd": np.full(sh, 8.0), "v_sd": np.full(sh, 6.0), "eof_u": np.full(sh, 10.0), "eof_v": np.zeros(sh),
     "eof_var": np.full(12, 0.4), "years": np.arange(1994, 2024)}
tmp = tempfile.mkdtemp()
np.savez(os.path.join(tmp, "test_clim.npz"), **{k: np.asarray(v, np.float32) for k, v in d.items()})
climate.DATA = tmp
climate.load.cache_clear()

s = load_scenario("s19_terto_arrivals_crossing")
s["weather"] = {"seed": 2, "wind": {"climatology": {"month": 1, "file": "test_clim"}, "noise_kt": 0}}
w = build_world(s)
wx = w.env.weather
u_tovra = wx.at(*w.waypoints["TOVRA"], 350)[0]       # 31.75N
u_marja = wx.at(*w.waypoints["MARJA"], 350)[0]       # 34.7N
ok(u_tovra > 55 and u_marja < 40, f"varies with position: {u_tovra:.0f} kt westerly at TOVRA, {u_marja:.0f} kt at MARJA")
ok(abs(wx.at(*w.waypoints["TOVRA"], 100)[0]) < u_tovra, "weaker below the lowest level")
s["weather"]["wind"]["climatology"]["mode"] = 1.0
u_strong = build_world(s).env.weather.at(*w.waypoints["TOVRA"], 350)[0]
ok(abs(u_strong - u_tovra - 10) < 1.0, f"mode +1 adds the leading variability mode ({u_strong:.0f} kt)")
ok(wx.summary(0)["climatology"]["month"] == 1 and abs(wx.isa_deviation_c - (-50 - (15 - 1.98 * 34))) < 1.5,
   f"temperature of the month for performance (ISA{wx.isa_deviation_c:+.1f})")
for _ in range(120):
    w.step(1)
ok(True, "the world runs on it")
print("climate checks passed")
