# ATC Sector Simulator

An en-route sector simulator for training and evaluating ATC agents (LLM or otherwise). It runs scripted
traffic scenarios in a synthetic training sector or in the **real Agadir CTA** (AIP Maroc ENR 4.4 points, ENR
airways, RAD direct routings, the four Agadir sectors), with the following, and streams everything to a live radar
page:
- type-specific aircraft performance;
- a full 3D weather model: jet streams, thunderstorms, radar reflectivity from the ground to FL800, turbulence;
- pilots who request weather deviations and ride levels on their own;
- controller instructions over a local-network HTTP/WebSocket API;
- the semicircular rule, RVSM (FL290–FL410) with per-aircraft RVSM approval, in-flight loss of RVSM capability,
  state aircraft and military formations (2000 ft);
- a flight plan for every aircraft (departure, destination, ICAO route string, equipment, RVSM status).

```
pip install -r requirements.txt
python run.py                      # listens on 0.0.0.0:8000, prints the LAN address
```

**Documentation:** the app has a *Help* menu (`/help`) with the user manuals (controller, pseudo-pilot,
instructor / admin), the scenario designer and JSON format, the scenario library, the weather and aircraft models
and the API reference. The pages are Markdown files in `atcsim/docs/`, public (no sign-in needed).

Open `http://<machine-ip>:8000/` and sign in with Google (see DEPLOY.md). For a local test without Google, start
with `python run.py --dev-login`: a test login then asks only for an e-mail address (the first account is
administrator). It is off unless asked for, because it lets anyone in as anyone. `/radar` is the single-user radar of the earlier versions and
`/docs` the interactive API reference. Scripts and agents send the `X-API-Key` header (`ATC_API_KEY`), or run the
server with `ATC_AUTH=off` for the old open mode on your own machine. The first session that uses a new aircraft
type or temperature takes about a second longer, while performance tables are built.
To put it on the internet with Google sign-in and HTTPS, see **[DEPLOY.md](DEPLOY.md)**.

## Connected exercises

Three kinds of users sign in with their Google account; their profile holds first name, last name and licence
number. The **administrator** (`ATC_ADMIN_EMAILS`) gives roles on the *Users* page.

- **Instructor** (home page):
  - **Live exercises** as a grid of live thumbnails (airspace and traffic), each with a status bar: elapsed sim
    time, LIVE / PAUSED / READY, conflict (red **LOSS**, amber **CONFLICT** within 3 min, or none), who is connected,
    a TX tag on whoever is transmitting, aircraft waiting to be assumed. Click one to open it; the exercise page has
    a switcher to the others and a link back to the grid.
  - **Scenarios** they can use: the library (read-only), their own (read/write), and those other instructors shared
    with them (read-only or read/write). *Copy* makes a private copy, *Share* gives another instructor access,
    *Edit* opens the designer (saving a read-only scenario requires a new name).
  - **Start exercise** builds the scenario paused, then on the exercise page: invite people from the user list (name,
    licence) as controller, pseudo-pilot or co-instructor, or copy the controller / pseudo-pilot link and send it.
    Run, pause, time factor, restart, all the panels of the radar (requests, conflicts, sequence, events, metrics),
    the pseudo-pilot's aircraft panel and command line, the telephone as any unit. **End exercise** closes it for
    everybody and produces the recording.
