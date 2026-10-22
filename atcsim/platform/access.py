"""Who may do what. An ASGI middleware in front of the whole app (HTTP and WebSocket):

* signed-out visitors only reach the sign-in page, /auth/* and static files; pages redirect to /login;
* /sessions/{id}/... is reserved to the members of that exercise, and each seat has its own rights:
    instructor : everything (simulation control, areas, runway, all instructions)
    pilot      : reads everything, executes instructions (the pseudo-pilot flies the aircraft), probes
    atc        : reads everything, probes, uses the sequencing tool, assumes aircraft and enters clearances in the
                 labels (/entries, /assume);
                 the aircraft only do what the pseudo-pilot makes them do
* scenario files, the designer and session creation are for instructors (and the admin).
"""
from __future__ import annotations

import re
from typing import Optional

from starlette.responses import JSONResponse, RedirectResponse

from . import config, db
from .auth import current_user

PUBLIC = ("/login", "/auth/", "/static/", "/api/health", "/favicon.ico", "/join/",
          "/help", "/help/", "/api/help", "/api/help/", "/api/landing",
          "/ai-data/")            # datasets for a training machine (RunPod pod): the link's token is the access       # the documentation is public
PAGES = ("/", "/designer", "/radar", "/solo", "/debrief/", "/ai", "/admin", "/profile", "/x/", "/stats", "/help")
SESS = re.compile(r"^/sessions/([0-9a-f]{6,32})(/.*)?$")

# per-seat rights on /sessions/{id}/<rest> for non-GET methods (GET is open to every member)
WRITE_RIGHTS = {
    "pilot": (r"^/(instructions|command|probe|mtcd|cpdlc/respond|cpdlc/downlink|chat|control|live|bookmark)$",),   # pseudo-pilots run the simulation too
    "atc": (r"^/(probe|mtcd|mtcd/settings|sequence|cpdlc/parse|cpdlc/command|chat|entries|assume/[A-Z0-9]+|release/[A-Z0-9]+)$",),
}


class _Req:                      # minimal request-like view of an ASGI scope for current_user()
    def __init__(self, scope):
        self.headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        cookie = self.headers.get("cookie", "")
        self.cookies = {}
        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                self.cookies[k] = v.strip('"')


def seat_for(sid: str, user: dict) -> Optional[str]:
    if config.AUTH == "off" or user.get("id") == 0:
        return "instructor"
    if _watch(sid) is not None:        # the live view of an AI run: staff, read-only
        return "atc" if user.get("role") in ("instructor", "admin") else None
    o = _debrief(sid)
    if o is not None:                  # a debrief's run: everybody sees the controller's screen
        return "atc" if db.seat_of(o["eid"], user) else None
    return db.seat_of(sid, user)


def _watch(sid: str) -> Optional[dict]:
    from . import ailab
    return ailab.watch_lookup(sid)


def _debrief(sid: str) -> Optional[dict]:
    if len(sid) <= 8:
        return None
    from . import debriefs
    return debriefs.lookup(sid)


def allowed(path: str, method: str, user: Optional[dict]) -> Optional[str]:
    """None if allowed, else the refusal reason."""
    if config.AUTH == "off":
        return None
    if any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC):
        return None
    if user is None:
        return "sign in first"
    role = user["role"]
    m = SESS.match(path)
    if m:
        sid, rest = m.group(1), m.group(2) or ""
        seat = seat_for(sid, user)
        if seat is None:
            return "you are not a member of this exercise"
        if _watch(sid) is not None:                # an AI run, live: look only
            return None if method in ("GET", "HEAD") or rest in ("/stream",) or (method == "POST" and rest == "/connect") \
                else "this is a live view of an AI run: look only"
        o = _debrief(sid)
        if o is not None:                          # a debrief's player (debriefs.py)
            from . import debriefs
            if method in ("GET", "HEAD") or rest in ("/stream", "/comms") or (method == "POST" and rest == "/connect"):
                return None
            if rest == "/control":
                return None if debriefs.can_control(sid, user) else "the instructor runs the debrief session"
            if not debriefs.can_write(sid, user):
                return "this player is not yours"
            if debriefs.can_control(sid, user):
                return None
            for pat in WRITE_RIGHTS["atc"]:
                if re.match(pat, rest):
                    return None
            return "the instructor runs the debrief session"
        if seat == "instructor" or method in ("GET", "HEAD") or rest in ("/stream", "/comms"):
            return None
        if method == "POST" and rest == "/connect":
            return None
        for pat in WRITE_RIGHTS.get(seat, ()):
            if re.match(pat, rest):
                return None
        if seat == "atc" and rest in ("/control", "/live", "/bookmark") and db.is_solo(sid):
            return None                                # solo radar: the controller runs the simulation
        return f"not allowed for the {seat} seat"
    if path in ("/sessions",) and method == "POST":
        return None if role in ("instructor", "admin") else "only instructors create sessions"
    if path.startswith(("/designer", "/scenarios", "/docs", "/openapi.json", "/redoc")) or path == "/sessions":
        return None if role in ("instructor", "admin") else "instructors only"
    if path.startswith("/api/admin"):
        return None if role == "admin" else "admin only"
    return None                  # /airspace, /types, /api/*, pages: signed-in users (finer checks in the routes)


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        user = current_user(_Req(scope))
        scope.setdefault("state", {})["user"] = user
        path, method = scope["path"], scope.get("method", "GET")
        why = allowed(path, method, user)
        if why is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        is_page = method == "GET" and (path in PAGES or path.startswith(("/x/", "/help/", "/debrief/", "/ai/watch/")) or path == "/designer")
        if is_page and user is None:
            resp = RedirectResponse("/login?next=" + path, status_code=303)
        else:
            resp = JSONResponse({"detail": why}, status_code=401 if user is None else 403)
        await resp(scope, receive, send)
