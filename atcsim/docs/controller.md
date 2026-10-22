# Controller manual

This manual is for the **controller seat** of an exercise. On this seat you have the radar and nothing else, as
in the ops room: you give clearances on the frequency and enter them in the labels. The aircraft do what the
pseudo-pilot makes them do. They never react to what you type.

> **The rule of the seat:** what you *say* moves the aircraft (through the pseudo-pilot); what you *enter* informs
> the system: labels, MTCD, route adherence, OLDI coordination. Enter what you cleared, as you would on a real
> system.

## Joining an exercise

Your instructor invites you from the user list, or sends you a link (`/join/…`). The exercise then appears on your
home page in the *Your exercises* tile while it runs. **Open** takes you to the radar.

![The home page: Your exercises (Open takes you to the radar), Evaluations, Recordings, Statistics, Help](/static/docs/c_home.jpg "The home page: Your exercises (Open takes you to the radar), Evaluations, Recordings, Statistics, Help")

Click the tile itself for every exercise you took part in. Search them by name (exercise, scenario, instructor,
pseudo-pilot), by date, by seat, and sort them by date, name, complexity or density.

![Your exercises, hardest first, with the difficulty and who sat where](/static/docs/c_exercises.jpg "Your exercises, hardest first, with the difficulty and who sat where")

The microphone needs HTTPS, and the browser asks once for permission. Hold the **space bar** to transmit. It works
anywhere except inside a text field.

## The radar picture


![The controller seat: the whole window is the radar. Toolbar at the top, information box top left, alarms at the top centre, the push-to-talk hint and the telephone at the bottom](/static/docs/c_radar.jpg "The controller seat: the whole window is the radar. Toolbar at the top, information box top left, alarms at the top centre, the push-to-talk hint and the telephone at the bottom")

### Toolbar

The toolbar changes **your view only**, never the simulation:

