"""Browser test of the range/bearing lines (RBL): either end snaps to a fix (its symbol or its name) and to an aircraft
(its symbol or its label); the positions at the CPA are drawn only when the CPA button of the RBL's label is on.
  python tests/e2e_rbl.py http://127.0.0.1:8786 <session id> [screenshot dir]
The server runs with ATC_AUTH=off and a session with traffic (e.g. s10_busy_mixed, running)."""
import os
import sys

from playwright.sync_api import sync_playwright

BASE, SID = sys.argv[1], sys.argv[2]
OUT = sys.argv[3] if len(sys.argv) > 3 else "."
os.makedirs(OUT, exist_ok=True)


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m, flush=True)
    if not c:
        raise SystemExit(1)


with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1500, "height": 900})
    pg.goto(f"{BASE}/radar?session={SID}")
    pg.wait_for_function("typeof state !== 'undefined' && state && state.aircraft && state.aircraft.length > 2 && nameRects.length > 5 && labelRects.length > 2", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("document.querySelector('[data-v=names]') || 0")
    end = "(t => typeof t === 'string' ? t : (t.name || 'map position'))"
    # screen points (CSS px, for the mouse) of a fix's name, an aircraft's label (away from its symbol), another one
    pts = pg.evaluate("""() => {
      const c = cv.getBoundingClientRect(), d = devicePixelRatio, css = (x, y) => [c.left + x / d, c.top + y / d];
      const inside = (x, y) => x > 80 * d && y > 80 * d && x < cv.width - 80 * d && y < cv.height - 120 * d;
      const free = (x, y) => !labelAtPt([x, y]) && !shown.aircraft.some(a => { const [ax, ay] = toPx(a.x_nm, a.y_nm); return Math.hypot(ax - x, ay - y) < 20 * d; });
      const nr = nameRects.find(n => sector.waypoints[n.n] && !n.n.startsWith('@') && inside(n.x0, n.y0) && free((n.x0 + n.x1) / 2, (n.y0 + n.y1) / 2));
      const labs = labelRects.filter(l => inside(l.x0, l.y0)).map(l => ({ cs: l.cs, x: l.x1 - 6 * d, y: (l.y0 + l.y1) / 2 }))
        .filter(l => labelAtPt([l.x, l.y]) && labelAtPt([l.x, l.y]).cs === l.cs);
      return { fix: nr.n, name: css((nr.x0 + nr.x1) / 2, (nr.y0 + nr.y1) / 2), labA: labs[0] && { cs: labs[0].cs, at: css(labs[0].x, labs[0].y) },
               labB: labs[1] && { cs: labs[1].cs, at: css(labs[1].x, labs[1].y) } }; }""")
    ok(pts["labA"] and pts["labB"], f"a fix ({pts['fix']}) and two labels on screen ({pts['labA'] and pts['labA']['cs']}, {pts['labB'] and pts['labB']['cs']})")

    # 1. key B from a fix's NAME (the first end snaps to the fix), a click on an aircraft's LABEL places the second end
    pg.mouse.move(*pts["name"])
    pg.keyboard.press("b")
    first = pg.evaluate(f"rblDrag && {end}(rblDrag.from)")
    ok(first == pts["fix"], f"key B on the name of {pts['fix']}: the first end snaps to the fix ({first})")
    pg.mouse.move(*pts["labA"]["at"])
    pg.mouse.click(*pts["labA"]["at"])
    r = pg.evaluate(f"rbls.map(r => [{end}(r.a), {end}(r.b)])")
    ok(r and r[-1] == [pts["fix"], pts["labA"]["cs"]], f"a click on {pts['labA']['cs']}'s label places the second end on it: {r[-1] if r else r}")

    # 2. right-drag from a label to another label
    pg.mouse.move(*pts["labA"]["at"]); pg.mouse.down(button="right")
    pg.mouse.move(pts["labA"]["at"][0] + 30, pts["labA"]["at"][1] + 30, steps=4)
    pg.mouse.move(*pts["labB"]["at"], steps=6); pg.mouse.up(button="right")
    r = pg.evaluate(f"rbls.map(r => [{end}(r.a), {end}(r.b)])")
    ok([pts["labA"]["cs"], pts["labB"]["cs"]] in r, f"right-drag from a label to a label: aircraft to aircraft ({r[-1]})")

    # 3. the RBL button (Tools), two clicks: a fix's name, then a label
    pg.evaluate("document.querySelector('#rblBtn').click()")
    pg.mouse.click(*pts["name"]); pg.mouse.click(*pts["labB"]["at"])
    r = pg.evaluate(f"rbls.map(r => [{end}(r.a), {end}(r.b)])")
    ok([pts["fix"], pts["labB"]["cs"]] in r, f"RBL button: fix name then label ({r[-1]})")

    # 4. the CPA projection: off by default, the CPA line of the label toggles it, dragging the label still moves it
    pg.evaluate("rbls.length = 0")
    pair = pg.evaluate("""() => { const ac = shown.aircraft; let best = null;
        for (let i = 0; i < ac.length; i++) for (let j = i + 1; j < ac.length; j++) { const c = cpa(ac[i], ac[j]);
          if (c.moving && c.t > 60 && c.t < 1800 && (!best || c.d < best.d)) best = { a: ac[i].callsign, b: ac[j].callsign, d: c.d }; }
        if (best) addRbl(best.a, best.b); return best; }""")
    ok(pair is not None, f"two converging aircraft: {pair}")
    pg.wait_for_timeout(400)
    rect = pg.evaluate("(() => { const r = rblRects.find(x => x.i === 0); const c = cv.getBoundingClientRect(), d = devicePixelRatio; return r && r.cpa && [c.left + (r.cpa.x0 + r.cpa.x1) / 2 / d, c.top + (r.cpa.y0 + r.cpa.y1) / 2 / d]; })()")
    ok(rect is not None and pg.evaluate("rbls[0].cpa") is not True, "the label has a CPA button; the projection is off")
    pg.screenshot(path=f"{OUT}/rbl_1_projection_off.png")
    pg.mouse.move(*rect); pg.wait_for_timeout(200)
    ok(pg.evaluate("cv.style.cursor") == "pointer" and pg.evaluate("hoverRblCpa") == 0, "over the CPA line: a button (pointer, underlined)")
    pg.mouse.click(*rect); pg.wait_for_timeout(400)
    ok(pg.evaluate("rbls[0].cpa") is True, "a click shows the positions at the CPA")
    pg.screenshot(path=f"{OUT}/rbl_2_projection_on.png")
    pg.mouse.click(*rect)
    ok(pg.evaluate("rbls[0].cpa") is False, "another click hides them")
    o0 = pg.evaluate("[rbls[0].ox, rbls[0].oy]")
    pg.mouse.move(*rect); pg.mouse.down(); pg.mouse.move(rect[0] + 60, rect[1] + 40, steps=5); pg.mouse.up()
    ok(pg.evaluate("[rbls[0].ox, rbls[0].oy]") != o0 and pg.evaluate("rbls[0].cpa") is False, "dragging the label from the button moves it, no toggle")
    br.close()
print("RBL browser checks passed")
