# Scenario designer

A scenario is one JSON file. You can build it three ways, and mix them freely:

1. **Visually**, in the designer (`/designer`): click routes, drag aircraft along the timeline, draw storms and
   areas.
2. **As JSON**, in the designer's **{ } JSON** editor, by **⤒ Import**ing a file, or with any JSON tool or text
   editor. The file format is below, field by field.
3. **With scripts**: generate or transform scenario files (Python, `jq`…) and check / save them through the API, or
   with the repository's scenario builder.

Whatever the route, the server **checks** the scenario every time: errors, warnings, the planned conflicts, the
traffic load against capacity, and the estimated ratings. Use [Check and preview](#check-and-preview) to make sure
the traps bite.

## The designer

### Layout

- **Map** (centre): the airspace, routes, points, aircraft at the cursor time, weather, areas.
- **Timeline** (bottom): one lane for the weather, one per area, one per aircraft. An aircraft's bar runs from its
  entry to its exit, and is grey before the entry. The vertical line is the **cursor time**.
- **Side panel**: the **Scenario**, **Selected** and **Check & preview** tabs.

![The designer: map in the centre, timeline below, the Scenario / Selected / Check & preview tabs on the right](/static/docs/d_designer.jpg "The designer: map in the centre, timeline below, the Scenario / Selected / Check & preview tabs on the right")

Timeline controls: use the wheel to move the cursor, <kbd>Ctrl</kbd>+wheel to zoom, and the wheel over the
callsigns (or <kbd>Shift</kbd>+wheel) to scroll the lanes. With many aircraft: drag the timeline's top edge to make
it taller (kept in your browser), **⇕ Show all** to see every lane at once (lanes get thinner when they must; again
to go back), **filter callsigns** (several: separated by spaces) and **conflicts only**. **▶** plays at the chosen speed (×10–×120). Markers: ◆ the aircraft's timing point, ● pilot
requests (L level, D direct, S speed), ■ RVSM events. A red underline marks a planned conflict, a white one a loss
in the preview run.

- **Drag a bar** to move a flight in time.
- **Drag a marker** to re-time a request, an event, a cell or an area activation.

### Map tools

| Tool | Key | Use |
|---|---|---|
| ⬚ Select / move | <kbd>V</kbd> | Select; drag an aircraft **along its route** (re-times it so it is there at the cursor time); <kbd>Shift</kbd>-drag frees it from its route; drag cells and areas; pan |
| ✈ Aircraft | <kbd>A</kbd> | Click waypoints (or pick a published, RAD or standard route), then click **on the route** where the aircraft must be at the cursor time. Or click an **empty spot** first to start it there (a free start), then its waypoints, then <kbd>Enter</kbd> |
| ⛈ Storm cell | <kbd>C</kbd> | A thunderstorm cell at the cursor time |
| ≋ Squall line | <kbd>L</kbd> | Drag along the line |
| ▦ Convection area | <kbd>R</kbd> | Drag a rectangle: isolated / scattered / embedded cells |
| ≈ Rain area | <kbd>W</kbd> | Drag a rectangle: stratiform (widespread moderate) rain |
| ⬠ Area (polygon) | <kbd>P</kbd> | Click the corners, double-click or <kbd>Enter</kbd> to close |
| ◯ Area (circle) | <kbd>O</kbd> | Drag from the centre |
| ↔ RBL | <kbd>B</kbd> | As on the controller seat: <kbd>B</kbd> starts a line at the cursor (snapped to the aircraft or the fix under it) that follows the mouse; a click or <kbd>B</kbd> again places it, <kbd>Esc</kbd> cancels. Or right-drag from one end to the other, or the RBL button then two clicks. The line is shown live with bearing, range (and ΔFL); between two aircraft also the **CPA along their trajectories** (follows the timeline). Right-click a label to remove it |
| View | | Map layers, remembered in the browser: coast, routes, point names (the neighbours' minor points are named when zoomed in), airports with their runways, navaids, SIDs, STARs, holds (of the runways in use, or *All runways*), aircraft labels |
| FIND | <kbd>Ctrl</kbd>+<kbd>F</kbd> | As on the radar: an aircraft (callsign, SSR code), a point, an aerodrome (ICAO, IATA, name), an airway, an area, a sector, a coordinate or `POINT/BRG/DIST`. Aircraft are selected and centred; the rest is marked until removed |
| Geographic cursor | <kbd>Q</kbd> | The cursor shows its coordinates (degrees, minutes, seconds); a click drops a marked coordinate (<kbd>Shift</kbd>-click keeps the cursor on) |

Other keys: <kbd>Delete</kbd> removes the selection, <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd>
undo / redo, <kbd>Space</kbd> plays, <kbd>←</kbd>/<kbd>→</kbd> step the cursor. While you place an aircraft's route, the four arrows pan the map instead (<kbd>Shift</kbd> for bigger steps), so you can carry on beyond the edge of the screen; <kbd>↑</kbd>/<kbd>↓</kbd> pan at any time.

> **Placing a conflict with the RBL:** draw an RBL between the two aircraft, then drag one of them along its route
> until the CPA is where you want the conflict, and at the separation you want: 0 NM for a collision course, or
> 9 NM for a "sleeper" that a small speed change turns into a loss.

### Scenario tab

| Field | Meaning |
|---|---|
| Open / New | Open a scenario you can read, or start a new one on an airspace. For Agadir, tick the sectors controlled |
| Save, Save & open in radar | Write `scenarios/<name>.json`; the second also starts a paused exercise and opens it |
| ⤓ JSON | Download the scenario file |
| **{ } JSON** | Edit the whole scenario as JSON: paste from a script, fix a value, copy it out. *Apply* loads it (undo brings back the previous version) |
| **⤒ Import** | Open a `.json` scenario file from your computer |
| ↶ ↷ | Undo / redo |
| name | File name: lower case, digits, `_` or `-` (2–64 characters). Saving a read-only scenario needs a new name |
| title | Short human title |
| **author** | Who designed it. Empty = you, when you save |
| **complexity** | 1–5, or *not set — estimate*. The server's estimate is shown next to it |
| **traffic density** | 1–5, or *not set — estimate*. Next to it: the controlled sectors, the flights per hour in the first hour, the declared capacity and the ratio |
| duration | Seconds |
| pilot delay | Seconds before a pilot executes an instruction (default 4) |
| separation | Horizontal minimum in NM (default: the airspace's, 5 training / 7 Agadir) |
| start (UTC) | Clock at T+0: flight plans and estimates |
| description | The briefing (shown to the controller and to agents) |
| design notes | The traps (hidden from agents) |
| Weather | Seed, intensity of the next storm drawn, surface temperature, tropopause, the wind - on the Agadir airspace the **ERA5 climatology** (month, the jet that day: from the seed or −2σ … +2σ, variability 0–2; see [weather](weather.md#climatological-wind-era5)) or a profile from the wind at FL350 -, the list of storm patterns, cells and rain areas, and the full weather JSON (*Apply JSON*) |
| Expected results | The `expected` block (see [Expected and solution](scenario-json.md#expected-and-solution)); *from previews* fills it from the last preview runs |

### Generate traffic

In the Scenario tab, **Generate traffic** builds a random scenario for the sectors ticked under *Open / New*
(Agadir or Casablanca airspace). It replaces the scenario open; save it under its name or a new one.

| Parameter | Meaning |
|---|---|
| flights / h | Flights through the controlled sectors per hour, all told: the planted situations and the flights already in the sectors at the start are part of it (30 / h over an hour is 30 aircraft; over 45 minutes, 22) |
| density | 1–5: how bunched the traffic is (waves) |
| duration | Minutes |
| sequences / h, of *a*–*b* aircraft | Arrival streams of *a* to *b* aircraft converging on a sequencing fix of the sectors that have one (North, West, South, Central, East). None in Oceanic |
| overtakes / h | Same route and level, the faster one behind |
| crossings / h | Two flights at the same level whose routes cross inside the sectors, meeting at the crossing. The routes are drawn by their traffic (a route with a single crossing, like IBALU - SAMAR, is not crowded out by the long ones with many) |
| level changes / h | A pilot request to climb or descend through a crossing flight's level |
| transatlantic / h | Flights to and from North America by IBALU and SUBAL / BEXAL, in the sectors those routes cross. Westbound, mostly Moroccan departures still climbing to the level they request; eastbound, at cruise. Across the West / Oceanic boundary they are coordinated by OLDI like every flight whose route touches both sides (see [level coordination](controller.md)) |
| entry waves / h | 2–4 flights entering at the same point within a few minutes and leaving by different routes, some at the same level: mostly at VASTO (UN858 to AKUDA, NIDEB to TIGGI, direct BAROK), also SAMAR (BEXAL, NEVTU - TIGGI) |
| nearby traffic / h | Flights passing close to the ticked sectors on the neighbours' routes (the CTA's other sectors, Casablanca, Canarias, Lisbon), within 80 NM: on the picture like traffic gone to the next sector, never yours (an instruction to one is refused), not counted in *flights / h* nor in the debrief's traffic. Close to its departure it is still climbing, close to its destination descending |
| slow traffic | Share of slower aircraft: Moroccan military transports (C130, A400) at FL170–250 on the routes to or from Morocco, business jets (C550) at their own high levels elsewhere (none low over the ocean). In a sequence, the slow one ahead is a military transport on a Moroccan route, else a business jet at the arrivals' levels |
| long-haul | Share of long-haul flights (South or North America, the Gulf) on the flows that carry both kinds; a flight's type follows its length (wide-bodies long-haul, no regional jets between northern Europe and the Canaries) |
| separation | The horizontal minimum of the exercise (empty: the airspace's). Every flight enters the sectors at least this far from the traffic at its level; a planted pair is re-timed so that it meets inside, not at the boundary |
| aerodromes | For the aerodromes the ticked sectors handle (North, Central, South, East...; every scheduled Moroccan airport, Laâyoune, Dakhla, Tan-Tan and Smara included): departures and arrivals per hour. Departures take off on a SID and climb into the sectors (an airport without a published SID departs omnidirectionally: runway heading to about 1500 ft, then direct to where it joins its route); arrivals are filed to the aerodrome (by its sequencing fix when it has one). Empty: only what the random traffic brings |
| runway changes | *none*, *random* or *wind*: changes of the runway in use during the exercise, each aerodrome of the ticked sectors on its own (some, all or none of them), to the reciprocal runway; *wind* makes them likelier where the runway in use has a tailwind. **%**: the share of aerodromes that change. Departures after the change take off from the new runway, arrivals are planned for it |
| levels | The cruise band of the background traffic. Within it each flight's level follows its length: most at FL340–380, long-haul FL330–410, Morocco - Canaries and domestic lower (FL240–340) |
| seed | Same seed and parameters, same scenario; empty draws one (the last is shown) |

Every level follows the semicircular rule for the direction the flight has in the sectors (odd southbound, even northbound, below FL290 too), so same-level crossings are between flights of the same direction. The flights fly the routes that are actually planned through the Casablanca FIR: the generator builds them from the traffic flows in `atcsim/data/flows/agadir.json` (Europe - South America on UN873 / UN857 / UN866 / UN858, Europe - Canaries, Europe - West Africa through MAK and ADM, the Moroccan airports' flows, domestic), each with its real origins and destinations, as busy as its real traffic (each flow's `weight`; the Europe - Canaries flows carry most, the North Atlantic ones little) and its aerodromes weighted the same way (Gran Canaria and Tenerife South ahead of Tenerife North and La Palma, Lanzarote ahead of Fuerteventura, Casablanca ahead of Rabat). The routes follow the RAD (GMAC appendix): VASTO DCT BAROK, VASTO NIDEB TIGGI, SAMAR NEVTU TIGGI, DESUM DCT AKUDA for the Lanzarote / Fuerteventura departures, TAKAV VEDOD for their arrivals, IBALU DCT SAMAR for the Gran Canaria / Tenerife North arrivals. Each flight's departure, destination and airline mostly (three in four) come from the **city pairs really flown** in `atcsim/data/flows/agadir_pairs.json` (imported by `tools/import_flight_pairs.py` from the workbook `tools/traffic/Agadir_CTA_Flight_Plan_Routes.xlsx`: the airlines of both airports' timetables, with group codes such as Ryanair RYR / MAY / RUK, easyJet EZY from the UK and EJU from the EU, Wizz WZZ / WMT, Transavia TRA / TVF; an airline listed by one airport only is half as likely). The workbook's own routes are not flown: every flight keeps the RAD routing of its flow, transatlantic ones included. Pairs with no scheduled service are never generated (London - Gran Canaria flies from Gatwick, Barcelona to Tenerife North). Otherwise the airlines are those of the flight's ends (Royal Air Maroc and Air Arabia Maroc for Morocco, Binter and the Spanish airlines for the Canaries, the European airlines, holiday and low-cost ones included, of the other end; wide-body operators long-haul). Each route is the whole flight's: from the departure (or as far back as the airways go) through the sectors to the destination when it is in the FIR (by the airways to a STAR, then the STAR; arrivals with a sequencing fix end at the fix, where approach takes them), or as far on as the airways go for transits - never back through the controlled sectors. Flights appear 55 NM before their entry; a flight from a nearby aerodrome with SIDs takes the SID of its direction of travel (the one that ends on its route, or leads onto it in line, never one it would have to turn back from: out of Casablanca TOLSI for Europe, ESALA or SADIC east, NIKZO or FOBAC south, LACAJ south-east), is filed from where that SID joins the route and appears on the runway, climbing to the level it enters at (the result says how many). Departures and destinations follow the route: a nearby aerodrome when the route passes it, otherwise a distant one in the direction the flight comes from or goes to (North America for the IBALU - SUBAL / BEXAL flow).

A flight whose destination lies close beyond its exit (Faro, by AMSEL or OSLAD) reaches its top of descent while still yours: its pilot asks for descent before the exit (about the level of a 3:1 path at the exit point); Faro is among the airspace's descent destinations, so its descent levels are not held to the semicircular rule.

Departures from the aerodromes under the ticked sectors, but GMMN and GMAD (silent departures), ask for a **start-up clearance** before take-off (see
[the scenario reference](scenario-json.md#start-up-requests)); the Scenario tab's **Start-up requests** box turns
them off, and **Runway changes** (*GMMN 17L 25:00, GMMX 28 40:00*) sets changes by hand. The result counts the
runway changes and start-up requests generated.

Each planted aircraft is given a point and a time (be *here* at *T+x*), and the designer works back to when it appears. The situations are planted first, at the time and place they meet; the background traffic is then cleared of any
conflict not planned, and flights that cannot fly their route are dropped. The result lists what was planted, and the
design notes describe each situation (who, where, when) for the debrief.

### Selected tab

For an **aircraft**: callsign, type, level, route, dep / dest, timing (*at cursor T+…* keeps the same point at the
cursor time), filed Mach, RVSM, formation size, direction override and the flight-plan fields. Then come its
**requests**, added at the cursor time:

- *request descent*;
- *level*;
- *direct*;
- *speed* (Mach under 2, else IAS in kt);
- *weather deviation*: around the real weather, as an offset from track, or as a fixed heading, with a distance
  and *then direct*.

Then its **events**: *unable RVSM*, *RVSM restored*. Cells, rain areas and areas have their own forms.

### Check and preview

After every edit the server rebuilds the scenario (half a second later) and reports:

![Check & preview after a naive-approve run: planned conflicts, then the losses and requests of the run, with the real trajectories on the map](/static/docs/d_check.jpg "Check & preview after a naive-approve run: planned conflicts, then the losses and requests of the run, with the real trajectories on the map")

- **errors**: the scenario cannot be built (unknown point, route never entering the controlled area…);
- **warnings**: semicircular rule, one-way airways flown backwards, entry-only / exit-only points misused,
  non-RVSM aircraft in RVSM airspace, a level above the aircraft's maximum;
- **planned conflicts**: losses of separation between the filed trajectories (routes, levels, wind included);
- **traffic load and ratings**: flights in the first hour against the declared capacity, and the estimated
  complexity and density.

**Run passive** and **Run naive approve** fly the scenario in fast time with the full physics, weather and pilot
model. *Passive* never answers anything; *naive approve* approves every pilot request at once. The map and the
timeline can then show the real trajectories and the losses. That is how you check that a request is a trap:
approved as asked, it must cause a loss.

## Building scenarios with JSON

### Minimal scenario

```json
{
  "name": "my_first_crossing",
  "title": "Two aircraft crossing at FL350",
  "airspace": "agadir",
  "control": ["WS"],
  "duration_s": 1800,
  "aircraft": [
    {"callsign": "RAM1311", "type": "B738", "route": ["NIPRI", "ROLRO"], "fl": 350,
     "reach": {"waypoint": "ROLRO", "time": 1300}},
    {"callsign": "TAP1478", "type": "A20N", "route": ["TAKAV", "VEDOD", "TERTO"], "fl": 350,
     "reach": {"waypoint": "VEDOD", "time": 1020}}
  ]
}
```

Paste it into **{ } JSON** and *Apply*. The designer shows both aircraft, the planned conflict (if any) and the
traffic load. Fix the timings until the conflict is where you want it, then **Save**.

### Workflow with JSON tools

1. **Start from something that works**: open a library scenario, then **⤓ JSON** (or `GET /scenarios/<name>/raw`).
2. **Transform it** with your tool. Some examples follow.
3. **Paste or import** it back (**{ } JSON** / **⤒ Import**), read the check, run the previews, **Save** under a new
   name.

![The { } JSON editor: the whole scenario as JSON, to edit, paste or copy](/static/docs/d_json.jpg "The { } JSON editor: the whole scenario as JSON, to edit, paste or copy")

Shift every flight and request by two minutes with `jq`:

```bash
jq '.aircraft |= map(if .reach then .reach.time += 120 elif .entry_time then .entry_time += 120 else . end)
    | .requests |= map(.time += 120) | .name = "w01_shifted"' w01_west_scattered_cb.json > w01_shifted.json
```

Thin the traffic: keep the planted flights (here, those timed with `reach`), keep every second background flight,
and drop the requests of the flights removed:

```bash
jq '(.aircraft | map(select(.reach))) as $planted
    | (.aircraft | map(select(.reach | not)) | to_entries | map(select(.key % 2 == 0) | .value)) as $bg
    | .aircraft = $planted + $bg
    | [.aircraft[].callsign] as $cs
    | .requests |= map(select(.callsign as $c | $cs | index($c)))
    | .name = "w01_lighter"' w01_west_scattered_cb.json > w01_lighter.json
```

Generate a stream of flights on a route with Python:

```python
import json, random
scn = json.load(open("p01_ws_two_merges.json"))
rnd = random.Random(7)
for k in range(10):                                   # 10 southbound flights on UN857, 6 minutes apart
    scn["aircraft"].append({
        "callsign": f"EXS{100 + k}", "type": rnd.choice(["B738", "A320", "B38M"]),
        "route": ["KORNO", "ABODA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"],
        "fl": rnd.choice([330, 350, 370, 390]),       # odd levels: southbound
        "entry_time": -600 + 360 * k,                 # negative = already in the sector at T+0
        "dep": "EGCC", "dest": "GCLP"})
scn["name"] = "p01_more_traffic"
json.dump(scn, open("p01_more_traffic.json", "w"), indent=1)
```

### Checking and saving from a script (API)

Scripts authenticate with the `X-API-Key` header (the server's `ATC_API_KEY`) and use the designer's back end:

```bash
KEY=…; URL=https://your-server
# check: errors, warnings, planned conflicts, traffic load, estimated ratings
curl -s -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d "{\"scenario\": $(cat out.json)}" $URL/designer/compile | jq '{ok, error, warnings, traffic_load, ratings, n: (.planned_conflicts|length)}'
# fly it in fast time with a reference policy
curl -s -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d "{\"scenario\": $(cat out.json), \"policy\": \"naive_approve\"}" $URL/designer/preview | jq '.losses'
# save (overwrite an existing file of yours)
curl -s -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
     -d "{\"scenario\": $(cat out.json), \"name\": \"p01_more_traffic\", \"overwrite\": true}" $URL/designer/save
```

`/designer/save` refuses a scenario that does not build, and fills `traffic_load` (and the author, when empty).
Files can also be copied straight into the server's `scenarios/` folder. The list reads that folder on every
request.

### The repository's scenario builder

The library is generated by `tools/build_scenarios.py`. Each scenario is a Python function that places the planted
aircraft geometrically, adds background traffic, and **verifies** the traps in fast time. Helpers:

| Helper | Does |
|---|---|
| `ac(cs, type, route, fl, mach, at=…, t=…, entry=…, **fpl)` | One aircraft: at a waypoint or point at time `t`, or entering at `entry` |
| `req(t, cs, type, value, reason)` | A scripted pilot request |
| `meet(scn, mover, route_leg)` / `meet_alt(scn, cs, legs)` | Where and when an aircraft, flown alone, crosses a leg (and at what altitude): put the other aircraft exactly there |
| `when_alt(scn, cs, alt_ft)` | When and where an aircraft passes an altitude (for climb / descent traps) |
| `put(scn, cs, at=…, t=…)`, `set_fl`, `parity_level` | Re-time an aircraft, set its level, nearest level of the right parity |
| `dense(scn, flows, seed, target=0.95, scale=1.0)` | Background traffic on the published flows (`tools/agadir_traffic.py`), thinned to `target` × the declared capacity, de-conflicted so only the planted conflicts remain |
| `check(scn)` | Performance, semicircular rule, one-way routes, the `expected` block against fast-time runs |

```bash
python tools/build_scenarios.py p01 p02        # rebuild and check some scenarios
python -m tools.fill_flightplans               # complete the flight plans of every scenario file
```

## File format

Every field of a scenario file — types, defaults, limits, the rules the builder checks, timing and how to plant
conflicts, complete examples, a prompt template for language models, and the generated list of every point, route
and aircraft type — is on its own page: **[Scenario JSON reference](scenario-json.md)**. The JSON Schema is at
[/api/help/scenario.schema.json](/api/help/scenario.schema.json).

## Difficulty ratings

**Density** is measured against the **declared capacity** of the controlled sectors (Agadir, flights per hour):
WN 58, WS 43, OCN 56, OCS 65, WW (WN+WS) 48, OCE (ON+OS) 51, WW-OCE (all four) 47.

The load is the number of flights inside the controlled sectors at some time during the first hour, estimated
along their routes. It is scaled to an hour when the scenario is shorter.

| Density | Load / capacity |
|---|---|
| 1 | under 50 % |
| 2 | 50–70 % |
| 3 | 70–90 % |
| 4 | 90–105 % |
| 5 | over 105 % |

Aim for a **slight overshoot at most** (about 110–115 %) for the hardest scenarios. More than that is not realistic
traffic. The training airspace has no declared capacity, so its density is estimated from the flights per hour.

**Complexity** is your judgement. The estimate weighs the density, requests, areas, events, weather and planted
conflicts. As a guide:

| Complexity | Typically |
|---|---|
| 1 | One isolated problem, no weather |
| 2 | Two or three independent conflicts, one simple request, settled weather |
| 3 | Sleepers (overtaking), request traps, isolated storms, one sector |
| 4 | Storms with deviations, arrival sequences with LoA, unable-RVSM, non-RVSM aircraft, near capacity |
| 5 | Several of those at once, severe weather, band-boxed sectors, formations, areas activating |

A scenario's ratings travel with every exercise run from it, and show in the controllers' history and statistics.
