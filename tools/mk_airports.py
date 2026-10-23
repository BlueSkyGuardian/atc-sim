"""Airports, runways and radio navaids of the region from OurAirports (public domain,
https://github.com/davidmegginson/ourairports-data): every Moroccan aerodrome (Western Sahara included), every
Canary Islands aerodrome, the southern part of mainland Portugal with Madeira, southern Spain with Ceuta, Melilla and
Gibraltar. Written into the airspace file:

  airports              reference aerodromes (display, lookup, flight plans): ARP, elevation, type, runways with
                        their threshold coordinates (when OurAirports has them) and true headings
  external_points       the radio navaids (VOR, DME, NDB, TACAN) not already in the file (navaid: true)
  aerodromes[].runways  the runways the simulated aerodromes were missing (added after the existing ones, so the
                        automatic runway choice is unchanged)

  python tools/mk_airports.py <dir with airports.csv runways.csv navaids.csv>
"""
import csv
import json
import math
import os
import re
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
P = os.path.join(os.path.dirname(__file__), "..", "atcsim", "data", "airspaces", "agadir_cta.json")
d = json.loads(open(P).read())

TYPES = {"large_airport", "medium_airport", "small_airport"}
ES_SOUTH = {"ES-AN", "ES-EX", "ES-MC", "ES-CE", "ES-ML", "ES-CM"}


def wanted(a) -> bool:
    if a["type"] not in TYPES:
        return False
    lat = float(a["latitude_deg"])
    icao = re.fullmatch(r"[A-Z]{4}", a["ident"] or "") is not None
    c, r = a["iso_country"], a["iso_region"]
    if c in ("MA", "EH"):
        return True                                           # every Moroccan aerodrome
    if r == "ES-CN":
        return True                                           # every Canary Islands aerodrome
    if c == "GI":
        return True
    if c == "ES" and r in ES_SOUTH and lat < 39.5:
        return icao or a["type"] != "small_airport"
    if c == "PT" and (r == "PT-30" or lat < 40.0):
        return icao or a["type"] != "small_airport"
    return False


def in_region(lat, lon, c) -> bool:
    if c in ("MA", "EH", "GI"):
        return True
    if c == "ES":
        return lat < 40.5
    if c == "PT":
        return lat < 40.5
    return False


def dms(lat, lon) -> str:
    def f(v, w, h):
        v = abs(v); dd = int(v); m = int((v - dd) * 60); s = (v - dd - m / 60) * 3600
        if s >= 59.995:
            s = 0; m += 1
        if m >= 60:
            m = 0; dd += 1
        return f"{dd:0{w}d}{m:02d}{s:05.2f}{h}"
    return f(lat, 2, "N" if lat >= 0 else "S") + " " + f(lon, 3, "W" if lon < 0 else "E")


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


num = lambda v: float(v) if v not in ("", None) else None
airports = [a for a in csv.DictReader(open(os.path.join(SRC, "airports.csv"), encoding="utf-8")) if wanted(a)]
ids = {a["ident"] for a in airports}
rws = {}
for r in csv.DictReader(open(os.path.join(SRC, "runways.csv"), encoding="utf-8")):
    if r["airport_ident"] in ids and r["closed"] != "1":
        rws.setdefault(r["airport_ident"], []).append(r)

out = []
for a in sorted(airports, key=lambda a: a["ident"]):
    lat, lon = float(a["latitude_deg"]), float(a["longitude_deg"])
    runways = []
    for r in rws.get(a["ident"], []):
        ends = []
        for e in ("le", "he"):
            if not r[f"{e}_ident"]:
                continue
            end = {"id": r[f"{e}_ident"]}
            if r[f"{e}_latitude_deg"] and r[f"{e}_longitude_deg"]:
                end["coord"] = dms(float(r[f"{e}_latitude_deg"]), float(r[f"{e}_longitude_deg"]))
            if num(r[f"{e}_heading_degT"]) is not None:
                end["heading_true"] = round(float(r[f"{e}_heading_degT"]), 1)
            if num(r[f"{e}_elevation_ft"]) is not None:
                end["elev_ft"] = int(float(r[f"{e}_elevation_ft"]))
            if num(r[f"{e}_displaced_threshold_ft"]):
                end["displaced_ft"] = int(float(r[f"{e}_displaced_threshold_ft"]))
            ends.append(end)
        if not ends or ends[0]["id"].startswith("H"):
            continue                                          # helipads
        runways.append({"id": "/".join(e["id"] for e in ends), "ends": ends,
                        **({"length_ft": int(float(r["length_ft"]))} if r["length_ft"] else {}),
                        **({"width_ft": int(float(r["width_ft"]))} if r["width_ft"] else {}),
                        **({"surface": r["surface"]} if r["surface"] else {})})
    out.append({"id": a["ident"], "name": a["name"], "type": a["type"].replace("_airport", ""), "coord": dms(lat, lon),
                **({"elev_ft": int(float(a["elevation_ft"]))} if a["elevation_ft"] else {}),
                "country": a["iso_country"], **({"iata": a["iata_code"]} if a["iata_code"] else {}),
                **({"city": a["municipality"]} if a["municipality"] else {}),
                **({"scheduled": True} if a["scheduled_service"] == "yes" else {}), "runways": runways})
