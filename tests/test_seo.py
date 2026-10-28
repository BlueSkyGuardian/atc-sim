"""Search engines and link previews: Google's ownership file, robots.txt, the sitemap, the landing page at the site's
address for visitors (the hub for signed-in users), titles / descriptions / canonical addresses / Open Graph and
structured data on the public pages, the icons. Run:  python -m tests.test_seo   (throw-away data folder)."""
import json
import os
import re
import sys
import tempfile

os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-seo-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_NEWS"] = "off"
os.environ["ATC_ARTICLES_URL"] = tempfile.mkdtemp(prefix="atcsim-seo-articles-")     # an empty newsroom

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app  # noqa: E402
from atcsim.platform import config, db, news  # noqa: E402

config.PUBLIC_URL = "https://atc.example.com"
anon = TestClient(app)


def ok(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


def meta(page, k):
    m = re.search(rf'<meta (?:property|name)="{re.escape(k)}" content="([^"]*)"', page)
    return m and m.group(1)


def canon(page):
    m = re.search(r'<link rel="canonical" href="([^"]*)"', page)
    return m and m.group(1)


def jsonld(page):
    return [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]


# ---------------------------------------------------------------- Google Search Console
r = anon.get("/googledb6d43d3018dfdc9.html")
ok(r.status_code == 200 and r.text == "google-site-verification: googledb6d43d3018dfdc9.html", "the ownership file, word for word, signed out")
ok(anon.get("/google0000000000000000.html").status_code in (401, 404), "no other file of that kind")

# ---------------------------------------------------------------- robots.txt, sitemap
rb = anon.get("/robots.txt")
ok(rb.status_code == 200 and "Sitemap: https://atc.example.com/sitemap.xml" in rb.text and "Disallow: /radar" in rb.text
   and "Allow: /news" in rb.text and "Allow: /api/news/" in rb.text, "robots.txt: the public pages open, the application closed, the sitemap")
news._schema()
db.x("INSERT INTO news_articles (slug, date, region, section, title, summary, body, sources, figures, author, updated, synced_at) "
     "VALUES ('2026-10-28-test-story', '2026-10-28', 'morocco', NULL, 'A test', 'Summary.', 'Text.', '[]', '[]', 'ATC SIM newsroom', 1793200000, 0)")
sm = anon.get("/sitemap.xml")
ok(sm.status_code == 200 and sm.headers["content-type"].startswith("application/xml") and "<loc>https://atc.example.com/</loc>" in sm.text
   and "<loc>https://atc.example.com/news/a/2026-10-28-test-story</loc><lastmod>" in sm.text
   and "<loc>https://atc.example.com/help/controller</loc>" in sm.text and "/radar" not in sm.text,
   "the sitemap: landing, news, every article (with its date), the documentation")

hd = anon.head("/sitemap.xml")
ok(hd.status_code == 200 and hd.headers["content-type"].startswith("application/xml") and hd.content == b""
   and anon.head("/robots.txt").status_code == 200 and anon.head("/").status_code == 200,
   "HEAD (crawlers ask it first): answered as GET, without the body - not 405")

# ---------------------------------------------------------------- the landing page
r = anon.get("/", follow_redirects=False)
ok(r.status_code == 200 and "Online radar simulator for GMAC" in r.text and canon(r.text) == "https://atc.example.com/",
   f"signed out, the site's address is the landing page itself, not a redirect ({r.status_code})")
ok(meta(r.text, "og:image") == "https://atc.example.com/share.jpg" and meta(r.text, "og:title").startswith("ATC SIM")
   and meta(r.text, "description"), "with its description and link preview")
ld = jsonld(r.text)
ok(ld and {x["@type"] for x in ld[0]["@graph"]} == {"Organization", "WebSite", "SoftwareApplication"}, "structured data: organisation, site, application")
ok(canon(anon.get("/login?next=/radar").text) == "https://atc.example.com/", "the sign-in address points search engines to the landing page")
ok(anon.get("/share.jpg").headers["content-type"] == "image/jpeg", "the landing page's picture")
u = TestClient(app)
u.post("/auth/dev", json={"email": "atc@gmail.com"})
ok('id="google"' not in u.get("/").text and u.get("/").status_code == 200, "signed in: the hub, as before")
ok(anon.get("/radar", follow_redirects=False).status_code == 303, "the application's pages still ask to sign in")
ok(anon.get("/api/me").status_code == 401, "and its API too")

# ---------------------------------------------------------------- documentation
h = anon.get("/help/controller").text
ok("<title>Controller manual · ATC SIM documentation</title>" in h and "MTCD" in (meta(h, "description") or "")
   and canon(h) == "https://atc.example.com/help/controller", "a documentation page: its own title, description and address")
ok(canon(anon.get("/help").text) == "https://atc.example.com/help", "the documentation's first page")
ok(any(x.get("@type") == "TechArticle" for x in jsonld(h)), "structured data: a technical article")
nf = anon.get("/help/no-such-page")
ok(nf.status_code == 404 and meta(nf.text, "robots") == "noindex", "an unknown page: not found, not indexed")

# ---------------------------------------------------------------- news, icons
n = anon.get("/news").text
ok(canon(n) == "https://atc.example.com/news" and any(x["@type"] == "CollectionPage" for x in jsonld(n)), "the news page: its address and structured data")
ok(meta(anon.get("/news/unsubscribe").text, "robots") == "noindex", "the unsubscribe page is not indexed")
ok(anon.get("/favicon.ico").headers["content-type"] == "image/x-icon" and anon.get("/favicon.svg").text.startswith("<svg")
   and anon.get("/apple-touch-icon.png").content[:4] == b"\x89PNG", "the icons")
ok('rel="icon"' in r.text and 'rel="icon"' in h and 'rel="icon"' in n, "the public pages name their icon")
print("seo checks passed")
