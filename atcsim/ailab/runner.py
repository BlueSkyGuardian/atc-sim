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

Watch live: while the server keeps "watch.json" fresh ({"factor": 0 | x, "until": epoch}), the world is written to
"live.snap" twice a second (and at every decision), and with a factor the run is paced to that many simulated
seconds per second, so a person can follow it on the radar. Nobody watching: full speed, nothing written.
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


def _blob(w) -> bytes:
    import io
    import pickle
    import zlib
    from ..session import _pid
    rec = w.recording
    w.recording = {"every_s": rec["every_s"], "frames": [], "meta": rec["meta"]}

    class _P(pickle.Pickler):
        def persistent_id(self, o):
            return _pid(o)
    try:
        f = io.BytesIO()
        _P(f, protocol=5).dump(w)
    finally:
        w.recording = rec
    return zlib.compress(f.getvalue(), 1)


class Watch:
    """Someone watches the run live: publish the world, pace the clock."""

    def __init__(self, rd: str):
        self.p, self.live = os.path.join(rd, "watch.json"), os.path.join(rd, "live.snap")
        self.checked, self.until, self.factor, self.last = 0.0, 0.0, 0.0, 0.0
        self.anchor = None

    def on(self) -> bool:
        now = time.time()
        if now - self.checked > 0.5:
            self.checked = now
            try:
                with open(self.p) as f:
                    j = json.load(f)
                f2 = float(j.get("factor") or 0)
                if f2 != self.factor:
                    self.anchor = None
                self.until, self.factor = float(j.get("until") or 0), f2
            except (OSError, ValueError):
                self.until = 0.0
        return now < self.until

    def publish(self, w, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last < 0.5:
            return
        self.last = now
        try:
            with open(self.live + ".tmp", "wb") as f:
                f.write(_blob(w))
            os.replace(self.live + ".tmp", self.live)
        except OSError:
            pass

    def pace(self, w) -> None:
        if self.factor <= 0:
            return
        now = time.time()
        if self.anchor is None:
            self.anchor = (now, w.t)
            return
        ahead = (w.t - self.anchor[1]) / self.factor - (now - self.anchor[0])
        if ahead > 0:
            time.sleep(min(ahead, 2.0))
        elif ahead < -2.0:
            self.anchor = (now, w.t)         # fell behind (a slow step): start again from here

    def reset(self) -> None:
        self.anchor = None


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
    system = agents.system_prompt(agent_cfg.get("system_prompt"))
    if agent_cfg.get("briefing", True):
        from .briefing import briefing
        system = system + "\n\n" + briefing(w)
    interval = max(5.0, float(agent_cfg.get("interval_s", 30)))
    n_hist = int(agent_cfg.get("history", 2))
    from . import queries as q_mod
    q_rounds = int(agent_cfg.get("query_rounds", 0) or 0) if agent_cfg.get("queries") else 0
    if q_rounds:
        system = system + "\n\n" + q_mod.help_text(q_rounds)
    max_actions = int(agent_cfg.get("max_actions", 30))
    if max_actions == 12:                                  # the former default: too few with ten aircraft calling
        max_actions = 30
    force = bool(env.get("force"))
    rules_on = agent_cfg.get("rules", True) is not False
    fix_rounds = int(agent_cfg.get("correction_rounds", 1) or 0)
    auto_assume, auto_transfer = bool(env.get("auto_assume")), bool(env.get("auto_transfer"))
    if auto_assume:
        w.auto_assume = True                               # aircraft call already assumed: the model only separates
    ctx = int((prov.get("params") or {}).get("context_tokens") or 8192)
    ans_tokens = int((prov.get("params") or {}).get("max_tokens") or 600)
    budget = max(1500, ctx - ans_tokens - 200)
    est = lambda ms: sum(len(m["content"]) for m in ms) / 3.2 + 8 * len(ms)      # tokens, roughly (3.2 characters each)
    conf_seen: dict = {}
    history, feedback, steps = [], None, []
    next_t, last_t = 0.0, -1e9
    seen_calls, seen_reqs, seen_conf = set(), set(), set()
    prog["status"] = "running"
    stop_p = os.path.join(rd, "stop")
    watch = Watch(rd)

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
                from .. import atctools
                live_pairs = {"×".join(sorted(c["aircraft"])) for c in atctools.mtcd(w)}
                for k in list(conf_seen):
                    if k not in live_pairs:
                        conf_seen.pop(k)
                for k in live_pairs:
                    conf_seen.setdefault(k, w.t)
                if auto_transfer:
                    with s.lock:
                        for a in list(w.aircraft.values()):
                            if a.outside and a.assumed:
                                inputs.apply(w, "release", {"callsign": a.callsign}, {"name": "auto transfer", "seat": "atc"})
                text, struct = obs_mod.observe(w, {**(agent_cfg.get("observation") or {}), "_seen": conf_seen})
                hist = history[-n_hist:] if n_hist else []
                msgs = agents.messages(system, text, hist, feedback)
                trimmed = []
                while hist and est(msgs) > budget:               # keep within the model's context: older decisions go first
                    hist = hist[1:]
                    msgs = agents.messages(system, text, hist, feedback)
                    trimmed.append("memory")
                rec = {"i": len(steps), "t": round(w.t, 1), "observation": text, "struct": struct, "feedback": feedback,
                       "prompt_tokens_est": int(est(msgs))}
                if trimmed:
                    rec["trimmed"] = f"{len(trimmed)} earlier decision(s) left out to fit the context ({ctx} tokens)"
                prog.update(thinking=True, t=round(w.t, 1))
                _write(prog_p, prog)
                if watch.on():
                    watch.publish(w, force=True)
                t0 = time.time()
                try:
                    if isinstance(agent, agents.Baseline):
                        answer, usage, lat = agent.decide(w), {}, time.time() - t0
                    else:
                        answer, usage, lat = agent.chat(msgs)
                    usage = dict(usage or {})
                    acts, reasoning, perr, qs = act_mod.parse_full(answer)
                    rounds = []
                    while qs and not isinstance(agent, agents.Baseline):
                        left = q_rounds - len(rounds)
                        if left > 0 and est(msgs) + 1200 > budget:      # no room for another round in the context
                            left = 0
                            rec["trimmed"] = (rec.get("trimmed", "") + "; " if rec.get("trimmed") else "") + "no more queries: the context is full"
                        if left <= 0:
                            if not acts:                       # out of questions and no instructions
                                rounds.append({"response": answer, "queries": qs, "results": [],
                                               "note": "no query rounds left"})
                            break
                        with s.lock:
                            res = q_mod.answer(w, qs)
                        follow = ("ANSWERS TO YOUR QUERIES:\n" + q_mod.as_text(res) +
                                  (f"\n\nNow give your instructions (or more queries: {left - 1} round{'s' if left - 1 != 1 else ''} left)."
                                   if left - 1 > 0 else "\n\nNow give your instructions (no query left for this decision)."))
                        rounds.append({"response": answer, "reasoning": reasoning, "queries": qs, "results": res, "followup": follow})
                        msgs = msgs + [{"role": "assistant", "content": answer}, {"role": "user", "content": follow}]
                        prog.update(querying=len(rounds))
                        _write(prog_p, prog)
                        answer, u2, l2 = agent.chat(msgs)
                        lat += l2
                        for k, v in (u2 or {}).items():
                            if isinstance(v, (int, float)):
                                usage[k] = (usage.get(k) or 0) + v
                        acts, reasoning, perr, qs = act_mod.parse_full(answer)
                    with s.lock:
                        results = act_mod.apply(w, acts, actor, max_actions=max_actions, force=force, rules=rules_on)
                    last = results
                    n_fix = 0
                    while (fix_rounds > n_fix and not isinstance(agent, agents.Baseline)
                           and (perr or any(not r["ok"] or r.get("unknown_fields") for r in last)) and est(msgs) + 900 < budget):
                        bad = [r for r in last if not r["ok"] or r.get("unknown_fields")]
                        if perr:
                            rec["unreadable_first"] = perr
                            follow = (f"YOUR ANSWER COULD NOT BE READ ({perr}). Answer again now with ONE JSON object: "
                                      '{"reasoning": "...", "actions": [...]} and nothing else.')
                        else:
                            follow = ("YOUR ANSWER HAD ERRORS. The other instructions were given; these were NOT:\n"
                                      + "\n".join(f"- {json.dumps(r['action'])} -> {r['text']}" for r in bad)
                                      + "\nAnswer again now with ONLY the corrected instructions for these (an empty actions list to drop them), "
                                        "same JSON format.")
                        rounds.append({"kind": "correction", "response": answer, "reasoning": reasoning, "results": last,
                                       "followup": follow})
                        msgs = msgs + [{"role": "assistant", "content": answer}, {"role": "user", "content": follow}]
                        answer, u2, l2 = agent.chat(msgs)
                        lat += l2
                        for k, v in (u2 or {}).items():
                            if isinstance(v, (int, float)):
                                usage[k] = (usage.get(k) or 0) + v
                        acts, reasoning2, perr, _ = act_mod.parse_full(answer)
                        reasoning = reasoning2 or reasoning
                        with s.lock:
                            last = act_mod.apply(w, acts, actor, max_actions=max_actions, force=force, rules=rules_on)
                        for r in last:
                            r["correction"] = True
                        results = results + last
                        n_fix += 1
                    if n_fix:
                        rec["corrections"] = n_fix
                    if rounds:
                        rec["queries"] = rounds
                    rec.update(response=answer, reasoning=reasoning, parse_error=perr or None, results=results,
                               latency_s=round(lat, 2), usage=usage)
                except Exception as e:  # noqa: BLE001 - the model did not answer: the traffic goes on
                    rec.update(response=None, error=str(e)[:500], results=[], latency_s=round(time.time() - t0, 2))
                    prog["errors"] += 1
                    answer = None
                fixed = {str(r["action"].get("callsign", "")).upper() for r in rec["results"] if r.get("correction") and r["ok"]}
                errs = [r for r in rec["results"] if (not r["ok"] or r.get("unknown_fields") or r.get("early"))
                        and not (not r.get("correction") and str(r["action"].get("callsign", "")).upper() in fixed)]
                given = [r for r in rec["results"] if r["ok"]]
                fb = []
                if errs:
                    fb.append("ERRORS — read them and do not repeat them: " + " | ".join(f"{json.dumps(r['action'])} -> {r['text']}" for r in errs))
                if given:
                    fb.append("given: " + "; ".join(r["text"] for r in given))
                feedback = (". ".join(fb) if fb else None) if answer is not None else f"no answer ({rec.get('error')})"
                td = struct.get("todo") or {}
                done = {(str(r["action"].get("type", "")).lower(), str(r["action"].get("callsign") or r["action"].get("request_id") or "").upper())
                        for r in rec["results"] if r.get("ok")}
                did = lambda t, x: (t, x.upper()) in done
                missed = []
                if td.get("assume") and not auto_assume:
                    m_ = [c for c in td["assume"] if not did("assume", c)]
                    if m_:
                        missed.append("not assumed (still calling you): " + ", ".join(m_))
                if td.get("transfer") and not auto_transfer:
                    m_ = [c for c in td["transfer"] if not did("decontrol", c)]
                    if m_:
                        missed.append("not transferred: " + ", ".join(m_))
                if td.get("answer"):
                    m_ = [q for q in td["answer"] if not did("reply", q)]
                    if m_:
                        missed.append("requests not answered: " + ", ".join(m_))
                acted = {x[1] for x in done}
                if td.get("solve"):
                    m_ = ["×".join(p) for p in td["solve"] if not set(x.upper() for x in p) & acted]
                    if m_:
                        missed.append("conflicts left unsolved: " + ", ".join(m_))
                if td.get("resume"):
                    m_ = [c for c in td["resume"] if c.upper() not in acted]
                    if m_:
                        missed.append("not resumed: " + ", ".join(m_))
                if missed and answer is not None:
                    rec["missed"] = missed
                    feedback = ((feedback + ". ") if feedback else "") + "LEFT UNDONE FROM YOUR TO DO: " + "; ".join(missed)
                if (rec.get("queries") or [{}])[-1].get("note"):
                    feedback = ((feedback + "; ") if feedback else "") + ("queries are not available (or none left): answer with instructions"
                                                                          if not q_rounds else "no query round left: answer with instructions")
                if rec.get("parse_error"):
                    feedback = f"your answer could not be read ({rec['parse_error']}): answer with one JSON object"
                if answer is not None:
                    history.append({"observation": text, "response": answer})
                steps.append(rec)
                sf.write(json.dumps(rec) + "\n")
                sf.flush()
                last_t, next_t = w.t, w.t + interval
                if watch.on():
                    watch.publish(w, force=True)
                    watch.reset()
                prog.update(thinking=False, querying=0, t=round(w.t, 1), decisions=len(steps), last={"t": rec["t"], "reasoning": rec.get("reasoning"),
                            "actions": [r["text"] for r in rec["results"]][:6], "error": rec.get("error")})
                _write(prog_p, prog)
            with s.lock:
                s._tick()
            if watch.on():
                watch.publish(w)
                watch.pace(w)
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
    if watch.on():
        watch.publish(w, force=True)
    prog.update(status="done", t=round(w.t, 1), finished_at=time.time(), score=ev["score"], thinking=False)
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
