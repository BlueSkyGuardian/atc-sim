"""A fake ADS-B network for the tests: aircraft flying straight lines over the Agadir CTA, served as the readsb API
(GET /v2/point/{lat}/{lon}/{nm}) with the route lookup (POST /api/0/routeset) like adsb.lol.
  python tests/fake_adsb.py <port>          (or import it: serve(port) in a thread)"""
import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = time.time()
# callsign, hex, type, category, lat, lon, track, gs, alt, selected alt, vertical rate, squawk, origin-destination
FLEET = [
    ("RAM201", "02a1b1", "B738", "A3", 31.2, -12.6, 200, 450, 37000, 37000, 0, "2101", "GMMN-GCLP"),
    ("IBE3301", "34c0a1", "A320", "A3", 33.9, -13.4, 205, 440, 36000, 36000, 0, "3301", "LEMD-GCXO"),
    ("TAP1471", "4951c1", "A21N", "A3", 34.6, -13.9, 210, 455, 38000, 38000, 0, "1471", "LPPT-GCTS"),
    ("RYR4AB", "4ca7d1", "B38M", "A3", 30.5, -12.2, 25, 430, 35000, 35000, 0, "5512", "GCLP-EGSS"),
    ("RAM1407", "02a1b2", "B788", "A5", 32.5, -9.1, 250, 480, 39000, 39000, 0, "1407", "GMMN-SBGR"),
    ("BAW2YD", "406b41", "A320", "A3", 31.8, -13.0, 30, 440, 34000, 36000, 1200, "6623", "GCLP-EGLL"),
    ("EZY73AD", "407d71", "A21N", "A3", 33.0, -12.1, 200, 445, 33000, 25000, -1800, "7012", "EGKK-GCRR"),
    ("DLH4UP", "3c6dd1", "A20N", "A3", 34.2, -12.6, 205, 450, 37000, 37000, 0, "1234", "EDDF-GCXO"),
    ("AEA5DF", "343091", "B738", "A3", 30.4, -11.3, 15, 420, 31000, 31000, 0, "2234", "GCTS-LEMD"),
    ("VLG7AB", "34531b", "A320", "A3", 35.4, -10.4, 190, 440, 32000, 32000, 0, "4412", "LEBL-GCLP"),
    ("RAM900", "02a1b3", "E190", "A3", 30.9, -9.4, 330, 380, 21000, 33000, 1500, "0900", "GMAD-GMMN"),
    ("TVF3FR", "3946e1", "B738", "A3", 32.2, -13.8, 20, 450, 38000, 38000, 0, "3366", "GCFV-LFPO"),
    ("ABY451", "8963b1", "A320", "A3", 33.6, -9.8, 220, 430, 30000, 30000, 0, "4451", "GMMX-GMAD"),
    ("WZZ5AB", "471f01", "A21N", "A3", 34.9, -12.0, 200, 445, 35000, 35000, 0, "5577", "LHBP-GCTS"),
    ("UAE751", "896111", "B77W", "A5", 35.5, -8.2, 225, 500, 40000, 40000, 0, "7510", "OMDB-SBGR"),
    ("NOCALL1", "0a1b2c", "C550", "A2", 31.5, -10.5, 45, 360, 25000, None, 0, "7000", ""),
    ("RAM301", "02a1b4", "B738", "A3", 31.21, -12.4, 200, 450, 37000, 37000, 0, "2102", "GMMN-GCTS"),   # behind RAM201
    # two converging at FL350 over 32.5N 11W in about 5 minutes: the conflict tools have something to show
    ("CFG410", "3c1a01", "A320", "A3", 32.5, -11.0 - 38 / 60 / math.cos(math.radians(32.5)), 90, 450, 35000, 35000, 0, "4101", "LEMD-GMMX"),
    ("SVA226", "710101", "B789", "A5", 32.5 - 38 / 60, -11.0, 0, 450, 35000, 35000, 0, "2260", "GOBD-LEMD"),
]


def positions(now=None):
    now = now or time.time()
    out = []
    for cs, hx, t, cat, lat, lon, trk, gs, alt, sel, vr, sq, od in FLEET:
        dt = now - T0
        d = gs * dt / 3600.0                                   # NM flown
        lat2 = lat + d / 60.0 * math.cos(math.radians(trk))
        lon2 = lon + d / 60.0 * math.sin(math.radians(trk)) / math.cos(math.radians(lat))
        a = alt + vr * dt / 60.0
        if sel is not None and vr:
            a = min(a, sel) if vr > 0 else max(a, sel)
        ac = {"hex": hx, "type": "adsb_icao", "flight": cs.ljust(8) if cs != "NOCALL1" else "", "r": "CN-" + hx[-3:].upper(), "t": t,
              "category": cat, "alt_baro": int(a), "alt_geom": int(a) + 200, "gs": gs, "track": trk, "baro_rate": vr if a != sel else 0,
              "squawk": sq, "emergency": "none", "lat": round(lat2, 5), "lon": round(lon2, 5), "seen_pos": 0.8, "seen": 0.3,
              "messages": 1000, "rssi": -20.0}
        if sel is not None:
            ac["nav_altitude_mcp"] = sel
        out.append(ac)
    return out


def _dist_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    return math.degrees(math.acos(min(1, max(-1, math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))))) * 60


class H(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        H.requests.append(self.path)
        H.last_headers = dict(self.headers)
        p = self.path.strip("/").split("/")
        if len(p) == 5 and p[0] == "v2" and p[1] == "point":
            lat, lon, nm = float(p[2]), float(p[3]), float(p[4])
            ac = [a for a in positions() if _dist_nm(lat, lon, a["lat"], a["lon"]) <= nm]
            return self._send({"ac": ac, "msg": "No error", "now": int(time.time() * 1000), "total": len(ac)})
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        H.requests.append(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/api/0/routeset"):
            od = {f[0]: f[12] for f in FLEET}
            out = [{"callsign": p["callsign"], "airport_codes": od.get(p["callsign"], "unknown") or "unknown", "plausible": True}
                   for p in body.get("planes", [])]
            return self._send(out)
        self._send({"error": "not found"}, 404)


def serve(port):
    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    s.serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]))
