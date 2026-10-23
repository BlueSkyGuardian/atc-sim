"""Significant points of the Spanish AIP (ENAIRE AIP ENR 4.4, name-code designators) south of 40.5N and west of 0.5E -
southern Spain, the Strait and the Canary Islands - into the airspace file's external points. A point already in the
file keeps its Moroccan AIP position (points) and an external point is updated to the ENAIRE position. They are
"minor" points: on the scope their names show when zoomed in.

  pdftotext -layout ENAIRE_AIP_ENR_4.4.pdf enr44.txt
  python tools/mk_enr44.py enr44.txt
"""
import json
import math
import os
import re
import sys

P = os.path.join(os.path.dirname(__file__), "..", "atcsim", "data", "airspaces", "agadir_cta.json")
d = json.loads(open(P).read())
L = open(sys.argv[1], encoding="utf-8").read().split("\n")
edition = next((m.group(1) for l in L[:6] for m in [re.search(r"WEF (\d{2}-[A-Z]{3}-\d{2})", l)] if m), "")


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


pts = {}
for i, line in enumerate(L):
    m = re.match(r"^\s*([A-Z]{5})\s+(\d{6}(?:\.\d+)?[NS])\b(.*)$", line)
    if not m:
        continue
    name, lat, rest = m.groups()
    for j in range(i + 1, min(i + 4, len(L))):
        mm = re.match(r"^\s*(\d{7}(?:\.\d+)?[EW])\b(.*)$", L[j])
        if mm:
            rem = [rest, mm.group(2)]
            k = j + 1
            while k < len(L) and k < j + 6 and L[k].strip() and not re.match(r"^\s*[A-Z]{5}\s+\d{6}", L[k]):
                rem.append(L[k]); k += 1
            txt = " ".join(" ".join(r.split()) for r in rem).strip()
            fra = re.search(r"\(([IEXAD]+)\)", rest)
            pts[name] = {"coord": f"{lat} {mm.group(1)}", "remarks": txt, "fra": fra.group(1) if fra else ""}
            break

ext = d["external_points"]["points"]
added = updated = kept = 0
for k, v in sorted(pts.items()):
    lat, lon = parse(v["coord"])
    if lat >= 40.5 or lon >= 0.5:
        continue
    tma = re.search(r"TMA ([A-ZÁÉÍÓÚÑ]+(?: [A-ZÁÉÍÓÚÑ]+)?)", v["remarks"])
    region = "Canarias" if lat < 30.5 else ("Sevilla/Madrid" if lon < -1.0 or lat < 38 else "Madrid/Valencia")
    entry = {"coord": v["coord"], "region": region, "source": f"ENAIRE AIP ENR 4.4 (WEF {edition})" if edition else "ENAIRE AIP ENR 4.4",
             "minor": True, **({"fra": v["fra"]} if v["fra"] else {}),
             **({"name": re.sub(r"\s*Obligatorio.*?// ?", "", v["remarks"])[:140]} if v["remarks"] else {}),
             **({"tma": tma.group(1).title()} if tma else {})}
    if k in d["points"]:
        kept += 1                                       # Moroccan AIP point: its position stays
        continue
    if k in ext:
        if nm(parse(ext[k]["coord"]), (lat, lon)) > 0.05 or "ENAIRE" not in ext[k].get("source", ""):
            ext[k] = {**ext[k], **entry, "minor": ext[k].get("minor", False)}
            updated += 1
        continue
    ext[k] = entry
    added += 1
d["_enr44_source"] = f"ENAIRE AIP ENR 4.4 {edition}: {len(pts)} points, those south of 40.5N and west of 0.5E as external points"
open(P, "w").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
print(f"{len(pts)} points in ENR 4.4 ({edition}); {added} added, {updated} external updated, {kept} already Moroccan points")
