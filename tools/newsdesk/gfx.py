"""Graphics for the newsroom's articles: maps and charts as SVG (the site) and PNG (the newsletter e-mails).

    python tools/newsdesk/gfx.py map   spec.json out.svg [--png out.png]
    python tools/newsdesk/gfx.py chart spec.json out.svg [--png out.png]

Map spec (coordinates are [lon, lat]):
    {"title": "...", "subtitle": "...", "region": "morocco" | "maghreb" | "west-med" | "europe" | "mediterranean" |
     "atlantic" | "africa" | "middle-east" | "world"   (or "bbox": [lon_min, lat_min, lon_max, lat_max]),
     "highlight": ["MAR", "ESP"],                       countries filled in the accent colour (ISO alpha-3)
     "points": [{"lon": -7.59, "lat": 33.37, "label": "Casablanca (GMMN)", "kind": "airport" | "event" | "point"}],
     "lines": [{"coords": [[-7.59, 33.37], [2.55, 49.0]], "label": "...", "style": "route" | "dashed"}],
     "areas": [{"coords": [[...], ...], "label": "..."}],
     "source": "Natural Earth; positions from AIP Maroc"}
Chart spec:
    {"type": "bar" | "hbar" | "line", "title": "...", "subtitle": "...", "labels": ["2023", "2024", "2025"],
     "series": [{"name": "Passengers (millions)", "values": [27.1, 32.7, 36.3]}], "unit": "M", "source": "ONDA"}

Both: "lang": "fr" | "ar" draws the figure for the French or Arabic version: "Source" and the country names in that
language (write the title, subtitle, labels and series names in it too); "country_names": {"DZA": "..."} overrides
a country's name. Arabic text is laid out right to left by the browser that draws the PNG.

Every number on a chart must come from the article's sources; the source line says where from.
"""
from __future__ import annotations

import html
import json
import math
import os
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
INK, DIM, GRID, LAND, LAND2, SEA, ACCENT, BLUE = "#1d2733", "#6b7885", "#e3e8ed", "#e9edf1", "#d6dde4", "#f7f9fb", "#c2185b", "#2f6fa8"
PALETTE = ["#2f6fa8", "#c2185b", "#3f9b6e", "#d08a1e", "#6d5bb5", "#4a8fa3"]
FONT = "font-family:Archivo,Helvetica,Arial,'Noto Sans Arabic','Noto Naskh Arabic','DejaVu Sans',Tahoma,sans-serif"
REGIONS = {
    "morocco": [-17.5, 20.5, -0.5, 36.5], "maghreb": [-18, 18, 12, 38.5], "west-med": [-12, 27, 16, 46],
    "europe": [-25, 34, 42, 71], "mediterranean": [-10, 28, 37, 47], "atlantic": [-80, 5, 0, 60],
    "africa": [-20, -36, 53, 38], "middle-east": [25, 10, 63, 42], "world": [-170, -58, 180, 80],
}
_world = None


def _countries():
    global _world
    if _world is None:
        _world = json.load(open(os.path.join(HERE, "world.json"), encoding="utf-8"))["countries"]
    return _world


