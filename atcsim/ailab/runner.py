"""One headless run, in its own process:  python -m atcsim.ailab.runner <job.json>

The simulation is stepped as fast as it goes and stops at each decision point (every interval_s seconds, or at once
when an aircraft calls, a request comes, or a new conflict under 5 minutes appears) to show the situation to the
agent and apply its answer. The run is kept like an exercise (data/debriefs/<exercise>/orig: snapshots every 10 s,
replay frames, and every action logged) so it can be replayed 1:1 in the debrief.

Files written in the run's folder (read by the server while it runs):
  progress.json   status, sim time, decisions, the last decision
  steps.jsonl     one line per decision: what was shown, the prompt, the answer, the instructions and their results
  result.json     at the end: score, evaluation, preview, the written debrief report
  experiences.jsonl   the decisions with their reward and label (after the evaluation)
A file named "stop" in the folder ends the run early (it is still evaluated and kept).
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
import warnings


def _write(path: str, obj) -> None:
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f)
    os.replace(path + ".tmp", path)


def preview(w) -> dict:
    """A compact picture of the run: the sector, every track (one point per 30 s), where the losses happened."""
    d = w.env.airspace.describe()
    tracks, last = {}, {}
    for f in w.recording.get("frames") or []:
        for r in f.get("ac") or []:
            cs = r[0]
            if f["t"] - last.get(cs, -1e9) >= 30:
                tracks.setdefault(cs, []).append([round(r[1], 1), round(r[2], 1), int(r[3])])
                last[cs] = f["t"]
    losses = []
    frames = w.recording.get("frames") or []
    for x in (w.metrics().get("separation") or {}).get("losses") or []:
        t0 = x.get("start_s") or 0
        fr = min(frames, key=lambda f: abs(f["t"] - t0)) if frames else None
        if fr:
            pos = [[r[1], r[2]] for r in fr["ac"] if r[0] in (x.get("aircraft") or [])]
            if pos:
                losses.append({"t": t0, "aircraft": x.get("aircraft"), "x": round(sum(p[0] for p in pos) / len(pos), 1),
                               "y": round(sum(p[1] for p in pos) / len(pos), 1), "min_nm": x.get("min_horizontal_nm")})
    rnd = lambda pts: [[round(p[0], 1), round(p[1], 1)] for p in pts]
    return {"bbox": d["bbox_nm"], "outline": rnd(d["outline"]), "sectors": [rnd(s["polygon"]) for s in d["sectors"]],
            "tracks": tracks, "losses": losses}


def _summary_metrics(w) -> dict:
    from ..platform.exercises import summarize_metrics
    return summarize_metrics(w)


def run(job: dict) -> None:
    from .. import inputs, live, debrief as debrief_mod
    from ..session import Session
    from . import actions as act_mod, agents, evaluate, observe as obs_mod
    rd = job["dir"]
    prog_p, steps_p = os.path.join(rd, "progress.json"), os.path.join(rd, "steps.jsonl")
    agent_cfg, env, prov = job["agent"], job.get("env") or {}, job["provider"]
    prog = {"status": "starting", "t": 0, "decisions": 0, "started_at": time.time(), "errors": 0, "last": None}
    _write(prog_p, prog)

    s = Session(job["scenario"], mode="stepped", autostart=False, sid=job["exercise_id"], scn=job["scn"],
                label=job.get("label"))
    s.stop()
    s.solo = True                                   # synthetic pilots fly what is entered and read it back
    w = s.world
    w.auto_wx = bool(env.get("auto_wx", True))
    if env.get("readback_rate"):
        live.set_readback_rate(w, float(env["readback_rate"]), by="AI lab")
    s.persist_dir = job["archive_dir"]
    s._persist(rewrite=True)
    end = min(float(w.duration_s), float(env.get("end_s") or 1e9))
    prog["duration"] = end
    actor = {"name": job.get("actor_name") or "AI", "seat": "atc", "id": None}
    agent = agents.make(prov)
    system = agent_cfg.get("system_prompt") or agents.DEFAULT_SYSTEM_PROMPT
    interval = max(5.0, float(agent_cfg.get("interval_s", 30)))
    n_hist = int(agent_cfg.get("history", 2))
    max_actions = int(agent_cfg.get("max_actions", 12))
    force = bool(env.get("force"))
    history, feedback, steps = [], None, []
    next_t, last_t = 0.0, -1e9
    seen_calls, seen_reqs, seen_conf = set(), set(), set()
    prog["status"] = "running"
    stop_p = os.path.join(rd, "stop")

    def trigger() -> bool:
        from .. import atctools
        st_calls = {c for c, a in w.aircraft.items() if a.active and a.on_frequency and not a.assumed}
        st_reqs = {r.id for r in w.requests.values() if r.status == "pending" and r.kind != "checkin"}
        st_conf = {tuple(c["aircraft"]) for c in atctools.mtcd(w) if c["time_to_conflict_s"] <= 300}
        new = bool(st_calls - seen_calls or st_reqs - seen_reqs or st_conf - seen_conf)
        seen_calls.clear(); seen_calls.update(st_calls)
        seen_reqs.clear(); seen_reqs.update(st_reqs)
        seen_conf.clear(); seen_conf.update(st_conf)
        return new

    with open(steps_p, "a") as sf:
        while w.t < end - 1e-6 and not w.finished:
            if os.path.exists(stop_p):
                prog["status"] = "stopping"
                break
            due = w.t >= next_t - 1e-6
            if not due and agent_cfg.get("event_triggers", True) and w.t - last_t >= 10 and int(w.t) % 5 == 0:
                due = trigger()
            if due:
                trigger()
                text, struct = obs_mod.observe(w, agent_cfg.get("observation") or {})
                msgs = agents.messages(system, text, history[-n_hist:] if n_hist else [], feedback)
                rec = {"i": len(steps), "t": round(w.t, 1), "observation": text, "struct": struct, "feedback": feedback}
                t0 = time.time()
                try:
                    if isinstance(agent, agents.Baseline):
                        answer, usage, lat = agent.decide(w), {}, time.time() - t0
                    else:
                        answer, usage, lat = agent.chat(msgs)
                    acts, reasoning, perr = act_mod.parse(answer)
                    with s.lock:
                        results = act_mod.apply(w, acts, actor, max_actions=max_actions, force=force)
                    rec.update(response=answer, reasoning=reasoning, parse_error=perr or None, results=results,
                               latency_s=round(lat, 2), usage=usage)
                except Exception as e:  # noqa: BLE001 - the model did not answer: the traffic goes on
                    rec.update(response=None, error=str(e)[:500], results=[], latency_s=round(time.time() - t0, 2))
                    prog["errors"] += 1
                    answer = None
                fb = [f"{'OK' if r['ok'] else 'REFUSED'} {json.dumps(r['action'])}: {r['text']}" for r in rec["results"]]
                feedback = ("; ".join(fb) if fb else None) if answer is not None else f"no answer ({rec.get('error')})"
                if rec.get("parse_error"):
                    feedback = f"your answer could not be read ({rec['parse_error']}): answer with one JSON object"
                if answer is not None:
                    history.append({"observation": text, "response": answer})
                steps.append(rec)
                sf.write(json.dumps(rec) + "\n")
                sf.flush()
                last_t, next_t = w.t, w.t + interval
                prog.update(t=round(w.t, 1), decisions=len(steps), last={"t": rec["t"], "reasoning": rec.get("reasoning"),
                            "actions": [r["text"] for r in rec["results"]][:6], "error": rec.get("error")})
                _write(prog_p, prog)
            with s.lock:
                s._tick()
            if int(w.t) % 30 == 0:
                prog["t"] = round(w.t, 1)
                _write(prog_p, prog)
    # ---- the end: kept, evaluated, labelled
    with s.lock:
        s._snapshot()
    prog.update(status="evaluating", t=round(w.t, 1))
    _write(prog_p, prog)
    ev = evaluate.score(w, steps)
    evaluate.label(w, steps)
    with open(os.path.join(rd, "experiences.jsonl"), "w") as f:
        for st in steps:
            f.write(json.dumps({**st, "run_id": job["run_id"], "scenario": job["scenario"], "model": prov.get("model") or prov.get("kind"),
                                "system_prompt": system, "episode_score": ev["score"]}) + "\n")
    try:
        report = debrief_mod.build(w)
    except Exception:  # noqa: BLE001
        report = None
    _write(os.path.join(rd, "result.json"), {"evaluation": ev, "preview": preview(w), "report": report,
                                             "stopped": os.path.exists(stop_p),
                                             "metrics": _summary_metrics(w)})
    prog.update(status="done", t=round(w.t, 1), finished_at=time.time(), score=ev["score"])
    _write(prog_p, prog)


def main(argv) -> int:
    warnings.filterwarnings("ignore")
    with open(argv[0]) as f:
        job = json.load(f)
    try:
        run(job)
    except Exception as e:  # noqa: BLE001
        p = os.path.join(job["dir"], "progress.json")
        prog = {}
        try:
            prog = json.load(open(p))
        except (OSError, ValueError):
            pass
        prog.update(status="failed", error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])
        _write(p, prog)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
