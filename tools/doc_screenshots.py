"""Screenshots for the user manuals (atcsim/static/docs/*.jpg), taken from a real exercise in three browsers.

  python tools/doc_screenshots.py http://127.0.0.1:8765

The server must run with the test login (no Google client) and ATC_ADMIN_EMAILS=boss@gmail.com, on an empty data
directory (the script creates the accounts). It takes a few minutes; rerun it after UI changes.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "atcsim", "static", "docs")
SCN = os.environ.get("SCN", "p05_ws_storms_arrivals")
os.makedirs(OUT, exist_ok=True)
VIEW = {"width": 1440, "height": 860}


def shot(pg, name, sel=None, pad=14, region=None, full_page=False):
    """Save a JPEG: the whole viewport, a region [x, y, w, h], or the box around the selector(s) (with padding)."""
    clip = None
    if region:
        clip = dict(zip(("x", "y", "width", "height"), region))
    elif sel:
        boxes = [b for s in ([sel] if isinstance(sel, str) else sel) for b in
                 [pg.locator(s).first.bounding_box()] if b]
        if boxes:
            x0 = max(0, min(b["x"] for b in boxes) - pad); y0 = max(0, min(b["y"] for b in boxes) - pad)
            x1 = min(VIEW["width"], max(b["x"] + b["width"] for b in boxes) + pad)
            y1 = max(b["y"] + b["height"] for b in boxes) + pad
            clip = {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
    pg.screenshot(path=os.path.join(OUT, name + ".jpg"), type="jpeg", quality=86, clip=clip, full_page=full_page)
    print("  ", name, "" if not clip else f"{int(clip['width'])}x{int(clip['height'])}", flush=True)


def around(pg, cs, w=520, h=300, dx=-160, dy=-150):
    """A region of the viewport around an aircraft symbol."""
    x, y = pg.evaluate(f"""() => {{ const a = state.aircraft.concat(state.transferred || []).find(q => q.callsign === '{cs}'),
        [x, y] = toPx(a.x_nm, a.y_nm), c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")
    x0, y0 = max(0, x + dx), max(0, y + dy)
    return [x0, y0, min(w, VIEW["width"] - x0), min(h, VIEW["height"] - y0)]


with sync_playwright() as p:
    br = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                 "--autoplay-policy=no-user-gesture-required"])

    def person(email, first, last, lic):
        # reduced motion: the 3D background is drawn once (software WebGL is slow in headless browsers)
        ctx = br.new_context(viewport=VIEW, base_url=BASE, permissions=["microphone"], reduced_motion="reduce")
        pg = ctx.new_page()
        pg.goto("/login"); pg.fill("#email", email); pg.click("#devBtn")
        pg.wait_for_url("**/profile?first=1"); pg.wait_for_selector("#fn")
        pg.fill("#fn", first); pg.fill("#ln", last); pg.fill("#lic", lic); pg.click("#save")
        pg.wait_for_url(BASE + "/")
        return pg

    # ---------------------------------------------------------------- the sign-in page
    gl = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])   # software WebGL: the sign-in scene
    lp = gl.new_context(viewport=VIEW, base_url=BASE).new_page()
    lp.goto("/login"); lp.wait_for_timeout(5000)
    shot(lp, "landing")
    gl.close()

    boss = person("boss@gmail.com", "Samira", "Idrissi", "ADM-1")
    ins = person("ins@gmail.com", "Youssef", "Benali", "MA-INS-12")
    atc = person("atc@gmail.com", "Alaa", "Hamza", "MA-ATC-77")
    pil = person("pil@gmail.com", "Karim", "Tazi", "MA-PP-3")
    boss.goto("/admin"); boss.wait_for_selector("[data-role]")
    for email, role in (("ins@gmail.com", "instructor"), ("pil@gmail.com", "pilot")):
        boss.locator("tr", has_text=email).locator("select").select_option(role); boss.wait_for_timeout(400)
    boss.reload(); boss.wait_for_selector("[data-role]")
    shot(boss, "a_users", "main")

    # ---------------------------------------------------------------- instructor: scenarios, new exercise
    ins.goto("/"); ins.wait_for_selector("#tiles [data-tile=scenarios]"); ins.wait_for_timeout(500)
    shot(ins, "i_home", region=[0, 0, VIEW["width"], VIEW["height"]])
    ins.click("#tiles [data-tile=scenarios]"); ins.wait_for_selector("#scns [data-run]")
    ins.wait_for_timeout(300)
    shot(ins, "i_scenarios", region=[0, 0, VIEW["width"], VIEW["height"]])
    ins.click(f"[data-run='{SCN}']")
    ins.fill("#nxName", "Lesson 5 · storms and arrivals")
    ins.wait_for_timeout(300)
    shot(ins, "i_new_exercise", ".modal")
    ins.click("#nxGo")
    ins.wait_for_url("**/x/*", timeout=180000)
    eid = ins.url.rsplit("/", 1)[1]
    ins.click("#asideTabs [data-t=exercise]")
    ins.wait_for_selector("#exMembers .empty, #exMembers .mem")
    ins.select_option("#invUser", label=ins.locator("#invUser option", has_text="Alaa").inner_text())
    ins.select_option("#invSeat", "atc"); ins.click("#invBtn")
    ins.wait_for_selector("#exMembers .mem")
    links = ins.evaluate("EXINFO.links")
    pil.goto(links["pilot"]); pil.wait_for_url(f"**/x/{eid}")
    atc.goto("/"); atc.wait_for_selector(f"a[href='/x/{eid}']")
    atc.wait_for_timeout(400)
    shot(atc, "c_home", "#tiles")
    atc.click(f"a[href='/x/{eid}'] button")
    for pg in (atc, pil, ins):
        pg.wait_for_function("() => typeof state !== 'undefined' && state && state.aircraft", timeout=60000)
    shot(ins, "i_exercise_tab", "aside")

    # run until an aircraft calls
    ins.click("#ctrlRow [data-act=resume]"); ins.click("#ctrlRow [data-tf='10']")
    calling = None
    for _ in range(300):
        calling = atc.evaluate("(state.aircraft.find(a => a.on_frequency && !a.assumed) || {}).callsign")
        if calling:
            break
        time.sleep(0.5)
    ins.click("#ctrlRow [data-tf='1']")
    assert calling, "nobody called"
    atc.wait_for_timeout(1500)
    shot(atc, "c_radar")
    pil.wait_for_selector("#todo .todo.call", timeout=15000); pil.wait_for_timeout(500)
    shot(pil, "p_todo", "#todo")

    def field_pt(cs, f, zoom=3):
        atc.evaluate(f"""(() => {{ const a = state.aircraft.concat(state.transferred || []).find(x => x.callsign === '{cs}');
            setZoom({zoom}); view.cx = a.x_nm + 8; view.cy = a.y_nm - 3; labelOff['{cs}'] = [40, 60];
            for (const b of state.aircraft.concat(state.transferred || []))       // neighbours' labels out of the way
              if (b.callsign !== '{cs}' && Math.hypot(b.x_nm - a.x_nm, b.y_nm - a.y_nm) < 25) labelOff[b.callsign] = [-170, -90]; }})()""")
        atc.wait_for_timeout(400)
        return atc.evaluate(f"""() => {{ const r = fieldRects.find(q => q.cs === '{cs}' && q.f === '{f}'), c = cv.getBoundingClientRect(), d = devicePixelRatio;
            return r ? [c.left + (r.x0 + r.x1) / 2 / d, c.top + (r.y0 + r.y1) / 2 / d] : null; }}""")

    # calling aircraft, then the callsign menu and the assume
    field_pt(calling, "cs")
    shot(atc, "c_calling", region=around(atc, calling))
    atc.mouse.click(*field_pt(calling, "cs"))
    atc.wait_for_selector("#lblMenu.on.fm [data-c]")
    atc.wait_for_timeout(300)
    shot(atc, "c_callsign_menu", region=around(atc, calling, w=560, h=360, dx=-120, dy=-120))
    atc.click("#lblMenu [data-c=asm]")
    atc.wait_for_function(f"() => (state.aircraft.find(a => a.callsign === '{calling}') || {{}}).assumed")
    atc.keyboard.press("Escape")
    # the level menu (probe colours)
    atc.mouse.click(*field_pt(calling, "cfl"))
    atc.wait_for_selector("#lblMenu.on.fm [data-x]")
    atc.wait_for_timeout(1500)
    shot(atc, "c_level_menu", region=around(atc, calling, w=560, h=420, dx=-120, dy=-120))
    atc.keyboard.press("Escape")
    # full label menu: heading entered, weather deviation, probe
    hp = field_pt(calling, "hdg")
    atc.evaluate(f"onLongPressLabel('{calling}', 'hdg', {{clientX: {hp[0]}, clientY: 90}})")
    atc.wait_for_selector("#lblMenu.on")
    hdg = atc.evaluate(f"state.aircraft.find(a => a.callsign === '{calling}').heading")
    atc.locator("#lblMenu [data-f=heading]").fill(str((int(hdg) + 30) % 360 or 360))
    atc.locator("#lblMenu [data-f=heading]").press("Enter")
    atc.wait_for_function(f"() => state.entries['{calling}'] && state.entries['{calling}'].heading")
    atc.click("#lblMenu [data-wx=L]")
    atc.wait_for_timeout(1500)
    shot(atc, "c_full_menu", "#lblMenu", pad=10)
    atc.keyboard.press("Escape")
    # exit level, then the label close-up
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("x"); atc.wait_for_selector("#lblMenu.on")
    cfl = atc.evaluate(f"state.coord['{calling}'] ? state.coord['{calling}'].cfl : state.aircraft.find(a => a.callsign === '{calling}').cleared_fl")
    atc.keyboard.type(str(int(cfl) - 20)); atc.keyboard.press("Enter")
    atc.wait_for_timeout(800); atc.keyboard.press("Escape")
    field_pt(calling, "cs", zoom=4)
    atc.wait_for_timeout(1200)
    shot(atc, "c_label", region=around(atc, calling, w=420, h=230, dx=-90, dy=-80))
    # type performance
    atc.keyboard.press("Escape")
    atc.mouse.click(*field_pt(calling, "type"))
    try:
        atc.wait_for_selector("#tyWin table tr td", timeout=4000)
    except Exception:
        atc.evaluate(f"openTypeWin('{calling}')"); atc.wait_for_selector("#tyWin table tr td")
    atc.wait_for_timeout(300)
    shot(atc, "c_type", "#tyWin")
    atc.keyboard.press("Escape")
    # route display (long press on the route value)
    atc.keyboard.press("Escape"); atc.evaluate("window.closeMenu && closeMenu()")
    rp = field_pt(calling, "route", zoom=1.4)
    atc.mouse.move(*rp); atc.mouse.down(); atc.wait_for_timeout(900); atc.mouse.up()
    atc.mouse.move(rp[0] + 300, rp[1] - 200); atc.wait_for_timeout(300)
    shot(atc, "c_route")
    atc.keyboard.press("Escape")
    # track info window
    atc.evaluate("document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("i"); atc.wait_for_selector("#tiWin")
    ms = atc.evaluate("(state.aircraft.find(a => a.active !== false && a.mode_s && a.mode_s.selected_fl != null && a.status === 'active') || {}).callsign") or calling
    atc.evaluate(f"selected = '{ms}'")
    atc.mouse.move(*field_pt(ms, "cs", zoom=2)); atc.wait_for_timeout(1500)
    shot(atc, "c_track_info", "#tiWin")
    atc.keyboard.press("i")
    # flight plan, flight list, OLDI
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("f"); atc.wait_for_selector("#fpWin pre"); atc.wait_for_timeout(400)
    shot(atc, "c_fpl", "#fpWin")
    atc.keyboard.press("Escape")
    atc.keyboard.press("g"); atc.wait_for_selector("#flWin tr[data-cs]"); atc.wait_for_timeout(400)
    shot(atc, "c_flight_list", "#flWin")
    atc.keyboard.press("Escape")
    atc.keyboard.press("o"); atc.wait_for_selector("#olWin table"); atc.wait_for_timeout(400)
    shot(atc, "c_oldi", "#olWin")
    atc.keyboard.press("Escape")
    # RBL with B: from an aircraft, following the mouse
    atc.evaluate("setZoom(1.6)")
    atc.wait_for_timeout(500)
    bp = atc.evaluate(f"""() => {{ const a = state.aircraft.find(x => x.callsign === '{calling}'), [x, y] = toPx(a.x_nm, a.y_nm),
        c = cv.getBoundingClientRect(), d = devicePixelRatio; return [c.left + x / d, c.top + y / d]; }}""")
    atc.mouse.move(*bp); atc.keyboard.press("b"); atc.mouse.move(bp[0] - 200, bp[1] + 130, steps=6); atc.wait_for_timeout(400)
    shot(atc, "c_rbl", region=[max(0, bp[0] - 330), max(0, bp[1] - 120), 520, 330])
    atc.mouse.down(); atc.mouse.up()
    # route editor
    atc.evaluate(f"selected = '{calling}'; document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("e"); atc.wait_for_selector("#reWin, .rePanel, [data-re]", timeout=5000)
    atc.wait_for_timeout(600)
    shot(atc, "c_route_editor")
    atc.keyboard.press("Escape")
    # shortcuts and display settings
    atc.keyboard.press("?"); atc.wait_for_selector("#helpWin"); atc.wait_for_timeout(300)
    shot(atc, "c_shortcuts", "#helpWin")
    atc.keyboard.press("Escape")
    atc.click("#dispBtn"); atc.wait_for_timeout(400)
    shot(atc, "c_display", "#dispPanel")
    atc.click("#dpClose")

    # ---------------------------------------------------------------- pseudo-pilot
    pil.click(f"#fleet tr[data-cs='{calling}']")
    pil.wait_for_selector(f"#deck .dk-cs:has-text('{calling}')")
    pil.wait_for_timeout(800)
    shot(pil, "p_seat")
    shot(pil, "p_deck", "#deck")
    # telephone: the pilot calls the controller as Casablanca
    pil.click("#pilotUnits [data-unit=GMMM]")
    atc.wait_for_selector("#callHud .callbox.ring", timeout=5000)
    atc.wait_for_timeout(400)
    shot(atc, "c_phone_ringing", "#callHud")
    atc.click("#callHud [data-ans]"); atc.wait_for_timeout(1500)
    atc.click("#callHud [data-hang]")

    # ---------------------------------------------------------------- CPDLC: command line, pilot WILCO, request, window
    dcs = atc.evaluate("(() => { const e = Object.entries(state.cpdlc || {}).find(([c, d]) => d.state === 'cda' && state.aircraft.some(a => a.callsign === c && a.assumed)); return e ? e[0] : null; })()")
    if dcs:
        fl = atc.evaluate(f"Math.floor(state.aircraft.find(a => a.callsign === '{dcs}').fl / 10) * 10 + 20")
        atc.evaluate(f"selected = '{dcs}'; document.activeElement && document.activeElement.blur()"); atc.wait_for_timeout(400)
        atc.keyboard.press("Enter"); atc.keyboard.type(f"CL{fl} M78 D")
        atc.wait_for_function("document.getElementById('cbSg').children.length > 0", timeout=5000); atc.wait_for_timeout(500)
        shot(atc, "c_cpdlc_typing", region=[300, VIEW["height"] - 330, VIEW["width"] - 600, 330])
        atc.keyboard.press("Backspace"); atc.keyboard.press("Backspace")
        atc.wait_for_function(f"document.getElementById('cbPv').textContent.includes('CLIMB TO FL{fl:03d}')", timeout=5000)
        atc.keyboard.press("Enter")
        pil.wait_for_selector(f"#dlBox .todo.dl[data-cs='{dcs}']", timeout=10000); pil.wait_for_timeout(500)
        pil.evaluate("document.getElementById('dlSec').scrollIntoView({ block: 'start' })"); pil.wait_for_timeout(300)
        shot(pil, "p_cpdlc", "#dlSec")
        pil.click(f"#dlBox .todo.dl[data-cs='{dcs}'] [data-ans=WILCO]"); pil.wait_for_timeout(1500)
        pil.evaluate(f"api(`/sessions/${{SID}}/cpdlc/downlink`, {{ method: 'POST', body: JSON.stringify({{ callsign: '{dcs}', kind: 'level', value: {fl + 20}, reason: 'weather' }}) }})")
        atc.wait_for_timeout(1500)
        atc.keyboard.press("j"); atc.wait_for_selector("#dlWin .dl-r"); atc.wait_for_timeout(1200)
        shot(atc, "c_cpdlc_window", "#dlWin")
        atc.click("#dlWin [data-a=APP]"); atc.wait_for_timeout(1500)
        pil.wait_for_selector(f"#dlBox .todo.dl[data-cs='{dcs}'] [data-ans=WILCO]", timeout=8000)
        pil.click(f"#dlBox .todo.dl[data-cs='{dcs}'] [data-ans=WILCO]"); pil.wait_for_timeout(800)
        atc.keyboard.press("Escape")

    # ---------------------------------------------------------------- coordination chat
    pil.wait_for_selector("#chatSec [data-as] option", state="attached")
    pil.select_option("#chatSec [data-as]", "GCCC"); pil.fill("#chatSec [data-cs]", dcs or "")
    pil.fill("#chatSec [data-tx]", "REQUEST FL360 AT THE BOUNDARY, CONFLICTING TRAFFIC AT FL380"); pil.press("#chatSec [data-tx]", "Enter")
    atc.wait_for_selector(".ch-toast", timeout=8000); atc.wait_for_timeout(300)
    atc.evaluate("document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("t"); atc.wait_for_selector("#chatWin [data-q=APPROVED]")
    atc.click("#chatWin [data-q=APPROVED]"); atc.wait_for_timeout(800)
    atc.fill("#chatWin [data-tx]", "NEXT ONE WILL BE AT FL340"); atc.press("#chatWin [data-tx]", "Enter")
    pil.select_option("#chatSec [data-as]", "GMAC WN"); pil.fill("#chatSec [data-cs]", "")
    pil.fill("#chatSec [data-tx]", "RELEASED FOR CLIMB ON CONTACT"); pil.press("#chatSec [data-tx]", "Enter")
    atc.wait_for_timeout(2000)
    shot(atc, "c_chat", "#chatWin")
    pil.evaluate("document.getElementById('chatSec').scrollIntoView({ block: 'start' })"); pil.wait_for_timeout(300)
    shot(pil, "p_chat", "#chatSec")
    atc.keyboard.press("Escape")

    # ---------------------------------------------------------------- MTCD: fast forward until a planted conflict is close
    ins.click("#ctrlRow [data-tf='10']")
    atc.evaluate("document.activeElement && document.activeElement.blur()")
    atc.keyboard.press("m"); atc.wait_for_selector("#mtWin")
    for _ in range(400):
        if atc.evaluate("(state.mtcd || []).some(c => c.time_to_conflict_s < 600)") or atc.evaluate("state.sim_time_s") > 2300:
            break
        time.sleep(0.5)
    ins.click("#ctrlRow [data-tf='1']")
    atc.wait_for_timeout(2500)
    if atc.evaluate("(state.mtcd || []).length"):
        atc.evaluate("(() => { const c = state.mtcd[0]; selected = c.aircraft[0]; view.cx = (c.at[0][0] + c.at[1][0]) / 2 + 25; view.cy = (c.at[0][1] + c.at[1][1]) / 2; setZoom(1.8); })()")
        atc.wait_for_timeout(1500)
    shot(atc, "c_mtcd")
    atc.keyboard.press("m"); atc.evaluate("selected = null")

    # ---------------------------------------------------------------- instructor: monitor, dashboard
    ins.click("#asideTabs [data-t=monitor]"); ins.wait_for_timeout(1500)
    shot(ins, "i_monitor", "aside")
    ins.click("#asideTabs [data-t=exercise]"); ins.wait_for_selector("#liveSec .lv-tabs")
    ins.click("#liveSec [data-tab-lv=emergency]"); ins.wait_for_timeout(500)
    ins.evaluate("document.getElementById('liveSec').scrollIntoView({ block: 'start' })"); ins.wait_for_timeout(300)
    shot(ins, "i_live", "#liveSec")
    ins.goto("/#live"); ins.wait_for_selector(".thumb[data-id]", state="attached", timeout=90000); ins.wait_for_timeout(4000)
    shot(ins, "i_dashboard", region=[0, 0, VIEW["width"], 520])
    ins.goto(f"/x/{eid}"); ins.wait_for_function("() => typeof state !== 'undefined' && state && state.aircraft", timeout=30000)
    ins.wait_for_timeout(1500)
    shot(ins, "i_seat")
    ins.once("dialog", lambda d: d.accept())
    ins.click("#asideTabs [data-t=exercise]"); ins.click("#endBtn")
    ins.wait_for_url(BASE + "/#evaluations", timeout=60000)
    ins.wait_for_selector("[data-ev]"); ins.click("[data-ev]")
    for g in ins.locator(".modal .stars").all():
        g.locator("button").nth(3).click()
    ins.fill("#evC", "Good scan and early assumes. Start the TERTO arrivals down earlier; the storms south of VEDOD "
                     "left you little room.")
    shot(ins, "i_evaluation", ".modal")
    ins.click("#evGo"); ins.wait_for_selector(".modal", state="detached")
    atc.goto("/#evaluations"); atc.wait_for_selector("#evalReceived .ev")
    shot(atc, "c_evaluation", "#evalReceived")
    atc.goto("/#exercises"); atc.wait_for_selector("#xList [data-x]"); atc.select_option("#xSort", "cx-"); atc.wait_for_timeout(400)
    shot(atc, "c_exercises", region=[0, 0, VIEW["width"], 560])
    atc.goto("/stats"); atc.wait_for_selector("canvas"); atc.wait_for_timeout(1500)
    shot(atc, "c_stats", full_page=True)

    # ---------------------------------------------------------------- designer
    ins.goto("/designer?scenario=s10_busy_mixed")
    ins.wait_for_function("() => scn && scn.name === 's10_busy_mixed' && lastGoodComp", timeout=60000)
    ins.evaluate("setT(600)"); ins.wait_for_timeout(1500)
    shot(ins, "d_designer")
    ins.click("#jsonBtn"); ins.wait_for_timeout(500); ins.evaluate("document.getElementById('jsTa').scrollTop = 0; document.getElementById('jsTa').setSelectionRange(0, 0)")
    shot(ins, "d_json", region=[0, 0, VIEW["width"], VIEW["height"]])
    ins.click("#jsCancel")
    ins.click("[data-tab=chk]"); ins.click("#pvNaive")
    ins.wait_for_function("() => prev.naive_approve", timeout=180000); ins.wait_for_timeout(800)
    shot(ins, "d_check")

    # ---------------------------------------------------------------- solo radar (the controller)
    atc.goto("/solo"); atc.wait_for_selector("[data-fly]"); atc.wait_for_timeout(800)
    shot(atc, "s_pick", region=[0, 0, VIEW["width"], VIEW["height"]])
    atc.click("[data-fly='p02_wn_merges_and_climbs']")
    atc.wait_for_url("**/x/*", timeout=120000)
    atc.wait_for_function("() => typeof state !== 'undefined' && state && state.session && document.body.classList.contains('solo')", timeout=60000)
    atc.keyboard.press("p"); atc.click("#ctrlRow [data-tf='10']"); atc.wait_for_timeout(9000); atc.click("#ctrlRow [data-tf='1']")
    for _ in range(3):
        c = atc.evaluate("(state.aircraft.find(a => a.on_frequency && !a.assumed) || {}).callsign")
        if not c:
            break
        atc.evaluate(f"assume('{c}')"); atc.wait_for_timeout(500)
    scs = atc.evaluate("(state.aircraft.find(a => a.assumed && a.fl >= 300) || state.aircraft.find(a => a.assumed) || {}).callsign")
    if scs:
        fl = atc.evaluate(f"Math.floor(state.aircraft.find(a => a.callsign === '{scs}').fl / 10) * 10 + 20")
        atc.evaluate(f"selected = '{scs}'; document.activeElement && document.activeElement.blur()")
        atc.keyboard.press("Enter"); atc.keyboard.type(f"CL{fl} M78"); atc.wait_for_timeout(300); atc.keyboard.press("Shift+Enter")
        atc.wait_for_timeout(1500)
        atc.keyboard.press("Enter"); atc.keyboard.type("H200"); atc.wait_for_timeout(600)
    shot(atc, "s_radar")
    atc.keyboard.press("Escape")
    # ---------------------------------------------------------------- the debrief of that solo run
    atc.evaluate("""async () => {
        const P = (p, b) => api(p, { method: 'POST', body: JSON.stringify(b) });
        await P(`/sessions/${SID}/bookmark`, { note: 'two arrivals converging at ROLRO' });
        await P(`/sessions/${SID}/control`, { action: 'step', seconds: 900 });
        await P(`/sessions/${SID}/bookmark`, { note: 'late hand-off' });
        await P(`/sessions/${SID}/control`, { action: 'step', seconds: 600 });
        await P(`/api/exercises/${EXID}/end`, {}); }""")
    eid_solo = atc.evaluate("EXID")
    atc.goto(f"/debrief/{eid_solo}/report"); atc.wait_for_selector("#wl"); atc.wait_for_timeout(1500)
    shot(atc, "x_report", full_page=True)
    atc.goto(f"/debrief/{eid_solo}"); atc.wait_for_function("() => typeof rec !== 'undefined' && rec && rec.frames.length > 3 && replayMode", timeout=60000)
    atc.evaluate("replayPlaying = false; setReplay(true, 600)"); atc.wait_for_timeout(2500)
    shot(atc, "x_debrief")
    br.close()
print("screenshots in", OUT)
