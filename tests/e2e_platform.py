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
    atc.evaluate(f"onSelectAircraft('{calling}', {{clientX: 700, clientY: 400}}, true)")
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
    atc.wait_for_timeout(600)
    atc.screenshot(path=f"{OUT}/07_controller_label_cfl.png")
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
    ins.once("dialog", lambda d: d.accept())
    ins.click("#endBtn")
    ins.wait_for_url(BASE + "/", timeout=60000)
    atc.wait_for_selector("#pauseBanner:has-text('ended')", timeout=15000)
    ok(True, "end: the controller's screen says so")
    for _ in range(300):
        r = ins.evaluate("fetch('/api/recordings').then(r => r.json())")
        if r and r[0]["status"] != "rendering":
            break
        time.sleep(1)
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
