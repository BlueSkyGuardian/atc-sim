"""Aviation news: the public news page (/news, no sign-in) and the daily newsletter.

Sources are RSS / Atom feeds - aviation news sites, and Google News searches (air traffic control, air navigation
technology, Morocco). The administrator edits the list. Every hour the server reads the feeds and keeps the new
stories: the headline, the feed's own short excerpt (cut to a few lines), the link to the article and where it comes
from - never the article itself, which stays on its site.

Once a day (at the hour set, Casablanca time by default) the newsletter is put together from the stories of the last
day: with a language model (any OpenAI-compatible endpoint registered in the AI lab: DeepSeek, Gemini, ...) the
stories that matter most to controllers and aviation people are picked, sorted into sections and given a summary
of one or two sentences written from the headline and excerpt (and the issue its title and introduction); without a
model, by keywords (air traffic control first), with the feeds' excerpts. The issue is published on the news page and
mailed to the subscribers.

Subscribing: signed-in users in their profile (their Google address is verified); anyone else from the news page,
confirmed by a link sent to the address (double opt-in). Every newsletter has an unsubscribe link (and the one-click
List-Unsubscribe header the mail services use).

Languages: French (the default), English and Arabic (news_i18n.py). The newsroom writes every article in the three; the
issue's title, introduction and summaries are written in the three by the model. Each reader picks a language in their
profile (subscribers from the news page: the page's language when they subscribed): the newsletter comes in it, and
the news page opens in it (?lang=, then the page's own choice kept in a cookie, then the profile, then French).
"""
from __future__ import annotations

import html
import json
import logging
import math
import re
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, db, notify
from . import news_i18n as L
from .auth import current_user, require_user

log = logging.getLogger("atcsim.news")
router = APIRouter()

SECTIONS = ["Air traffic control & airspace", "Technology & innovation", "Safety", "Airlines & airports",
            "Morocco & Africa", "Defence & space"]

DEFAULTS = {"enabled": True, "send": True, "send_hour": 7, "tz": "Africa/Casablanca", "model_id": None,
            "max_items": 10, "fetch_every_min": 60, "max_per_day": 450, "keep_days": 45}


def _gnews(q: str, hl: str = "en-US", gl: str = "US", ceid: str = "US:en") -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"


DEFAULT_SOURCES = [
    ("Google News · air traffic control", _gnews('"air traffic control" OR "air traffic controllers" when:2d'), "Air traffic control & airspace"),
    ("Google News · air navigation", _gnews('"air navigation service" OR "air traffic management" OR Eurocontrol OR NATS OR "FAA NextGen" when:3d'), "Air traffic control & airspace"),
    ("Google News · aviation technology", _gnews('aviation technology OR "remote tower" OR eVTOL OR "sustainable aviation fuel" when:2d'), "Technology & innovation"),
    ("Google News · Maroc aviation", _gnews("aviation Maroc OR ONDA OR \"Royal Air Maroc\" when:3d", "fr", "MA", "MA:fr"), "Morocco & Africa"),
    ("Google News · Morocco aviation", _gnews('Morocco aviation OR "Royal Air Maroc" OR "Moroccan airports" when:3d'), "Morocco & Africa"),
    ("Simple Flying", "https://simpleflying.com/feed/", "Airlines & airports"),
    ("AeroTime", "https://www.aerotime.aero/rss", None),
    ("AVweb", "https://www.avweb.com/feed/", None),
    ("The Aviationist", "https://theaviationist.com/feed/", "Defence & space"),
    ("Leeham News", "https://leehamnews.com/feed/", "Airlines & airports"),
    ("Aerospace Technology", "https://www.aerospace-technology.com/feed/", "Technology & innovation"),
    ("GreenAir News", "https://www.greenairnews.com/?feed=rss2", "Technology & innovation"),
    ("Aviation Source News", "https://aviationsourcenews.com/feed/", None),
    ("AFRVIATOR", "https://afrviator.org/feed/", "Morocco & Africa"),
]

# keyword weights (without a model): what matters to a controller first
KEYWORDS = [
    (r"air traffic control|air traffic controller|\batc\b|\batco\b|controllers?\b", 6, "Air traffic control & airspace"),
    (r"airspace|air navigation|\bansp\b|eurocontrol|\bnats\b|nav canada|enaire|\bdsna\b|\bonda\b|nextgen|sesar|"
     r"separation|sector|approach control|tower|radar|ads-b|cpdlc|datalink|route network|flow management|\batfm\b", 4,
     "Air traffic control & airspace"),
    (r"remote tower|artificial intelligence|\bai\b|automation|machine learning|software|satellite|evtol|drone|uas|"
     r"hydrogen|electric|sustainable aviation fuel|\bsaf\b|digital|simulator|simulation|cyber", 3, "Technology & innovation"),
    (r"incident|accident|near[- ]miss|runway incursion|investigation|safety|\bntsb\b|\bbea\b|\baaib\b|emergency|"
     r"go-around|loss of separation|turbulence|bird strike", 3, "Safety"),
    (r"morocco|maroc|royal air maroc|casablanca|marrakech|agadir|tanger|tangier|rabat|\bfes\b|africa|afrique|"
     r"dakar|asecna|algeria|tunisia|mauritania|canary|canaries", 3, "Morocco & Africa"),
    (r"airline|airport|airbus|boeing|embraer|order|fleet|route|passenger|capacity|strike", 1, "Airlines & airports"),
    (r"air force|military|fighter|defen[cs]e|missile|space|nasa|rocket|launch", 1, "Defence & space"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS news_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE, section TEXT,
  enabled INTEGER NOT NULL DEFAULT 1, last_fetch REAL, last_status TEXT, last_count INTEGER, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS news_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, source TEXT, guid TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  link TEXT NOT NULL, published REAL NOT NULL, excerpt TEXT DEFAULT '', image TEXT, section TEXT, score REAL DEFAULT 0,
  summary TEXT, fetched_at REAL NOT NULL, norm TEXT
);
CREATE INDEX IF NOT EXISTS news_items_pub ON news_items (published);
CREATE TABLE IF NOT EXISTS news_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL UNIQUE, title TEXT NOT NULL, intro TEXT DEFAULT '',
  sections TEXT NOT NULL, model TEXT, created_at REAL NOT NULL, sent_at REAL, sent_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0, status TEXT DEFAULT 'draft'
);
CREATE TABLE IF NOT EXISTS news_subscribers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, user_id INTEGER, token TEXT NOT NULL,
  created_at REAL NOT NULL, confirmed_at REAL, unsub_at REAL, last_sent REAL, confirm_sent REAL, ip TEXT
);
CREATE TABLE IF NOT EXISTS news_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_articles (
  slug TEXT PRIMARY KEY, date TEXT NOT NULL, region TEXT NOT NULL, section TEXT, title TEXT NOT NULL, summary TEXT,
  body TEXT NOT NULL, sources TEXT NOT NULL, figures TEXT NOT NULL, author TEXT, updated INTEGER, synced_at REAL
);
"""
_ready = False
_ready_lock = threading.Lock()
_job_lock = threading.Lock()                 # one fetch / build / send at a time


def _schema() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if _ready:
            return
        db.conn().executescript(SCHEMA)
        if db.q1("SELECT COUNT(*) AS n FROM news_sources")["n"] == 0:
            for name, url, sec in DEFAULT_SOURCES:
                db.x("INSERT OR IGNORE INTO news_sources (name, url, section, created_at) VALUES (?,?,?,?)",
                     (name, url, sec, time.time()))
        for tbl, col in (("news_issues", "articles TEXT"), ("news_articles", "i18n TEXT"), ("news_issues", "i18n TEXT"),
                         ("news_subscribers", "lang TEXT")):
            try:
                db.conn().execute(f"ALTER TABLE {tbl} ADD COLUMN {col}")
            except Exception:                            # noqa: BLE001 - already there
                pass
        db.conn()                                        # (users.news_lang: db.py)
        if db.q1("SELECT 1 AS x FROM news_settings WHERE key='_users_subscribed'") is None:
            # the newsletter is on by default for every account: the accounts made before it, once
            now = time.time()
            for u in db.q("SELECT id, email FROM users WHERE active=1 AND email LIKE '%@%'"):
                db.x("INSERT OR IGNORE INTO news_subscribers (email, user_id, token, created_at, confirmed_at) VALUES (?,?,?,?,?)",
                     (u["email"].lower(), u["id"], secrets.token_urlsafe(24), now, now))
            db.x("INSERT OR REPLACE INTO news_settings (key, value) VALUES ('_users_subscribed', 'true')")
        _ready = True


def settings() -> dict:
    _schema()
    out = dict(DEFAULTS)
    for r in db.q("SELECT key, value FROM news_settings"):
        try:
            out[r["key"]] = json.loads(r["value"])
        except ValueError:
            pass
    return out


def _set(**kw) -> dict:
    _schema()
    for k, v in kw.items():
        if k in DEFAULTS:
            db.x("INSERT OR REPLACE INTO news_settings (key, value) VALUES (?,?)", (k, json.dumps(v)))
    return settings()


def _tz(s: dict):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(s.get("tz") or "Africa/Casablanca")
    except Exception:                                    # noqa: BLE001
        return timezone.utc


# ------------------------------------------------------------------ reading the feeds
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")


def clean(s: Optional[str], n: int = 0) -> str:
    """Plain text: tags out, entities decoded, spaces collapsed; cut to n characters at a word."""
    t = WS.sub(" ", html.unescape(TAG.sub(" ", html.unescape(s or "")))).strip()
    if n and len(t) > n:
        t = t[:n].rsplit(" ", 1)[0].rstrip(",;:-– ") + "…"
    return t


def _norm(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    stop = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "at", "with", "as", "by", "is", "after", "from"}
    return " ".join(sorted(set(w for w in words if w not in stop and len(w) > 2)))


def _when(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _local(el) -> str:
    return el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""


def parse(data: bytes) -> List[dict]:
    """The stories of an RSS 2.0 / RSS 1.0 / Atom feed: title, link, guid, published, excerpt, image, source name."""
    root = ET.fromstring(data)
    out = []
    entries = [e for e in root.iter() if _local(e) in ("item", "entry")]
    for e in entries:
        f: Dict[str, object] = {}
        for c in e:
            k = _local(c)
            if k == "title":
                f["title"] = "".join(c.itertext())
            elif k == "link":
                href = c.get("href")
                if href and (c.get("rel") in (None, "alternate")):
                    f.setdefault("link", href)
                elif (c.text or "").strip():
                    f.setdefault("link", c.text.strip())
            elif k in ("guid", "id"):
                f["guid"] = (c.text or "").strip()
            elif k in ("pubdate", "published", "updated", "date"):
                f.setdefault("published", _when(c.text))
            elif k in ("description", "summary") and "excerpt" not in f:
                f["excerpt"] = c.text or "".join(c.itertext())
            elif k == "encoded" and "excerpt" not in f:
                f["excerpt"] = c.text or ""
            elif k in ("content",) and c.get("url") and (c.get("medium") == "image" or (c.get("type") or "").startswith("image")):
                f.setdefault("image", c.get("url"))
            elif k == "thumbnail" and c.get("url"):
                f.setdefault("image", c.get("url"))
            elif k == "enclosure" and (c.get("type") or "").startswith("image"):
                f.setdefault("image", c.get("url"))
            elif k == "source":
                f["source"] = (c.text or "").strip()
        title = clean(str(f.get("title") or ""))
        link = str(f.get("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        if f.get("source") and title.endswith(" - " + str(f["source"])):
            title = title[: -len(" - " + str(f["source"]))]                       # Google News: "Headline - Site"
        ex = clean(str(f.get("excerpt") or ""), 320)
        if ex.lower().startswith(title.lower()[:40]):                               # Google News: the excerpt = the title
            ex = ""
        out.append({"title": title[:300], "link": link[:1000], "guid": (str(f.get("guid") or "") or link)[:500],
                    "published": f.get("published"), "excerpt": ex, "image": f.get("image"),
                    "source": str(f.get("source") or "")[:120]})
    return out


def fetch_source(src: dict, client=None) -> int:
    """Read one feed and keep its new stories. Returns how many were new."""
    import httpx
    own = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True, headers={
        "User-Agent": f"ATC-SIM-News/1.0 (+{config.PUBLIC_URL or 'aviation news reader'})",
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
    new = 0
    try:
        r = client.get(src["url"])
        r.raise_for_status()
        items = parse(r.content)
        now = time.time()
        recent = {x["norm"] for x in db.q("SELECT norm FROM news_items WHERE published > ?", (now - 3 * 86400,)) if x["norm"]}
        for it in items:
            pub = it["published"] or now
            if pub > now + 3600 or pub < now - settings()["keep_days"] * 86400:
                continue
            norm = _norm(it["title"])
            if norm and norm in recent:
                continue                                   # the same story from another source
            if db.q1("SELECT 1 AS x FROM news_items WHERE guid=?", (it["guid"],)):
                continue                                   # read before
            sec, score = classify(it["title"] + " " + it["excerpt"], src.get("section"))
            db.x("INSERT OR IGNORE INTO news_items (source_id, source, guid, title, link, published, excerpt, image, "
                 "section, score, fetched_at, norm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (src["id"], it["source"] or src["name"].replace("Google News · ", ""), it["guid"], it["title"],
                  it["link"], pub, it["excerpt"], it["image"], sec, score, now, norm))
            new += 1
            if norm:
                recent.add(norm)
        db.x("UPDATE news_sources SET last_fetch=?, last_status=?, last_count=? WHERE id=?",
             (now, f"ok: {len(items)} stories, {new} new", new, src["id"]))
    except Exception as e:                               # noqa: BLE001 - one bad feed never stops the others
        db.x("UPDATE news_sources SET last_fetch=?, last_status=? WHERE id=?", (time.time(), f"error: {e}"[:200], src["id"]))
    finally:
        if own:
            client.close()
    return new


def fetch_all() -> dict:
    _schema()
    import httpx
    total, n = 0, 0
    with httpx.Client(timeout=20, follow_redirects=True, headers={
            "User-Agent": f"ATC-SIM-News/1.0 (+{config.PUBLIC_URL or 'aviation news reader'})",
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}) as c:
        for src in db.q("SELECT * FROM news_sources WHERE enabled=1"):
            total += fetch_source(src, c)
            n += 1
    keep = settings()["keep_days"]
    db.x("DELETE FROM news_items WHERE published < ?", (time.time() - keep * 86400,))   # (issues keep their own copy)
    return {"sources": n, "new": total}


def classify(text: str, hint: Optional[str]) -> tuple:
    """(section, score) by keywords; the source's own section when nothing says otherwise."""
    t = text.lower()
    best, score = None, 0.0
    for pat, w, sec in KEYWORDS:
        k = len(re.findall(pat, t))
        if k:
            score += w * min(k, 3)
            if best is None or w > best[1]:
                best = (sec, w)
    sec = best[0] if best and (best[1] >= 3 or not hint) else (hint or (best[0] if best else "Airlines & airports"))
    return sec, score


