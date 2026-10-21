"""AI lab (/ai, instructors and administrators): models, agent profiles, headless runs, experiences, datasets.

* Models: RunPod serverless vLLM endpoints, OpenAI-compatible servers (a RunPod pod running vLLM, TGI, Ollama...),
  or the built-in baseline. API keys are kept encrypted (key derived from the server's secret) and never sent back.
* Agent profiles: the system prompt, what the model is shown, how often it decides, how many actions it may give.
* Runs: a scenario controlled headlessly by an agent (atcsim/ailab/runner.py, one process per run, ATC_AI_PARALLEL at
  a time). Each run is kept as an exercise of its own (not listed with the people's exercises) so it opens in the
  debrief: replayed 1:1, and branched like any other.
* Experiences: every decision with what was shown, the answer, the instructions and their results, a reward and a
  label (and the judge's grade when asked). Datasets: exports as JSONL (chat fine-tuning, or whole trajectories),
  downloadable, and fetchable by a RunPod pod with the dataset's link.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import config, db
from .auth import require_user

router = APIRouter()
ROOT = os.path.join(config.DATA_DIR, "ai")
RUNS = os.path.join(ROOT, "runs")
SETS = os.path.join(ROOT, "datasets")
PARALLEL = int(os.environ.get("ATC_AI_PARALLEL", "2"))
_lock = threading.RLock()
_procs: dict = {}                    # run id -> Popen

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_models (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,  -- runpod | openai | baseline | solver
  endpoint_id TEXT, base_url TEXT, model TEXT, api_key TEXT, params TEXT,
  created_by INTEGER, created_at REAL
);
CREATE TABLE IF NOT EXISTS ai_agents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, config TEXT NOT NULL, created_by INTEGER, created_at REAL
);
CREATE TABLE IF NOT EXISTS ai_runs (
  id TEXT PRIMARY KEY, batch TEXT, created_by INTEGER, created_at REAL, status TEXT, scenario TEXT,
  model_id INTEGER, agent_id INTEGER, model_name TEXT, agent_name TEXT, env TEXT, exercise_id TEXT,
  score REAL, result TEXT, error TEXT, finished_at REAL, judge TEXT
);
CREATE TABLE IF NOT EXISTS ai_datasets (
  id TEXT PRIMARY KEY, name TEXT, created_by INTEGER, created_at REAL, format TEXT, filters TEXT,
  records INTEGER, runs INTEGER, token TEXT
);
"""
_ready = False


def _db():
    global _ready
    if not _ready:
        db.conn().executescript(SCHEMA)
        try:
            db.conn().execute("ALTER TABLE exercises ADD COLUMN ai INTEGER DEFAULT 0")
        except Exception:  # noqa: BLE001 - already there
            pass
        _ready = True


def _staff(request: Request) -> dict:
    _db()
    return require_user(request, roles=("instructor",))


# ------------------------------------------------------------------ secrets
def _fernet():
    from cryptography.fernet import Fernet
    k = base64.urlsafe_b64encode(hashlib.sha256(("ai-lab:" + config.SECRET_KEY).encode()).digest())
    return Fernet(k)


