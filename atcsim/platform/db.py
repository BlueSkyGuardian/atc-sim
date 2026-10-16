"""SQLite store: users, scenario ownership and sharing, exercises and their members, recordings."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional

from .config import ADMIN_EMAILS, DATA_DIR, DEV_LOGIN

DB_PATH = os.path.join(DATA_DIR, "atcsim.db")
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  google_sub TEXT,
  first_name TEXT DEFAULT '',
  last_name TEXT DEFAULT '',
  licence TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'atc',
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS scenario_meta (
  name TEXT PRIMARY KEY,
  owner_id INTEGER,                  -- NULL: library scenario (read-only for everyone)
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_shares (
  name TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  perm TEXT NOT NULL CHECK (perm IN ('read', 'write')),
  PRIMARY KEY (name, user_id)
);
CREATE TABLE IF NOT EXISTS exercises (
  id TEXT PRIMARY KEY,               -- = simulator session id
  name TEXT NOT NULL,
  scenario TEXT NOT NULL,
  instructor_id INTEGER NOT NULL,
  created_at REAL NOT NULL,
  ended_at REAL,
  status TEXT NOT NULL DEFAULT 'open',
  atc_token TEXT, pilot_token TEXT, observer_token TEXT
);
CREATE TABLE IF NOT EXISTS exercise_members (
  exercise_id TEXT NOT NULL,
  user_id INTEGER NOT NULL,
  seat TEXT NOT NULL,
  added_at REAL NOT NULL,
  PRIMARY KEY (exercise_id, user_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exercise_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('atc', 'pilot')),   -- atc: instructor rates the controller; pilot: controller rates the pseudo-pilot
  rater_id INTEGER NOT NULL,
  ratee_id INTEGER NOT NULL,
  scores TEXT NOT NULL,            -- {"dimension": 1..5}
  grade INTEGER NOT NULL,          -- overall, 1..5
  comment TEXT DEFAULT '',
  created_at REAL NOT NULL,
  ack_at REAL,
  ack_comment TEXT,
  UNIQUE (exercise_id, kind, rater_id, ratee_id)
);
CREATE TABLE IF NOT EXISTS recordings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  exercise_id TEXT NOT NULL,
  path TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  duration_s REAL,
  size_bytes INTEGER,
  archived INTEGER NOT NULL DEFAULT 0,
  info TEXT
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            for col in ("user_id INTEGER", "title TEXT"):          # added with the controller-view recordings
                try:
                    _conn.execute(f"ALTER TABLE recordings ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
            for col in ("metrics TEXT", "sim_time_s REAL", "multi INTEGER DEFAULT 1"):   # added with the statistics
                try:
                    _conn.execute(f"ALTER TABLE exercises ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
        return _conn


def q(sql: str, args=()) -> List[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql: str, args=()) -> Optional[dict]:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args=()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


# ------------------------------------------------------------------ users
def user_by_id(uid: int) -> Optional[dict]:
    return q1("SELECT * FROM users WHERE id=?", (uid,))


def user_by_email(email: str) -> Optional[dict]:
    return q1("SELECT * FROM users WHERE email=?", (email.lower(),))


def upsert_login(email: str, google_sub: str = "", first: str = "", last: str = "") -> dict:
    email = email.lower()
    u = user_by_email(email)
    now = time.time()
    if u is None:
        first_user = q1("SELECT COUNT(*) AS n FROM users")["n"] == 0
        # the first account becomes admin only in the test login mode; online, list admins in ATC_ADMIN_EMAILS
        role = "admin" if (email in ADMIN_EMAILS or (first_user and DEV_LOGIN and not ADMIN_EMAILS)) else "atc"
        x("INSERT INTO users (email, google_sub, first_name, last_name, role, created_at, last_login) "
          "VALUES (?,?,?,?,?,?,?)", (email, google_sub, first, last, role, now, now))
    else:
        x("UPDATE users SET last_login=?, google_sub=COALESCE(NULLIF(?, ''), google_sub) WHERE id=?",
          (now, google_sub, u["id"]))
        if email in ADMIN_EMAILS and u["role"] != "admin":
            x("UPDATE users SET role='admin' WHERE id=?", (u["id"],))
    return user_by_email(email)


def public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "first_name", "last_name", "licence", "role", "active")} | {
        "name": (f"{u['first_name']} {u['last_name']}".strip() or u["email"]),
        "profile_complete": bool(u["first_name"] and u["last_name"] and u["licence"])}


# ------------------------------------------------------------------ scenarios
def scenario_access(name: str, user: dict) -> Optional[str]:
    """'write', 'read' or None for this user."""
    if user["role"] == "admin":
        return "write"
    m = q1("SELECT * FROM scenario_meta WHERE name=?", (name,))
    if m is None or m["owner_id"] is None:                     # library scenario
        return "read" if user["role"] in ("instructor", "admin") else None
    if m["owner_id"] == user["id"]:
        return "write"
    s = q1("SELECT perm FROM scenario_shares WHERE name=? AND user_id=?", (name, user["id"]))
    return s["perm"] if s else None


def set_owner(name: str, uid: int) -> None:
    if q1("SELECT 1 FROM scenario_meta WHERE name=?", (name,)) is None:
        x("INSERT INTO scenario_meta (name, owner_id, created_at) VALUES (?,?,?)", (name, uid, time.time()))


def shares(name: str) -> List[dict]:
    return q("SELECT s.user_id, s.perm, u.email, u.first_name, u.last_name FROM scenario_shares s "
             "JOIN users u ON u.id=s.user_id WHERE s.name=?", (name,))


# ------------------------------------------------------------------ exercises
def exercise(eid: str) -> Optional[dict]:
    return q1("SELECT * FROM exercises WHERE id=?", (eid,))


def seat_of(eid: str, user: dict) -> Optional[str]:
    """The user's seat in this exercise: 'instructor', 'atc', 'pilot', or None."""
    e = exercise(eid)
    if e is None:
        return "instructor" if user["role"] == "admin" else None
    if e["instructor_id"] == user["id"] or user["role"] == "admin":
        return "instructor"
    m = q1("SELECT seat FROM exercise_members WHERE exercise_id=? AND user_id=?", (eid, user["id"]))
    return m["seat"] if m else None


def members(eid: str) -> List[dict]:
    return q("SELECT m.user_id, m.seat, u.email, u.first_name, u.last_name, u.licence FROM exercise_members m "
             "JOIN users u ON u.id=m.user_id WHERE m.exercise_id=? ORDER BY m.seat", (eid,))


def add_member(eid: str, uid: int, seat: str) -> None:
    x("INSERT OR REPLACE INTO exercise_members (exercise_id, user_id, seat, added_at) VALUES (?,?,?,?)",
      (eid, uid, seat, time.time()))


def dumps(o) -> str:
    return json.dumps(o, separators=(",", ":"))