# ------------------------------------------------------------------ the newsroom's own articles
# Written every morning by the newsroom (a scheduled agent, tools/newsdesk/INSTRUCTIONS.md) and pushed to the `news`
# branch of the repository; read from there (or a local folder) every hour. Everything is checked again here: no HTML
# in the text, https links only, figures that are plain SVG (no script, nothing loaded from elsewhere) and PNG.
import os as _os

ARTICLES_URL = _os.environ.get("ATC_ARTICLES_URL", "https://raw.githubusercontent.com/BlueSkyGuardian/atc-sim/news/articles").rstrip("/")
MEDIA = _os.path.join(config.DATA_DIR, "news_media")
REGION_ORDER = ("morocco", "europe", "international")
REGION_NAMES = {"morocco": "Morocco", "europe": "Europe", "international": "International"}
_SLUG = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")
_FILE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,80}\.(svg|png)$")
_UNSAFE_SVG = ("<script", "javascript:", "<foreignobject", "<iframe", "<object", "<embed", " onload", " onclick",
               " onerror", " onmouse", "xlink:href=\"http", "href=\"http")
ART_STATE = {"last_sync": None, "status": None}


def _get(rel: str, client) -> bytes:
    if ARTICLES_URL.startswith(("http://", "https://")):
        r = client.get(f"{ARTICLES_URL}/{rel}", params={"t": int(time.time() // 300)})     # past the CDN's cache
        r.raise_for_status()
        return r.content
    with open(_os.path.join(ARTICLES_URL, rel), "rb") as f:
        return f.read()


def _clean_body(md: str) -> str:
    md = re.sub(r"<[^>]*>", "", md)                                       # no HTML, ever
    md = re.sub(r"\]\(\s*(?!https://)[^)]*\)", "]", md)                    # links: https only
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)                            # images: only the figures
    return md.strip()


def _take_figure(slug: str, f: dict, c) -> Optional[dict]:
    """Download one figure (SVG for the site, PNG for the e-mails), checked: never anything that runs."""
    sv, pn = str(f.get("file") or ""), str(f.get("png") or "")
    if not (_FILE.match(sv) and sv.endswith(".svg") and _FILE.match(pn) and pn.endswith(".png")):
        return None
    svg = _get(f"{slug}/{sv}", c)
    low = svg[:2_000_000].decode("utf-8", "replace").lower()
    if any(u in low for u in _UNSAFE_SVG) or not low.lstrip().startswith("<svg") or len(svg) > 2_000_000:
        raise ValueError(f"unsafe figure {sv}")
    png = _get(f"{slug}/{pn}", c)
    if not png.startswith(b"\x89PNG") or len(png) > 3_000_000:
        raise ValueError(f"bad png {pn}")
    open(_os.path.join(MEDIA, slug, sv), "wb").write(svg)
    open(_os.path.join(MEDIA, slug, pn), "wb").write(png)
    return {"file": sv, "png": pn, "caption": clean(f.get("caption"), 300), "alt": clean(f.get("alt"), 300),
            "credit": clean(f.get("credit"), 120)}


def sync_articles() -> dict:
    """Read the newsroom's index and take in the new or changed articles."""
    _schema()
    import httpx
    new, bad = 0, []
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "ATC-SIM-News/1.0"}) as c:
        try:
            idx = json.loads(_get("index.json", c)).get("articles") or []
        except Exception as e:                           # noqa: BLE001
            ART_STATE.update(last_sync=time.time(), status=f"index: {e}"[:200])
            return {"new": 0, "error": str(e)}
        for a in idx[:300]:
            slug = str(a.get("slug") or "")
            if not _SLUG.match(slug):
                continue
            row = db.q1("SELECT updated FROM news_articles WHERE slug=?", (slug,))
            if row and int(row["updated"] or 0) >= int(a.get("updated") or 0):
                continue
            try:
                m = json.loads(_get(f"{slug}/meta.json", c))
                body = _clean_body(_get(f"{slug}/body.md", c).decode("utf-8"))
                if m.get("region") not in REGION_ORDER or not m.get("title") or len(body) < 400:
                    raise ValueError("incomplete")
                _os.makedirs(_os.path.join(MEDIA, slug), exist_ok=True)
                figs = [_take_figure(slug, f, c) for f in (m.get("figures") or [])[:3]]
                figs = [f for f in figs if f]
                # the French and Arabic versions: their text, and their figures' captions (or figures of their own)
                i18n = {}
                for lg, tr in (m.get("translations") or {}).items():
                    if lg not in L.LANGS or lg == "en" or not isinstance(tr, dict) or not tr.get("title"):
                        continue
                    try:
                        tbody = _clean_body(_get(f"{slug}/body.{lg}.md", c).decode("utf-8"))
                    except Exception:                    # noqa: BLE001 - no body: that language is not there
                        continue
                    if len(tbody) < 300:
                        continue
                    tf = []
                    for k, f in enumerate(figs):
                        x = ((tr.get("figures") or [])[k:k + 1] or [{}])[0]
                        own = _take_figure(slug, x, c) if x.get("file") and x.get("png") else None
                        tf.append(dict(own or f, caption=clean(x.get("caption") or f["caption"], 300),
                                       alt=clean(x.get("alt") or f["alt"], 300)))
                    i18n[lg] = {"title": clean(tr["title"], 140), "summary": clean(tr.get("summary"), 400), "body": tbody,
                                "figures": tf}
                srcs = [{"title": clean(x.get("title"), 200), "url": str(x.get("url")), "site": clean(x.get("site"), 80)}
                        for x in (m.get("sources") or [])[:12] if str(x.get("url", "")).startswith("https://")]
                db.x("INSERT OR REPLACE INTO news_articles (slug, date, region, section, title, summary, body, sources, figures, "
                     "author, updated, synced_at, i18n) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (slug, str(m.get("date") or slug[:10]), m["region"], m.get("section") if m.get("section") in SECTIONS else None,
                      clean(m["title"], 140), clean(m.get("summary"), 400), body, json.dumps(srcs), json.dumps(figs),
                      clean(m.get("author") or "ATC SIM newsroom", 80), int(a.get("updated") or time.time()), time.time(),
                      json.dumps(i18n, ensure_ascii=False)))
                new += 1
                for lg in ("en", "fr"):
                    try:
                        _share_card(slug, rebuild=True, lang=lg)
                    except Exception as e:               # noqa: BLE001 - no preview picture is not a reason to refuse
                        log.warning("share card %s: %s", slug, e)
            except Exception as e:                       # noqa: BLE001 - one bad article never stops the others
                bad.append(f"{slug}: {e}")
    ART_STATE.update(last_sync=time.time(), status=f"ok: {len(idx)} in the index, {new} new or changed"
                     + (f"; refused: {'; '.join(bad)[:300]}" if bad else ""))
    return {"new": new, "refused": bad}


