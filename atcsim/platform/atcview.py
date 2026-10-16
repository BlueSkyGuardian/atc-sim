"""The controller's screen, re-rendered from the exercise capture: what the controller had on the scope (their zoom,
pan, display options, label positions, weather product), every aircraft and label as the controller's seat showed it
(entered CFL / speed / heading / direct, Mode S level in amber), the on-screen panels (toolbar, HUD, alarms,
label menu with what was being typed, telephone boxes, push-to-talk), the mouse pointer and clicks, a caption log of
the controller's actions, and the audio the controller heard and said (radio + their telephone calls).

Input (see recorder.py): frames.jsonl (traffic every second), ui.jsonl (the controller's browser: 'v' view state,
'o' on-screen panels, 'm' mouse, 'c' clicks, 'd' double-clicks), events.jsonl (joins, push-to-talk, calls),
weather.jsonl, audio.bin, static.json.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import subprocess
import time
import wave
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .recorder import RATE, _load_jsonl, folder, mix_audio

FPS = 10
MAXW = 1600
COL = {"bg": (11, 16, 22), "assumed": (95, 211, 163), "white": (255, 255, 255), "calling2": (201, 214, 226),
       "other": (111, 129, 145), "los": (255, 90, 90), "sel": (127, 184, 255), "amber": (242, 184, 75)}
AREA_COL = {"danger": (224, 138, 60), "restricted": (224, 193, 60), "prohibited": (255, 90, 90)}
DBZ = [(15, (3, 0, 244)), (20, (2, 253, 2)), (25, (1, 197, 1)), (30, (0, 142, 0)), (35, (253, 248, 2)),
       (40, (229, 188, 0)), (45, (253, 149, 0)), (50, (253, 0, 0)), (55, (212, 0, 0)), (60, (188, 0, 0)),
       (65, (248, 0, 253))]
_fonts: Dict[tuple, ImageFont.FreeTypeFont] = {}


def font(px: float, mono: bool = True, bold: bool = False):
    key = (max(7, int(round(px))), mono, bold)
    if key not in _fonts:
        name = ("DejaVuSansMono" if mono else "DejaVuSans") + ("-Bold" if bold else "")
        path = f"/usr/share/fonts/truetype/dejavu/{name}.ttf"
        _fonts[key] = ImageFont.truetype(path, key[0]) if os.path.exists(path) else ImageFont.load_default(key[0])
    return _fonts[key]


_tcache: Dict[tuple, Image.Image] = {}


def text(img: Image.Image, xy, s, fill, f) -> None:
    """Draw text through a cache of rendered glyph masks (rasterising text is most of the rendering time)."""
    if not s:
        return
    key = (s, id(f))
    m = _tcache.get(key)
    if m is None:
        if len(_tcache) > 40000:
            _tcache.clear()
        box = f.getbbox(s)
        m = Image.new("L", (max(1, int(box[2]) + 1), max(1, int(box[3]) + 1)), 0)
        ImageDraw.Draw(m).text((0, 0), s, fill=255, font=f)
        _tcache[key] = m
    a = fill[3] if len(fill) > 3 else 255
    if a <= 0:
        return
    if a < 255:
        m = m.point(lambda v: v * a // 255)
    img.paste(tuple(fill[:3]), (int(round(xy[0])), int(round(xy[1]))), m)


def clean(s: str) -> str:
    """Drop what the fonts cannot draw (emoji, variation selectors)."""
    return "".join(ch for ch in str(s) if ord(ch) <= 0xFFFF and ch != "\ufe0f").strip()


def hexc(s, default=(200, 200, 200, 255)):
    """'#rrggbb' or '#rrggbbaa' -> (r, g, b, a)."""
    try:
        s = s.lstrip("#")
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        a = int(s[6:8], 16) if len(s) >= 8 else 255
        return (r, g, b, a)
    except (ValueError, AttributeError, IndexError):
        return default


def dashed(d: ImageDraw.ImageDraw, pts, fill, width=1, dash=4.0, gap=4.0):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 0.5:
            continue
        ux, uy, p = (x2 - x1) / L, (y2 - y1) / L, 0.0
        while p < L:
            q = min(L, p + dash)
            d.line([(x1 + ux * p, y1 + uy * p), (x1 + ux * q, y1 + uy * q)], fill=fill, width=width)
            p = q + gap


def cpa(a: list, b: list):
    va = (a[5] * math.sin(math.radians(a[6])) / 3600, a[5] * math.cos(math.radians(a[6])) / 3600)
    vb = (b[5] * math.sin(math.radians(b[6])) / 3600, b[5] * math.cos(math.radians(b[6])) / 3600)
    px, py, vx, vy = b[1] - a[1], b[2] - a[2], vb[0] - va[0], vb[1] - va[1]
    v2 = vx * vx + vy * vy
    t = 0.0 if v2 < 1e-12 else max(0.0, -(px * vx + py * vy) / v2)
    return t, math.hypot(px + vx * t, py + vy * t), math.hypot(px, py), (math.degrees(math.atan2(px, py)) + 360) % 360


class View:
    """One state of the controller's scope (from a 'v' snapshot) and its transform into the video frame."""

    def __init__(self, v: dict, W: int, H: int):
        self.v = v
        self.w, self.h = max(100, v.get("w") or 1200), max(100, v.get("h") or 800)
        self.k = min(W / self.w, H / self.h)
        self.ox, self.oy = (W - self.w * self.k) / 2, (H - self.h * self.k) / 2
        self.cx, self.cy, self.s = v.get("cx", 0.0), v.get("cy", 0.0), v.get("s", 2.0)
        self.V = v.get("V") or {}
        self.wx = v.get("wx") or ["cappi", 350, True]
        self.lo = v.get("lo") or {}
        self.key = json.dumps([self.w, self.h, self.cx, self.cy, self.s, self.V], sort_keys=True)

    def P(self, x: float, y: float):
        return (self.ox + self.k * (self.w / 2 + (x - self.cx) * self.s),
                self.oy + self.k * (self.h / 2 - (y - self.cy) * self.s))

    def S(self, x: float, y: float):                       # scope CSS pixels -> video pixels
        return (self.ox + self.k * x, self.oy + self.k * y)

    def on(self, name: str) -> bool:
        return self.V.get(name, True) is not False


