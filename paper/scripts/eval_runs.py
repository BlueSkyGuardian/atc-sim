"""Short headless runs: built-in baseline vs built-in solver, same agent profile, 20 simulated minutes."""
import json, os, sys, tempfile, time, warnings
warnings.filterwarnings("ignore")
os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-paper-")
os.environ["ATC_DEV_LOGIN"] = "1"; os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"; os.environ["ATC_AI_PARALLEL"] = "4"
sys.path.insert(0, "/home/claude/atc-repo"); os.chdir("/home/claude/atc-repo")
import shutil
shutil.copytree("/home/claude/paper/hjdata/hj", os.path.join(os.environ["ATC_DATA_DIR"], "hj"), dirs_exist_ok=True)
from fastapi.testclient import TestClient
from atcsim.server import app
def client(e):
    c = TestClient(app); assert c.post("/auth/dev", json={"email": e}).status_code == 200; return c
boss, ins = client("boss@gmail.com"), client("ins@gmail.com")
u = {x["email"]: x for x in boss.get("/api/users").json()}
boss.patch(f"/api/users/{u['ins@gmail.com']['id']}", json={"role": "instructor"})
ins.put("/api/me", json={"first_name": "I", "last_name": "X", "licence": "L"})
SC = sys.argv[1].split(",")
END = float(sys.argv[2]) if len(sys.argv) > 2 else 1200
models = {k: ins.post("/api/ai/models", json={"name": k, "kind": k}).json()["id"] for k in ("baseline", "solver")}
a0 = ins.get("/api/ai/agents").json()["agents"][0]
runs = {}
for k, mid in models.items():
    r = ins.post("/api/ai/runs", json={"scenarios": SC, "model_id": mid, "agent_id": a0["id"], "env": {"end_s": END}}).json()["runs"]
    for sc, rid in zip(SC, r):
        runs[rid] = (k, sc)
out = {}
t0 = time.time()
while len(out) < len(runs) and time.time() - t0 < 3000:
    for rid, (k, sc) in runs.items():
        if rid in out: continue
        x = ins.get(f"/api/ai/runs/{rid}").json()
        if x["status"] in ("done", "failed"):
            st = ins.get(f"/api/ai/runs/{rid}/steps?limit=1000").json()["steps"]
            out[rid] = {"kind": k, "scenario": sc, "status": x["status"], "score": x.get("score"), "evaluation": x.get("evaluation"),
                        "error": x.get("error"),
                        "n_steps": len(st), "decide_s": [s.get("latency_s") for s in st],
                        "clearances": [r_["action"] for s in st for r_ in (s.get("results") or []) if r_.get("ok") and r_["action"].get("type") == "clear"],
                        "grades": [g for s in st for g in (s.get("grades") or [])],
                        "reasons": [s.get("reasoning") for s in st if s.get("reasoning")]}
            print(k, sc, x["status"], x.get("score"), (x.get("evaluation") or {}).get("losses"), flush=True)
            json.dump(out, open(os.environ.get("OUT", "/home/claude/paper/data/runs.json"), "w"), indent=1, default=str)
    time.sleep(3)
print("done", time.time() - t0)
