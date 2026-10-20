"""AI lab: models (keys kept encrypted, never sent back), agent profiles, headless runs by a model (a stand-in
OpenAI-compatible server) and by the built-in baseline, evaluation and labels, the run replayed in the debrief,
datasets (chat / trajectories) and their link for a training machine.
   python -m tests.test_ailab   (uses a throw-away data folder; starts tests/mock_llm.py on a local port)"""
import json
import os
import subprocess
import sys
import tempfile
import time
import warnings

warnings.filterwarnings("ignore")
os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-ai-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"
PORT = 8791

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app  # noqa: E402


def client(email):
    c = TestClient(app)
    assert c.post("/auth/dev", json={"email": email}).status_code == 200
    return c


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


mock = subprocess.Popen([sys.executable, "-m", "tests.mock_llm", str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    boss, ins, atc, anon = client("boss@gmail.com"), client("ins@gmail.com"), client("atc@gmail.com"), TestClient(app)
    users = {u["email"]: u for u in boss.get("/api/users").json()}
    boss.patch(f"/api/users/{users['ins@gmail.com']['id']}", json={"role": "instructor"})
    for c, n in ((ins, "Ina"), (atc, "Alaa")):
        c.put("/api/me", json={"first_name": n, "last_name": "X", "licence": "L-" + n})
    ok(atc.get("/api/ai/models").status_code == 403 and ins.get("/ai").status_code == 200, "the AI lab is for instructors")

    # ---- models
    r = ins.post("/api/ai/models", json={"name": "RunPod Qwen", "kind": "runpod", "endpoint_id": "abc123", "model": "Qwen/Qwen2.5-7B-Instruct",
                                         "api_key": "rpa_SECRETKEY1234", "params": {"temperature": 0.1}}).json()
    ok(r["has_key"] and "api_key" not in r and r["key_hint"] == "…1234", "RunPod model saved, its key never sent back")
    import sqlite3
    raw = sqlite3.connect(os.path.join(os.environ["ATC_DATA_DIR"], "atcsim.db")).execute("SELECT api_key FROM ai_models").fetchone()[0]
    ok("SECRETKEY" not in raw, "the key is stored encrypted")
    from atcsim.ailab import agents
    ok(agents.base_url({"kind": "runpod", "endpoint_id": "abc123"}) == "https://api.runpod.ai/v2/abc123/openai/v1", "RunPod's OpenAI-compatible URL")
    ins.put(f"/api/ai/models/{r['id']}", json={"name": "RunPod Qwen 2", "kind": "runpod", "endpoint_id": "abc123", "model": "Qwen/Qwen2.5-7B-Instruct"})
    ok(ins.get("/api/ai/models").json()[0]["has_key"], "an update without a key keeps it")
    ok(ins.post("/api/ai/models", json={"name": "x", "kind": "openai", "base_url": "nope", "model": "m"}).status_code == 400, "a bad URL refused")
    mock_m = ins.post("/api/ai/models", json={"name": "Mock vLLM", "kind": "openai", "base_url": f"http://127.0.0.1:{PORT}/v1", "model": "mock-controller",
                                              "api_key": "k", "params": {"timeout_s": 20, "retries": 1}}).json()
    base_m = ins.post("/api/ai/models", json={"name": "Baseline", "kind": "baseline"}).json()
    for _ in range(40):
        t = ins.post(f"/api/ai/models/{mock_m['id']}/test").json()
        if t.get("ok"):
            break
        time.sleep(0.5)
    ok(t.get("ok") and t.get("models") == ["mock-controller"] and '"ok"' in t.get("answer", ""), f"connection test: models and an answer ({t.get('latency_s')} s)")

    # ---- agent profile
    ag = ins.get("/api/ai/agents").json()
    a0 = ag["agents"][0]
    ok(a0["name"] == "Default controller" and "assume" in a0["config"]["system_prompt"], "a default agent profile with the instruction set")
    ins.put(f"/api/ai/agents/{a0['id']}", json={"name": "Fast", "config": {**a0["config"], "interval_s": 45}})
    ok(ins.get("/api/ai/agents").json()["agents"][0]["config"]["interval_s"] == 45, "profile edited")

    # ---- runs
    r1 = ins.post("/api/ai/runs", json={"scenarios": ["p02_wn_merges_and_climbs"], "model_id": mock_m["id"], "agent_id": a0["id"],
                                        "env": {"end_s": 600}}).json()
    r2 = ins.post("/api/ai/runs", json={"scenarios": ["p02_wn_merges_and_climbs"], "model_id": base_m["id"], "agent_id": a0["id"],
                                        "repeat": 2, "env": {"end_s": 900}}).json()
    ok(len(r1["runs"]) == 1 and len(r2["runs"]) == 2, "runs queued (one by the model, two by the baseline)")
    seen_running = False
    for _ in range(600):
        runs = {x["id"]: x for x in ins.get("/api/ai/runs").json()}
        seen_running |= any(x["status"] == "running" and (x.get("progress") or {}).get("decisions") for x in runs.values())
        if all(runs[i]["status"] in ("done", "failed") for i in r1["runs"] + r2["runs"]):
            break
        time.sleep(1)
    m1, b1 = runs[r1["runs"][0]], runs[r2["runs"][0]]
    ok(seen_running, "progress while running (sim time, decisions)")
    ok(m1["status"] == "done" and b1["status"] == "done", f"runs finished (model {m1['score']}, baseline {b1['score']})")
    ev = m1["evaluation"]
    ok(ev["decisions"] >= 10 and ev["unreadable"] >= 1 and ev["refused"] >= 1, f"model run: {ev['decisions']} decisions, {ev['unreadable']} unreadable, {ev['refused']} refused")
    ok(b1["evaluation"]["losses"] == 0 and b1["score"] > m1["score"], "the baseline resolves the conflicts the lazy model leaves (higher score)")
    ok(m1["preview"]["tracks"] and m1["preview"]["outline"], "a preview of each run (tracks, sector)")
    st = ins.get(f"/api/ai/runs/{m1['id']}/steps?limit=200").json()
    s0 = st["steps"]
    ok(st["total"] == ev["decisions"] and all("label" in s and "reward" in s for s in s0), "every decision labelled and rewarded")
    ok(any(s.get("parse_error") and s["label"] == "bad" for s in s0), "an unreadable answer: labelled bad")
    ok(any("+1 assumed" in s["label_why"] for s in s0) and any("answered" in " ".join(s["label_why"]) for s in s0), "assuming who calls, answering requests: rewarded")
    ok(any(any("left alone, lost" in w for w in s["label_why"]) and s["label"] == "bad" for s in s0), "a conflict shown and left alone that became a loss: bad")
    ok(any("RESULT OF YOUR LAST INSTRUCTIONS" in (s.get("feedback") or "") or s.get("feedback") for s in s0[1:]), "the pilots' answers fed back to the model")
    ok("api_key" not in json.dumps(ins.get(f"/api/ai/runs/{m1['id']}").json()), "no key in a run's details")

    # ---- the run in the debrief
    eid = m1["exercise_id"]
    ok(all(x["id"] != eid for x in ins.get("/api/exercises").json()), "AI runs are not listed with the exercises")
    p = ins.post(f"/api/debriefs/{eid}/player").json()
    ins.post(f"/api/debriefs/{eid}/seek", json={"sid": p["sid"], "t": 300})
    stt = ins.get(f"/sessions/{p['sid']}/state").json()
    ok(stt["sim_time_s"] == 300 and any(a["assumed"] for a in stt["aircraft"]), "replayed in the debrief (the model's assumes are there)")

    # ---- judge (the stand-in answers garbage to the judge prompt: errors are recorded, the run stays usable)
    ins.post(f"/api/ai/runs/{m1['id']}/judge", json={"model_id": mock_m["id"], "every": 5})
    for _ in range(120):
        j = ins.get(f"/api/ai/runs/{m1['id']}").json()["judge"]
        if j and j.get("status") == "done":
            break
        time.sleep(0.5)
    ok(j and j["status"] == "done", "judge pass done")

    # ---- experiences and datasets
    s = ins.post("/api/ai/experiences/stats", json={"labels": ["good", "neutral", "bad"]}).json()
    ok(s["runs"] == 3 and s["decisions"] > 30, f"experiences: {s['decisions']} decisions in {s['runs']} runs {s['by_label']}")
    d = ins.post("/api/ai/datasets", json={"name": "good chat", "format": "chat", "filters": {"labels": ["good"]}}).json()
    ok(d["records"] == s["by_label"]["good"] and d["link"].endswith(".jsonl"), f"chat dataset: {d['records']} good decisions")
    lines = ins.get(d["download"]).text.strip().splitlines()
    rec = json.loads(lines[0])
    ok([m["role"] for m in rec["messages"]] == ["system", "user", "assistant"] and rec["meta"]["label"] == "good", "chat format: system, situation, answer (+ labels)")
    link = d["link"].split("://", 1)[1].split("/", 1)[1]
    ok(anon.get("/" + link).status_code == 200 and anon.get("/" + link.replace(".jsonl", "x.jsonl")).status_code == 404,
       "the link works without signing in (training machine), a wrong token does not")
    t = ins.post("/api/ai/datasets", json={"name": "traj", "format": "trajectories", "filters": {"models": ["Baseline"]}}).json()
    tr = [json.loads(x) for x in ins.get(t["download"]).text.strip().splitlines()]
    ok(len(tr) == 2 and tr[0]["steps"] and "reward" in tr[0]["steps"][0] and tr[0]["score"] is not None, "trajectories: whole runs with rewards (baseline only)")
    ok(anon.get(d["download"]).status_code in (401, 403), "the download itself needs an account")
    ins.delete(f"/api/ai/runs/{r2['runs'][1]}")
    ok(len(ins.get("/api/ai/runs").json()) == 2, "a run deleted")
    print("ai lab checks passed")
finally:
    mock.kill()