def _article_out(r: dict, full: bool = False, lang: str = "en") -> dict:
    """An article in a language: its translation when the newsroom wrote one (lang says which it is, langs which
    exist), else the English original."""
    figs = json.loads(r["figures"] or "[]")
    i18n = json.loads(r.get("i18n") or "{}")
    tr = i18n.get(lang) if lang != "en" else None
    got = lang if tr else "en"
    tr = tr or {}
    figs = tr.get("figures") or figs
    out = {"slug": r["slug"], "date": r["date"], "region": r["region"], "region_name": L.region(r["region"], lang),
           "section": r["section"], "section_name": L.section(r["section"], lang),
           "title": tr.get("title") or r["title"], "summary": tr.get("summary") or r["summary"],
           "author": L.t("newsroom", lang) if (r["author"] or "ATC SIM newsroom") == "ATC SIM newsroom" else r["author"],
           "lang": got, "langs": ["en"] + [x for x in L.LANGS if x in i18n],
           "figures": [dict(f, url=f"/news/media/{r['slug']}/{f['file']}", png_url=f"/news/media/{r['slug']}/{f['png']}") for f in figs]}
    if full:
        out.update(body=tr.get("body") or r["body"], sources=json.loads(r["sources"] or "[]"))
    return out


def articles(days: Optional[int] = None, region: Optional[str] = None, limit: int = 30, since_day: Optional[str] = None,
             lang: str = "en", slugs: Optional[List[str]] = None) -> List[dict]:
    _schema()
    sql, args = "SELECT * FROM news_articles WHERE 1=1", []
    if region:
        sql += " AND region=?"
        args.append(region)
    if since_day:
        sql += " AND date >= ?"
        args.append(since_day)
    if slugs is not None:
        sql += f" AND slug IN ({','.join('?' * len(slugs)) or 'NULL'})"
        args += list(slugs)
    sql += " ORDER BY date DESC, CASE region WHEN 'morocco' THEN 0 WHEN 'europe' THEN 1 ELSE 2 END, slug LIMIT ?"
    args.append(limit)
    return [_article_out(r, lang=lang) for r in db.q(sql, tuple(args))]


