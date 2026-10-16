"""Rule-based baseline agent that talks to the simulator over HTTP (stdlib only).

It is deliberately simple. Its job is to show the API loop an LLM agent will use, and to
give you a score to beat:

    every N seconds:
        GET  /sessions/{id}/state                  -> picture
        GET  /sessions/{id}/separation             -> predicted conflicts / area infringements
        POST /sessions/{id}/probe                  -> what-if for each candidate clearance
        POST /sessions/{id}/instructions           -> issue the chosen clearance

Swap `decide_request()` and `resolve()` for LLM calls and keep the rest.

usage:
    python examples/baseline_agent.py --scenario s02_climb_request_into_crossing
    python examples/baseline_agent.py --all                 # run every scenario, print a score table
    python examples/baseline_agent.py --server http://192.168.1.20:8000 --scenario s10_busy_mixed --realtime
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

SERVER = "http://127.0.0.1:8000"
API_KEY = os.environ.get("ATC_API_KEY", "")     # needed when the server runs with accounts (ATC_AUTH=on)


def call(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SERVER + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def new_conflicts(probe: dict, callsign: str) -> list:
    """Conflicts/infringements in the what-if that involve `callsign` and are not in the baseline."""
    base = {tuple(c["aircraft"]) for c in probe["baseline"]["conflicts"]}
    base_inf = {(a["callsign"], a["area_id"]) for a in probe["baseline"]["area_infringements"]}
    out = [c for c in probe["with_instructions"]["conflicts"]
           if callsign in c["aircraft"] and tuple(c["aircraft"]) not in base]
    out += [a for a in probe["with_instructions"]["area_infringements"]
            if a["callsign"] == callsign and (a["callsign"], a["area_id"]) not in base_inf]
    return out


# ------------------------------------------------------------------------- decisions
def _ok(sid: str, instructions: list, callsign: str) -> bool:
    p = call("POST", f"/sessions/{sid}/probe", {"instructions": instructions, "horizon_s": 900})
    wx = [w for w in p["with_instructions"].get("weather_penetrations", []) if w["callsign"] == callsign]
    return not new_conflicts(p, callsign) and not p["rejected_instructions"] and not wx


def decide_request(sid: str, req: dict, ac: dict) -> list:
    """Returns the instructions to send. Plain requests: approve if the probe shows nothing new in 15 min.
    Weather deviations: approve as asked, else approve with a level change, else offer the other side."""
    cs, rid = req["callsign"], req["id"]
    if req.get("kind") == "checkin":
        return [{"callsign": cs, "type": "assume"}]      # first contact: assume the aircraft
    if req["type"] == "report":
        # e.g. "unable RVSM": acknowledge, and take it out of RVSM airspace (or 2000 ft) if the probe allows
        out = [{"callsign": cs, "type": "reply", "request_id": rid, "decision": "roger"}]
        for fl in (270, 250, ac["cleared_fl"] - 20, ac["cleared_fl"] + 20):
            cand = out + [{"callsign": cs, "type": "altitude", "fl": fl}]
            if _ok(sid, cand, cs):
                return cand
        return out
    approve = {"callsign": cs, "type": "reply", "request_id": rid, "decision": "approved"}
    unable = {"callsign": cs, "type": "reply", "request_id": rid, "decision": "unable"}
    if req["type"] == "weather_deviation":
        options = [[approve]]
        for d in (20, -20, 40, -40):           # same-direction levels first (semicircular rule)
            options.append([approve, {"callsign": cs, "type": "altitude", "fl": ac["cleared_fl"] + d}])
        other = "left" if req["value"]["side"] == "right" else "right"
        options.append([unable, {"callsign": cs, "type": "deviation", "side": other}])
        for opt in options:
            if _ok(sid, opt, cs):
                return opt
        return [approve]           # weather comes first: approve and resolve the conflict afterwards
    v = req["value"]
    if (req["type"] == "altitude" and ac["rvsm"]["status"] == "non_approved" and ac["category"] == "civil"
            and 290 <= v <= 410):
        return [unable]                        # not RVSM approved: cannot enter FL290-FL410
    clearance = {"callsign": cs, "type": req["type"]}
    speed = {"mach": v} if isinstance(v, float) and v < 2 else {"ias_kt": v}
    clearance.update({"altitude": {"fl": v}, "direct": {"waypoint": v}, "speed": speed}[req["type"]])
    if _ok(sid, [clearance], cs):
        return [approve]
    if "weather" in (req.get("reason") or ""):
        dev = {"callsign": cs, "type": "deviation", "side": "either"}
        if _ok(sid, [unable, dev], cs):
            return [unable, dev]
    return [unable]


def candidates(ac: dict, sector: dict) -> list:
    cs, fl, hdg = ac["callsign"], ac["cleared_fl"], ac["heading"]
    out = []
    for d in (20, -20, 10, -10):                # +-2000 ft keeps the semicircular level, +-1000 is tactical only
        if sector["vertical_limits"]["lower_fl"] < fl + d <= sector["vertical_limits"]["upper_fl"]:
            out.append([{"callsign": cs, "type": "altitude", "fl": fl + d}])
    if ac["speed_mode"] == "mach":
        for d in (-0.03, 0.02, -0.05):
            out.append([{"callsign": cs, "type": "speed", "mach": round(ac["mach"] + d, 2)}])
    for d in (30, -30, 45, -45, 60, -60):
        out.append([{"callsign": cs, "type": "heading", "heading": (hdg + d - 1) % 360 + 1}])
    return out


def resolve(sid: str, problem: dict, aircraft: dict, sector: dict) -> list | None:
    """Try simple manoeuvres for each aircraft involved; return the first that clears the problem."""
    involved = problem.get("aircraft") or [problem["callsign"]]
    for cs in involved:
        ac = aircraft.get(cs)
        if not ac:
            continue
        for cand in candidates(ac, sector):
            p = call("POST", f"/sessions/{sid}/probe", {"instructions": cand, "horizon_s": 900})
            if p["rejected_instructions"]:
                continue
            still = [c for c in p["with_instructions"]["conflicts"] if cs in c["aircraft"]]
            still += [a for a in p["with_instructions"]["area_infringements"] if a["callsign"] == cs]
            still += [x for x in p["with_instructions"].get("weather_penetrations", []) if x["callsign"] == cs]
            if not still:
                return cand
    return None


def try_resume(sid: str, ac: dict) -> bool:
    """Send an aircraft on a vector back to its route when that is conflict-free."""
    cand = [{"callsign": ac["callsign"], "type": "resume"}]
    p = call("POST", f"/sessions/{sid}/probe", {"instructions": cand, "horizon_s": 900})
    cs = ac["callsign"]
    bad = [c for c in p["with_instructions"]["conflicts"] if cs in c["aircraft"]]
    bad += [a for a in p["with_instructions"]["area_infringements"] if a["callsign"] == cs]
    if not bad:
        call("POST", f"/sessions/{sid}/instructions", cand)
        return True
    return False


# ------------------------------------------------------------------------- loop
def run_session(scenario: str, realtime: bool = False, period_s: int = 10, verbose: bool = True) -> dict:
    s = call("POST", "/sessions", {"scenario": scenario, "mode": "realtime" if realtime else "stepped",
                                   "time_factor": 5.0, "label": "baseline-agent"})
    sid = s["session"]["session_id"]
    brief = call("POST", f"/sessions/{sid}/connect?client=baseline_agent")
    sector = brief["sector"]
    if verbose:
        print(f"session {sid}  scenario {scenario}  (open the radar: {SERVER}/?session={sid})")
    vector_since = {}
    while True:
        st = call("GET", f"/sessions/{sid}/state")
        if st["finished"]:
            break
        aircraft = {a["callsign"]: a for a in st["aircraft"]}
        busy = {p["text"].split()[0] for p in st["pending_instructions"]}

        # 1. pilot requests, one at a time (each probe sees clearances already issued)
        for req in st["pending_requests"]:
            if req["callsign"] not in aircraft:
                continue
            d = decide_request(sid, req, aircraft[req["callsign"]])
            recs = call("POST", f"/sessions/{sid}/instructions", d)
            if verbose:
                what = ", ".join(i.get("decision") or i["type"] + " " + str(i.get("fl") or i.get("side") or "") for i in d)
                print(f"  t={st['sim_time_s']:6.0f}  {req['phraseology']:72s} -> {what}")

        # 2. predicted conflicts and area infringements within 6 minutes
        sep = call("GET", f"/sessions/{sid}/separation?lookahead_s=900")
        problems = [c for c in sep["predicted_conflicts"] if c["time_to_los_s"] is not None and c["time_to_los_s"] <= 360]
        problems += [a for a in sep["predicted_area_infringements"] if a["time_to_entry_s"] <= 360]
        busy |= {a["callsign"] for a in st["aircraft"] if a["nav_mode"] == "wxdev"}   # leave deviating aircraft laterally alone
        for prob in problems:
            if any(cs in busy for cs in prob.get("aircraft", [prob.get("callsign")])):
                continue
            sol = resolve(sid, prob, aircraft, sector)
            if sol:
                call("POST", f"/sessions/{sid}/instructions", sol)
                for i in sol:
                    busy.add(i["callsign"])
                    if i["type"] == "heading":
                        vector_since[i["callsign"]] = st["sim_time_s"]
                if verbose:
                    print(f"  t={st['sim_time_s']:6.0f}  resolve {prob.get('aircraft') or prob.get('area_id')} -> {sol}")
            break  # re-read the picture after each resolution

        # 3. aircraft on vectors: resume own navigation when safe (after at least 90 s)
        for cs, t0 in list(vector_since.items()):
            ac = aircraft.get(cs)
            if ac is None:
                vector_since.pop(cs)
            elif ac["nav_mode"] == "heading" and st["sim_time_s"] - t0 >= 90 and cs not in busy:
                if try_resume(sid, ac):
                    vector_since.pop(cs)
                    if verbose:
                        print(f"  t={st['sim_time_s']:6.0f}  {cs} resume own navigation")

        if realtime:
            time.sleep(period_s / 5.0)
        else:
            call("POST", f"/sessions/{sid}/control", {"action": "step", "seconds": period_s})
    m = call("GET", f"/sessions/{sid}/metrics")
    call("DELETE", f"/sessions/{sid}")
    return m


def summarize(name: str, m: dict) -> str:
    sep, air, rq = m["separation"], m["airspace"], m["requests"]["by_status"]
    return (f"{name:36s} LoS {sep['loss_events']:2d} ({sep['loss_seconds']:5.0f}s)  "
            f"infr {air['infringement_events']:2d}  instr {m['instructions']['issued']:3d}  "
            f"req appr/unable {rq['approved']}/{rq['unable']}  extra NM {m['traffic']['extra_track_miles']:6.1f}  "
            f"wx s>=30dBZ {m['weather']['seconds_in_30dbz']:4.0f}  self-dev {m['weather']['deviations_without_clearance']}  "
            f"seq spacing/level viol {m['sequencing']['spacing_violations']}/{m['sequencing']['level_violations']}  "
            f"rvsm viol {len(m['rvsm']['violations'])}  wrong-dir exits {len(m['semicircular']['wrong_direction_exits'])}  "
            f"off-route exits {len(m['traffic']['left_sector_off_route'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=SERVER)
    ap.add_argument("--scenario")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--realtime", action="store_true")
    a = ap.parse_args()
    SERVER = a.server.rstrip("/")
    names = [s["name"] for s in call("GET", "/scenarios")] if a.all else [a.scenario or "s01_basic_crossing"]
    rows = []
    for n in names:
        m = run_session(n, realtime=a.realtime, verbose=not a.all)
        rows.append(summarize(n, m))
    print("\n".join(rows))
