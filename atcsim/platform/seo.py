"""Search engines and link previews for the public pages.

* `/` shows the landing page to visitors who are not signed in (a search engine's home page, not a redirect to a
  sign-in address); signed in, the hub as before.
* The landing and documentation pages get their own title, description, canonical address, Open Graph / Twitter tags
  and structured data (schema.org JSON-LD); the news pages get theirs in news.py.
* `/robots.txt` (the public pages open, the application closed) and `/sitemap.xml` (landing, news, every newsroom
  article, the newsletter issues, the documentation).
* Site-ownership files: Google Search Console's HTML file (`VERIFY_FILES`, more with ATC_SITE_VERIFY="file.html,...").
* `/favicon.ico`, `/favicon.svg`, `/apple-touch-icon.png`.
"""
from __future__ import annotations

import html
import json
import os
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from . import config, db

router = APIRouter()
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
SITE_NAME = "ATC SIM"

# Google Search Console's ownership check: the file it gives, served at the site's root, word for word
VERIFY_FILES = {"googledb6d43d3018dfdc9.html"} | {f.strip() for f in os.environ.get("ATC_SITE_VERIFY", "").split(",")
                                                   if re.match(r"^google[0-9a-f]{8,32}\.html$", f.strip())}

LANDING_TITLE = "ATC SIM · Online radar simulator for GMAC"
LANDING_DESC = ("En-route radar training on the real Agadir CTA, in the browser: controller, pseudo-pilot and instructor "
                "seats, flight plans, OLDI coordination, weather, recordings and evaluations.")

DOC_DESC = {
    "index": "Overview of ATC SIM, the en-route radar simulator on the Agadir CTA: seats, exercises, scenarios, weather, AI lab and API.",
    "controller": "Controller manual: radar labels, menus, MTCD, flight plans, OLDI coordination, RBLs, radio, telephone and shortcuts.",
    "pilot": "Pseudo-pilot manual: the to-do list, the control deck, flying every aircraft and playing the adjacent units on the telephone.",
    "instructor": "Instructor and admin manual: exercises, invitations, live supervision, recordings, debriefs and evaluations.",
    "designer": "Scenario designer: build traffic, weather and traps for an exercise, by hand, by generator or as JSON.",
    "scenario-json": "Scenario JSON reference: every field of a scenario file, its type, default and limits, for people and language models.",
    "ai": "AI lab: language models controlling the simulation, their runs, evaluations and the labelled experience data.",
    "scenarios": "Scenario library and progression: from one sector to the whole Agadir CTA.",
    "weather": "Weather modelling: jet streams, convective cells, squall lines and turbulence, and how pilots deviate around them.",
    "aircraft": "Aircraft performance, pilot behaviour and the Agadir airspace: points, routes and types.",
    "api": "API reference: drive the simulator from code or an agent - sessions, instructions, streams and schemas.",
}


def base_url(request: Request) -> str:
    """The site's address for absolute links: ATC_PUBLIC_URL, else what the request came to (proxy headers included)."""
    if config.PUBLIC_URL:
        return config.PUBLIC_URL
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _e(v) -> str:
    return html.escape(str(v), quote=True)


def head_tags(*, title: str, description: str, url: str, image: Optional[str] = None, kind: str = "website",
              jsonld: Optional[List[dict]] = None, robots: Optional[str] = None, extra: Optional[Dict[str, str]] = None) -> str:
    """The <head> lines of a public page: description, canonical, Open Graph, Twitter card, structured data, icons."""
    t = [f'<meta name="description" content="{_e(description)}">', f'<link rel="canonical" href="{_e(url)}">']
    if robots:
        t.append(f'<meta name="robots" content="{_e(robots)}">')
    og = {"og:site_name": SITE_NAME, "og:type": kind, "og:title": title, "og:description": description, "og:url": url,
          "og:locale": "en_US"}
    if image:
        og.update({"og:image": image, "og:image:secure_url": image if image.startswith("https://") else None,
                   "og:image:type": "image/jpeg", "og:image:width": "1200", "og:image:height": "630", "og:image:alt": title})
    og.update(extra or {})
    og.update({"twitter:card": "summary_large_image" if image else "summary", "twitter:title": title,
               "twitter:description": description, "twitter:image": image})
    for k, v in og.items():
        if v:
            t.append(f'<meta {"name" if k.startswith("twitter:") else "property"}="{k}" content="{_e(v)}">')
    t.append('<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="icon" href="/favicon.ico" sizes="48x48">'
             '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><meta name="theme-color" content="#0d1b2a">')
    for j in jsonld or []:
        s = json.dumps(j, ensure_ascii=False).replace("</", "<\\/")       # never closes the script element
        t.append(f'<script type="application/ld+json">{s}</script>')
    return "\n".join(t)


