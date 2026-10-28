"""Aviation news and the newsletter: reading RSS / Atom feeds (local test feeds), duplicates, sections, the public news
page and API, the daily issue by keywords and with a model (a local stand-in for an OpenAI-compatible endpoint: its
picks, an invented story it may return, a broken answer), subscribing (double opt-in, signed-in users), sending with
the unsubscribe link and header, unsubscribing, the daily job, the welcome e-mail.
Run:  python -m tests.test_news   (throw-away data folder, no internet needed)."""
import email
import glob
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time

os.environ["ATC_DATA_DIR"] = tempfile.mkdtemp(prefix="atcsim-news-")
os.environ["ATC_DEV_LOGIN"] = "1"
os.environ["ATC_ADMIN_EMAILS"] = "boss@gmail.com"
os.environ["ATC_NEWS"] = "off"                       # no background job: the test drives it
os.environ.pop("ATC_SMTP_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402

from atcsim.server import app  # noqa: E402
from atcsim.platform import config, db, news, notify  # noqa: E402

config.PUBLIC_URL = "https://atc.example.com"         # the links in the e-mails (cookies stay plain-HTTP for the test client)


def ok(cond, msg):
    print(("  ok  " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


def client(email_):
    c = TestClient(app)
    assert c.post("/auth/dev", json={"email": email_}).status_code == 200
    return c


def mails(to):
    out = []
    for f in sorted(glob.glob(os.path.join(notify.OUTBOX, "*" + to.replace("@", "_at_") + "*"))):
        out.append(email.message_from_bytes(open(f, "rb").read()))
    return out


def body_of(m, kind="text/plain"):
    for p in m.walk():
        if p.get_content_type() == kind:
            return p.get_payload(decode=True).decode()
    return ""


now = time.time()
rfc = lambda t: time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(t))
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
RSS = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Wings</title>
<item><title>Air traffic controllers trial AI tool for sequencing arrivals</title><link>https://wings.example/atc-ai</link>
<guid>wings-1</guid><pubDate>{rfc(now - 3600)}</pubDate><description>&lt;p&gt;An &lt;b&gt;air navigation service&lt;/b&gt; tests
an arrival manager that suggests vectors to approach controllers &amp;amp; cuts holding.&lt;/p&gt;</description>
<media:content url="https://wings.example/a.jpg" medium="image"/></item>
<item><title>Royal Air Maroc opens a new route from Casablanca</title><link>https://wings.example/ram</link><guid>wings-2</guid>
<pubDate>{rfc(now - 7200)}</pubDate><description>The Moroccan airline adds flights to Africa.</description></item>
<item><title>Ten best airport lounges</title><link>https://wings.example/lounges</link><guid>wings-3</guid>
<pubDate>{rfc(now - 5000)}</pubDate><description>Our favourite lounges.</description></item>
<item><title>Old story from last year</title><link>https://wings.example/old</link><guid>wings-old</guid>
<pubDate>{rfc(now - 400 * 86400)}</pubDate><description>Ancient.</description></item>
</channel></rss>"""
GNEWS = f"""<?xml version="1.0"?><rss version="2.0"><channel><title>Google News</title>
<item><title>Runway incursion at a busy airport under investigation - Safety Daily</title><link>https://news.google.com/x/1</link>
<guid>g-1</guid><pubDate>{rfc(now - 1800)}</pubDate><description>&lt;a href="x"&gt;Runway incursion at a busy airport under investigation&lt;/a&gt;</description>
<source url="https://safety.example">Safety Daily</source></item>
<item><title>Air traffic controllers trial AI tool for sequencing arrivals - Other Site</title><link>https://news.google.com/x/2</link>
<guid>g-2</guid><pubDate>{rfc(now - 1700)}</pubDate><source url="https://other.example">Other Site</source></item>
</channel></rss>"""
ATOM = f"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Tech Air</title>
<entry><title>Remote tower goes live at a regional airport</title><link rel="alternate" href="https://tech.example/remote-tower"/>
<id>tag:tech,1</id><updated>{iso(now - 2400)}</updated><summary type="html">A digital tower with cameras replaces the old one.</summary></entry>
</feed>"""
FEEDS = {"/rss.xml": RSS, "/gnews.xml": GNEWS, "/atom.xml": ATOM}
MODEL = {"mode": "good"}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in FEEDS:
            b = FEEDS[self.path].encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
        elif self.path == "/v1/models":
            b = b'{"data":[{"id":"mock-editor"}]}'
            self.send_response(200)
        else:
            b = b"not here"
            self.send_response(404)
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n))
        user = req["messages"][-1]["content"]
        ids = [int(x) for x in re.findall(r"^(\d+) \|", user, re.M)]
        MODEL["last_prompt"] = user
        if MODEL["mode"] == "garbage":
            content = "Sorry, I cannot do that."
        else:
            atc = [i for i in ids if "Air traffic" in user.split(f"\n{i} |")[1].split("\n")[0]] if ids else []
            pick = atc[:1] + [i for i in ids if i not in atc][:2]
            content = json.dumps({"title": "Controllers try an AI arrival manager", "intro": "An arrival manager on trial; a new route.",
                                  "sections": [{"name": "Air traffic control & airspace", "items": [{"id": pick[0], "summary": "An ANSP tests an arrival manager."}]},
                                               {"name": "Morocco & Africa", "items": [{"id": i, "summary": "Summary by the model."} for i in pick[1:]] + [{"id": 99999, "summary": "invented"}]}]})
        b = json.dumps({"choices": [{"message": {"content": content}}], "usage": {}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

boss, ins, atc = client("boss@gmail.com"), client("ins@gmail.com"), client("atc@gmail.com")
for c, (fn, ln) in zip((boss, ins, atc), (("Big", "Boss"), ("Ina", "Structor"), ("Alaa", "Hamza"))):
    c.put("/api/me", json={"first_name": fn, "last_name": ln, "licence": "X-1"})
anon = TestClient(app)

# ---------------------------------------------------------------- welcome
notify.flush()
w = mails("atc@gmail.com")
ok(w and "Welcome to the ATC simulator" in w[0]["Subject"], "first sign-in: a welcome e-mail")
client("atc@gmail.com")
notify.flush()
ok(len(mails("atc@gmail.com")) == 1, "only the first time")

# ---------------------------------------------------------------- sources and reading
st = boss.get("/api/news/admin").json()
ok(len(st["sources"]) >= 10 and any("news.google.com" in s["url"] for s in st["sources"]), "default sources: news sites and Google News searches")
for s in st["sources"]:
    boss.delete(f"/api/news/admin/sources/{s['id']}")
ok(ins.get("/api/news/admin").status_code == 403 and anon.get("/api/news/admin").status_code == 401, "the newsletter administration is the admin's")
r = boss.post("/api/news/admin/sources", json={"name": "Wings", "url": BASE + "/rss.xml"})
ok(r.status_code == 200 and r.json()["last_status"].startswith("ok: 4 stories, 3 new"), f"a feed added is read at once: {r.json()['last_status']}")
boss.post("/api/news/admin/sources", json={"name": "Google News · ATC", "url": BASE + "/gnews.xml"})
boss.post("/api/news/admin/sources", json={"name": "Tech Air", "url": BASE + "/atom.xml", "section": "Technology & innovation"})
r = boss.post("/api/news/admin/sources", json={"name": "Broken", "url": BASE + "/nothing.xml"})
ok(r.json()["last_status"].startswith("error"), "a feed that does not answer: its error, the others go on")
ok(boss.post("/api/news/admin/fetch").json()["new"] == 0, "read again: nothing new")

d = anon.get("/api/news").json()
titles = [i["title"] for i in d["items"]]
ok(anon.get("/api/news").status_code == 200 and anon.get("/news").status_code == 200, "the news page and its API are public")
ok("Old story from last year" not in titles, "stories older than the keeping time are left out")
ok(titles.count("Air traffic controllers trial AI tool for sequencing arrivals") == 1, "the same story from two sources: kept once")
gi = next(i for i in d["items"] if i["title"].startswith("Runway incursion"))
ok(gi["title"] == "Runway incursion at a busy airport under investigation" and gi["source"] == "Safety Daily" and gi["excerpt"] == "",
   "Google News: the site's name taken off the headline, the source kept, the excerpt that repeats the headline dropped")
ai = next(i for i in d["items"] if i["title"].startswith("Air traffic controllers"))
ok(ai["excerpt"] == "An air navigation service tests an arrival manager that suggests vectors to approach controllers & cuts holding."
   and ai["image"] == "https://wings.example/a.jpg", "excerpt: tags out, entities decoded; the image kept")
ok(ai["section"] == "Air traffic control & airspace" and gi["section"] == "Safety"
   and next(i for i in d["items"] if "Royal Air Maroc" in i["title"])["section"] == "Morocco & Africa", "sections by keywords")
ok(next(i for i in d["items"] if "Remote tower" in i["title"])["section"] in ("Technology & innovation", "Air traffic control & airspace"),
   "Atom feeds too (a remote tower: air traffic control before the source's own section)")
ok(anon.get("/api/news?section=Safety").json()["items"][0]["title"].startswith("Runway"), "filter by section")
ok(len(anon.get("/api/news?q=lounges").json()["items"]) == 1, "search")

# ---------------------------------------------------------------- the issue: keywords
i1 = boss.post("/api/news/admin/build", json={"replace": True}).json()
ok(i1["sections"][0]["name"] == "Air traffic control & airspace" and i1["model"] is None, "built by keywords: air traffic control first")
ok(anon.get(f"/api/news/issues/{i1['id']}").status_code == 404 and boss.get(f"/api/news/issues/{i1['id']}").status_code == 200,
   "a draft is not public (the admin previews it)")

# ---------------------------------------------------------------- the issue: with a model
r = boss.post("/api/ai/models", json={"name": "Mock editor", "kind": "openai", "base_url": BASE + "/v1", "model": "mock-editor", "api_key": "sk-test"})
mid = r.json()["id"]
boss.put("/api/news/admin/settings", json={"model_id": mid})
i2 = boss.post("/api/news/admin/build", json={"replace": True}).json()
ok(i2["model"] == "Mock editor" and i2["title"] == "Controllers try an AI arrival manager" and i2["intro"], "built by the model: its title and introduction")
allids = [it["id"] for s in i2["sections"] for it in s["items"]]
ok(99999 not in allids and len(allids) == 3, "a story the model invented is left out")
ok(i2["sections"][0]["items"][0]["summary"] == "An ANSP tests an arrival manager.", "its summaries")
ok("Old story" not in MODEL["last_prompt"] and "| Wings |" in MODEL["last_prompt"], "the model is given the last day's stories (id, source, headline, excerpt)")
MODEL["mode"] = "garbage"
i3 = boss.post("/api/news/admin/build", json={"replace": True}).json()
ok(i3["sections"] and str(i3["model"]).startswith("model failed"), f"a broken answer: picked by keywords instead ({i3['model'][:50]})")
MODEL["mode"] = "good"
i2 = boss.post("/api/news/admin/build", json={"replace": True}).json()

# ---------------------------------------------------------------- subscribing
r = anon.post("/api/news/subscribe", json={"email": "reader@example.org"})
ok(r.json()["status"] == "confirm", "subscribe from the news page: a confirmation link is sent")
cm = mails("reader@example.org")
link = re.search(r"https://atc\.example\.com/news/confirm\?t=[\w-]+", body_of(cm[0])).group(0)
ok(cm and "Confirm your subscription" in cm[0]["Subject"], "the confirmation e-mail")
ok(db.q1("SELECT confirmed_at FROM news_subscribers WHERE email='reader@example.org'")["confirmed_at"] is None, "not subscribed before confirming")
ok("You are subscribed" in anon.get(link.replace("https://atc.example.com", "")).text, "the link confirms")
ok(anon.post("/api/news/subscribe", json={"email": "not an address"}).status_code == 400, "a bad address is refused")
codes = [anon.post("/api/news/subscribe", json={"email": f"spam{k}@example.org"}).status_code for k in range(6)]
ok(429 in codes, "too many from one address: refused for a while")
ok(atc.get("/api/me/newsletter").json()["subscribed"] is False, "signed-in users: not subscribed by default")
ok(atc.put("/api/me/newsletter", json={"on": True}).json()["subscribed"], "they subscribe in one click (their address is verified)")

# ---------------------------------------------------------------- sending
r = boss.post(f"/api/news/admin/issues/{i2['id']}/send", json={"test": True}).json()
ok(r["sent"] == 1 and len(mails("boss@gmail.com")) >= 1, "a test to the administrator")
ok(boss.get("/api/news/admin").json()["issues"][0]["status"] == "draft", "the test leaves it a draft")
r = boss.post(f"/api/news/admin/issues/{i2['id']}/send", json={"test": False}).json()
ok(r["sent"] == 2, f"sent to the subscribers ({r})")
m = mails("reader@example.org")[-1]
txt, page = body_of(m), body_of(m, "text/html")
unsub = re.search(r"https://atc\.example\.com/news/unsubscribe\?t=[\w-]+", txt).group(0)
ok("Controllers try an AI arrival manager" in m["Subject"] and "https://wings.example/atc-ai" in page and "Read it on the site" in page,
   "the newsletter: headlines linking to their sites, a link to read it on the site")
ok(m["List-Unsubscribe"] == f"<{unsub}>" and m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click", "one-click unsubscribe header")
ok(anon.get(f"/api/news/issues/{i2['id']}").status_code == 200 and anon.get("/api/news/issues").json()[0]["id"] == i2["id"],
   "once sent the issue is public, in the issues list")
path = unsub.replace("https://atc.example.com", "")
ok("Unsubscribe from" in anon.get(path).text and
   db.q1("SELECT unsub_at FROM news_subscribers WHERE email='reader@example.org'")["unsub_at"] is None,
   "opening the link only asks (link scanners unsubscribe nobody)")
ok("You are unsubscribed" in anon.post(path).text, "the button (or the one-click header) unsubscribes")

# ---------------------------------------------------------------- the daily job
from datetime import datetime, timedelta  # noqa: E402
tz = news._tz(news.settings())
tomorrow = (datetime.now(tz) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
FEEDS["/rss.xml"] = RSS.replace("</channel>", f"""<item><title>New ATC sector opens over the Atlantic</title><link>https://wings.example/sector</link>
<guid>wings-9</guid><pubDate>{rfc(tomorrow.timestamp() - 1800)}</pubDate><description>Airspace redesign.</description></item></channel>""")
n_before = len(mails("atc@gmail.com"))
did = news.tick(tomorrow.timestamp())
ok(did and "sent to 1" in did, f"the daily job at its hour: read, built, sent ({did})")
ok(len(mails("atc@gmail.com")) == n_before + 1 and len(mails("reader@example.org")) == 2, "to the subscribers left (not to the one who left)")
ok("sent to" not in str(news.tick(tomorrow.timestamp() + 120)), "once a day")
print("news checks passed")
