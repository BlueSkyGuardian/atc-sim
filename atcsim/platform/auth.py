"""Accounts: Google sign-in (OpenID Connect, authorization-code flow), signed session cookie, profile, admin.

Without a Google client configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET), a test login form is offered instead
(ATC_DEV_LOGIN=1): any Gmail address, no password - for local testing only.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel

from . import config, db

router = APIRouter()
_ser = URLSafeTimedSerializer(config.SECRET_KEY, salt="atcsim-session")
COOKIE = "atc_session"
MAX_AGE = 14 * 24 * 3600
LOCAL_ADMIN = {"id": 0, "email": "local@localhost", "first_name": "Local", "last_name": "Admin", "licence": "-",
               "role": "admin", "active": 1}


def user_from_cookie(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        uid = _ser.loads(raw, max_age=MAX_AGE)["uid"]
    except (BadSignature, KeyError, TypeError):
        return None
    u = db.user_by_id(int(uid))
    return u if u and u["active"] else None


def current_user(request) -> Optional[dict]:
    """The signed-in user (HTTP request or WebSocket), or None."""
    if config.AUTH == "off":
        return LOCAL_ADMIN
    key = request.headers.get("x-api-key")
    if config.API_KEY and key and secrets.compare_digest(key, config.API_KEY):
        return LOCAL_ADMIN
    return user_from_cookie(request.cookies.get(COOKIE))


def require_user(request: Request, roles=None) -> dict:
    u = current_user(request)
    if u is None:
        raise HTTPException(401, "sign in first")
    if roles and u["role"] not in roles and u["role"] != "admin":
        raise HTTPException(403, f"needs role {' or '.join(roles)}")
    return u


def _login_response(u: dict, to: str = "/") -> RedirectResponse:
    r = RedirectResponse(to, status_code=303)
    r.set_cookie(COOKIE, _ser.dumps({"uid": u["id"]}), max_age=MAX_AGE, httponly=True, samesite="lax",
                 secure=config.COOKIE_SECURE)
    return r


def _base(request: Request) -> str:
    return config.PUBLIC_URL or str(request.base_url).rstrip("/")


@router.get("/auth/config")
def auth_config():
    return {"google": bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET), "dev_login": config.DEV_LOGIN,
            "auth": config.AUTH}


@router.get("/auth/login")
def login(request: Request, next: str = "/"):
    if not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        raise HTTPException(503, "Google sign-in is not configured on this server (see DEPLOY.md)")
    state = secrets.token_urlsafe(24)
    params = {"client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": _base(request) + "/auth/callback",
              "response_type": "code", "scope": "openid email profile", "state": state, "prompt": "select_account"}
    r = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=303)
    r.set_cookie("oauth_state", _ser.dumps({"s": state, "next": next if next.startswith("/") else "/"}),
                 max_age=600, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
    return r


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    import httpx
    if error:
        return RedirectResponse(f"/login?error={error}", status_code=303)
    try:
        st = _ser.loads(request.cookies.get("oauth_state", ""), max_age=600)
    except BadSignature:
        raise HTTPException(400, "sign-in expired, try again")
    if not state or not secrets.compare_digest(state, st["s"]):
        raise HTTPException(400, "bad sign-in state")
    tok = httpx.post("https://oauth2.googleapis.com/token", timeout=15, data={
        "code": code, "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": _base(request) + "/auth/callback", "grant_type": "authorization_code"})
    if tok.status_code != 200:
        raise HTTPException(400, f"Google token exchange failed: {tok.text[:200]}")
    info = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=15,
                     headers={"Authorization": f"Bearer {tok.json()['access_token']}"}).json()
    if not info.get("email") or not info.get("email_verified"):
        raise HTTPException(403, "Google account email not verified")
    u = db.upsert_login(info["email"], info.get("sub", ""), info.get("given_name", ""), info.get("family_name", ""))
    if not u["active"]:
        return RedirectResponse("/login?error=account%20disabled", status_code=303)
    r = _login_response(u, st.get("next", "/"))
    r.delete_cookie("oauth_state")
    return r


class DevLogin(BaseModel):
    email: str


@router.post("/auth/dev")
def dev_login(body: DevLogin):
    if not config.DEV_LOGIN:
        raise HTTPException(404, "not available")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "give an email address")
    u = db.upsert_login(email)
    r = JSONResponse({"ok": True, "user": db.public_user(u)})
    r.set_cookie(COOKIE, _ser.dumps({"uid": u["id"]}), max_age=MAX_AGE, httponly=True, samesite="lax",
                 secure=config.COOKIE_SECURE)
    return r


@router.post("/auth/logout")
def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE)
    return r


# ------------------------------------------------------------------ profile
@router.get("/api/me")
def me(request: Request):
    u = current_user(request)
    if u is None:
        raise HTTPException(401, "sign in first")
    return db.public_user(u)


class Profile(BaseModel):
    first_name: str
    last_name: str
    licence: str


@router.put("/api/me")
def update_me(body: Profile, request: Request):
    u = require_user(request)
    if u["id"] == 0:
        raise HTTPException(400, "local mode")
    if not (body.first_name.strip() and body.last_name.strip() and body.licence.strip()):
        raise HTTPException(400, "first name, last name and licence number are required")
    db.x("UPDATE users SET first_name=?, last_name=?, licence=? WHERE id=?",
         (body.first_name.strip(), body.last_name.strip(), body.licence.strip().upper(), u["id"]))
    return db.public_user(db.user_by_id(u["id"]))


# ------------------------------------------------------------------ users (directory for instructors, admin)
@router.get("/api/users")
def list_users(request: Request, role: Optional[str] = None):
    """Directory: instructors see active users (to invite them); the admin sees everyone."""
    u = require_user(request, roles=("instructor",))
    rows = db.q("SELECT * FROM users ORDER BY last_name, first_name, email")
    if u["role"] != "admin":
        rows = [r for r in rows if r["active"]]
    if role:
        rows = [r for r in rows if r["role"] == role]
    return [db.public_user(r) | {"last_login": r["last_login"], "created_at": r["created_at"]} for r in rows]


class UserPatch(BaseModel):
    role: Optional[str] = None
    active: Optional[bool] = None


@router.patch("/api/users/{uid}")
def patch_user(uid: int, body: UserPatch, request: Request):
    admin = require_user(request, roles=("admin",))
    t = db.user_by_id(uid)
    if t is None:
        raise HTTPException(404, "no such user")
    if body.role is not None:
        if body.role not in config.ROLES:
            raise HTTPException(400, f"role must be one of {config.ROLES}")
        if t["id"] == admin["id"] and body.role != "admin":
            raise HTTPException(400, "you cannot remove your own admin role")
        db.x("UPDATE users SET role=? WHERE id=?", (body.role, uid))
    if body.active is not None:
        if t["id"] == admin["id"] and not body.active:
            raise HTTPException(400, "you cannot disable your own account")
        db.x("UPDATE users SET active=? WHERE id=?", (1 if body.active else 0, uid))
    return db.public_user(db.user_by_id(uid))


class Invite(BaseModel):
    email: str
    role: str = "atc"


@router.post("/api/users")
def pre_register(body: Invite, request: Request):
    """Admin: create an account in advance for a Gmail address (the person signs in with Google later)."""
    require_user(request, roles=("admin",))
    if body.role not in config.ROLES:
        raise HTTPException(400, f"role must be one of {config.ROLES}")
    email = body.email.strip().lower()
    if db.user_by_email(email):
        raise HTTPException(409, "already registered")
    db.x("INSERT INTO users (email, role, created_at) VALUES (?,?,?)", (email, body.role, time.time()))
    return db.public_user(db.user_by_email(email))