def _enc(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode() if s else ""


def _dec(s: str) -> str:
    if not s:
        return ""
    try:
        return _fernet().decrypt(s.encode()).decode()
    except Exception:  # noqa: BLE001 - another server's secret: the key must be entered again
        return ""


# ------------------------------------------------------------------ models
class ModelIn(BaseModel):
    name: str
    kind: str = "runpod"               # runpod | openai | baseline
    endpoint_id: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None      # left out (None) on an update: kept
    params: dict = {}


def _model_row(r: dict, secret: bool = False) -> dict:
    out = {k: r[k] for k in ("id", "name", "kind", "endpoint_id", "base_url", "model", "created_at")}
    out["params"] = json.loads(r["params"] or "{}")
    key = _dec(r["api_key"])
    out["has_key"] = bool(key)
    out["key_hint"] = ("…" + key[-4:]) if len(key) > 8 else ("set" if key else "")
    if secret:
        out["api_key"] = key
    return out


def _model(mid: int, secret: bool = False) -> dict:
    r = db.q1("SELECT * FROM ai_models WHERE id=?", (mid,))
    if r is None:
        raise HTTPException(404, "no such model")
    return _model_row(r, secret)


def _check_model(b: ModelIn) -> None:
    if b.kind not in ("runpod", "openai", "baseline", "solver"):
        raise HTTPException(400, "kind: runpod, openai, baseline or solver")
    if b.kind == "runpod" and not (b.endpoint_id or "").strip():
        raise HTTPException(400, "the RunPod endpoint id")
    if b.kind == "openai" and not (b.base_url or "").startswith(("http://", "https://")):
        raise HTTPException(400, "the base URL (https://…/v1)")
    if b.kind not in ("baseline", "solver") and not (b.model or "").strip():
        raise HTTPException(400, "the model name (the Hugging Face model deployed, or its served name)")


@router.get("/api/ai/models")
def models(request: Request):
    _staff(request)
    return [_model_row(r) for r in db.q("SELECT * FROM ai_models ORDER BY id")]


@router.post("/api/ai/models")
def model_add(b: ModelIn, request: Request):
    u = _staff(request)
    _check_model(b)
    mid = db.x("INSERT INTO ai_models (name, kind, endpoint_id, base_url, model, api_key, params, created_by, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (b.name.strip()[:80], b.kind, (b.endpoint_id or "").strip(), (b.base_url or "").strip(),
                                             (b.model or "").strip(), _enc(b.api_key or ""), json.dumps(b.params or {}), u["id"], time.time()))
    return _model(mid)


@router.put("/api/ai/models/{mid}")
def model_edit(mid: int, b: ModelIn, request: Request):
    _staff(request)
    _model(mid)
    _check_model(b)
    db.x("UPDATE ai_models SET name=?, kind=?, endpoint_id=?, base_url=?, model=?, params=? WHERE id=?",
         (b.name.strip()[:80], b.kind, (b.endpoint_id or "").strip(), (b.base_url or "").strip(), (b.model or "").strip(),
          json.dumps(b.params or {}), mid))
    if b.api_key is not None:
        db.x("UPDATE ai_models SET api_key=? WHERE id=?", (_enc(b.api_key), mid))
    return _model(mid)


@router.delete("/api/ai/models/{mid}")
def model_del(mid: int, request: Request):
    _staff(request)
    db.x("DELETE FROM ai_models WHERE id=?", (mid,))
    return {"deleted": mid}


@router.post("/api/ai/models/{mid}/test")
def model_test(mid: int, request: Request):
    """Check the connection: the models served, and one short answer (with its latency)."""
    from ..ailab import agents
    _staff(request)
    return agents.test(_model(mid, secret=True))


# ------------------------------------------------------------------ agent profiles
class AgentIn(BaseModel):
    name: str
    config: dict = {}


def _agent_defaults() -> dict:
    from ..ailab import agents, observe
    return {"system_prompt": agents.DEFAULT_SYSTEM_PROMPT, "interval_s": 30, "event_triggers": True, "history": 2,
            "max_actions": 30, "briefing": True, "queries": True, "query_rounds": 3, "rules": True, "correction_rounds": 1, "observation": dict(observe.DEFAULTS)}


@router.get("/api/ai/agents")
def agents_list(request: Request):
    _staff(request)
    rows = db.q("SELECT * FROM ai_agents ORDER BY id")
    if not rows:
        db.x("INSERT INTO ai_agents (name, config, created_by, created_at) VALUES (?,?,?,?)",
             ("Default controller", json.dumps(_agent_defaults()), None, time.time()))
        rows = db.q("SELECT * FROM ai_agents ORDER BY id")
    from ..ailab import agents as _ag

    def cfg(r):
        c = {**_agent_defaults(), **json.loads(r["config"])}
        c["system_prompt"] = _ag.system_prompt(c.get("system_prompt"))
        if int(c.get("max_actions") or 0) == 12:          # the former default: too few with ten aircraft calling
            c["max_actions"] = 30
        return c
    return {"agents": [{"id": r["id"], "name": r["name"], "config": cfg(r)} for r in rows],
            "defaults": _agent_defaults()}