def e(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _header(w: int, title: str, subtitle: str) -> str:
    out = f'<rect x="0" y="0" width="{w}" height="100%" fill="#ffffff"/>'
    out += f'<text x="24" y="38" style="{FONT};font-size:21px;font-weight:700" fill="{INK}">{e(title)}</text>'
    if subtitle:
        out += f'<text x="24" y="62" style="{FONT};font-size:14px" fill="{DIM}">{e(subtitle)}</text>'
    return out


SOURCE_WORD = {"en": "Source: ", "fr": "Source : ", "ar": "المصدر: "}
# country names on the maps, in French and Arabic (the countries the newsroom's maps show; others stay in English)
COUNTRY = {
    "MAR": ("Maroc", "المغرب"), "DZA": ("Algérie", "الجزائر"), "TUN": ("Tunisie", "تونس"), "LBY": ("Libye", "ليبيا"),
    "EGY": ("Égypte", "مصر"), "MRT": ("Mauritanie", "موريتانيا"), "MLI": ("Mali", "مالي"), "SEN": ("Sénégal", "السنغال"),
    "NER": ("Niger", "النيجر"), "TCD": ("Tchad", "تشاد"), "SDN": ("Soudan", "السودان"), "NGA": ("Nigeria", "نيجيريا"),
    "CIV": ("Côte d'Ivoire", "كوت ديفوار"), "GHA": ("Ghana", "غانا"), "GIN": ("Guinée", "غينيا"), "BFA": ("Burkina Faso", "بوركينا فاسو"),
    "CMR": ("Cameroun", "الكاميرون"), "GAB": ("Gabon", "الغابون"), "COD": ("RD Congo", "الكونغو الديمقراطية"),
    "ESP": ("Espagne", "إسبانيا"), "PRT": ("Portugal", "البرتغال"), "FRA": ("France", "فرنسا"), "ITA": ("Italie", "إيطاليا"),
    "DEU": ("Allemagne", "ألمانيا"), "GBR": ("Royaume-Uni", "المملكة المتحدة"), "IRL": ("Irlande", "أيرلندا"),
    "BEL": ("Belgique", "بلجيكا"), "NLD": ("Pays-Bas", "هولندا"), "CHE": ("Suisse", "سويسرا"), "AUT": ("Autriche", "النمسا"),
    "POL": ("Pologne", "بولندا"), "CZE": ("Tchéquie", "التشيك"), "HUN": ("Hongrie", "المجر"), "ROU": ("Roumanie", "رومانيا"),
    "GRC": ("Grèce", "اليونان"), "TUR": ("Turquie", "تركيا"), "NOR": ("Norvège", "النرويج"), "SWE": ("Suède", "السويد"),
    "FIN": ("Finlande", "فنلندا"), "DNK": ("Danemark", "الدنمارك"), "UKR": ("Ukraine", "أوكرانيا"), "RUS": ("Russie", "روسيا"),
    "SAU": ("Arabie saoudite", "السعودية"), "ARE": ("Émirats arabes unis", "الإمارات"), "QAT": ("Qatar", "قطر"),
    "IRN": ("Iran", "إيران"), "IRQ": ("Irak", "العراق"), "SYR": ("Syrie", "سوريا"), "JOR": ("Jordanie", "الأردن"),
    "ISR": ("Israël", "إسرائيل"), "LBN": ("Liban", "لبنان"), "OMN": ("Oman", "عمان"), "YEM": ("Yémen", "اليمن"),
    "USA": ("États-Unis", "الولايات المتحدة"), "CAN": ("Canada", "كندا"), "MEX": ("Mexique", "المكسيك"),
    "BRA": ("Brésil", "البرازيل"), "ARG": ("Argentine", "الأرجنتين"), "CHL": ("Chili", "تشيلي"), "COL": ("Colombie", "كولومبيا"),
    "PER": ("Pérou", "البيرو"), "VEN": ("Venezuela", "فنزويلا"), "CHN": ("Chine", "الصين"), "JPN": ("Japon", "اليابان"),
    "IND": ("Inde", "الهند"), "AUS": ("Australie", "أستراليا"), "ZAF": ("Afrique du Sud", "جنوب إفريقيا"),
    "ETH": ("Éthiopie", "إثيوبيا"), "KEN": ("Kenya", "كينيا"), "ISL": ("Islande", "آيسلندا"),
}


def country_name(c: dict, lang: str, own: Optional[dict] = None) -> str:
    if own and c.get("a3") in own:
        return own[c["a3"]]
    if lang in ("fr", "ar") and c.get("a3") in COUNTRY:
        return COUNTRY[c["a3"]][0 if lang == "fr" else 1]
    return c["name"]


def _footer(w: int, h: int, source: str, lang: str = "en") -> str:
    return (f'<text x="24" y="{h - 14}" style="{FONT};font-size:11.5px" fill="{DIM}">{e((SOURCE_WORD.get(lang, "Source: ") + source) if source else "")}</text>'
            f'<text x="{w - 24}" y="{h - 14}" text-anchor="end" style="{FONT};font-size:11.5px;font-weight:700" fill="{INK}">ATC <tspan fill="{ACCENT}">SIM</tspan></text>')


# ------------------------------------------------------------------ maps
def map_svg(spec: dict) -> str:
    W, H = int(spec.get("width", 960)), int(spec.get("height", 600))
    top, bottom, side = (78 if spec.get("subtitle") else 58), 34, 18
    box = spec.get("bbox") or REGIONS.get(spec.get("region", "morocco"), REGIONS["morocco"])
    lon0, lat0, lon1, lat1 = map(float, box)
    k = math.cos(math.radians((lat0 + lat1) / 2))            # equirectangular, true scale at the middle latitude
    pw, ph = W - 2 * side, H - top - bottom
    sx = pw / ((lon1 - lon0) * k)
    sy = ph / (lat1 - lat0)
    s = min(sx, sy)
    cx, cy = (lon0 + lon1) / 2, (lat0 + lat1) / 2
    ox, oy = side + pw / 2, top + ph / 2

    def P(lon, lat):
        return ox + (lon - cx) * k * s, oy - (lat - cy) * s
    # what is visible (a margin): the view's edges
    vlon0, vlon1 = cx - (pw / 2) / (k * s), cx + (pw / 2) / (k * s)
    vlat0, vlat1 = cy - (ph / 2) / s, cy + (ph / 2) / s
    hl = set(spec.get("highlight") or [])
    clip = f'<clipPath id="mapclip"><rect x="{side}" y="{top}" width="{pw}" height="{ph}" rx="6"/></clipPath>'
    body = f'<rect x="{side}" y="{top}" width="{pw}" height="{ph}" rx="6" fill="{SEA}" stroke="{GRID}"/>'
    land = ""
    labels = []
    span = vlon1 - vlon0
    for c in _countries():
        paths = []
        vis = False
        for ring in c["rings"]:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            if max(xs) < vlon0 - 2 or min(xs) > vlon1 + 2 or max(ys) < vlat0 - 2 or min(ys) > vlat1 + 2:
                continue
            vis = True
            mx, my = 3.0, 3.0                      # far outside the view: clamped (the file stays small, the shape the same inside)
            pts = [P(min(max(x, vlon0 - mx), vlon1 + mx), min(max(y, vlat0 - my), vlat1 + my)) for x, y in ring]
            paths.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z")
        if not vis:
            continue
        on = c["a3"] in hl
        land += (f'<path d="{" ".join(paths)}" fill="{"#f6d5e2" if on else LAND}" stroke="{"#c2185b" if on else "#c3ccd5"}" '
                 f'stroke-width="{1.2 if on else 0.6}" stroke-linejoin="round"/>')
        lx, ly = c["label"]
        rank_ok = (c.get("rank") or 9) <= (2 if span > 120 else 3 if span > 50 else 5 if span > 20 else 7)
        if (on or rank_ok) and vlon0 + 1 < lx < vlon1 - 1 and vlat0 + 0.5 < ly < vlat1 - 0.5:
            # its size on the map (the largest ring): a name is written only where it fits
            big = max(c["rings"], key=lambda r: (max(p[0] for p in r) - min(p[0] for p in r)) * (max(p[1] for p in r) - min(p[1] for p in r)))
            wpx = (max(p[0] for p in big) - min(p[0] for p in big)) * k * s
            labels.append((on, country_name(c, spec.get("lang", "en"), spec.get("country_names")), P(lx, ly), wpx, c.get("rank") or 9))
    body += f'<g clip-path="url(#mapclip)">{land}'
    for a in spec.get("areas") or []:
        pts = [P(x, y) for x, y in a["coords"]]
        body += (f'<path d="M{"L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}Z" fill="{ACCENT}" fill-opacity=".12" '
                 f'stroke="{ACCENT}" stroke-width="1.4" stroke-dasharray="5 3"/>')
    for ln in spec.get("lines") or []:
        pts = [P(x, y) for x, y in ln["coords"]]
        dash = ' stroke-dasharray="7 5"' if ln.get("style") == "dashed" else ""
        body += (f'<path d="M{"L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{BLUE}" '
                 f'stroke-width="2.2" stroke-linecap="round"{dash}/>')
    body += "</g>"
    # labels without overlaps: the points and their names first (they are the story), then the highlighted countries,
    # then the others by importance; a country name only where it fits inside the country
    taken = []

    def free(b):
        return all(b[2] < t[0] or b[0] > t[2] or b[3] < t[1] or b[1] > t[3] for t in taken)
    for pt in spec.get("points") or []:
        x, y = P(pt["lon"], pt["lat"])
        w_ = 7.2 * len(pt.get("label") or "")
        right = x <= W - 200
        taken.append((x - 12, y - 12, x + 12, y + 12))
        if pt.get("label"):
            taken.append((x + 6, y - 10, x + 12 + w_, y + 8) if right else (x - 12 - w_, y - 10, x - 6, y + 8))
    for on, name, (x, y), wpx, rank in sorted(labels, key=lambda l: (not l[0], l[4], -l[3])):
        size = 12.5 if on else 11
        tw = len(name) * size * 0.68 + 4
        if not on and tw > wpx * 1.1:
            continue
        b = (x - tw / 2, y - size, x + tw / 2, y + 3)
        if not free(b):
            continue
        taken.append(b)
        body += (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" style="{FONT};font-size:{size}px;'
                 f'font-weight:{700 if on else 500};letter-spacing:{"0" if spec.get("lang") == "ar" else ".04em"}" fill="{ACCENT if on else "#8a96a2"}">{e(name.upper() if spec.get("lang", "en") != "ar" else name)}</text>')
    for ln in spec.get("lines") or []:
        if ln.get("label"):
            (x0, y0), (x1, y1) = P(*ln["coords"][0]), P(*ln["coords"][-1])
            body += _halo_text((x0 + x1) / 2, (y0 + y1) / 2 - 8, ln["label"], BLUE, 12, "middle")
    for a in spec.get("areas") or []:
        if a.get("label"):
            pts = [P(x, y) for x, y in a["coords"]]
            body += _halo_text(sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), a["label"], ACCENT, 12, "middle")
    for pt in spec.get("points") or []:
        x, y = P(pt["lon"], pt["lat"])
        kind = pt.get("kind", "point")
        if kind == "airport":
            body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#fff" stroke="{BLUE}" stroke-width="2.2"/>'
        elif kind == "event":
            body += (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{ACCENT}" fill-opacity=".18"/>'
                     f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{ACCENT}"/>')
        else:
            body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{INK}"/>'
        if pt.get("label"):
            anchor = "end" if x > W - 200 else "start"
            body += _halo_text(x + (-9 if anchor == "end" else 9), y + 4, pt["label"], INK, 12.5, anchor, 600)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
           f'aria-label="{e(spec.get("title", "map"))}"><defs>{clip}</defs>{_header(W, spec.get("title", ""), spec.get("subtitle", ""))}'
           f'{body}{_footer(W, H, spec.get("source") or "Natural Earth", spec.get("lang", "en"))}</svg>')
    return svg


