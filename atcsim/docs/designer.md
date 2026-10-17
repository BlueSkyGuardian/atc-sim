# Scenario designer and JSON format

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

Timeline controls: use the wheel to move the cursor, <kbd>Ctrl</kbd>+wheel to zoom, and <kbd>Shift</kbd>+wheel to
scroll the lanes. **▶** plays at the chosen speed (×10–×120). Markers: ◆ the aircraft's timing point, ● pilot
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
| ↔ RBL | <kbd>B</kbd> | Click two aircraft: bearing, range, ΔFL at the cursor time and the **CPA along their trajectories** (follows the timeline) |

Other keys: <kbd>Delete</kbd> removes the selection, <kbd>Ctrl</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd>
undo / redo, <kbd>Space</kbd> plays, <kbd>←</kbd>/<kbd>→</kbd> step the cursor.

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
| Weather | Seed, intensity of the next storm drawn, surface temperature, tropopause, wind at FL350, the list of storm patterns, cells and rain areas, and the full weather JSON (*Apply JSON*) |
| Expected results | The `expected` block (see [Expected results](#expected-results-and-checks)); *from previews* fills it from the last preview runs |

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

## File format reference

Everything is optional unless marked **required**. Coordinates in the training airspace are NM from the centre
(x east, y north). In Agadir, use point names, or `[x, y]` in the airspace's NM plane, `{"lat": …, "lon": …}`, or
an AIP string such as `"320500N 0145000W"` where noted.

### Top level

| Key | Type | Meaning |
|---|---|---|
| `name` **required** | string | File name, `[a-z0-9_-]{2,64}` |
| `title` | string | Short title |
| `author` | string | Designer's name |
| `complexity` | 1–5 | Designer's rating; absent = estimated |
| `density` | 1–5 | Designer's rating; absent = estimated from `traffic_load` |
| `traffic_load` | object | Written by the server when saving: `{"config", "unit_name", "capacity", "flights_per_hour", "ratio", "density"}` |
| `description` | string | Briefing for the controller / agents |
| `design_notes` | string | The traps (hidden from agents) |
| `airspace` | `"training"` \| `"agadir"` | Default `training` |
| `control` | list of sector ids | Agadir: the sectors controlled (`WN`, `WS`, `ON`, `OS`); default all four band-boxed |
| `duration_s` | number | Default 1800 |
| `pilot_delay_s` | number | Seconds before an instruction is executed (default 4) |
| `start_utc` | `"HH:MM"` | Clock at T+0 (default 12:00) |
| `magnetic_variation` | degrees | For the Mode S magnetic heading; negative = west (Agadir default −1) |
| `separation` | `{"horizontal_nm": 6}` | Overrides the airspace's horizontal minimum |
| `oldi` | `{"abi_s": 1200, "act_s": 600, "rev_limit_s": 180}` | OLDI timings |
| `auto_assume` | bool | Assume aircraft automatically on first contact (default false) |
| `auto_routes` | bool | Agadir: complete routes beyond the CTA from the airspace's onward-route rules (default true) |
| `pre_entry_nm` / `checkin_nm` | NM | Aircraft appear 30 NM before their entry point and call 10 NM before it (defaults) |
| `post_exit_display_s` | s | How long a transferred aircraft stays painted (default 240) |
| `sector` | `{"lower_fl", "upper_fl"}` | Training airspace: vertical limits |
| `extra_waypoints` | `{"NAME": [x, y]}` | Training airspace: extra points |
| `weather` | object | See [Weather](#weather) and the [weather modelling](weather.md) page |
| `areas` | list | See [Areas](#areas) |
| `aircraft` **required** | list | See [Aircraft](#aircraft) |
| `requests` | list | See [Requests](#requests) |
| `events` | list | See [Events](#events) |
| `aerodromes`, `sequencing` | lists | Arrival aerodromes and sequencing fixes; Agadir has defaults (GCRR via TERTO, GMMN via MARJA and SALCA). See [Arrivals](aircraft.md#arrivals-and-sequencing) |
| `expected` | object | What the reference policies must produce (see [Expected results](#expected-results-and-checks)) |
| `solution` | list | A scripted reference solution (see below) |

### Aircraft

```json
{"callsign": "RAM205", "type": "B789", "route": ["BEXAL", "IBALU", "SALCA"], "fl": 370,
 "mach": 0.84, "load": 0.4, "reach": {"point": [12.3, 140.2], "time": 700},
 "dep": "CYUL", "dest": "GMMN", "rvsm": true, "equipment": "SDE2E3FGHIJ2J3J4J5M1RWXY/LB1D1",
 "remarks": "TCAS", "fpl": {"ssr": "4231", "reg": "CNRGA"}}
```

| Key | Meaning |
|---|---|
| `callsign` **required** | Unique |
| `type` **required** | ICAO type (see `GET /types`): 36 civil and 12 military types |
| `route` **required** | Point names, in flying order. At least two, or one with `start` |
| `fl` **required** | Filed / cruise level. It must follow the semicircular rule: odd levels southbound (090–269), even northbound |
| timing, **exactly one of** | `"entry_time": s` (at the first point), `"reach": {"waypoint": "IBALU", "time": s}`, `"reach": {"point": [x, y], "time": s}` (a point on the route), or `"start": {"point": [x, y], "time": s}` (anywhere, then the route). Negative times put the aircraft further along at T+0 |
| `mach` | Filed cruise Mach (default: the type's typical one) |
| `load` / `mass_kg` | Mass: OEW + load × (MTOW − OEW), default 0.5 |
| `arrival` | `false` = overflight even if it exits at a sequencing fix |
| `direction` | `"southbound"` / `"northbound"`: overrides the direction from the route (semicircular rule) |
| `dep`, `dest` | ICAO aerodromes |
| `rvsm` | `false` = not RVSM approved (item 10 without `W`) |
| `military` | `true` = state / military (`MIL` tag) |
| `formation` | `{"size": 4, "types": "4xF16", "extent_nm": 1.0}`: only the leader is shown; 2000 ft from everyone |
| `equipment`, `remarks` | Item 10 and RMK |
| `route_after` | Points after the CTA exit (flight plan only) |
| `auto_route` | `false` = do not complete the route outside the CTA |
| `pre_entry_nm`, `checkin_nm` | Per aircraft |
| `fpl` | Flight-plan fields: `ssr`, `reg`, `code` (Mode S address), `eobt`, `altn`, `equipment`, `opr`, `pob`, `rmk`, `sts`, `pbn`. Anything missing is generated deterministically from the callsign |

### Requests

```json
{"time": 1240, "callsign": "RAM1416", "type": "altitude", "value": 380, "reason": "requesting cruise level"}
```

| `type` | `value` |
|---|---|
| `altitude` | Flight level (it must follow the semicircular rule, unless descending to destination) |
| `direct` | Point name |
| `speed` | Mach if under 2, else IAS in kt |
| `descent` | Optional level used if simply approved (default: the LoA level, or about 4000 ft lower); any descent clearance answers it |
| `weather_deviation` | Instead of `value`: `{"side": "either"\|"left"\|"right"}` (the pilot's own plan around the real weather), or `{"side": "left", "offset_deg": 30, "distance_nm": 20}`, or `{"heading": 250, "distance_nm": 15}`; optional `"then_direct": "IBALU"` |

A scripted request is made only once the aircraft is assumed. If it is due earlier, the pilot asks right after
the assume. The simulated pilots add their own requests (weather, turbulence, descent).

### Events

```json
{"time": 900, "callsign": "RYR7HB", "type": "rvsm_unable", "reason": "altimeter disagreement"}
```

`rvsm_unable` (the pilot reports it; the pair minimum becomes 2000 ft in RVSM airspace) and `rvsm_restored`.

### Areas

```json
{"id": "R90", "name": "Naval exercise", "kind": "restricted",
 "polygon": ["320500N 0145000W", "322500N 0141500W", "315000N 0135500W", "313000N 0143000W"],
 "lower_fl": 0, "upper_fl": 450, "active": false,
 "schedule": [{"time": 900, "active": true}, {"time": 3300, "active": false}]}
```

`kind`: `danger`, `restricted` or `prohibited`. Use `polygon` (points as `[x, y]`, `{"lat", "lon"}` or AIP
strings) or `"circle": {"center": …, "radius_nm": 8}`. The instructor can also switch areas by hand during an
exercise.

### Weather

```json
"weather": {
  "seed": 101,
  "temperature": {"surface_c": 24, "tropopause_fl": 390, "lapse_c_per_kft": 2.1},
  "wind": {"profile": [{"fl": 0, "dir": 200, "kt": 10}, {"fl": 350, "dir": 250, "kt": 55}, {"fl": 800, "dir": 90, "kt": 20}],
           "jet": {"core_fl": 340, "dir": 268, "kt": 65, "axis_point": [0, 120], "half_width_nm": 90,
                   "depth_below_ft": 11000, "depth_above_ft": 8000},
           "noise_kt": 5, "noise_scale_nm": 40},
  "convection": [{"regime": "scattered", "intensity": "strong", "count": 8, "region": [-40, -120, 60, -40]},
                 {"regime": "squall_line", "intensity": "severe",
                  "line": {"center": [-20, -95], "length_nm": 170, "orient_deg": 120, "gaps": 0,
                           "gap_list": [[-35, 22], [40, 20]], "spacing_nm": 8}}],
  "cells": [{"id": "CB1", "x": -5, "y": 0, "at_time": 300, "top_fl": 460, "dbz": 59, "radius_nm": 5,
             "lifetime_s": 6000, "age_s": 1800, "motion": {"toward": 85, "kt": 20}}],
  "stratiform": [{"center": [-140, -60], "radius_nm": [60, 30], "orient_deg": 30, "dbz": 32}],
  "pilot": {"hazard_dbz": 30, "margin_nm": 6, "severe_dbz": 40, "severe_margin_nm": 10,
            "request_range_nm": 60, "self_deviation_range_nm": 15}
}
```

Every field is explained on the [weather modelling](weather.md) page. `convection` may be one pattern or a list.
Regimes are `isolated`, `scattered`, `squall_line` and `embedded`; intensities are `moderate`, `strong` and
`severe`. The `seed` makes the random parts reproducible: the same seed gives the same storms.

### Expected results and checks

```json
"expected": {
  "passive": {"los": true, "pairs": [["EXS64M", "TOM3KD"]], "allow_left_sector": true},
  "naive_approve": {"los": true, "pairs": [["CFG7RA", "RAM1416"]], "rvsm_violation": true}
}
```

For each policy (`passive`, `naive_approve`):

| Key | Checked |
|---|---|
| `los` | Whether there is any loss of separation |
| `pairs` | These pairs must lose separation (others may too) |
| `infringement` | Whether an active area is infringed |
| `seq_level` / `seq_spacing` | Whether LoA level / spacing violations occur at the sequencing fixes |
| `rvsm_violation` | Whether a non-approved aircraft enters RVSM airspace |
| `wx_request` | A callsign that must make a weather / ride request |
| `allow_left_sector` | Aircraft may leave the sector off their planned exit (dense traffic, weather) |

A `solution` is a scripted reference controller. The build checks that it gives no loss, no penetration, no
infringement and no violation:

```json
"solution": [
  {"at": 600, "do": [{"callsign": "KLM81", "type": "altitude", "fl": 250}]},
  {"on_request": "RAM508", "type": "altitude", "do": [{"reply": "approved"}]},
  {"on_checkin": "BAW3J", "delay_s": 30, "do": [{"callsign": "BAW3J", "type": "speed", "mach": 0.76}]}
]
```

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