class Renderer:
    def __init__(self, static: dict, W: int, H: int, dr: str):
        self.sec = static["sector"]
        self.areas = {a["id"]: a for a in static.get("areas", [])}
        self.W, self.H, self.dr = W, H, dr
        self.wp = self.sec.get("waypoints") or {}
        self.rad = {tuple(r) for r in (self.sec.get("rad_routes") or [])} | \
            {tuple(reversed(r)) for r in (self.sec.get("rad_routes") or [])}
        self._map_key, self._map = None, None
        self._wx_key, self._wx = None, None

    # ---------------------------------------------------------------- static map for a view
    def map_layer(self, vw: View) -> Image.Image:
        if self._map_key == vw.key:
            return self._map
        img = Image.new("RGB", (self.W, self.H), COL["bg"])
        d = ImageDraw.Draw(img)
        k, sec, wp = vw.k, self.sec, self.wp
        poly = lambda pts: [vw.P(*p) for p in pts] + [vw.P(*pts[0])]
        sectors = sec.get("sectors") or []
        if len(sectors) > 1:
            ctl = set(sec.get("controlled_sectors") or [])
            for sc in sectors:
                if vw.on("sectors"):
                    dashed(d, poly(sc["polygon"]), (47, 122, 95), 1, 3 * k, 4 * k)
                if vw.on("freqs"):
                    cx = sum(p[0] for p in sc["polygon"]) / len(sc["polygon"])
                    cy = sum(p[1] for p in sc["polygon"]) / len(sc["polygon"])
                    x, y = vw.P(cx, cy)
                    d.text((x - 50 * k, y - 8 * k), f"{sc['name'].upper()} {sc.get('frequency') or ''}",
                           fill=(63, 156, 122) if sc["id"] in ctl else (43, 90, 73), font=font(10.5 * k))
        if sec.get("outline"):
            d.line(poly(sec["outline"]), fill=(45, 66, 88), width=max(1, round(1.2 * k)))
        shape = (sec.get("shape") or {}).get("polygon") or (sec.get("shape") or {}).get("corners") or []
        if shape:
            d.line(poly(shape), fill=(74, 104, 134), width=max(1, round(1.8 * k)))
        for r in sec.get("routes") or []:
            rad = tuple(r) in self.rad
            if (rad and not vw.on("rad")) or (not rad and not vw.on("routes")):
                continue
            pts = [vw.P(*wp[n]) for n in r if n in wp]
            if len(pts) < 2:
                continue
            if rad:
                dashed(d, pts, (42, 63, 51), 1, 6 * k, 6 * k)
            else:
                d.line(pts, fill=(27, 40, 53), width=1)
        ext, xfer = set(sec.get("external_points") or []), set(sec.get("transfer_points") or [])
        f = font(10.5 * k)
        for n, p in wp.items():
            x, y = vw.P(*p)
            if not (-20 < x < self.W + 20 and -20 < y < self.H + 20):
                continue
            if n in ext:
                c = (65, 88, 110)
                d.polygon([(x, y - 4 * k), (x + 4 * k, y), (x, y + 4 * k), (x - 4 * k, y)], outline=c)
                if vw.on("names"):
                    text(img, (x + 6 * k, y - 4 * k), n, c, f)
                continue
            c = (92, 122, 150)
            tri = [(x, y - 5 * k), (x + 4.5 * k, y + 3.5 * k), (x - 4.5 * k, y + 3.5 * k)]
            d.polygon(tri, fill=c if n in xfer else None, outline=c)
            if vw.on("names"):
                text(img, (x + 7 * k, y - 4 * k), n, c, f)
        self._map_key, self._map = vw.key, img
        return img

    # ---------------------------------------------------------------- weather (the controller's product)
    def weather(self, vw: View, meta: dict):
        key = (meta["file"], vw.key)
        if self._wx_key == key:
            return self._wx
        enc = np.load(os.path.join(self.dr, meta["file"]))
        b, res = meta["bbox"], meta["res"]
        ny, nx = enc.shape
        # visible part only (zoomed-in views would otherwise make huge images)
        x_lo = vw.cx - (vw.ox / vw.k + vw.w / 2) / vw.s
        x_hi = vw.cx + ((self.W - vw.ox) / vw.k - vw.w / 2) / vw.s
        y_hi = vw.cy + (vw.oy / vw.k + vw.h / 2) / vw.s
        y_lo = vw.cy - ((self.H - vw.oy) / vw.k - vw.h / 2) / vw.s
        i0, i1 = max(0, int((x_lo - b[0]) / res) - 1), min(nx, int((x_hi - b[0]) / res) + 2)
        j0, j1 = max(0, int((y_lo - b[1]) / res) - 1), min(ny, int((y_hi - b[1]) / res) + 2)
        if i1 <= i0 or j1 <= j0:
            self._wx_key, self._wx = key, None
            return None
        sub = enc[j0:j1, i0:i1]
        dbz = sub.astype(np.float32) / 2 - 10
        rgba = np.zeros(sub.shape + (4,), np.uint8)
        for d_, c in DBZ:
            m = (sub > 0) & (dbz >= d_)
            rgba[m, :3] = c
            rgba[m, 3] = 55 if d_ < 30 else 140
        im = Image.fromarray(rgba[::-1], "RGBA")
        (x0, y0), (x1, y1) = vw.P(b[0] + i0 * res, b[1] + j1 * res), vw.P(b[0] + i1 * res, b[1] + j0 * res)
        w, h = max(1, int(round(x1 - x0))), max(1, int(round(y1 - y0)))
        if w * h > 25_000_000:
            self._wx_key, self._wx = key, None
            return None
        im = im.resize((w, h), Image.BILINEAR)
        self._wx_key, self._wx = key, (im, (int(round(x0)), int(round(y0))))
        return self._wx

    # ---------------------------------------------------------------- one picture
    def layer(self, vw: View, fr: dict, trail: dict, routes: dict, wx_meta, overlay: list, captions: list,
              blink: bool) -> Image.Image:
        img = self.map_layer(vw).copy()
        if wx_meta is not None and (vw.wx[0] if vw.wx else "cappi") != "off":
            w = self.weather(vw, wx_meta)
            if w:
                img.paste(w[0], w[1], w[0])
        d = ImageDraw.Draw(img)
        k, wp = vw.k, self.wp
        f9, f10, f11 = font(9.5 * k), font(10.5 * k), font(11 * k)
        if vw.on("areas"):
            for a in self.areas.values():
                active = a["id"] in fr.get("areas", [])
                col = AREA_COL.get(a["kind"], (200, 200, 60))
                c = col if active else tuple(int(v * 0.55) for v in col)
                sh = a["shape"]
                if sh["type"] == "circle":
                    (cx, cy), r = vw.P(*sh["center"]), sh["radius_nm"] * vw.s * k
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=2 if active else 1)
                    lx, ly = vw.P(sh["center"][0] - sh["radius_nm"] * 0.6, sh["center"][1])
                else:
                    pts = [vw.P(*p) for p in sh["points"]]
                    (d.line if active else lambda p, **kw: dashed(d, p, kw["fill"], 1, 6 * k, 5 * k))(pts + pts[:1], fill=c, width=2)
                    xs, ys = [p[0] for p in sh["points"]], [p[1] for p in sh["points"]]
                    lx, ly = vw.P(min(xs) + 1, (min(ys) + max(ys)) / 2)
                text(img, (lx, ly - 8 * k), a.get("designator", a["id"]), c, f10)
                d.text((lx, ly + 4 * k), f"{int(a.get('lower_fl', 0)):03d}-{a.get('upper_fl', '')} {'ACT' if active else 'off'}",
                       fill=c, font=f10)
        acs = fr.get("ac", [])
        if vw.on("routes"):
            for r in acs:
                rt = [n for n in routes.get(r[0], []) if n in wp]
                if len(rt) > 1:
                    d.line([vw.P(*wp[n]) for n in rt], fill=(36, 57, 77), width=1)
                if not r[8] and len(r) > 17 and r[17] in wp:
                    dashed(d, [vw.P(r[1], r[2]), vw.P(*wp[r[17]])], (36, 57, 77), 1, 2 * k, 4 * k)
        los = {cs for p in fr.get("los", []) for cs in p}
        sel = vw.v.get("sel")
        for r in acs:
            cs, x, y, fl, cfl, gs, trk, vs, onf, asm, ent = r[:11]
            typ, nav, nxt, ahdg, spd, dest, entry, form, mil = (r[11:20] + [None] * 9)[:9]
            calling = onf and not asm
            col = COL["los"] if cs in los else COL["sel"] if cs == sel else \
                (COL["white"] if blink else COL["calling2"]) if calling else COL["assumed"] if onf else COL["other"]
            px, py = vw.P(x, y)
            if not (-200 < px < self.W + 200 and -200 < py < self.H + 200):
                continue
            if vw.on("trails"):
                dim = tuple(int(c * 0.55) for c in col)
                for (tx, ty) in trail.get(cs, []):
                    qx, qy = vw.P(tx, ty)
                    d.rectangle([qx - 1.2 * k, qy - 1.2 * k, qx + 1.2 * k, qy + 1.2 * k], fill=dim)
            a = math.radians(trk)
            vx, vy = vw.P(x + math.sin(a) * gs / 30, y + math.cos(a) * gs / 30)
            d.line([(px, py), (vx, vy)], fill=col, width=max(1, round(1.2 * k)))
            if cs in los:
                rr = 3.5 * vw.s * k
                d.ellipse([px - rr, py - rr, px + rr, py + rr], outline=tuple(int(c * 0.4) for c in col))
            h = 3.5 * k
            d.rectangle([px - h, py - h, px + h, py + h], outline=col, width=max(1, round(1.5 * k)))
            # data block, as on the controller's seat
            e = ent if isinstance(ent, list) else [None] * 5
            ecfl, espd, ehdg, edct, enote = (e + [None] * 5)[:5]
            shown_cfl = ecfl if ecfl is not None else cfl
            arrow = "↑" if vs > 0 else "↓" if vs < 0 else " "
            l1 = cs + (f" x{form}" if form else "") + ("  CALL" if calling else "")
            l2 = f"{fl:03d}{arrow}{shown_cfl if shown_cfl != fl else ''} {typ or ''}{' MIL' if mil else ''}"
            sfl = f" S{cfl}" if ecfl is not None and ecfl != cfl else ""
            l3 = f"{gs}" + (f" {espd}" if espd else "") + (f" H{int(ehdg):03d}" if ehdg else f" >{edct}" if edct else f" >{nxt or ''}")
            l4 = (f"→{dest}" if dest and dest != "ZZZZ" else "") if onf else f"prev sector → {entry or ''}"
            if enote:
                l4 = (l4 + " " if l4 else "") + str(enote)
            o = vw.lo.get(cs) or [10, -14]
            bx, by = px + o[0] * k, py + o[1] * k
            if cs in vw.lo:
                d.line([(px, py), (bx + (60 * k if o[0] < 0 else -2 * k), by + (26 * k if o[1] < 0 else -10 * k))],
                       fill=tuple(int(c * 0.7) for c in col))
            top = by - 10 * k
            text(img, (bx, top), l1, col, f11)
            text(img, (bx, top + 12 * k), l2, col, f11)
            if sfl:
                text(img, (bx + d.textlength(l2, font=f11), top + 12 * k), sfl, COL["amber"], f11)
            text(img, (bx, top + 24 * k), l3, col, f11)
            if l4:
                text(img, (bx, top + 36 * k), l4, tuple(int(c * 0.8) for c in col), f11)
        # range and bearing lines
        by_cs = {r[0]: r for r in acs}
        for pair in vw.v.get("rbl") or []:
            A, B = by_cs.get(pair[0]), by_cs.get(pair[1])
            if not A or not B:
                continue
            t, dd, rng, brg = cpa(A, B)
            (ax, ay), (bx2, by2) = vw.P(A[1], A[2]), vw.P(B[1], B[2])
            c = (215, 155, 255)
            d.line([(ax, ay), (bx2, by2)], fill=c, width=max(1, round(1.3 * k)))
            mx, my = (ax + bx2) / 2, (ay + by2) / 2
            t1 = f"{int(round(brg)):03d}° {rng:.1f} NM  ΔFL {abs(A[3] - B[3])}"
            t2 = f"CPA {dd:.1f} NM in {int(t // 60):02d}:{int(t % 60):02d}" if t > 0 else "diverging"
            d.rectangle([mx - 4 * k, my - 12 * k, mx + max(d.textlength(t1, font=f10), d.textlength(t2, font=f10)) + 4 * k,
                         my + 16 * k], fill=(11, 16, 22))
            text(img, (mx, my - 10 * k), t1, c, f10)
            text(img, (mx, my + 3 * k), t2, c, f10)
        self.overlays(img, vw, overlay)
        self.caption_box(img, captions)
        return img

    # ---------------------------------------------------------------- the controller's on-screen panels
    def overlays(self, img: Image.Image, vw: View, items: list) -> None:
        if not items:
            return
        d = ImageDraw.Draw(img, "RGBA")              # translucent fills blend onto the picture
        k = vw.k
        for it in items:
            try:
                x, y, w, h, typ, txt, fg, bg, bd, fs, flags = (list(it) + [None] * 11)[:11]
                x0, y0 = vw.S(x, y)
                x1, y1 = vw.S(x + w, y + h)
            except (TypeError, ValueError):
                continue
            fgc, bgc, bdc = hexc(fg), hexc(bg, (0, 0, 0, 0)), hexc(bd, (0, 0, 0, 0))
            flags = flags or ""
            fs = fs or 12
            f = font(fs * k, mono="m" in flags, bold="b" in flags)
            if typ in ("p", "b", "i") and (bgc[3] > 0 or bdc[3] > 0) and x1 > x0 and y1 > y0:
                d.rounded_rectangle([x0, y0, x1, y1], radius=3 * k, fill=bgc if bgc[3] else None,
                                    outline=bdc if bdc[3] else None)
            if typ == "x":
                d.rectangle([x0, y0, x1, y1], outline=(150, 160, 170, 255), fill=(80, 140, 230, 255) if "c" in flags else None)
                continue
            txt = clean(txt or "")
            if not txt:
                continue
            col = (fgc[0], fgc[1], fgc[2], 140) if "h" in flags else fgc       # h = placeholder
            lines = [ln for ln in txt.split("\n") if ln.strip()] or [txt]
            if typ in ("b", "i"):
                tw = max(d.textlength(t, font=f) for t in lines)
                tx = x0 + (6 * k if typ == "i" else max(2 * k, (x1 - x0 - tw) / 2))
                ty = y0 + (y1 - y0 - len(lines) * fs * k * 1.2) / 2
            else:
                tx, ty = x0, y0 + max(0, (y1 - y0 - len(lines) * fs * k * 1.25) / 2)
            for n, t in enumerate(lines):
                text(img, (tx, ty + n * fs * k * 1.25), t, col, f)

    def caption_box(self, img: Image.Image, caps: list) -> None:
        if not caps:
            return
        d = ImageDraw.Draw(img, "RGBA")
        f = font(13, mono=False)
        lh, pad = 19, 8
        w = max(d.textlength(c[1], font=f) for c in caps) + 2 * pad + 70
        h = lh * len(caps) + 2 * pad
        x0, y0 = 12, self.H - h - 40
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=5, fill=(10, 14, 20, 215), outline=(60, 90, 120, 255))
        for n, (stamp, line, alpha) in enumerate(caps):
            a = int(255 * alpha)
            text(img, (x0 + pad, y0 + pad + n * lh), stamp, (120, 140, 160, a), f)
            text(img, (x0 + pad + 70, y0 + pad + n * lh), line, (235, 240, 245, a), f)


