"""Extrapolates debrief timelines in their own process (so the server's simulations and players are not slowed):
   python -m atcsim.xpworker --serve <scenario.json>     (warm worker: job file paths on stdin)
   python -m atcsim.xpworker <job file>

The job (a pickle written by platform/debriefs.py) gives the scenario, the world to start from (a snapshot), the
actions of the parent timeline still to come, the aircraft whose actions are not replayed, the end time.
Output, in the timeline's folder, as it goes (the server reads them while the worker runs):
  x<gen>.bin       snapshots every 10 s  (struct "<dI" time, length, then the compressed world)
  x<gen>.log       the actions applied, one JSON line each, written before the snapshot that follows them
  x<gen>.done.json at the end: the timeline's markers, its end time and its outcome
"""
from __future__ import annotations

import json
import os
import pickle
import struct
import sys
import warnings


def run(s, job: dict) -> None:
    from . import inputs
    from .platform.debriefs import _summary, _timeline_events, blob_of
    d, gen = job["dir"], job["gen"]
    genf = os.path.join(d, "gen")

    def cancelled() -> bool:                       # the timeline was changed again: this computation is not needed
        try:
            return open(genf).read().strip() != str(gen)
        except OSError:
            return True
    w = s._load_world(job["start_blob"])
    fut, touched, end, snap_s = job["fut"], set(job["touched"]), float(job["end"]), float(job["snap_s"])
    skip = set(job["skip_kinds"])
    sb = open(os.path.join(d, f"x{gen}.bin"), "ab")
    lg = open(os.path.join(d, f"x{gen}.log"), "a")
    n0, i, last = len(inputs.log(w)), 0, float(job["start_t"])
    try:
        while w.t < end - 1e-6 and not w.finished:
            w.step(1.0)
            while i < len(fut) and fut[i]["t"] <= w.t + 1e-6:
                inp = fut[i]
                i += 1
                if inp["kind"] in skip or inputs.callsign_of(inp["kind"], inp["body"]) in touched:
                    continue
                try:
                    inputs.apply(w, inp["kind"], inp["body"], {"name": inp.get("by"), "seat": inp.get("seat")})
                except Exception:  # noqa: BLE001 - an action that does not fit this timeline any more
                    pass
            log = inputs.log(w)
            for e in log[n0:]:
                lg.write(json.dumps(e) + "\n")
            n0 = len(log)
            if w.t - last >= snap_s - 1e-6 or w.t >= end - 1e-6 or w.finished:
                lg.flush()
                b = blob_of(w)
                sb.write(struct.pack("<dI", round(w.t, 1), len(b)) + b)
                sb.flush()
                last = w.t
                if cancelled():
                    return
    finally:
        lg.close()
        sb.close()
    done = {"events": _timeline_events(w), "end_t": round(w.t, 1), "summary": _summary(w, float(job["t0"]))}
    p = os.path.join(d, f"x{gen}.done.json")
    with open(p + ".tmp", "w") as f:
        json.dump(done, f)
    os.replace(p + ".tmp", p)


def build(scn_path: str):
    from .session import Session
    with open(scn_path) as f:
        scn = json.load(f)
    s = Session(scn.get("name") or "debrief", mode="realtime", autostart=False, sid="xp", scn=scn)
    s.stop()
    return s


def main(argv) -> int:
    """--serve <scenario.json>: build the scenario once, then run the jobs whose file paths come on stdin, one per
    line, answering "done <path>" on stdout (a warm worker: no start-up wait for each new timeline).
    <job file>: run one job."""
    warnings.filterwarnings("ignore")
    if argv[0] == "--serve":
        s = build(argv[1])
        print("ready", flush=True)
        for line in sys.stdin:
            jp = line.strip()
            if not jp:
                continue
            try:
                with open(jp, "rb") as f:
                    run(s, pickle.load(f))
            except Exception as e:  # noqa: BLE001
                print(f"error {jp} {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            print(f"done {jp}", flush=True)
        return 0
    with open(argv[0], "rb") as f:
        job = pickle.load(f)
    run(build(job["scn_path"]), job)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
