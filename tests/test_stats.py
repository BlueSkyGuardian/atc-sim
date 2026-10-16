"""Evaluations and statistics: three exercises with the same controller and pseudo-pilot, evaluations both ways,
acknowledgement, instructor learning curve.   python -m tests.test_stats  [data folder to keep]"""
import os
import sys
import tempfile

os.environ["ATC_DATA_DIR"] = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="atcsim-stats-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"
from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app, manager  # noqa: E402
from atcsim.platform import db  # noqa: E402


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m)
    if not c:
        raise SystemExit(1)


def client(email, first, last, lic):
    c = TestClient(app)
    c.post("/auth/dev", json={"email": email})
    c.put("/api/me", json={"first_name": first, "last_name": last, "licence": lic})
    return c


boss = client("boss@gmail.com", "Big", "Boss", "ADM")
ins = client("ins@gmail.com", "Ina", "Structor", "I-1")
pupils = [client(f"atc{i}@gmail.com", f"Pupil{i}", "Controller", f"ATC-{i}") for i in range(2)]
pil = client("pil@gmail.com", "Pierre", "Lote", "PP-1")
for e, r in (("ins@gmail.com", "instructor"), ("pil@gmail.com", "pilot")):
    boss.patch(f"/api/users/{db.user_by_email(e)['id']}", json={"role": r})
pid = db.user_by_email("pil@gmail.com")["id"]
plan = [("s01_basic_crossing", 900), ("s02_climb_request_into_crossing", 900), ("s03_descent_through_opposite", 900)]
for k, (scn, secs) in enumerate(plan):
    for i, atc in enumerate(pupils):
        aid = db.user_by_email(f"atc{i}@gmail.com")["id"]
        ex = ins.post("/api/exercises", json={"scenario": scn, "name": f"Lesson {k + 1}"}).json()
        ins.post(f"/api/exercises/{ex['id']}/members", json={"user_id": aid, "seat": "atc"})
        ins.post(f"/api/exercises/{ex['id']}/members", json={"user_id": pid, "seat": "pilot"})
        s = manager.get(ex["id"])
        with s.lock:
            for _ in range(secs - k * 250 * (i + 1)):      # shorter = fewer conflicts: a pupil who improves
                s.world.step(1)
        ins.post(f"/api/exercises/{ex['id']}/end")
ev = ins.get("/api/evaluations").json()
ok(len([p for p in ev["pending"] if p["kind"] == "atc"]) == 6, "instructor has 6 controller evaluations to write")
dims = [d["key"] for d in ev["dimensions"]["atc"]]
for n, p in enumerate(ev["pending"]):
    g = 2 + min(3, n // 2)
    r = ins.post("/api/evaluations", json={"exercise_id": p["exercise_id"], "kind": "atc", "ratee_id": p["ratee_id"],
                                           "scores": {d: g for d in dims}, "grade": g, "comment": f"Session note {n}"})
    ok(r.status_code == 200, f"instructor evaluates {p['ratee']} ({p['exercise']}) {g}/5")
ok(ins.post("/api/evaluations", json={"exercise_id": ev["pending"][0]["exercise_id"], "kind": "atc", "ratee_id": ev["pending"][0]["ratee_id"],
                                      "scores": {d: 3 for d in dims}, "grade": 3}).status_code == 403, "no second evaluation")
a0 = pupils[0].get("/api/evaluations").json()
ok(len(a0["received"]) == 3 and len([p for p in a0["pending"] if p["kind"] == "pilot"]) == 3, "controller: 3 received, 3 pilot ratings to write")
pd = [d["key"] for d in a0["dimensions"]["pilot"]]
for p in a0["pending"]:
    pupils[0].post("/api/evaluations", json={"exercise_id": p["exercise_id"], "kind": "pilot", "ratee_id": p["ratee_id"],
                                             "scores": {d: 4 for d in pd}, "grade": 4, "comment": "clear readbacks"})
r = pupils[0].post(f"/api/evaluations/{a0['received'][0]['id']}/ack", json={"comment": "Understood, I will anticipate more."}).json()
ok(r["ack_at"] and r["ack_comment"], "controller acknowledges with a comment")
ok(pupils[1].post(f"/api/evaluations/{a0['received'][0]['id']}/ack", json={}).status_code == 404, "only the controller acknowledges")
st = ins.get("/api/stats").json()["instructor"]
ok(st["exercises"] == 6 and st["pupils"] == 2 and len(st["curve"]) == 3, f"instructor stats: curve {[c['mean_los'] for c in st['curve']]}")
cs = pupils[0].get("/api/stats").json()["controller"]
ok(len(cs["sessions"]) == 3 and cs["sessions"][0]["grade"] is not None, f"controller stats: losses {[x['los'] for x in cs['sessions']]}")
ok(pil.get("/api/stats").json()["pilot"]["mean_grade"] == 4, "pseudo-pilot ratings")
ok(pupils[0].get("/stats").status_code == 200, "stats page")
print("stats checks passed")
