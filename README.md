# ATC SIM newsroom

This branch holds only the newsroom's articles (`articles/<YYYY-MM-DD>-<slug>/`: `meta.json`, `body.md` and the
figures), written every morning by a scheduled agent following `tools/newsdesk/INSTRUCTIONS.md` on `main`, checked
with `tools/newsdesk/publish.py`. The simulator's server reads `articles/index.json` from here every hour.
