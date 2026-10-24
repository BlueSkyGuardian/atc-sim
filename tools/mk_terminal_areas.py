"""Terminal airspaces (TMA, CTA, CTR, ATZ) of the Moroccan aerodromes into the airspace file ("terminal_areas"), from
the table of their AIP definitions (ENR 2.1 and AD 2.17: Morocco_TMA_CTA_CTR.xlsx, sheet "Airspace"). The lateral
limits are read from the AIP wording: circles ("25 NM radius centred on ADM VOR/DME ..."), lines between coordinates,
arcs (clockwise unless stated) "to" a point or "back to the point of origin", arcs "from A to B clockwise", two
circles joined by their tangents. Limits that follow a border or a coast are approximated (noted "approx").

  python tools/mk_terminal_areas.py Morocco_TMA_CTA_CTR.xlsx
"""
import json
import math
import os
import re
import sys

import openpyxl

P = os.path.join(os.path.dirname(__file__), "..", "atcsim", "data", "airspaces", "agadir_cta.json")
R_NM = 3440.065
COORD = re.compile(r"(\d{6}(?:\.\d+)?)\s*([NS])\s*(\d{7}(?:\.\d+)?)\s*([EW])")


def ll_of(m) -> tuple:
    la, hn, lo, he = m
    f = lambda t, w: float(t[:w]) + float(t[w:w + 2]) / 60 + float(t[w + 2:]) / 3600
    return (-1 if hn == "S" else 1) * f(la, 2), (-1 if he == "W" else 1) * f(lo, 3)


def dms(lat, lon) -> str:
    def one(v, w, p, n):
        a = abs(v); d = int(a); m = int((a - d) * 60); s = round(((a - d) * 60 - m) * 60, 1)
        if s >= 60:
            s -= 60; m += 1
        if m >= 60:
            m -= 60; d += 1
        return f"{d:0{w}d}{m:02d}{s:04.1f}{p if v >= 0 else n}"
    return f"{one(lat, 2, 'N', 'S')} {one(lon, 3, 'E', 'W')}"