@router.post("/api/ai/agents")
def agent_add(b: AgentIn, request: Request):
    u = _staff(request)
    aid = db.x("INSERT INTO ai_agents (name, config, created_by, created_at) VALUES (?,?,?,?)",
               (b.name.strip()[:80] or "Agent", json.dumps({**_agent_defaults(), **b.config}), u["id"], time.time()))
    return {"id": aid}


@router.put("/api/ai/agents/{aid}")
def agent_edit(aid: int, b: AgentIn, request: Request):
    _staff(request)
    db.x("UPDATE ai_agents SET name=?, config=? WHERE id=?", (b.name.strip()[:80] or "Agent", json.dumps({**_agent_defaults(), **b.config}), aid))
    return {"id": aid}


@router.delete("/api/ai/agents/{aid}")
def agent_del(aid: int, request: Request):
    _staff(request)
    db.x("DELETE FROM ai_agents WHERE id=?", (aid,))
    return {"deleted": aid}


# ------------------------------------------------------------------ runs
class RunIn(BaseModel):
    scenarios: list
    model_id: int
    agent_id: int
    repeat: int = 1
    env: dict = {}                     # auto_wx, readback_rate, force, end_s
    name: Optional[str] = None


def _run_dir(rid: str) -> str:
    return os.path.join(RUNS, rid)


