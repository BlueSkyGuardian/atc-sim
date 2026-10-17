"""Browser test of a connected exercise: instructor, controller and pseudo-pilot in three browsers (fake microphones).
  python tests/e2e_platform.py http://127.0.0.1:8765 <screenshot dir>
The server must run with the test login (no Google client) and ATC_ADMIN_EMAILS=boss@gmail.com."""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = sys.argv[2] if len(sys.argv) > 2 else "."
os.makedirs(OUT, exist_ok=True)
SCN = os.environ.get("SCN", "w02_west_squall_line")


def ok(c, m):
    print(("  ok  " if c else "  FAIL ") + m, flush=True)
    if not c:
        raise SystemExit(1)


def check_key_rbl(pg, who):
    """Key B with nothing selected: from an aircraft under the mouse and from a point in space; the line follows the
    mouse while it is drawn; a click places it."""
    pg.evaluate("selected = null; rbls.length = 0; document.activeElement && document.activeElement.blur()")
    pts = pg.evaluate("""() => { const c = cv.getBoundingClientRect(), d = devicePixelRatio, out = {};
        const vis = (x, y) => x > c.left + 40 && x < c.right - 40 && y > c.top + 60 && y < c.bottom - 90 && document.elementFromPoint(x, y) === cv;
        for (const a of state.aircraft) { const [x, y] = toPx(a.x_nm, a.y_nm), X = c.left + x / d, Y = c.top + y / d;
          if (vis(X, Y) && rblTargetAt([x, y]) === a.callsign) { out.ac = [X, Y, a.callsign]; break; } }
        for (let gx = 0.2; gx < 0.8 && !out.sp; gx += 0.05) for (let gy = 0.25; gy < 0.75 && !out.sp; gy += 0.05) {
          const X = c.left + c.width * gx, Y = c.top + c.height * gy, q = [(X - c.left) * d, (Y - c.top) * d], t = rblTargetAt(q);
          if (vis(X, Y) && typeof t === 'object' && !t.name && !labelRects.some(l => q[0] >= l.x0 - 20 && q[0] <= l.x1 + 20 && q[1] >= l.y0 - 20 && q[1] <= l.y1 + 20)) out.sp = [X, Y]; }
        return out; }""")
    ok(pts.get("ac") and pts.get("sp"), f"{who}: aircraft and empty space on the radar " + str(pts))
    x, y, cs = pts["ac"]
    pg.mouse.move(x, y); pg.wait_for_timeout(100); pg.keyboard.press("b")
    ok(pg.evaluate("!!(rblDrag && rblDrag.key) && rblDrag.from") == cs, f"{who}: B over {cs} (nothing selected) starts the line from it")
    pg.mouse.move(x - 90, y + 60, steps=4); c1 = pg.evaluate("rblDrag.cur.slice()")
    pg.mouse.move(x - 150, y + 110, steps=4); c2 = pg.evaluate("rblDrag.cur.slice()")
    ok(c1 != c2, f"{who}: the line follows the mouse")
    pg.mouse.down(); pg.mouse.up()
    ok(pg.evaluate("rbls.length") == 1 and pg.evaluate("rbls[0].a") == cs and not pg.evaluate("rblDrag"), f"{who}: a click places it")
    sx, sy = pts["sp"]
    pg.mouse.move(sx, sy); pg.wait_for_timeout(100); pg.keyboard.press("B")
    ok(pg.evaluate("!!(rblDrag && rblDrag.key) && typeof rblDrag.from === 'object'"), f"{who}: B in empty space starts the line there")
    pg.mouse.move(sx + 120, sy + 40, steps=4); pg.keyboard.press("b")          # B again also places it
    ok(pg.evaluate("rbls.length") == 2 and pg.evaluate("typeof rbls[1].a === 'object'"), f"{who}: B again places it")
    pg.mouse.move(sx, sy); pg.keyboard.press("b"); pg.keyboard.press("Escape")
    ok(not pg.evaluate("rblDrag") and pg.evaluate("rbls.length") == 2, f"{who}: Escape cancels")
    pg.evaluate("rbls.length = 0")