def brg(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def dest(c, b, d) -> tuple:
    la1, lo1, bb, dd = math.radians(c[0]), math.radians(c[1]), math.radians(b), d / R_NM
    la2 = math.asin(math.sin(la1) * math.cos(dd) + math.cos(la1) * math.sin(dd) * math.cos(bb))
    lo2 = lo1 + math.atan2(math.sin(bb) * math.sin(dd) * math.cos(la1), math.cos(dd) - math.sin(la1) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def circle(c, r, n=72) -> list:
    return [dest(c, 360.0 * k / n, r) for k in range(n)]


def arc(c, r, a, b, cw=True) -> list:
    """Points of the arc from (the bearing of) a to b around c (both ends excluded)."""
    b0, b1 = brg(c, a), brg(c, b)
    span = (b1 - b0) % 360 if cw else -((b0 - b1) % 360)
    n = max(2, int(abs(span) / 4))
    return [dest(c, b0 + span * k / n, r) for k in range(1, n)]


d = json.loads(open(P).read())
pts = {**{k: v["coord"] for k, v in d["points"].items()},
       **{k: v["coord"] for k, v in d["external_points"]["points"].items()}}


def navaid(name) -> tuple:
    return ll_of(COORD.search(pts[name]).groups())


ARP = {a["id"]: a["coord"] for a in d.get("airports", [])}


def centre(txt, ad_icao):
    """The centre named after 'centred on': a coordinate, or a VOR/DME by name, or the aerodrome's ARP."""
    m = COORD.search(txt)
    if m:
        return ll_of(m.groups())
    for nm in re.findall(r"\b([A-Z]{2,3})\b", txt):
        if nm in pts:
            return navaid(nm)
    if "VOR/DME" in txt and ad_icao == "GMMZ":
        return navaid("OZT")
    if txt.strip().startswith("ARP") and ad_icao in ARP:
        return ll_of(COORD.search(ARP[ad_icao].replace(".", "")).groups()) if COORD.search(ARP[ad_icao].replace(".", "")) \
            else ll_of(COORD.search(ARP[ad_icao]).groups())
    raise ValueError(f"no centre in {txt!r}")


def parse(text, shape, icao) -> tuple:
    """(polygon as [(lat, lon)], approx?)"""
    t = " ".join(str(text).split())
    if icao == "GMFO" and "border" in t and "parallel" in t:
        # TMA Oujda: the border from 34 10N, the given points, the coast to the border, the border back
        poly = [(34.1667, -1.745), ll_of(("341000", "N", "0021630", "W")), ll_of(("341800", "N", "0023600", "W")),
                ll_of(("350516.40", "N", "0023600", "W")), (35.12, -2.35), (35.09, -2.21), (34.98, -1.95),
                (34.76, -1.75), (34.45, -1.68)]
        return poly, True
    if shape.startswith("Two circles"):
        cs = [ll_of(m.groups()) for m in COORD.finditer(t)]
        r = float(re.search(r"(\d+(?:\.\d+)?) NM", t).group(1))
        ring = [p for c in cs for p in circle(c, r, 72)]
        return _hull(ring), False
    if shape == "Circle" or (shape.startswith("Arc") and "bounded" in t and "from" not in t):
        r = float(re.search(r"(\d+(?:\.\d+)?) NM radius", t).group(1))
        c = centre(t[t.index("centred on"):], icao)
        return circle(c, r), shape != "Circle"
    if "[point not legible" in t:
        raise ValueError("a point is missing from the AIP text")
    # sequential: points, "arc [clockwise] R NM radius centred on C to P", "then arc R ... back to point of origin",
    # "Arc R NM radius centred on C from A to B [clockwise]", "then lines P - P"
    poly = []
    i = 0
    tok = re.compile(r"arc (?:(clockwise|anticlockwise|counter-clockwise) )?(\d+(?:\.\d+)?) NM radius (?:clockwise )?"
                     r"centred on (.*?)(?= from | to | back |, then|$)", re.I)
    while i < len(t):
        m_arc = tok.search(t, i)
        m_pt = COORD.search(t, i)
        if m_arc and (not m_pt or m_arc.start() < m_pt.start()):
            r = float(m_arc.group(2))
            c = centre(m_arc.group(3), icao)
            j = m_arc.end()
            rest = t[j:]
            cw = not (m_arc.group(1) or "").lower().startswith(("anti", "counter")) and "anticlockwise" not in rest[:60]
            mf = re.search(r"\bfrom\b", rest[:160])
            if mf and not poly:                       # "... from A to B [clockwise]" (after a bounding remark)
                cs = list(COORD.finditer(rest, mf.end()))[:2]
                a, b = [ll_of(m.groups()) for m in cs]
                poly += [a] + arc(c, r, a, b, cw) + [b]
                i = j + cs[1].end()
            elif rest.lstrip().startswith("back"):
                poly += arc(c, r, poly[-1], poly[0], cw)
                break
            else:                                   # "to P"
                mp = COORD.search(rest)
                b = ll_of(mp.groups())
                poly += arc(c, r, poly[-1], b, cw) + [b]
                i = j + mp.end()
            continue
        if m_pt:
            p = ll_of(m_pt.groups())
            if not poly or abs(p[0] - poly[-1][0]) + abs(p[1] - poly[-1][1]) > 1e-6:
                poly.append(p)
            i = m_pt.end()
            continue
        break
    if len(poly) > 2 and abs(poly[0][0] - poly[-1][0]) + abs(poly[0][1] - poly[-1][1]) < 1e-6:
        poly.pop()
    return poly, False


def _hull(ps):
    ps = sorted(set((round(a, 6), round(b, 6)) for a, b in ps))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in ps:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(ps):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def feet(v) -> int:
    s = str(v or "").upper()
    if not s or s.startswith(("SFC", "GND")):
        return 0
    m = re.search(r"FL\s*(\d+)", s)
    if m:
        return int(m.group(1)) * 100
    m = re.search(r"(\d+(?:\.\d+)?)\s*M\b", s)
    if m:
        return int(round(float(m.group(1)) * 3.28084 / 100.0) * 100)
    m = re.search(r"(\d+)\s*FT", s)
    if m:
        return int(m.group(1))
    return 0


wb = openpyxl.load_workbook(sys.argv[1], data_only=True)
ws = wb["Airspace"]
rows = list(ws.iter_rows(values_only=True))
head = rows[0]
out, skipped = [], []
for r in rows[1:]:
    if not r or not r[1] or str(r[1]).strip() in ("—", "-", ""):
        continue
    row = dict(zip(head, r))
    icao, name, typ = row["ICAO"].strip("*"), row["Airspace"], row["Type"]
    try:
        poly, approx = parse(row["Lateral limits (coordinates)"], str(row["Shape"]), icao)
    except Exception as e:                          # noqa: BLE001
        skipped.append(f"{name}: {e}")
        continue
    if len(poly) < 3:
        skipped.append(f"{name}: fewer than 3 points")
        continue
    out.append({"id": re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_"), "name": name, "type": typ, "aerodrome": icao,
                "class": row["Class"], "unit": row["ATS unit"], "lower_ft": feet(row["Lower limit"]),
                "upper_ft": feet(row["Upper limit"]), "lower": str(row["Lower limit"]), "upper": str(row["Upper limit"]),
                "transition_alt": row["Transition alt"], "source": f"AIP Maroc {row['Source']}",
                **({"note": row["Note"]} if row.get("Note") else {}), **({"approx": True} if approx else {}),
                "polygon": [dms(*p) for p in poly]})
d["terminal_areas"] = {"_note": "Terminal airspaces of the Moroccan aerodromes (TMA, CTA, CTR, ATZ) from the AIP Maroc "
                                "(ENR 2.1, AD 2.17), table Morocco_TMA_CTA_CTR.xlsx; 'aerodrome' is the one whose "
                                "approach / tower is responsible. Shapes following a border or a coast are approximated "
                                "(approx). The simulated approach takes an arrival over inside its aerodrome's terminal "
                                "airspace once the controller has decontrolled it.",
                       "areas": out}
open(P, "w").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
print(f"{len(out)} terminal areas; skipped {len(skipped)}:")
for s in skipped:
    print("  ", s)
for a in out:
    print(f"   {a['aerodrome']} {a['type']:4} {a['name']:32} {a['lower_ft']:>6}-{a['upper_ft']:<6} {len(a['polygon']):3} pts{' approx' if a.get('approx') else ''}")
