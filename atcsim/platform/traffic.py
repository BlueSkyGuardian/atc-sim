"""Live traffic: the real aircraft over and around the Agadir CTA, from a public ADS-B network, on the radar (view only).

The server reads the feed (adsb.lol by default; airplanes.live and adsb.fi serve the same "readsb" format) for the
area of the CTA plus a margin, in a few circles of up to 250 NM, while somebody watches the page - one reading for
every viewer, whatever their number - and slowly in the background while the coverage map is being measured (the
first 24 hours after the server starts without one, or when an admin asks).

Each aircraft becomes an aircraft of a simulator world that is never flown: placed where the feed says, carried on
along its track between two reports, its cleared level the level selected in the cockpit (Mode S) when it is
broadcast - so the radar's tools all work on it: labels, the sweep, history, STCA, the conflict look-ahead (straight
ahead along the track, to the selected level), RBLs, track info. Inside the CTA it shows as ours, outside as the
others'. The origin and destination come from the network's route database where it knows the callsign; the route is
not known (flight plans are not public): the look-ahead flies the present track.

Settings (environment):
  ATC_LIVE=off                    no live traffic
  ATC_LIVE_URL                    the feed, with {lat} {lon} {nm}: https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}
                                  (https://api.airplanes.live/v2/point/{lat}/{lon}/{nm},
                                   https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm})
  ATC_LIVE_ROUTES_URL             origin / destination by callsign (adsb.lol's routeset; empty: none)
  ATC_LIVE_CREDIT                 the credit shown on the page
  ATC_LIVE_EVERY_S                the closest two requests may be (default 3 s); ATC_LIVE_GAP_S the spacing to begin
                                  with (6 s: it adapts to the network's limit); ATC_LIVE_IDLE_S a round while
                                  measuring (60 s)
  ATC_LIVE_HEADERS                headers sent with every request, "Name: value; Name2: value2" - an API key when the
                                  network asks for one (adsb.lol has announced a key for its feeders; a paid feed
                                  such as ADS-B Exchange on RapidAPI: "X-RapidAPI-Key: ...; X-RapidAPI-Host: ...")
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config
from .auth import require_user

log = logging.getLogger("atcsim.traffic")
router = APIRouter()

LIVE_SID = "11fe11fe"                 # the live traffic's session (hex: the session paths; 8 characters: not a debrief)
ENABLED = os.environ.get("ATC_LIVE", "on").lower() not in ("off", "0", "no", "false")
SOURCE = os.environ.get("ATC_LIVE_URL", "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}")
ROUTES_URL = os.environ.get("ATC_LIVE_ROUTES_URL", "https://api.adsb.lol/api/0/routeset")
CREDIT = os.environ.get("ATC_LIVE_CREDIT", "ADS-B data: adsb.lol (Open Database License)")
# the requests go one at a time, spaced: GAP_S to begin with, never closer than EVERY_S; a refusal (429 Too Many
# Requests) doubles the spacing (and waits what the network asks), a run of answers shortens it again - the reading
# settles just under the network's limit, which adsb.lol sets by its load
EVERY_S = max(1.0, float(os.environ.get("ATC_LIVE_EVERY_S", "3") or 3))
GAP_S = max(EVERY_S, float(os.environ.get("ATC_LIVE_GAP_S", "6") or 6))
MAX_GAP_S = 120.0
IDLE_S = max(10.0, float(os.environ.get("ATC_LIVE_IDLE_S", "60") or 60))
HEADERS = {k.strip(): v.strip() for k, _, v in (h.partition(":") for h in os.environ.get("ATC_LIVE_HEADERS", "").split(";"))
           if k.strip() and v.strip()}
MARGIN_NM = 25.0                      # the picture: the CTA and this much around it (two circles of 250 NM)
RADIUS_NM = 250.0                     # the feeds' largest circle
STALE_S = 60.0                        # an aircraft not heard for this long (or 3 rounds of reading) leaves the picture
WATCH_S = 30.0                        # a page that asked for the status this recently is watching
CELL_NM = 10.0                        # the coverage map's cells
MEASURE_H = 24.0                      # the first measurement of the coverage
LIVE_DIR = os.path.join(config.DATA_DIR, "live")

# a type the simulator does not know: the nearest one it does (its performance only shapes the look-ahead; the label
# shows the real type)
_FAMILY = [("A35", "A359"), ("A33", "A333"), ("A34", "A343"), ("A38", "A388"), ("A30", "A333"), ("A31", "A319"),
           ("A32", "A320"), ("A22", "A220"), ("BCS", "A220"), ("B78", "B789"), ("B77", "B77W"), ("B76", "B763"),
           ("B75", "B752"), ("B74", "B744"), ("B73", "B738"), ("B3", "B38M"), ("MD1", "B77W"), ("E29", "E195"),
           ("E19", "E190"), ("E17", "E170"), ("E75", "E75L"), ("E14", "E145"), ("E13", "E145"), ("CRJ", "CRJ9"),
           ("AT", "E145"), ("DH8", "E145"), ("SF34", "E145"), ("C25", "C550"), ("C5", "C550"), ("C6", "C550"),
           ("C68", "C550"), ("GL", "GLF6"), ("G2", "GLF6"), ("G6", "GLF6"), ("CL", "GLF6"), ("FA", "GLF6"),
           ("F2TH", "GLF6"), ("LJ", "C550"), ("PC", "C550"), ("C13", "C130"), ("C17", "C17"), ("K35", "K35R"),
           ("A40", "A400"), ("F16", "F16"), ("F18", "F18"), ("EUFI", "EUFI"), ("RFAL", "RFAL"), ("MIR", "MIR2")]
_BY_CATEGORY = {"A1": "C550", "A2": "E145", "A3": "A320", "A4": "B752", "A5": "B77W", "A6": "F16"}


def perf_type(t: str, category: str = "") -> str:
    from .. import performance
    t = (t or "").upper()
    if t in performance.SUPPORTED:
        return t
    for p, s in _FAMILY:
        if t.startswith(p) and s in performance.SUPPORTED:
            return s
    return _BY_CATEGORY.get((category or "").upper(), "A320")


# ------------------------------------------------------------------ reading the feed
class Feed:
    """The latest report of every aircraft heard, the coverage counts, the state of the reading."""

    def __init__(self):
        self.lock = threading.Lock()
        self.targets: Dict[str, dict] = {}            # hex -> the latest report (+ "t_pos": epoch of the position)
        self.routes: Dict[str, dict] = {}             # callsign -> {"orig", "dest", "t"}
        self.status = {"last_ok": None, "last_error": None, "errors": 0, "requests": 0, "polls": 0, "mode": "idle",
                       "limited": 0, "last_limit_t": None}
        self.gap = GAP_S                              # the spacing of the requests now (adapts)
        self.floor = EVERY_S                          # not faster than this: 1.3 x a spacing that was refused (30 min)
        self.hold_until = 0.0                         # refused: no request before this
        self.watched_at = 0.0
        self.measure_until = 0.0
        self.circles: List[tuple] = []                # (lat, lon, nm)
        self.grid = None                              # coverage: {"x0", "y0", "nx", "ny", "cell", "counts", "polls", "since"}
        self._thread = None
        self._last_save = time.time()

    # -------------------------------------------------------------- the area
    def setup(self, asp) -> None:
        """The circles to read (covering the CTA and its margin) and the coverage grid, from the airspace."""
        x0, y0, x1, y1 = asp.bbox
        x0, y0, x1, y1 = x0 - MARGIN_NM, y0 - MARGIN_NM, x1 + MARGIN_NM, y1 + MARGIN_NM
        # as few circles as cover the area (each request counts against the network's limit): the smallest grid of
        # rectangles whose half-diagonal fits in a circle
        nx, ny = min(((a, b) for a in range(1, 6) for b in range(1, 6)
                      if math.hypot((x1 - x0) / a, (y1 - y0) / b) / 2 <= RADIUS_NM - 2), key=lambda g: (g[0] * g[1], g))
        sx, sy = (x1 - x0) / nx, (y1 - y0) / ny
        circ = []
        for i in range(nx):
            for j in range(ny):
                cx, cy = x0 + sx * (i + 0.5), y0 + sy * (j + 0.5)
                ll = asp.to_latlon(cx, cy)
                if ll:
                    circ.append((round(ll[0], 3), round(ll[1], 3), int(min(RADIUS_NM, math.ceil(math.hypot(sx, sy) / 2) + 2))))
        self.circles = circ
        self.box = (x0, y0, x1, y1)
        gx, gy = int(math.ceil((x1 - x0) / CELL_NM)), int(math.ceil((y1 - y0) / CELL_NM))
        self.grid = self._load_grid(x0, y0, gx, gy) or {"x0": x0, "y0": y0, "nx": gx, "ny": gy, "cell": CELL_NM,
                                                        "counts": [0] * (gx * gy), "polls": 0, "since": time.time()}
        if not os.path.exists(os.path.join(LIVE_DIR, "coverage.json")) and self.measure_until < time.time():
            self.measure_until = time.time() + MEASURE_H * 3600       # nothing measured yet: the first day

    def _load_grid(self, x0, y0, nx, ny) -> Optional[dict]:
        try:
            g = json.load(open(os.path.join(LIVE_DIR, "coverage.json")))
            if g.get("nx") == nx and g.get("ny") == ny and abs(g["x0"] - x0) < 1e-6 and abs(g["y0"] - y0) < 1e-6:
                return g
        except (OSError, ValueError, KeyError):
            pass
        return None

    def save_grid(self) -> None:
        os.makedirs(LIVE_DIR, exist_ok=True)
        with self.lock:
            g = dict(self.grid)
        with open(os.path.join(LIVE_DIR, "coverage.json.tmp"), "w") as f:
            json.dump(g, f)
        os.replace(os.path.join(LIVE_DIR, "coverage.json.tmp"), os.path.join(LIVE_DIR, "coverage.json"))
        self._last_save = time.time()

    # -------------------------------------------------------------- reading
    def mode(self, now: Optional[float] = None) -> str:
        now = now or time.time()
        return "watched" if now - self.watched_at < WATCH_S else "measuring" if now < self.measure_until else "idle"

    def cycle_s(self, m: Optional[str] = None) -> float:
        """How often every circle is read again, at the present spacing."""
        m = m or self.mode()
        n = max(1, len(self.circles))
        return (self.gap if m == "watched" else max(self.gap, IDLE_S / n)) * n

    def merge(self, d: dict, to_xy) -> int:
        """One circle's answer into the picture and the coverage map; returns the number of aircraft heard."""
        heard = {}
        now_s = (d.get("now") or time.time() * 1000) / 1000.0
        for a in d.get("ac") or d.get("aircraft") or []:
            if a.get("lat") is None or a.get("lon") is None or not a.get("hex"):
                continue
            a = dict(a)
            a["t_pos"] = now_s - float(a.get("seen_pos") or a.get("seen") or 0)
            old = heard.get(a["hex"])
            if old is None or a["t_pos"] > old["t_pos"]:
                heard[a["hex"]] = a
        now = time.time()
        stale = max(STALE_S, 3 * self.cycle_s())
        with self.lock:
            for h, a in heard.items():
                if h not in self.targets or a["t_pos"] >= self.targets[h]["t_pos"]:
                    self.targets[h] = a
            for h in [h for h, a in self.targets.items() if now - a["t_pos"] > stale]:
                self.targets.pop(h, None)
            g = self.grid
            if g is not None:                            # coverage: the positions received, cell by cell
                for a in heard.values():
                    if now - a["t_pos"] > 30:            # an old position repeated: not a reception now
                        continue
                    x, y = to_xy(a["lat"], a["lon"])
                    i, j = int((x - g["x0"]) // g["cell"]), int((y - g["y0"]) // g["cell"])
                    if 0 <= i < g["nx"] and 0 <= j < g["ny"]:
                        g["counts"][j * g["nx"] + i] += 1
            self.status.update(last_ok=now, heard=len(heard))
        return len(heard)

    def read_circle(self, client, i: int, to_xy) -> str:
        """Read circle i: "ok", or "limited" (the network refused: slower from now), or raises."""
        lat, lon, nm = self.circles[i]
        self.status["requests"] += 1
        r = client.get(SOURCE.format(lat=lat, lon=lon, nm=nm))
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0.0
            self.floor = min(MAX_GAP_S, max(EVERY_S, self.gap * 1.3))
            self.gap = min(MAX_GAP_S, self.gap * 2)
            self.hold_until = time.time() + min(300.0, max(wait, self.gap))
            self.status.update(limited=self.status["limited"] + 1, last_limit_t=time.time())
            return "limited"
        r.raise_for_status()
        self.merge(r.json(), to_xy)
        return "ok"

    def read_once(self, client, to_xy) -> int:
        """Every circle once (the tests): the number of aircraft on the picture after."""
        for i in range(len(self.circles)):
            self.read_circle(client, i, to_xy)
        self.round_done()
        with self.lock:
            return len(self.targets)

    def round_done(self) -> None:
        with self.lock:
            self.status["polls"] += 1
            if self.grid is not None:
                self.grid["polls"] += 1

    def read_routes(self, client) -> None:
        """Origin and destination of the callsigns not known yet (or known for more than 6 h), in one request."""
        if not ROUTES_URL:
            return
        now = time.time()
        with self.lock:
            want = [a for a in self.targets.values() if (a.get("flight") or "").strip()
                    and (now - self.routes.get(a["flight"].strip(), {}).get("t", 0)) > 6 * 3600][:100]
        if not want:
            return
        body = {"planes": [{"callsign": a["flight"].strip(), "lat": a["lat"], "lng": a["lon"]} for a in want]}
        r = client.post(ROUTES_URL, json=body)
        if r.status_code >= 400:
            for a in want:                                  # do not ask again for an hour
                self.routes[a["flight"].strip()] = {"t": now - 5 * 3600}
            return
        got = {}
        for x in r.json() if isinstance(r.json(), list) else []:
            cs = str(x.get("callsign") or "").strip()
            codes = str(x.get("airport_codes") or "")
            if cs and "-" in codes and codes != "unknown":
                ap = codes.split("-")
                got[cs] = {"orig": ap[0], "dest": ap[-1], "plausible": x.get("plausible"), "t": now}
        with self.lock:
            for a in want:
                cs = a["flight"].strip()
                self.routes[cs] = got.get(cs) or {"t": now}

    def loop(self, to_xy) -> None:
        import httpx
        time.sleep(5)
        last_routes, i, good = 0.0, 0, 0
        hdr = {"User-Agent": f"ATC-SIM-live/1.0 (+{config.PUBLIC_URL or 'training simulator'})", **HEADERS}
        with httpx.Client(timeout=12, headers=hdr) as c:
            while True:
                m = self.mode()
                self.status["mode"] = m
                if m == "idle" or time.time() < self.hold_until or not self.circles:
                    time.sleep(0.5)
                    continue
                t0 = time.time()
                try:
                    res = self.read_circle(c, i, to_xy)
                    if res == "ok":
                        i = (i + 1) % len(self.circles)
                        if i == 0:
                            self.round_done()                # every circle read once more: a round
                        good += 1
                        if good >= 2 * len(self.circles):    # answered for two rounds: a little faster, not into
                            if time.time() - (self.status["last_limit_t"] or 0) > 1800:     # a refused pace again
                                self.floor = EVERY_S
                            self.gap, good = max(EVERY_S, self.floor, self.gap * 0.85), 0
                    else:
                        good = 0
                    recently_limited = time.time() - (self.status["last_limit_t"] or 0) < 300
                    if res == "ok" and m == "watched" and not recently_limited and time.time() - last_routes > 300:
                        last_routes = time.time()            # origins and destinations: a request now and then
                        time.sleep(self.gap)
                        try:
                            self.read_routes(c)
                        except Exception as e:              # noqa: BLE001 - the routes are a bonus
                            log.info("routes: %s", e)
                except Exception as e:                      # noqa: BLE001 - the feed may be down: say so, try again later
                    good = 0
                    self.status.update(last_error=f"{type(e).__name__}: {e}"[:200], errors=self.status["errors"] + 1,
                                       last_error_t=time.time())
                    self.hold_until = time.time() + min(120.0, 2 * self.gap)
                if time.time() - self._last_save > 300:
                    try:
                        self.save_grid()
                    except OSError:
                        pass
                n = max(1, len(self.circles))
                spacing = self.gap if m == "watched" else max(self.gap, IDLE_S / n)
                while time.time() - t0 < spacing:
                    if m != "watched" and self.mode() == "watched":     # somebody opened the page: read now
                        break
                    time.sleep(0.25)

    def start(self, to_xy) -> None:
        if self._thread is None and ENABLED:
            self._thread = threading.Thread(target=self.loop, args=(to_xy,), daemon=True, name="live-traffic")
            self._thread.start()


FEED = Feed()


# ------------------------------------------------------------------ the session shown on the radar
def _blank_scenario() -> dict:
    from ..scenario import load_scenario
    scn = load_scenario("c01_cta_moderate_wx")
    now = datetime.now(timezone.utc)
    scn.update(name="live", title="Live traffic", description="The real traffic over the Agadir CTA (ADS-B), view only.",
               aircraft=[], requests=[], areas=[], events=[], duration_s=30 * 86400, start_utc=now.strftime("%H:%M"),
               auto_assume=True)
    w = dict(scn.get("weather") or {})
    w["convection"] = {"regime": "none", "count": 0}
    scn["weather"] = w
    return scn


def _live_world_class():
    from ..world import World

    class LiveWorld(World):
        """A world that is not flown: its aircraft are the real ones, placed by the feed."""

        def state(self, trails: bool = False, sweep_s=None) -> dict:
            st = super().state(trails, sweep_s)
            for lst in (st["aircraft"], st.get("transferred") or []):
                for d in lst:
                    a = self.aircraft.get(d["callsign"])
                    lv = getattr(a, "live", None) if a else None
                    if lv:
                        d["type"] = lv.get("type") or d["type"]
                        d["type_name"] = lv.get("type") or d["type_name"]
                        d["live"] = lv
                        # no route known: none of the points the look-ahead uses (straight ahead) on the labels
                        d.update(route=[], next_waypoint=None, fp_next=None, exit_point=None, current_exit=None,
                                 planned_exit=None, entry_waypoint=None, to_exit_nm=None, to_exit_s=None,
                                 route_after_exit=[], route_before_entry=[], next_unit="", prev_unit="")
            st["live"] = True
            return st
    return LiveWorld


def make_session():
    from ..session import Session
    from ..aircraft import Aircraft
    from .. import performance

    class LiveSession(Session):
        def __init__(self):
            super().__init__("live", mode="realtime", autostart=True, sid=LIVE_SID, scn=_blank_scenario(),
                             label="Live traffic")
            w = self.world
            w.__class__ = _live_world_class()
            now = datetime.now(timezone.utc)
            w.start_utc_min = now.hour * 60 + now.minute + now.second / 60.0
            self._t0 = time.time()
            self.solo = True
            self.status = "running"
            self.version = 0
            self._pts: Dict[str, str] = {}
            FEED.setup(w.airspace)
            FEED.start(w.airspace.to_xy)

        def _snapshot(self) -> None:          # nothing to rewind: it is the present
            pass

        def _maybe_snapshot(self) -> None:
            pass

        def _persist(self, rewrite: bool = False) -> None:
            pass

        def control(self, action, seconds=1.0, time_factor=None, mode=None):
            raise ValueError("live traffic: view only")

        def _tick(self) -> bool:
            """One second: the clock (the real time) and the aircraft where the feed puts them now."""
            w = self.world
            t_new = time.time() - self._t0
            dt = max(0.0, t_new - w.t)
            w.t = t_new
            self._place(dt)
            sw = w.radar_sweep_s
            pic = getattr(w, "_radar", None)
            if sw and (math.floor(w.t / sw + 1e-9) != math.floor((w.t - max(dt, 1.0)) / sw + 1e-9)
                       or (w.aircraft and (pic is None or not pic["ac"]))):       # (the first traffic: painted at once)
                w._paint(sw)
            return True

        def _place(self, dt: float) -> None:
            w, asp, now = self.world, self.world.airspace, time.time()
            with FEED.lock:
                targets = list(FEED.targets.values())
                routes = dict(FEED.routes)
            x0, y0, x1, y1 = FEED.box
            seen = set()
            for a in targets:
                alt = a.get("alt_baro")
                if alt == "ground" or alt is None or float(alt) < 500:
                    continue
                age = min(max(0.0, now - a["t_pos"]), 30.0)
                x, y = asp.to_xy(float(a["lat"]), float(a["lon"]))
                gs, trk = float(a.get("gs") or 0), float(a.get("track") or a.get("true_heading") or 0)
                x += gs * age / 3600 * math.sin(math.radians(trk))        # carried on along its track since the report
                y += gs * age / 3600 * math.cos(math.radians(trk))
                if not (x0 <= x <= x1 and y0 <= y <= y1):
                    continue
                cs = (a.get("flight") or "").strip() or a.get("r") or a["hex"].upper()
                if cs in seen:
                    cs = f"{cs}_{a['hex'][-3:].upper()}"
                seen.add(cs)
                ac = w.aircraft.get(cs)
                hx = a["hex"]
                pa, pb = f"@LV{hx}A", f"@LV{hx}B"
                if ac is None:
                    t = str(a.get("t") or "")
                    perf = performance.get(perf_type(t, a.get("category", "")), w.env.dT)
                    w.waypoints[pa], w.waypoints[pb] = (x, y), (x, y + 1)
                    ac = Aircraft(callsign=cs, perf=perf, route=[pa, pb], entry_time=0.0, fl=int(float(alt) // 100),
                                  mass=perf.oew + 0.6 * (perf.mtow - perf.oew))
                    ac.spawn_time = w.t
                    w.aircraft[cs] = ac
                    self._pts[cs] = hx
                # the route: from here straight ahead along the track (the look-ahead's trajectory)
                w.waypoints[pa] = (x, y)
                w.waypoints[pb] = (x + 400 * math.sin(math.radians(trk)), y + 400 * math.cos(math.radians(trk)))
                ac.route, ac.next_index, ac.leg_from, ac.nav_mode = [pa, pb], 1, (x, y), "lnav"
                ac.x, ac.y, ac.alt_ft = x, y, float(alt)
                ac.vs_fpm = float(a.get("baro_rate") or a.get("geom_rate") or 0)
                ac.gs, ac.trk = gs, trk
                ac.southbound = math.cos(math.radians(trk)) < 0      # the semicircular rule: its direction of flight
                ac.hdg = float(a.get("true_heading") or a.get("mag_heading") or trk)
                ac.tas = float(a.get("tas") or 0) or gs
                sel = a.get("nav_altitude_mcp") or a.get("nav_altitude_fms")
                ac.cleared_alt_ft = float(round(float(sel) / 100) * 100) if sel else float(round(float(alt) / 100) * 100)
                if a.get("mach"):
                    ac.spd_mach, ac.spd_cas = None, None
                inside = asp.inside((x, y))
                ac.status, ac.gone = ("active" if inside else "left_sector"), False
                ac.assumed = ac.on_frequency = ac.entered = inside
                ac.released = False
                sq = str(a.get("squawk") or "")
                rt = routes.get(cs.split("_")[0]) or {}
                # what the transponder sends: enhanced Mode S when it broadcasts a selected level or an airspeed
                ehs = bool(sel or a.get("ias") or a.get("mach"))
                ac.equipment = "SDFGRWY/LB1" if ehs else "SDFGRWY/SB1"
                if a.get("ias") and not a.get("tas"):
                    from .. import atmosphere as atm
                    ac.tas = atm.cas_to_tas(float(a["ias"]) * atm.KT, ac.alt_ft * atm.FT, ac.perf.dT) / atm.KT
                ac.flight_plan = {"callsign": cs, "aircraft_type": str(a.get("t") or ac.icao), "ssr": sq,
                                  "mode_s_address": hx.upper(), "equipment": ac.equipment,
                                  "departure": rt.get("orig", ""), "destination": rt.get("dest", ""),
                                  "route": "not known (flight plans are not public): the look-ahead flies the present track",
                                  "wake": {"A5": "H", "A4": "M", "A3": "M", "A2": "L", "A1": "L"}.get(a.get("category", ""), "M")}
                em = str(a.get("emergency") or "none")
                ac.emergency = ({"code": sq, "kind": "general", "text": em} if em not in ("none", "") or sq in ("7500", "7600", "7700")
                                else None)
                ac.military = bool(int(a.get("dbFlags") or 0) & 1)
                ac.live = {"hex": hx, "reg": a.get("r"), "type": a.get("t"), "desc": a.get("desc"),
                           "age_s": round(age, 1), "selected_fl": int(round(float(sel) / 100)) if sel else None,
                           "selected_hdg": a.get("nav_heading"), "ias_kt": a.get("ias"), "mach": a.get("mach"),
                           "orig": rt.get("orig"), "dest": rt.get("dest"), "squawk": sq}
                ac._trail(dt)
            for cs in [c for c in w.aircraft if c not in seen]:     # not heard any more: off the picture
                hx = self._pts.pop(cs, None)
                w.aircraft.pop(cs, None)
                if hx:
                    w.waypoints.pop(f"@LV{hx}A", None)
                    w.waypoints.pop(f"@LV{hx}B", None)
            self.version += 1 if seen else 0

    return LiveSession()


_session_lock = threading.Lock()


def session():
    """The live traffic's session (made at the first look)."""
    from . import debriefs
    m = debriefs.manager
    with _session_lock:
        s = m.sessions.get(LIVE_SID)
        if s is None:
            s = make_session()
            with m.lock:
                m.sessions[LIVE_SID] = s
        return s


# ------------------------------------------------------------------ pages and API
@router.get("/live", include_in_schema=False)
def live_page(request: Request):
    require_user(request)
    if not ENABLED:
        raise HTTPException(404, "live traffic is off on this server")
    from .exercises import page
    return page("radar.html")


@router.get("/api/live")
def live_status(request: Request):
    """Open (or keep open: every few seconds) the live traffic: the session to show, the state of the feed."""
    require_user(request)
    if not ENABLED:
        raise HTTPException(404, "live traffic is off on this server")
    s = session()
    FEED.watched_at = time.time()
    st = dict(FEED.status)
    w = s.world
    return {"sid": s.id, "credit": CREDIT, "source": SOURCE.split("/")[2] if "//" in SOURCE else SOURCE,
            "mode": FEED.mode(), "aircraft": len(w.aircraft), "in_cta": sum(1 for a in w.aircraft.values() if a.active),
            "last_ok": st.get("last_ok"), "last_error": st.get("last_error"), "last_error_t": st.get("last_error_t"),
            "every_s": round(FEED.cycle_s(), 1), "gap_s": round(FEED.gap, 1), "measure_until": FEED.measure_until or None,
            "polls": st.get("polls"), "limited": st.get("limited"), "last_limit_t": st.get("last_limit_t"),
            "hold_s": max(0, round(FEED.hold_until - time.time())),
            "circles": len(FEED.circles)}


@router.get("/api/live/coverage")
def coverage(request: Request):
    """Where positions are received: the count of aircraft heard in every 10 NM cell over the readings so far."""
    require_user(request)
    session()
    with FEED.lock:
        g = dict(FEED.grid or {})
    return g


class MeasureIn(BaseModel):
    hours: float = 24


@router.post("/api/live/measure")
def measure(body: MeasureIn, request: Request):
    """Admins: keep reading the feed (slowly) for so many hours, for the coverage map; 0 stops. reset: start again."""
    u = require_user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "admin only")
    session()
    FEED.measure_until = time.time() + max(0.0, min(float(body.hours), 24 * 14)) * 3600 if body.hours > 0 else 0.0
    return {"measure_until": FEED.measure_until or None}


@router.post("/api/live/coverage/reset")
def coverage_reset(request: Request):
    u = require_user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "admin only")
    session()
    with FEED.lock:
        g = FEED.grid
        g["counts"], g["polls"], g["since"] = [0] * (g["nx"] * g["ny"]), 0, time.time()
    FEED.save_grid()
    return {"ok": True}


def start() -> None:
    """At the server's start: the coverage measurement runs from the beginning when there is none yet."""
    if ENABLED and not os.path.exists(os.path.join(LIVE_DIR, "coverage.json")):
        try:
            session()
        except Exception as e:                               # noqa: BLE001
            log.warning("live traffic: %s", e)