with sync_playwright() as p:
    br = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                 "--autoplay-policy=no-user-gesture-required"])

    def person(email, first, last, lic):
        ctx = br.new_context(viewport={"width": 1500, "height": 900}, base_url=BASE, permissions=["microphone"])
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and print(f"   [{email} console] {m.text}"))
        pg.on("pageerror", lambda e: print(f"   [{email} pageerror] {e}"))
        pg.goto("/login")
        pg.fill("#email", email)
        pg.click("#devBtn")
        pg.wait_for_url("**/profile?first=1")
        pg.wait_for_selector("#email[value*='@'], #fn")
        pg.fill("#fn", first); pg.fill("#ln", last); pg.fill("#lic", lic)
        pg.click("#save")
        pg.wait_for_url(BASE + "/")
        return ctx, pg

    bctx, boss = person("boss@gmail.com", "Big", "Boss", "ADM-1")
    users = {}
    ictx, ins = person("ins@gmail.com", "Ina", "Structor", "MA-INS-1")
    actx, atc = person("alaa@gmail.com", "Alaa", "Hamza", "MA-ATC-77")
    pctx, pil = person("pil@gmail.com", "Pierre", "Lote", "MA-PP-3")
    boss.goto("/admin")
    boss.wait_for_selector("[data-role]")
    for email, role in (("ins@gmail.com", "instructor"), ("pil@gmail.com", "pilot")):
        row = boss.locator("tr", has_text=email)
        row.locator("select").select_option(role)
        boss.wait_for_timeout(400)
    boss.screenshot(path=f"{OUT}/01_admin.png")
    ok("Pseudo-pilot" in boss.locator("tbody").inner_text(), "admin sets roles")

    ins.reload()
    ins.wait_for_selector("#scns tr")
    ins.screenshot(path=f"{OUT}/02_instructor_home.png", full_page=True)
    ins.click(f"[data-run='{SCN}']")
    ins.fill("#nxName", "Lesson 7 · squall line")
    ins.click("#nxGo")
    ins.wait_for_url("**/x/*", timeout=180000)
    eid = ins.url.rsplit("/", 1)[1]
    ins.click("#asideTabs [data-t=exercise]")
    ins.wait_for_selector("#exMembers .empty, #exMembers .mem")
    ins.wait_for_function("() => window.state || document.querySelector('#hud').textContent.length > 5", timeout=60000)
    ok(True, f"exercise {eid} created from the scenario list")
    # invite the controller from the list, the pilot by link
    ins.select_option("#invUser", label=ins.locator("#invUser option", has_text="Alaa").inner_text())
    ins.select_option("#invSeat", "atc")
    ins.click("#invBtn")
    ins.wait_for_selector("#exMembers .mem")
    links = ins.evaluate("EXINFO.links")
    atc.goto("/")
    atc.wait_for_selector(f"a[href='/x/{eid}']")
    atc.screenshot(path=f"{OUT}/03_controller_home.png")
    atc.click(f"a[href='/x/{eid}'] button")
    pil.goto(links["pilot"])
    pil.wait_for_url(f"**/x/{eid}")
    for pg in (atc, pil):
        pg.wait_for_function("() => typeof state !== 'undefined' && state && state.aircraft", timeout=60000)
    ok(atc.evaluate("document.body.classList.contains('seat-atc')") and pil.evaluate("SEAT") == "pilot", "seats")
    ok(not atc.is_visible("aside"), "controller: radar only, no side panel")
    # run, fast forward until something calls
    ins.click("#ctrlRow [data-act=resume]")
    ins.click("#ctrlRow [data-tf='10']")
    calling = None
    for _ in range(240):
        calling = atc.evaluate("(state.aircraft.find(a => a.on_frequency && !a.assumed) || {}).callsign")
        if calling:
            break
        time.sleep(0.5)
    ins.click("#ctrlRow [data-tf='1']")
    ok(bool(calling), f"{calling} calls")
    pil.wait_for_selector("#todo .todo.call", timeout=10000)
    pil.screenshot(path=f"{OUT}/04_pilot_checkin.png")
    # radio: the pilot calls on the frequency (space bar)
    pil.click("#cv", position={"x": 300, "y": 300})
    pil.keyboard.down("Space")
    atc.wait_for_selector("#rxBox .rx", timeout=5000)
    atc.screenshot(path=f"{OUT}/05_controller_hears_pilot.png")
    ins.wait_for_selector("#rxBox .rx", timeout=5000)
    pil.wait_for_timeout(1500)
    pil.keyboard.up("Space")
    ok(True, "radio: pilot transmits, controller and instructor see who is talking")
    # controller assumes (label menu) and enters a level
    atc.evaluate(f"onLongPressLabel('{calling}', 'cfl', {{clientX: 700, clientY: 400}})")
    atc.wait_for_selector("#lblMenu.on")
    atc.screenshot(path=f"{OUT}/06_label_menu.png")
    atc.click("#lmAsBtn")
    atc.wait_for_function(f"() => (state.aircraft.find(a => a.callsign === '{calling}') || {{}}).assumed")
    ok(True, "controller assumes from the label")
    fl = atc.evaluate(f"state.aircraft.find(a => a.callsign === '{calling}').cleared_fl")
    newfl = fl - 20
    inp = atc.locator("#lblMenu [data-f=cfl]")
    inp.fill(str(newfl)); inp.press("Enter")
    atc.wait_for_function(f"() => state.entries['{calling}'] && state.entries['{calling}'].cfl === {newfl}")
    atc.keyboard.press("Escape")
    atc.click("#cv", position={"x": 50, "y": 600})
    atc.keyboard.down("Space"); atc.wait_for_timeout(1500); atc.keyboard.up("Space")
    ok(True, f"controller enters CFL {newfl} and transmits")
    # decontrol, then take it back (label menu)
    atc.evaluate(f"onLongPressLabel('{calling}', 'cfl', {{clientX: 700, clientY: 400}})")
    atc.click("#lmRelBtn")
    atc.wait_for_function(f"() => state.aircraft.concat(state.transferred).find(a => a.callsign === '{calling}').released")
    atc.evaluate(f"onLongPressLabel('{calling}', 'cfl', {{clientX: 700, clientY: 400}})")
    atc.wait_for_selector("#lmAssume:visible")
    atc.click("#lmAsBtn")
    atc.wait_for_function(f"() => state.aircraft.find(a => a.callsign === '{calling}').assumed")
    atc.keyboard.press("Escape")
    ok(True, "controller decontrols and assumes again")
    # the label's values are flat buttons: move the label clear of the others, then find a value on screen
    def field_pt(cs, f):
        atc.evaluate(f"""(() => {{ const a = state.aircraft.concat(state.transferred || []).find(x => x.callsign === '{cs}');
            setZoom(4); view.cx = a.x_nm + 6; view.cy = a.y_nm - 4; labelOff['{cs}'] = [40, 60]; }})()""")   # zoomed in, clear of the others
        atc.wait_for_timeout(300)
        return atc.evaluate(f"""() => {{ const r = fieldRects.find(q => q.cs === '{cs}' && q.f === '{f}'), c = cv.getBoundingClientRect(), d = devicePixelRatio;
            return r ? [c.left + (r.x0 + r.x1) / 2 / d, c.top + (r.y0 + r.y1) / 2 / d] : null; }}""")
    # a click on the callsign: control options; on the type: its performance; on the level: the level menu
    lp = field_pt(calling, "cs")
    atc.mouse.click(*lp)
    atc.wait_for_selector("#lblMenu.on.fm [data-c]")
    ok(atc.evaluate("selected") == calling and atc.locator("#lblMenu [data-c=rel], #lblMenu [data-c=asm]").count() == 1
       and atc.locator("#lblMenu [data-c=fpl]").count() == 1, "a click on the callsign selects it and offers assume/decontrol, flight plan")
    atc.keyboard.press("Escape")
    # decontrol from the callsign menu; the decontrolled label's callsign takes it back
    atc.mouse.click(*field_pt(calling, "cs"))
    atc.wait_for_selector("#lblMenu [data-c=rel]"); atc.click("#lblMenu [data-c=rel]")
    atc.wait_for_function(f"() => state.aircraft.concat(state.transferred).find(a => a.callsign === '{calling}').released")
    atc.wait_for_timeout(600)
    ok(atc.evaluate(f"window.__drawnLabels['{calling}'].s") == 1, "decontrolled: its own short label")
    atc.screenshot(path=f"{OUT}/07l_decontrolled.png")
    atc.mouse.click(*field_pt(calling, "cs"))
    atc.wait_for_selector("#lblMenu [data-c=asm]"); atc.click("#lblMenu [data-c=asm]")
    atc.wait_for_function(f"() => state.aircraft.find(a => a.callsign === '{calling}').assumed")
    ok(True, "the decontrolled label's callsign assumes it again")
    atc.mouse.click(*field_pt(calling, "type"))
    atc.wait_for_selector("#tyWin table tr td")
    ok("Ceiling" in atc.inner_text("#tyWin") and atc.locator("#tyWin table tr").count() > 5, "a click on the type opens its performance: " + atc.inner_text("#tyWin .wh").replace("\n", " "))
    atc.screenshot(path=f"{OUT}/07k_type_performance.png")
    atc.keyboard.press("Escape")
    atc.mouse.click(*field_pt(calling, "cfl"))
    atc.wait_for_selector("#lblMenu.on.fm [data-x]")
    atc.wait_for_timeout(800)
    atc.screenshot(path=f"{OUT}/07a_level_menu.png")
    ok(atc.locator("#lblMenu.fm [data-x]").count() >= 8, "a click on the level opens the level menu")
    atc.keyboard.press("Escape")
    # long press on the route value: the route; long press on another value: the full menu
    box = field_pt(calling, "route")
    atc.mouse.move(*box); atc.mouse.down(); atc.wait_for_timeout(800)
    ok(bool(atc.evaluate("routeShow && routeShow.cs")), f"long press on the route shows it ({atc.evaluate('routeShow && routeShow.cs')})")
    atc.screenshot(path=f"{OUT}/07b_route_longpress.png")
    atc.mouse.up()
    ok(not atc.is_visible("#lblMenu.on"), "a long press on the route does not open a menu")
    hp = field_pt(calling, "hdg")
    atc.mouse.move(*hp); atc.mouse.down(); atc.wait_for_timeout(800); atc.mouse.up()
    ok(atc.is_visible("#lblMenu.on") and not atc.evaluate("document.querySelector('#lblMenu').classList.contains('fm')"), "long press on a value opens the full label menu")
    atc.keyboard.press("Escape")
    # RBL between two map points / a point and an aircraft
    atc.click("#rblBtn"); atc.mouse.click(1000, 520); atc.mouse.click(1100, 640)
    ok(atc.evaluate("rbls.length === 1 && typeof rbls[0].a === 'object' && typeof rbls[0].b === 'object'"), "RBL between two points " + atc.evaluate("JSON.stringify(rbls)"))
    atc.click("#rblBtn"); atc.mouse.click(*box); atc.mouse.click(1050, 600)
    ok(atc.evaluate("rbls.length === 2"), "RBL between an aircraft and a point")
    # display menu
    atc.click("#dispBtn")
    atc.evaluate("(() => { const i = document.querySelector('#dispPanel [data-k=lblFont]'); i.value = 13; i.dispatchEvent(new Event('input')); })()")
    ok(atc.evaluate("TH.lblFont") == 13, "display menu changes the label font")
    atc.screenshot(path=f"{OUT}/07c_display_menu.png")
    atc.click("#dpClose")
    atc.wait_for_timeout(600)
    atc.screenshot(path=f"{OUT}/07_controller_label_cfl.png")
    # label line 3: the present heading always; a heading entered gives the yellow H tag, a weather deviation the WX tag
    texts = lambda cs: atc.evaluate(f"window.__drawnLabels['{cs}'].l.map(l => l.map(s => s[0]).join(''))")
    hdg = atc.evaluate(f"state.aircraft.find(a => a.callsign === '{calling}').heading")
    l3 = texts(calling)
    ok(any(f" {int(hdg):03d}" in ln for ln in l3), f"label shows the present heading {int(hdg):03d}: {l3}")
    atc.evaluate(f"openLabelMenu('{calling}', 'heading')")
    atc.wait_for_selector("#lblMenu.on")
    atc.keyboard.type(str((int(hdg) + 40) % 360 or 360)); atc.keyboard.press("Enter")
    atc.wait_for_function(f"() => state.entries['{calling}'] && state.entries['{calling}'].heading")
    atc.click("#lblMenu [data-wx=L]")
    atc.wait_for_function(f"() => state.entries['{calling}'].wx === 'L'")
    atc.wait_for_selector("#lblMenu [data-lv].ok, #lblMenu [data-lv].cf", timeout=10000)
    atc.wait_for_timeout(600)
    atc.screenshot(path=f"{OUT}/07d_label_menu_probe_wx.png")
    segs = atc.evaluate(f"window.__drawnLabels['{calling}'].l.flat()")
    htag, wxtag = atc.evaluate("[TH.hTag, TH.wxTag]")
    ok(any(t.strip() == "H" and c == htag for t, c in segs), "H tag after a heading entry")
    ok(any(t.strip() == "WXL" and c == wxtag for t, c in segs), "WX tag after a weather deviation entry")
    ok(atc.locator("#lblMenu [data-lv].ok, #lblMenu [data-lv].cf").count() >= 4, "level probe colours the proposed levels")
    atc.keyboard.press("Escape")
    # exit level (XFL) for the OLDI coordination: key X opens the field
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("x")
    atc.wait_for_selector("#lblMenu.on")
    xfl = newfl - 20
    atc.keyboard.type(str(xfl)); atc.keyboard.press("Enter")
    atc.wait_for_function(f"() => state.coord && state.coord['{calling}'] && state.coord['{calling}'].xfl === {xfl} && state.coord['{calling}'].xfl_entered")
    atc.wait_for_timeout(700)
    ok(any(f" X{xfl}" in ln for ln in texts(calling)), f"label shows CFL and XFL: {texts(calling)[1:3]}")
    atc.keyboard.press("Escape")
    atc.evaluate("document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("o")
    atc.wait_for_selector("#olWin table")
    atc.wait_for_timeout(500)
    ok(atc.locator("#olWin tr[data-cs]").count() > 0, f"O: OLDI messages ({atc.locator('#olWin tr[data-cs]').count()})")
    atc.screenshot(path=f"{OUT}/07i_oldi.png")
    atc.keyboard.press("Escape")
    # keyboard: F flight plan, G flight list, M MTCD
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("f")
    atc.wait_for_selector("#fpWin pre")
    ok("(FPL-" + calling in atc.inner_text("#fpWin pre") and "correlated" in atc.inner_text("#fpWin"), "F: flight plan, correlated with the track")
    atc.screenshot(path=f"{OUT}/07e_flight_plan.png")
    atc.keyboard.press("Escape")
    atc.keyboard.press("g")
    atc.wait_for_selector("#flWin tr[data-cs]")
    ok(atc.locator("#flWin tr[data-cs]").count() > 5, "G: flight plan list")
    atc.screenshot(path=f"{OUT}/07f_flight_list.png")
    atc.keyboard.press("Escape")
    ok(not atc.evaluate("mtcdOn"), "MTCD off by default")
    atc.keyboard.press("M")                     # capitals work too
    atc.wait_for_selector("#mtWin")
    ok(atc.evaluate("mtcdOn"), "M: MTCD on (" + str(atc.evaluate("(state.mtcd || []).length")) + " conflicts)")
    atc.keyboard.press("Escape")
    mc = atc.evaluate("(state.mtcd || [])[0] ? state.mtcd[0].aircraft[0] : null")
    if mc:                                      # its conflict label on the scope can be dragged away
        atc.evaluate(f"(() => {{ selected = '{mc}'; const c = state.mtcd[0]; view.cx = (c.at[0][0] + c.at[1][0]) / 2 - 10; view.cy = (c.at[0][1] + c.at[1][1]) / 2; setZoom(1.5); }})()")
        atc.wait_for_timeout(400)
        r = atc.evaluate("""() => { const r = window.__mtRects[0]; if (!r) return null; const c = cv.getBoundingClientRect(), d = devicePixelRatio;
            return [c.left + (r.x0 + 6) / d, c.top + (r.y0 + r.y1) / 2 / d]; }""")
        if r and atc.evaluate(f"document.elementFromPoint({r[0]}, {r[1]}) === cv"):
            atc.mouse.move(*r); atc.mouse.down(); atc.mouse.move(r[0] + 60, r[1] + 50, steps=5); atc.mouse.up()
            ok(any(v != [0, 0] for v in atc.evaluate("Object.values(mtOff)")), "MTCD label moved")
        else:
            ok(False, f"MTCD label not found on the scope: {r}")
        atc.evaluate("selected = null")
    # track info window (I): the aircraft under the mouse
    atc.keyboard.press("i")
    atc.wait_for_selector("#tiWin")
    atc.evaluate(f"(() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'); view.cx = a.x_nm; view.cy = a.y_nm; }})()")   # centred
    box = field_pt(calling, "cs")
    atc.evaluate(f"""(() => {{ const w = document.getElementById('tiWin'), a = state.aircraft.find(x => x.callsign === '{calling}'), [x] = toPx(a.x_nm, a.y_nm);
        w.style.left = (x / devicePixelRatio > cv.clientWidth / 2 ? 8 : cv.clientWidth - 320) + 'px'; }})()""")   # the window away from it
    cands = [box, atc.evaluate(f"""() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'), [x, y] = toPx(a.x_nm, a.y_nm),
            c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")]
    box = next((b for b in cands if atc.evaluate(f"document.elementFromPoint({b[0]}, {b[1]}) === cv")), cands[1])
    atc.mouse.move(box[0] - 30, box[1] - 30); atc.mouse.move(*box, steps=4); atc.wait_for_timeout(900)
    hv = atc.evaluate("hoverAc")
    ok(bool(hv) and hv in atc.inner_text("#tiWin .wh") and "Mode" in atc.inner_text("#tiWin"),
       f"track info window shows {hv} (" + str(atc.evaluate(f"(() => {{ const el = document.elementFromPoint({box[0]}, {box[1]}); return [el.tagName, el.className, el.parentElement && el.parentElement.id, (el.textContent || '').slice(0, 40), {box[0]}, {box[1]}]; }})()")) + ")")
    atc.screenshot(path=f"{OUT}/07g_track_info.png")
    atc.evaluate("document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("I")                     # off again
    atc.mouse.move(40, 700)
    # route editor (E): a fix and a position, entered as the system route
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("e")
    atc.wait_for_selector("#reWin .rc")
    n_before = atc.locator("#reWin .rc").count()
    spot = next((xy for xy in ((820, 470), (700, 600), (900, 300), (600, 380)) if atc.evaluate(f"document.elementFromPoint({xy[0]}, {xy[1]}) === cv")), (820, 470))
    atc.mouse.click(*spot)
    atc.wait_for_timeout(300)
    ok(atc.locator("#reWin .rc").count() == n_before + 1, f"route editor: a click on the map adds a point ({atc.locator('#reWin .rc').count()} / {n_before}, {atc.evaluate(f'document.elementFromPoint({spot[0]}, {spot[1]}).id')})")
    atc.screenshot(path=f"{OUT}/07j_route_editor.png")
    atc.click("#reWin [data-apply]")
    atc.wait_for_function(f"() => state.entries['{calling}'] && (state.entries['{calling}'].route || []).length > 0 && state.entries['{calling}'].route.every(p => typeof p === 'string')", timeout=10000)
    ok(True, "re-routing entered: " + " ".join(atc.evaluate(f"state.entries['{calling}'].route")))
    # any track can be assumed: one still with the previous sector
    inb = atc.evaluate("(state.aircraft.find(a => !a.on_frequency && !a.assumed) || {}).callsign")
    if inb:
        atc.evaluate(f"assume('{inb}')")
        atc.wait_for_function(f"() => (state.aircraft.find(a => a.callsign === '{inb}') || {{}}).assumed", timeout=10000)
        ok(True, f"assumes {inb} before it calls (early assume)")
    # key B over an aircraft: the line follows the mouse, a click places it
    n0 = atc.evaluate("rbls.length")
    bp = atc.evaluate(f"""() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'), [x, y] = toPx(a.x_nm, a.y_nm),
        c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")
    if atc.evaluate(f"document.elementFromPoint({bp[0]}, {bp[1]}) === cv"):
        atc.evaluate("document.activeElement && document.activeElement.blur()")
        atc.mouse.move(*bp); atc.keyboard.press("b"); atc.mouse.move(bp[0] - 120, bp[1] + 80, steps=5); atc.mouse.down(); atc.mouse.up()
        ok(atc.evaluate("rbls.length") == n0 + 1 and atc.evaluate("rbls[rbls.length - 1].a") == calling, "B over an aircraft: RBL from it, placed by a click " + atc.evaluate("JSON.stringify({r: rbls.slice(-1), d: rblDrag, sel: selected})"))
    check_key_rbl(atc, "controller")
    # RBL by right-drag (no button), then its label moved
    n0 = atc.evaluate("rbls.length")
    ap = atc.evaluate(f"""() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'), [x, y] = toPx(a.x_nm, a.y_nm),
        c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")
    atc.mouse.move(*ap); atc.mouse.down(button="right"); atc.mouse.move(ap[0] - 80, ap[1] + 60, steps=6)
    atc.mouse.move(ap[0] - 160, ap[1] + 120, steps=6); atc.mouse.up(button="right")
    ok(atc.evaluate("rbls.length") == n0 + 1, "RBL by right-drag")
    r = atc.evaluate("""() => { const r = rblRects[rblRects.length - 1], c = cv.getBoundingClientRect(), d = devicePixelRatio;
        return [c.left + (r.x0 + 8) / d, c.top + (r.y0 + r.y1) / 2 / d]; }""")
    atc.mouse.move(*r); atc.mouse.down(); atc.mouse.move(r[0] + 50, r[1] + 70, steps=6); atc.mouse.up()
    ok(atc.evaluate("rbls[rbls.length - 1].ox") != 0, "RBL label moved")
    atc.screenshot(path=f"{OUT}/07h_rbl_drag.png")
    check_key_rbl(pil, "pilot")
    # the pilot selects the aircraft on the radar: the keyboard stays free for the space bar (no jump into the shorthand)
    pp = pil.evaluate(f"""() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'), [x, y] = toPx(a.x_nm, a.y_nm),
        c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")
    if pil.evaluate(f"document.elementFromPoint({pp[0]}, {pp[1]}) === cv"):
        pil.mouse.click(*pp); pil.wait_for_timeout(400)
        ok(pil.evaluate("selected") == calling and pil.evaluate("document.activeElement.tagName") != "INPUT",
           "pilot selects on the radar, the shorthand line does not take the keyboard")
    # the pilot executes
    pil.click(f"#fleet tr[data-cs='{calling}']")
    pil.wait_for_selector(f"#deck .dk-cs:has-text('{calling}')")
    pil.fill("#deck [data-i=fl]", str(newfl))
    pil.click("#deck [data-go=fl]")
    pil.wait_for_function("() => { const o = document.querySelector('#deck [data-out]'); return o && o.textContent; }", timeout=20000)
    res = pil.locator("#deck [data-out]")
    ok("c-los" not in (res.get_attribute("class") or ""), "pilot panel: " + res.inner_text())
    pil.screenshot(path=f"{OUT}/08_pilot_panel.png")
    pil.wait_for_function(f"() => state.aircraft.find(a => a.callsign === '{calling}').cleared_fl === {newfl}", timeout=30000)
    ok(True, "pilot executes the clearance")
    pt = pil.locator("#deck button.pt").nth(1)
    ptname = pt.inner_text()
    pt.click()
    pil.wait_for_function(f"() => state.aircraft.find(a => a.callsign === '{calling}').next_waypoint === '{ptname}'", timeout=30000)
    ok(True, f"pilot flies direct {ptname} from the deck")
    via = pil.evaluate(f"""(() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}');
        return realPoints((a.route || []).slice(Math.max(0, a.route.indexOf(a.next_waypoint || a.fp_next))).concat(a.route_after_exit || [])).slice(-2).join(' '); }})()""")
    if via:
        pil.fill("#deck [data-i=via]", via)
        pil.click("#deck [data-go=via]")
        pil.wait_for_function("() => { const o = document.querySelector('#deck [data-out]'); return o && /done|executing|✗/.test(o.textContent); }", timeout=20000)
        ok("✓" in pil.inner_text("#deck [data-out]"), f"pilot re-routes via {via}: " + pil.inner_text("#deck [data-out]"))
    pil.screenshot(path=f"{OUT}/08b_pilot_deck.png")
    # phone: the pilot calls the controller as Casablanca
    pil.click("#pilotUnits [data-unit=GMMM]")
    atc.wait_for_selector("#callHud .callbox.ring", timeout=5000)
    atc.screenshot(path=f"{OUT}/09_controller_phone_ringing.png")
    atc.click("#callHud [data-ans]")
    pil.wait_for_selector("#callHud .callbox.act", timeout=5000)
    ins.wait_for_selector("#callHud .callbox:has-text('listening')", timeout=5000)
    atc.wait_for_timeout(2500)
    ins.screenshot(path=f"{OUT}/10_instructor_listening.png")
    atc.click("#callHud [data-hang]")
    atc.wait_for_function("() => !document.querySelector('#callHud .callbox')")
    ok(True, "phone: ring, answer, instructor listens in, hang up")
    # the controller calls Lisbon, the pilot answers as LPPC
    atc.click("#phoneBtn"); atc.click("#atcUnits [data-unit=LPPC]")
    pil.wait_for_selector("#callHud .callbox.ring", timeout=5000)
    pil.click("#callHud [data-ans]")
    atc.wait_for_selector("#callHud .callbox.act", timeout=5000)
    atc.wait_for_timeout(1500)
    pil.click("#callHud [data-hang]")
    ok(True, "phone: controller calls a unit, the pilot answers")
    rx = atc.evaluate("window.__voice.rxFrames || 0")
    ok(rx > 10, f"controller's browser played {rx} audio frames (fake microphones)")
    # dashboard
    ins.goto("/")
    ins.wait_for_selector(".thumb[data-id] canvas")
    ins.wait_for_timeout(2500)
    ins.screenshot(path=f"{OUT}/11_instructor_dashboard.png")
    ok("LIVE" in ins.locator(f".thumb[data-id='{eid}'] [data-bar]").inner_text(), "dashboard thumbnail: live")
    ins.click(f".thumb[data-id='{eid}']")
    ins.wait_for_url(f"**/x/{eid}")
    ins.wait_for_function("() => typeof state !== 'undefined' && state && state.aircraft", timeout=30000)
    ins.screenshot(path=f"{OUT}/12_instructor_seat.png")
    ins.wait_for_timeout(1500)
    check_key_rbl(ins, "instructor")
    ins.once("dialog", lambda d: d.accept())
    ins.click("#endBtn")
    ins.wait_for_url(BASE + "/#evaluations", timeout=60000)
    atc.wait_for_selector("#pauseBanner:has-text('ended')", timeout=15000)
    ok(True, "end: the controller's screen says so")
    for _ in range(300):
        r = ins.evaluate("fetch('/api/recordings').then(r => r.json())")
        if r and r[0]["status"] != "rendering":
            break
        time.sleep(1)
    # evaluations: the instructor rates the controller, the controller rates the pilot and acknowledges
    ins.wait_for_selector("[data-ev]")
    ins.click("[data-ev]")
    for g in ins.locator(".modal .stars").all():
        g.locator("button").nth(3).click()
    ins.fill("#evC", "Good scan. Anticipate the crossing at TERTO earlier.")
    ins.click("#evGo")
    ins.wait_for_selector(".modal", state="detached")
    ok(True, "instructor evaluates the controller")
    atc.goto("/#evaluations")
    atc.wait_for_selector("#evalReceived .ev")
    ok("Anticipate the crossing" in atc.locator("#evalReceived").inner_text(), "controller reads the evaluation")
    atc.fill("[data-ackc]", "Noted, thank you.")
    atc.click("[data-ack]")
    atc.wait_for_selector("#evalReceived .ack .cm")
    ok(True, "controller acknowledges it")
    atc.click("[data-ev]")
    for g in atc.locator(".modal .stars").all():
        g.locator("button").nth(4).click()
    atc.click("#evGo")
    atc.wait_for_selector(".modal", state="detached")
    ok(True, "controller rates the pseudo-pilot")
    atc.goto("/stats"); atc.wait_for_selector("canvas")
    atc.screenshot(path=f"{OUT}/14_controller_stats.png", full_page=True)
    ok(r[0]["status"] == "ready", f"recording {r[0]['status']} {r[0].get('error') or ''} {r[0].get('duration_s')} s")
    ins.reload(); ins.wait_for_selector("#recs tr")
    ins.screenshot(path=f"{OUT}/13_recordings.png")
    with ins.expect_download() as dl:
        ins.click("#recs a button")
    path = f"{OUT}/exercise.mp4"
    dl.value.save_as(path)
    ok(os.path.getsize(path) > 10000, f"downloaded {os.path.getsize(path)} bytes")
    br.close()
print("e2e passed")