def _halo_text(x, y, t, col, size, anchor="start", weight=500) -> str:
    st = f'{FONT};font-size:{size}px;font-weight:{weight}'
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" style="{st}" fill="#fff" stroke="#fff" stroke-width="4" '
            f'stroke-linejoin="round">{e(t)}</text><text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" style="{st}" fill="{col}">{e(t)}</text>')


# ------------------------------------------------------------------ charts
def _nice(lo: float, hi: float, n: int = 5) -> List[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=10 * mag)
    a = math.floor(lo / step) * step
    out = []
    v = a
    while v <= hi + step * 0.001:
        out.append(round(v, 10))
        v += step
    if out[-1] < hi:
        out.append(round(out[-1] + step, 10))
    return out


def _fmt(v: float, unit: str = "") -> str:
    s = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}"
    return s + (unit or "")


def chart_svg(spec: dict) -> str:
    W, H = int(spec.get("width", 960)), int(spec.get("height", 520))
    kind = spec.get("type", "bar")
    labels = [str(x) for x in spec.get("labels") or []]
    series = spec.get("series") or []
    unit = spec.get("unit", "")
    if not labels or not series or any(len(s_["values"]) != len(labels) for s_ in series):
        raise ValueError("labels and every series' values must have the same length")
    top = (84 if spec.get("subtitle") else 64) + (24 if len(series) > 1 else 0)
    bottom, left, right = 56, 70, 28
    vals = [v for s_ in series for v in s_["values"] if v is not None]
    lo = min(0.0, min(vals))
    ticks = _nice(lo, max(vals))
    vmin, vmax = ticks[0], ticks[-1]
    body = ""
    if len(series) > 1:                                      # legend
        x = 24
        for i, s_ in enumerate(series):
            body += (f'<rect x="{x}" y="{top - 26}" width="12" height="12" rx="2" fill="{PALETTE[i % len(PALETTE)]}"/>'
                     f'<text x="{x + 18}" y="{top - 16}" style="{FONT};font-size:12.5px" fill="{INK}">{e(s_["name"])}</text>')
            x += 34 + 7 * len(s_["name"])
    if kind == "hbar":
        left = 24 + min(260, 7.2 * max(len(l) for l in labels))
        pw, ph = W - left - right - 40, H - top - bottom
        X = lambda v: left + (v - vmin) / (vmax - vmin) * pw
        for t in ticks:
            body += (f'<line x1="{X(t):.1f}" y1="{top}" x2="{X(t):.1f}" y2="{top + ph}" stroke="{GRID}"/>'
                     f'<text x="{X(t):.1f}" y="{top + ph + 18}" text-anchor="middle" style="{FONT};font-size:11.5px" fill="{DIM}">{e(_fmt(t, unit))}</text>')
        n, m = len(labels), len(series)
        band = ph / n
        bh = min(28, band * 0.7 / m)
        for i, lab in enumerate(labels):
            y0 = top + i * band + (band - bh * m) / 2
            body += f'<text x="{left - 10}" y="{y0 + bh * m / 2 + 4:.1f}" text-anchor="end" style="{FONT};font-size:12.5px" fill="{INK}">{e(lab)}</text>'
            for j, s_ in enumerate(series):
                v = s_["values"][i]
                if v is None:
                    continue
                x0, x1 = X(max(vmin, 0)), X(v)
                body += (f'<rect x="{min(x0, x1):.1f}" y="{y0 + j * bh:.1f}" width="{abs(x1 - x0):.1f}" height="{bh - 3:.1f}" rx="2" fill="{PALETTE[j % len(PALETTE)]}"/>'
                         f'<text x="{x1 + 6:.1f}" y="{y0 + j * bh + bh / 2 + 2:.1f}" style="{FONT};font-size:11.5px;font-weight:600" fill="{INK}">{e(_fmt(v, unit))}</text>')
    else:
        pw, ph = W - left - right, H - top - bottom
        Y = lambda v: top + ph - (v - vmin) / (vmax - vmin) * ph
        for t in ticks:
            body += (f'<line x1="{left}" y1="{Y(t):.1f}" x2="{left + pw}" y2="{Y(t):.1f}" stroke="{GRID}"/>'
                     f'<text x="{left - 8}" y="{Y(t) + 4:.1f}" text-anchor="end" style="{FONT};font-size:11.5px" fill="{DIM}">{e(_fmt(t, unit))}</text>')
        n, m = len(labels), len(series)
        band = pw / n
        step = max(1, math.ceil(n / 14))
        for i, lab in enumerate(labels):
            if i % step == 0:
                body += f'<text x="{left + band * (i + .5):.1f}" y="{top + ph + 18}" text-anchor="middle" style="{FONT};font-size:12px" fill="{INK}">{e(lab)}</text>'
        if kind == "line":
            for j, s_ in enumerate(series):
                col = PALETTE[j % len(PALETTE)]
                pts = [(left + band * (i + .5), Y(v)) for i, v in enumerate(s_["values"]) if v is not None]
                body += f'<path d="M{"L".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{col}" stroke-width="2.6" stroke-linejoin="round"/>'
                for x, y in pts:
                    body += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="#fff" stroke="{col}" stroke-width="2"/>'
                x, y = pts[-1]
                body += f'<text x="{x:.1f}" y="{y - 10:.1f}" text-anchor="middle" style="{FONT};font-size:12px;font-weight:700" fill="{col}">{e(_fmt(s_["values"][-1], unit))}</text>'
        else:
            bw = min(56, band * 0.72 / m)
            for i in range(n):
                x0 = left + band * i + (band - bw * m) / 2
                for j, s_ in enumerate(series):
                    v = s_["values"][i]
                    if v is None:
                        continue
                    y1, y0 = Y(v), Y(max(vmin, 0))
                    body += (f'<rect x="{x0 + j * bw:.1f}" y="{min(y0, y1):.1f}" width="{bw - 3:.1f}" height="{abs(y0 - y1):.1f}" rx="2" fill="{PALETTE[j % len(PALETTE)]}"/>'
                             f'<text x="{x0 + j * bw + (bw - 3) / 2:.1f}" y="{min(y0, y1) - 6:.1f}" text-anchor="middle" style="{FONT};font-size:11.5px;font-weight:600" fill="{INK}">{e(_fmt(v, unit))}</text>')
        body += f'<line x1="{left}" y1="{Y(max(vmin, 0)):.1f}" x2="{left + pw}" y2="{Y(max(vmin, 0)):.1f}" stroke="#9aa6b2"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="{e(spec.get("title", "chart"))}">{_header(W, spec.get("title", ""), spec.get("subtitle", ""))}{body}'
            f'{_footer(W, H, spec.get("source", ""), spec.get("lang", "en"))}</svg>')