def _progress(rid: str) -> dict:
    try:
        with open(os.path.join(_run_dir(rid), "progress.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _row(r: dict, full: bool = False) -> dict:
    out = {k: r[k] for k in ("id", "batch", "created_at", "status", "scenario", "model_name", "agent_name", "exercise_id",
                             "score", "error", "finished_at")}
    out["env"] = json.loads(r["env"] or "{}")
    if r["status"] in ("queued", "running"):
        p = _progress(r["id"])
        out["progress"] = {k: p.get(k) for k in ("status", "t", "duration", "decisions", "errors", "last", "thinking", "querying", "stop_reason")}
    res = json.loads(r["result"]) if r["result"] else None
    if res:
        out["evaluation"] = res.get("evaluation")
        out["preview"] = res.get("preview")
        if full:
            out["report"] = res.get("report")
    out["judge"] = json.loads(r["judge"]) if r.get("judge") else None
    return out


@router.get("/api/ai/runs")
def runs(request: Request, limit: int = 200):
    _staff(request)
    _sweep()
    return [_row(r) for r in db.q("SELECT * FROM ai_runs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 1000)),))]


@router.post("/api/ai/runs")
def run_new(b: RunIn, request: Request):
    """Queue runs: every scenario × repeat, with this model and agent profile and environment."""
    from ..scenario import load_scenario
    u = _staff(request)
    m = _model(b.model_id)
    ag = next((a for a in agents_list(request)["agents"] if a["id"] == b.agent_id), None)
    if ag is None:
        raise HTTPException(404, "no such agent profile")
    if not b.scenarios:
        raise HTTPException(400, "choose at least one scenario")
    n = max(1, min(int(b.repeat), 50))
    batch = secrets.token_hex(4)
    out = []
    for name in b.scenarios:
        if db.scenario_access(name, u) is None and db.q1("SELECT 1 FROM scenario_meta WHERE name=? AND owner_id IS NOT NULL", (name,)):
            raise HTTPException(403, f"no access to {name}")
        load_scenario(name)
        for _ in range(n):
            rid, eid = secrets.token_hex(6), secrets.token_hex(4)
            now = time.time()
            db.x("INSERT INTO ai_runs (id, batch, created_by, created_at, status, scenario, model_id, agent_id, model_name, agent_name, env, exercise_id) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (rid, batch, u["id"], now, "queued", name, m["id"], ag["id"],
                                                       m["name"], ag["name"], json.dumps(b.env or {}), eid))
            label = f"AI · {m['name']} · {name}"
            db.x("INSERT INTO exercises (id, name, scenario, instructor_id, created_at, ended_at, status, atc_token, pilot_token, "
                 "observer_token, scn_info, multi, solo, ai) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (eid, label, name, u["id"], now, now, "ended", secrets.token_urlsafe(12), secrets.token_urlsafe(12),
                  secrets.token_urlsafe(12), db.dumps(db.scenario_snapshot(name)), 0, 1, 1))
            out.append(rid)
    _sweep()
    return {"batch": batch, "runs": out}


def _start(r: dict) -> None:
    from ..scenario import load_scenario
    rid = r["id"]
    d = _run_dir(rid)
    os.makedirs(d, exist_ok=True)
    m = _model(r["model_id"], secret=True)
    ag = db.q1("SELECT * FROM ai_agents WHERE id=?", (r["agent_id"],))
    cfg = {**_agent_defaults(), **(json.loads(ag["config"]) if ag else {})}
    job = {"run_id": rid, "dir": d, "exercise_id": r["exercise_id"], "scenario": r["scenario"], "scn": load_scenario(r["scenario"]),
           "archive_dir": os.path.join(config.DATA_DIR, "debriefs", r["exercise_id"], "orig"),
           "provider": m, "agent": cfg, "env": json.loads(r["env"] or "{}"), "actor_name": f"AI · {m['name']}",
           "label": f"AI · {m['name']} · {r['scenario']}"}
    jp = os.path.join(d, "job.json")
    with open(jp, "w") as f:
        json.dump(job, f)
    os.chmod(jp, 0o600)                   # it holds the API key while the run goes; removed at the end
    with open(os.path.join(d, "agent.json"), "w") as f:
        json.dump(cfg, f)
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=root_dir + os.pathsep + os.environ.get("PYTHONPATH", ""))
    err = open(os.path.join(d, "stderr.txt"), "w")
    _procs[rid] = subprocess.Popen([sys.executable, "-m", "atcsim.ailab.runner", jp], stdout=subprocess.DEVNULL, stderr=err,
                                   env=env, cwd=root_dir)
    db.x("UPDATE ai_runs SET status='running' WHERE id=?", (rid,))


def _finish(r: dict) -> None:
    rid, d = r["id"], _run_dir(r["id"])
    p = _progress(rid)
    try:
        os.remove(os.path.join(d, "job.json"))
    except OSError:
        pass
    res = None
    try:
        with open(os.path.join(d, "result.json")) as f:
            res = json.load(f)
    except (OSError, ValueError):
        pass
    if res:
        ev = res["evaluation"]
        db.x("UPDATE ai_runs SET status=?, score=?, result=?, finished_at=?, error=? WHERE id=?",
             ("stopped" if res.get("stopped") else "done", ev["score"], json.dumps(res), time.time(), p.get("stop_reason"), rid))
        db.x("UPDATE exercises SET metrics=?, sim_time_s=?, debrief=?, ended_at=? WHERE id=?",
             (json.dumps(res.get("metrics") or {}), ev.get("sim_time_s"), json.dumps(res.get("report")) if res.get("report") else None,
              time.time(), r["exercise_id"]))
    else:
        db.x("UPDATE ai_runs SET status='failed', error=?, finished_at=? WHERE id=?",
             ((p.get("error") or "the run stopped") + "", time.time(), rid))


def _sweep() -> None:
    """Start queued runs (up to PARALLEL at a time) and collect the finished ones."""
    _db()
    with _lock:
        for r in db.q("SELECT * FROM ai_runs WHERE status='running'"):
            pr = _procs.get(r["id"])
            if pr is None or pr.poll() is not None:     # finished (or the server restarted while it ran)
                _procs.pop(r["id"], None)
                if pr is None and _progress(r["id"]).get("status") not in ("done", "failed"):
                    db.x("UPDATE ai_runs SET status='failed', error='the server restarted during the run' WHERE id=?", (r["id"],))
                    continue
                _finish(r)
        running = sum(1 for p in _procs.values() if p.poll() is None)
        for r in db.q("SELECT * FROM ai_runs WHERE status='queued' ORDER BY created_at LIMIT ?", (max(0, PARALLEL - running),)):
            try:
                _start(r)
            except Exception as e:  # noqa: BLE001
                db.x("UPDATE ai_runs SET status='failed', error=? WHERE id=?", (f"{type(e).__name__}: {e}", r["id"]))


def _sweeper() -> None:
    while True:
        time.sleep(2)
        try:
            if _ready:
                _sweep()
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_sweeper, daemon=True, name="ai-sweeper").start()


