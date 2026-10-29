"""Aviation news and the newsletter: reading RSS / Atom feeds (local test feeds), duplicates, sections, the public news
page and API, the daily issue by keywords and with a model (a local stand-in for an OpenAI-compatible endpoint: its
picks, an invented story it may return, a broken answer), subscribing (double opt-in, signed-in users), sending with
the unsubscribe link and header, unsubscribing, the daily job, the welcome e-mail; the three languages (French by
default, English, Arabic right to left): the newsroom's translations, the page's language, the profile's language and
each subscriber's newsletter in theirs.
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
                                               {"name": "Morocco & Africa", "items": [{"id": i, "summary": "Summary by the model."} for i in pick[1:]] + [{"id": 99999, "summary": "invented"}]}],
                                  "i18n": {"fr": {"title": "Des contrôleurs testent un gestionnaire d'arrivées", "intro": "Un AMAN à l'essai.",
                                                  "summaries": {str(pick[0]): "Un prestataire teste un gestionnaire d'arrivées."}},
                                           "ar": {"title": "مراقبون جويون يجربون مدير الوصول", "intro": "تجربة مدير الوصول.",
                                                  "summaries": {str(pick[0]): "مزود خدمات يختبر مدير الوصول."}}}}, ensure_ascii=False)
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
fr2 = boss.get(f"/api/news/issues/{i2['id']}?lang=fr").json()
ar2 = boss.get(f"/api/news/issues/{i2['id']}?lang=ar").json()
ok(fr2["title"].startswith("Des contrôleurs") and fr2["sections"][0]["label"] == "Contrôle aérien et espace aérien"
   and fr2["sections"][0]["items"][0]["summary"].startswith("Un prestataire") and ar2["title"].startswith("مراقبون")
   and fr2["sections"][1]["items"][0]["summary"] == "Summary by the model.",
   "the issue in French and Arabic: the model's translations (a summary it did not translate stays in English)")
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
ok(cm and "Confirmez votre abonnement" in cm[0]["Subject"], "the confirmation e-mail (in French, the page's language by default)")
ok(db.q1("SELECT confirmed_at FROM news_subscribers WHERE email='reader@example.org'")["confirmed_at"] is None, "not subscribed before confirming")
ok("Vous êtes abonné" in anon.get(link.replace("https://atc.example.com", "")).text, "the link confirms")
r = anon.post("/api/news/subscribe", json={"email": "qari@example.org", "lang": "ar"})
cm = mails("qari@example.org")
ok(cm and "أكّد اشتراكك" in str(email.header.make_header(email.header.decode_header(cm[0]["Subject"]))) and 'dir="rtl"' in body_of(cm[0], "text/html"),
   "subscribed from the Arabic page: the confirmation in Arabic, right to left")
anon.get(re.search(r"https://atc\.example\.com(/news/confirm\?t=[\w-]+)", body_of(cm[0])).group(1))
ok(anon.post("/api/news/subscribe", json={"email": "not an address"}).status_code == 400, "a bad address is refused")
codes = [anon.post("/api/news/subscribe", json={"email": f"spam{k}@example.org"}).status_code for k in range(6)]
ok(429 in codes, "too many from one address: refused for a while")
ok(atc.get("/api/me/newsletter").json()["subscribed"] is True, "accounts: subscribed by default")
ok(atc.put("/api/me/newsletter", json={"on": False}).status_code == 409, "unsubscribing must be confirmed")
ok(atc.put("/api/me/newsletter", json={"on": False, "confirm": True}).json()["subscribed"] is False, "confirmed: unsubscribed")
client("atc@gmail.com")
ok(atc.get("/api/me/newsletter").json()["subscribed"] is False, "signing in again does not subscribe them back")
ok(atc.put("/api/me/newsletter", json={"on": True}).json()["subscribed"], "subscribing again: one click")
ok(atc.get("/api/me").json()["news_lang"] == "fr" and atc.get("/api/me/newsletter").json()["lang"] == "fr", "an account reads the news in French until it chooses")
u = atc.put("/api/me", json={"first_name": "Alaa", "last_name": "Hamza", "licence": "X-1", "news_lang": "en"}).json()
ok(u["news_lang"] == "en" and atc.get("/api/me/newsletter").json()["lang"] == "en", "the profile's news language (saved with the profile)")
ok(atc.put("/api/me/newsletter", json={"lang": "xx"}).status_code == 400, "a language we do not write in is refused")
r = boss.post("/api/users", json={"email": "later@gmail.com", "role": "atc"})
ok(db.q1("SELECT confirmed_at FROM news_subscribers WHERE email='later@gmail.com'")["confirmed_at"], "an account made in advance: subscribed too")

# ---------------------------------------------------------------- sending
r = boss.post(f"/api/news/admin/issues/{i2['id']}/send", json={"test": True}).json()
ok(r["sent"] == 1 and len(mails("boss@gmail.com")) >= 1, "a test to the administrator")
ok(boss.get("/api/news/admin").json()["issues"][0]["status"] == "draft", "the test leaves it a draft")
r = boss.post(f"/api/news/admin/issues/{i2['id']}/send", json={"test": False}).json()
ok(r["sent"] == 6, f"sent to the subscribers: the accounts and the confirmed readers ({r})")
subj_ = lambda m_: str(email.header.make_header(email.header.decode_header(m_["Subject"])))
m = mails("atc@gmail.com")[-1]
ok("Controllers try an AI arrival manager" in subj_(m) and "https://wings.example/atc-ai" in body_of(m, "text/html")
   and "Read it on the site" in body_of(m, "text/html"), "the newsletter in English for the account that chose it: headlines linking to their sites")
m = mails("reader@example.org")[-1]
txt, page = body_of(m), body_of(m, "text/html")
unsub = re.search(r"https://atc\.example\.com/news/unsubscribe\?t=[\w-]+", txt).group(0)
ok("Des contrôleurs testent" in subj_(m) and "https://wings.example/atc-ai" in page and "Le lire sur le site" in page
   and "Contrôle aérien et espace aérien" in page and 'lang="fr"' in page, "in French for the reader who subscribed from the French page")
ma = mails("qari@example.org")[-1]
pa = body_of(ma, "text/html")
ok("مراقبون جويون" in subj_(ma) and 'dir="rtl"' in pa and "المراقبة الجوية والمجال الجوي" in pa and "?lang=ar" in pa,
   "in Arabic, right to left, its links to the Arabic pages")
ok(m["List-Unsubscribe"] == f"<{unsub}>" and m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click", "one-click unsubscribe header")
ok(anon.get(f"/api/news/issues/{i2['id']}").status_code == 200 and anon.get("/api/news/issues").json()[0]["id"] == i2["id"],
   "once sent the issue is public, in the issues list")
path = unsub.replace("https://atc.example.com", "")
ok("Se désabonner de la lettre" in anon.get(path).text and
   db.q1("SELECT unsub_at FROM news_subscribers WHERE email='reader@example.org'")["unsub_at"] is None,
   "opening the link only asks (link scanners unsubscribe nobody)")
ok("Vous êtes désabonné" in anon.post(path).text, "the button (or the one-click header) unsubscribes (in their language)")

# ---------------------------------------------------------------- the daily job
from datetime import datetime, timedelta  # noqa: E402
tz = news._tz(news.settings())
tomorrow = (datetime.now(tz) + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
if tomorrow.timestamp() - time.time() < 13 * 3600:       # run late in the evening: the day after (nobody gets two in 12 h)
    tomorrow += timedelta(days=1)
FEEDS["/rss.xml"] = RSS.replace("</channel>", f"""<item><title>New ATC sector opens over the Atlantic</title><link>https://wings.example/sector</link>
<guid>wings-9</guid><pubDate>{rfc(tomorrow.timestamp() - 1800)}</pubDate><description>Airspace redesign.</description></item></channel>""")
n_before = len(mails("atc@gmail.com"))
did = news.tick(tomorrow.timestamp())
ok(did and "sent to 5" in did, f"the daily job at its hour: read, built, sent ({did})")
ok(len(mails("atc@gmail.com")) == n_before + 1 and len(mails("reader@example.org")) == 2, "to the subscribers left (not to the one who left)")
ok("sent to" not in str(news.tick(tomorrow.timestamp() + 120)), "once a day")

# ---------------------------------------------------------------- the newsroom's own articles (a local `news` branch)
import shutil, subprocess  # noqa: E401,E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "newsdesk"))
import publish  # noqa: E402

root = tempfile.mkdtemp(prefix="newsbranch-")
day = datetime.now(tz).strftime("%Y-%m-%d")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def article(slug, region, body, svg='<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>',
            langs=("fr", "ar")):
    d = os.path.join(root, "articles", slug)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "fig.svg"), "w").write(svg)
    open(os.path.join(d, "fig.png"), "wb").write(PNG)
    tr = {}
    figs_ = re.findall(r"\{\{figure:[^}]+\}\}", body)
    if "fr" in langs:
        tr["fr"] = {"title": f"Titre de {slug}", "summary": "Un résumé.", "figures": [{"caption": "Une figure.", "alt": "Une figure"}]}
        open(os.path.join(d, "body.fr.md"), "w").write(" ".join(["le mot"] * 200) + "\n\n" + "\n\n".join(figs_) + "\n\nVoir [ici](https://one.example/x).")
    if "ar" in langs:
        tr["ar"] = {"title": f"عنوان {slug[-12:]}", "summary": "ملخص قصير.", "figures": [{"caption": "رسم.", "alt": "رسم"}]}
        open(os.path.join(d, "body.ar.md"), "w").write(" ".join(["كلمة عربية"] * 200) + "\n\n" + "\n\n".join(figs_))
    json.dump({"slug": slug, "date": slug[:10], "region": region, "section": "Air traffic control & airspace",
               "title": f"Title of {slug}", "summary": "A summary.", "author": "ATC SIM newsroom",
               "sources": [{"title": "One", "url": "https://one.example/x", "site": "One"}, {"title": "Two", "url": "https://two.example/y", "site": "Two"}],
               "figures": [{"file": "fig.svg", "png": "fig.png", "caption": "A figure.", "alt": "A figure", "credit": "ATC SIM"}],
               **({"translations": tr} if tr else {})},
              open(os.path.join(d, "meta.json"), "w"), ensure_ascii=False)
    open(os.path.join(d, "body.md"), "w").write(body)
    return d


words = " ".join(["word"] * 400)
article(f"{day}-europe-story", "europe", f"{words}\n\n## More\n\n{{{{figure:fig.svg}}}}\n\nSee [it](https://one.example/x).")
article(f"{day}-morocco-story", "morocco", f"{words}\n\nMore.")
bad = article(f"{day}-bad-story", "morocco", f"{words} <b>html</b> [x](http://plain.example) ![img](https://i.example/a.png)",
              svg='<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
errs = publish.check(bad)
ok(any("no HTML" in x for x in errs) and any("https" in x for x in errs) and any("unsafe SVG" in x for x in errs)
   and any("no Markdown images" in x for x in errs), f"the publisher refuses HTML, plain-http links, images, unsafe SVG ({len(errs)} problems)")
ok(publish.main(["publish", root]) == 1, "and does not build the index while an article is wrong")
shutil.rmtree(bad)
mono = article("2026-11-02-english-only", "europe", words, langs=())
ok(any("French and Arabic" in x for x in publish.check(mono)), "an article of today on without its French and Arabic versions is refused")
half = article("2026-11-02-half", "europe", f"{words}\n\n{{{{figure:fig.svg}}}}", langs=("fr", "ar"))
open(os.path.join(half, "body.ar.md"), "w").write(" ".join(["mot"] * 400) + "\n\n{{figure:fig.svg}}")
ok(any("not written in Arabic" in x for x in publish.check(half)), "an 'Arabic' version that is not in Arabic is refused")
shutil.rmtree(mono)
shutil.rmtree(half)
ok(publish.main(["publish", root]) == 0, "all good: the index is built")
idx = json.load(open(os.path.join(root, "articles", "index.json")))["articles"]
ok([a["region"] for a in idx] == ["morocco", "europe"] and idx[0]["langs"] == ["en", "fr", "ar"] and "body.ar.md" in idx[0]["files"],
   "the index: Morocco first, then Europe; the languages and their files")
# an unsafe figure slipped past the publisher (written after the index): the server refuses that article
evil = article(f"{day}-evil-story", "international", words, svg='<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>')
open(os.path.join(root, "articles", f"{day}-europe-story", "body.ar.md"), "w").write("")       # lost: Arabic falls back to English
idx.append({"slug": f"{day}-evil-story", "date": day, "region": "international", "updated": int(time.time())})
json.dump({"articles": idx}, open(os.path.join(root, "articles", "index.json"), "w"))
open(os.path.join(root, "articles", f"{day}-morocco-story", "body.md"), "a").write(" <script>alert(1)</script> [js](javascript:alert(1))")
news.ARTICLES_URL = os.path.join(root, "articles")
db.x("DELETE FROM news_articles")                        # only the local test articles (not what the startup read from GitHub)
r = news.sync_articles()
ok(r["new"] == 2 and any("evil" in x for x in r["refused"]), f"synced: 2 articles, the unsafe one refused ({r})")
d = anon.get("/api/news/articles").json()["articles"]
ok([a["region"] for a in d] == ["morocco", "europe"] and d[0]["figures"][0]["url"].endswith("/fig.svg"), "public list: Morocco first, with figures")
full = anon.get(f"/api/news/articles/{day}-morocco-story").json()
ok("<script" not in full["body"] and "javascript:" not in full["body"] and len(full["sources"]) == 2, "HTML and script links stripped by the server too")
mr = anon.get(f"/news/media/{day}-morocco-story/fig.svg")
ok(mr.status_code == 200 and mr.headers["content-type"].startswith("image/svg+xml") and "default-src 'none'" in mr.headers["content-security-policy"],
   "figures served as images, under a content security policy that runs nothing")
ok(anon.get(f"/news/media/{day}-morocco-story/..%2Fmeta.json").status_code == 404 and anon.get("/news/media/x/y.svg").status_code == 404, "nothing else served")
ok(anon.get(f"/news/a/{day}-morocco-story").status_code == 200, "the article page is public")
# ---------------------------------------------------------------- the three languages
fr = anon.get("/api/news/articles").json()
ok(fr["lang"] == "fr" and fr["articles"][0]["title"].startswith("Titre de") and fr["articles"][0]["region_name"] == "Maroc"
   and fr["articles"][0]["lang"] == "fr", "the articles in French by default")
ar = anon.get("/api/news/articles?lang=ar").json()["articles"]
eu = next(a for a in ar if "europe" in a["slug"])
ok(ar[0]["title"].startswith("عنوان") and ar[0]["region_name"] == "المغرب" and eu["lang"] == "en" and eu["langs"] == ["en", "fr"],
   "in Arabic; an article whose Arabic version is missing comes in English (and says so)")
fa = anon.get(f"/api/news/articles/{day}-morocco-story?lang=fr").json()
ok(fa["body"].startswith("le mot") and fa["figures"][0]["caption"] == "Une figure." and fa["section_name"] == "Contrôle aérien et espace aérien",
   "the French article: its text, its figures' captions, its section's name")
pg_ar = anon.get(f"/news/a/{day}-morocco-story?lang=ar").text
ok('<html lang="ar" dir="rtl"' in pg_ar and "عنوان" in pg_ar and 'hreflang="fr"' in pg_ar and 'hreflang="x-default"' in pg_ar
   and 'content="ar_MA"' in pg_ar, "the Arabic page: right to left, its versions linked (hreflang), its locale")
pg_eu = anon.get(f"/news/a/{day}-europe-story?lang=ar").text
ok("متوفر باللغة الإنجليزية فقط" in pg_eu and 'lang="en" dir="ltr"' in pg_eu and 'hreflang="ar"' not in pg_eu,
   "an article not in Arabic: the English one, said so, no Arabic version announced")
ok('<html lang="fr"' in anon.get("/news").text and "Actualité aéronautique" in anon.get("/news").text, "the news page: French by default")
ok('<html lang="en"' in atc.get("/news").text, "a signed-in reader: in their profile's language")
atc.cookies.set("news_lang", "ar")
ok('<html lang="ar"' in atc.get("/news").text and '<html lang="fr"' in atc.get("/news?lang=fr").text,
   "the page's own choice (cookie) next, the address's language first")
atc.cookies.delete("news_lang")
ok(news.sync_articles()["new"] == 0, "synced again: nothing new")

# ---------------------------------------------------------------- link previews (WhatsApp, LinkedIn, X...)
import io  # noqa: E402
import re as _re  # noqa: E402
from PIL import Image  # noqa: E402

slug = f"{day}-morocco-story"
db.x("UPDATE news_articles SET title=?, summary=? WHERE slug=?", ('ONDA & "Agadir" <radar>', "Short & clear.", slug))
pg = anon.get(f"/news/a/{slug}?lang=en", headers={"user-agent": "WhatsApp/2.23.20.0"}).text


def meta(page, k):
    m = _re.search(rf'<meta (?:property|name)="{_re.escape(k)}" content="([^"]*)"', page)
    return m and m.group(1)


ok(meta(pg, "og:title") == "ONDA &amp; &quot;Agadir&quot; &lt;radar&gt;" and "<title>ONDA &amp; &quot;Agadir&quot; &lt;radar&gt; · ATC SIM</title>" in pg,
   "an article's page: its own title in the page and the preview, escaped")
ok(meta(pg, "og:description") == "Short &amp; clear." and meta(pg, "og:type") == "article" and meta(pg, "twitter:card") == "summary_large_image"
   and meta(pg, "og:url") == f"https://atc.example.com/news/a/{slug}?lang=en", "its summary, type article, the large card, its address")
img = (meta(pg, "og:image") or "").replace("&amp;", "&")
ok(img.startswith(f"https://atc.example.com/news/media/{slug}/share.jpg?v="), f"its own picture, an absolute address ({img})")
ri = anon.get(img.replace("https://atc.example.com", ""))
pic = Image.open(io.BytesIO(ri.content))
ok(ri.status_code == 200 and ri.headers["content-type"] == "image/jpeg" and pic.size == (1200, 630) and len(ri.content) < 300_000,
   f"the picture: a 1200x630 JPEG under 300 kB ({len(ri.content)} bytes)")
ok(pg.count('name="description"') == 1 and "news.html" not in pg and 'id="' in pg, "the page is still the news page, one description")
gen = anon.get("/news?lang=en").text
ok(meta(gen, "og:image") == "https://atc.example.com/news/share.jpg" and meta(gen, "og:title") == "Aviation news · ATC SIM",
   "the news page: the site's own preview")
pf = anon.get(f"/news/a/{slug}").text
ok((meta(pf, "og:image") or "").startswith(f"https://atc.example.com/news/media/{slug}/share.fr.jpg") and meta(pf, "og:title").startswith("Titre de"),
   "the French page: its French title and preview picture")
ok(anon.get(f"/news/media/{slug}/share.fr.jpg").status_code == 200, "the French picture is served")
rs = anon.get("/news/share.jpg")
ok(rs.status_code == 200 and Image.open(io.BytesIO(rs.content)).size == (1200, 630), "the site's picture")
nf = anon.get("/news/a/2020-01-01-no-such")
ok(nf.status_code == 404 and meta(nf.text, "robots") == "noindex", "an unknown article: not found, not indexed")
ok('<span class="reg">Morocco' in pg and "<h1>ONDA &amp; &quot;Agadir&quot; &lt;radar&gt;</h1>" in pg and "word word" in pg
   and '<div id="articleView">' in pg and '<div id="latestView" hidden>' in pg, "the article's text is in the page as served (no JavaScript needed)")
ld = [json.loads(x) for x in _re.findall(r'<script type="application/ld\+json">(.*?)</script>', pg, _re.S)]
ok(ld and ld[0]["@type"] == "NewsArticle" and ld[0]["headline"].startswith("ONDA") and ld[0]["image"][0] == img
   and len(ld[0]["citation"]) == 2 and ld[1]["@type"] == "BreadcrumbList", "structured data: NewsArticle with its picture and sources, breadcrumbs")
ok(f'href="/news/a/{slug}?lang=en"' in gen and '<div id="roomWrap">' in gen, "the news page lists the newsroom's articles as served")
r = boss.post("/api/news/admin/sync")
ok(r.status_code == 200 and r.json()["new"] == 0 and r.json()["count"] == 2 and r.json()["status"].startswith("ok"),
   f"admin: 'read the newsroom now' ({r.status_code} {r.text[:80]})")
ok(ins.post("/api/news/admin/sync").status_code == 403, "admins only")
db.x("DELETE FROM news_issues")                          # a fresh day: its issue is built after the articles came in
iss = news.build(day)
ok([a["region"] for a in iss["articles"]] == ["morocco", "europe"], "the next issue opens with the newsroom's articles, Morocco first")
subj, txt, page = news.render(iss, "https://atc.example.com/news/unsubscribe?t=x", "https://atc.example.com")
ok("From our newsroom" in page and f"https://atc.example.com/news/media/{day}-morocco-story/fig.png" in page
   and f"https://atc.example.com/news/a/{day}-morocco-story" in txt, "the e-mail: the articles with their PNG figure and links to the site")
subj, txt, page = news.render(news.issue(iss["id"], "ar"), "", "https://atc.example.com")
ok("من غرفة أخبارنا" in page and "عنوان" in page and f"/news/a/{day}-morocco-story?lang=ar" in page and 'dir="rtl"' in page,
   "in Arabic: the articles' Arabic titles, links to their Arabic pages")
print("news checks passed")
