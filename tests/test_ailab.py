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

    import httpx
    cmq = agents.ChatModel({"kind": "openai", "base_url": f"http://127.0.0.1:{PORT}/v1", "model": "Qwen/Qwen3-8B", "api_key": "k", "params": {}})
    cmq.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": 'Reply with {"ok": true}'}])
    lb = httpx.get(f"http://127.0.0.1:{PORT}/last").json()
    ok((lb.get("chat_template_kwargs") or {}).get("enable_thinking") is False and lb["messages"][0]["content"].endswith("/no_think"),
       "thinking off by default: enable_thinking=false (and /no_think for Qwen3)")
    strict = subprocess.Popen([sys.executable, "-m", "tests.mock_llm", str(PORT + 2)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              env=dict(os.environ, MOCK_STRICT="1"))
    try:
        time.sleep(2.5)
        cms = agents.ChatModel({"kind": "openai", "base_url": f"http://127.0.0.1:{PORT + 2}/v1", "model": "Qwen3.8-27B", "api_key": "k",
                                "params": {"json_mode": True, "retries": 0}})
        txt_s, _, _ = cms.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": 'Reply with {"ok": true}'}])
        lb = httpx.get(f"http://127.0.0.1:{PORT + 2}/last").json()
        ok('"ok"' in txt_s and "chat_template_kwargs" not in lb and "response_format" not in lb and lb["messages"][0]["content"].endswith("/no_think")
           and cms._plain, "a server that refuses the optional fields (502): sent again without them, and so from then on")
    finally:
        strict.kill()
    from atcsim.ailab import actions as A0
    acts0, _, err0 = A0.parse('<think>{"actions": [{"type": "nope"}]} long thoughts</think>\n{"reasoning": "r", "actions": [{"type": "assume", "callsign": "X1"}]}')
    ok(not err0 and acts0 == [{"type": "assume", "callsign": "X1"}], "a reasoning model's <think> part is not taken for the answer")

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
    ok(any((s.get("parse_error") or s.get("unreadable_first")) and "-2 unreadable answer" in s["label_why"] for s in s0), "an unreadable answer: penalised (even once corrected)")
    ok(any("+1 assumed" in s["label_why"] for s in s0) and any("answered" in " ".join(s["label_why"]) for s in s0), "assuming who calls, answering requests: rewarded")
    ok(any(any("left alone, lost" in w for w in s["label_why"]) and s["label"] == "bad" for s in s0), "a conflict shown and left alone that became a loss: bad")
    ok(any("RESULT OF YOUR LAST INSTRUCTIONS" in (s.get("feedback") or "") or s.get("feedback") for s in s0[1:]), "the pilots' answers fed back to the model")
    ok("api_key" not in json.dumps(ins.get(f"/api/ai/runs/{m1['id']}").json()), "no key in a run's details")
    qd = [q for s in s0 for q in s.get("queries") or [] if q.get("kind") != "correction" and q.get("results")]
    ok(qd and any("NM apart" in x["result"] for x in qd[0]["results"]) and any(not x["ok"] for x in qd[0]["results"]),
       f"queries: the model asked, the simulator answered (geometry, levels, probe, route; an unknown one refused), then it decided ({len(qd)} decisions)")
    ok("TO DO NOW" in s0[3]["observation"] and "at (" in s0[3]["observation"], "the situation starts with TO DO NOW, with positions")
    fc, xf = ev["first_calls"], ev["transfers"]
    ok(fc["calls"] > 0 and fc["answered"] == fc["calls"], f"first calls answered: {fc['answered']}/{fc['calls']}, mean wait {fc['mean_wait_s']} s")
    ok(xf["left"] > 0 and xf["never"] == 0, f"aircraft that left were transferred: {xf['left'] - xf['never']}/{xf['left']}")
    ok(any(any(w.endswith("transferred") for w in s["label_why"]) for s in s0), "transferring what left: rewarded")
    ok(any("LEFT YOUR SECTOR" in (s.get("observation") or "") for s in s0), "the model is shown the aircraft that left, to transfer")
    from atcsim.ailab import actions as A
    from atcsim.session import Session
    ss = Session("p02_wn_merges_and_climbs", mode="stepped", autostart=False)
    ss.stop(); ss.solo = True
    while not any(a.on_frequency and not a.assumed and a.active for a in ss.world.aircraft.values()):
        ss._tick()
    cs = next(a.callsign for a in ss.world.aircraft.values() if a.on_frequency and not a.assumed and a.active)
    rr = A.apply(ss.world, [{"type": "clear", "callsign": cs, "cfl": 360}], {"name": "AI", "seat": "atc"})
    ok(not rr[0]["ok"] and '"type": "assume"' in rr[0]["text"], "clearing an aircraft not assumed: the refusal says to assume it first")
    rr = A.apply(ss.world, [{"type": "assume", "callsign": cs}, {"type": "clear", "callsign": cs, "heading": 200, "distance": "8NM"}], {"name": "AI", "seat": "atc"})
    ok(rr[0]["ok"] and rr[1]["ok"] and "'distance' is not a field" in rr[1]["text"], "assume + clear in one answer; an unknown field reported")
    acx = ss.world.aircraft[cs]
    wrong = next(f for f in (360, 350, 340, 370) if not __import__("atcsim.aircraft", fromlist=["x"]).level_parity_ok(f, acx.southbound))
    rr = A.apply(ss.world, [{"type": "clear", "callsign": cs, "cfl": wrong}], {"name": "AI", "seat": "atc"})
    ok(not rr[0]["ok"] and "semicircular rule" in rr[0]["text"], f"a level of the wrong direction is refused: {rr[0]['text'][:90]}")
    rr = A.apply(ss.world, [{"type": "clear", "callsign": cs, "cfl": wrong}], {"name": "AI", "seat": "atc"}, rules=False)
    ok(rr[0]["ok"], "... unless the profile's rules are off")
    ss.stop()

    # ---- watch live (read-only)
    wv = ins.post(f"/api/ai/runs/{m1['id']}/watch", json={"factor": 4}).json()
    ok(wv["sid"].endswith("ff") and ins.get(f"/sessions/{wv['sid']}/state").status_code == 200, "watch: a radar session on the run")
    ok(ins.post(f"/sessions/{wv['sid']}/control", json={"action": "resume"}).status_code == 403 and
       ins.post(f"/sessions/{wv['sid']}/assume/{cs}").status_code == 403, "watch: look only")
    ok(atc.post(f"/api/ai/runs/{m1['id']}/watch", json={}).status_code == 403 and atc.get(f"/sessions/{wv['sid']}/state").status_code == 403,
       "watch: staff only")
    ok(ins.get(f"/ai/watch/{m1['id']}").status_code == 200, "watch page")

    ok(any(s.get("missed") for s in s0) and any("LEFT UNDONE FROM YOUR TO DO" in (s.get("feedback") or "") for s in s0),
       "what the model left undone from TO DO is fed back")
    ok(all(s.get("prompt_tokens_est") for s in s0), "prompt size recorded per decision")
    cd = [s for s in s0 if s.get("corrections")]
    ok(cd and any(r.get("correction") for r in cd[0]["results"]) and any(q.get("kind") == "correction" for q in cd[0]["queries"]),
       f"a refused or unreadable answer is sent back for a correction in the same decision ({len(cd)} decisions)")
    small = ins.post("/api/ai/models", json={"name": "Mock small context", "kind": "openai", "base_url": f"http://127.0.0.1:{PORT}/v1",
                                             "model": "mock-controller", "api_key": "k", "params": {"timeout_s": 20, "retries": 1, "context_tokens": 7000}}).json()
    r3 = ins.post("/api/ai/runs", json={"scenarios": ["p02_wn_merges_and_climbs"], "model_id": small["id"], "agent_id": a0["id"],
                                        "env": {"end_s": 420, "auto_assume": True, "auto_transfer": True}}).json()["runs"][0]
    for _ in range(300):
        x3 = ins.get(f"/api/ai/runs/{r3}").json()
        if x3["status"] in ("done", "failed"):
            break
        time.sleep(1)
    s3 = ins.get(f"/api/ai/runs/{r3}/steps?limit=200").json()["steps"]
    ok(x3["status"] == "done" and any(s.get("trimmed") for s in s3) and max(s["prompt_tokens_est"] for s in s3) <= 7000 - 600,
       f"a small context: older decisions / queries left out to fit (max ~{max(s['prompt_tokens_est'] for s in s3)} tokens)")
    ok(not any("not assumed" in m or "not transferred" in m for s in s3 for m in s.get("missed") or [])
       and x3["evaluation"]["never_assumed"] == 0, "assume / transfer automatically: not asked of the model")
    ins.delete(f"/api/ai/runs/{r3}")

    # ---- stop during a long model call; a model that never answers stops the run by itself
    slow = subprocess.Popen([sys.executable, "-m", "tests.mock_llm", str(PORT + 1)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, MOCK_DELAY="120"))
    try:
        time.sleep(2)
        sm = ins.post("/api/ai/models", json={"name": "Slow", "kind": "openai", "base_url": f"http://127.0.0.1:{PORT + 1}/v1", "model": "mock-controller",
                                              "api_key": "k", "params": {"timeout_s": 300, "retries": 3}}).json()
        r4 = ins.post("/api/ai/runs", json={"scenarios": ["p02_wn_merges_and_climbs"], "model_id": sm["id"], "agent_id": a0["id"], "env": {"end_s": 600}}).json()["runs"][0]
        for _ in range(60):
            if (ins.get(f"/api/ai/runs/{r4}").json().get("progress") or {}).get("thinking"):
                break
            time.sleep(0.5)
        t_stop = time.time()
        ins.post(f"/api/ai/runs/{r4}/stop")
        for _ in range(120):
            x4 = ins.get(f"/api/ai/runs/{r4}").json()
            if x4["status"] not in ("running", "queued"):
                break
            time.sleep(0.5)
        ok(x4["status"] == "stopped" and time.time() - t_stop < 30, f"stop during a model call: stopped in {time.time() - t_stop:.0f} s, still evaluated")
        ins.delete(f"/api/ai/runs/{r4}")
        dead = ins.post("/api/ai/models", json={"name": "Dead", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "x",
                                                "api_key": "k", "params": {"timeout_s": 5, "retries": 0}}).json()
        r5 = ins.post("/api/ai/runs", json={"scenarios": ["p02_wn_merges_and_climbs"], "model_id": dead["id"], "agent_id": a0["id"], "env": {"end_s": 2400}}).json()["runs"][0]
        for _ in range(240):
            x5 = ins.get(f"/api/ai/runs/{r5}").json()
            if x5["status"] not in ("running", "queued"):
                break
            time.sleep(0.5)
        ok(x5["status"] == "failed" and "before the start" in (x5.get("error") or ""),
           f"a model that does not answer: the run ends before the simulation starts ({x5.get('error')[:80]})")
        ins.delete(f"/api/ai/runs/{r5}")
    finally:
        slow.kill()

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
    da = ins.post("/api/ai/datasets", json={"name": "all chat", "format": "chat", "filters": {"labels": ["good", "neutral", "bad"]}}).json()
    recs = [json.loads(x) for x in ins.get(da["download"]).text.strip().splitlines()]
    ok(any("ANSWERS TO YOUR QUERIES" in m["content"] for r_ in recs for m in r_["messages"] if m["role"] == "user"),
       "chat dataset: the query rounds are in the conversations")
    ok(any("YOUR ANSWER HAD ERRORS" in m["content"] or "COULD NOT BE READ" in m["content"] for r_ in recs for m in r_["messages"] if m["role"] == "user"),
       "chat dataset: the corrections are in the conversations")
    ok(rec["messages"][0]["role"] == "system" and rec["messages"][-1]["role"] == "assistant" and rec["meta"]["label"] == "good", "chat format: system, situation, (queries,) answer (+ labels)")
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