def cursor(d: ImageDraw.ImageDraw, x: float, y: float, s: float = 1.0):
    pts = [(0, 0), (0, 17), (4.5, 13), (7.5, 20), (10.5, 18.8), (7.5, 12), (12.5, 12)]
    poly = [(x + px * s, y + py * s) for px, py in pts]
    d.polygon(poly, fill=(255, 255, 255), outline=(0, 0, 0))


def _entry_text(cs: str, e: dict) -> str:
    if not e:
        return f"{cs}: label cleared"
    parts = []
    if e.get("cfl") is not None:
        parts.append(f"CFL {e['cfl']}")
    if e.get("speed"):
        parts.append(f"SPD {e['speed']}")
    if e.get("heading"):
        parts.append(f"HDG {int(e['heading']):03d}")
    if e.get("direct"):
        parts.append(f"DCT {e['direct']}")
    if e.get("note"):
        parts.append(f"note “{e['note']}”")
    return f"{cs}: " + " · ".join(parts)


def render_controller(eid: str, user_id, rid: Optional[int] = None) -> dict:
    dr = folder(eid)
    with open(os.path.join(dr, "static.json")) as f:
        static = json.load(f)
    frames = _load_jsonl(os.path.join(dr, "frames.jsonl"))
    events = _load_jsonl(os.path.join(dr, "events.jsonl"))
    wxs = _load_jsonl(os.path.join(dr, "weather.jsonl"))
    ui = [r for r in _load_jsonl(os.path.join(dr, "ui.jsonl")) if user_id is None or r.get("u") == user_id]
    ui.sort(key=lambda r: r["w"])
    views = [(r["w"], r["d"]) for r in ui if r["k"] == "v" and isinstance(r["d"], dict)]
    if len(frames) < 2 or not views:
        from .recorder import render
        return render(eid)
    overlays = [(r["w"], r["d"]) for r in ui if r["k"] == "o"]
    mouse = [(r["w"], r["d"]) for r in ui if r["k"] == "m"]
    clicks = [(r["w"], r["d"]) for r in ui if r["k"] in ("c", "d") and isinstance(r["d"], dict)]
    dbl = {i for i, r in enumerate([r for r in ui if r["k"] in ("c", "d") and isinstance(r["d"], dict)]) if r["k"] == "d"}
    myconns = {r["c"] for r in ui} | {e["conn"] for e in events if e["type"] == "join" and e.get("user_id") == user_id
                                       and e.get("seat") == "atc"}
    # telephone calls the controller was on (for the audio) and talk events (for the captions)
    calls, open_calls = [], {}
    for e in events:
        if e["type"] != "call":
            continue
        c = open_calls.setdefault(e["call"], {"unit": e.get("unit"), "parties": set(), "t0": None, "t1": None})
        for kk in ("caller", "answerer"):
            if e.get(kk) is not None:
                c["parties"].add(e[kk])
        if e["state"] == "active":
            c["t0"] = e["wall"]
        if e["state"] == "ended":
            c["t1"] = e["wall"]
            if c["t0"] is not None:
                calls.append(c)
            open_calls.pop(e["call"], None)
    for c in open_calls.values():
        if c["t0"] is not None:
            c["t1"] = 1e12
            calls.append(c)

    def keep(wall, chan, conn):
        if chan == 0 or conn in myconns:
            return True
        return any(c["t0"] <= wall <= c["t1"] and conn in c["parties"] and c["parties"] & myconns for c in calls)

    first_ui, last_ui = ui[0]["w"], ui[-1]["w"]
    t0 = max(frames[0]["w"], first_ui - 1)
    t1 = min(frames[-1]["w"], last_ui + 3)
    if t1 - t0 < 2:
        t1 = frames[-1]["w"]
    # video size from the controller's (largest) scope
    vmax = max((v for _, v in views), key=lambda v: (v.get("w") or 0) * (v.get("h") or 0))
    k0 = min(1.0, MAXW / max(1, vmax.get("w") or 1280))
    W = int(round((vmax.get("w") or 1280) * k0 / 2)) * 2
    H = int(round((vmax.get("h") or 720) * k0 / 2)) * 2
    nfr = int((t1 - t0) * FPS) + 1
    pcm = mix_audio(os.path.join(dr, "audio.bin"), t0, t1, keep)[: int(nfr / FPS * RATE) + RATE // FPS]
    wav = os.path.join(dr, f"audio_{rid or user_id}.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    out = os.path.join(dr, f"controller_{user_id}.mp4" if user_id is not None else "controller.mp4")
    tmp = out + ".part.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "pipe:0", "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
           "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k",
           "-shortest", "-movflags", "+faststart", tmp]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    R = Renderer(static, W, H, dr)

    # captions: the controller's entries and assumptions, toolbar / panel buttons clicked, own transmissions, calls
    caps: List[tuple] = []
    for fr in frames:
        for a in fr.get("acts", []):
            if a["type"] == "assumed":
                caps.append((fr["w"], fr["t"], f"ASSUME {a['callsign']}"))
            elif a["type"] == "atc_entry":
                caps.append((fr["w"], fr["t"], _entry_text(a["callsign"], a.get("entry") or {})))
    fr_walls = [f["w"] for f in frames]

    def sim_at(wall):
        j = max(0, bisect.bisect_right(fr_walls, wall) - 1)
        return frames[j]["t"]
    for wall, c in clicks:
        what = c.get("what") or ""
        if what and not what.startswith(("label ", "scope")) and len(what) < 40 and not what.startswith("field"):
            caps.append((wall, sim_at(wall), f"click “{what}”"))
    for e in events:
        if e["type"] == "ptt" and e.get("conn") in myconns and e.get("on"):
            caps.append((e["wall"], sim_at(e["wall"]), "transmits on the frequency"))
        if e["type"] == "call" and (e.get("caller") in myconns or e.get("answerer") in myconns):
            st = {"ringing": "calls", "active": "on the line with", "ended": "hangs up"}.get(e["state"], e["state"])
            caps.append((e["wall"], sim_at(e["wall"]), f"telephone: {st} {e.get('unit')}"))
        elif e["type"] == "call" and e["state"] == "ringing" and e.get("direction") == "unit_to_atc":
            caps.append((e["wall"], sim_at(e["wall"]), f"telephone: {e.get('unit')} calling"))
    caps.sort()
    cap_walls = [c[0] for c in caps]

    v_walls = [w for w, _ in views]
    o_walls = [w for w, _ in overlays]
    m_walls = [w for w, _ in mouse]
    c_walls = [w for w, _ in clicks]
    wx_walls = [m["w"] for m in wxs]
    routes: Dict[str, list] = {}
    route_upto = -1
    view_cache: Dict[int, View] = {}
    last_key, last_img = None, None
    try:
        for i in range(nfr):
            wall = t0 + i / FPS
            j = max(0, bisect.bisect_right(fr_walls, wall) - 1)
            while route_upto < j:
                route_upto += 1
                routes.update(frames[route_upto].get("rt") or {})
            vi = max(0, bisect.bisect_right(v_walls, wall) - 1)
            if vi not in view_cache:
                view_cache.clear()
                view_cache[vi] = View(views[vi][1], W, H)
            vw = view_cache[vi]
            oi = bisect.bisect_right(o_walls, wall) - 1
            wi = bisect.bisect_right(wx_walls, wall) - 1
            ci = bisect.bisect_right(cap_walls, wall)
            cap_now = []
            for c in caps[max(0, ci - 6):ci]:
                age = wall - c[0]
                if age < 14:
                    ts = int(c[1])
                    cap_now.append((f"T+{ts // 60:02d}:{ts % 60:02d}", c[2],
                                    1.0 if age < 10 else round(max(0.0, (14 - age) / 4) * 4) / 4))
            calling = any(r[8] and not r[9] for r in frames[j]["ac"])
            blink = calling and int(wall * 2) % 2 == 0
            key = (j, vi, oi, wi, blink, tuple((c[1], round(c[2], 1)) for c in cap_now))
            if key != last_key:
                trail = {}
                for back in range(10, 121, 10):
                    if j - back < 0:
                        break
                    for r in frames[j - back]["ac"]:
                        trail.setdefault(r[0], []).append((r[1], r[2]))
                last_img = R.layer(vw, frames[j], trail, routes, wxs[wi] if wi >= 0 else None,
                                   overlays[oi][1] if oi >= 0 else [], cap_now, blink)
                last_key = key
            img = last_img.copy()
            d = ImageDraw.Draw(img)
            # clicks: a ring that grows for half a second (double ring for a double-click)
            cj = bisect.bisect_right(c_walls, wall)
            for n in range(max(0, cj - 4), cj):
                cw, c = clicks[n]
                age = wall - cw
                if 0 <= age < 0.6:
                    x, y = vw.S(c.get("x", 0), c.get("y", 0))
                    col = (255, 216, 74) if c.get("b", 0) == 0 else (120, 180, 255)
                    for rr in ([4 + 30 * age] + ([10 + 30 * age] if n in dbl else [])):
                        d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=col, width=2)
            mi = bisect.bisect_right(m_walls, wall) - 1
            if mi >= 0 and mouse[mi][1] and wall - m_walls[mi] < 30:
                m0 = mouse[mi][1]
                if mi + 1 < len(mouse) and mouse[mi + 1][1] and m_walls[mi + 1] - m_walls[mi] < 0.3:
                    m1, f = mouse[mi + 1][1], (wall - m_walls[mi]) / max(1e-3, m_walls[mi + 1] - m_walls[mi])
                    m0 = [m0[0] + (m1[0] - m0[0]) * f, m0[1] + (m1[1] - m0[1]) * f]
                x, y = vw.S(m0[0], m0[1])
                cursor(d, x, y, max(0.8, vw.k))
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
