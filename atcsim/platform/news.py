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
    "introduction of two or three sentences linking the main stories. Answer with JSON only: "
    '{{"title": "...", "intro": "...", "sections": [{{"name": "<section>", "items": [{{"id": <id>, "summary": "..."}}]}}]}}')


def _with_model(cands: List[dict], n: int, model_id: int) -> dict:
    from .ailab import _model
    from ..ailab.agents import ChatModel
    p = _model(int(model_id), secret=True)
    p = dict(p, params={**(p.get("params") or {}), "max_tokens": 2500, "temperature": 0.3, "json_mode": True,
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
    return {"title": clean(str(j.get("title") or ""), 120) or "Aviation news", "intro": clean(str(j.get("intro") or ""), 700),
            "sections": secs, "model": p.get("name") or p.get("model")}


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
    cands = candidates()
    if not cands:
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
        built = _heuristic(cands, n)
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
    if old:
        db.x("UPDATE news_issues SET title=?, intro=?, sections=?, model=?, created_at=? WHERE id=?",
             (built["title"], built["intro"], json.dumps(built["sections"]), built["model"], time.time(), old["id"]))
        iid = old["id"]
    else:
        iid = db.x("INSERT INTO news_issues (day, title, intro, sections, model, created_at) VALUES (?,?,?,?,?,?)",
                   (day, built["title"], built["intro"], json.dumps(built["sections"]), built["model"], time.time()))
    return issue(iid)


def _item_out(r: dict, summary: Optional[str] = None) -> dict:
    return {"id": r["id"], "title": r["title"], "link": r["link"], "source": r["source"], "published": r["published"],
            "excerpt": r["excerpt"], "summary": summary if summary is not None else (r.get("summary") or ""),
            "section": r["section"], "image": r["image"], "site": urlparse(r["link"]).netloc.replace("www.", "")}


def issue(iid: int) -> dict:
    r = db.q1("SELECT * FROM news_issues WHERE id=?", (iid,))
    if r is None:
        raise HTTPException(404, "no such issue")
    secs = [x for x in json.loads(r["sections"]) if x.get("items")]
    return {"id": r["id"], "day": r["day"], "title": r["title"], "intro": r["intro"], "sections": secs,
            "model": r["model"], "status": r["status"], "sent_at": r["sent_at"], "sent_count": r["sent_count"],
            "failed_count": r["failed_count"], "created_at": r["created_at"]}


# ------------------------------------------------------------------ the e-mail
def _base() -> str:
    return config.PUBLIC_URL or ""


def render(iss: dict, unsub_url: str = "", base: str = "") -> tuple:
    """(subject, text, html) of an issue's e-mail."""
    base = base or _base()
    view = f"{base}/news/issue/{iss['id']}" if base else ""
    day = datetime.strptime(iss["day"], "%Y-%m-%d").strftime("%A %d %B %Y")
    subject = f"{iss['title']} · Aviation news, {datetime.strptime(iss['day'], '%Y-%m-%d').strftime('%d %b')}"
    lines = [f"AVIATION NEWS · {day}", "", iss["title"], ""]
    if iss["intro"]:
        lines += [iss["intro"], ""]
    for s in iss["sections"]:
        lines += [s["name"].upper(), ""]
        for it in s["items"]:
            lines += [f"• {it['title']}", f"  {it['summary']}" if it["summary"] else "", f"  {it['source'] or it['site']} · {it['link']}", ""]
    if view:
        lines += [f"Read it on the site: {view}"]
    if unsub_url:
        lines += [f"Unsubscribe: {unsub_url}"]
    text = "\n".join(lines)
    e = html.escape
    secs = ""
    for s in iss["sections"]:
        rows = "".join(f"""<tr><td style="padding:10px 0 12px;border-bottom:1px solid #eef1f4">
<a href="{e(it['link'])}" style="color:#13263a;font-weight:650;font-size:15px;text-decoration:none;line-height:1.35">{e(it['title'])}</a>
{f'<div style="color:#3c4a57;font-size:14px;line-height:1.5;margin-top:4px">{e(it["summary"])}</div>' if it['summary'] else ''}
<div style="color:#8693a0;font-size:12px;margin-top:4px">{e(it['source'] or it['site'])}</div></td></tr>""" for it in s["items"])
        secs += f"""<tr><td style="padding:22px 0 4px;color:#c2185b;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase">{e(s['name'])}</td></tr>{rows}"""
    page = f"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f4f6f8;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1d2733">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#fff;border:1px solid #e1e6eb;border-radius:10px">
<tr><td style="padding:22px 26px 6px"><div style="font-weight:800;letter-spacing:.02em">ATC <span style="color:#c2185b">SIM</span>
<span style="color:#8693a0;font-weight:500;font-size:13px">· aviation news · {e(day)}</span></div>
<h1 style="font-size:21px;line-height:1.3;margin:14px 0 8px">{e(iss['title'])}</h1>
{f'<p style="font-size:15px;line-height:1.55;color:#3c4a57;margin:0">{e(iss["intro"])}</p>' if iss['intro'] else ''}</td></tr>
<tr><td style="padding:0 26px 10px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{secs}</table></td></tr>
<tr><td style="padding:14px 26px 22px;color:#8693a0;font-size:12px;line-height:1.5">Headlines and short summaries; each story is on its own site.
{f'<a href="{e(view)}" style="color:#8693a0">Read it on the site</a>.' if view else ''}
{f' You get this because you subscribed to the simulator\'s aviation newsletter. <a href="{e(unsub_url)}" style="color:#8693a0">Unsubscribe</a>.' if unsub_url else ''}</td></tr>
</table></td></tr></table></body></html>"""
    return subject, text, page


def send_issue(iid: int, only: Optional[List[str]] = None, now: Optional[float] = None) -> dict:
    """Mail an issue to the confirmed subscribers (or only to these addresses: a test). Marks it sent."""
    iss = issue(iid)
    s = settings()
    base = _base()
    now = now or time.time()
    if only:
        subs = [{"email": a, "token": "", "id": None} for a in only]
    else:
        subs = db.q("SELECT * FROM news_subscribers WHERE confirmed_at IS NOT NULL AND unsub_at IS NULL "
                    "AND (last_sent IS NULL OR last_sent < ?) ORDER BY id LIMIT ?",
                    (now - 12 * 3600, int(s.get("max_per_day") or 450)))
    sent, failed = 0, 0
    conn = None
    try:
        for i, sub in enumerate(subs):
            unsub = f"{base}/news/unsubscribe?t={sub['token']}" if sub.get("token") and base else ""
            subject, text, page = render(iss, unsub, base)
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
            db.x("INSERT INTO news_subscribers (email, user_id, token, created_at, confirmed_at) VALUES (?,?,?,?,?)",
                 (email, u["id"], secrets.token_urlsafe(24), now, now))
        else:
            db.x("UPDATE news_subscribers SET user_id=?, confirmed_at=COALESCE(confirmed_at, ?), unsub_at=NULL WHERE id=?",
                 (u["id"], now, r["id"]))
    elif r is not None:
        db.x("UPDATE news_subscribers SET unsub_at=? WHERE id=?", (now, r["id"]))
    return user_status(u)


def user_status(u: dict) -> dict:
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE email=?", (u["email"].lower(),))
    return {"subscribed": bool(r and r["confirmed_at"] and not r["unsub_at"])}


_rate: Dict[str, list] = {}


def _limited(key: str, n: int, per_s: float) -> bool:
    now = time.time()
    lst = [t for t in _rate.get(key, []) if now - t < per_s]
    lst.append(now)
    _rate[key] = lst
    return len(lst) > n


# ------------------------------------------------------------------ pages and API
STATIC = config.ROOT + "/atcsim/static"


@router.get("/news", include_in_schema=False)
@router.get("/news/issue/{iid}", include_in_schema=False)
def news_page(iid: Optional[int] = None):
    return FileResponse(STATIC + "/news.html")


@router.get("/api/news")
def list_news(section: Optional[str] = None, q: Optional[str] = None, before: Optional[float] = None, limit: int = 40):
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
    return {"items": [_item_out(r) for r in rows], "sections": SECTIONS, "updated": last["t"] if last else None}


@router.get("/api/news/issues")
def list_issues(limit: int = 30):
    _schema()
    rows = db.q("SELECT id, day, title, status, sent_at FROM news_issues ORDER BY day DESC LIMIT ?", (max(1, min(limit, 200)),))
    return [r for r in rows if r["status"] == "sent"] or []


@router.get("/api/news/issues/{iid}")
def get_issue(iid: int, request: Request):
    _schema()
    iss = issue(iid)
    u = current_user(request)
    if iss["status"] != "sent" and not (u and u["role"] == "admin"):
        raise HTTPException(404, "no such issue")
    return iss


class Sub(BaseModel):
    email: str


@router.post("/api/news/subscribe")
def subscribe(body: Sub, request: Request):
    """Public: subscribe an address - a link is sent to confirm it (signed-in users: at once, their own address)."""
    _schema()
    u = current_user(request)
    email = body.email.strip().lower()
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
        db.x("INSERT INTO news_subscribers (email, token, created_at, ip) VALUES (?,?,?,?)", (email, tok, now, ip))
    else:
        tok = r["token"]
        db.x("UPDATE news_subscribers SET unsub_at=NULL, confirmed_at=NULL, ip=? WHERE id=?", (ip, r["id"]))
    base = notify.base_of(request)
    link = f"{base}/news/confirm?t={tok}"
    m = notify.compose(email, "Confirm your subscription · Aviation news",
                       f"Hello,\n\nSomeone (hopefully you) asked to receive the daily aviation newsletter of the ATC "
                       f"simulator at this address.\n\nConfirm: {link}\n\nIf it was not you, ignore this e-mail: nothing "
                       f"will be sent.\n\n-- \nATC SIM",
                       f"""<!doctype html><html><body style="font-family:system-ui,sans-serif;background:#f4f6f8;padding:24px;color:#1d2733">
<div style="max-width:520px;margin:auto;background:#fff;border:1px solid #e1e6eb;border-radius:10px;padding:24px 28px">
<div style="font-weight:800">ATC <span style="color:#c2185b">SIM</span></div>
<p>Someone (hopefully you) asked to receive the daily aviation newsletter of the ATC simulator at this address.</p>
<p style="margin:22px 0"><a href="{html.escape(link)}" style="background:#c2185b;color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:600">Confirm my subscription</a></p>
<p style="color:#7a8794;font-size:12px">If it was not you, ignore this e-mail: nothing will be sent.</p></div></body></html>""")
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
        return _msg_page("This link is not valid", "It may be old, or already used to unsubscribe. Subscribe again from the news page.")
    db.x("UPDATE news_subscribers SET confirmed_at=COALESCE(confirmed_at, ?), unsub_at=NULL WHERE id=?", (time.time(), r["id"]))
    return _msg_page("You are subscribed", "The aviation newsletter will reach you every morning. Every issue has a link to unsubscribe.")


@router.get("/news/unsubscribe", include_in_schema=False)
def unsubscribe_page(t: str = ""):
    """A page with a button (link scanners that open the link do not unsubscribe anyone)."""
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE token=?", (t,)) if t else None
    if r is None:
        return _msg_page("This link is not valid", "")
    if r["unsub_at"]:
        return _msg_page("You are unsubscribed", f"{html.escape(r['email'])} gets no more newsletters.")
    return _msg_page("Unsubscribe from the aviation newsletter?",
                     f"""<form method="post" action="/news/unsubscribe?t={html.escape(t)}"><p>{html.escape(r['email'])} will get no more newsletters.</p>
<button style="background:#c2185b;color:#fff;border:0;padding:10px 18px;border-radius:6px;font-weight:600;cursor:pointer">Unsubscribe</button></form>""", raw=True)


@router.post("/news/unsubscribe", include_in_schema=False)
def unsubscribe(t: str = ""):
    """The form's button, and the mail services' one-click unsubscribe (List-Unsubscribe-Post)."""
    _schema()
    r = db.q1("SELECT * FROM news_subscribers WHERE token=?", (t,)) if t else None
    if r is None:
        raise HTTPException(404, "not valid")
    db.x("UPDATE news_subscribers SET unsub_at=? WHERE id=?", (time.time(), r["id"]))
    return _msg_page("You are unsubscribed", f"{html.escape(r['email'])} gets no more newsletters. Changed your mind? Subscribe again from the news page.")


def _msg_page(title: str, body: str, raw: bool = False) -> HTMLResponse:
    b = body if raw else f"<p>{html.escape(body)}</p>"
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><link rel="stylesheet" href="/static/app.css"></head><body>
<main style="max-width:560px;padding-top:60px"><div class="card"><div style="font-weight:800;letter-spacing:.02em;margin-bottom:12px">ATC <span style="color:var(--magenta)">SIM</span></div>
<h1 style="font-size:22px">{html.escape(title)}</h1>{b}<p><a href="/news">Aviation news</a></p></div></main></body></html>""")


@router.get("/api/me/newsletter")
def my_newsletter(request: Request):
    return user_status(require_user(request))


class OnOff(BaseModel):
    on: bool


@router.put("/api/me/newsletter")
def set_my_newsletter(body: OnOff, request: Request):
    return subscribe_user(require_user(request), body.on)


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
            "job": STATE}


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