- **Controller** (seat *atc*): only the radar. The toolbar changes the view only: weather product and level, cloud
  tops, areas, routes, RAD, sector borders, sector names/frequencies, point names, trails, RBL, reset view, the
  flight-plan list (**FPL**), the **MTCD** list, **?** (shortcuts) and the UTC clock.
  - **Assume any track, any time:** a calling aircraft (blinking, *CALL*), one still with the previous sector (early
    assume; it then calls already assumed), or one decontrolled earlier. Double-click it, ASSUME in its label menu,
    or key **A**. **Decontrol** (label menu, key **U**, `CS REL`) and take back as often as needed.
  - **Decontrolled aircraft** have a short label of their own (a diamond symbol, the callsign, level and type, next
    unit, in grey); its callsign is a button too: *Assume* takes the aircraft back.
  - **The label's values are flat buttons.** A click on the **callsign** selects the aircraft and offers assume or
    decontrol, flight plan, track info, type performance, edit / show route and the full menu. A click on the
    **aircraft type** opens its performance window (type data, speed schedule, assignable Mach / IAS, mass now, and
    by level at the present weight: speeds, climb rate, idle descent, whether the level can be reached). A click on
    a value opens the small menu of that value: the level (levels list, coloured by the probe), the exit level (with *= CFL*),
    the speed, the heading (left / right steps, own navigation), the route (directs, *Edit route…*, back to the filed
    route), the WX tag. A **long press on a value** opens the full label menu; a long press on the **route** value
    (the next point) shows the route. The value under the mouse is underlined.
  - **Full label menu** (long press) to enter what was cleared: CFL (with quick levels, coloured by the level probe: green = no
    conflict in 20 min, red = conflict, hover for which), speed (M78, 280, SN), heading, direct (points of the route
    or any point), **exit level** (XFL), **weather deviation** (L, R, L20, either side) and a free note; **Probe** checks the level / heading
    / direct typed before giving it. This is what the label then shows. The aircraft does not move: the pseudo-pilot
    flies what was said on the frequency.
  - **Three levels:** the **current level** (AFL, from Mode C / Mode S), the **cleared level** (CFL, what the
    controller entered; until then the coordinated level) and the **exit level** (XFL, entered for the coordination
    with the next unit; empty = the cleared level is coordinated). The level line reads `350↓330 S350 X310 B738`:
    current, arrow, cleared, the Mode S selected level in amber when it differs from the CFL (enhanced surveillance
    only), and `X310` when an exit level is entered or differs — cyan once coordinated by ACT, light blue after the
    ABI, white not yet sent, amber when it breaks the LoA / semicircular rule or needs a phone coordination.
  - **OLDI coordination** (automatic, for every aircraft you control, with the unit it goes to): **ABI** 20 minutes
    before the coordination point (again if the exit level changes), **ACT** 10 minutes before (binding: point,
    estimate, exit level), **REV** when the exit level — or the estimate, by more than 3 minutes — changes after the
    ACT, and a **LAM** back from the receiving system for each. A change closer than 3 minutes to the point is not
    sent: `COORD` blinks on the label, the OLDI button shows ⚠, and the label menu says what to coordinate with whom
    by phone, with a *✓ coordinated by phone* button. The previous units send their ABI / ACT for incoming traffic:
    an inbound label shows the entry point, the coordinated entry level and the unit (`KORNO F350 ←GMMM`). The
    **OLDI** window (key **O**) lists every message sent and received; the label menu, the track info window and the
    flight-plan window show the coordination state. Times per scenario: `"oldi": {"abi_s": 1200, "act_s": 600,
    "rev_limit_s": 180}`. Pseudo-pilots and instructors, who play the adjacent units, see the same messages and the
    level coordinated for the selected aircraft.
  - **Label line 3:** ground speed, speed, the **present heading (always)** and the next point of the route (or the
    direct entered). A speed or heading being changed shows current›assigned in a highlight colour (`M78›76`,
    `127›250`). An assigned heading adds a yellow **H**, an approved weather deviation a yellow **WX** (`WXL20`).
    On the controller's seat these come only from what the controller entered — a pilot on a heading or deviating
    by himself shows nothing until the controller enters it (the route-deviation alert then shows). Elsewhere they
    are what the aircraft is doing.
  - **Line above the callsign — special tracks and alerts (visual only):** `NRVSM` (amber inside FL290–410), `MIL`,
    `NSL` non-standard level (against the semicircular rule, or not a flight level), `RDEV` route deviation (more than
    3 NM from the flight-plan route, or not flying the direct entered, with no heading / WX entered), `LVL` level bust
    (leaving the cleared level, or levelled more than 300 ft from it; red, flashing), and `MTC7'` medium-term conflict
    in 7 minutes.
  - **Transfer cues:** inbound traffic still with the previous sector in its own colour with *entry point
    ←previous unit*; calling in white, blinking, *CALL*; assumed in green; **3 minutes before the exit** a chevron
    ahead of the symbol and *XFR →next unit* (LPPC, GCCC, LECS, GMMM, MADEIRA, or the Agadir sector not controlled);
    after the exit, still assumed, *DECONTROL →unit* blinking; decontrolled in grey with the next unit.
  - **Track info window** (*INFO*, key **I**; movable, on or off): the aircraft under the mouse, else the selected
    one, else the last one under the mouse (middle-click an aircraft or 📌 to keep one): SSR code, Mode S address, type, registration, operator,
    departure → destination and requested level, current / cleared / exit levels and the coordination state, the
    **Mode S downlinked parameters** when the aircraft has enhanced surveillance (selected level, magnetic heading,
    IAS, Mach, TAS, ground speed, track, vertical rate, roll, track rate, pressure setting), the next points, units,
    exit point and time, and its alerts. Elementary surveillance gives the address only; Mode A/C nothing more.
  - **Re-routing** (route value → *Edit route…*, or key **E**): the route from the aircraft onwards as a list of
    points. Click on the map to insert a point after the highlighted one: a fix when its name or its symbol is
    clicked, otherwise the position itself (named from its coordinates, e.g. `3103N01244W`); × removes a point;
    names can be typed. *Enter route* puts it in the system: the label's next point, the route display, MTCD, route
    adherence and the estimates follow it. The pseudo-pilot makes the aircraft fly a re-routing from the deck's
    **Route** row (*Fly via* the points typed, or *Edit on map…*), as `{"type": "reroute", "points": [...]}`: the new
    points, then the rest of the route from the last one if it is on it (else the planned exit).
  - **Flight plans** (key **F** for the selected aircraft, **G** or *FPL* for the list): every flight has an ICAO flight
    plan, correlated with its track by the SSR code and the Mode S address. The window shows the FPL message, the
    decoded items, the coordination (entry point, ETO, level, from which unit; exit point, ETO, level, to which unit)
    and the estimates over the points ahead. The list holds every flight of the session (planned, inbound, calling,
    assumed, transferred) with filters.
  - **MTCD** (button or key **M**, on / off, off by default): medium-term conflict detection over 20 minutes, from the system trajectories (the
    flight-plan route, or the heading / direct entered, at the system cleared level), so it warns long before the
    STCA. Click a conflict (or select one of the aircraft) to see where both will be when it starts; drag that label
    away if it is in the way. The aircraft
    gets a violet diamond and `MTCn'` in its label.
  - **Long press** on a label (or an aircraft, or key **R**): its flight-plan route from the present position — also
    while vectored or deviating, with the heading line — every point with the minutes to it.
  - **RBL:** right-drag from an aircraft or any point to another (no button needed); or press **B** with the mouse
    over an aircraft, a fix or anywhere in space: the line starts there and follows the mouse, a click (or B again)
    places its other end, Esc cancels; or shift-click two, or the RBL button. An end snaps to a fix only when the click is within 10 px of the fix symbol (a ring shows what it snaps
    to). While dragging, the line already shows bearing, range, ΔFL and the CPA. A point stands still: the CPA is the aircraft's closest approach to it. Drag
    an RBL label to move it; right-click it to remove it.
  - **Keyboard** (outside text fields; capitals or not): Tab / Shift+Tab next aircraft, C or L cleared level,
    X exit level, H heading, S speed, D direct / route, W weather deviation (each opens that value's menu), N note,
    E edit route, A assume, U decontrol, I track info, O OLDI, F flight plan, R route, B RBL
    from the mouse, Delete last RBL (Shift+Delete all), G flight list, M MTCD, + / − zoom, arrows pan, 0 reset, Esc,
    ? help; Space (held) transmits.
  - **⚙ Display:** zoom, label font, point-name font, aircraft symbol, speed vector length, history dots, weather
    opacity, and the colours of the scope (background, boundaries, sector lines, airways, RAD, points, aerodromes…),
    of each aircraft state (assumed, calling, not concerned, handed off, selected, STCA, loss) and of each label line
    (callsign, level, speed/heading, info; *auto* = the state colour), the changing-value and Mode S highlights.
    Saved in the browser; the controller video uses the controller's settings.
  - **Assume again:** a decontrolled aircraft is taken back with ASSUME in its label menu or a double-click.
  - **Decontrol by hand:** an aircraft that leaves the sector stays assumed (full label, *left sector · decontrol*)
    and keeps flying its flight plan in the next sectors until the controller decontrols it (label menu
    **DECONTROL**, or `CS REL`); it then turns grey as another sector's traffic and leaves the picture once well
    clear. Decontrolling earlier, inside the sector, is possible too. The pseudo-pilot keeps flying the aircraft
    either way (headings, levels, speeds from the next sector), with level limits FL50–FL600 once it is gone.
  - STCA (3 min) and loss-of-separation alarms; no pilot request list: requests come by voice.
- **Pseudo-pilot** (seat *pilot*): the radar and a workspace in three parts.
  - **To do:** what to say on the frequency, oldest first: first calls (*“RAM101 FL350, checking in”*, until the
    controller assumes) and pilot requests or reports to read out, with **Approved** (the aircraft does it) /
    **Refused** / **Acknowledged**.
  - **Control deck** of the selected aircraft (click it in the list, in To do or on the radar): present level,
    speed, heading and navigation, what it is executing, its limits (max FL now, Mach and IAS range, climb rate),
    then one row per action: **Level** (value, ±10/±20, optional minimum rate, *Climb / Descend FLxxx*),
    **Heading** (left / right / shortest, resume own navigation), **Speed** (Mach, knots now, on conversion, resume
    normal), **Direct** (the next points of the route as buttons, or any point), **Weather** deviation. The result,
    or the aircraft's *unable …*, shows under it. Enter validates a field.
  - **Weather does not block the pseudo-pilot:** a heading or direct into weather is executed, with a note
    (the simulated pilots of the agent API still refuse them, as a real crew would).
  - **Can do now:** the deck shows the aircraft's present limits and refreshes them every 10 s: the levels it may
    be given (FL100 to the sector's top, or FL50–FL600 once it is in another sector) and its maximum level at this
    weight, climb rate available now, idle descent rate (and with speed brakes), turn radius, assignable Mach and
    IAS range and its own speed schedule (Mach / IAS / conversion level); rates it cannot fly are greyed out. Use them
    to request something realistic when going off script.
  - **Aircraft:** every aircraft with level, speed, navigation and status (calling, request, on frequency,
    previous sector), those needing something first. The *Shorthand* line still takes the command syntax.
- **Weather in exercises:** aircraft never deviate by themselves: only the pseudo-pilot makes them deviate. A
  weather-deviation request (the simulated pilot asks at about 60 NM) still unanswered when the weather is 15 NM
  ahead is closed as **not answered** and counted as a task not handled (metrics, the controller's statistics).
  The system keeps track of every aircraft **dealing with weather** (it asked for a deviation, was cleared one, the
  controller entered a WX tag, or the pseudo-pilot made it deviate): weather it flies through meanwhile is not
  counted as a penetration (it is kept apart in the metrics). The pseudo-pilot's To do shows a *WX* item for each,
  and the deck a **Clear of weather** button: once reported clear, weather is counted again. The instructor can
  give back the old behaviour in the Exercise tab (*simulated pilots deviate around weather by themselves*: an
  unanswered request then ends in a deviation without clearance, and aircraft still with the previous sector deviate
  as coordinated there). Solo sessions and the agent API keep the simulated pilots' own deviations.
- **Instructor's side panel** is in tabs: **Aircraft** (the pseudo-pilot workspace and telephone), **Exercise**
  (members, invitations, links, run / pause / speed / restart / replay, end), **Monitor** (selected aircraft details,
  requests, sequence, predicted conflicts, events, metrics).
- **Radio:** hold the **space bar** (outside text fields) or the on-screen button to transmit on the frequency.
  Everybody in the exercise hears it; the scope shows who is transmitting.
- **Telephone:** the controller's ☎ menu lists GMMM, LPPC, GCCC, LECS, MADEIRA; calling one rings the pseudo-pilots
  (and instructors), who answer as that unit. Pseudo-pilots and instructors call the controller as any of those
  units from their *Telephone* panel; the controller sees a ringing box with the unit and answers. Calls are full
  duplex (open microphone) until hung up; instructors hear every call.
- **Evaluations:** when an exercise ends, the instructor rates each controller (phraseology, conflict detection,
  conflict resolution, anticipation and planning, coordination, and an overall grade, all out of 5, with a comment),
  and each controller rates the pseudo-pilots (readbacks, execution of clearances, reaction time, R/T, realism, overall
  grade, comment). They are written from the home page (*Evaluations*, also linked from the "exercise ended" banner).
  The controller reads the instructor's evaluation there and acknowledges it with a comment, which the instructor sees.
- **Stats** (menu): tabs by role.
  - *As instructor:* exercises run, hours simulated, pupils, evaluations given; the pupils' losses of separation by
    session number (the mean over the pupils of their 1st, 2nd, 3rd… session with you, and the mean of the sessions
    so far: lower is better), one line per pupil, the mean grade by session number, exercises per month, and a table
    per pupil (first / last / mean losses, mean grade).
  - *As controller:* losses of separation per session (and per hour of simulation), the instructors' grades over
    time (overall and each dimension), other mistakes (area infringements, RVSM, wrong-direction exits, weather
    penetrations, requests left unanswered), reaction times (assume, answer a request), the average profile per
    dimension, and the list of sessions. Solo-radar sessions count as practice.
  - *As pseudo-pilot:* the controllers' ratings over time, the average profile and their comments.
  The results of an exercise are kept when it is ended with *End exercise*.
- **Recording:** what matters is the controller's screen. While the exercise runs, the controller's browser sends
  what happens on it (scope size, zoom and pan, display options, weather product, label positions, selection, RBLs,
  every on-screen panel with its text: toolbar, HUD, alarms, the label menu with what is being typed, telephone
  boxes, push-to-talk; the mouse 20 times a second; every click and what was clicked) over the voice connection,
  and the server keeps the traffic every second and all audio. When the exercise ends, the server renders one
  **.mp4 per controller** (their scope size up to 1600 px wide, 10 frames/s, H.264 + AAC): the traffic exactly as
  their seat showed it (every label as drawn, alert and transfer tags, RBLs where they put them, the route editor),
  their panels and windows (track info, flight plans, MTCD, OLDI, display menu), the
  mouse pointer with click rings, a caption log of their actions (assume, label entries, buttons, transmissions,
  calls), and the audio they heard and said (the frequency and their own telephone calls). An hour takes about
  10 minutes to render. Without a controller connected, a plain radar overview is rendered instead. The videos
  appear under *Recordings* on the home page of the instructor and the participants, to download; the instructor
  can archive (hide), re-render or delete them.

Audio is 16 kHz G.711 μ-law over the `/sessions/{id}/comms` WebSocket (≈16 kB/s per person talking). The
microphone needs HTTPS (or `localhost`).

**Radar page:**
- **Labels:** drag a label to move it (a leader line appears); double-click the label to put it back.
- **Map:** drag the background to pan, use the wheel to zoom, and double-click to refit the view.
- **What's drawn:** sector lines with frequencies, airways (solid), RAD routings (dashed, toggled with the RAD box),
  and transfer points (filled triangles).
- **Traffic:** transferred aircraft stay grey for a few minutes. There are no range rings.
- **RBL tool:** right-drag between two aircraft or points, shift-click two, or click **RBL** then two, to draw a range and bearing line.
  - Its label shows the bearing, the current distance and ΔFL, then the CPA distance and the time to CPA.
  - Both aircraft's projected positions at CPA are drawn as circles, joined to the aircraft by dashed lines.
  - The CPA comes from straight-line extrapolation of the present track and ground speed.
  - The line turns red when the CPA is below the horizontal minimum (5 or 7 NM) and the vertical gap is below the applicable minimum.
  - Drag a label to move it; right-click it to remove that line; Esc cancels; **✕ RBL** removes all.
- **Alarms** (top of the scope): a flashing red **LOSS OF SEPARATION** banner per pair below the minimum (distance,
  vertical, and the minimum that applies), and an amber **STCA** banner per pair predicted to lose separation within
  3 minutes. Click a banner to select the aircraft. A sound for each new alarm can be switched on (off by default).
  Aircraft in conflict get a halo of half the minimum (two halos touching = minimum separation), and the HUD shows
  the minimum in force.
- **Top of descent (T) and LoA latest-descent (L) markers** are drawn only for the selected aircraft.
- **Recording and replay:** every session records the traffic (one frame every 5 sim seconds) from the start.
  - The timeline strip under the scope shows the recorded part and event markers in four rows: losses of
    separation / infringements / weather penetrations, pilot requests and RVSM reports, instructions, and others
    (area activations, fix crossings). Hover a marker to read it.
  - Click or drag the timeline, or use the wheel over it, to scroll through the recording. The scope then shows the
    traffic at that moment (interpolated, with trails), the losses and requests open then, and the weather of that
    time. **▶ Replay** plays from the cursor at ×1–×60; **Live** goes back to the live picture.
  - **Export** downloads the whole recording (with the scenario, airspace and metrics) as one JSON file; **Load**
    opens such a file later for an offline replay, without a session (traffic and events only; the weather needs
    the live session).

- **Creating a session** shows a progress panel over the scope (elapsed time, and a progress bar once the scenario
  has been opened before on this browser) until the first traffic picture arrives; a dense scenario takes a few
  seconds to build.

**Scenario designer** (`/designer`, or the link next to *New session* on the radar): build scenarios visually.
- **Timeline:** wheel to scroll the time cursor, Ctrl+wheel to zoom, Shift+wheel to scroll the lanes, ▶ to play.
  One lane for the weather, one per area, one per aircraft (its bar runs from the entry to the exit; grey before
  entry). Markers: ◆ the aircraft's timing point, ● pilot requests (L/D/S), ■ RVSM events, red underline = planned
  conflict, white = loss in the preview run. **Drag a bar** to move a flight in time, **drag a marker** to re-time a
  request, an event, a cell or an area activation.
- **RBL (↔, key B):** click two aircraft. The label gives bearing, range and ΔFL at the cursor time, and the closest
  point of approach **along their trajectories** (filed routes, or the preview run when shown), with both positions
  at CPA. It follows the aircraft as you scroll the timeline, so you can drag one along its route until the CPA is
  where you want the conflict. Right-click a label to remove it.
- **Weather, all combined:** any number of squall lines (≋), convection areas (▦: isolated, scattered or embedded
  cells), single cells (⛈) and stratiform rain areas (≈), on top of the wind and jet. Each tool *adds* to the weather;
  the Scenario tab lists everything with its intensity (select or × to remove), and *new storms* sets the intensity of
  the next one drawn. In the scenario file `weather.convection` may be one pattern or a list of patterns.
- **Aircraft anywhere:** with ✈, *click an empty spot* (inside or outside the CTA) to put an aircraft there at the
  cursor time, then click the waypoints it flies to and press Enter (or double-click). In Select mode such an
  aircraft is dragged freely; Shift-drag frees any other one (normally dragged along its route). In the scenario
  file this is `"start": {"point": [x, y], "time": s}` (the route then needs only one waypoint). An aircraft that
  starts inside the sector after T+0 simply appears there (e.g. a departure from an aerodrome under the CTA). Aircraft defined by a route and a time at a point
  still appear 30 NM before their entry point (`pre_entry_nm` per aircraft changes that).
- **Map tools:** ✈ *Aircraft*: click waypoints (or pick a standard / RAD / airway route), then click on the route
  where the aircraft must be **at the cursor time**. ⛈ *Storm cell* at the cursor time; ≋ *Squall line* (drag along
  it); ▦ *Convection area* (isolated/scattered/embedded cells in a rectangle); ⬠/◯ restricted, danger or prohibited
  *areas* with an activation schedule. In *Select* mode, dragging an aircraft along its route re-times it so that it
  is there at the cursor time; cells and areas can be dragged too. Keys: V A C L R P O, Delete, Ctrl+Z / Ctrl+Y,
  Space, ←/→.
- **Selected tab:** type, level, route, dep/dest, Mach, RVSM, formation; requests (level, direct, speed, weather
  deviation: around the weather, an offset from track or a fixed heading) and
  events (unable RVSM, RVSM restored) are added at the cursor time.
- **Check & preview:** after each edit the server rebuilds the scenario and reports errors, warnings
  (semicircular rule, one-way airways, entry-only / exit-only points, non-RVSM in RVSM airspace, max FL) and the
  **planned conflicts** (filed routes and levels, wind included). *Run passive* / *Run naive approve* fly it in fast
  time; the map and timeline can then show the real trajectories and losses. *from previews* turns the results
  into the scenario's `expected` block.
- **Save** writes `scenarios/<name>.json`; *Save & open in radar* also starts an exercise (paused) and opens it.

---

## Aircraft

| Part | Model |
|---|---|
| Atmosphere | ISA with a temperature deviation. Flight levels are **pressure altitudes**. Exact CAS↔TAS↔Mach conversions (ICAO / BADA 3 formulas) |
| Type performance | From **[OpenAP](https://github.com/junzis/openap)** (TU Delft, LGPL-3.0) for 36 types: max climb and idle thrust, clean drag polar with compressibility (wave) drag, fuel flow vs thrust, MTOW/OEW, ceiling, VMO/MMO, wing geometry, real-world cruise-Mach statistics. A **per-type calibration** sits on top of it (see below) |
| Vertical / speed | Point-mass total-energy model, (T − D)·V = m·g·ḣ + m·V·V̇, with the BADA energy-share factor for constant-Mach / constant-CAS climbs and descents. Climbs use max climb thrust, descents idle. Mass decreases with fuel burn |
| Temperature | Drag uses the actual air density at the pressure altitude. Engines are flat-rated: climb thrust falls 0.8 %/°C above ISA+10. Hot days therefore lower the maximum FL realistically: none up to ISA+10, then about 1,000–3,000 ft by ISA+20 |
| Speed | Each aircraft flies a **Mach / IAS pair** and converts from Mach to IAS at the **conversion altitude**, where both give the same TAS (M.78 / 300 kt → about FL293; M.76 / 280 kt → about FL311). ATC can replace either member, alone or as part of a level clearance: *"descend FL250, maintain Mach .76, on conversion 280 knots"*. Restrictions already in force carry into later climbs and descents, and the conversion altitude moves with them. *"Maintain 280 knots"* (IAS now) applies at every level. Below FL100, 250 kt applies unless ATC has given a speed. VMO/MMO protection is always active |
| Pilot checks | A climb is refused below 300 ft/min residual climb at the new level and present weight, or without a 1.3 g buffet margin, or above MMO or the ceiling. Speeds are checked against the type range, buffet, VMO and MMO. Rates are checked against available climb or idle-plus-speedbrake descent. **Headings or directs into weather are refused** (*"unable heading 160, weather on that heading"*) |
| Lateral | 25° bank limit, 5°/s roll rate. FMS-style LNAV: track-to-fix legs, cross-track correction, wind correction, fly-by turn anticipation |
| Pilot delay | Instructions execute after `pilot_delay_s` (default 4 s) |

**Civil types (36):** A19N A20N A21N A318 A319 A320 A321 A332 A333 A343 A359 A388 B37M B38M B39M B734 B737 B738 B739 B744 B748 B752 B763 B772 B773 B77W B788 B789 C550 CRJ9 E145 E170 E190 E195 E75L GLF6.

**Military types (12, parametric, `atcsim/data/military_types.json`):** F16 F18 EUFI RFAL MIR2 MRF1 F5 AJET (fighters and
trainers, military power, transit drag with tanks), C130 A400 (turboprops: shaft power lapse, thrust = ηP/V,
brake-specific fuel), K35R C17. They are not in OpenAP: each uses a parabolic polar from its aspect ratio and a
transit CD0, the same wave-drag formulation as the civil types, a generic engine lapse, and the same calibration
(max climb thrust scaled so the max FL at mid weight matches a reference). Fighters, trainers and the C-130H are not
RVSM approved by default; the A400M, KC-135R and C-17 are.

### Type calibration (`atcsim/data/type_calibration.json`)

OpenAP's raw drag polars are inconsistent across types. A B777-200 comes out at L/D max 14.9 and an A350 at 16.3, against about 19–20 in service, so those aircraft could hardly hold cruise. Each type therefore gets two factors:
- **Drag factor:** scales CD0 and k together so L/D max matches a reference value.
- **Thrust factor:** scales max climb thrust so the max FL at mid weight (ISA, 300 ft/min residual) matches a reference value.

Optional entries:
- `cruise_mach`: overrides OpenAP's cruise Mach (the C550's data has M.77 against an MMO of .70).
- `cas_schedule`: sets the climb/descent IAS.
- `fuel_factor`: corrects the fuel model, used only for the A320neo family, whose OpenAP fuel model reads about 25 % low.

**Fuel flow is not calibrated**, so cruise fuel burn is an independent check. After calibration all 36 types burn within their typical range, for example A359 6.2 t/h, A388 11.8 t/h, B788 4.8 t/h, A320 2.7 t/h.

The reference values are approximate typical figures, not manufacturer data. Edit the file to change them; `GET /types` shows the raw and calibrated values for every type.

---

## Weather (`atcsim/weather.py`)

Everything is a deterministic function of (x, y, FL, time) generated from a seed. Every client, and the look-ahead prediction, therefore sees the same atmosphere, and runs are reproducible.

| Field | Model |
|---|---|
| Temperature | Surface temperature, lapse rate (steeper for unstable air masses), tropopause, stratosphere. It gives the freezing level and the ISA deviation used for aircraft performance |
| Wind (GND–FL800) | A background profile by level, plus a **jet stream**. The jet is set by core FL, axis, half-width and separate shear depths below and above the core; it can have a streak that travels along the axis and meanders. Smooth random small-scale wind (a few kt, 40 NM scale) drifts with the flow |
| Convection | Thunderstorm cells with a life cycle: developing → mature → dissipating, over 45–90 min. Tops rise and the core strengthens as they develop, then they decay with spreading anvils. Cells move at 75 % of the FL050–FL300 mean wind, 25° to the right. They tilt with the wind shear and carry an anvil blown downwind from the top. Each cell has its own texture |
| Reflectivity | Realistic vertical profile: core maximum below or near the freezing level, falling to 18 dBZ at the echo top, overshooting tops. Stratiform rain has a **bright band** at the freezing level. Cells combine in linear Z, anvils by maximum |
| Regimes | `isolated`, `scattered` (multicell clusters with daughter cells), `squall_line` (line with gaps, regenerating cells, trailing stratiform), `embedded` (cells hidden in a rain shield), plus explicit cells and stratiform areas for designed traps. Intensity is `moderate`, `strong` or `severe` |
| Turbulence (EDR) | Clear-air turbulence from vertical wind shear (about 15 kt per 1000 ft gives moderate), in patches. Convective turbulence in and above cells. 0.1 is light, 0.3 moderate, 0.5 severe |

**Radar products:** reflectivity at any FL, composite reflectivity, echo tops, EDR, temperature, wind vectors, vertical profiles, and hazard polygons for agents.

### Pilot behaviour (`atcsim/pilot.py`)

- **Onboard radar:** every 10 s each pilot scans 80 NM ahead along the path they will actually fly: the route, an assigned heading, or a deviation in progress. The scan uses the altitude the aircraft will have reached at each point.
- **What a pilot avoids:** reflectivity of 30 dBZ or more at their level. With the radar tilted down, they also avoid any storm whose top isn't at least 2,000 ft below them and whose core is 40 dBZ or more. They keep 6 NM from hazards and 10 NM from severe ones (40/50 dBZ).
- **Deviation requests:** when the path is blocked within 60 NM, the pilot works out the shortest dogleg that clears with margin, preferring the upwind side. They request *"heading 170 for 65 miles due weather, then direct S4"*.
  - If going over the top is shorter (at least 3,000 ft above the tops) and the aircraft can reach that level, they request the climb instead (*"requesting FL380, to top weather"*).
- **If the controller doesn't answer:** the pilot repeats the request after about 75 s.
- **If nothing is approved by 15 NM from the weather:** the pilot **deviates anyway** (PIC authority). This is logged as `deviation_without_clearance` and counted in the metrics. An "unable" doesn't stop it either.
- **During and after a deviation:** if the weather moves onto the deviation path, the pilot asks for a further deviation. At the end they call *"clear of weather, proceeding direct S4"* and rejoin the route.
- **Turbulence:** after about 45 s in continuous moderate or severe turbulence, the pilot asks for the nearest smooth level it can reach. If there is none, it files a report.
- **Planner limitation:** the pilots' planner doesn't know about restricted or prohibited areas. Protecting them is the controller's job.

The look-ahead prediction (`/separation`, `/probe`) flies this pilot model as well. A pending request with no answer is predicted as an unauthorised deviation at 15 NM, which is exactly what will happen if nobody answers.

---

## Arrivals and sequencing (`atcsim/arrivals.py`)

**E1 and S4 are sequencing fixes.** Each leads to a destination aerodrome outside the sector:

| Fix | Aerodrome (fictional) | Distance beyond fix | Runways | Letter of agreement |
|---|---|---|---|---|
| E1 | **EFLD** Eastfield | ~40 NM east | 09 / 27 | level **FL250** at E1, **10 NM in trail** |
| S4 | **SPRT** Southport | ~90 NM south | 18 / 36 | level **FL250** at S4, **10 NM in trail** |

- **Who is an arrival:** every flight exiting at a sequencing fix, unless the scenario marks it `"arrival": false` (an overflight continuing elsewhere).
- **Below the LoA level:** the LoA level (FL250) is what you normally give. To avoid an arrival levelling off there
  too long you can coordinate a lower level with the next sector and clear it down to FL100: the clearance is
  accepted with a reminder (*"below the LoA level FL250 at E1: coordinate with the next sector"*), and the fix
  crossing counts as compliant (`coordinated_fl` in the crossing log). Crossing above the LoA level is still a
  violation. Any aircraft can be cleared down to FL100: below the sector floor the clearance comes with a reminder to
  coordinate. Descending to destination (below FL250, or to one of the `descent_destinations`) the semicircular rule
  is not applied, neither to clearances nor to scripted requests.
- **Runway in use:** `auto` (into the surface wind) or set per scenario, with optional scheduled changes. A supervisor can also change it through the API.

**Top of descent.** Each arrival's pilot keeps recomputing:
- **distance to go** = remaining route to the fix + approach path from the fix to the final approach fix (FAF, 10 NM final, aerodrome elevation + 3000 ft);
- **distance needed** = an idle descent with the aircraft's own performance and weight, flying its Mach / IAS pair (including any restriction you gave, and converting at the right altitude), with the wind along the way, the deceleration to 250 kt at FL100 and to approach speed before the FAF.

TOD is where the two are equal. A B738 from FL360 needs about 118 NM.

**The runway changes the approach path:**
- **Straight-in** (the fix is on the approach side): fix → intermediate fix → FAF → threshold.
- **Reversal** (the aircraft arrives from the far side of the runway in use): fix → overhead → outbound on the approach side → 180° turn → inbound → FAF → threshold. That's about 30 NM more, so the TOD comes much later.

For example, from FL360 to E1 a B738 asks for descent 59 NM before E1 with runway 09, but only 24 NM before with runway 27. At S4, the reversal runway pushes most pilots' TOD past the fix.

**Pilot behaviour:**
- **At TOD:** the pilot asks *"request descent"*. Any descent clearance answers it.
- **Without a clearance:** the aircraft stays level and gets high. The pilot repeats every 60 s, saying how far past its TOD it is.
- **Cleared late:** it descends at idle with speedbrakes.
- **Never below the LoA level:** pilots don't ask to go below it; the next sector does that.

**What the controller sees for each arrival:**
- `tod_in_nm`: the pilot's own top of descent.
- `loa_latest_descent_in_nm`: the latest point to start down and still be level FL250 at the fix.

When the runway in use makes the pilot's TOD later than the LoA point, the pilot won't ask in time, and you have to start the descent yourself.

**At the fix:**
- **Checked:** the level (FL250 ±300 ft), the spacing to the previous arrival (≥10 NM), and the optional speed.
- **Logged:** each crossing is a `fix_crossing` event and is scored in `metrics.sequencing`.

**Sequence status:** `/sequence` lists, for each fix, the inbound arrivals in ETA order, with ETA, spacing to the one ahead, TOD and the LoA descent point.

Everything is configurable per scenario:
```json
"aerodromes": [{"id": "EFLD", "x": 97, "y": 6, "elev_ft": 300, "runways": [{"id": "09", "heading": 90}, {"id": "27", "heading": 270}],
                "runway_in_use": "auto", "runway_schedule": [{"time": 900, "runway": "27"}]}],
"sequencing": [{"fix": "E1", "aerodrome": "EFLD", "spacing_nm": 10, "cross_at_fl": 250, "min_fl": 250, "speed_at_fix_kt": null}]
```

---

## Airspaces (`atcsim/airspace.py`)

A scenario picks its airspace with `"airspace"`: `training` (default) or `agadir`. With `agadir` it can also say
which sectors the agent controls, `"control": ["WS"]`; the default is all four, band-boxed.

### Training sector
A 120 × 120 NM square, FL250–FL460, coordinates in NM from the centre (x east, y north).
- **Waypoints:** N1–N4 and S1–S4 at x = ±15, ±45 on the north and south edges; W1/W2 and E1/E2 at y = ±20 on the west and east edges.
- **Routes:** 44 published routes. Every waypoint joins every waypoint on the opposite side, plus N3/N4 → E1/E2.

### Agadir airspace: the Casablanca FIR (`atcsim/data/airspaces/agadir_cta.json`)
The file holds the whole FIR GMMM: the CTA AGADIR (Agadir ACC, the default) and the CTA CASABLANCA (Casablanca
ACC). A scenario controls sectors of one of them (`"control"`); the other is drawn on the scope, dimmer, and is the
adjacent unit.
- **FIR outline** (ENR 2.1): 35°58'N / 35°50'N to 002°06'W, a straight line to the Mediterranean coast at the
  Moroccan-Algerian border, the border to 315000N 0024000W (approximate: the AIP only says "following the border"),
  then 312159N 0033642W, BAGBO, 300000N 0060825W, 284000N 0084000W, 27°40'N to 013°10'W, RUSIK, DEVLA, KORAL,
  300000N 0123000W, and the CTA AGADIR's western boundary with its 100 NM arc.
- **CTA AGADIR boundary:** 321500N 0143700W, 355800N 0120000W, 355800N 0072300W, 355000N 0072300W,
  355000N 0062703W, 331841N 0092623W, 330334N 0092726W, 325741N 0094344W, 300000N 0123000W, 313000N 0154500W, then
  the 100 NM arc anticlockwise around 330400N 0162100W. Edges along 35°58'N and 35°50'N follow the parallel.
  Simulated volume FL195–FL460 (class C), both CTAs.
- **Sectors:** Oceanic North 136.000, West North 128.800, West South 127.000, Oceanic South 124.500. The sector
  lines run through the FRA intermediate points OSDIV, NEVTU, ABTIR, NIDEB, IBLIV, XELER, ERMED and DOMOG and through
  ADILI on the northern boundary. Two junctions are read off the chart: J 324422N 0122612W and V 331137N 0113157W.
- **Points:** the 242 points of ENR 4.4 (43 of them in the CTA AGADIR, plus RUNAX), the en-route VOR/DMEs (OJD,
  FES, RBT, TTN, SLK, MAK, ESS, ERA, OZT, ALM, BRF, ARI, TNR, BML, and LAY, DKH, SMR south of the FIR). Points of the
  other control area are neighbouring points (route points before / after, direct-to targets), like the Lisboa or
  Canarias points. ADILI isn't in ENR 4.4, so its position is read off the chart. The upper chart was georeferenced
  on 17 ENR 4.4 points with a residual of about 0.5 NM.
- **CTA CASABLANCA sectors** (ENR 2.1, published lines): North 125.500 (backup 132.325), East 125.100 (132.100),
  Central 133.100 (132.500), South-South 126.700 (132.500). North and East share the 28 NM arc around VOR/DME RBT;
  the junction of North, East and Central is published as 330012N 0065607W (East) and 325957N 0065616W (Central),
  0.2 NM apart, and is taken as one point. Central and South-South meet the CTA AGADIR boundary at 323914N 0100100W;
  ROLRO and that point are kept on the boundary line.
- **Airways:** the upper ATS routes of AIP Maroc ENR 3.1 / 3.2, which are the reference: the points in the
  published order, one-way where the table gives cruising levels in one direction only. Through the CTA AGADIR:
  - UN866 BEXAL–KUBIL–OSDIV–KONBA
  - **UN873** BAROK–BENTU–ABTIR–MITLA–SAMAR, southbound only
  - **UN858** VASTO–SULAM–DIMSA–IBLIV–SUNID–AKUDA, northbound only
  - **UN857**/A857 KORNO–ABODA–IBALU–ERMED–TOVRA–VEDOD–TERTO, southbound only
  - **UL82** RUNAX–IBALU, southbound only
  - UR722 AMSEL–ABODA–MARJA–LAKAM–SAK–CSD–FOBAC–MABAP–MAK
  - UR724 OSLAD–IBALU–NISBA–MOKRI–ESS–DENOB–AGALI–ADM
  - **UH899** SAK–ODAXA–LAPNO–IBALU, westbound only
  - UA411 ORSUP–OJD–…–FES–…–RBT–LAKAM–SALCA–IBALU

  and in the CTA CASABLANCA: UA600, UB11, UG5, UG664, UG850, UH104, UH119, **UH300** (MOGBA–KEMEX–ESS, northbound
  only), UH400, UH500, UJ744, UL58, UL102, UM240, UM372, UM985, UM999, UN10, UN726, **UN869** (GALTO–…–RUSIK,
  southbound only), **UN871** (KORAL–…–ADUBI, northbound only), **UQ401** (OZT–RATVA–RUBAB–BML, northbound only),
  UR975, UR977, UT900, UV203, UW255, UW700, UW900, UZ801. A leg shared with a two-way route (SLK–PELAX on UB11 and
  UN869) may be flown both ways.
- **Areas:** the ENR 5.1 prohibited (GMP), restricted (GMR) and danger (GMD) areas, in every scenario: prohibited
  ones active, the others inactive until the scenario or the instructor activates them.

  One-way airways have arrows on the radar, and the scenario build rejects flights filed against them.
- **Standard routes:** RUNAX–IBALU–ERMED–TOVRA–VEDOD–TERTO (southbound) and DESUM–SULAM–UN858–AKUDA (northbound).
- **Entry and exit points** (FRA designators of ENR 4.4):
  - Entry only: KORNO, RUNAX, VASTO, DESUM. Every route through VASTO is northbound.
  - Exit only: TERTO, TIGGI, AKUDA.

  The scenario build rejects routes that start at an exit-only point or end at an entry-only one. A direct-to an
  entry-only point used as the exit comes back with a warning.
- **RAD direct routings** — one-way, in the order listed (drawn dashed with direction arrows on the radar).
  Flying one backwards (e.g. SAMAR–IBALU–RUNAX) is flagged by the scenario build and the designer, like a one-way
  airway. Exception: SUBAL–IBALU–SALCA and BEXAL–IBALU–SALCA are also flown westbound by the North America
  departures (no arrows on them):
  - RUNAX–IBALU–SAMAR
  - SUBAL–IBALU–SALCA
  - TAKAV–DOMOG
  - BEXAL–IBALU–SALCA
  - TAKAV–VEDOD–TERTO
  - VASTO–NIDEB–TIGGI
  - VASTO–BAROK
  - SAMAR–BEXAL
  - SAMAR–TIGGI
  - NIPRI–ROLRO
- **Neighbouring-sector points** (`external_points` in the JSON; small diamonds on the radar, `?` = provisional):
  Lisboa / Sevilla / Madrid (ELVAR, BARDI, AGADO, BALNO, MINTA, PESAS, PINEK, XERES, OBOLO, TOSDI, LPPT), Madeira
  on the Lisboa / Santa Maria boundary (MANOX, NAVIX, KOMUT, GUNTI), Casablanca and the approaches (ODAXA, CSD, SAK,
  ODALO, VALBA, ADM, EKTAL, AGALI), Canarias (GINAS, PEPOM, CANIS, ISORU, GURKA, FTV). Coordinates come from the Maroc, Lisboa and España ENR 4.4, the GCFV AD 2, OurAirports and the
  user (OBOLO, TOSDI). Every Agadir CTA point was checked against the Maroc ENR 4.4 (06 AUG 2026); ADILI is not in
  it and stays read off the chart.
- **Flight plans beyond the CTA** (`onward_routes` rules in the JSON, applied when a scenario is built unless the
  route already starts/ends outside or the aircraft has `"auto_route": false`). By transfer point and destination:
  - AKUDA: OBOLO–PESAS or OBOLO–MINTA (Spain south and east, Mediterranean), ELVAR–BARDI (northern Europe, Madrid,
    Barcelona); about half each with the traffic mix of the flows.
  - TIGGI / BEXAL to Europe: LPPT direct (Lisbon), AGADO (Porto), XERES or PINEK (UK, Ireland, Nordic), TOSDI,
    BALNO or PINEK (France, Benelux, Germany...), mostly BALNO and rarely PESAS / MINTA (Spain, Mediterranean).
  - North America: Casablanca departures ODAXA–SALCA–IBALU–SUBAL then MANOX or NAVIX, or –BEXAL then KOMUT or
    GUNTI; the arrivals the other way round (MANOX / NAVIX before SUBAL, KOMUT / GUNTI before BEXAL, ODAXA after
    SALCA to GMMN).
  - BAROK: ELVAR–BARDI or BALNO, both ways (before BAROK for southbound traffic).
  - MARJA to GMMX: CSD–ODALO with runway 10 in use, SAK–VALBA with runway 28. MARJA to GMAD: CSD then EKTAL or AGALI.
    DOMOG / NISBA to GMAD: ADM then AGALI or EKTAL.
  - TERTO: GINAS or PEPOM (GCRR), CANIS (GCLP), FTV (GCFV). SAMAR to GCLP: ISORU. KONBA: GURKA after (southbound)
    or before (northbound).
  The destination groups are ICAO prefixes in the JSON and can be edited. The choice among alternatives is
  reproducible per callsign. The simulation flies the controlled part; the rest is in the flight plan, drawn for
  the selected aircraft in the designer, and used to place aircraft before they enter.
- **Directs:** the controller can clear an aircraft direct to any point, including one of the next sector (`RAM101 D
  ELVAR`, or the *Direct to* buttons in the radar's aircraft panel: the points ahead and the flight plan after the
  exit). An aircraft on such a direct is transferred where its track crosses the boundary (exit reported as
  `DCT ELVAR`, listed under `rerouted_exits`), not counted as leaving the sector off route.
- **Arrivals:** GCRR (Lanzarote, rwy 03/21) via **TERTO**; GMMN (Casablanca Mohammed V, rwy 35R/17L) via **MARJA**
  and **SALCA**. The letter-of-agreement values, level FL250 and 10 NM in trail, are **placeholders** until the
  real ones are entered in the JSON file.
- **Projection:** spherical Lambert conformal conic (standard parallels 30.5°N and 35.5°N, origin 33°N 11.5°W),
  scale error under 0.3 %. Headings are relative to grid north, which is true north on 11.5°W and within about
  2° elsewhere. Magnetic variation (about 3°W) is not applied yet. `/airspace?name=agadir` returns every point with
  its lat/lon and AIP string.
- **Transfer points:** named points on the boundary of the controlled area are entry and exit points. When a route
  crosses the boundary between two named points, the simulator adds a coordination point (COP) there, named `C`
  plus two letters of each neighbour, e.g. `CTAVE` on TAKAV–VEDOD. A route may start before the controlled area
  and continue after it; the part outside is flown by the previous or next sector.

Coordination points: in a scenario that controls only some sectors, a route that crosses the boundary of the
controlled area between two published points gets a generated transfer point there, named C + two letters of each
neighbour (e.g. CIBSA between IBLIV and SAMAR). They are not AIP points: the scope draws them as a small tick
without a name, labels show the next published point, and they are not offered as directs.

### Common to both
- **Traffic before entry:** aircraft appear `pre_entry_nm` (default 30 NM) before their entry waypoint, on the extension of their first leg. They are still with the previous sector (shown dimmed) and refuse your instructions. They **call you** `checkin_nm` (default 10 NM) before the boundary (*"RAM101 FL350, checking in"*, a pending request
of kind `checkin`), and **you assume them** on that first contact: instruction `{"type": "assume"}`, shorthand
`RAM101 AS`, the *Assume* button, a double-click on the blinking `CALL` aircraft, or a reply to the check-in. Until
then they refuse other instructions ("assume it first") and make no requests of their own; separation already counts
from their first call. Aircraft already in the sector at T+0 start assumed. Metrics: `assumption` (delay between
first call and assumption, late ones over 30 s, aircraft never assumed). `"auto_assume": true` in a scenario (or
`auto_assume` when creating a session) assumes them automatically. The evaluation policies (passive, naive approve,
scripted) assume on first contact. Losses, area infringements and weather penetration only count when at least one aircraft involved is on your frequency. A weather deviation needed before check-in is flown as if coordinated by the previous sector.
- **After exit:** a transferred aircraft stays painted (grey) for `post_exit_display_s` (default 240 s), flying on
  along its last track. It is listed in `state.transferred`, not in `state.aircraft`.
- **Airspace areas:** danger, restricted and prohibited areas (polygon or circle, FL limits) activate on a published schedule or manually. Points can be given as `[x, y]`, `{"lat", "lon"}` or an AIP string.

### Levels, RVSM and formations
- **Semicircular rule:** the direction of flight is the bearing from the entry point to the exit point.
  - Southbound (090–269): odd levels (… 290, 310 … 410, 450).
  - Northbound (270–089): even levels (… 280, 300 … 400, 430).
  - Filed levels and scripted requests must comply; the scenario build checks this.
  - A clearance to a wrong-direction level is accepted, since it can be needed tactically, but it comes back with a
    `warnings` entry and is counted.
  - An aircraft transferred level at a wrong-direction level is scored (`semicircular.wrong_direction_exits`).
    Arrivals are exempt: their LoA level applies.
  - Pilots only ask for correct-direction levels.
- **RVSM airspace FL290–FL410 inclusive:**
  - Each aircraft carries `rvsm.approved` and `rvsm.status`: `approved`, `non_approved` or `unable`.
  - Its equipment string (ICAO item 10) contains `W` when it is RVSM approved.
  - The flight plan shows it, and the radar label shows `NRVSM` or `RVSM✗`.
- **Horizontal radar separation:** **5 NM** in the training sector, **7 NM** in the Agadir CTA
  (`separation_minima` in the airspace JSON). A scenario can set its own with `"separation": {"horizontal_nm": 6}`
  (designer: *separation* field). Loss-of-separation detection, the look-ahead, `/probe`, the designer's planned
  conflicts, the STCA alarms and the RBL colours all use it; it is in the briefing (`separation_minima`) and in every
  state (`separation_minimum_nm`).
- **Vertical separation minima per pair** (also used by the prediction):

  | Case | Minimum |
  |---|---|
  | Below FL290 | 1000 ft |
  | FL290–FL410, both RVSM-approved | 1000 ft |
  | FL290–FL410, either non-RVSM (not approved or unable) | 2000 ft |
  | Either aircraft above FL410 | 2000 ft |
  | A military formation, against anyone, at any level | 2000 ft, or the horizontal minimum + formation extent (default 1 NM) |
- **Non-approved civil aircraft** inside FL290–FL410 while on your frequency count as an **RVSM airspace violation**
  (`rvsm.violations`). State aircraft may enter RVSM airspace, with 2000 ft from everyone.
- **Loss of RVSM capability:** a scenario event `{"time", "callsign", "type": "rvsm_unable", "reason"}` makes the
  aircraft unable RVSM. The pilot reports *"RYR8KQ unable RVSM due autopilot failure"* as a request of type
  `report`, answered with decision `roger` (shorthand `ROG`). `rvsm_restored` gives the capability back.
- **Formations:** `"formation": {"size": 4, "types": "4xF16", "extent_nm": 1}`. Only the leader is shown, labelled
  `VIPER11 x4 … F16 MIL NRVSM`.

### Flight plan (`atcsim/flightplan.py`)
Every aircraft has a complete ICAO flight plan, correlated with its radar track by the SSR code (unique in the
session) and the Mode S address:
- item 7 callsign and SSR code; 8 flight rules and type of flight (S scheduled, N charter, G general aviation,
  M military); 9 number (formations), type, wake; 10 equipment and surveillance;
- 13 departure and EOBT; 15 speed, requested level and the ICAO route (e.g. `KORNO UN857 TERTO`, `BEXAL DCT IBALU
  DCT SALCA`) over the sector and the neighbouring ones; 16 destination, total EET, alternate;
- 18 STS, PBN, DOF, REG, EET at the FIR boundaries, SEL, CODE (Mode S address), OPR, PER, TYP, RMK;
  19 endurance, persons on board.
EOBT and EETs are worked out so that the flight is at its entry point at the planned time (great-circle distances
from the aerodromes, present speed). The UTC clock of the session starts at the scenario's `start_utc` (12:00 by
default). Each flight also knows the unit it comes from and the one it goes to (the Agadir sector not controlled,
or Lisboa, Madeira, Sevilla, Canarias, Casablanca, by the points of its route), shown in labels, flight plans and
the coordination. The surveillance part of item 10 decides what the radar gets: `L`/`H` = Mode S enhanced
surveillance (downlinked parameters), `S`/`E`/`P`/`I`/`X` = elementary (address only), `A`/`C` = Mode A/C only.

Scenario keys: `dep`, `dest`, `rvsm`, `military`, `formation`, `equipment`, `remarks`, and `"fpl": {"ssr", "reg",
"code", "eobt", "altn", "equipment", "opr", "pob", "rmk", "sts", "pbn"}`. Whatever is missing is generated
(deterministic from the callsign: operator, registration and Mode S block by country, type of flight, equipment by
type and operator). Every scenario of the library carries its flight plans (`python -m tools.fill_flightplans`
writes them; `tools/build_scenarios.py` does it after generating); the designer edits them in the *Flight plan* part
of the selected aircraft and the scenario's start time in the Scenario tab. Arrivals take their destination from the
sequencing fix.

---

## API

| Method | Path | What |
|---|---|---|
| GET | `/airspace?name=training\|agadir&control=WS`, `/airspaces`, `/types` | Geometry (boundary, sectors, points with lat/lon, airways, RAD routes, transfer points), minima; aircraft types with calibration |
| GET | `/scenarios`, `/scenarios/{name}?include_notes=true` | Scenario list / briefing (`include_notes` is for evaluators: it shows the trap) |
| POST | `/sessions` | `{"scenario", "mode": "realtime"\|"stepped", "time_factor", "autostart"}` → briefing |
| GET / POST | `/sessions/{id}` · `/sessions/{id}/connect` | Briefing: sector, **weather summary**, **area status**, aircraft types, state |
| WS | `/sessions/{id}/stream` | Live push of state and events every sim second; the separation look-ahead is computed in parallel (on a copy of the world, never holding up the clock) and attached to the next tick when ready; send instructions, commands or control over it |
| POST | `/sessions/{id}/control` | `start\|pause\|resume\|set_speed\|set_mode\|step\|restart` |
| GET | `/sessions/{id}/state` · `/positions` · `/aircraft/{cs}` | Picture. Aircraft detail includes performance at present weight and the weather phase |
| GET | `/sessions/{id}/aircraft/{cs}/performance` | The type's performance and, at the present weight, a table by level (schedule speeds, climb, idle descent, level available) |
| GET | `/sessions/{id}/separation` | Current pairs and **predicted** conflicts, area infringements and **weather penetrations** |
| POST | `/sessions/{id}/probe` | What-if for candidate instructions (including replies to requests); changes nothing |
| GET / POST | `/sessions/{id}/mtcd` | Medium-term conflicts between the system trajectories (20 min) / probe one aircraft: `{"callsign", "cfl"\|"heading"\|"direct"}` or `{"callsign", "levels": [...]}` |
| GET | `/sessions/{id}/oldi?since=&callsign=` | OLDI messages (ABI, ACT, REV, PHONE; sent / received, LAM) and each aircraft's cleared, exit and coordinated levels |
| GET | `/sessions/{id}/flightplans` · `/flightplans/{cs}` | Every flight plan / one: FPL message, decoded items, estimates over the points ahead, coordination, correlation |
| POST | `/sessions/{id}/instructions` · `/command` | Issue instructions / controller shorthand |
| GET | `/sessions/{id}/requests` · `/events` · `/metrics` · `/instructions` | Logs and scores |
| GET / PATCH | `/sessions/{id}/areas[/{id}]` | Area status / manual activation |
| GET | `/sessions/{id}/sequence` | Each sequencing fix: aerodrome, runway in use, approach type, LoA; inbound arrivals in ETA order with spacing, TOD and LoA descent point; crossings so far |
| GET / PATCH | `/sessions/{id}/aerodromes[/{ad}]` | Aerodromes, runways and approach paths / change the runway in use `{"runway": "27"}` |
| GET | `/sessions/{id}/weather` | Temperature, freezing level, tropopause, wind profile, jet, storm motion, **every cell** (position, motion, echo top, max dBZ, 30/45 dBZ radii, life-cycle stage), squall-line geometry and gaps |
| GET | `/sessions/{id}/weather/radar?product=cappi\|composite\|echo_tops\|edr\|temperature&fl=350&res_nm=2&bbox=…&encoding=b64\|json` | Gridded products (uint8 base64 or JSON) |
| GET | `/sessions/{id}/weather/hazards?fl=350&threshold_dbz=30` | Polygons of the weather to avoid at a level, plus the pilots' margins |
| GET | `/sessions/{id}/weather/column?x=&y=` | Profile from the ground to FL800: dBZ, wind, temperature, EDR |
| GET | `/sessions/{id}/weather/wind?fl=350&res_nm=15` | Wind vectors |
| GET | `/sessions/{id}/recording?since=&every=` | Recorded frames (compact rows, see `frame_fields`), aircraft metadata and timeline events |
| GET | `/sessions/{id}/recording/export` | The whole recording as a downloadable file (`kind: atcsim-recording`) |
| GET | `/scenarios/{name}/raw` | The scenario file itself |
| POST | `/designer/compile` · `/designer/preview` · `/designer/weather` · `/designer/save` | Designer back end: `{"scenario": {...}}` → errors, warnings, planned trajectories and conflicts / fast-time run (`policy`) / weather image at `at_s` / write the file (`name`, `overwrite`) |

The weather endpoints take `at_s=` to look at another moment (replay).

### Instructions (closed set)

```json
{"callsign": "RAM101", "type": "altitude",  "fl": 360, "rate_fpm": 1500}
{"callsign": "RAM101", "type": "heading",   "heading": 250, "turn": "left"}
{"callsign": "RAM101", "type": "direct",    "waypoint": "S3"}
{"callsign": "RAM101", "type": "altitude",  "fl": 250, "mach": 0.76, "conversion_kt": 280}   // descend with a speed restriction
{"callsign": "RAM101", "type": "speed",     "mach": 0.76, "conversion_kt": 280}  // Mach above conversion, then 280 kt
{"callsign": "RAM101", "type": "speed",     "ias_kt": 280}                       // 280 kt now, at every level
{"callsign": "RAM101", "type": "speed",     "resume_normal": true}
{"callsign": "RAM101", "type": "resume"}
{"callsign": "RAM101", "type": "deviation", "side": "left", "max_nm": 20, "then_direct": "S3"}
{"callsign": "RAM101", "type": "reply",     "request_id": "R001", "decision": "approved"}
{"callsign": "RYR8KQ", "type": "reply",     "request_id": "R004", "decision": "roger"}     // acknowledge a report
```

- **`deviation`:** the controller-initiated form, *"deviation left of track approved, up to 20 miles"*. The pilot plans its own way around on that side. It is refused if no deviation within the limit clears the weather.
- **Answering a weather deviation request:** `reply` approves the exact heading/distance/rejoin the pilot asked for. A heading, direct, deviation or level clearance instead counts as `amended`.

Shorthand (`/command` and the radar input):
- **Levels:** `FL360`, with `V1500` after it to set the rate.
- **Headings:** `H270`, or `L270` / `R270` to force the turn direction.
- **Routing:** `D S3`, `RES`.
- **Speed:** `M76` (Mach), `K280` (on conversion 280 kt), `S280` (280 kt now), `SN` (resume normal speed). After a level in the same command they become part of that clearance: `RAM101 FL250 M76 K280` = *"descend FL250, maintain Mach .76, on conversion 280 knots"*.
- **Weather deviation:** `DEV`, `DEVL`, `DEVR20`.
- **Replies:** `APP`, `UNA` or `ROG` (reports), optionally followed by a request id.

### Requests

- **Scripted requests** come from the scenario at set times.
- **Pilot-generated requests** (`origin: "pilot"`) are weather deviations, climbs to top weather, turbulence level changes, descent requests at the top of descent, and reports (`type: "report"`, e.g. unable RVSM).

Agents never see future requests.

### Metrics

- **Separation:** losses and seconds.
- **Airspace:** infringements.
- **Weather:** seconds inside ≥30/40/50 dBZ, penetration events, deviations without clearance, seconds in moderate or severe turbulence.
- **Requests:** counts by outcome and response time.
- **Sequencing:** spacing, level and speed violations at the fixes, every crossing.
- **RVSM:** non-approved aircraft inside RVSM airspace (events, seconds).
- **Semicircular:** wrong-direction exits and wrong-direction clearances.
- **Traffic:** instructions, extra track miles (inside the sector), fuel, and exits (planned, rerouted or off-route).

---

## Scenario format

The complete reference, with every field, the rules, examples, a prompt template for writing scenarios with a
language model and generated tables of every point, route and aircraft type, is the in-app page
*Help → Scenario JSON reference* (`atcsim/docs/scenario-json.md`, public at `/help/scenario-json`). The JSON Schema
is served at `/api/help/scenario.schema.json` and the reference data at `/api/help/reference.json`. In short:

```json
{
  "name": "...", "title": "...", "description": "shown to agents", "design_notes": "the trap (hidden)",
  "author": "who designed it", "complexity": 3, "density": 4,
  "duration_s": 1500, "pilot_delay_s": 4,
  "weather": {
    "seed": 31,
    "temperature": {"surface_c": 24, "tropopause_fl": 390, "lapse_c_per_kft": 2.1},
    "wind": {"profile": [{"fl": 0, "dir": 200, "kt": 10}, {"fl": 350, "dir": 250, "kt": 55}, {"fl": 800, "dir": 90, "kt": 20}],
             "jet": {"core_fl": 340, "dir": 268, "kt": 65, "axis_point": [0, 30], "half_width_nm": 90,
                     "depth_below_ft": 11000, "depth_above_ft": 8000},
             "noise_kt": 5},
    "convection": {"regime": "squall_line", "intensity": "severe",
                   "line": {"center": [-30, 0], "length_nm": 240, "gap_list": [[10, 22]]}},
    "cells": [{"id": "CB1", "x": -5, "y": 0, "at_time": 300, "top_fl": 460, "dbz": 59, "radius_nm": 5,
               "lifetime_s": 6000, "age_s": 1800, "motion": {"toward": 85, "kt": 20}}],
    "pilot": {"hazard_dbz": 30, "margin_nm": 6, "severe_dbz": 40, "severe_margin_nm": 10,
              "request_range_nm": 60, "self_deviation_range_nm": 15}
  },
  "areas": [ ... ],
  "aircraft": [{"callsign": "RAM101", "type": "B738", "route": ["N2", "S3"], "fl": 340, "mach": 0.78, "load": 0.5,
                "reach": {"point": [0, 0], "time": 600}}],
  "requests": [{"time": 330, "callsign": "RAM101", "type": "altitude", "value": 360, "reason": "..."}]
}
```

Request types: `altitude` (FL), `direct` (waypoint), `speed` (Mach < 2 or IAS kt), `descent` and `weather_deviation`.
`descent` is the pilot's *"request descent"* (no level): any descent clearance answers it; an optional `value` is
the level used if the request is simply approved (default: the LoA level, or about 4000 ft lower). Typical for
traffic to GMMN, GMMI, GMMX, GCRR and GCFV (`descent_destinations` in the airspace JSON). A scripted request is
made only once you have assumed the aircraft (if it is due earlier, the pilot asks right after you assume him). A
scripted weather deviation is turned into a concrete pilot request (heading, distance, then direct) at the moment it
is made, from the aircraft's position and track then:
- `{"side": "either"|"left"|"right"}`: the pilot's own plan around the real weather ahead (as the pilot model does
  by itself). With no weather ahead it falls back to 30° off track on that side.
- `{"side": "left", "offset_deg": 30, "distance_nm": 20}`: a fixed offset from the present track.
- `{"heading": 250, "distance_nm": 15}`: a fixed heading.
- Optional `"then_direct": "IBALU"` (otherwise a downstream route point beyond the dogleg).

The pilot waits for the answer even if no weather shows on the radar, flies the plan if approved, and reacts
normally (repeats, deviates on his own when close) if real weather is there. Approving, refusing or amending it
works as for the pilots' own weather requests.


- **Aircraft timing:** one of `entry_time`, `reach: {waypoint, time}` or `reach: {point, time}`. Entry times are worked out from the ground speed in the 3D wind. The `reach` waypoint can be anywhere on the filed route, including before or after the controlled part. A time before 0 puts the aircraft further along its route at the start.
- **Real airspace:** `"airspace": "agadir"`, `"control": ["WS"]`, `"post_exit_display_s": 240`, `"events": [{"time": 400, "callsign": "RYR8KQ", "type": "rvsm_unable", "reason": "autopilot failure"}]`.
- **Flight plan / category:** `"dep": "EGCC", "dest": "GCRR", "rvsm": false, "equipment": "SDFGRY/S", "military": true, "formation": {"size": 4}, "remarks": "STS/STATE"`.
- **Mass:** `load` (0–1 between OEW and MTOW) or `mass_kg`.

---

### Author, complexity, traffic density

`author` (by default the person who saves it in the designer), `complexity` and `density` (1 very low … 5 very high) are
set in the designer. When a rating is not set, it is estimated from the content and shown with a `~`: the density
from the flights in the controlled sectors during the first hour against the declared capacity (`traffic_load`,
written on every save; aircraft per hour in the training airspace, which has no declared capacity), the complexity
from the density, requests, areas, events, weather and planted conflicts. They appear in the instructors'
and admins' scenario list, and on every exercise: each exercise keeps the values the scenario had when it was run, shown
in the controller's exercise list, statistics (sessions table) and evaluations.

## Scenario library

`tools/build_scenarios.py` generates every scenario and verifies it in fast time with the full physics, weather and pilot model. It runs two reference policies:
- `passive`: never answers.
- `naive_approve`: approves everything instantly.

For the weather traps it also runs a **reference solution**, which must give zero losses, zero weather penetration and zero infringements.

The reference solutions must also give zero sequencing violations, zero RVSM airspace violations and zero
wrong-direction exits. In s06, s12 and s13 the trap aircraft that leave via E1 are marked as overflights, because
those traps were designed before arrivals existed.

All training scenarios were re-levelled for the semicircular rule. Traps that relied on opposite-direction traffic
at the same level (impossible under the rule) were rebuilt with same-direction or northbound/southbound pairs 1000 ft
apart. `meet()` in the builder places the second aircraft where the first one actually flies after the trap is
sprung. The sequencing reference solution for s16 can be regenerated with `tools/seq_solver.py`, a greedy search over
speed restrictions and doglegs run in the real simulator. They prove each scenario is solvable; they are not
optimal plans.

The build fails in any of these cases:
- a trap doesn't trigger;
- a reference solution fails;
- a pilot rejects a clearance it asked for;
- a filed level is beyond the aircraft's performance;
- a filed or requested level breaks the semicircular rule.

| Scenario | Weather | What it tests | Passive | Naive |
|---|---|---|---|---|
| s00_free_flow | westerly jet | Over-control: no conflicts, 3 safe requests | ✓ | ✓ |
| s01_basic_crossing | calm | Same-level crossing at the centre | LoS | LoS |
| s02_climb_request_into_crossing | westerly jet | Climb into a crossing; the tempting FL390 amendment also fails | ✓ | LoS |
| s03_descent_through_opposite | northerly | Descent through head-on traffic | ✓ | LoS |
| s04_direct_request_reroute | westerly jet | Reroute cuts across same-level traffic | ✓ | LoS |
| s05_slowdown_on_converging_exit | westerly jet | Leader slows on converging routes | ✓ | LoS |
| s06_speed_up_into_crossing | northerly | Speed request erases an 8 NM crossing; distractor request at the same time (IBE612 overflies E1) | ✓ | LoS |
| s07_restricted_area_activation | westerly jet | Area activates across a route; the north detour runs into traffic | R12 | R12 |
| s08_simultaneous_level_requests | westerly jet | Two same-level requests 2 s apart | ✓ | LoS |
| s09_area_vertical_limits | northerly | Descent into an active restricted area | ✓ | R40 |
| s10_busy_mixed | westerly jet | 11 aircraft, 4 built-in conflicts, danger area, 4 requests | 4 LoS + D55 | 7 LoS + D55 |
| **s11_weather_deviation_into_traffic** | jet + one CB (tops FL460) | The requested deviation runs into opposite traffic that was 8 NM clear on the filed route. "Unable" isn't a fix, because the pilot deviates anyway | ✓ | LoS |
| **s12_squall_line_gap** | severe squall line, one 22 NM gap | Two same-level flights 40 NM apart are funnelled into the same gap; opposite traffic uses it at other levels | ✓ | LoS |
| **s13_turbulence_level_request** | 155 kt jet, CAT below the core | Ride request to FL390 climbs a heavy B789 into crossing traffic at the top of its climb; FL390 after the traffic is the fix | ✓ | LoS |
| **s14_climb_over_tops** | broken line of storms, tops FL350 | Northbound request to climb over the tops into traffic at FL380; fixes are a lateral deviation or a timely FL400 | ✓ | LoS |
| **s16_e1_arrival_sequencing** | westerly jet | Five arrivals converge on E1 6–8 NM apart and all high. Approving descents as asked puts them at FL250 together (LoS 0.4 NM); leaving them high breaks the LoA. The reference solution sequences with vectors and Mach/IAS restrictions | LoA + spacing | LoS + spacing |
| **s17_s4_runway_change** | northerly | SPRT changes from runway 18 (straight-in) to 36 (reversal) mid-session. The pilots' TOD moves past the LoA descent point, so later arrivals ask too late or not at all, and you must start their descent yourself | LoA | LoA |
| **s15_convective_afternoon** | scattered multicell storms, 12 flights, active restricted area | Open problem: deviations push aircraft into each other and into R71 | R71 | R71 |

**Agadir CTA: a graded progression, weather always present.** Traffic is measured against the declared sector
capacities (flights per hour: WN 58, WS 43, ON/OCN 56, OS/OCS 65, WW = WN+WS 48, OCE = ON+OS 51, WW-OCE = all 47;
`capacity_per_hour` in `atcsim/data/airspaces/agadir_cta.json`, `atcsim/capacity.py`). The traffic density of a
scenario is the number of flights inside the controlled sectors during the first hour against that capacity:
1 under 50 %, 2 50–70 %, 3 70–90 %, 4 90–105 %, 5 over 105 % (the library tops out around 112 %, a slight
overshoot).

| Step | Scenario | Control | Traffic / capacity | Complexity |
|---|---|---|---|---|
| 1 | p01_ws_two_merges | WS | 55 % | 2 |
| 2 | p02_wn_merges_and_climbs | WN | 69 % | 2 |
| 3 | p03_ocn_isolated_storms | ON | 79 % | 3 |
| 4 | p04_ocs_embedded_unable_rvsm | OS | 85 % | 3 |
| 5 | p05_ws_storms_arrivals | WS | 95 % | 4 |
| 6 | p06_wn_capacity_sequencing | WN | 100 % | 4 |
| 7 | o01_oceanic_light_wx | ON+OS | ~98 % | 4 |
| 8 | w01_west_scattered_cb | WN+WS | ~100 % | 4 |
| 9 | o02_oceanic_embedded_cb | ON+OS | ~106 % | 5 |
| 10 | w02_west_squall_line | WN+WS | ~110 % | 5 |
| 11 | c01_cta_moderate_wx | all | ~102 % | 5 |
| 12 | c02_cta_severe_wx | all | ~112 % | 5 |

What each one trains is in the in-app documentation (*Help → Scenario library and progression*,
`atcsim/docs/scenarios.md`).

Background traffic comes from `tools/agadir_traffic.py`:
- **Flows:** published routes only, with hourly rates, type mixes, operators and city pairs.
- **Levels:** set by the semicircular rule and the aircraft's performance.
- **Start:** flights enter from 30 minutes before T+0, so the sectors are already busy.
- **Thinning:** `dense(target=…)` drops background flights until the first-hour load is the target fraction of the
  declared capacity (`scale=` raises the generator rates when a sector needs more).
- **De-confliction:** a passive run moves background flights that lose separation to another level, so the
  conflicts left are the planted ones (plus whatever the weather causes).
- **Cache:** the result is stored in `tools/traffic/<name>.json`.

The build writes `author`, `complexity`, `density` and `traffic_load` into every Agadir scenario. No reference
solutions are scripted for these. The build checks that every planted conflict actually happens in the passive or
naive run. Weather deviations and in-flight pilot requests add unplanned problems on top.

## Baseline agent

`examples/baseline_agent.py` uses only the HTTP API.
- **Plain requests:** it probes each request and approves if nothing new appears within 15 minutes.
- **Weather deviations:** it tries the request as asked, then the request with a level change, then a deviation to the other side.
- **Climbs over weather:** if the climb isn't safe, it refuses the climb and offers a lateral deviation.
- **Conflicts:** it searches level, Mach and heading changes via `/probe`, then resumes the route.

```
python examples/baseline_agent.py --all
python examples/baseline_agent.py --scenario s12_squall_line_gap --realtime     # watch it on the radar
```

Results with the current library:
- **Clean:** s00–s15.
- **No sequencing logic, so it fails:**
  - s16 (spacing and level violations at E1);
  - s17 (LoA level violations after the runway change);
  - the arrival sequences in the Agadir scenarios (not re-run on the new dense set yet).

The sequencing scenarios are the first real targets for an LLM agent. Beyond them, the agent should beat the
baseline on track miles, fuel, instruction count, response time and approving more requests.

## Known limits

- **Performance data:** OpenAP plus calibration gives realistic, consistent magnitudes per type, but it is not manufacturer data.
- **Pilot model:** pilots are deterministic. There is no variability in reaction time or deviation choice, and no reports of the weather they see.
- **Radar display:** no radar position noise or attenuation.
- **Agadir:** headings are grid/true, not magnetic (the Mode S magnetic heading applies a fixed variation, `magnetic_variation`, default 1° W). The letters of agreement are placeholders. Below FL195 (class G) is not simulated. Sector lines through the two chart junctions are accurate to about 1 NM.
- **Weather lifecycle:** storm life cycles are scripted from the seed rather than produced by a numerical weather model, so storms don't react to each other.
- **Radio:** two people can transmit at the same time and both are heard (no blocked transmissions). Mic levels are whatever the browser's automatic gain gives.
- **Recording:** the controller video re-draws the screen from what the browser reported, so it matches the page closely but is not a pixel copy (fonts, emoji); weather echo-top and turbulence products appear as reflectivity.
- **Not modelled:** icing, hail outside the cores, lightning and surface weather. The sector is FL250+, so they matter little.

## Layout

```
run.py                           start the server
atcsim/atmosphere.py             ISA, CAS/TAS/Mach, energy share factor
atcsim/performance.py            per-type tables: OpenAP + calibration, temperature effects, limits, max FL
atcsim/data/type_calibration.json  editable per-type calibration targets
atcsim/weather.py                temperature, 3D wind/jet, convection, radar products, turbulence
atcsim/arrivals.py               aerodromes, runways, approach paths, top of descent, descent requests, sequencing
atcsim/pilot.py                  onboard radar, deviation planning, weather/ride requests, deviating without clearance
atcsim/aircraft.py               aircraft dynamics, speed modes, LNAV, RVSM / formation / flight plan, semicircular rule
atcsim/airspace.py               airspaces: training square, Agadir CTA (sectors, airways, RAD, transfer points)
atcsim/projection.py             Lambert conformal projection, AIP coordinate parsing
atcsim/data/airspaces/agadir_cta.json   Agadir CTA data (ENR 4.4 coordinates, airways, RAD, sectors, LoA placeholders)
atcsim/data/military_types.json  parametric military types
atcsim/model.py                  airspace areas, requests
atcsim/world.py                  stepping, instruction checks, monitoring, prediction, metrics
atcsim/scenario.py               scenario loading/validation
atcsim/session.py                sessions, clock threads, shorthand parser
atcsim/server.py                 HTTP + WebSocket API, weather endpoints
atcsim/evaluate.py               headless reference policies and scripted solutions
atcsim/static/radar.html         live radar with weather layers, RBL, recording timeline and replay
atcsim/static/designer.html      visual scenario designer
atcsim/designer.py               designer back end: compile/lint, planned conflicts, preview runs, save
atcsim/platform/config.py        platform settings (environment variables, see DEPLOY.md)
atcsim/platform/db.py            SQLite: users, scenario ownership and sharing, exercises and seats, recordings
atcsim/platform/auth.py          Google sign-in, session cookie, profile, user directory, roles
atcsim/platform/access.py        who may do what (middleware, per-seat rights on /sessions/...)
atcsim/platform/exercises.py     exercises, invitations, scenario sharing, dashboard data, pages
atcsim/platform/comms.py         radio and telephone relay (WebSocket)
atcsim/platform/recorder.py      exercise capture (traffic, weather, audio, events), recordings API, radar overview video
atcsim/platform/atcview.py       the controller-screen video (Pillow + numpy + ffmpeg)
atcsim/static/uirec.js           controller's screen activity sent to the recording
atcsim/static/home.html          home: live exercise thumbnails, scenarios, recordings
atcsim/static/seat.js, comms.js  controller / pseudo-pilot / instructor seats, push-to-talk, telephone
atcsim/static/login.html, profile.html, admin.html
tests/test_platform.py           accounts, rights, voice relay, recording (python -m tests.test_platform)
tests/e2e_platform.py            three browsers with fake microphones through a whole exercise
tests/test_stats.py              evaluations both ways, acknowledgement, statistics
tests/test_atctools.py           flight plans, MTCD and probe, special-track alerts, early assume
atcsim/flightplan.py             ICAO flight plans (generator, message, items), surveillance and Mode S data
atcsim/oldi.py                   cleared / exit levels, OLDI coordination (ABI, ACT, REV, LAM) with the adjacent units
atcsim/atctools.py               MTCD and probe, special tracks / monitoring alerts, flight-plan list and estimates
atcsim/static/atctools.js        flight-plan window and list, MTCD list and display, keyboard shortcuts, UTC clock
tools/fill_flightplans.py        writes complete flight plans into the scenario files
atcsim/platform/stats.py         evaluations and statistics API, /stats page
atcsim/static/stats.html, vendor/chart.umd.min.js   statistics charts (Chart.js 4.4.1, MIT)
atcsim/capacity.py               declared sector capacities, traffic load of a scenario, density bands
atcsim/docs_gen.py               generated documentation: reference tables, reference.json, scenario JSON Schema
atcsim/docs/*.md, static/help.html, vendor/marked.min.js   in-app documentation (Markdown, rendered with marked 12, MIT)
atcsim/static/docs/*.jpg         screenshots in the manuals (python tools/doc_screenshots.py <server>: three browsers through an exercise)
atcsim/static/login.html, landing3d.js, vendor/three.module.min.js   sign-in page with the 3D Agadir CTA (three.js 0.160, MIT)
atcsim/static/app.css, theme.css, fonts.css   night-chart theme: dashboards (app.css, with the 3D CTA behind them), radar seats and designer chrome (theme.css); Archivo self-hosted (vendor/fonts, SIL OFL)
Dockerfile, deploy/              internet deployment (Docker + Caddy HTTPS)
scenarios/*.json                 scenario library
tools/build_scenarios.py         scenario generator + trap/solution verification (python tools/build_scenarios.py a02 s16 ...)
tools/seq_solver.py              greedy sequencing-solution finder (python tools/seq_solver.py s16)
tools/agadir_traffic.py          dense Agadir background traffic: flows, rates, levels, de-confliction, sector loads
tools/traffic/*.json             cached background traffic of the Agadir scenarios (delete one to regenerate it)
examples/baseline_agent.py       reference agent
```