# ------------------------------------------------------------------ PNG and checks
UNSAFE = ("<script", "javascript:", "<foreignobject", "<iframe", "<object", "<embed", " onload", " onclick", " onerror",
          " onmouse", "xlink:href=\"http", "href=\"http")


def safe(svg: str) -> Optional[str]:
    """None when the SVG is safe to publish (no script, no event handler, nothing loaded from elsewhere), else why."""
    low = svg.lower()
    for u in UNSAFE:
        if u in low:
            return f"unsafe SVG: contains {u.strip()!r}"
    if not low.lstrip().startswith("<svg"):
        return "not an SVG"
    return None


def png(svg_path: str, png_path: str, scale: float = 2.0) -> None:
    """The PNG of an SVG (for the e-mails): drawn by the headless browser."""
    from playwright.sync_api import sync_playwright
    svg = open(svg_path, encoding="utf-8").read()
    import re
    w = int(float(re.search(r'width="([\d.]+)"', svg).group(1)))
    h = int(float(re.search(r'height="([\d.]+)"', svg).group(1)))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=scale)
        pg.set_content(f'<html><body style="margin:0;background:#fff">{svg}</body></html>')
        pg.wait_for_timeout(200)
        pg.locator("svg").first.screenshot(path=png_path)
        b.close()


def main(argv: List[str]) -> int:
    if len(argv) < 4 or argv[1] not in ("map", "chart"):
        print(__doc__)
        return 2
    spec = json.load(open(argv[2], encoding="utf-8"))
    svg = map_svg(spec) if argv[1] == "map" else chart_svg(spec)
    why = safe(svg)
    if why:
        print(why)
        return 1
    open(argv[3], "w", encoding="utf-8").write(svg)
    if "--png" in argv:
        png(argv[3], argv[argv.index("--png") + 1])
    print(f"wrote {argv[3]}" + (f" and {argv[argv.index('--png') + 1]}" if "--png" in argv else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
