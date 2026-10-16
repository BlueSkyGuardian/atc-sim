"""Evaluations after an exercise and statistics.

* The instructor rates each controller of an exercise (phraseology, conflict detection, conflict resolution,
  anticipation, coordination + an overall grade, all out of 5, and a comment). The controller reads it and
  acknowledges it with a comment of their own.
* Each controller rates the pseudo-pilots of the exercise (readbacks, execution, reaction time, R/T, realism +
  overall grade and comment).
* Statistics: for an instructor, the exercises run and their pupils' progress (losses of separation by session
  number, averaged over the pupils: lower is better); for a controller, the results of each exercise and the
  instructors' grades; for a pseudo-pilot, the controllers' ratings.
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .auth import require_user

router = APIRouter()

ATC_DIMS = [("phraseology", "Phraseology"), ("detection", "Conflict detection"), ("resolution", "Conflict resolution"),
            ("anticipation", "Anticipation and planning"), ("coordination", "Coordination (telephone)")]
PILOT_DIMS = [("readback", "Readbacks"), ("execution", "Execution of clearances"), ("reaction", "Reaction time"),
              ("rt", "R/T and phraseology"), ("realism", "Realism (calls, requests, timing)")]
DIMS = {"atc": ATC_DIMS, "pilot": PILOT_DIMS}


@router.get("/stats", include_in_schema=False)
def stats_page():
    import os
    return FileResponse(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "stats.html"))


def _name(uid: Optional[int]) -> str:
    u = db.user_by_id(uid) if uid else None
    return db.public_user(u)["name"] if u else "?"


def _ended(where: str, args=()) -> List[dict]:
    return db.q(f"SELECT * FROM exercises WHERE ended_at IS NOT NULL AND {where} ORDER BY created_at", args)


# ------------------------------------------------------------------ evaluations
def pending_for(u: dict) -> List[dict]:
    """Evaluations this user still has to write."""
    out = []
    for e in _ended("instructor_id=? AND COALESCE(multi, 1)=1", (u["id"],)):
        for m in db.members(e["id"]):
            if m["seat"] == "atc" and not db.q1("SELECT 1 FROM evaluations WHERE exercise_id=? AND kind='atc' AND "
                                                "rater_id=? AND ratee_id=?", (e["id"], u["id"], m["user_id"])):
                out.append({"exercise_id": e["id"], "exercise": e["name"], "scenario": e["scenario"], "date": e["ended_at"],
                            "kind": "atc", "ratee_id": m["user_id"], "ratee": f"{m['first_name']} {m['last_name']}".strip(),
                            "role": "controller"})
    for e in db.q("SELECT e.* FROM exercises e JOIN exercise_members m ON m.exercise_id=e.id WHERE m.user_id=? AND "
                  "m.seat='atc' AND e.ended_at IS NOT NULL ORDER BY e.created_at", (u["id"],)):
        for m in db.members(e["id"]):
            if m["seat"] == "pilot" and m["user_id"] != u["id"] and not db.q1(
                    "SELECT 1 FROM evaluations WHERE exercise_id=? AND kind='pilot' AND rater_id=? AND ratee_id=?",
                    (e["id"], u["id"], m["user_id"])):
                out.append({"exercise_id": e["id"], "exercise": e["name"], "scenario": e["scenario"], "date": e["ended_at"],
                            "kind": "pilot", "ratee_id": m["user_id"], "ratee": f"{m['first_name']} {m['last_name']}".strip(),
                            "role": "pseudo-pilot"})
    return out


def _eval_row(r: dict) -> dict:
    e = db.exercise(r["exercise_id"]) or {}
    return {"id": r["id"], "exercise_id": r["exercise_id"], "exercise": e.get("name"), "scenario": e.get("scenario"),
            "date": e.get("ended_at") or r["created_at"], "kind": r["kind"], "rater": _name(r["rater_id"]),
            "ratee": _name(r["ratee_id"]), "scores": json.loads(r["scores"]), "grade": r["grade"],
            "comment": r["comment"], "created_at": r["created_at"], "ack_at": r["ack_at"], "ack_comment": r["ack_comment"]}


@router.get("/api/evaluations")
def evaluations(request: Request):
    u = require_user(request)
    return {"dimensions": {k: [{"key": a, "label": b} for a, b in v] for k, v in DIMS.items()},
            "pending": pending_for(u),
            "received": [_eval_row(r) for r in db.q("SELECT * FROM evaluations WHERE ratee_id=? ORDER BY created_at DESC",
                                                    (u["id"],))],
            "given": [_eval_row(r) for r in db.q("SELECT * FROM evaluations WHERE rater_id=? ORDER BY created_at DESC LIMIT 200",
                                                 (u["id"],))]}


class Evaluation(BaseModel):
    exercise_id: str
    kind: str
    ratee_id: int
    scores: Dict[str, int]
    grade: int
    comment: str = ""


@router.post("/api/evaluations")
def evaluate(body: Evaluation, request: Request):
    u = require_user(request)
    if not any(p["exercise_id"] == body.exercise_id and p["kind"] == body.kind and p["ratee_id"] == body.ratee_id
               for p in pending_for(u)):
        raise HTTPException(403, "nothing to evaluate here (already done, or not your exercise)")
    keys = [k for k, _ in DIMS[body.kind]]
    if set(body.scores) != set(keys) or not all(1 <= int(v) <= 5 for v in body.scores.values()) or not 1 <= body.grade <= 5:
        raise HTTPException(400, f"give every score from 1 to 5: {', '.join(keys)} and the overall grade")
    db.x("INSERT INTO evaluations (exercise_id, kind, rater_id, ratee_id, scores, grade, comment, created_at) "
         "VALUES (?,?,?,?,?,?,?,?)", (body.exercise_id, body.kind, u["id"], body.ratee_id,
                                      db.dumps({k: int(body.scores[k]) for k in keys}), body.grade,
                                      body.comment.strip()[:4000], time.time()))
    return {"ok": True}


class Ack(BaseModel):
    comment: str = ""


@router.post("/api/evaluations/{eid}/ack")
def acknowledge(eid: int, body: Ack, request: Request):
    u = require_user(request)
    r = db.q1("SELECT * FROM evaluations WHERE id=?", (eid,))
    if r is None or r["ratee_id"] != u["id"]:
        raise HTTPException(404, "no such evaluation of yours")
    db.x("UPDATE evaluations SET ack_at=?, ack_comment=? WHERE id=?", (time.time(), body.comment.strip()[:4000], eid))
    return _eval_row(db.q1("SELECT * FROM evaluations WHERE id=?", (eid,)))


# ------------------------------------------------------------------ statistics
def _metrics(e: dict) -> dict:
    try:
        return json.loads(e["metrics"]) if e.get("metrics") else {}
    except ValueError:
        return {}


def _hours(e: dict) -> float:
    return round((e.get("sim_time_s") or 0) / 3600, 2)


def _grade(eid: str, kind: str, ratee: int) -> Optional[dict]:
    r = db.q1("SELECT * FROM evaluations WHERE exercise_id=? AND kind=? AND ratee_id=? ORDER BY created_at DESC",
              (eid, kind, ratee))
    return {"grade": r["grade"], "scores": json.loads(r["scores"])} if r else None


def controller_sessions(uid: int, instructor: Optional[int] = None) -> List[dict]:
    """The exercises this person controlled (controller seat; plus their own solo-radar sessions), oldest first."""
    rows = db.q("SELECT e.* FROM exercises e JOIN exercise_members m ON m.exercise_id=e.id WHERE m.user_id=? AND "
                "m.seat='atc' AND e.ended_at IS NOT NULL" + (" AND e.instructor_id=?" if instructor else "") +
                " ORDER BY e.created_at", (uid, instructor) if instructor else (uid,))
    if instructor is None:
        rows += db.q("SELECT * FROM exercises WHERE instructor_id=? AND multi=0 AND ended_at IS NOT NULL", (uid,))
    rows.sort(key=lambda e: e["created_at"])
    out = []
    for e in rows:
        m = _metrics(e)
        if not m:
            continue                                    # ended before statistics existed, or after a restart
        g = _grade(e["id"], "atc", uid)
        h = _hours(e)
        out.append({"exercise_id": e["id"], "exercise": e["name"], "scenario": e["scenario"], "date": e["ended_at"],
                    "solo": e.get("multi") == 0, "instructor": _name(e["instructor_id"]), "sim_hours": h,
                    "los": m.get("los_events", 0), "los_per_hour": round(m.get("los_events", 0) / h, 2) if h > 0.05 else None,
                    "metrics": m, "grade": g["grade"] if g else None, "scores": g["scores"] if g else None})
    return out


def _avg(vals) -> Optional[float]:
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), 2) if v else None


@router.get("/api/stats")
def stats(request: Request):
    u = require_user(request)
    out: dict = {"me": db.public_user(u), "dimensions": {k: [{"key": a, "label": b} for a, b in v] for k, v in DIMS.items()}}
    # ---- as instructor
    ran = _ended("instructor_id=?", (u["id"],))
    if ran or u["role"] in ("instructor", "admin"):
        pupils: Dict[int, List[dict]] = {}
        for e in ran:
            for m in db.members(e["id"]):
                if m["seat"] == "atc":
                    pupils.setdefault(m["user_id"], [])
        series = []
        for pid in pupils:
            ss = controller_sessions(pid, instructor=u["id"])
            if ss:
                series.append({"user_id": pid, "name": _name(pid), "sessions": [
                    {"n": i + 1, "date": s["date"], "exercise": s["exercise"], "los": s["los"], "grade": s["grade"],
                     "los_per_hour": s["los_per_hour"]} for i, s in enumerate(ss)]})
        nmax = max([len(p["sessions"]) for p in series] or [0])
        curve = []
        for n in range(1, nmax + 1):
            at = [p["sessions"][n - 1] for p in series if len(p["sessions"]) >= n]
            cum = [sum(x["los"] for x in p["sessions"][:n]) / n for p in series if len(p["sessions"]) >= n]
            curve.append({"n": n, "pupils": len(at), "mean_los": _avg([x["los"] for x in at]),
                          "mean_cumulative_los": _avg(cum), "mean_grade": _avg([x["grade"] for x in at])})
        months: Dict[str, int] = {}
        for e in ran:
            k = time.strftime("%Y-%m", time.gmtime(e["created_at"]))
            months[k] = months.get(k, 0) + 1
        out["instructor"] = {"exercises": len(ran), "multi": sum(1 for e in ran if e.get("multi") != 0),
                             "sim_hours": round(sum(_hours(e) for e in ran), 1), "pupils": len(series),
                             "curve": curve, "pupils_series": series,
                             "per_month": [{"month": k, "n": v} for k, v in sorted(months.items())],
                             "given": len(db.q("SELECT id FROM evaluations WHERE rater_id=? AND kind='atc'", (u["id"],)))}
    # ---- as controller
    ss = controller_sessions(u["id"])
    if ss:
        dims = [k for k, _ in ATC_DIMS]
        out["controller"] = {"sessions": ss, "sim_hours": round(sum(s["sim_hours"] for s in ss), 1),
                             "mean_los": _avg([s["los"] for s in ss]), "mean_grade": _avg([s["grade"] for s in ss]),
                             "dims_avg": {k: _avg([(s["scores"] or {}).get(k) for s in ss]) for k in dims}}
    # ---- as pseudo-pilot
    rs = db.q("SELECT * FROM evaluations WHERE ratee_id=? AND kind='pilot' ORDER BY created_at", (u["id"],))
    if rs:
        rows = [_eval_row(r) for r in rs]
        out["pilot"] = {"ratings": rows, "mean_grade": _avg([r["grade"] for r in rows]),
                        "dims_avg": {k: _avg([r["scores"].get(k) for r in rows]) for k, _ in PILOT_DIMS}}
    return out