_CACHE: Dict[str, tuple] = {}


def static_page(name: str) -> str:
    path = os.path.join(STATIC, name)
    mt = os.path.getmtime(path)
    c = _CACHE.get(name)
    if not c or c[0] != mt:
        c = (mt, open(path, encoding="utf-8").read())
        _CACHE[name] = c
    return c[1]


def with_head(page: str, title: str, tags: str) -> str:
    """The page with its title replaced and `tags` in place of its description (or at the end of <head>)."""
    page = re.sub(r"<title>.*?</title>", lambda m: f"<title>{_e(title)}</title>", page, count=1, flags=re.S)
    if re.search(r'<meta name="description"[^>]*>', page):
        return re.sub(r'<meta name="description"[^>]*>', lambda m: tags, page, count=1)
    return page.replace("</head>", tags + "\n</head>", 1)


def organization(base: str) -> dict:
    return {"@type": "Organization", "@id": base + "/#org", "name": SITE_NAME, "url": base + "/",
            "logo": {"@type": "ImageObject", "url": base + "/apple-touch-icon.png", "width": 180, "height": 180}}


# ------------------------------------------------------------------ the landing page
def landing(request: Request) -> HTMLResponse:
    base = base_url(request)
    ld = [{"@context": "https://schema.org", "@graph": [
        organization(base),
        {"@type": "WebSite", "@id": base + "/#site", "url": base + "/", "name": SITE_NAME, "description": LANDING_DESC,
         "publisher": {"@id": base + "/#org"}, "inLanguage": "en"},
        {"@type": "SoftwareApplication", "name": "ATC SIM", "applicationCategory": "EducationalApplication",
         "applicationSubCategory": "Air traffic control simulator", "operatingSystem": "Web browser", "url": base + "/",
         "description": LANDING_DESC, "publisher": {"@id": base + "/#org"}}]}]
    tags = head_tags(title=LANDING_TITLE, description=LANDING_DESC, url=base + "/", image=base + "/share.jpg", jsonld=ld)
    return HTMLResponse(with_head(static_page("login.html"), LANDING_TITLE, tags))


