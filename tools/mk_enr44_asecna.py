"""Significant points of the ASECNA AIP ENR 4.4 south of the GMMM FIR (Dakar FIR: Western Sahara / Mauritania, down
to Nouakchott) into the airspace file's external points, and the ATS routes they carry: a Moroccan airway ending on
the FIR boundary (UR975 at ECHED, UM372 and UR977 at BULIS, UA600 at DKH...) is continued with its ASECNA points when
its end point carries the route in the ASECNA table or is within 150 NM of it; the other segments go to
"external_airways" (for routing beyond the FIR, not in the flight plan's airway names). A point already in the file keeps its position (the Moroccan AIP's for the
boundary points: ECHED differs by 5 NM). External points are "minor": their names show when zoomed in.

  pdftotext -layout AIP_ASECNA_ENR_4.4.pdf asecna.txt && python tools/mk_enr44_asecna.py asecna.txt
"""
import json
import math
import os
import re
import sys

P = os.path.join(os.path.dirname(__file__), "..", "atcsim", "data", "airspaces", "agadir_cta.json")
BOX = (17.9, 27.7, -20.5, -8.0)          # lat min, lat max, lon min, lon max: south of the GMMM FIR, Dakar FIR
SRC = "AIP ASECNA ENR 4.4 (01 OCT 2026)"

d = json.loads(open(P).read())
L = [l.rstrip() for l in open(sys.argv[1], encoding="utf-8")]
lat_re = re.compile(r"^\s*(\d{2})°(\d{2})'(\d{2}(?:\.\d+)?)\"([NS])\s*(.*)$")
lon_re = re.compile(r"^\s*(\d{3})°(\d{2})'(\d{2}(?:\.\d+)?)\"([EW])\s*(.*)$")
name_re = re.compile(r"^([A-Z0-9]{2,5})\s*$")


def nxt(i):
    i += 1
    while i < len(L) and not L[i].strip():
        i += 1
    return i


pts = {}
i = 0
while i < len(L):
    m = lat_re.match(L[i])
    if m:
        j = nxt(i)
        n = name_re.match(L[j]) if j < len(L) else None
        k = nxt(j)
        o = lon_re.match(L[k]) if n and k < len(L) else None
        if n and o:
            la = (int(m[1]) + int(m[2]) / 60 + float(m[3]) / 3600) * (-1 if m[4] == "S" else 1)
            lo = (int(o[1]) + int(o[2]) / 60 + float(o[3]) / 3600) * (-1 if o[4] == "W" else 1)
            sec = lambda s: (f"{float(s):04.1f}" if "." in s else s).replace(".0", "")
            pts[n[1]] = {"lat": la, "lon": lo, "coord": f"{m[1]}{m[2]}{sec(m[3])}{m[4]} {o[1]}{o[2]}{sec(o[3])}{o[4]}",
                         "role": " ".join((m[5] + " " + o[5]).split())}
            i = k + 1
            continue
    i += 1


def parse(s):
    a, b = s.split()
    def f(t, w):
        h = t[-1]; t = t[:-1]
        v = float(t[:w]) + float(t[w:w + 2]) / 60 + float(t[w + 2:] or 0) / 3600
        return -v if h in "SW" else v
    return f(a, 2), f(b, 3)


def nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return 3440.065 * math.acos(min(1, math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo2 - lo1)))


def region(p):
    r = p["role"].upper()
    for k, v in (("NOUAKCHOTT", "Dakar (Nouakchott)"), ("NOUADHIBOU", "Dakar (Nouadhibou)"), ("ATAR", "Dakar (Atar)"),
                 ("TIDJIKJA", "Dakar (Tidjikja)")):
        if k in r:
            return v
    return "Dakar FIR"


ext = d["external_points"]["points"]
mine = {k: v for k, v in pts.items() if BOX[0] <= v["lat"] <= BOX[1] and BOX[2] <= v["lon"] <= BOX[3]}
added = kept = 0
for k, v in sorted(mine.items()):
    if k in d["points"] or k in ext:
        kept += 1
        continue
    tma = re.search(r"(?:IAF|IF|MAHWP|MAHP|IAWP)\b.*?(NOUAKCHOTT|NOUADHIBOU|ATAR|TIDJIKJA)", v["role"].upper())
    ext[k] = {"coord": v["coord"], "region": region(v), "source": SRC, "minor": True,
              **({"name": v["role"][:140]} if v["role"] else {}), **({"tma": tma.group(1).title()} if tma else {})}
    added += 1


def xy(k):
    if k in d["points"]:
        return parse(d["points"][k]["coord"])
    if k in ext:
        return parse(ext[k]["coord"])
    return (pts[k]["lat"], pts[k]["lon"])


# ---- ATS routes: designators named in the point's routes column
routes = {}
for k, v in mine.items():
    for tok in re.findall(r"\b([A-Z]{1,2}\d{3})(?:/([A-Z]{1,2}\d{3}))?\b", v["role"]):
        for t in tok:
            if t:
                routes.setdefault(t, set()).add(k)
aw = d["airways"]
xa = d.setdefault("external_airways", {"_note": "ATS route segments beyond the GMMM FIR (not Moroccan airways): "
                                                "used to carry routes on, drawn as external"})
lower_of = {v.get("lower"): kk for kk, v in aw.items() if v.get("lower")}
merged = {}
for t, members in routes.items():
    key = t if t in aw else lower_of.get(t) or ("U" + t if "U" + t in aw else t)
    merged.setdefault(key, set()).update(members)
changed = []
for key, members in sorted(merged.items()):
    members = set(members)
    if len(members) + (1 if key in aw else 0) < 2:
        continue
    cont = False
    if key in aw:
        pl = list(aw[key]["points"])
        # continue from the end on the FIR boundary that is nearest to the new points
        c = [sum(xy(m)[0] for m in members) / len(members), sum(xy(m)[1] for m in members) / len(members)]
        end_last = nm(xy(pl[-1]), c) <= nm(xy(pl[0]), c)
        start = pl[-1] if end_last else pl[0]
        cont = start in members or min(nm(xy(start), xy(m)) for m in members if m != start) <= 150.0
    if not cont:
        pl, start, end_last = [], max(members, key=lambda m: xy(m)[0]), True     # northernmost first
    chain, cur, rest = [], start, set(members) - set(pl) - {start}
    if not cont:
        chain.append(start)
    while rest:                                      # nearest neighbour onwards from the boundary end
        n = min(rest, key=lambda m: nm(xy(cur), xy(m)))
        chain.append(n); rest.discard(n); cur = n
    if cont:
        aw[key]["points"] = pl + chain if end_last else chain[::-1] + pl
        aw[key]["_continued"] = f"beyond the FIR: {SRC}"
        changed.append(f"{key} (continued): {' '.join(aw[key]['points'])}")
    else:
        xa[key] = {"points": chain, "_source": SRC}
        changed.append(f"{key} (external): {' '.join(chain)}")
d["_enr44_asecna_source"] = f"{SRC}: {len(pts)} points, {len(mine)} south of the GMMM FIR ({added} added)"
open(P, "w").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
print(f"{SRC}: {len(pts)} points; {len(mine)} in the box, {added} added, {kept} already in the file")
for c in changed:
    print("  ", c)
for k in ("ECHED", "BULIS"):
    if k in pts and k in d["points"]:
        print(f"  {k}: Moroccan {d['points'][k]['coord']} vs ASECNA {pts[k]['coord']} "
              f"({nm(parse(d['points'][k]['coord']), (pts[k]['lat'], pts[k]['lon'])):.1f} NM)")
