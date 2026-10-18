"""The debrief of an exercise, built when it ends (kept with the exercise): what the instructor and the trainee read
after the run.

* workload: aircraft on the controller's frequency, minute by minute;
* each loss of separation with the clearances given to its two aircraft in the 10 minutes before it;
* requests not answered (expired) and the response times; read-back errors caught and missed;
* infringements, weather penetrations, emergencies and other live actions, bookmarks, CPDLC.
"""
from __future__ import annotations

from typing import Dict, List

LOOKBACK_S = 600.0


def _clearances(w, callsigns, t0: float, t1: float) -> List[dict]:
    out = []
    for e in w.events:
        if not (t0 <= e["time_s"] <= t1) or e.get("callsign") not in callsigns:
            continue
        if e["type"] == "atc_entry":
            ent = {k: v for k, v in (e.get("entry") or {}).items() if k in ("cfl", "xfl", "heading", "direct", "speed", "wx", "route")}
            if ent:
                out.append({"t": e["time_s"], "callsign": e["callsign"], "kind": "entry",
                            "text": " ".join(f"{k.upper()} {v if not isinstance(v, list) else ' '.join(map(str, v))}" for k, v in ent.items())})
        elif e["type"] in ("instruction", "pilot_readback") or (e["type"] == "cpdlc" and e.get("direction") == "up"):
            out.append({"t": e["time_s"], "callsign": e["callsign"], "kind": e["type"], "text": e.get("text", "")})
    return out[-6:]


def build(w) -> dict:
    m = w.metrics()
    # workload: aircraft on the frequency (and those calling) at each minute, from the replay frames
    frames = w.recording.get("frames") or []
    load: List[list] = []
    nxt = 0.0
    for f in frames:
        if f["t"] + 1e-6 < nxt:
            continue
        n = sum(1 for r in f.get("ac") or [] if r[12] and r[13] == "A")       # on the frequency, in the sector
        load.append([round(f["t"] / 60.0, 1), n])
        nxt = f["t"] + 60.0
    peak = max((x[1] for x in load), default=0)
    sep = m.get("separation") or {}
    losses = []
    for p in sep.get("losses") or []:
        acs = p.get("aircraft") or []
        t = float(p.get("start_s") or 0)
        losses.append({"aircraft": acs, "start_s": t, "end_s": p.get("end_s"), "min_horizontal_nm": p.get("min_horizontal_nm"),
                       "min_vertical_ft": p.get("min_vertical_ft"), "ongoing": p.get("ongoing", False),
                       "before": _clearances(w, set(acs), t - LOOKBACK_S, t + 1)})
    reqs = [r for r in w.requests.values() if r.kind != "checkin"]
    unanswered = [{"t": r.time, "callsign": r.callsign, "text": r.phrase(), "status": r.status} for r in reqs
                  if r.status in ("expired", "pending")]
    live = [{"t": e["time_s"], "text": e.get("text", ""), "action": e.get("action"), "by": e.get("by")}
            for e in w.events if e["type"] == "live"]
    rb = getattr(w, "readback_traps", [])
    return {
        "sim_time_s": round(w.t, 1), "duration_s": w.duration_s,
        "workload": {"per_minute": load, "peak": peak,
                     "mean": round(sum(x[1] for x in load) / len(load), 1) if load else 0},
        "losses": losses,
        "infringements": ((m.get("airspace") or {}).get("log") or [])[:30],
        "weather_penetrations": (m.get("weather") or {}).get("penetrations", [])[:30],
        "requests": {"total": len(reqs), "unanswered": unanswered[:40],
                     "mean_response_s": (m.get("requests") or {}).get("mean_response_time_s")},
        "assume": m.get("assumption"),
        "readback": {"counts": dict(w.m.get("readback", {"errors": 0, "caught": 0, "missed": 0})),
                     "items": [{k: v for k, v in t.items() if k != "instr"} for t in rb if t["status"] in ("caught", "missed")][:40]},
        "live": live[:60],
        "bookmarks": list(getattr(w, "bookmarks", []))[:100],
        "cpdlc": {k: (len(v) if isinstance(v, list) else v) for k, v in (w.m.get("cpdlc") or {}).items()},
        "instructions": m.get("instructions"),
        "rvsm_violations": len((m.get("rvsm") or {}).get("violations", [])),
        "wrong_direction_exits": len((m.get("semicircular") or {}).get("wrong_direction_exits", [])),
    }


def bookmark(w, note: str, by: str = "") -> Dict:
    bm = w.__dict__.setdefault("bookmarks", [])
    b = {"id": len(bm) + 1, "t": round(w.t, 1), "note": " ".join(str(note or "").split())[:200] or "mark", "by": by}
    bm.append(b)
    w._event("bookmark", bookmark=b["id"], by=by, text=f"⚑ {b['note']}" + (f" ({by})" if by else ""))
    return b