# ------------------------------------------------------------------ the daily issue
def candidates(hours: float = 26.0, limit: int = 80) -> List[dict]:
    since = time.time() - hours * 3600
    rows = db.q("SELECT * FROM news_items WHERE published > ? ORDER BY published DESC LIMIT 400", (since,))
    # the best of each source first, so that no single site fills the issue
    rows.sort(key=lambda r: -(r["score"] + 2.0 * math.exp(-(time.time() - r["published"]) / 43200)))
    per, out = {}, []
    for r in rows:
        k = r["source"] or ""
        if per.get(k, 0) >= 6:
            continue
        per[k] = per.get(k, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _heuristic(cands: List[dict], n: int) -> dict:
    picked, per = [], {}
    for r in cands:
        if per.get(r["source"], 0) >= 2:
            continue
        per[r["source"]] = per.get(r["source"], 0) + 1
        picked.append(r)
        if len(picked) >= n:
            break
    secs: Dict[str, list] = {}
    for r in picked:
        secs.setdefault(r["section"] or "Airlines & airports", []).append(
            {"id": r["id"], "summary": r["excerpt"] or ""})
    order = [s for s in SECTIONS if s in secs]
    top = picked[0]["title"] if picked else "Aviation news"
    return {"title": top[:120], "intro": "", "sections": [{"name": s, "items": secs[s]} for s in order], "model": None}


SYSTEM = (
    "You edit a short daily aviation newsletter for air traffic controllers, pseudo-pilots and instructors of an air "
    "traffic control training simulator in Morocco. From the stories given (id, source, headline, excerpt), pick the "
    "{n} most useful to them: air traffic control, airspace and air navigation first, then technology and innovation, "
    "safety, Morocco and Africa, then the biggest airline, airport and industry news. Leave out duplicates, adverts, "
    "listicles, opinion pieces and stories with nothing new. Sort the picked stories into these sections, in this "
    "order, leaving empty ones out: {sections}. For each, write a summary of one or two sentences in plain English "
    "from the headline and excerpt ONLY - add no fact, figure or name that is not in them; if the excerpt is empty, "
    "say only what the headline says. Give the issue a title (the main story, under 90 characters) and an "
    "introduction of two or three sentences linking the main stories. The newsletter goes out in English, French and "
    "Arabic: write the title, the introduction and every summary in English, then the same in French and in Modern "
    "Standard Arabic (faithful translations, aviation terms as French and Arabic aviation writers use them; ICAO "
    "codes, callsigns and names unchanged). Answer with JSON only: "
    '{{"title": "...", "intro": "...", "sections": [{{"name": "<section>", "items": [{{"id": <id>, "summary": "..."}}]}}], '
    '"i18n": {{"fr": {{"title": "...", "intro": "...", "summaries": {{"<id>": "..."}}}}, '
    '"ar": {{"title": "...", "intro": "...", "summaries": {{"<id>": "..."}}}}}}}}')


def _with_model(cands: List[dict], n: int, model_id: int) -> dict:
    from .ailab import _model
    from ..ailab.agents import ChatModel
    p = _model(int(model_id), secret=True)
    p = dict(p, params={**(p.get("params") or {}), "max_tokens": 8000, "temperature": 0.3, "json_mode": True,
                        "timeout_s": 120, "retries": 2})
    m = ChatModel(p)
    lines = [f"{r['id']} | {r['source']} | {r['title']} | {clean(r['excerpt'], 260)}" for r in cands]
    msgs = [{"role": "system", "content": SYSTEM.format(n=n, sections=", ".join(SECTIONS))},
            {"role": "user", "content": "Stories of the last day:\n" + "\n".join(lines)}]
    text, _usage, _dt = m.chat(msgs)
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    try:
        j = json.loads(t[t.index("{"): t.rindex("}") + 1])
    except ValueError:
        raise ValueError(f"the answer was not the JSON asked for: {t[:80]!r}")
    ids = {r["id"] for r in cands}
    secs, seen = [], set()
    for s in j.get("sections") or []:
        name = s.get("name") if s.get("name") in SECTIONS else None
        items = []
        for it in s.get("items") or []:
            try:
                i = int(it.get("id"))
            except (TypeError, ValueError):
                continue
            if i in ids and i not in seen:
                seen.add(i)
                items.append({"id": i, "summary": clean(str(it.get("summary") or ""), 420)})
        if name and items:
            secs.append({"name": name, "items": items})
    if not secs:
        raise ValueError("the model picked no story it was given")
    secs.sort(key=lambda s: SECTIONS.index(s["name"]))
    i18n = {}
    for lg in ("fr", "ar"):
        x = (j.get("i18n") or {}).get(lg) or {}
        if not isinstance(x, dict) or not x.get("title"):
            continue
        sm = {}
        for k, v in (x.get("summaries") or {}).items():
            try:
                if int(k) in seen:
                    sm[str(int(k))] = clean(str(v or ""), 480)
            except (TypeError, ValueError):
                continue
        i18n[lg] = {"title": clean(str(x["title"]), 140), "intro": clean(str(x.get("intro") or ""), 800), "summaries": sm}
    return {"title": clean(str(j.get("title") or ""), 120) or "Aviation news", "intro": clean(str(j.get("intro") or ""), 700),
            "sections": secs, "model": p.get("name") or p.get("model"), "i18n": i18n}


def build(day: Optional[str] = None, replace: bool = False) -> dict:
    """Put the day's issue together (and keep it, as a draft until it is sent). day: YYYY-MM-DD (local)."""
    _schema()
    s = settings()
    day = day or datetime.now(_tz(s)).strftime("%Y-%m-%d")
    old = db.q1("SELECT * FROM news_issues WHERE day=?", (day,))
    if old and not replace:
        return issue(old["id"])
    if old and old["status"] == "sent":
        raise ValueError(f"the issue of {day} was sent already")
    try:
        sync_articles()
    except Exception as e:                               # noqa: BLE001
        log.warning("newsroom sync: %s", e)
    cands = candidates()
    if not cands and not articles(since_day=(datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d"), limit=1):
        raise ValueError("no stories in the last day (read the feeds first)")
    n = int(s.get("max_items") or 10)
    built, why = None, ""
    if s.get("model_id"):
        try:
            built = _with_model(cands, n, s["model_id"])
        except Exception as e:                           # noqa: BLE001 - the newsletter goes out anyway
            why = f"model failed ({e}); picked by keywords"
            log.warning("newsletter model: %s", e)
    if built is None:
        built = _heuristic(cands, n) if cands else {"title": "Aviation news", "intro": "", "sections": [], "model": None}
        if why:
            built["model"] = why[:200]
    for sec in built["sections"]:
        full = []
        for it in sec["items"]:
            row = db.q1("SELECT * FROM news_items WHERE id=?", (it["id"],))
            if row is None:
                continue
            if it["summary"]:
                db.x("UPDATE news_items SET summary=? WHERE id=? AND (summary IS NULL OR summary='')", (it["summary"], it["id"]))
            full.append(_item_out(row, it["summary"] or row["excerpt"]))     # the issue keeps its own copy
        sec["items"] = full
    built["sections"] = [x for x in built["sections"] if x["items"]]
    # the newsroom's articles since the last issue sent (the day's, and the day before's if not sent yet)
    prev = db.q1("SELECT day FROM news_issues WHERE status='sent' AND day < ? ORDER BY day DESC LIMIT 1", (day,))
    since = (datetime.strptime(prev["day"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d") if prev else \
        (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    arts = [x for x in articles(since_day=since, limit=8) if x["date"] <= day]
    arts.sort(key=lambda x: (REGION_ORDER.index(x["region"]) if x["region"] in REGION_ORDER else 9, x["date"]))
    i18n = built.get("i18n") or {}
    if arts and not str(built.get("model") or "") or (arts and not cands):
        built["title"] = arts[0]["title"][:120]           # no model: the lead article is the issue's title (in each language)
        lead = articles(slugs=[arts[0]["slug"]], limit=1, lang="fr") + articles(slugs=[arts[0]["slug"]], limit=1, lang="ar")
        for x in lead:
            if x["lang"] != "en":
                i18n.setdefault(x["lang"], {"intro": "", "summaries": {}})["title"] = x["title"][:140]
    if old:
        db.x("UPDATE news_issues SET title=?, intro=?, sections=?, model=?, created_at=?, articles=?, i18n=? WHERE id=?",
             (built["title"], built["intro"], json.dumps(built["sections"]), built["model"], time.time(), json.dumps(arts),
              json.dumps(i18n, ensure_ascii=False), old["id"]))
        iid = old["id"]
    else:
        iid = db.x("INSERT INTO news_issues (day, title, intro, sections, model, created_at, articles, i18n) VALUES (?,?,?,?,?,?,?,?)",
                   (day, built["title"], built["intro"], json.dumps(built["sections"]), built["model"], time.time(), json.dumps(arts),
                    json.dumps(i18n, ensure_ascii=False)))
    return issue(iid)


def _item_out(r: dict, summary: Optional[str] = None) -> dict:
    return {"id": r["id"], "title": r["title"], "link": r["link"], "source": r["source"], "published": r["published"],
            "excerpt": r["excerpt"], "summary": summary if summary is not None else (r.get("summary") or ""),
            "section": r["section"], "image": r["image"], "site": urlparse(r["link"]).netloc.replace("www.", "")}


def issue(iid: int, lang: str = "en") -> dict:
    """An issue in a language: its title, introduction and summaries as the model translated them (else the English
    ones, or the feeds' own excerpts), the section names translated, the newsroom's articles in that language."""
    r = db.q1("SELECT * FROM news_issues WHERE id=?", (iid,))
    if r is None:
        raise HTTPException(404, "no such issue")
    secs = [x for x in json.loads(r["sections"]) if x.get("items")]
    arts = json.loads(r.get("articles") or "[]")
    tr = json.loads(r.get("i18n") or "{}").get(lang) if lang != "en" else None
    tr = tr or {}
    if lang != "en":
        sm = tr.get("summaries") or {}
        secs = [dict(sec, items=[dict(it, summary=sm.get(str(it["id"])) or it["summary"]) for it in sec["items"]]) for sec in secs]
    for sec in secs:
        sec["label"] = L.section(sec["name"], lang)
    if arts:
        now = {a["slug"]: a for a in articles(slugs=[a["slug"] for a in arts], limit=50, lang=lang)}
        arts = [now.get(a["slug"], a) for a in arts]
    return {"id": r["id"], "day": r["day"], "title": tr.get("title") or r["title"], "intro": tr.get("intro") or r["intro"],
            "sections": secs, "articles": arts, "lang": lang, "langs": ["en"] + [x for x in json.loads(r.get("i18n") or "{}")],
            "model": r["model"], "status": r["status"], "sent_at": r["sent_at"], "sent_count": r["sent_count"],
            "failed_count": r["failed_count"], "created_at": r["created_at"]}


# ------------------------------------------------------------------ the e-mail
def _base() -> str:
    return config.PUBLIC_URL or ""


def render(iss: dict, unsub_url: str = "", base: str = "") -> tuple:
    """(subject, text, html) of an issue's e-mail, in the issue's language (issue(iid, lang)): right to left in Arabic."""
    base = base or _base()
    lg = iss.get("lang") or "en"
    T = lambda k: L.t(k, lg)
    rtl = lg in L.RTL
    left = "right" if rtl else "left"
    q = "" if lg == L.DEFAULT else f"?lang={lg}"
    view = f"{base}/news/issue/{iss['id']}{q}" if base else ""
    day = L.long_date(iss["day"], lg)
    subject = f"{iss['title']} · {T('news')}, {L.short_date(iss['day'], lg)}"
    lines = [f"{T('news').upper()} · {day}", "", iss["title"], ""]
    if iss["intro"]:
        lines += [iss["intro"], ""]
    arts = iss.get("articles") or []
    link = lambda a: f"{base}/news/a/{a['slug']}{q}"
    if arts:
        lines += [T("from_newsroom").upper(), ""]
        for a in arts:
            lines += [f"• {a['title']} ({a['region_name']})", f"  {a['summary']}", f"  {link(a)}" if base else "", ""]
    for sec in iss["sections"]:
        lines += [(sec.get("label") or sec["name"]).upper(), ""]
        for it in sec["items"]:
            lines += [f"• {it['title']}", f"  {it['summary']}" if it["summary"] else "", f"  {it['source'] or it['site']} · {it['link']}", ""]
    if view:
        lines += [f"{T('read_on_site')}: {view}"]
    if unsub_url:
        lines += [f"{T('unsubscribe')}: {unsub_url}"]
    text = "\n".join(lines)
    e = html.escape
    newsroom = ""
    for k, a in enumerate(arts):
        fig = (a.get("figures") or [None])[0]
        img = (f'<a href="{e(link(a))}"><img src="{e(base)}{e(fig["png_url"])}" alt="{e(fig.get("alt") or "")}" '
               f'width="548" style="width:100%;max-width:548px;height:auto;border:1px solid #e1e6eb;border-radius:6px;display:block;margin:0 0 10px"></a>') \
            if fig and base and k < 3 else ""
        newsroom += f"""<tr><td style="padding:14px 0 16px;border-bottom:1px solid #eef1f4">{img}
<div style="color:#2f6fa8;font-size:11px;font-weight:700;letter-spacing:{'0' if rtl else '.08em'};text-transform:uppercase">{e(a['region_name'])}</div>
<a href="{e(link(a))}" style="color:#13263a;font-weight:700;font-size:17px;text-decoration:none;line-height:1.35">{e(a['title'])}</a>
<div style="color:#3c4a57;font-size:14px;line-height:1.6;margin-top:5px">{e(a['summary'])}</div>
<div style="margin-top:6px"><a href="{e(link(a))}" style="color:#c2185b;font-size:13px;font-weight:600;text-decoration:none">{e(T('read_article'))}</a></div></td></tr>"""
    cap = f"font-size:12px;font-weight:700;letter-spacing:{'0' if rtl else '.08em'};text-transform:uppercase"
    if newsroom:
        newsroom = f"""<tr><td style="padding:22px 0 4px;color:#c2185b;{cap}">{e(T('from_newsroom'))}</td></tr>{newsroom}"""
    secs = ""
    for sec in iss["sections"]:
        rows = "".join(f"""<tr><td style="padding:10px 0 12px;border-bottom:1px solid #eef1f4">
<a href="{e(it['link'])}" style="color:#13263a;font-weight:650;font-size:15px;text-decoration:none;line-height:1.35">{e(it['title'])}</a>
{f'<div style="color:#3c4a57;font-size:14px;line-height:1.55;margin-top:4px">{e(it["summary"])}</div>' if it['summary'] else ''}
<div style="color:#8693a0;font-size:12px;margin-top:4px">{e(it['source'] or it['site'])}</div></td></tr>""" for it in sec["items"])
        secs += f"""<tr><td style="padding:22px 0 4px;color:#c2185b;{cap}">{e(sec.get('label') or sec['name'])}</td></tr>{rows}"""
    font = "Tahoma,'Segoe UI','Noto Naskh Arabic',Arial,sans-serif" if rtl else "system-ui,-apple-system,Segoe UI,Roboto,sans-serif"
    page = f"""<!doctype html><html lang="{lg}" dir="{L.direction(lg)}"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body dir="{L.direction(lg)}" style="margin:0;background:#f4f6f8;font-family:{font};color:#1d2733">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" dir="{L.direction(lg)}" style="max-width:600px;background:#fff;border:1px solid #e1e6eb;border-radius:10px;text-align:{left}">
<tr><td style="padding:22px 26px 6px"><div style="font-weight:800;letter-spacing:.02em" dir="ltr" align="{left}">ATC <span style="color:#c2185b">SIM</span>
<span style="color:#8693a0;font-weight:500;font-size:13px">· {e(T('news'))} · {e(day)}</span></div>
<h1 style="font-size:21px;line-height:1.35;margin:14px 0 8px">{e(iss['title'])}</h1>
{f'<p style="font-size:15px;line-height:1.6;color:#3c4a57;margin:0">{e(iss["intro"])}</p>' if iss['intro'] else ''}</td></tr>
<tr><td style="padding:0 26px 10px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="text-align:{left}">{newsroom}{(f'<tr><td style="padding:22px 0 0;color:#8693a0;{cap}">{e(T("around_web"))}</td></tr>' if newsroom and secs else '')}{secs}</table></td></tr>
<tr><td style="padding:14px 26px 22px;color:#8693a0;font-size:12px;line-height:1.6">{e(T('footer'))}
{f'<a href="{e(view)}" style="color:#8693a0">{e(T("read_on_site"))}</a>.' if view else ''}
{f' {e(T("why"))} <a href="{e(unsub_url)}" style="color:#8693a0">{e(T("unsubscribe"))}</a>.' if unsub_url else ''}</td></tr>
</table></td></tr></table></body></html>"""
    return subject, text, page


def send_issue(iid: int, only: Optional[List[str]] = None, now: Optional[float] = None) -> dict:
    """Mail an issue to the confirmed subscribers (or only to these addresses: a test). Marks it sent."""
    issue(iid)                                         # (exists)
    s = settings()
    base = _base()
    now = now or time.time()
    if only:
        subs = [{"email": a, "token": "", "id": None, "lang": lang_of_email(a)} for a in only]
    else:
        # each in their language: the profile's for an account, else the one they subscribed in (French by default)
        subs = db.q("SELECT s.*, COALESCE(u.news_lang, s.lang) AS lang FROM news_subscribers s LEFT JOIN users u "
                    "ON u.email = s.email WHERE s.confirmed_at IS NOT NULL AND s.unsub_at IS NULL "
                    "AND (s.last_sent IS NULL OR s.last_sent < ?) ORDER BY s.id LIMIT ?",
                    (now - 12 * 3600, int(s.get("max_per_day") or 450)))
    by_lang = {lg: issue(iid, lg) for lg in L.LANGS}
    sent, failed = 0, 0
    conn = None
    try:
        for i, sub in enumerate(subs):
            unsub = f"{base}/news/unsubscribe?t={sub['token']}" if sub.get("token") and base else ""
            subject, text, page = render(by_lang[L.norm(sub.get("lang")) or L.DEFAULT], unsub, base)
            hdr = {}
            if unsub:
                hdr = {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
            m = notify.compose(sub["email"], subject, text, page, hdr)
            try:
                if notify.SMTP_HOST and (conn is None or i % 40 == 0):
                    if conn is not None:
                        try:
                            conn.quit()
                        except Exception:                # noqa: BLE001
                            pass
                    conn = notify.connect()
                notify.deliver(m, conn)
                sent += 1
                if sub.get("id"):
                    db.x("UPDATE news_subscribers SET last_sent=? WHERE id=?", (now, sub["id"]))
            except Exception as e:                       # noqa: BLE001
                failed += 1
                log.warning("newsletter to %s: %s", sub["email"], e)
                conn = None
    finally:
        if conn is not None:
            try:
                conn.quit()
            except Exception:                            # noqa: BLE001
                pass
    if not only:
        db.x("UPDATE news_issues SET status='sent', sent_at=?, sent_count=sent_count+?, failed_count=failed_count+? "
             "WHERE id=?", (now, sent, failed, iid))
    return {"sent": sent, "failed": failed, "to": len(subs)}


# ------------------------------------------------------------------ the daily job
_thread: Optional[threading.Thread] = None
STATE = {"last_fetch": 0.0, "last_run": None, "last_error": None}


def tick(now: Optional[float] = None) -> Optional[str]:
    """One look at the clock: read the feeds when due; build and send the day's issue at its hour."""
    s = settings()
    if not s.get("enabled"):
        return None
    now = now or time.time()
    did = None
    with _job_lock:
        if now - STATE["last_fetch"] >= 60 * float(s.get("fetch_every_min") or 60):
            STATE["last_fetch"] = now
            r = fetch_all()
            did = f"fetched {r['new']} new"
            try:
                ra = sync_articles()
                did += f", {ra['new']} newsroom article(s)"
            except Exception as e:                       # noqa: BLE001
                log.warning("newsroom sync: %s", e)
        loc = datetime.fromtimestamp(now, _tz(s))
        day = loc.strftime("%Y-%m-%d")
        if s.get("send") and loc.hour >= int(s.get("send_hour") or 7):
            row = db.q1("SELECT * FROM news_issues WHERE day=?", (day,))
            if row is None or row["status"] != "sent":
                try:
                    iss = build(day) if row is None else issue(row["id"])
                    r = send_issue(iss["id"], now=now)
                    did = f"issue {day} sent to {r['sent']}"
                except ValueError as e:                  # nothing to send today
                    if row is None:
                        STATE["last_error"] = str(e)
    STATE["last_run"] = now
    return did


def _loop() -> None:
    time.sleep(20)                                    # let the server start first
    while True:
        try:
            tick()
        except Exception as e:                           # noqa: BLE001 - the loop never dies
            STATE["last_error"] = str(e)
            log.exception("news")
        time.sleep(60)


def start() -> None:
    """The background job (ATC_NEWS=off: none - tests, a machine without internet)."""
    global _thread
    import os
    if os.environ.get("ATC_NEWS", "on").lower() == "off":
        return
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="atcsim-news", daemon=True)
        _thread.start()


# ------------------------------------------------------------------ subscriptions
def subscribe_user(u: dict, on: bool) -> dict:
    _schema()
    email = u["email"].lower()
    r = db.q1("SELECT * FROM news_subscribers WHERE email=?", (email,))
    now = time.time()
    if on:
        if r is None:
            db.x("INSERT INTO news_subscribers (email, user_id, token, created_at, confirmed_at, lang) VALUES (?,?,?,?,?,?)",
                 (email, u["id"], secrets.token_urlsafe(24), now, now, user_lang(u)))
        else:
            db.x("UPDATE news_subscribers SET user_id=?, confirmed_at=COALESCE(confirmed_at, ?), unsub_at=NULL WHERE id=?",
                 (u["id"], now, r["id"]))
    elif r is not None:
        db.x("UPDATE news_subscribers SET unsub_at=? WHERE id=?", (now, r["id"]))
    return user_status(u)


def subscribe_default(u: dict) -> None:
    """A new account: subscribed (unless that address unsubscribed before - their choice stands)."""
    _schema()
    if u.get("email") and "@" in u["email"] and db.q1("SELECT 1 AS x FROM news_subscribers WHERE email=?", (u["email"].lower(),)) is None:
        subscribe_user(u, True)


def user_status(u: dict) -> dict:
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE email=?", (u["email"].lower(),))
    return {"subscribed": bool(r and r["confirmed_at"] and not r["unsub_at"]), "lang": user_lang(u)}


def user_lang(u: Optional[dict]) -> str:
    """The language an account reads the news in (its profile's; French until it picks one)."""
    if not u or not u.get("id"):
        return L.DEFAULT
    row = db.user_by_id(u["id"]) or {}
    return L.norm(row.get("news_lang")) or L.DEFAULT


def set_user_lang(u: dict, lang: str) -> str:
    """The profile's news language: the newsletter comes in it, the news page opens in it."""
    _schema()
    lg = L.norm(lang)
    if lg is None:
        raise HTTPException(400, f"news language: one of {', '.join(L.LANGS)}")
    db.x("UPDATE users SET news_lang=? WHERE id=?", (lg, u["id"]))
    db.x("UPDATE news_subscribers SET lang=? WHERE email=?", (lg, u["email"].lower()))
    return lg


def lang_of_email(email: str) -> str:
    _schema()
    u = db.user_by_email(email)
    if u and L.norm(u.get("news_lang")):
        return L.norm(u["news_lang"])
    r = db.q1("SELECT lang FROM news_subscribers WHERE email=?", (email.lower(),))
    return L.norm(r and r["lang"]) or L.DEFAULT


def request_lang(request: Request, given: Optional[str] = None) -> str:
    """The news page's language: asked for (?lang=), else the page's own choice (cookie), else the reader's profile,
    else French."""
    for v in (given, request.query_params.get("lang"), request.cookies.get("news_lang")):
        if L.norm(v):
            return L.norm(v)
    try:
        u = current_user(request)
    except Exception:                                    # noqa: BLE001
        u = None
    return user_lang(u) if u and u.get("id") else L.DEFAULT


_rate: Dict[str, list] = {}


def _limited(key: str, n: int, per_s: float) -> bool:
    now = time.time()
    lst = [t for t in _rate.get(key, []) if now - t < per_s]
    lst.append(now)
    _rate[key] = lst
    return len(lst) > n


# ------------------------------------------------------------------ pages and API
STATIC = config.ROOT + "/atcsim/static"


def _card_name(lang: str) -> str:
    """The link-preview picture of a language (Arabic shows the English one: the card's text is drawn left to right)."""
    return "share.fr.jpg" if lang == "fr" else "share.jpg"


def _share_card(slug: str, rebuild: bool = False, lang: str = "en") -> Optional[str]:
    """The article's link-preview picture (built once, when the article comes in), in English and in French."""
    from . import share
    lang = "fr" if lang == "fr" else "en"
    out = _os.path.join(MEDIA, slug, _card_name(lang))
    if _os.path.exists(out) and not rebuild:
        return out
    r = db.q1("SELECT * FROM news_articles WHERE slug=?", (slug,))
    if r is None:
        return None
    a = _article_out(r, lang=lang)
    png = _os.path.join(MEDIA, slug, a["figures"][0]["png"]) if a["figures"] else None
    day = L.short_date(r["date"], a["lang"]) + " " + r["date"][:4]
    return share.card(out, a["title"], f"{a['region_name']} · {day}", png)


def _site_base(request: Request) -> str:
    from . import seo
    return seo.base_url(request)


SITE_DESCRIPTION = ("Aviation news for air traffic controllers: Morocco first, then Europe and the world - air traffic control "
                    "and airspace, technology, safety, airlines and airports.")

_INLINE = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\((https://[^)\s]+)\)")


def _inline(t: str) -> str:
    """One line of the article's Markdown, escaped: **bold** and [links](https://...) only."""
    out, i = [], 0
    for m in _INLINE.finditer(t):
        out.append(html.escape(t[i:m.start()]))
        if m.group(1) is not None:
            out.append(f"<strong>{html.escape(m.group(1))}</strong>")
        else:
            out.append(f'<a href="{html.escape(m.group(3), quote=True)}" rel="noopener nofollow" target="_blank">{html.escape(m.group(2))}</a>')
        i = m.end()
    out.append(html.escape(t[i:]))
    return "".join(out)


def _md_html(md: str, figures: List[dict]) -> str:
    """The article's body as HTML, for the page as served (search engines, link readers, no JavaScript): headings,
    paragraphs, lists, bold, https links and the figures; the browser then draws it again with the full Markdown."""
    figs = {f["file"]: f for f in figures}
    used, blocks, para, items = set(), [], [], []

    def fig(f):
        return (f'<figure><img src="{html.escape(f["url"], quote=True)}" alt="{html.escape(f.get("alt") or "", quote=True)}">'
                f'<figcaption>{html.escape(f.get("caption") or "")}</figcaption></figure>')

    def flush():
        if para:
            blocks.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            items.clear()
    for line in md.splitlines():
        t = line.strip()
        m = re.fullmatch(r"\{\{figure:([^}]+)\}\}", t)
        if not t:
            flush()
        elif m:
            flush()
            if m.group(1) in figs:
                used.add(m.group(1))
                blocks.append(fig(figs[m.group(1)]))
        elif t.startswith("#"):
            flush()
            lvl = min(4, max(2, len(t) - len(t.lstrip("#"))))
            blocks.append(f"<h{lvl}>{_inline(t.lstrip('#').strip())}</h{lvl}>")
        elif re.match(r"^([-*]|\d+\.)\s+", t):
            if para:
                flush()
            items.append(re.sub(r"^([-*]|\d+\.)\s+", "", t))
        else:
            if items:
                flush()
            para.append(t)
    flush()
    rest = [fig(f) for f in figures if f["file"] not in used]
    if rest:
        k = next((i for i, x in enumerate(blocks) if x.startswith("<p>")), -1)
        blocks[k + 1:k + 1] = rest
    return "\n".join(blocks)


def _lang_url(url: str, lang: str) -> str:
    """The address of a page in a language (French, the default, has none)."""
    return url if lang == L.DEFAULT else f"{url}?lang={lang}"


def _news_html(request: Request, *, title: str, description: str, url: str, image: str, kind: str = "website",
               jsonld: Optional[list] = None, extra: Optional[Dict[str, str]] = None, fill=None, status: int = 200,
               robots: Optional[str] = None, og_title: Optional[str] = None, lang: str = L.DEFAULT,
               langs: Optional[List[str]] = None) -> HTMLResponse:
    """The news page as served: in its language (lang, dir), its versions in the other languages linked (hreflang)."""
    from . import seo
    page = seo.static_page("news.html")
    page = page.replace('<html lang="en">', f'<html lang="{lang}" dir="{L.direction(lang)}" data-news-lang="{lang}">', 1)
    if fill:
        page = fill(page)
    langs = langs or list(L.LANGS)
    tags = seo.head_tags(title=og_title or title, description=description, url=_lang_url(url, lang), image=image, kind=kind,
                         jsonld=jsonld, extra={"og:locale": L.OG_LOCALE[lang], **(extra or {})}, robots=robots)
    tags += "".join(f'\n<link rel="alternate" hreflang="{x}" href="{html.escape(_lang_url(url, x), quote=True)}">' for x in langs)
    tags += f'\n<link rel="alternate" hreflang="x-default" href="{html.escape(url, quote=True)}">'
    r = HTMLResponse(seo.with_head(page, title, tags), status_code=status)
    r.headers["Vary"] = "Cookie"
    r.headers["Content-Language"] = lang
    return r


def _card_html(a: dict, lang: str = L.DEFAULT) -> str:
    f = (a.get("figures") or [None])[0]
    img = f'<img src="{html.escape(f["url"], quote=True)}" alt="{html.escape(f.get("alt") or "", quote=True)}" loading="lazy">' if f else ""
    day = f" · {L.short_date(a['date'], lang)}"
    other = f' lang="{a["lang"]}" dir="{L.direction(a["lang"])}"' if a.get("lang") and a["lang"] != lang else ""
    return (f'<a class="card acard" href="{_lang_url("/news/a/" + a["slug"], lang)}"{other}>{img}<div class="in"><span class="reg">{html.escape(a["region_name"])}{day}</span>'
            f'<span class="t">{html.escape(a["title"])}</span><span class="s">{html.escape(a.get("summary") or "")}</span></div></a>')


@router.get("/news", include_in_schema=False)
@router.get("/news/issue/{iid}", include_in_schema=False)
def news_page(request: Request, iid: Optional[int] = None, lang: Optional[str] = None):
    from . import seo
    base = _site_base(request)
    _schema()
    lg = request_lang(request, lang)
    title, desc, status, robots = f"{L.t('news', lg)} · ATC SIM", L.t("description", lg), 200, None
    if iid is not None:
        r = db.q1("SELECT status FROM news_issues WHERE id=?", (iid,))
        if r and r["status"] == "sent":
            iss = issue(iid, lg)
            title = f"{iss['title']} · ATC SIM"
            desc = clean(iss["intro"] or desc, 300)
        else:
            title, status, robots = "Not found · ATC SIM", 404, "noindex"
    url = base + (f"/news/issue/{iid}" if iid is not None else "/news")
    arts = articles(limit=12, lang=lg) if iid is None else []
    ld = [{"@context": "https://schema.org", "@type": "CollectionPage", "name": title, "description": desc, "url": _lang_url(url, lg),
           "inLanguage": lg, "publisher": seo.organization(base),
           **({"mainEntity": {"@type": "ItemList", "itemListElement": [
               {"@type": "ListItem", "position": i + 1, "url": base + _lang_url(f"/news/a/{a['slug']}", lg), "name": a["title"]}
               for i, a in enumerate(arts)]}} if arts else {})}]

    def fill(page: str) -> str:                      # the newsroom's latest, in the page as served
        if not arts:
            return page
        page = page.replace('<div class="room" id="room"></div>', '<div class="room" id="room">' + "".join(_card_html(a, lg) for a in arts) + "</div>", 1)
        return page.replace('<div id="roomWrap" hidden>', '<div id="roomWrap">', 1)
    return _news_html(request, title=title, description=desc, url=url, image=base + "/news/share.jpg", jsonld=ld,
                      fill=fill, status=status, robots=robots, lang=lg)


@router.get("/news/share.jpg", include_in_schema=False)
def site_share():
    from . import share
    out = _os.path.join(MEDIA, "_site", "share.jpg")
    if not _os.path.exists(out):
        _os.makedirs(_os.path.dirname(out), exist_ok=True)
        share.site_card(out)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/news")
def list_news(section: Optional[str] = None, q: Optional[str] = None, before: Optional[float] = None, limit: int = 40,
              lang: Optional[str] = None):
    """Public: the latest stories (headline, excerpt or summary, source, link), newest first."""
    _schema()
    sql, args = "SELECT * FROM news_items WHERE 1=1", []
    if section:
        sql += " AND section=?"
        args.append(section)
    if q:
        sql += " AND (title LIKE ? OR excerpt LIKE ? OR source LIKE ?)"
        args += [f"%{q}%"] * 3
    if before:
        sql += " AND published < ?"
        args.append(before)
    sql += " ORDER BY published DESC LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    rows = db.q(sql, tuple(args))
    last = db.q1("SELECT MAX(fetched_at) AS t FROM news_items")
    lg = L.norm(lang) or L.DEFAULT
    return {"items": [dict(_item_out(r), section_name=L.section(r["section"], lg)) for r in rows], "sections": SECTIONS,
            "section_names": {x: L.section(x, lg) for x in SECTIONS}, "updated": last["t"] if last else None}


@router.get("/news/a/{slug}", include_in_schema=False)
def article_page(slug: str, request: Request, lang: Optional[str] = None):
    from . import seo
    base = _site_base(request)
    _schema()
    lg = request_lang(request, lang)
    r = db.q1("SELECT * FROM news_articles WHERE slug=?", (slug,)) if _SLUG.match(slug) else None
    if r is None:
        return _news_html(request, title="Article not found · ATC SIM", description=L.t("description", lg), url=base + "/news",
                          image=base + "/news/share.jpg", status=404, robots="noindex", lang=lg)
    a = _article_out(r, full=True, lang=lg)
    try:
        _share_card(slug, lang=a["lang"])
        img = f"{base}/news/media/{slug}/{_card_name(a['lang'])}?v={int(r['updated'] or 0)}"
    except Exception as e:                               # noqa: BLE001
        log.warning("share card %s: %s", slug, e)
        img = base + "/news/share.jpg"
    url, title, desc = f"{base}/news/a/{slug}", a["title"], a["summary"] or L.t("description", lg)
    modified = datetime.fromtimestamp(int(r["updated"] or time.time()), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    section = a["section_name"] or a["region_name"]
    ld = [{"@context": "https://schema.org", "@type": "NewsArticle", "mainEntityOfPage": {"@type": "WebPage", "@id": url},
           "headline": title[:110], "description": desc, "image": [img] + [base + f["png_url"] for f in a["figures"]],
           "datePublished": a["date"], "dateModified": modified, "articleSection": section, "inLanguage": a["lang"],
           "contentLocation": {"@type": "Place", "name": a["region_name"]},
           "author": {"@type": "Organization", "name": a["author"] or "ATC SIM newsroom", "url": base + "/news"},
           "publisher": seo.organization(base),
           "citation": [{"@type": "CreativeWork", "name": x["title"], "url": x["url"]} for x in a["sources"]]},
          {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
              {"@type": "ListItem", "position": 1, "name": L.t("news", lg), "item": base + _lang_url("/news", lg)},
              {"@type": "ListItem", "position": 2, "name": a["region_name"], "item": base + _lang_url("/news", lg)},
              {"@type": "ListItem", "position": 3, "name": title, "item": _lang_url(url, lg)}]}]

    def fill(page: str) -> str:                      # the article itself in the page as served (no JavaScript needed)
        srcs = "".join(f'<li><a href="{html.escape(x["url"], quote=True)}" target="_blank" rel="noopener nofollow">{html.escape(x["title"])}</a>'
                       f' <span class="dim">· {html.escape(x["site"])}</span></li>' for x in a["sources"])
        note = f'<p class="dim only">{html.escape(L.t("only_en", lg))}</p>' if a["lang"] != lg else ""
        body = (f'{note}<div lang="{a["lang"]}" dir="{L.direction(a["lang"])}"><span class="reg">{html.escape(a["region_name"])}{" · " + html.escape(a["section_name"]) if a["section_name"] else ""}</span>'
                f'<h1>{html.escape(title)}</h1><div class="dim" style="font-size:13px"><time datetime="{a["date"]}">{html.escape(L.long_date(a["date"], lg))}</time>'
                f' · {html.escape(a["author"])}</div><p class="lead">{html.escape(a["summary"] or "")}</p>'
                f'<div class="body">{_md_html(a["body"], a["figures"])}</div>'
                f'<h2 style="font-size:15px;margin-top:26px">{html.escape(L.t("sources", lg))}</h2><ul class="srcs">{srcs}</ul></div>')
        page = page.replace('<article class="card art" id="article"></article>', f'<article class="card art" id="article">{body}</article>', 1)
        page = page.replace('<div id="articleView" hidden>', '<div id="articleView">', 1)
        page = page.replace('<div class="nw-top">', '<div class="nw-top" style="display:none">', 1)     # the article's title is the page's h1
        return page.replace('<div id="latestView">', '<div id="latestView" hidden>', 1)
    return _news_html(request, title=f"{title} · ATC SIM", og_title=title, description=desc, url=url, image=img, kind="article", jsonld=ld,
                      extra={"article:published_time": a["date"], "article:modified_time": modified, "article:section": section,
                             "article:author": a["author"]}, fill=fill, lang=lg, langs=a["langs"])


@router.get("/api/news/articles")
def list_articles(request: Request, region: Optional[str] = None, limit: int = 30, lang: Optional[str] = None):
    """Public: the newsroom's articles, newest first (Morocco, then Europe, then international within a day), in a
    language (each says which it is in: an article not translated comes in English)."""
    lg = request_lang(request, lang)
    return {"articles": articles(region=region if region in REGION_ORDER else None, limit=max(1, min(limit, 100)), lang=lg),
            "regions": [{"id": r, "name": L.region(r, lg)} for r in REGION_ORDER], "lang": lg}


@router.get("/api/news/articles/{slug}")
def get_article(slug: str, request: Request, lang: Optional[str] = None):
    _schema()
    r = db.q1("SELECT * FROM news_articles WHERE slug=?", (slug,))
    if r is None:
        raise HTTPException(404, "no such article")
    return _article_out(r, full=True, lang=request_lang(request, lang))


@router.get("/news/media/{slug}/{name}", include_in_schema=False)
def media(slug: str, name: str):
    """A figure: SVG (the site) or PNG (the e-mails), never anything that runs."""
    if not (_SLUG.match(slug) and (_FILE.match(name) or name in ("share.jpg", "share.fr.jpg"))):
        raise HTTPException(404, "not found")
    path = _os.path.join(MEDIA, slug, name)
    if name.startswith("share.") and not _os.path.exists(path):
        try:
            _share_card(slug, lang="fr" if name == "share.fr.jpg" else "en")
        except Exception as e:                           # noqa: BLE001
            log.warning("share card %s: %s", slug, e)
    if not _os.path.exists(path):
        raise HTTPException(404, "not found")
    kind = "image/svg+xml" if name.endswith(".svg") else "image/jpeg" if name.endswith(".jpg") else "image/png"
    return FileResponse(path, media_type=kind, headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"})


@router.get("/api/news/issues")
def list_issues(request: Request, limit: int = 30, lang: Optional[str] = None):
    _schema()
    lg = request_lang(request, lang)
    rows = db.q("SELECT id, day, title, status, sent_at, i18n FROM news_issues ORDER BY day DESC LIMIT ?", (max(1, min(limit, 200)),))
    out = []
    for r in rows:
        if r["status"] != "sent":
            continue
        tr = json.loads(r.pop("i18n") or "{}").get(lg) or {}
        out.append(dict(r, title=tr.get("title") or r["title"], day_name=L.long_date(r["day"], lg)))
    return out


@router.get("/api/news/issues/{iid}")
def get_issue(iid: int, request: Request, lang: Optional[str] = None):
    _schema()
    iss = issue(iid, request_lang(request, lang))
    u = current_user(request)
    if iss["status"] != "sent" and not (u and u["role"] == "admin"):
        raise HTTPException(404, "no such issue")
    return iss


class Sub(BaseModel):
    email: str
    lang: Optional[str] = None               # the newsletter's language (the news page's when they subscribed)


@router.post("/api/news/subscribe")
def subscribe(body: Sub, request: Request):
    """Public: subscribe an address - a link is sent to confirm it (signed-in users: at once, their own address)."""
    _schema()
    u = current_user(request)
    email = body.email.strip().lower()
    lg = L.norm(body.lang) or request_lang(request)
    if u and u.get("email", "").lower() == email:
        return {"status": "subscribed", **subscribe_user(u, True)}
    if not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}", email):
        raise HTTPException(400, "give a valid e-mail address")
    ip = request.client.host if request.client else "?"
    if _limited("ip:" + ip, 5, 3600) or _limited("em:" + email, 2, 600):
        raise HTTPException(429, "too many requests: try again later")
    r = db.q1("SELECT * FROM news_subscribers WHERE email=?", (email,))
    now = time.time()
    if r and r["confirmed_at"] and not r["unsub_at"]:
        return {"status": "subscribed"}                     # already (nothing is revealed beyond that)
    if r is None:
        tok = secrets.token_urlsafe(24)
        db.x("INSERT INTO news_subscribers (email, token, created_at, ip, lang) VALUES (?,?,?,?,?)", (email, tok, now, ip, lg))
    else:
        tok = r["token"]
        db.x("UPDATE news_subscribers SET unsub_at=NULL, confirmed_at=NULL, ip=?, lang=? WHERE id=?", (ip, lg, r["id"]))
    base = notify.base_of(request)
    link = f"{base}/news/confirm?t={tok}"
    T = lambda k: L.t(k, lg)
    m = notify.compose(email, T("confirm_subject"),
                       f"{T('confirm_text')}\n\n{T('confirm_button')}: {link}\n\n{T('confirm_ignore')}\n\n-- \nATC SIM",
                       f"""<!doctype html><html lang="{lg}" dir="{L.direction(lg)}"><body dir="{L.direction(lg)}" style="font-family:system-ui,Tahoma,sans-serif;background:#f4f6f8;padding:24px;color:#1d2733">
<div style="max-width:520px;margin:auto;background:#fff;border:1px solid #e1e6eb;border-radius:10px;padding:24px 28px">
<div style="font-weight:800" dir="ltr">ATC <span style="color:#c2185b">SIM</span></div>
<p style="line-height:1.6">{html.escape(T('confirm_text'))}</p>
<p style="margin:22px 0"><a href="{html.escape(link)}" style="background:#c2185b;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600">{html.escape(T('confirm_button'))}</a></p>
<p style="color:#7a8794;font-size:12px">{html.escape(T('confirm_ignore'))}</p></div></body></html>""")
    try:
        notify.deliver(m)
        db.x("UPDATE news_subscribers SET confirm_sent=? WHERE email=?", (now, email))
    except Exception as e:                               # noqa: BLE001
        raise HTTPException(502, f"the confirmation e-mail could not be sent: {e}")
    return {"status": "confirm"}


@router.get("/news/confirm", include_in_schema=False)
def confirm(t: str = ""):
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE token=?", (t,)) if t else None
    if r is None:
        return _msg_page(MSG["invalid"][L.DEFAULT], MSG["invalid_body"][L.DEFAULT], lang=L.DEFAULT)
    lg = L.norm(r.get("lang")) or L.DEFAULT
    db.x("UPDATE news_subscribers SET confirmed_at=COALESCE(confirmed_at, ?), unsub_at=NULL WHERE id=?", (time.time(), r["id"]))
    return _msg_page(MSG["subscribed"][lg], MSG["subscribed_body"][lg], lang=lg)


@router.get("/news/unsubscribe", include_in_schema=False)
def unsubscribe_page(t: str = ""):
    """A page with a button (link scanners that open the link do not unsubscribe anyone)."""
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE token=?", (t,)) if t else None
    if r is None:
        return _msg_page(MSG["invalid"][L.DEFAULT], "", lang=L.DEFAULT)
    lg = lang_of_email(r["email"])
    if r["unsub_at"]:
        return _msg_page(MSG["unsubscribed"][lg], MSG["no_more"][lg].format(email=r["email"]), lang=lg)
    return _msg_page(MSG["ask_unsub"][lg],
                     f"""<form method="post" action="/news/unsubscribe?t={html.escape(t)}"><p>{html.escape(MSG["will_no_more"][lg].format(email=r['email']))}</p>
<button style="background:#c2185b;color:#fff;border:0;padding:10px 18px;border-radius:6px;font-weight:600;cursor:pointer">{html.escape(L.t("unsubscribe", lg))}</button></form>""", raw=True, lang=lg)


@router.post("/news/unsubscribe", include_in_schema=False)
def unsubscribe(t: str = ""):
    """The form's button, and the mail services' one-click unsubscribe (List-Unsubscribe-Post)."""
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE token=?", (t,)) if t else None
    if r is None:
        raise HTTPException(404, "not valid")
    db.x("UPDATE news_subscribers SET unsub_at=? WHERE id=?", (time.time(), r["id"]))
    lg = lang_of_email(r["email"])
    return _msg_page(MSG["unsubscribed"][lg], MSG["no_more"][lg].format(email=r["email"]) + " " + MSG["changed_mind"][lg], lang=lg)


MSG = {
    "invalid": {"en": "This link is not valid", "fr": "Ce lien n'est pas valide", "ar": "هذا الرابط غير صالح"},
    "invalid_body": {"en": "It may be old, or already used to unsubscribe. Subscribe again from the news page.",
                     "fr": "Il est peut-être ancien, ou a déjà servi à se désabonner. Abonnez-vous à nouveau depuis la page d'actualité.",
                     "ar": "ربما يكون قديماً أو استُعمل لإلغاء الاشتراك. اشترك من جديد من صفحة الأخبار."},
    "subscribed": {"en": "You are subscribed", "fr": "Vous êtes abonné", "ar": "تم اشتراكك"},
    "subscribed_body": {"en": "The aviation newsletter will reach you every morning. Every issue has a link to unsubscribe.",
                        "fr": "La lettre d'actualité aéronautique vous parviendra chaque matin. Chaque numéro contient un lien de désabonnement.",
                        "ar": "ستصلك نشرة أخبار الطيران كل صباح. يتضمن كل عدد رابطاً لإلغاء الاشتراك."},
    "unsubscribed": {"en": "You are unsubscribed", "fr": "Vous êtes désabonné", "ar": "تم إلغاء اشتراكك"},
    "no_more": {"en": "{email} gets no more newsletters.", "fr": "{email} ne recevra plus la lettre d'actualité.",
                "ar": "لن تصل النشرة بعد الآن إلى {email}."},
    "changed_mind": {"en": "Changed your mind? Subscribe again from the news page.",
                     "fr": "Vous avez changé d'avis ? Abonnez-vous à nouveau depuis la page d'actualité.",
                     "ar": "غيّرت رأيك؟ اشترك من جديد من صفحة الأخبار."},
    "ask_unsub": {"en": "Unsubscribe from the aviation newsletter?", "fr": "Se désabonner de la lettre d'actualité aéronautique ?",
                  "ar": "إلغاء الاشتراك في نشرة أخبار الطيران؟"},
    "will_no_more": {"en": "{email} will get no more newsletters.", "fr": "{email} ne recevra plus la lettre d'actualité.",
                     "ar": "لن تصل النشرة بعد الآن إلى {email}."},
}


def _msg_page(title: str, body: str, raw: bool = False, lang: str = "en") -> HTMLResponse:
    b = body if raw else f"<p>{html.escape(body)}</p>"
    return HTMLResponse(f"""<!doctype html><html lang="{lang}" dir="{L.direction(lang)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><meta name="robots" content="noindex"><link rel="stylesheet" href="/static/app.css"></head><body>
<main style="max-width:560px;padding-top:60px"><div class="card"><div style="font-weight:800;letter-spacing:.02em;margin-bottom:12px" dir="ltr">ATC <span style="color:var(--magenta)">SIM</span></div>
<h1 style="font-size:22px">{html.escape(title)}</h1>{b}<p><a href="{_lang_url('/news', lang)}">{html.escape(L.t('news', lang))}</a></p></div></main></body></html>""")


@router.get("/api/me/newsletter")
def my_newsletter(request: Request):
    return user_status(require_user(request))


class OnOff(BaseModel):
    on: Optional[bool] = None
    confirm: bool = False                    # unsubscribing is confirmed (the profile asks first)
    lang: Optional[str] = None               # the news language (the profile's): fr, en or ar


@router.put("/api/me/newsletter")
def set_my_newsletter(body: OnOff, request: Request):
    u = require_user(request)
    if body.lang is not None:
        set_user_lang(u, body.lang)
    if body.on is None:
        return user_status(u)
    if not body.on and not body.confirm:
        raise HTTPException(409, "confirm that you want to unsubscribe")
    return subscribe_user(u, body.on)


# ------------------------------------------------------------------ administration
def _admin(request: Request) -> dict:
    return require_user(request, roles=("admin",))


@router.get("/api/news/admin")
def admin_state(request: Request):
    _admin(request)
    _schema()
    s = settings()
    subs = db.q1("SELECT SUM(confirmed_at IS NOT NULL AND unsub_at IS NULL) AS active, SUM(confirmed_at IS NULL AND unsub_at IS NULL) AS pending, "
                 "SUM(unsub_at IS NOT NULL) AS left FROM news_subscribers")
    from .ailab import _model_row
    models = [_model_row(r) for r in db.q("SELECT * FROM ai_models WHERE kind IN ('openai','runpod') ORDER BY id")] \
        if db.q1("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_models'") else []
    return {"settings": s, "sections": SECTIONS, "sources": db.q("SELECT * FROM news_sources ORDER BY id"),
            "subscribers": {k: int(v or 0) for k, v in subs.items()}, "models": models, "mail": notify.mode(),
            "issues": db.q("SELECT id, day, title, status, sent_at, sent_count, failed_count, model FROM news_issues ORDER BY day DESC LIMIT 14"),
            "items_24h": db.q1("SELECT COUNT(*) AS n FROM news_items WHERE published > ?", (time.time() - 86400,))["n"],
            "job": STATE, "newsroom": {**ART_STATE, "source": ARTICLES_URL,
                                       "count": db.q1("SELECT COUNT(*) AS n FROM news_articles")["n"]}}


class SettingsIn(BaseModel):
    enabled: Optional[bool] = None
    send: Optional[bool] = None
    send_hour: Optional[int] = None
    tz: Optional[str] = None
    model_id: Optional[int] = None
    max_items: Optional[int] = None
    max_per_day: Optional[int] = None


@router.put("/api/news/admin/settings")
def put_settings(body: SettingsIn, request: Request):
    _admin(request)
    kw = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}
    if "send_hour" in kw and not (0 <= int(kw["send_hour"]) <= 23):
        raise HTTPException(400, "send_hour: 0 to 23")
    if "max_items" in kw:
        kw["max_items"] = max(3, min(int(kw["max_items"]), 25))
    if "model_id" in kw and kw["model_id"] in (0, -1):
        kw["model_id"] = None
    return _set(**kw)


class SourceIn(BaseModel):
    name: str
    url: str
    section: Optional[str] = None
    enabled: bool = True


@router.post("/api/news/admin/sources")
def add_source(body: SourceIn, request: Request):
    _admin(request)
    _schema()
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "the feed's address (https://...)")
    if body.section and body.section not in SECTIONS:
        raise HTTPException(400, f"section: one of {SECTIONS}")
    try:
        sid = db.x("INSERT INTO news_sources (name, url, section, enabled, created_at) VALUES (?,?,?,?,?)",
                   (body.name.strip()[:120] or urlparse(body.url).netloc, body.url.strip(), body.section, 1 if body.enabled else 0, time.time()))
    except Exception:                                    # noqa: BLE001
        raise HTTPException(409, "this feed is in the list already")
    src = db.q1("SELECT * FROM news_sources WHERE id=?", (sid,))
    fetch_source(src)                                    # read at once: the status tells whether it works
    return db.q1("SELECT * FROM news_sources WHERE id=?", (sid,))


@router.patch("/api/news/admin/sources/{sid}")
def edit_source(sid: int, body: SourceIn, request: Request):
    _admin(request)
    db.x("UPDATE news_sources SET name=?, url=?, section=?, enabled=? WHERE id=?",
         (body.name.strip()[:120], body.url.strip(), body.section, 1 if body.enabled else 0, sid))
    return db.q1("SELECT * FROM news_sources WHERE id=?", (sid,))


@router.delete("/api/news/admin/sources/{sid}")
def del_source(sid: int, request: Request):
    _admin(request)
    db.x("DELETE FROM news_sources WHERE id=?", (sid,))
    return {"deleted": sid}


@router.post("/api/news/admin/fetch")
def fetch_now(request: Request):
    _admin(request)
    with _job_lock:
        STATE["last_fetch"] = time.time()
        return fetch_all()


@router.post("/api/news/admin/sync")
def sync_now(request: Request):
    """Read the newsroom's articles now (instead of waiting for the hourly read)."""
    _admin(request)
    with _job_lock:
        r = sync_articles()
    return dict(r, status=ART_STATE.get("status"), count=db.q1("SELECT COUNT(*) AS n FROM news_articles")["n"])


class BuildIn(BaseModel):
    replace: bool = True


@router.post("/api/news/admin/build")
def build_now(body: BuildIn, request: Request):
    """Put today's issue together now (a draft: it goes out at its hour, or with 'send now')."""
    _admin(request)
    try:
        with _job_lock:
            return build(replace=body.replace)
    except ValueError as e:
        raise HTTPException(400, str(e))


class SendIn(BaseModel):
    test: bool = True


@router.post("/api/news/admin/issues/{iid}/send")
def send_now(iid: int, body: SendIn, request: Request):
    """test: to the administrator only (the issue stays a draft); else to every subscriber now."""
    u = _admin(request)
    with _job_lock:
        return send_issue(iid, only=[u["email"]] if body.test else None)