@router.get("/share.jpg", include_in_schema=False)
def landing_card():
    from . import share
    out = os.path.join(config.DATA_DIR, "news_media", "_site", "landing.jpg")
    if not os.path.exists(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        share.card(out, "Online radar simulator for GMAC", "En-route radar training · Agadir CTA", None, brand="ATC SIM")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------ documentation
def help_page(request: Request, slug: str) -> HTMLResponse:
    from .exercises import DOC_PAGES
    titles = {s: t for s, t, _ in DOC_PAGES}
    page = static_page("help.html")
    if slug not in titles:
        return HTMLResponse(with_head(page, "Not found · ATC SIM", '<meta name="robots" content="noindex">'), status_code=404)
    base = base_url(request)
    url = base + ("/help" if slug == "index" else f"/help/{slug}")
    title = f"{titles[slug]} · ATC SIM documentation"
    desc = DOC_DESC.get(slug) or LANDING_DESC
    ld = [{"@context": "https://schema.org", "@type": "TechArticle", "headline": titles[slug], "description": desc, "url": url,
           "inLanguage": "en", "publisher": organization(base), "isPartOf": {"@type": "WebSite", "url": base + "/", "name": SITE_NAME}},
          {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
              {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": base + "/"},
              {"@type": "ListItem", "position": 2, "name": "Documentation", "item": base + "/help"}]
              + ([{"@type": "ListItem", "position": 3, "name": titles[slug], "item": url}] if slug != "index" else [])}]
    return HTMLResponse(with_head(page, title, head_tags(title=title, description=desc, url=url, image=base + "/share.jpg", jsonld=ld)))


# ------------------------------------------------------------------ robots, sitemap, verification, icons
@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base = base_url(request)
    return PlainTextResponse("\n".join([
        "User-agent: *",
        "Allow: /$", "Allow: /login", "Allow: /news", "Allow: /help", "Allow: /static/", "Allow: /share.jpg",
        "Allow: /api/news/", "Allow: /api/help", "Allow: /api/landing",      # what the public pages read (rendering)
        "Disallow: /news/unsubscribe", "Disallow: /news/confirm",
        "Disallow: /api/", "Disallow: /sessions", "Disallow: /x/", "Disallow: /debrief/", "Disallow: /designer",
        "Disallow: /radar", "Disallow: /solo", "Disallow: /ai", "Disallow: /admin", "Disallow: /profile",
        "Disallow: /stats", "Disallow: /auth/", "Disallow: /join/", "Disallow: /ai-data/", "Disallow: /docs",
        "", f"Sitemap: {base}/sitemap.xml", ""]))


def _lastmod(t) -> Optional[str]:
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(float(t), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    from . import news
    from .exercises import DOC_PAGES
    base = base_url(request)
    urls = [(base + "/", None, "weekly", "1.0"), (base + "/news", None, "hourly", "0.9")]
    news._schema()
    for r in db.q("SELECT slug, updated, date FROM news_articles ORDER BY date DESC, slug LIMIT 5000"):
        urls.append((f"{base}/news/a/{r['slug']}", _lastmod(r["updated"]) or r["date"], "monthly", "0.8"))
    for r in db.q("SELECT id, sent_at FROM news_issues WHERE status='sent' ORDER BY day DESC LIMIT 2000"):
        urls.append((f"{base}/news/issue/{r['id']}", _lastmod(r["sent_at"]), "never", "0.4"))
    for s, _, _ in DOC_PAGES:
        urls.append((base + ("/help" if s == "index" else f"/help/{s}"), None, "monthly", "0.6"))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lm, cf, pr in urls:
        out.append(f"<url><loc>{_e(loc)}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "")
                   + f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>")
    out.append("</urlset>")
    return Response("\n".join(out), media_type="application/xml")


def _verification(name: str):
    def serve():
        """A site-ownership file (Google Search Console), word for word."""
        return PlainTextResponse(f"google-site-verification: {name}", media_type="text/html")
    return serve


for _f in sorted(VERIFY_FILES):
    router.add_api_route("/" + _f, _verification(_f), methods=["GET"], include_in_schema=False)


def _icon_png(size: int):
    """The site's icon: a radar scope on the night-chart blue, the magenta target in it."""
    from PIL import Image, ImageDraw
    s = 4 * size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=(13, 27, 42, 255))
    c = s / 2
    for r, w in ((0.36, 0.045), (0.22, 0.04)):
        R = s * r
        d.ellipse((c - R, c - R, c + R, c + R), outline=(111, 168, 220, 255), width=max(1, int(s * w)))
    d.line((c, c, c + s * 0.36 * 0.72, c - s * 0.36 * 0.69), fill=(95, 211, 163, 255), width=max(1, int(s * 0.05)))
    R = s * 0.085
    x, y = c - s * 0.16, c + s * 0.12
    d.ellipse((x - R, y - R, x + R, y + R), fill=(209, 71, 143, 255))
    return im.resize((size, size), Image.LANCZOS)


_ICONS: Dict[str, bytes] = {}


def _icon(kind: str) -> bytes:
    if kind not in _ICONS:
        import io
        b = io.BytesIO()
        if kind == "ico":
            _icon_png(256).save(b, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
        else:
            img = _icon_png(180)
            bg = __import__("PIL.Image", fromlist=["Image"]).new("RGB", img.size, (13, 27, 42))
            bg.paste(img, (0, 0), img)                     # apple icons: no transparency
            bg.save(b, "PNG")
        _ICONS[kind] = b.getvalue()
    return _ICONS[kind]


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0d1b2a"/>
<g fill="none" stroke="#6fa8dc"><circle cx="32" cy="32" r="23" stroke-width="3"/><circle cx="32" cy="32" r="14" stroke-width="2.6"/></g>
<path d="M32 32 L44 20" stroke="#5fd3a3" stroke-width="3.2" stroke-linecap="round"/><circle cx="22" cy="40" r="5.4" fill="#d1478f"/></svg>"""

_ICON_HEADERS = {"Cache-Control": "public, max-age=604800"}


@router.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    return Response(_icon("ico"), media_type="image/x-icon", headers=_ICON_HEADERS)


@router.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    return Response(FAVICON_SVG, media_type="image/svg+xml", headers=_ICON_HEADERS)


@router.get("/apple-touch-icon.png", include_in_schema=False)
def apple_icon():
    return Response(_icon("png"), media_type="image/png", headers=_ICON_HEADERS)
