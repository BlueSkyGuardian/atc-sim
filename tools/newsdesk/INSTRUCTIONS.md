# The ATC SIM newsroom: daily procedure

Every morning (before 07:00 Casablanca time, when the newsletter goes out) the newsroom writes **3 to 5 original
articles** about the last day's aviation news, each with at least one chart or map, and publishes them on the `news`
branch of this repository. The simulator's server reads that branch every hour: the articles appear on the public
news page (`/news`) and at the top of the day's newsletter.

The readers are air traffic controllers, pseudo-pilots and instructors, most of them in Morocco.

## 1. Choose the stories (Morocco first, then Europe, then international)

Search the news of the **last 24 to 48 hours** (web search; open and read the actual articles and, where there is
one, the official source: ONDA, DGAC Maroc, Royal Air Maroc, EUROCONTROL, EASA, the European Commission, ICAO, FAA,
NATS, DSNA, ENAIRE, investigation boards...).

* **Morocco** first: Moroccan airports and air navigation (ONDA), DGAC, Royal Air Maroc and the airlines serving
  Morocco, airspace and routes, airport works, traffic figures, regulation, training. One to two articles when there
  is real news; Morocco in a regional story (Maghreb, West Africa, the Canaries, Spain - Morocco links) counts.
* **Europe** second: EUROCONTROL and the network (capacity, delays, strikes, airspace), the ANSPs, SESAR, EASA,
  airports and airlines. One to two articles.
* **International** third: ICAO, FAA, major safety events and investigations, technology (remote towers, AI in ATM,
  datalink, satellite surveillance, eVTOL integration), the big industry news. One article.

Air traffic control, airspace and air navigation come before airline business news everywhere. Do not repeat a story
already published (look at `articles/index.json` on the `news` branch). **Never invent news**: if fewer than three
stories are worth an article, publish fewer (and never fewer than one only if nothing at all happened - say so in your
final report).

## 2. Write each article

* **Original writing in your own words**, 350 to 1100 words: what happened, why it matters to controllers (capacity,
  procedures, safety, technology, careers), the background, what comes next. Never copy sentences from a source (one
  short quotation, in quotation marks and attributed, at most).
* **Facts only from the sources you read**; every figure, date and name checkable in them. Where sources disagree, say
  so. No speculation presented as fact.
* At least **two independent sources** (different sites), listed in `meta.json` and linked in the text where used.
* Plain, precise English; aviation terms used correctly (ICAO abbreviations explained at their first use).
* Title: factual, under 110 characters. Summary: one or two sentences, under 320 characters.

## 3. Make the figures (charts and maps)

One to three per article, made with `tools/newsdesk/gfx.py` (it draws in the site's style and writes the PNG copy the
e-mails need):

```
python tools/newsdesk/gfx.py map   spec.json articles/<slug>/map.svg   --png articles/<slug>/map.png
python tools/newsdesk/gfx.py chart spec.json articles/<slug>/chart.svg --png articles/<slug>/chart.png
```

* **Maps**: where it happens - the airports, the airspace, the route, the region (`"region": "morocco"`, `"maghreb"`,
  `"west-med"`, `"europe"`, `"mediterranean"`, `"atlantic"`, `"africa"`, `"middle-east"`, `"world"`, or a `bbox`).
  Coordinates [lon, lat] from a reliable source (the AIP, the airport's published position, Wikipedia's coordinates).
  The country shapes are Natural Earth's, as seen from Morocco (Morocco's southern provinces are part of Morocco).
* **Charts**: only numbers that are in the sources (traffic, delays, passengers, orders, dates on a timeline); the
  chart's `source` says where they come from. No chart rather than an invented or estimated one.
* Each figure in `meta.json` with a caption (what it shows) and an alt text (what it shows, for screen readers).

## 4. Put each article in its folder

On the `news` branch: `articles/<YYYY-MM-DD>-<few-words>/` with `meta.json`, `body.md` and the figures. The format is
in `tools/newsdesk/publish.py` (its docstring). `{{figure:map.svg}}` in `body.md` places a figure; otherwise the
figures come after the first paragraph. `"author": "ATC SIM newsroom"`.

## 5. Check and publish

```
git fetch origin news && git worktree add /tmp/news origin/news     # (or: git checkout news)
... write the articles in /tmp/news/articles/ ...
python tools/newsdesk/publish.py /tmp/news           # checks every article, rebuilds articles/index.json
cd /tmp/news && git add articles && git commit -m "Newsroom: <date>: <titles>" && git push origin HEAD:news
```

Fix everything `publish.py` reports before pushing; push only to the `news` branch (never to `main`). Open one PNG of
each article to look at it before publishing (the labels readable, nothing overlapping).

## 6. Report

End with a short report: the articles published (title, region, sources), the stories considered and left out and
why, any problem.
