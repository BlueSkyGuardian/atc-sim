"""Platform settings, from environment variables (see DEPLOY.md)."""
from __future__ import annotations

import os
import secrets

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("ATC_DATA_DIR", os.path.join(ROOT, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

PUBLIC_URL = os.environ.get("ATC_PUBLIC_URL", "").rstrip("/")     # e.g. https://atc.example.com
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ATC_ADMIN_EMAILS", "").split(",") if e.strip()}
# "on" (default): accounts and roles; "off": the open single-user mode of the earlier versions (local use only)
AUTH = os.environ.get("ATC_AUTH", "on").lower()
# a password-less login form for testing, enabled automatically while no Google client is configured
DEV_LOGIN = os.environ.get("ATC_DEV_LOGIN", "1" if not GOOGLE_CLIENT_ID else "0") == "1"
API_KEY = os.environ.get("ATC_API_KEY", "")                        # header X-API-Key = admin rights (scripts)
COOKIE_SECURE = PUBLIC_URL.startswith("https://")


def _secret() -> str:
    s = os.environ.get("ATC_SECRET_KEY")
    if s:
        return s
    path = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_urlsafe(48))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


SECRET_KEY = _secret()

ROLES = ("admin", "instructor", "atc", "pilot")
SEATS = ("instructor", "atc", "pilot")
ADJACENT_UNITS = ["GMMM", "LPPC", "GCCC", "LECS", "MADEIRA"]       # phone lines of the Agadir controller
UNIT_NAMES = {"GMMM": "Casablanca ACC", "LPPC": "Lisboa ACC", "GCCC": "Canarias ACC", "LECS": "Sevilla ACC",
              "MADEIRA": "Madeira (Lisboa)"}
