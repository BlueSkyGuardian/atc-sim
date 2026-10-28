"""Notifications: what happened that concerns a user (invited to an exercise or a debrief, a scenario shared with
them, an evaluation of their work, their account or role), in the navbar's bell, and by e-mail.

E-mail goes out by SMTP (ATC_SMTP_HOST, ATC_SMTP_PORT, ATC_SMTP_USER, ATC_SMTP_PASSWORD, ATC_SMTP_FROM,
ATC_SMTP_SECURITY = starttls | ssl | none). Without ATC_SMTP_HOST the messages are written to the data folder's
outbox (data/outbox/*.eml) instead - nothing is lost, and a test server sends nothing. Mail is sent by a background
thread: an invitation never waits for the mail server, and a mail server down never fails an invitation (the
notification is in the bell anyway; the failure is logged in the outbox log). Each user can turn the e-mails off
in their profile (the bell stays).
"""
from __future__ import annotations

import html
import logging
import os
import queue
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config, db
from .auth import require_user

log = logging.getLogger("atcsim.mail")
router = APIRouter()

SMTP_HOST = os.environ.get("ATC_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("ATC_SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("ATC_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("ATC_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("ATC_SMTP_FROM", "") or SMTP_USER or "atcsim@localhost"
SMTP_SECURITY = os.environ.get("ATC_SMTP_SECURITY", "starttls").lower()     # starttls | ssl | none
OUTBOX = os.path.join(config.DATA_DIR, "outbox")
APP_NAME = "ATC SIM"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                 -- exercise_invite | debrief_invite | scenario_share | evaluation | account | role
  title TEXT NOT NULL,
  body TEXT DEFAULT '',
  link TEXT DEFAULT '',               -- a path on the site (/x/<id>, /debrief/<id>, ...)
  actor_id INTEGER,
  created_at REAL NOT NULL,
  read_at REAL,
  emailed TEXT                        -- sent | outbox | off | failed: ... | NULL (queued)
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications (user_id, created_at);
"""
_ready = False
_ready_lock = threading.Lock()


def _schema() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        db.conn().executescript(SCHEMA)
        try:                                            # e-mail preference of each user (1 = on, the default)
            db.conn().execute("ALTER TABLE users ADD COLUMN email_notify INTEGER NOT NULL DEFAULT 1")
        except Exception:                               # noqa: BLE001 - already there
            pass
        _ready = True


def mode() -> str:
    """How e-mail goes out: 'smtp' (to the people) or 'outbox' (written to the data folder only)."""
    return "smtp" if SMTP_HOST else "outbox"


# ------------------------------------------------------------------ creating notifications
def notify(user_id: int, kind: str, title: str, body: str = "", link: str = "", actor: Optional[dict] = None,
           email: bool = True, base_url: str = "") -> Optional[int]:
    """A notification for user_id (in their bell), and an e-mail unless they turned e-mails off. Never for the
    person who did it themselves. Returns the notification id (None when not created)."""
    _schema()
    u = db.user_by_id(user_id)
    if u is None or (actor is not None and actor.get("id") == user_id):
        return None
    nid = db.x("INSERT INTO notifications (user_id, kind, title, body, link, actor_id, created_at) VALUES (?,?,?,?,?,?,?)",
               (user_id, kind, title[:300], body[:2000], link[:500], (actor or {}).get("id"), time.time()))
    wants = bool(u.get("email_notify", 1)) and bool(u.get("active", 1))
    if email and wants:
        _queue.put((nid, u, kind, title, body, link, actor, base_url or config.PUBLIC_URL))
        _start_worker()
    else:
        db.x("UPDATE notifications SET emailed=? WHERE id=?", ("off" if email else None, nid))
    return nid


def actor_name(actor: Optional[dict]) -> str:
    if not actor:
        return "Someone"
    n = f"{actor.get('first_name') or ''} {actor.get('last_name') or ''}".strip()
    return n or actor.get("email") or "Someone"


def base_of(request: Optional[Request]) -> str:
    """The site's address for the links in the e-mails: ATC_PUBLIC_URL, else the address the request came to."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    try:
        return str(request.base_url).rstrip("/") if request is not None else ""
    except Exception:                                   # noqa: BLE001
        return ""


# ------------------------------------------------------------------ e-mail
_queue: "queue.Queue" = queue.Queue()
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()


def _start_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="atcsim-mail", daemon=True)
            _worker.start()


def _run() -> None:
    while True:
        item = _queue.get()
        try:
            nid, u, kind, title, body, link, actor, base = item
            status = send(u["email"], title, body, link, base, greeting=(u.get("first_name") or "").strip())
            db.x("UPDATE notifications SET emailed=? WHERE id=?", (status, nid))
        except Exception as e:                          # noqa: BLE001 - mail must never take the server down
            log.warning("e-mail to %s not sent: %s", (item[1] or {}).get("email"), e)
            try:
                db.x("UPDATE notifications SET emailed=? WHERE id=?", (f"failed: {e}"[:200], item[0]))
            except Exception:                           # noqa: BLE001
                pass
        finally:
            _queue.task_done()


def flush(timeout: float = 10.0) -> bool:
    """Wait until the queued e-mails are out (tests, the admin's test mail)."""
    end = time.time() + timeout
    while _queue.unfinished_tasks and time.time() < end:
        time.sleep(0.05)
    return not _queue.unfinished_tasks


def _message(to: str, subject: str, body: str, link: str, base: str, greeting: str = "") -> EmailMessage:
    url = (base.rstrip("/") + link) if link.startswith("/") and base else link
    hello = f"Hello {greeting}," if greeting else "Hello,"
    text = f"{hello}\n\n{subject}.\n" + (f"\n{body}\n" if body else "") + (f"\nOpen it: {url}\n" if url else "") + \
        f"\n-- \n{APP_NAME}" + (f" · {base}" if base else "") + \
        "\nYou receive this because of your account on the simulator; turn these e-mails off in your profile."
    btn = (f'<p style="margin:22px 0"><a href="{html.escape(url)}" style="background:#c2185b;color:#fff;'
           f'padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600">Open</a></p>') if url else ""
    page = f"""<!doctype html><html><body style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#1d2733;
background:#f4f6f8;margin:0;padding:24px"><div style="max-width:560px;margin:auto;background:#fff;border-radius:10px;
padding:24px 28px;border:1px solid #e1e6eb"><div style="font-weight:800;letter-spacing:.02em;margin-bottom:18px">ATC
<span style="color:#c2185b">SIM</span></div><p>{html.escape(hello)}</p><p style="font-size:16px;font-weight:600">
{html.escape(subject)}</p>{f'<p style="white-space:pre-line">{html.escape(body)}</p>' if body else ''}{btn}
<p style="color:#7a8794;font-size:12px;margin-top:26px">You receive this because of your account on the simulator
{f'(<a href="{html.escape(base)}/profile" style="color:#7a8794">{html.escape(base)}</a>)' if base else ''}; turn these
e-mails off in your profile.</p></div></body></html>"""
    m = EmailMessage()
    m["Subject"] = f"{subject} - {APP_NAME}"
    m["From"] = formataddr((APP_NAME, SMTP_FROM))
    m["To"] = to
    m["Message-ID"] = make_msgid(domain=(SMTP_FROM.split("@")[-1] or "atcsim"))
    m.set_content(text)
    m.add_alternative(page, subtype="html")
    return m


def send(to: str, subject: str, body: str = "", link: str = "", base: str = "", greeting: str = "") -> str:
    """Send one notification e-mail now: 'sent' (SMTP) or 'outbox' (written to data/outbox). Raises on an SMTP
    error."""
    return deliver(_message(to, subject, body, link, base, greeting))


def compose(to: str, subject: str, text: str, page: str, headers: Optional[Dict[str, str]] = None) -> EmailMessage:
    """A ready-made e-mail (plain text and HTML), with extra headers (List-Unsubscribe for the newsletter)."""
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = formataddr((APP_NAME, SMTP_FROM))
    m["To"] = to
    m["Message-ID"] = make_msgid(domain=(SMTP_FROM.split("@")[-1] or "atcsim"))
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(text)
    m.add_alternative(page, subtype="html")
    return m


def deliver(m: EmailMessage, smtp=None) -> str:
    """Send a message: 'sent' (SMTP) or 'outbox'. smtp: an open connection to reuse (a newsletter to many)."""
    to = str(m["To"])
    if not SMTP_HOST:
        os.makedirs(OUTBOX, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}-{to.replace('@', '_at_')}.eml"
        with open(os.path.join(OUTBOX, name), "wb") as f:
            f.write(bytes(m))
        return "outbox"
    if smtp is not None:
        smtp.send_message(m)
        return "sent"
    s = connect()
    try:
        s.send_message(m)
    finally:
        try:
            s.quit()
        except Exception:                               # noqa: BLE001
            pass
    return "sent"


def connect():
    """An open, signed-in SMTP connection (the caller quits it)."""
    ctx = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
    s.ehlo()
    if SMTP_SECURITY == "starttls":
        s.starttls(context=ctx)
        s.ehlo()
    if SMTP_USER:
        s.login(SMTP_USER, SMTP_PASSWORD)
    return s


# ------------------------------------------------------------------ API: the bell
def _row(r: dict) -> dict:
    a = db.user_by_id(r["actor_id"]) if r.get("actor_id") else None
    return {"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "link": r["link"],
            "created_at": r["created_at"], "read": r["read_at"] is not None, "actor": actor_name(a) if a else None}


@router.get("/api/notifications")
def list_notifications(request: Request, limit: int = 30):
    """The user's notifications, newest first, and how many are unread."""
    u = require_user(request)
    _schema()
    rows = db.q("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (u["id"], max(1, min(int(limit), 200))))
    unread = db.q1("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND read_at IS NULL", (u["id"],))["n"]
    return {"unread": unread, "items": [_row(r) for r in rows]}


class ReadBody(BaseModel):
    ids: Optional[List[int]] = None        # none: all of them


@router.post("/api/notifications/read")
def mark_read(body: ReadBody, request: Request):
    u = require_user(request)
    _schema()
    now = time.time()
    if body.ids:
        for i in body.ids[:500]:
            db.x("UPDATE notifications SET read_at=? WHERE id=? AND user_id=? AND read_at IS NULL", (now, int(i), u["id"]))
    else:
        db.x("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", (now, u["id"]))
    return list_notifications(request)


class Prefs(BaseModel):
    email_notify: bool


@router.get("/api/me/notify")
def get_prefs(request: Request):
    u = require_user(request)
    _schema()
    r = db.user_by_id(u["id"]) or {}
    return {"email_notify": bool(r.get("email_notify", 1)), "mode": mode()}


@router.put("/api/me/notify")
def set_prefs(body: Prefs, request: Request):
    u = require_user(request)
    _schema()
    db.x("UPDATE users SET email_notify=? WHERE id=?", (1 if body.email_notify else 0, u["id"]))
    return get_prefs(request)


class TestMail(BaseModel):
    to: Optional[str] = None


@router.post("/api/admin/test-email")
def test_email(body: TestMail, request: Request):
    """Admin: send a test e-mail (to themselves by default) now, to check the mail settings."""
    u = require_user(request, roles=("admin",))
    to = (body.to or u["email"]).strip()
    if "@" not in to:
        raise HTTPException(400, "give an e-mail address")
    try:
        status = send(to, "Test e-mail", "If you can read this, the simulator's e-mail settings work.", "/",
                      base_of(request), greeting=(u.get("first_name") or ""))
    except Exception as e:                              # noqa: BLE001
        raise HTTPException(502, f"the mail server refused: {e}")
    return {"status": status, "mode": mode(), "to": to,
            "note": "written to the data folder's outbox (no ATC_SMTP_HOST set)" if status == "outbox" else "sent"}


@router.get("/api/admin/mail")
def mail_status(request: Request):
    """Admin: how e-mail goes out, and the last e-mails' delivery."""
    require_user(request, roles=("admin",))
    _schema()
    rows = db.q("SELECT n.id, n.kind, n.title, n.created_at, n.emailed, u.email FROM notifications n "
                "JOIN users u ON u.id=n.user_id ORDER BY n.created_at DESC LIMIT 30")
    return {"mode": mode(), "host": SMTP_HOST or None, "from": SMTP_FROM, "security": SMTP_SECURITY, "recent": rows}
