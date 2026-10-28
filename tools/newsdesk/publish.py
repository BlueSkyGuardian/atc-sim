"""Check the newsroom's articles and rebuild their index (the site reads articles/index.json from the `news` branch).

    python tools/newsdesk/publish.py <news checkout>            check every article, rebuild the index
    python tools/newsdesk/publish.py <news checkout> --check    check only

An article is a folder articles/<slug>/ (slug: YYYY-MM-DD-words-with-dashes) with
    meta.json   {"slug", "date": "YYYY-MM-DD", "region": "morocco" | "europe" | "international", "section",
                 "title" (<= 110 characters), "summary" (one or two sentences, <= 320 characters),
                 "sources": [{"title", "url": "https://...", "site"}, ...] (two sites at least),
                 "figures": [{"file": "x.svg", "png": "x.png", "caption", "alt", "credit"}] (1 to 3),
                 "author": "ATC SIM newsroom"}
    body.md     the article, 350 to 1100 words of Markdown (## headings, paragraphs, lists, **bold**, links): no HTML;
                {{figure:x.svg}} puts a figure there (otherwise the figures come after the first paragraph)
    x.svg, x.png the figures (tools/newsdesk/gfx.py)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gfx import safe  # noqa: E402

REGIONS = ("morocco", "europe", "international")
SECTIONS = ("Air traffic control & airspace", "Technology & innovation", "Safety", "Airlines & airports",
            "Morocco & Africa", "Defence & space")
SLUG = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")


def check(adir: str) -> list:
    """The problems of one article (empty: ready)."""
    errs = []
    slug = os.path.basename(adir.rstrip("/"))
    if not SLUG.match(slug):
        errs.append(f"folder name {slug!r}: YYYY-MM-DD-words-with-dashes")
    try:
        m = json.load(open(os.path.join(adir, "meta.json"), encoding="utf-8"))
    except Exception as e:                               # noqa: BLE001
        return errs + [f"meta.json: {e}"]
    for k in ("slug", "date", "region", "section", "title", "summary", "sources", "figures"):
        if not m.get(k):
            errs.append(f"meta.json: {k} missing")
    if m.get("slug") != slug:
        errs.append("meta.json: slug is not the folder's name")
    if m.get("date") and not slug.startswith(str(m["date"])):
        errs.append("meta.json: date is not the slug's date")
    if m.get("region") not in REGIONS:
        errs.append(f"region: one of {REGIONS}")
    if m.get("section") not in SECTIONS:
        errs.append(f"section: one of {SECTIONS}")
    if len(m.get("title") or "") > 110:
        errs.append("title: 110 characters at most")
    if len(m.get("summary") or "") > 320:
        errs.append("summary: 320 characters at most")
    srcs = m.get("sources") or []
    if any(not str(s.get("url", "")).startswith("https://") or not s.get("title") or not s.get("site") for s in srcs):
        errs.append("sources: each with title, site and an https:// url")
    if len({str(s.get("site", "")).lower() for s in srcs}) < 2:
        errs.append("sources: two different sites at least")
    figs = m.get("figures") or []
    if not 1 <= len(figs) <= 3:
        errs.append("figures: 1 to 3")
    for f in figs:
        for key in ("file", "png", "caption", "alt"):
            if not f.get(key):
                errs.append(f"figure {f.get('file')}: {key} missing")
        sv = os.path.join(adir, f.get("file") or "-")
        if not (f.get("file") or "").endswith(".svg") or not os.path.exists(sv):
            errs.append(f"figure {f.get('file')}: the .svg file is missing")
        else:
            why = safe(open(sv, encoding="utf-8").read())
            if why:
                errs.append(f"figure {f['file']}: {why}")
            if os.path.getsize(sv) > 600_000:
                errs.append(f"figure {f['file']}: over 600 kB")
        pn = os.path.join(adir, f.get("png") or "-")
        if not (f.get("png") or "").endswith(".png") or not os.path.exists(pn):
            errs.append(f"figure {f.get('file')}: the .png copy is missing (gfx.py --png)")
        elif os.path.getsize(pn) > 900_000:
            errs.append(f"figure {f['png']}: over 900 kB")
    try:
        body = open(os.path.join(adir, "body.md"), encoding="utf-8").read()
    except OSError:
        return errs + ["body.md missing"]
    words = len(re.findall(r"\w+", re.sub(r"\(https?://[^)]*\)", "", body)))
    if not 350 <= words <= 1100:
        errs.append(f"body.md: {words} words (350 to 1100)")
    if re.search(r"<\s*[a-zA-Z/!]", body):
        errs.append("body.md: no HTML")
    if re.search(r"\]\(\s*(?!https://)", body):
        errs.append("body.md: links must be https://")
    if re.search(r"!\[", body):
        errs.append("body.md: no Markdown images - the figures go in meta.json ({{figure:x.svg}} to place one)")
    for ref in re.findall(r"\{\{figure:([^}]+)\}\}", body):
        if ref not in {f.get("file") for f in figs}:
            errs.append(f"body.md: {{{{figure:{ref}}}}} is not one of the figures")
    return errs


def build_index(root: str) -> list:
    adirs = sorted(d for d in os.listdir(os.path.join(root, "articles")) if os.path.isdir(os.path.join(root, "articles", d)))
    out = []
    for d in adirs:
        p = os.path.join(root, "articles", d)
        m = json.load(open(os.path.join(p, "meta.json"), encoding="utf-8"))
        files = ["meta.json", "body.md"] + [x for f in m["figures"] for x in (f["file"], f["png"])]
        mt = max(os.path.getmtime(os.path.join(p, f)) for f in files)
        out.append({"slug": d, "date": m["date"], "region": m["region"], "section": m["section"], "title": m["title"],
                    "summary": m["summary"], "files": files, "updated": int(mt)})
    order = {r: i for i, r in enumerate(REGIONS)}
    out.sort(key=lambda a: (a["date"], -order[a["region"]]), reverse=True)
    return out


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = argv[1]
    adir = os.path.join(root, "articles")
    os.makedirs(adir, exist_ok=True)
    bad = 0
    for d in sorted(os.listdir(adir)):
        p = os.path.join(adir, d)
        if not os.path.isdir(p):
            continue
        errs = check(p)
        if errs:
            bad += 1
            print(f"✗ {d}")
            for e in errs:
                print(f"    {e}")
        else:
            print(f"✓ {d}")
    if bad:
        print(f"{bad} article(s) to fix: the index is not rebuilt")
        return 1
    if "--check" not in argv:
        idx = build_index(root)
        with open(os.path.join(adir, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"generated": int(time.time()), "articles": idx}, f, ensure_ascii=False, indent=1)
        print(f"index.json: {len(idx)} article(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
