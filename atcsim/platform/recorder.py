"""Exercise recording and the .mp4 of it.

While an exercise runs, its folder data/recordings/<exercise id>/ collects:
  static.json   the airspace drawn on the scope (outline, sectors, routes, points, areas)
  frames.jsonl  one line per wall-clock second: sim time, status, every aircraft (position, levels, speed, state),
                current losses of separation, active areas, controller label entries
  weather.jsonl + wx_<n>.npy   the composite reflectivity every ~20 s
  audio.bin     every radio and phone transmission as received: [wall f64][channel u8][speaker u16][n u32][mu-law]
  events.jsonl  push-to-talk, calls (ringing / answered / ended), people joining and leaving
When the exercise ends, a background thread renders the scope at 2 frames per second with Pillow, mixes the audio
(16 kHz) with numpy and encodes both with ffmpeg (H.264 + AAC) into exercise.mp4.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import threading
import time
import wave
from typing import Dict, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, db
from .access import seat_for
from .auth import require_user

router = APIRouter()
manager = None
REC_DIR = os.path.join(config.DATA_DIR, "recordings")
os.makedirs(REC_DIR, exist_ok=True)
RATE = 16000
FPS = 2
VW, VH = 1280, 720
_HDR = struct.Struct("<dBHI")

_active: Dict[str, "Capture"] = {}
_lock = threading.Lock()


def folder(eid: str) -> str:
    return os.path.join(REC_DIR, eid)


# ====================================================================== capture
class Capture:
    def __init__(self, eid: str, s, name: str):
        self.eid, self.s, self.name = eid, s, name
        self.dir = folder(eid)
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        self.audio_f = open(os.path.join(self.dir, "audio.bin"), "ab")
        self.events_f = open(os.path.join(self.dir, "events.jsonl"), "a")
        self.frames_f = open(os.path.join(self.dir, "frames.jsonl"), "a")
        self.wx_f = open(os.path.join(self.dir, "weather.jsonl"), "a")
        self.ui_f = open(os.path.join(self.dir, "ui.jsonl"), "a")
        self.atc_wx = None                       # weather product the controller displays: (mode, fl)
        self._routes: Dict[str, list] = {}
        self._ev_seq = 0
        self.stop = threading.Event()
        self._write_static()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"rec-{eid}")
        self.thread.start()

    def _write_static(self) -> None:
        from ..session import sector_description
        with self.s.lock:
            w = self.s.world
            sec = sector_description(w)
            areas = [a.snapshot(w.t) for a in w.areas.values()]
        keep = ("outline", "shape", "sectors", "waypoints", "routes", "rad_routes", "external_points",
                "transfer_points", "controlled_sectors", "bbox_nm", "name", "unit", "airways", "provisional_points",
                "coordination_points", "coast")
        st = {"name": self.name, "scenario": self.s.scenario_name, "created": time.time(), "sep_h": w.sep_h,
              "sector": {k: sec.get(k) for k in keep}, "areas": areas}
        with open(os.path.join(self.dir, "static.json"), "w") as f:
            json.dump(st, f)

    def _run(self) -> None:
        last_wx_t, nwx = -1e9, 0
        while not self.stop.wait(1.0):
            try:
                with self.s.lock:
                    w, wall = self.s.world, time.time()
                    ac, rt = [], {}
                    for a in [x for x in w.aircraft.values() if x.active or (x.outside and x.assumed)]:
                        e = w.entries.get(a.callsign)
                        mode, val = a.speed_target()
                        spd = f"M{int(round(a.mach() * 100))}" if mode == "mach" else f"I{int(round(a.cas()))}"
                        nav = {"lnav": "L", "heading": "H", "wxdev": "W"}.get(a.nav_mode, "L")
                        ac.append([a.callsign, round(a.x, 2), round(a.y, 2), int(round(a.alt_ft / 100)),
                                   int(round(a.cleared_alt_ft / 100)), int(round(a.gs)), int(round(a.trk)),
                                   int(round(a.vs_fpm, -2)), 1 if a.on_frequency and not a.released else 0,
                                   1 if a.assumed else 0,
                                   [e.get("cfl"), e.get("speed"), e.get("heading"), e.get("direct"), e.get("note")] if e else None,
                                   a.icao, nav, a.next_wp if nav == "L" else None,
                                   int(round(a.target_hdg)) % 360 or 360 if nav != "L" and a.target_hdg is not None else None,
                                   spd, a.flight_plan.get("destination") or "", a.entry_wp,
                                   (a.formation or {}).get("size"), 1 if a.military else 0,
                                   [a.arr.get("tod_in_nm"), a.arr.get("loa_latest_descent_in_nm"), a.arr.get("runway"),
                                    a.vs_fpm < -100 or a.cleared_alt_ft < a.alt_ft - 50]
                                   if a.destination else None,
                                   [int(round(a.hdg)) % 360 or 360,
                                    (f"M{int(round(a.speed_target()[1] * 100))}" if a.speed_target()[0] == "mach"
                                     else f"I{int(round(a.speed_target()[1]))}") if a.speed_assigned else None]])
                        route = [x for x in a.route if not x.startswith("@")]
                        if self._routes.get(a.callsign) != route:
                            self._routes[a.callsign] = route
                            rt[a.callsign] = route
                    fr = {"w": round(wall, 2), "t": round(w.t, 1), "st": self.s.status, "ac": ac,
                          "los": [list(k) for k in w._los], "areas": [a.id for a in w.areas.values() if a.active]}
                    if rt:
                        fr["rt"] = rt
                    stca = [c["aircraft"] for c in w.stca()]
                    if stca:
                        fr["stca"] = stca
                    tr = [[a.callsign, round(a.x, 2), round(a.y, 2), int(round(a.alt_ft / 100)), int(round(a.gs)),
                           int(round(a.trk)), a.exit_point] for a in w.aircraft.values()
                          if a.outside and not a.assumed]
                    if tr:
                        fr["tr"] = tr
                    if w.aerodromes and (w.t - getattr(self, "_ad_t", -1e9) >= 30):
                        from ..session import arrivals_description
                        self._ad_t = w.t
                        ad = json.dumps(arrivals_description(w), separators=(",", ":"), default=str)
                        if ad != getattr(self, "_ad_last", None):
                            self._ad_last = ad
                            fr["ad"] = json.loads(ad)
                    # the controller's actions for the captions of the video
                    acts = []
                    for ev in w.events:
                        if ev["seq"] <= self._ev_seq:
                            continue
                        if ev["type"] in ("atc_entry", "assumed"):
                            acts.append({k: ev.get(k) for k in ("type", "callsign", "entry")})
                        elif ev["type"] == "oldi" and ev.get("direction") == "out":
                            acts.append({"type": "oldi", "callsign": ev.get("callsign"), "text": ev.get("text")})
                    if w.events:
                        self._ev_seq = w.events[-1]["seq"]
                    if acts:
                        fr["acts"] = acts
                    t, wx = w.t, w.env.weather
                    bb = w.airspace.bbox
                with self.lock:
                    self.frames_f.write(json.dumps(fr, separators=(",", ":")) + "\n")
                    self.frames_f.flush()
                prod = self.atc_wx or ("composite", 350)
                every = 6 if prod[0] in ("cappi", "sel", "edr") else 15        # the radar page refreshes every 4 s
                if abs(t - last_wx_t) >= every or prod != getattr(self, "_last_prod", None):
                    last_wx_t, self._last_prod = t, prod
                    m = 30.0 if (bb[2] - bb[0] > 200 or bb[3] - bb[1] > 200) else 10.0
                    box = (bb[0] - m, bb[1] - m, bb[2] + m, bb[3] + m)
                    res = 4.0 if box[2] - box[0] > 250 else 2.0
                    # same encodings as /weather/radar: dBZ = v/2 - 10 ; echo tops FL = v*5 ; EDR = v/250
                    if prod[0] in ("cappi", "sel"):
                        _, _, z = wx.cappi(float(prod[1]), t, box, res)
                        enc = np.where(z > -9.9, np.clip((z + 10) * 2, 1, 255), 0)
                    elif prod[0] == "echo_tops":
                        enc = np.clip(wx.volume(t, box, max(res, 2.0), 20)["echo_tops"] / 5.0, 0, 255)
                    elif prod[0] == "edr":
                        _, _, X, Y = wx.grid(box, res)
                        enc = np.clip(wx.edr(X, Y, float(prod[1]), t) * 250, 0, 255)
                    else:
                        z = wx.volume(t, box, res, 20)["composite"]
                        enc = np.where(z > -9.9, np.clip((z + 10) * 2, 1, 255), 0)
                    enc = enc.astype(np.uint8)
                    cells = [[c["x_nm"], c["y_nm"], c["echo_top_fl"], c["max_dbz"], c["stage"][0]]
                             for c in wx.cells_summary(t)]
                    fn = f"wx_{nwx:05d}.npy"
                    np.save(os.path.join(self.dir, fn), enc)
                    nwx += 1
                    with self.lock:
                        self.wx_f.write(json.dumps({"w": round(time.time(), 2), "t": t, "file": fn, "bbox": box,
                                                    "res": res, "product": list(prod), "cells": cells}) + "\n")
                        self.wx_f.flush()
            except Exception as e:  # noqa: BLE001 - recording must never break the exercise
                self.event({"type": "recorder_error", "wall": time.time(), "error": f"{type(e).__name__}: {e}"})

    def audio(self, wall: float, chan: int, conn: int, payload: bytes) -> None:
        with self.lock:
            self.audio_f.write(_HDR.pack(wall, chan, conn, len(payload)))
            self.audio_f.write(payload)

    def ui(self, rows: list) -> None:
        with self.lock:
            for r in rows:
                self.ui_f.write(json.dumps(r, separators=(",", ":")) + "\n")
            self.ui_f.flush()

    def event(self, ev: dict) -> None:
        with self.lock:
            self.events_f.write(json.dumps(ev, separators=(",", ":")) + "\n")
            self.events_f.flush()

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        with self.lock:
            for f in (self.audio_f, self.events_f, self.frames_f, self.wx_f, self.ui_f):
                f.close()


def start(eid: str, s, name: str) -> None:
    with _lock:
        if eid not in _active:
            _active[eid] = Capture(eid, s, name)


def audio(eid: str, wall: float, chan: int, conn: int, payload: bytes) -> None:
    c = _active.get(eid)
    if c:
        c.audio(wall, chan, conn, payload)


def ui(eid: str, user_id, conn: int, recv_wall: float, now_ms: float, events: list, wx=None) -> None:
    """Controller screen activity (mouse, clicks, view, on-screen panels), timed on the server clock."""
    c = _active.get(eid)
    if not c:
        return
    rows = []
    for e in events[:2000]:
        try:
            t, kind, data = e
            rows.append({"u": user_id, "c": conn, "w": round(recv_wall - (float(now_ms) - float(t)) / 1000.0, 3),
                         "k": str(kind)[:2], "d": data})
        except (TypeError, ValueError):
            continue
    if wx:
        c.atc_wx = (str(wx[0]), float(wx[1]) if wx[1] else 350.0) if wx[0] != "off" else c.atc_wx
    c.ui(rows)


def event(eid: str, ev: dict) -> None:
    c = _active.get(eid)
    if c:
        c.event(ev)


def finish(eid: str, s=None) -> Optional[int]:
    """Stop capturing and render the video in the background. Returns the recording id (None: nothing recorded)."""
    with _lock:
        c = _active.pop(eid, None)
    if c:
        c.close()
    if not os.path.exists(os.path.join(folder(eid), "frames.jsonl")):
        return None
    old = db.q1("SELECT id FROM recordings WHERE exercise_id=?", (eid,))
    if old:
        return old["id"]
    # one video per controller who was in the exercise (their screen: mouse, clicks, entries, what they heard);
    # an overview of the radar picture when no controller connected
    users = []
    for r in _load_jsonl(os.path.join(folder(eid), "ui.jsonl")):
        if r.get("u") not in users:
            users.append(r.get("u"))
    jobs = []
    for u in users:
        who = db.user_by_id(u) if u else None
        title = "Controller screen · " + (db.public_user(who)["name"] if who else "controller")
        jobs.append((db.x("INSERT INTO recordings (exercise_id, status, created_at, user_id, title) VALUES "
                          "(?, 'rendering', ?, ?, ?)", (eid, time.time(), u, title)), u))
    if not jobs:
        jobs.append((db.x("INSERT INTO recordings (exercise_id, status, created_at, title) VALUES (?, 'rendering', ?, ?)",
                          (eid, time.time(), "Radar overview")), None))
    threading.Thread(target=_render_all, args=(eid, jobs), daemon=True, name=f"render-{eid}").start()
    return jobs[0][0]


def _render_all(eid: str, jobs: list) -> None:
    for rid, u in jobs:
        render_job(rid, eid, u)


def recover() -> None:
    """After a restart: exercises whose session is gone are ended, and their recordings rendered."""
    from .exercises import manager as _m
    for e in db.q("SELECT id FROM exercises WHERE ended_at IS NULL"):
        if _m is not None and e["id"] in _m.sessions:
            continue                                   # restored: it goes on
        db.x("UPDATE exercises SET ended_at=?, status='ended' WHERE id=?", (time.time(), e["id"]))
        if os.path.exists(os.path.join(folder(e["id"]), "frames.jsonl")):
            finish(e["id"])
    for r in db.q("SELECT id, exercise_id, user_id FROM recordings WHERE status IN ('rendering', 'pending')"):
        threading.Thread(target=render_job, args=(r["id"], r["exercise_id"], r["user_id"]), daemon=True).start()


# ====================================================================== rendering
def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for p in (f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono{'-Bold' if bold else ''}.ttf",
              "/usr/share/fonts/TTF/DejaVuSansMono.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _load_jsonl(path: str) -> list:
    out = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass                      # a line cut by a crash
    return out


def _ulaw_table() -> np.ndarray:
    u = np.arange(256, dtype=np.int32) ^ 0xFF
    sign, exp, man = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    mag = (((man << 3) + 0x84) << exp) - 0x84
    return np.where(sign != 0, -mag, mag).astype(np.float32) / 32768.0


ULAW = _ulaw_table()


def mix_audio(path: str, t0: float, t1: float, keep=None) -> np.ndarray:
    """All transmissions on one 16 kHz track. Each speaker's frames are laid end to end while they arrive in
    sequence (absorbing network jitter), and placed at their arrival time after a gap."""
    n = int(math.ceil((t1 - t0) * RATE)) + RATE
    out = np.zeros(n, np.float32)
    if not os.path.exists(path):
        return out
    cursor: Dict[tuple, int] = {}
    with open(path, "rb") as f:
        data = f.read()
    i = 0
    while i + _HDR.size <= len(data):
        wall, chan, conn, ln = _HDR.unpack_from(data, i)
        i += _HDR.size
        chunk = data[i:i + ln]
        i += ln
        if len(chunk) < ln:
            break
        if keep is not None and not keep(wall, chan, conn):
            continue
        pcm = ULAW[np.frombuffer(chunk, np.uint8)]
        start = int(round((wall - t0) * RATE)) - len(pcm)
        key = (chan, conn)
        c = cursor.get(key)
        if c is not None and abs(start - c) < 0.3 * RATE:
            start = c
        cursor[key] = start + len(pcm)
        if start < 0 or start + len(pcm) > n:
            continue
        out[start:start + len(pcm)] += pcm * (0.9 if chan == 0 else 0.8)
    return np.clip(out, -1, 1)


class Scope:
    """Draws the radar picture of a recorded exercise (Pillow)."""
    COL = {"assumed": (95, 211, 163), "calling": (240, 244, 248), "other": (111, 129, 145), "los": (255, 90, 90)}

    def __init__(self, static: dict):
        from PIL import Image, ImageDraw
        self.sec = static["sector"]
        self.areas = {a["id"]: a for a in static.get("areas", [])}
        poly = (self.sec.get("shape") or {}).get("polygon") or self.sec.get("outline") or [[-100, -100], [100, 100]]
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        self.cx, self.cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 30
        self.top = 44
        self.s = min(VW, VH - self.top) / (2 * half)
        self.f9, self.f10, self.f11, self.f13 = _font(10), _font(11), _font(12), _font(15, True)
        bg = Image.new("RGB", (VW, VH), (11, 16, 22))
        d = ImageDraw.Draw(bg)
        wp = self.sec.get("waypoints") or {}
        for sc in self.sec.get("sectors") or []:
            if len(self.sec.get("sectors") or []) > 1:
                d.line([self.px(*p) for p in sc["polygon"] + sc["polygon"][:1]], fill=(31, 74, 58), width=1)
        if self.sec.get("outline"):
            d.line([self.px(*p) for p in self.sec["outline"] + self.sec["outline"][:1]], fill=(45, 66, 88), width=1)
        d.line([self.px(*p) for p in poly + poly[:1]], fill=(74, 104, 134), width=2)
        for r in self.sec.get("routes") or []:
            pts = [self.px(*wp[n]) for n in r if n in wp]
            if len(pts) > 1:
                d.line(pts, fill=(27, 40, 53), width=1)
        ext = set(self.sec.get("external_points") or [])
        for n, p in wp.items():
            x, y = self.px(*p)
            if not (0 <= x < VW and self.top <= y < VH):
                continue
            col = (65, 88, 110) if n in ext else (92, 122, 150)
            d.polygon([(x, y - 4), (x + 4, y + 3), (x - 4, y + 3)], outline=col)
            d.text((x + 6, y - 2), n, fill=col, font=self.f9)
        self.bg = bg
        self._wx_cache: Dict[str, object] = {}

    def px(self, x: float, y: float):
        return (VW / 2 + (x - self.cx) * self.s, self.top + (VH - self.top) / 2 - (y - self.cy) * self.s)

    def weather(self, folder_: str, meta: dict):
        from PIL import Image
        key = meta["file"]
        if key in self._wx_cache:
            return self._wx_cache[key]
        enc = np.load(os.path.join(folder_, key))
        dbz = enc.astype(np.float32) / 2 - 10
        rgba = np.zeros(enc.shape + (4,), np.uint8)
        bins = [(15, (3, 0, 244)), (20, (2, 253, 2)), (25, (1, 197, 1)), (30, (0, 142, 0)), (35, (253, 248, 2)),
                (40, (229, 188, 0)), (45, (253, 149, 0)), (50, (253, 0, 0)), (55, (212, 0, 0)), (60, (188, 0, 0)),
                (65, (248, 0, 253))]
        for d_, c in bins:
            m = (enc > 0) & (dbz >= d_)
            rgba[m, :3] = c
            rgba[m, 3] = 55 if d_ < 30 else 140
        img = Image.fromarray(rgba[::-1], "RGBA")            # rows south->north: flip so north is up
        b = meta["bbox"]
        (x0, y0), (x1, y1) = self.px(b[0], b[3]), self.px(b[2], b[1])
        img = img.resize((max(1, int(x1 - x0)), max(1, int(y1 - y0))), Image.BILINEAR)
        self._wx_cache = {key: (img, (int(x0), int(y0)))}
        return self._wx_cache[key]

    def frame(self, f0: dict, f1: Optional[dict], k: float, trail: list, wx, banner: dict):
        from PIL import ImageDraw
        img = self.bg.copy()
        if wx is not None:
            im, pos = wx
            img.paste(im, pos, im)
        d = ImageDraw.Draw(img)
        for aid in f0.get("areas", []):
            a = self.areas.get(aid)
            if not a:
                continue
            col = {"danger": (224, 138, 60), "restricted": (224, 193, 60), "prohibited": (255, 90, 90)}.get(a["kind"], (200, 200, 60))
            sh = a["shape"]
            if sh["type"] == "circle":
                (cx, cy), r = self.px(*sh["center"]), sh["radius_nm"] * self.s
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=2)
            else:
                d.line([self.px(*p) for p in sh["points"] + sh["points"][:1]], fill=col, width=2)
            d.text(self.px(*(sh.get("center") or sh["points"][0])), a.get("designator", aid), fill=col, font=self.f9)
        nxt = {r[0]: r for r in (f1 or {}).get("ac", [])}
        los = {cs for p in f0.get("los", []) for cs in p}
        for cs, pts in trail.items():
            col = self.COL["other"]
            for (x, y) in pts:
                px, py = self.px(x, y)
                d.rectangle([px - 1, py - 1, px + 1, py + 1], fill=col)
        for r in f0.get("ac", []):
            cs, x, y, fl, cfl, gs, trk, vs, onf, asm, ent = r[:11]
            ecfl = ent[0] if isinstance(ent, list) else ent
            n = nxt.get(cs)
            if n:
                x, y = x + (n[1] - x) * k, y + (n[2] - y) * k
            col = self.COL["los"] if cs in los else self.COL["assumed"] if asm else \
                self.COL["calling"] if onf else self.COL["other"]
            px, py = self.px(x, y)
            if not (-50 < px < VW + 50 and self.top - 50 < py < VH + 50):
                continue
            a = math.radians(trk)
            vx, vy = self.px(x + math.sin(a) * gs / 30, y + math.cos(a) * gs / 30)
            d.line([(px, py), (vx, vy)], fill=col, width=1)
            d.rectangle([px - 3, py - 3, px + 3, py + 3], outline=col, width=2)
            arrow = "^" if vs > 0 else "v" if vs < 0 else " "
            shown_cfl = ecfl if ecfl is not None else cfl
            l1 = cs + ("  CALL" if onf and not asm else "")
            l2 = f"{fl:03d}{arrow}{shown_cfl if shown_cfl != fl else ''}"
            if ecfl is not None and ecfl != cfl:
                l2 += f" S{cfl}"
            d.text((px + 9, py - 22), l1, fill=col, font=self.f10)
            d.text((px + 9, py - 10), l2, fill=col, font=self.f10)
            d.text((px + 9, py + 2), f"{gs}", fill=col, font=self.f10)
        # banner
        d.rectangle([0, 0, VW, self.top - 1], fill=(17, 25, 34))
        d.line([(0, self.top - 1), (VW, self.top - 1)], fill=(31, 43, 56))
        d.text((12, 6), banner["title"], fill=(201, 214, 226), font=self.f13)
        d.text((12, 25), banner["sub"], fill=(108, 127, 146), font=self.f10)
        x = VW - 12
        for text, bgc, fg in reversed(banner["pills"]):
            w = d.textlength(text, font=self.f11) + 14
            d.rectangle([x - w, 10, x, 32], fill=bgc)
            d.text((x - w + 7, 14), text, fill=fg, font=self.f11)
            x -= w + 8
        return img


def render(eid: str) -> dict:
    """Render data/recordings/<eid>/exercise.mp4. Returns {path, duration_s, size_bytes}."""
    dr = folder(eid)
    with open(os.path.join(dr, "static.json")) as f:
        static = json.load(f)
    frames = _load_jsonl(os.path.join(dr, "frames.jsonl"))
    events = _load_jsonl(os.path.join(dr, "events.jsonl"))
    wxs = _load_jsonl(os.path.join(dr, "weather.jsonl"))
    if len(frames) < 2:
        raise RuntimeError("nothing recorded")
    talk = [e for e in events if e["type"] in ("ptt", "call")]
    starts = [f["w"] for f in frames if f["st"] == "running"] + [e["wall"] for e in talk]
    t0 = max(frames[0]["w"], (min(starts) if starts else frames[0]["w"]) - 3)
    t1 = frames[-1]["w"]
    if t1 - t0 < 1:
        t0 = frames[0]["w"]
    nfr = int((t1 - t0) * FPS) + 1
    pcm = mix_audio(os.path.join(dr, "audio.bin"), t0, t1)[: int(nfr / FPS * RATE) + RATE // FPS]
    wav = os.path.join(dr, "audio.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    out = os.path.join(dr, "overview.mp4")
    tmp = out + ".part.mp4"
    cmd = (["nice", "-n", "15"] if shutil.which("nice") else []) + ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{VW}x{VH}",
           "-r", str(FPS), "-i", "pipe:0", "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
           "-shortest", "-movflags", "+faststart", tmp]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    scope = Scope(static)
    walls = [f["w"] for f in frames]
    # who is talking, when
    ptt_spans, calls = [], []
    open_ptt, open_calls = {}, {}
    for e in talk:
        if e["type"] == "ptt":
            if e["on"]:
                open_ptt[e["conn"]] = (e["wall"], e["name"], e["seat"])
            elif e["conn"] in open_ptt:
                s0, nm, seat = open_ptt.pop(e["conn"])
                ptt_spans.append((s0, e["wall"], nm, seat))
        else:
            if e["state"] in ("ringing", "active"):
                prev = open_calls.get(e["call"])
                open_calls[e["call"]] = (prev[0] if prev else e["wall"], e["unit"], e["state"], e.get("direction") or
                                         (prev[3] if prev else ""))
            elif e["call"] in open_calls:
                s0, unit, st, dirn = open_calls.pop(e["call"])
                calls.append((s0, e["wall"], unit, dirn))
    for c, (s0, nm, seat) in open_ptt.items():
        ptt_spans.append((s0, t1, nm, seat))
    for c, (s0, unit, st, dirn) in open_calls.items():
        calls.append((s0, t1, unit, dirn))
    import bisect
    wx_walls = [m["w"] for m in wxs]
    try:
        for i in range(nfr):
            wall = t0 + i / FPS
            j = max(0, bisect.bisect_right(walls, wall) - 1)
            f0, f1 = frames[j], frames[min(j + 1, len(frames) - 1)]
            k = 0.0 if f1["w"] <= f0["w"] or f0["st"] != "running" else max(0.0, min(1.0, (wall - f0["w"]) / (f1["w"] - f0["w"])))
            trail = {}
            for back in (4, 8, 12, 16, 20):
                if j - back >= 0:
                    for r in frames[j - back]["ac"]:
                        trail.setdefault(r[0], []).append((r[1], r[2]))
            wx = None
            wj = bisect.bisect_right(wx_walls, wall) - 1 if wxs else -1
            if wj >= 0:
                wx = scope.weather(dr, wxs[wj])
            st = f0["st"]
            pills = []
            for (s0, s1, nm, seat) in ptt_spans:
                if s0 <= wall <= s1:
                    pills.append((f"RADIO  {nm} ({seat.upper()})", (180, 40, 40), (255, 255, 255)))
            for (s0, s1, unit, dirn) in calls:
                if s0 <= wall <= s1:
                    pills.append((f"PHONE  {unit} {'<- ATC' if dirn == 'atc_to_unit' else '-> ATC'}", (40, 90, 160), (255, 255, 255)))
            if f0["los"]:
                pills.append(("LOSS OF SEPARATION", (255, 90, 90), (255, 255, 255)))
            pills.append((st.upper(), (40, 120, 90) if st == "running" else (90, 90, 40), (255, 255, 255)))
            tsim = int(f0["t"] + (k * (f1["t"] - f0["t"]) if f1 is not f0 else 0))
            banner = {"title": f"{static['name']}  -  {static['scenario']}",
                      "sub": f"T+{tsim // 3600:d}:{tsim // 60 % 60:02d}:{tsim % 60:02d}   "
                             f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(wall))}   "
                             f"{len(f0['ac'])} aircraft", "pills": pills}
            img = scope.frame(f0, f1, k, trail, wx, banner)
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err[-500:]}")
    os.replace(tmp, out)
    os.remove(wav)
    return {"path": out, "duration_s": round(nfr / FPS, 1), "size_bytes": os.path.getsize(out)}


_render_lock = threading.Lock()          # one video at a time, at low priority: never slow a live exercise down


def render_job(rid: int, eid: str, user_id=None) -> None:
    with _render_lock:
        try:
            os.nice(15)                      # this thread only (Linux)
        except OSError:
            pass
        _render_job(rid, eid, user_id)


def _render_job(rid: int, eid: str, user_id=None) -> None:
    try:
        if user_id is not None or _has_ui(eid):
            from .atcview import render_controller
            r = render_controller(eid, user_id, rid)
        else:
            r = render(eid)
        db.x("UPDATE recordings SET status='ready', path=?, duration_s=?, size_bytes=?, info=NULL WHERE id=?",
             (r["path"], r["duration_s"], r["size_bytes"], rid))
    except Exception as e:  # noqa: BLE001
        db.x("UPDATE recordings SET status='failed', info=? WHERE id=?", (f"{type(e).__name__}: {e}"[:500], rid))


def _has_ui(eid: str) -> bool:
    p = os.path.join(folder(eid), "ui.jsonl")
    return os.path.exists(p) and os.path.getsize(p) > 0


# ====================================================================== API
def _rec_row(r: dict, u: dict) -> dict:
    e = db.exercise(r["exercise_id"]) or {}
    return {"id": r["id"], "exercise_id": r["exercise_id"], "exercise": e.get("name"), "scenario": e.get("scenario"),
            "title": r.get("title") or "",
            "status": r["status"], "created_at": r["created_at"], "duration_s": r["duration_s"],
            "size_bytes": r["size_bytes"], "archived": bool(r["archived"]), "error": r["info"],
            "can_manage": seat_for(r["exercise_id"], u) == "instructor",
            "download": f"/api/recordings/{r['id']}/file" if r["status"] == "ready" else None}


def _get(rid: int, u: dict, manage: bool = False) -> dict:
    r = db.q1("SELECT * FROM recordings WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "no such recording")
    seat = seat_for(r["exercise_id"], u)
    if seat is None or (manage and seat != "instructor"):
        raise HTTPException(403, "not allowed")
    return r


@router.get("/api/recordings")
def recordings(request: Request, archived: bool = False):
    u = require_user(request)
    rows = db.q("SELECT * FROM recordings WHERE archived=? ORDER BY created_at DESC LIMIT 300", (1 if archived else 0,))
    return [_rec_row(r, u) for r in rows if seat_for(r["exercise_id"], u)]


@router.get("/api/recordings/{rid}/file")
def recording_file(rid: int, request: Request):
    r = _get(rid, require_user(request))
    if r["status"] != "ready" or not r["path"] or not os.path.exists(r["path"]):
        raise HTTPException(409, f"recording is {r['status']}")
    e = db.exercise(r["exercise_id"]) or {}
    stamp = time.strftime("%Y%m%d-%H%M", time.gmtime(r["created_at"]))
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (e.get("name") or "exercise"))
    return FileResponse(r["path"], media_type="video/mp4", filename=f"{safe}-{stamp}.mp4")


class Archive(BaseModel):
    archived: bool = True


@router.post("/api/recordings/{rid}/archive")
def archive(rid: int, body: Archive, request: Request):
    _get(rid, require_user(request), manage=True)
    db.x("UPDATE recordings SET archived=? WHERE id=?", (1 if body.archived else 0, rid))
    return {"id": rid, "archived": body.archived}


@router.post("/api/recordings/{rid}/render")
def rerender(rid: int, request: Request):
    r = _get(rid, require_user(request), manage=True)
    db.x("UPDATE recordings SET status='rendering', info=NULL WHERE id=?", (rid,))
    threading.Thread(target=render_job, args=(rid, r["exercise_id"], r.get("user_id")), daemon=True).start()
    return {"id": rid, "status": "rendering"}


@router.delete("/api/recordings/{rid}")
def delete_recording(rid: int, request: Request):
    r = _get(rid, require_user(request), manage=True)
    if r["path"] and os.path.exists(r["path"]):
        os.remove(r["path"])
    db.x("DELETE FROM recordings WHERE id=?", (rid,))
    if not db.q1("SELECT 1 FROM recordings WHERE exercise_id=?", (r["exercise_id"],)):
        shutil.rmtree(folder(r["exercise_id"]), ignore_errors=True)
    return {"deleted": rid}