d["airports"] = out
d["_airports_source"] = ("OurAirports (public domain, github.com/davidmegginson/ourairports-data): every aerodrome of "
                         "Morocco (Western Sahara included) and the Canary Islands, Gibraltar, southern Spain (Andalucia, "
                         "Extremadura, Murcia, southern Castilla-La Mancha, Ceuta, Melilla) and southern Portugal "
                         "(south of 40N) with Madeira; runway thresholds and true headings as OurAirports has them")

# radio navaids
TYPE_TXT = {"VOR": "VOR", "VOR-DME": "VOR/DME", "VORTAC": "VORTAC", "DME": "DME", "NDB": "NDB", "NDB-DME": "NDB/DME",
            "TACAN": "TACAN"}
known = {**{k: v["coord"] for k, v in d["points"].items()}, **{k: v["coord"] for k, v in d["external_points"]["points"].items()}}
added, skipped = 0, []
for n in csv.DictReader(open(os.path.join(SRC, "navaids.csv"), encoding="utf-8")):
    if n["type"] not in TYPE_TXT or not n["latitude_deg"]:
        continue
    lat, lon = float(n["latitude_deg"]), float(n["longitude_deg"])
    if not in_region(lat, lon, n["iso_country"]):
        continue
    k = n["ident"].upper()
    if k in known:
        if nm(parse(known[k]), (lat, lon)) > 3.0:
            skipped.append(k)
        continue
    f = int(n["frequency_khz"]) if n["frequency_khz"] else None
    freq = (f"{f / 1000:.2f}" if f and f > 10000 else str(f)) if f else ""
    region = {"MA": "Maroc", "EH": "Maroc", "GI": "Gibraltar", "PT": "Lisboa" if lat > 34 else "Madeira",
              "ES": "Canarias" if lat < 30 else "Sevilla/Madrid"}.get(n["iso_country"], n["iso_country"])
    d["external_points"]["points"][k] = {"coord": dms(lat, lon), "region": region,
                                         "source": "OurAirports navaids", "navaid": True,
                                         "navaid_type": TYPE_TXT[n["type"]],
                                         "name": f"{TYPE_TXT[n['type']]} {n['name'].upper()} {freq}".strip()}
    known[k] = dms(lat, lon)
    added += 1

# runways the simulated aerodromes were missing
by_id = {a["id"]: a for a in out}
extra = {}
for ad in d["aerodromes"]:
    ref = by_id.get(ad["id"])
    if not ref:
        continue
    have = {str(r["id"]) for r in ad["runways"]}
    for r in ref["runways"]:
        for e in r["ends"]:
            if e["id"] in have or not re.fullmatch(r"\d{2}[LRC]?", e["id"]):
                continue
            hdg = e.get("heading_true")
            if hdg is None:
                hdg = (int(e["id"][:2]) * 10) % 360 - 1
            ad["runways"].append({"id": e["id"], "heading": int(round(hdg)) % 360 or 360})
            extra.setdefault(ad["id"], []).append(e["id"])

open(P, "w").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
rwn = sum(len(a["runways"]) for a in out)
print(f"{len(out)} airports ({rwn} runways), {added} navaids added, skipped (same ident elsewhere): {skipped}")
print("runways added to the simulated aerodromes:", extra)
cn = {}
for a in out:
    cn[a["country"]] = cn.get(a["country"], 0) + 1
print(cn)