| Control | What it does |
|---|---|
| Weather product and FL | Composite reflectivity, or a CAPPI at a flight level (to see embedded cells at your level) |
| Tops | Echo tops |
| Coast, Areas, Routes, RAD, Sectors, Names, Points, Trails | Map layers: land and coastlines (Morocco, Spain and the Canary Islands, Portugal, Madeira, the Azores), restricted / danger areas, airways, RAD routings (dashed, one-way arrows), sector lines with frequencies, point names, history dots |
| RBL | Range-and-bearing mode (you rarely need it, see [RBL](#range-and-bearing-lines-rbl)) |
| Reset view | Fit the airspace |
| **FPL** | The flight-plan list (key <kbd>G</kbd>) |
| **MTCD** | Medium-term conflict detection on/off (key <kbd>M</kbd>) |
| **INFO** | Track info window on/off (key <kbd>I</kbd>) |
| **OLDI** | OLDI messages window (key <kbd>O</kbd>) |
| **CPDLC** | CPDLC window: requests, dialogues, connections (key <kbd>J</kbd>); the count of requests waiting |
| **CHAT** | Coordination chat with the other sectors and units (key <kbd>T</kbd>); the count of unread messages |
| **HUD** | Show or hide the information box (saved in your browser) |
| **Timeline** | Solo radar (and the instructor): show or hide the replay timeline under the scope |
| **Solver** | Solo radar: the conflict solver's suggestions for each predicted conflict (see below) |
| **?** | Keyboard shortcuts |
| UTC clock | The exercise clock (the scenario's start time, then real time × the time factor) |
| ⚙ Display | Zoom, fonts, symbol, speed vector, history dots, weather opacity, every colour. Saved in your browser |

Map: drag the background to pan, use the wheel to zoom, double-click the background to refit.

### Aircraft states and colours

| State | Look |
|---|---|
| Inbound, still with the previous unit | Its own colour, label with *entry point ←previous unit* and the coordinated entry level (`KORNO F350 ←GMMM`) |
| **Calling** you | White, blinking, `CALL` |
| **Assumed** (yours) | Green, full label |
| About to leave (3 min before the exit) | A chevron ahead of the symbol and `XFR →next unit` |
| Left the sector, still assumed | `DECONTROL →unit` blinking: decontrol it |
| Decontrolled / other sector's traffic | Grey, short label with a ◇ symbol: callsign, level, type, next unit |

![An aircraft calling you: white, blinking, CALL next to the callsign](/static/docs/c_calling.jpg "An aircraft calling you: white, blinking, CALL next to the callsign")

The default colours can all be changed in ⚙ Display.

### The label

```
NRVSM MTC7'                 ← alerts line (special tracks, MTCD)
RAM101                      ← callsign
350↓330 S350 X310 B738      ← current level, arrow, cleared level, Mode S selected level, exit level, type
451 M78 026›066 H >IBALU    ← ground speed, speed, heading, tags, next point (or the direct entered)
→LPPC                       ← info line (next unit, coordination, notes)
```

![An assumed label: NRVSM alert, callsign, current level 280, exit level X280, type, then ground speed, IAS, heading changing 032›062 with the yellow H and WXL tags entered by the controller, and the next unit](/static/docs/c_label.jpg "An assumed label: NRVSM alert, callsign, current level 280, exit level X280, type, then ground speed, IAS, heading changing 032›062 with the yellow H and WXL tags entered by the controller, and the next unit")

**Three levels.** Real systems work with three levels, and so does this one:

- **Current level (AFL)**: what the transponder gives (Mode C / Mode S).
- **Cleared level (CFL)**: what you entered. Until you enter one it is the level coordinated with the previous
  unit.
- **Exit level (XFL)**: what you enter for the coordination with the next unit. Left empty, the cleared level is
  coordinated. The label shows `X310` when an XFL is entered or differs from the CFL. The colour gives its OLDI
  state: white not yet sent, light blue after the ABI, cyan once coordinated by the ACT. Amber means it breaks the
  LoA or the semicircular rule, or needs a phone coordination.

The **Mode S selected level** (`S350`) appears in amber when it differs from your CFL. You only get it for aircraft
with enhanced surveillance. It is how you catch a crew that set the wrong level.

**Line 3** always shows the **present heading**, whether or not a heading was given. A speed or heading that is
changing shows *current›assigned* (`M78›76`, `026›066`). A yellow **H** means you entered a heading. A yellow
**WX** (`WXL20`) means you entered a weather deviation. These tags come **only from your entries**: a pilot who
deviates by himself shows nothing until you enter it. The route-deviation alert shows instead.

**The route stays displayed** while an aircraft is vectored or deviating: a long press on the route value (or key
<kbd>R</kbd>) draws the flight-plan route from the present position with the minutes to each point, and the
heading line.

### Alerts line (visual only)

| Tag | Meaning |
|---|---|
| `NRVSM` | Not RVSM approved, or unable RVSM (amber inside FL290–410) |
| `MIL` | Military / state aircraft (formations: `x4` after the callsign) |
| `NSL` | Non-standard level: against the semicircular rule, or not a flight level |
| `RDEV` | Route deviation: more than 3 NM off the flight-plan route, or not flying the direct / route you entered, with no heading or WX entered |
| `LVL` | Level bust: leaving the cleared level, or levelled more than 300 ft from it (red, flashing) |
| `MTC7'` | Medium-term conflict in 7 minutes (MTCD on) |
| `COORD` | A change too close to the boundary for an OLDI revision: coordinate by phone |

Alarms at the top of the scope: an amber **STCA** banner per pair predicted to lose separation within 3 minutes,
and a red flashing **LOSS OF SEPARATION** banner per pair below the minimum (7 NM in the Agadir CTA, 5 NM in the
training sector; 1000 ft, or 2000 ft for non-RVSM pairs in RVSM airspace, above FL410 and against formations).
Click a banner to select the aircraft.

## Working with labels: flat buttons

Every value in the label is a **flat button**. It looks like ordinary label text and is underlined when the mouse
is over it.

![A click on the callsign: assume or decontrol, flight plan, track info, type performance, route](/static/docs/c_callsign_menu.jpg "A click on the callsign: assume or decontrol, flight plan, track info, type performance, route")

![A click on the cleared level: the level list, coloured by the probe (green: no conflict in 20 minutes, red: conflict)](/static/docs/c_level_menu.jpg "A click on the cleared level: the level list, coloured by the probe (green: no conflict in 20 minutes, red: conflict)")

![A click on the aircraft type: its performance at the present weight, level by level](/static/docs/c_type.jpg "A click on the aircraft type: its performance at the present weight, level by level")

| Click on… | Opens |
|---|---|
| **Callsign** | Assume / Decontrol, Flight plan, Track info, Type performance, Edit route, Show route, Full menu |
| **Aircraft type** | The type performance window: type data, speed schedule, assignable Mach / IAS, mass now, and by level at the present weight the speeds, climb rate, idle descent, and whether the level can be reached |
| **Cleared level** | Levels list, coloured by the level probe (green = no conflict in 20 min, red = conflict, hover for which) |
| **Exit level** | Levels list with *= CFL* |
| **Cleared level** | (with the list) a **rate** field: `1500` = at 1500 ft/min or greater, `1500-` = or less, to climb or descend two aircraft together |
| **Speed** | Mach / IAS values, *SN* (resume normal speed), and its **limit**: minutes, miles or a point (`10MIN`, `40NM`, `IBALU`, quick buttons) |
| **Heading** | Left / right steps, *own navigation*, and its **limit** (`4MIN`, `20NM`) with the point to proceed to at the limit (`→ERMED`) |
| **Next point** | Directs (points of the route, or any point), *Edit route…*, back to the filed route |
| **WX** | Weather deviation left / right / either side, with a distance, or clear |

A **long press** on any value opens the **full label menu** with every field together, the level probe and the
phone-coordination button. A long press on the **route** value shows the route instead. Drag a label to move it
(a leader line appears), and double-click it to put it back.

Clicking a label or a symbol **selects** the aircraft. The selection drives the keyboard shortcuts and the track
info window.

### Clearance limits

A heading or a speed is given with its **limit**, as the regulations require: *fly heading 250 for 4 minutes, then
direct ERMED*, *maintain M.74 until IBALU*. At the limit the pilot does what was cleared by himself and says so on
the frequency (*EWG3KP end of heading, proceeding direct ERMED*, *EWG3KP resuming normal speed*); a new heading or
speed replaces the limit. A heading or speed entered without a limit is still flown, with a warning (⚠ *heading
without a limit*), and counted in the exercise statistics. In the label menus: the **for / then** fields of the
heading, the **limit** field of the speed; on the command line, after the heading or the speed: `H250 4MIN THEN
ERMED`, `H250 20NM`, `M74 UNTIL IBALU`, `M74 10MIN`.

### Letter of agreement at the exits

Aircraft leaving by the **same exit point at levels that are not vertically separated** must be at least **10 NM
apart in trail** when they cross it, before they are transferred (the scenario or airspace may set another value).
Each crossing closer than that is recorded (*IBE3247 crossed ERMED 1.9 NM behind EWG3KP at FL350*) and counted in the
statistics; the Solver panel lists such pairs (**EXIT** in blue) with the clearances that give the spacing.

### Departures from GMMN

Departures from Casablanca (GMMN) climbing into the sector ask for their **final level on first contact**
(*RAM6589K B788 FL260, checking in, requesting FL360*): a level request is opened at the same time as the first call.
Their filed cruise level is that level, so it may be given without asking the pilot first.

### The full label menu

The full menu holds the CFL (with quick levels coloured by the probe), XFL, speed (`M78`, `280`, `SN`), heading,
direct, weather deviation (`L`, `R`, `L20`, either side) and a free note. **Probe** checks the level, heading or
direct you typed against the system trajectories before you give it. The coordination part shows the OLDI state
and, when needed, *✓ coordinated by phone*.

![The full label menu (long press on a value): every field, the coordination state, the probe](/static/docs/c_full_menu.jpg "The full label menu (long press on a value): every field, the coordination state, the probe")

## Assume, transfer, decontrol

- **Assume any track, at any time**: an aircraft calling you, one still with the previous sector (early assume:
  it then calls already assumed), or one you decontrolled earlier. Use a double-click, *Assume* in the callsign
  menu, or <kbd>A</kbd>.
- **Decontrol** with *Decontrol* in the callsign menu, <kbd>U</kbd>, or `CS REL` in a shorthand. An aircraft that
  leaves the sector stays assumed, with a full label and *DECONTROL →unit* blinking, until you decontrol it. It
  then turns grey as another sector's traffic.
- **Re-assume** a decontrolled aircraft: its grey label's callsign is a button with *Assume*.

Losses of separation, area infringements and weather penetrations count only for aircraft on your frequency.

## OLDI coordination

For every aircraft you control, the system coordinates automatically with the unit it goes to next:

![The OLDI window (key O): every message sent and received, with the receiving system's acknowledgement](/static/docs/c_oldi.jpg "The OLDI window (key O): every message sent and received, with the receiving system's acknowledgement")

| Message | When |
|---|---|
| **ABI** (advance boundary information) | 20 min before the coordination point, and again if the exit level changes |
| **ACT** (activation) | 10 min before: point, estimate and exit level are now binding |
| **REV** (revision) | After the ACT, when the exit level changes, or the estimate by more than 3 min |
| **LAM** | The receiving system's acknowledgement of each message, a couple of seconds later |

A change **closer than 3 minutes** to the point is not sent. `COORD` blinks on the label, the OLDI button shows ⚠,
and the label menu tells you what to coordinate with whom. Phone the unit, then press *✓ coordinated by phone*.

The previous units send their ABI / ACT for traffic coming to you. The inbound label shows the entry point, level
and unit. The **OLDI window** (<kbd>O</kbd>) lists every message sent and received.

## Tools

### Track info window

Turn it on with *INFO* or <kbd>I</kbd>. It is movable and shows the aircraft under the mouse, else the selected
one, else the last one under the mouse. Middle-click an aircraft or use 📌 to keep one. It shows the SSR code,
Mode S address, type, registration, operator, departure → destination, requested level, the current / cleared /
exit levels with the coordination state, the **Mode S downlinked parameters** when available, the next points,
the units, the exit point and time, and its alerts.

![Track info window for an aircraft with Mode S enhanced surveillance: levels, downlinked parameters, route and transfer](/static/docs/c_track_info.jpg "Track info window for an aircraft with Mode S enhanced surveillance: levels, downlinked parameters, route and transfer")

The Mode S parameters are the selected level, magnetic heading, IAS, Mach, TAS, ground speed, track, vertical
rate, roll, track rate and pressure setting. You get them for aircraft with enhanced surveillance only (`L`/`H` in
item 10). Elementary surveillance gives the address only; Mode A/C gives nothing more.

### Flight plans

Every aircraft has an ICAO flight plan, correlated with its track by the SSR code and the Mode S address.

![The flight-plan window (key F): the ICAO message, the decoded items, the coordination and the estimates](/static/docs/c_fpl.jpg "The flight-plan window (key F): the ICAO message, the decoded items, the coordination and the estimates")

![The flight-plan list (key G): every flight of the session, with filters](/static/docs/c_flight_list.jpg "The flight-plan list (key G): every flight of the session, with filters")

- <kbd>F</kbd> opens the selected aircraft's flight plan. It shows the FPL message, the decoded items, the
  coordination (entry point / ETO / level / from which unit, exit point / ETO / level / to which unit) and the
  estimates over the points ahead.
- <kbd>G</kbd> or *FPL* opens the list of every flight of the session (planned, inbound, calling, assumed,
  transferred), with filters.

### MTCD (medium-term conflict detection)

Turn it on with the *MTCD* button or <kbd>M</kbd>. It is off by default. MTCD looks ahead (**20 minutes** unless you
change the look-ahead) along the
**system trajectories**: the flight-plan route, or the heading / direct / route you entered, at the system cleared
level. It warns well before the STCA and finds conflicts you have not yet cleared into.

![MTCD on: the conflict list (top left), the violet diamonds and MTCn' tags, and where both aircraft will be when the conflict starts](/static/docs/c_mtcd.jpg "MTCD on: the conflict list (top left), the violet diamonds and MTCn' tags, and where both aircraft will be when the conflict starts")

- **Look-ahead**: at the top of the MTCD window, from 5 to 60 minutes. Type a value and press <kbd>Enter</kbd>, or
  use − / + (5 minutes each). It is a setting of the exercise: the instructor's MTCD follows yours, and the
  instructor can change it too. A short look-ahead keeps the list to what needs action now in dense traffic; a
  long one helps plan the oceanic crossings early.
- The list shows each pair, the time to the conflict, and the minimum distance and level difference.
- Click a conflict (or select one of its aircraft) to see where both will be when it starts. Drag that label away
  if it is in the way.
- The aircraft gets a violet diamond and `MTCn'` in its label.

The **level probe** in the level menus uses the same trajectories: green levels are clear over the look-ahead, red ones
are not (hover to see with whom).

### Range and bearing lines (RBL)

- **Key <kbd>B</kbd>** with the mouse over an aircraft, a fix, or anywhere in space starts a line there. The line
  follows the mouse and shows its values. A click (or <kbd>B</kbd> again) places the other end, and <kbd>Esc</kbd>
  cancels. You need no selection and no button.
- **Right-drag** from an aircraft or a point to another.
- Shift-click two aircraft, or click *RBL* and then two ends.

![Key B over an aircraft: the line follows the mouse and already shows bearing, range and the closest point of approach](/static/docs/c_rbl.jpg "Key B over an aircraft: the line follows the mouse and already shows bearing, range and the closest point of approach")

An end snaps to a fix only when you click within 10 px of the fix symbol. A ring shows what it will snap to.
Otherwise it is the position itself. The label gives the bearing, range and ΔFL, then the **CPA** (closest point
of approach) distance and time, from straight-line extrapolation of the tracks. The positions at CPA are drawn as
circles. A fixed point stands still, so the CPA is the aircraft's closest approach to it. The line turns red when
the CPA is below the minima. Drag an RBL label to move it, right-click it to remove it, use
<kbd>Delete</kbd> to remove the last line and <kbd>Shift</kbd>+<kbd>Delete</kbd> to remove them all.

### Re-routing

Open the editor with the route value → *Edit route…* or <kbd>E</kbd>. It lists the route from the aircraft
onwards. Click on the map to insert a point after the highlighted one. Clicking a fix's name or symbol inserts
the fix. Clicking anywhere else inserts the position itself, named from its coordinates (e.g. `3103N01244W`).
× removes a point, and you can also type names. *Enter route* puts it in the system: the label's next point, the
route display, MTCD, route adherence and the estimates all follow it. The pseudo-pilot flies what you said.

![The route editor (key E): the route ahead as a list; click on the map to insert a fix or a position](/static/docs/c_route_editor.jpg "The route editor (key E): the route ahead as a list; click on the map to insert a fix or a position")

### Weather

Use the weather product menu: composite reflectivity, or the **CAPPI at a flight level**. Embedded
thunderstorms hidden in rain only show clearly on a CAPPI at their level. *Tops* shows echo tops. Aircraft ask for
deviations by themselves, typically about 60 NM before the weather (*"request heading 170 for 65 miles due
weather, then direct SAMAR"*), or ask to climb over the tops.

In exercises, aircraft do **not** deviate by themselves. A request you leave unanswered until the weather is
15 NM ahead is closed as **not answered** and counted as a task not handled. When you approve a deviation, enter
the **WX** tag, so the system knows the aircraft is dealing with weather and the label does not show `RDEV`.

## CPDLC and the command line

### The command line

Press <kbd>Enter</kbd> (outside a text field) to open the command line at the bottom of the scope, and type the
clearance for the selected aircraft. Several items make one clearance: `CL350 M78 D ROLRO`. You can also start with
the callsign (`RAM201 H270`) without selecting the aircraft.

![The command line: the selected aircraft and its CPDLC state on the left, what will be sent above, suggestions as you type](/static/docs/c_cpdlc_typing.jpg "The command line: the selected aircraft and its CPDLC state on the left, what will be sent above, suggestions as you type")

- **Enter** sends it by **CPDLC** when the aircraft is connected (`CPDLC` next to the callsign). The clearance goes in
  the system only when the pilot answers **WILCO**.
- **Shift+Enter** only puts it in the system, for a clearance you give **by voice** (cleared level, heading, direct,
  speed, route, weather deviation, as with the label menus).
- An aircraft with no data link (`VOICE`) or not yet connected (`LOGON`): Enter puts the clearance in the system and
  reminds you to give it by voice.
- Above the line you see the message in clear (`↑ CLIMB TO FL350 · MAINTAIN M.78`) or what is wrong. Suggestions
  follow what you type: after `D` the next points of the aircraft's route, then points and keywords. <kbd>Tab</kbd>
  takes the highlighted one, the arrows move between them, <kbd>Esc</kbd> closes the line.

| Type | Sends | Type | Sends |
|---|---|---|---|
| `CL350`, `C350` | CLIMB TO FL350 | `DES240` | DESCEND TO FL240 |
| `350`, `FL350` | climb / descend / maintain, from the present level | `V1500` / `V1500-` after a level | AT 1500 FT/MIN MINIMUM / MAXIMUM |
| `4MIN`, `20NM` after a heading or a speed | its limit: FOR 4 MIN / FOR 20 NM | `UNTIL IBALU` after a speed, `THEN ERMED` after a heading | MAINTAIN … UNTIL IBALU / … THEN PROCEED DIRECT TO ERMED |
| `H270` | FLY HEADING 270 | `L270` / `R270` | TURN LEFT / RIGHT HEADING 270 |
| `D ROLRO`, `DROLRO`, `ROLRO` | PROCEED DIRECT TO ROLRO | `VIA IBALU TERTO` | CLEARED TO TERTO VIA IBALU TERTO |
| `M78` | MAINTAIN M.78 | `S280` / `K280` | MAINTAIN 280 KT / ON CONVERSION |
| `SN` | RESUME NORMAL SPEED | `RES` | RESUME OWN NAVIGATION |
| `DEV`, `DEVL20`, `DEVR15` | CLEARED TO DEVIATE UP TO 20 NM (EITHER SIDE / LEFT / RIGHT) OF ROUTE | `TXT …`, `"…"`, or any words | free text |
| `CT` / `MON` | CONTACT / MONITOR the next unit on its frequency (`CT GCCC 128.6` to choose) | `NDA GCCC` | NEXT DATA AUTHORITY |
| `APP` | approves the pilot's request as asked | `UNA`, `SBY`, `ROG`, `AFF`, `NEG` | UNABLE, STANDBY, ROGER, AFFIRM, NEGATIVE |
| `EOS` | END SERVICE (ends the connection) | | |

The level, heading and speed shortcuts are those of the classic sandbox's command box. Words the command line does not recognise are sent as free text, shown as such above the line before you send it (`CL370 WHEN READY` = CLIMB TO FL370 / WHEN READY).

### Connection and data authority

An aircraft is CPDLC equipped when its flight plan item 10 has J1 to J7. It logs on to GMAC about 15 minutes before
it enters, while the previous unit is still the **current data authority** (label: a dim `d`). At the transfer of
communication GMAC becomes the current data authority (label: `D`), and you can exchange messages once you have
assumed it.

When the coordination with the next unit is activated (OLDI ACT), the system sends **NEXT DATA AUTHORITY** to that
unit; `NDA` changes it. Transfer the aircraft with `CT` (or `MON`): when the pilot answers WILCO the aircraft is
decontrolled and the connection goes to the next unit with END SERVICE. Between our own sectors the connection stays
with GMAC. An aircraft you decontrol by voice has its connection transferred by the system.

### Dialogues on the label and in the CPDLC window

The label shows the dialogue after the callsign:

| Tag | Meaning |
|---|---|
| `D` / `d` | connected (we are the current data authority) / logged on, not yet ours |
| `↑CL350` (white) | your uplink, waiting for the pilot |
| `↑CL350 SBY` (amber) | the pilot answered STANDBY |
| `↑CL350?` (blinking amber) | no answer after 2 minutes |
| `↑UNABLE` (red) | the pilot answered UNABLE (shown for 40 s) |
| `✓` (green) | WILCO (a few seconds) |
| `↓RQ370`, `↓RQ ROLRO`, `↓RQ WX` (blinking cyan) | a request from the pilot: answer it |

The **CPDLC** button (key <kbd>J</kbd>) opens the CPDLC window. It counts the requests waiting for you.

![The CPDLC window: requests to answer, uplinks waiting for the pilot, the selected aircraft's connection and dialogue](/static/docs/c_cpdlc_window.jpg "The CPDLC window: requests to answer, uplinks waiting for the pilot, the selected aircraft's connection and dialogue")

- **Requests**: each pilot request with **Approve** (the clearance as asked), **Standby** and **Unable**. You can
  also answer on the command line: `APP`, `SBY`, `UNA`, or any clearance (`CL360` to give less than asked).
- **Waiting for the pilot**: your uplinks not answered yet.
- **The selected aircraft**: its connection (current and next data authority) and its messages.

## Radio, telephone and chat

- **Radio:** hold the **space bar** (outside text fields) or the on-screen button. Everyone in the exercise hears
  you. The scope shows who is transmitting.
- **Telephone ☎:** call GMMM, LPPC, GCCC, LECS or MADEIRA. The pseudo-pilots (and instructors) answer as that
  unit. When they call you, a ringing box shows the unit: answer it. Calls are full duplex until you hang up.

![An adjacent unit calling you: answer or decline](/static/docs/c_phone_ringing.jpg "An adjacent unit calling you: answer or decline")

### Coordination chat

Written coordination with the other sectors and the adjacent units, next to the telephone. Every message shows its
**sender and its destination**: you are our sector (`GMAC WS`, or `GMAC WN+WS` when band-boxed); the others are the
Agadir sectors not controlled in the exercise (`GMAC WN`, `GMAC ON`…) and the adjacent units (`GMMM`, `LPPC`,
`GCCC`, `LECS`, `MADEIRA`). The pseudo-pilots and the instructor write as those stations.

![The coordination chat: conversations by station, each message with sender → destination, the aircraft it is about, and quick answers](/static/docs/c_chat.jpg "The coordination chat: conversations by station, each message with sender → destination, the aircraft it is about, and quick answers")

- A message to you pops up at the bottom right for a few seconds (click it to open the conversation). The **CHAT**
  button (key <kbd>T</kbd>) counts the messages not read yet.
- The window lists the conversations by station. Pick the destination in *To*, the aircraft in *about* (the selected
  one by default), type and press Enter.
- A message to you has quick answers: **APPROVED**, **UNABLE**, **STANDBY**, **ROGER**. They go back to its sender,
  about the same aircraft.


## Keyboard shortcuts

Shortcuts work outside text fields, with or without capitals.

![The shortcuts window (key ?)](/static/docs/c_shortcuts.jpg "The shortcuts window (key ?)")

| Key | Action | Key | Action |
|---|---|---|---|
| <kbd>Tab</kbd> / <kbd>Shift</kbd>+<kbd>Tab</kbd> | Next / previous aircraft | <kbd>A</kbd> | Assume |
| <kbd>C</kbd> or <kbd>L</kbd> | Cleared level menu | <kbd>U</kbd> | Decontrol |
| <kbd>X</kbd> | Exit level menu | <kbd>F</kbd> | Flight plan |
| <kbd>H</kbd> | Heading menu | <kbd>G</kbd> | Flight-plan list |
| <kbd>S</kbd> | Speed menu | <kbd>I</kbd> | Track info window |
| <kbd>D</kbd> | Direct / route menu | <kbd>O</kbd> | OLDI window |
| <kbd>W</kbd> | Weather deviation menu | <kbd>M</kbd> | MTCD on/off |
| <kbd>N</kbd> | Note | <kbd>R</kbd> | Show route |
| <kbd>E</kbd> | Edit route | <kbd>B</kbd> | RBL from the mouse |
| <kbd>Delete</kbd> | Remove last RBL (<kbd>Shift</kbd>: all) | <kbd>+</kbd> / <kbd>−</kbd> | Zoom |
| Arrows | Pan | <kbd>0</kbd> | Reset view |
| <kbd>Esc</kbd> | Close / cancel / deselect | <kbd>?</kbd> | Help |
| <kbd>Space</kbd> (held) | Transmit | <kbd>Enter</kbd> | Command line (CPDLC, voice entry) |
| <kbd>J</kbd> | CPDLC window | <kbd>T</kbd> | Coordination chat |
| <kbd>P</kbd> | Run / pause (solo radar) | <kbd>K</kbd> | ⚑ Mark a moment (solo radar) |

## Solo radar: practising alone

*Solo radar* (in the top bar, for every account) lists the scenarios you can fly. Pick one with **Fly it** and the
radar opens with you as the controller. It is the same screen as in an exercise: labels and their menus, the command
line, MTCD, CPDLC, flight plans, OLDI, RBLs. There is no radio and no telephone; simulated pilots take their place.

![Solo radar: pick a scenario](/static/docs/s_pick.jpg "Solo radar: pick a scenario")

- **The control bar** at the top right: **Run** / **Pause** (key <kbd>P</kbd>), the time factor (×1 to ×30),
  **+10 s**, **⟲** to start again, **End**. It starts paused.
- **What you enter is flown.** A cleared level, heading, direct, speed, route or weather deviation entered in the
  label menus, or on the command line, is flown by the pilot after a short delay. The pilot reads it back, or says
  *unable* with the reason (performance, weather, outside the levels you can give).
- **Force**: a refused clearance shows **Force** in the radio log: the pilot flies it anyway (into the weather, as
  high as the aircraft can climb), to see what it would have given. A forced heading or direct is held through the
  weather (no deviation of its own) until the next heading, direct or route. In the debrief, **WX avoid: OFF** stops
  the simulated pilots deviating around weather by themselves at all. **Comply** in the toolbar makes the pilots accept
  every clearance while it is on (also in the debrief). Speeds outside the aircraft's range stay refused.
- **The radio log** at the bottom left shows what the pilots say: first calls (with **Assume**), read-backs, unable,
  and their requests with **Approve** / **Unable** (or **Roger** for a report). `APP`, `UNA` and `ROG` on the command
  line do the same.
- **CPDLC**: connected aircraft send their requests by data link and answer your uplinks by themselves (WILCO, or
  UNABLE when they cannot).
- **LIVE** in the toolbar opens the live actions: add an aircraft, an emergency, a pilot request, switch an area
  (see the [instructor manual](instructor.md#live-actions)). Its **Read-backs** tab makes pilots read some
  clearances back wrong: listen to the read-back in the radio log, and correct it (enter the clearance again)
  within a minute, or the pilot flies the wrong value.
- **HUD** in the toolbar hides or shows the information box (scenario, time, traffic, wind).
- **The timeline** under the scope records the run: click or drag it to look back (REPLAY shows in the information
  box), **▶ Replay** plays from there, **Live** returns to the live picture. Its markers show losses of separation,
  requests and instructions. **Timeline** in the toolbar hides or shows it. **⤺ Resume from T+…** puts the
  simulation back to the cursor (paused): try another plan from there.
- **Solver** in the toolbar opens the conflict solver. For each predicted conflict with one of your aircraft (over
  10 minutes) it lists the clearances that leave **everyone** separated, not only the pair — in the order a
  controller would use them: **speed**, then a **direct** or a **delaying vector** (a heading for 2, 4 or 6 minutes,
  then resume), then a **level**, and a pair of clearances when no single one works. The aircraft that has priority in
  its level (level before changing level; then the first in the sector) is left alone where possible; the reason is
  shown. A ✓ before the distance means the solution was checked by running the simulation ahead (the closest anyone
  then comes, in NM); the others are the prediction. *ask first* marks a level the pilot did not request: ask whether
  it is acceptable before giving it. *too close for a turn* means no heading of that aircraft can separate it in time.
  **Apply** enters the clearance in the label, as you would; a vector or speed goes with its limit. The panel also
  lists the pilots' **requests** waiting for you (green): *AS ASKED* when the requested level or direct is separated
  from everyone, *WITH* a speed or vector that makes it so (for the aircraft asking, or the one in the way), or *NOT NOW*
  when nothing does. Speeds are proposed within ±0.03 Mach (±20 kt) of the aircraft's own speed (none for a departure
  still climbing); a larger change, or a speed below 250 kt (given only when the pilot has said it can), entered by
  hand is flown with a warning. Vectors keep the aircraft's direction of
  flight (within 60° of the way to the point it rejoins, never away from its exit). A climb or descent may be stopped
  at an **intermediate level** (*FL280 for now, FL360 when separation is assured*): the request stays open and the
  final level follows when it is clear. *ruled out* lists the plausible clearances the rules set aside, by rule. The list follows the traffic every 5 simulated seconds.

  ![The solver panel](/static/docs/s_solver.jpg "The solver panel: the conflict, its solutions in order, checked ones with ✓")
- **Sequence** in the toolbar opens the sequencing tool (here, in the sandbox, and on the controller's seat of an
  exercise). Click two or more aircraft on the scope (click one again to
  remove it), then the point where they must be in trail. They are sequenced first come, first served at that point,
  10 NM in trail (the agreement's spacing at a sequencing fix), measured when the one behind crosses it. For each
  follower the scope shows its **sequencing ellipse** (dashed, in its colour): the points where, turning direct to the
  point, it ends up exactly the spacing behind the one ahead — one focus on the aircraft, the other on the point, the
  wind included. The dots on it are the **headings that can be given** (labelled every 10°), the solid line the one
  the plan takes (*heading 190 for 39 NM, then direct IBALU*). The panel lists the plan's clearances (a speed first
  when it is enough, otherwise the fan of vectors, checked against all the traffic) with **Apply**, and for each
  follower the headings within the rules with their time on the heading: click one to see it on the scope and apply
  it (that one is the geometry only, not checked against the other traffic). Headings off the direction of flight,
  over 15 minutes, or too small or too large a turn are left out. The picture follows the traffic every 10 seconds;
  <kbd>Esc</kbd> or ✕ leaves the tool. While it is on, the labels work as usual (click, drag, menus); an aircraft is
  added to or removed from the sequence by a click on its symbol. Each ellipse is a faint line, with the arc of the
  headings that can be given drawn plainly (a tick every 10°, its two ends labelled); moving the mouse over a
  follower in the panel, over its label or symbol on the scope, or picking one of its headings, brings it forward and
  dims the others. Every follower gets its ellipse (one already far enough behind is said so); an aircraft whose route
  does not go through the point is taken direct to it. The point can also be typed in the panel's *point* field (a
  waypoint under a label is hard to click).
- **⚑ Mark** on the control bar (key <kbd>K</kbd>) marks a moment with a note, to find it again in the debrief.
- **End** keeps the run in your exercises and your statistics (marked *solo*), with its recording, and opens the way to
  its **debrief**.

![The solo radar: the control bar top right, the radio log bottom left, the command line](/static/docs/s_radar.jpg "The solo radar: the control bar top right, the radio log bottom left, the command line")

## After the exercise

- **Debrief**: the exercise re-simulated on this radar, exactly as you saw it (your assumes, entries, coordination,
  alerts), with **Play / Pause**, **±30 s**, the speed and the time bar. Open it from the exercise's card or the
  Debriefs tile, whenever you want:
  - **try other instructions**: act on the labels (assume, decontrol, a level, a heading...), playing or paused. An
    action creates a **new timeline** from that second (your next actions within a minute join it); a synthetic pilot flies it, and it is extrapolated to the
    end of the exercise (the other aircraft do what they did), so you see the outcome in the **Timelines** panel: no
    loss, or when the first one happens. Switch between timelines to compare; your exercise itself never changes;
  - when your instructor holds the **debrief session** (**● Join the debrief** on your home page): an open call (talk
    freely, **🎙 On / 🔇 Muted**), one shared player that the instructor runs, your view follows theirs and you see their
    cursor. You may act on the picture too: your actions branch timelines everybody sees.
  **Report ↗** gives the run written up: workload, each loss of separation with the clearances before it, requests
  left unanswered, read-backs, infringements, the bookmarks.
- **Recording:** the server renders a video of *your* screen (traffic as your seat showed it, your labels, your
  windows, your mouse and clicks, a caption log of your actions) with the frequency and your telephone calls. It
  appears on the *Recordings* page (home page tile), ready to download.
- **Evaluation:** your instructor rates you (phraseology, conflict detection, conflict resolution, anticipation and
  planning, coordination, overall grade). Read it on the *Evaluations* page (home page tile), then acknowledge it with a
  comment. You rate the pseudo-pilot there too.
- **Statistics** (*Stats*, *As controller*): losses of separation per session and per hour, your grades over time,
  other mistakes (infringements, RVSM, wrong-direction exits, weather penetrations, unanswered requests), reaction
  times, your profile per dimension, and the list of sessions. Each session shows the scenario's **complexity** and
  **density** as they were when you flew it, so you can see your results against the difficulty.

![The debrief session, on the instructor's screen: the open call (top right), the timelines (left), the player's controls, a new timeline being extrapolated](/static/docs/x_debrief.jpg "The debrief session, on the instructor's screen: the open call (top right), the timelines (left), the player's controls, a new timeline being extrapolated")

![The instructor's evaluation on your home page, to acknowledge with a comment](/static/docs/c_evaluation.jpg "The instructor's evaluation on your home page, to acknowledge with a comment")

![Your statistics: losses per session, grades, other mistakes, reaction times, profile, and every session with its difficulty](/static/docs/c_stats.jpg "Your statistics: losses per session, grades, other mistakes, reaction times, profile, and every session with its difficulty")