@router.get("/api/ai/runs/{rid}")
def run_get(rid: str, request: Request):
    _staff(request)
    r = db.q1("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "no such run")
    out = _row(r, full=True)
    try:
        with open(os.path.join(_run_dir(rid), "agent.json")) as f:
            out["agent"] = json.load(f)
    except (OSError, ValueError):
        pass
    return out


@router.get("/api/ai/runs/{rid}/steps")
def run_steps(rid: str, request: Request, offset: int = 0, limit: int = 50):
    """The decisions of a run (labelled once it is evaluated)."""
    _staff(request)
    d = _run_dir(rid)
    p = os.path.join(d, "experiences.jsonl")
    if not os.path.exists(p):
        p = os.path.join(d, "steps.jsonl")
    if not os.path.exists(p):
        return {"total": 0, "steps": []}
    with open(p) as f:
        lines = f.readlines()
    out = []
    for line in lines[offset:offset + max(1, min(limit, 200))]:
        try:
            s = json.loads(line)
        except ValueError:
            continue
        s.pop("system_prompt", None)
        out.append(s)
    return {"total": len(lines), "steps": out}


@router.post("/api/ai/runs/{rid}/stop")
def run_stop(rid: str, request: Request):
    _staff(request)
    r = db.q1("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "no such run")
    if r["status"] == "queued":
        db.x("UPDATE ai_runs SET status='cancelled', finished_at=? WHERE id=?", (time.time(), rid))
    elif r["status"] == "running":
        open(os.path.join(_run_dir(rid), "stop"), "w").close()      # ends at the next second, still evaluated
        pr = _procs.get(rid)

        def interrupt():                                # in a model call (it may wait minutes): interrupt it
            time.sleep(2)
            if pr is not None and pr.poll() is None:
                pr.terminate()                          # SIGTERM: the runner leaves the call and evaluates
                try:
                    pr.wait(30)
                except subprocess.TimeoutExpired:
                    pr.kill()
        threading.Thread(target=interrupt, daemon=True).start()
    return {"id": rid}


@router.delete("/api/ai/runs/{rid}")
def run_del(rid: str, request: Request):
    _staff(request)
    r = db.q1("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "no such run")
    if r["status"] == "running":
        raise HTTPException(409, "stop it first")
    shutil.rmtree(_run_dir(rid), ignore_errors=True)
    shutil.rmtree(os.path.join(config.DATA_DIR, "debriefs", r["exercise_id"]), ignore_errors=True)
    db.x("DELETE FROM ai_runs WHERE id=?", (rid,))
    db.x("DELETE FROM exercises WHERE id=? AND ai=1", (r["exercise_id"],))
    return {"deleted": rid}


class JudgeIn(BaseModel):
    model_id: int
    every: int = 1                     # judge every n-th decision (cost)


@router.post("/api/ai/runs/{rid}/judge")
def run_judge(rid: str, b: JudgeIn, request: Request):
    """A second model grades each decision (1-5, with a critique), from what it saw, what it did and what followed."""
    _staff(request)
    r = db.q1("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if r is None or r["status"] not in ("done", "stopped"):
        raise HTTPException(409, "the run is not finished")
    m = _model(b.model_id, secret=True)
    if m["kind"] in ("baseline", "solver"):
        raise HTTPException(400, "the judge is a language model")
    db.x("UPDATE ai_runs SET judge=? WHERE id=?", (json.dumps({"status": "running", "model": m["name"], "done": 0}), rid))
    threading.Thread(target=_judge, args=(rid, m, max(1, b.every)), daemon=True).start()
    return {"id": rid, "judging": True}


def _judge(rid: str, m: dict, every: int) -> None:
    from ..ailab import agents, evaluate
    p = os.path.join(_run_dir(rid), "experiences.jsonl")
    steps = [json.loads(x) for x in open(p)]
    cm = agents.ChatModel(m)
    grades = []
    for i, s in enumerate(steps):
        if i % every:
            continue
        after = "; ".join(s.get("label_why") or []) or "nothing notable in the following minutes"
        try:
            text, _, _ = cm.chat(evaluate.judge_messages(s, after))
            d = json.loads(text[text.find("{"):text.rfind("}") + 1])
            s["judge"] = {"grade": int(d.get("grade")), "critique": str(d.get("critique", ""))[:600], "model": m["name"]}
            grades.append(s["judge"]["grade"])
        except Exception as e:  # noqa: BLE001
            s["judge"] = {"error": str(e)[:200], "model": m["name"]}
        db.x("UPDATE ai_runs SET judge=? WHERE id=?", (json.dumps({"status": "running", "model": m["name"], "done": i + 1,
                                                                     "total": len(steps)}), rid))
    with open(p + ".tmp", "w") as f:
        for s in steps:
            f.write(json.dumps(s) + "\n")
    os.replace(p + ".tmp", p)
    db.x("UPDATE ai_runs SET judge=? WHERE id=?", (json.dumps({"status": "done", "model": m["name"], "graded": len(grades),
                                                                 "mean_grade": round(sum(grades) / len(grades), 2) if grades else None}), rid))


# ------------------------------------------------------------------ experiences and datasets
def _experiences(filters: dict):
    """Every decision of the finished runs matching the filters (scenario, model, min score, labels, judge grade)."""
    rows = db.q("SELECT * FROM ai_runs WHERE status IN ('done', 'stopped') ORDER BY created_at")
    sc, mo = set(filters.get("scenarios") or []), set(filters.get("models") or [])
    min_score = filters.get("min_score")
    labels = set(filters.get("labels") or [])
    min_grade = filters.get("min_grade")
    for r in rows:
        if (sc and r["scenario"] not in sc) or (mo and r["model_name"] not in mo):
            continue
        if min_score is not None and (r["score"] or 0) < float(min_score):
            continue
        p = os.path.join(_run_dir(r["id"]), "experiences.jsonl")
        if not os.path.exists(p):
            continue
        with open(p) as f:
            steps = [json.loads(x) for x in f]
        yield r, [s for s in steps if (not labels or s.get("label") in labels)
                  and (min_grade is None or ((s.get("judge") or {}).get("grade") or 0) >= float(min_grade))]


@router.post("/api/ai/experiences/stats")
def exp_stats(filters: dict, request: Request):
    _staff(request)
    runs_n, n, by = 0, 0, {"good": 0, "neutral": 0, "bad": 0}
    for r, steps in _experiences(filters or {}):
        runs_n += 1
        n += len(steps)
        for s in steps:
            by[s.get("label") or "neutral"] = by.get(s.get("label") or "neutral", 0) + 1
    return {"runs": runs_n, "decisions": n, "by_label": by}


class DatasetIn(BaseModel):
    name: str
    format: str = "chat"               # chat (fine-tuning: messages) | trajectories (whole runs with rewards) | steps (raw)
    filters: dict = {}


def _chat_record(s: dict) -> dict:
    from ..ailab import agents
    msgs = agents.messages(s.get("system_prompt") or agents.DEFAULT_SYSTEM_PROMPT, s["observation"], [], s.get("feedback"))
    for rd in s.get("queries") or []:                 # the questions asked and the simulator's answers, in order
        if rd.get("followup"):
            msgs.append({"role": "assistant", "content": rd.get("response") or ""})
            msgs.append({"role": "user", "content": rd["followup"]})
    msgs.append({"role": "assistant", "content": s.get("response") or ""})
    return {"messages": msgs, "meta": {"run": s.get("run_id"), "t": s["t"], "scenario": s.get("scenario"), "label": s.get("label"),
                                       "reward": s.get("reward"), "episode_score": s.get("episode_score"),
                                       "judge_grade": (s.get("judge") or {}).get("grade")}}


@router.post("/api/ai/datasets")
def dataset_new(b: DatasetIn, request: Request):
    """Export the experiences matching the filters as a JSONL file:
    chat: one fine-tuning example per decision ({"messages": [system, user, assistant]}, plus its labels in "meta");
    trajectories: one line per run (every decision with its reward, the run's score and penalties);
    steps: the decisions as recorded (situation structure, answer, results, labels)."""
    u = _staff(request)
    if b.format not in ("chat", "trajectories", "steps"):
        raise HTTPException(400, "format: chat, trajectories or steps")
    os.makedirs(SETS, exist_ok=True)
    did, tok = secrets.token_hex(5), secrets.token_urlsafe(24)
    path = os.path.join(SETS, did + ".jsonl")
    n, nr = 0, 0
    with open(path, "w") as f:
        for r, steps in _experiences(b.filters or {}):
            if not steps:
                continue
            nr += 1
            if b.format == "trajectories":
                res = json.loads(r["result"]) if r["result"] else {}
                f.write(json.dumps({"run": r["id"], "scenario": r["scenario"], "model": r["model_name"], "score": r["score"],
                                    "evaluation": res.get("evaluation"), "steps": [
                                        {k: s.get(k) for k in ("t", "observation", "feedback", "response", "results", "reward", "label", "judge")}
                                        for s in steps]}) + "\n")
                n += len(steps)
            else:
                for s in steps:
                    f.write(json.dumps(_chat_record(s) if b.format == "chat" else s) + "\n")
                    n += 1
    db.x("INSERT INTO ai_datasets (id, name, created_by, created_at, format, filters, records, runs, token) VALUES (?,?,?,?,?,?,?,?,?)",
         (did, b.name.strip()[:80] or f"dataset {did}", u["id"], time.time(), b.format, json.dumps(b.filters or {}), n, nr, tok))
    return _dataset(did, request)


def _dataset(did: str, request: Request) -> dict:
    r = db.q1("SELECT * FROM ai_datasets WHERE id=?", (did,))
    if r is None:
        raise HTTPException(404, "no such dataset")
    base = config.PUBLIC_URL or str(request.base_url).rstrip("/")
    p = os.path.join(SETS, did + ".jsonl")
    return {"id": did, "name": r["name"], "created_at": r["created_at"], "format": r["format"], "filters": json.loads(r["filters"] or "{}"),
            "records": r["records"], "runs": r["runs"], "bytes": os.path.getsize(p) if os.path.exists(p) else 0,
            "download": f"/api/ai/datasets/{did}/file", "link": f"{base}/ai-data/{did}/{r['token']}.jsonl"}


@router.get("/api/ai/datasets")
def datasets(request: Request):
    _staff(request)
    return [_dataset(r["id"], request) for r in db.q("SELECT id FROM ai_datasets ORDER BY created_at DESC")]


@router.get("/api/ai/datasets/{did}/file")
def dataset_file(did: str, request: Request):
    _staff(request)
    p = os.path.join(SETS, did + ".jsonl")
    if not os.path.exists(p):
        raise HTTPException(404, "no such dataset")
    return FileResponse(p, media_type="application/jsonl", filename=f"atcsim_{did}.jsonl")


@router.get("/ai-data/{did}/{token}.jsonl", include_in_schema=False)
def dataset_link(did: str, token: str):
    """The dataset for a training machine (a RunPod pod: wget / curl this link). Whoever has the link can read it."""
    _db()
    r = db.q1("SELECT * FROM ai_datasets WHERE id=?", (did,))
    if r is None or not secrets.compare_digest(token, r["token"] or ""):
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(os.path.join(SETS, did + ".jsonl"), media_type="application/jsonl")


@router.delete("/api/ai/datasets/{did}")
def dataset_del(did: str, request: Request):
    _staff(request)
    try:
        os.remove(os.path.join(SETS, did + ".jsonl"))
    except OSError:
        pass
    db.x("DELETE FROM ai_datasets WHERE id=?", (did,))
    return {"deleted": did}


@router.get("/ai", include_in_schema=False)
def ai_page(request: Request):
    from .exercises import page
    _staff(request)
    return page("ai.html")



# ------------------------------------------------------------------ watch a run live
WATCH_FACTORS = (0, 1, 2, 4, 8, 16)          # 0: full speed (the radar jumps); else simulated seconds per second
WATCHERS: dict = {}                           # sid -> {"rid", "touch"}
_wlock = threading.Lock()


def watch_sid(eid: str) -> str:
    return eid + "ff"


def watch_lookup(sid: str) -> Optional[dict]:
    """A watch session id (<exercise>ff): {"eid", "rid"} when it is open, else None."""
    if len(sid) != 10 or not sid.endswith("ff"):
        return None
    return WATCHERS.get(sid)


def _watch_session(rid: str, r: dict):
    from ..session import Session
    from . import debriefs

    class WatchSession(Session):
        """The radar of a running AI run: the world the runner publishes (live.snap), read-only."""

        def __init__(self):
            from ..scenario import load_scenario
            super().__init__(r["scenario"], mode="realtime", autostart=False, sid=watch_sid(r["exercise_id"]),
                             scn=load_scenario(r["scenario"]), label=f"AI run {rid} (live)")
            self.solo = True
            self.status = "paused"
            self.version = 0
            self._mt = None
            self._live = os.path.join(_run_dir(rid), "live.snap")
            threading.Thread(target=self._follow, daemon=True, name=f"watch-{rid}").start()

        def _snapshot(self) -> None:
            pass

        def _maybe_snapshot(self) -> None:
            pass

        def _persist(self, rewrite: bool = False) -> None:
            pass

        def _follow(self) -> None:
            while not self._stop.is_set():
                try:
                    mt = os.stat(self._live).st_mtime_ns
                    if mt != self._mt:
                        with open(self._live, "rb") as f:
                            blob = f.read()
                        w = self._load_world(blob)
                        with self.lock:
                            self.world = w
                            self.status = "finished" if w.finished else "running"
                            self.version += 1
                        self._mt = mt
                except (OSError, EOFError, ValueError):
                    pass
                except Exception:  # noqa: BLE001 - a half-written file: the next one
                    pass
                self._stop.wait(0.2)

    sid = watch_sid(r["exercise_id"])
    with _wlock:
        s = debriefs.manager.sessions.get(sid)
        if s is None:
            s = WatchSession()
            with debriefs.manager.lock:
                debriefs.manager.sessions[sid] = s
        WATCHERS[sid] = {"rid": rid, "eid": r["exercise_id"], "touch": time.time()}
    return s


def _watch_gc() -> None:
    from . import debriefs
    while True:
        time.sleep(15)
        now = time.time()
        with _wlock:
            for sid, o in list(WATCHERS.items()):
                if now - o["touch"] > 60:
                    WATCHERS.pop(sid, None)
                    with debriefs.manager.lock:
                        s = debriefs.manager.sessions.pop(sid, None)
                    if s:
                        s.stop()


threading.Thread(target=_watch_gc, daemon=True, name="ai-watch-gc").start()


class WatchIn(BaseModel):
    factor: float = 4


@router.post("/api/ai/runs/{rid}/watch")
def run_watch(rid: str, b: WatchIn, request: Request):
    """Open (or keep open: call it every few seconds) the live view of a run; the run is paced to factor
    simulated seconds per second while somebody watches (0: full speed). Returns the session to show on the
    radar, the progress, and the latest decisions."""
    _staff(request)
    r = db.q1("SELECT * FROM ai_runs WHERE id=?", (rid,))
    if r is None:
        raise HTTPException(404, "no such run")
    if r["status"] in ("queued", "running"):
        with open(os.path.join(_run_dir(rid), "watch.json.tmp"), "w") as f:
            json.dump({"factor": max(0.0, min(float(b.factor), 64.0)), "until": time.time() + 10}, f)
        os.replace(os.path.join(_run_dir(rid), "watch.json.tmp"), os.path.join(_run_dir(rid), "watch.json"))
    s = _watch_session(rid, r)
    st = run_steps(rid, request, offset=max(0, _count_steps(rid) - 8), limit=8)
    return {"sid": s.id, "status": r["status"], "name": f"{r['model_name']} · {r['scenario']}", "exercise_id": r["exercise_id"],
            "progress": _progress(rid), "steps": [{k: x.get(k) for k in ("i", "t", "reasoning", "results", "error", "parse_error", "queries",
                                                                          "latency_s", "label", "reward")} for x in st["steps"]],
            "factors": WATCH_FACTORS, "score": r["score"], "live": os.path.exists(os.path.join(_run_dir(rid), "live.snap"))}


def _count_steps(rid: str) -> int:
    p = os.path.join(_run_dir(rid), "steps.jsonl")
    try:
        with open(p, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


@router.get("/ai/watch/{rid}", include_in_schema=False)
def watch_page(rid: str, request: Request):
    from .exercises import page
    _staff(request)
    return page("radar.html")
