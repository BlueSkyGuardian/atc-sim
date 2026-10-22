# Scenario JSON reference

This page describes **every field** of a scenario file, what it does, its type, default and limits, and the rules
the simulator checks. It is written to be complete enough to hand to a person or to a language model that has
never seen the simulator. The appendix at the end, generated from the simulator's own data, lists every point
name, route and aircraft type you can use.

> **For a language model:** paste this whole page (button *Copy page as Markdown* at the top) into the
> conversation, then ask for a scenario. Use the [prompt template](#prompt-template-for-a-language-model). The
> machine-readable JSON Schema is at [/api/help/scenario.schema.json](/api/help/scenario.schema.json), and the
> reference data at [/api/help/reference.json](/api/help/reference.json).

## How a scenario works

A scenario is a **script for one exercise**: which airspace and sectors are controlled, the weather, the
aircraft (route, level, and when they are where), the pilot requests and events that happen during the exercise,
and optionally the problems it is designed to contain.

- **Time** is in seconds from the start of the exercise, **T+0**. The exercise lasts `duration_s`.
- **Positions** are in nautical miles (NM) on a flat plane: **x east, y north**. In the training sector the
  origin is the sector's centre. In the Agadir CTA it is 33°N 11°30'W. You rarely need coordinates, because routes
  use **point names**.
- **Levels** are flight levels (FL350 = `350`). **Speeds** are Mach numbers (`0.78`) or knots of indicated airspeed.
- **Headings and bearings** are degrees true (grid), 001–360.
- An aircraft flies its **route** (a list of point names) at its **filed level** at its **cruise Mach**, in the
  wind. You decide *when* it is *where* with one timing field. The simulator works out where it is at T+0 and when
  it appears.
- Aircraft **appear** 30 NM before their entry into the controlled sectors, still with the previous unit. They
  **call** the controller 10 NM before the boundary. Aircraft already inside at T+0 start assumed.
- The **controller** then gives clearances. The simulator flies the aircraft with real performance and pilots who
  ask for weather deviations, ride levels and descent by themselves.

Keys that the simulator does not know are **ignored**. This means a typo in an optional key silently does
nothing, so check the spelling against this page.

## The smallest valid scenario

```json
{
  "name": "two_aircraft",
  "aircraft": [
    {"callsign": "RAM101", "type": "B738", "route": ["W1", "E2"], "fl": 350, "entry_time": 0},
    {"callsign": "BAW202", "type": "A320", "route": ["N2", "S3"], "fl": 350, "entry_time": 30}
  ]
}
```

That is a training-sector scenario (the default airspace), 30 minutes long (the default), with two aircraft at the
same level on crossing routes. Left alone, they pass 2 NM apart where the routes cross: a loss of separation
for the controller to prevent. Only `name` and `aircraft` are required. Each aircraft needs `callsign`, `type`,
`route`, `fl` and **exactly one** timing field.

## Top-level fields

| Field | Type | Default | What it is for |
|---|---|---|---|
| `name` | string, **required** | — | The file name and identifier. Lower-case letters, digits, `_` and `-`, 2–64 characters (`p07_my_scenario`) |
| `title` | string | `""` | Short human title shown in lists |
| `author` | string | the saver | Who designed it |
| `complexity` | integer 1–5 | estimated | Difficulty of the problems: 1 very low … 5 very high |
| `density` | integer 1–5 | estimated | Traffic density against capacity (see [Density](#difficulty-complexity-and-density)). Normally leave it out: the server measures it |
| `traffic_load` | object | — | **Written by the server** when saving (flights per hour, capacity, ratio). Do not write it |
| `description` | string | `""` | The **briefing** the controller (and an agent) reads before starting: sectors open, traffic, weather, what to expect. No spoilers |
| `design_notes` | string | `""` | The **traps** and the intended solution, for instructors. Hidden from the controller and from agents |
| `airspace` | `"training"` or `"agadir"` | `"training"` | Which airspace |
| `control` | list of sector ids | the four Agadir sectors | Agadir airspace (the whole Casablanca FIR): the sectors the controller works, all in one control area. CTA Agadir: `["WS"]`, `["WN","WS"]` (WW), `["ON","OS"]` (OCE), or all four. CTA Casablanca: `["N"]`, `["E"]`, `["C"]`, `["SS"]` or any combination. The other sectors of that control area are adjacent sectors; the other control area is the adjacent unit (Casablanca ACC or Agadir ACC) |
| `duration_s` | number ≥ 300 | 1800 | Length of the exercise, in seconds |
| `pilot_delay_s` | number ≥ 0 | 4 | Seconds between a clearance and the start of its execution |
| `start_utc` | `"HH:MM"` | `"12:00"` | UTC time at T+0 (flight plans, estimates, the clock) |
| `magnetic_variation` | number | −1 (Agadir), 0 | Degrees, negative = west. Only for the Mode S magnetic heading |
| `separation` | `{"horizontal_nm": n, "exit_spacing_nm": m}` | airspace's | Horizontal radar minimum, 3–20 NM (Agadir 7, training 5); `exit_spacing_nm`: the letter of agreement's in-trail spacing at an exit for aircraft at levels not vertically separated (default 10) |
| `oldi` | `{"abi_s", "act_s", "rev_limit_s"}` | 1200, 600, 180 | OLDI coordination timings (seconds before the boundary) |
| `auto_assume` | boolean | `false` | `true` = aircraft are assumed automatically when they call |
| `auto_routes` | boolean | `true` | Agadir: complete each route beyond the CTA from the onward-route rules (flight-plan realism) |
| `pre_entry_nm` | number | 30 | How far before its entry point an aircraft appears |
| `checkin_nm` | number | 10 | How far before its entry point an aircraft calls |
| `post_exit_display_s` | number | 240 | How long a transferred aircraft stays painted |
| `sector` | `{"lower_fl", "upper_fl"}` | airspace's | Training sector only: vertical limits (default FL250–FL460) |
| `extra_waypoints` | object | `{}` | Extra named points usable in routes: `{"MID01": [0, 0], "XAVI1": "312000N 0120000W"}` |
| `weather` | object | calm ISA | See [Weather](#weather) |
| `areas` | list | `[]` | Danger / restricted / prohibited areas. See [Areas](#areas) |
| `published_areas` | `false` or object | prohibited areas active | Agadir: the AIP areas (ENR 5.1 GMP / GMR / GMD), always loaded. `{"GMD25": true}` activates one, `{"GMR09": {"active": false, "schedule": [{"time": 900, "active": true}]}}` schedules one, `{"GMP02": false}` switches one off; `false` leaves them all out. See [Areas](#areas) |
| `aircraft` | list, **required** | — | See [Aircraft](#aircraft) |
| `requests` | list | `[]` | Scripted pilot requests. See [Requests](#requests) |
| `events` | list | `[]` | Scripted events (loss of RVSM). See [Events](#events) |
| `aerodromes` | list | airspace's | Arrival aerodromes. See [Arrivals](#aerodromes-and-sequencing) |
| `sequencing` | list | airspace's | Sequencing fixes with the letter of agreement. See [Arrivals](#aerodromes-and-sequencing) |
| `expected` | object | — | What must happen with no controller / with every request approved: the build check. See [Expected](#expected-and-solution) |
| `solution` | list | — | A scripted reference controller. See [Expected](#expected-and-solution) |

## Aircraft

```json
{
  "callsign": "RAM205", "type": "B789", "route": ["BEXAL", "IBALU", "SALCA"], "fl": 370,
  "reach": {"waypoint": "IBALU", "time": 900},
  "mach": 0.84, "load": 0.4, "dep": "CYUL", "dest": "GMMN",
  "rvsm": true, "remarks": "TCAS", "fpl": {"reg": "CN-RGA", "pob": 250}
}
```

| Field | Type | Default | Meaning and rules |
|---|---|---|---|
| `callsign` | string, **required** | — | Unique in the scenario, upper case: ICAO operator + number (`RAM205`, `EZY64LK`), a registration (`N215CJ`), or a military callsign (`VIPER21`). The operator prefix drives the generated flight plan (operator name, registration country) |
| `type` | string, **required** | — | ICAO aircraft type from the [appendix](#aircraft-types). An unknown type is an error |
| `route` | list of point names, **required** | — | Points in **flying order**. At least 2 (or 1 with `start`). See [Routes](#routes) |
| `fl` | integer, **required** | — | Filed cruise level. It should follow the [semicircular rule](#levels-and-the-semicircular-rule) and be within the type's capability (above the type's ceiling is an error; above its maximum level at this weight is a warning) |
| **timing** | — | — | **Exactly one** of `entry_time`, `reach`, `start`. See [Timing](#timing) |
| `mach` | number | type's typical | Filed cruise Mach (0.30–0.95) |
| `ias_kt` | number | — | Fly this indicated airspeed instead of the Mach schedule (slow aircraft, holding a speed) |
| `load` | number 0–1 | 0.5 | Mass = OEW + load × (MTOW − OEW). Lighter aircraft climb higher (long-haul at the start of a crossing: ~0.6–0.8; end of flight: ~0.3) |
| `mass_kg` | number | — | Mass in kg instead of `load`; must be between OEW and MTOW |
| `rfl` | integer | — | Requested final level of a departure climbing through the sector: the pilot asks for it on first contact, and it is the flight plan's cruise level. Departures from GMMN without `rfl` get one from their performance and direction (the highest few levels they can reach at their weight) |
| `cfl` | integer | — | Cleared level at the start, different from `fl`: a departure released by approach climbing (e.g. `"fl": 150, "cfl": 190` with a `start` at a TMA point, and an `rfl` it asks for on first contact). Casablanca departures from GMMN, GMME, GMMX, GMAD and GMMZ ask for their final level |
| `dep`, `dest` | ICAO codes | generated | Departure and destination aerodromes (flight plan, onward routing, arrival behaviour). An aircraft whose route ends at a sequencing fix is an arrival to that fix's aerodrome |
| `arrival` | boolean | `true` | `false` = an overflight even if the route ends at a sequencing fix (no descent request, no LoA) |
| `auto_descent_request` | boolean | `true` | arrival: `false` = the pilot asks for descent only when a scripted `"descent"` request says so (to set who asks first in a wave); refused, it asks again 90 s later |
| `direction` | `"southbound"` / `"northbound"` | from the route | Overrides the direction used for the semicircular rule |
| `rvsm` | boolean | type's default | RVSM approval. `false` for an aircraft not approved (ferry flights, old aircraft). In RVSM airspace (FL290–FL410) it needs 2000 ft and its presence there is a violation |
| `military` | boolean | type's category | State / military flight (`MIL` tag, `STS/STATE`) |
| `formation` | integer or object | — | `{"size": 4, "types": "4xF16", "extent_nm": 1.0}`, or just `4`. A formation is shown as one track (the leader), is military, is never RVSM approved, and needs 2000 ft (or the horizontal minimum + `extent_nm`) from everyone |
| `equipment` | string | generated | ICAO item 10 (`"SDE2E3FGHIJ1RWY/LB1"`). If the aircraft is RVSM approved the part before `/` **must contain `W`**. A data link code `J1`…`J7` makes it CPDLC equipped (leave them out for a voice-only aircraft). The part after `/` gives the surveillance: `L`/`H` = Mode S enhanced (downlinked data), `S`/`E`/`P`/`I`/`X` = elementary, `A`/`C` = Mode A/C |
| `remarks` | string | `""` | ICAO item 18 RMK text |
| `route_after` | list of names | — | Points after the CTA exit, for the flight plan only |
| `auto_route` | boolean | `auto_routes` | `false` = do not complete this route beyond the CTA |
| `pre_entry_nm`, `checkin_nm` | numbers | top-level values | Per aircraft |
| `fpl` | object | generated | Flight-plan fields. Anything missing is generated deterministically from the callsign: `ssr` (4 octal digits; codes must be unique, duplicates are renumbered), `reg`, `code` (Mode S address, 6 hex), `eobt` (`"HHMM"`), `altn`, `equipment`, `opr`, `pob`, `rmk`, `sts`, `pbn`, `sel`, `dof`, `type` (S scheduled, N charter, G general aviation, M military) |

### Routes

- Use **point names** from the appendix ([Agadir](#agadir-cta-airspace-agadir) or
  [training](#training-sector-airspace-training-the-default)), or names you define in `extra_waypoints`. Names
  are case-insensitive. Coordinates cannot be put directly in a route: define a named point first.
- The route must **enter the controlled sectors and have at least one leg inside them**. The first route point on
  the boundary (or inside) is the **entry point**, the last one is the **exit point**. Points before the entry or
  after the exit are flown by the adjacent units (and shown in the flight plan).
- **Agadir:**
  - start at a point whose role is *entry* (or *entry / exit*), or outside the CTA;
  - end at a point whose role is *exit* (or *entry / exit*), or outside;
  - never fly a one-way airway, RAD routing or standard route backwards. Breaking these rules is a warning in the
    designer and an error in the library build.
  - Free routing between CTA points is allowed (no airway needed), but realistic scenarios use the published
    airways and RAD routings listed in the appendix.
  - Where a route crosses the boundary of the controlled sectors between two points, a transfer point is
    generated automatically (named `C` + two letters of each neighbour).
- **Training sector:** routes go from one edge point to a point on another edge (`["N1", "S3"]`, `["W2", "E1"]`).
  E1 and S4 are arrival (sequencing) fixes.

### Timing

Give **exactly one** of these. The simulator flies the route backwards in the 3D wind to place the aircraft:

| Field | Meaning | Use it for |
|---|---|---|
| `"entry_time": 120` | The aircraft is at its **first route point** at T+120 | Simple streams of traffic |
| `"reach": {"waypoint": "IBALU", "time": 900}` | It is **at that point** of its route at T+900 (any point of the route, even outside the CTA) | Planting a conflict at a fix: two aircraft at the same fix at the same time |
| `"reach": {"point": [12.3, 140.2], "time": 700}` | It is **at that position** at T+700. The position must be within 1 NM of the route | Planting a conflict where two routes cross between fixes |
| `"start": {"point": [x, y], "time": 0}` | It **starts at that position** at that time, then flies its route (which then needs only one point) | A departure appearing inside the sector, an aircraft already somewhere at T+0 |

Times may be **negative**: `"entry_time": -600` means the aircraft entered 10 minutes before T+0. It is already
further along its route when the exercise starts, so the sector is not empty at T+0. An aircraft that would have
already left the sector at T+0 is an error.

**Planting a conflict.** Two aircraft at the **same level** that reach the **same point** at the **same time** will
collide. Separation is lost when they are closer than the horizontal minimum and the vertical minimum together.

- **Merge at a fix:** give both `"reach": {"waypoint": "VEDOD", "time": 2500}`, one with time 2525 (25 s apart is a
  certain loss: about 3 NM).
- **Crossing between fixes:** put both at the crossing position with `"reach": {"point": [x, y], ...}`. The
  appendix gives the coordinates of every point. The crossing of two straight legs is plain geometry.
- **A "sleeper"** (a conflict that is not obvious at first): same route and level, the faster one behind
  (`"mach": 0.86` catching `"mach": 0.80`, 12 NM behind). The loss comes 15–20 minutes later.
- **A request trap:** the conflict happens only if the controller approves a request. For example, an aircraft at
  FL340 asks for FL380 at T+1230, and another aircraft at FL350 passes nearby at the time the first would climb
  through FL350.
- **Not a conflict:** 1000 ft apart (2000 ft if one is non-RVSM inside FL290–FL410, or above FL410), or more than
  the horizontal minimum apart at their closest point.

Rules of thumb for timing:

- A jet at M0.78 at FL350 flies about 450 kt true airspeed, so 7.5 NM per minute, plus or minus the wind.
- The time between two points is the distance (from the coordinates in the appendix) divided by the ground speed.
- You do not need to be exact: write the scenario, then open it in the designer (or call `/designer/compile`).
  The check lists the **planned conflicts** it finds, and you adjust the times.

### Levels and the semicircular rule

- The direction of flight is the bearing from the **entry point to the exit point** of the controlled part.
  **Southbound** (bearing 090–269) uses **odd** levels: 290, 310, 330, 350, 370, 390, 410, 450.
  **Northbound** (270–089) uses **even** levels: 280, 300, 320, 340, 360, 380, 400, 430.
  Below FL290, both use the odd/even thousands: 250, 270 southbound; 260, 280 northbound.
- Filed levels and requested levels must follow it. Arrivals descending to their aerodrome are exempt.
- **RVSM airspace** is FL290–FL410. Above FL410 the minimum is 2000 ft.
- Agadir is simulated from FL195 to FL460. The training sector goes from FL250 to FL460.

## Requests

A request is something a pilot asks for at a given time. It is spoken on the frequency (by the pseudo-pilot in an
exercise) and appears in the controller's picture.

```json
{"time": 1240, "callsign": "RAM1416", "type": "altitude", "value": 380, "reason": "requesting cruise level"}
```

| Field | Meaning |
|---|---|
| `time` | When the pilot asks (seconds). If the aircraft is not assumed yet, it asks right after it is assumed |
| `callsign` | An aircraft of the scenario |
| `type` | `altitude`, `direct`, `speed`, `descent` or `weather_deviation` |
| `value` | Depends on the type (below) |
| `reason` | The pilot's words, appended to the request (`"turbulence"`, `"fuel economy"`, `"optimum level"`) |

| `type` | `value` | Example |
|---|---|---|
| `altitude` | Flight level, integer (semicircular rule) | `{"type": "altitude", "value": 380}` |
| `direct` | Point name | `{"type": "direct", "value": "IBALU"}` |
| `speed` | Mach if below 2, else IAS in knots | `{"type": "speed", "value": 0.74}`, `{"type": "speed", "value": 280}` |
| `descent` | Optional level; empty = the LoA level, or about 4000 ft lower | `{"type": "descent"}` |
| `weather_deviation` | An object, below | `{"type": "weather_deviation", "value": {"side": "left", "offset_deg": 30, "distance_nm": 20}}` |

Weather-deviation forms:

- `{"side": "either" | "left" | "right"}`: the pilot plans its own way around the real weather ahead (30° off track
  if there is none).
- `{"side": "left", "offset_deg": 30, "distance_nm": 20}`: a fixed offset from track (5–90°) for 3–200 NM.
- `{"heading": 250, "distance_nm": 15}`: a fixed heading.
- Optional `"then_direct": "IBALU"`: where to rejoin.

Simulated pilots also make their own requests (weather, turbulence, descent at their top of descent). Do not script
those.

## Events

```json
{"time": 900, "callsign": "RYR7HB", "type": "rvsm_unable", "reason": "altimeter disagreement"}
```

`type`: `rvsm_unable` (the pilot reports it. From then on it needs 2000 ft inside FL290–FL410) or `rvsm_restored`.

## Areas

```json
{"id": "R90", "name": "Naval exercise", "kind": "restricted",
 "polygon": ["320500N 0145000W", "322500N 0141500W", "315000N 0135500W", "313000N 0143000W"],
 "lower_fl": 0, "upper_fl": 450, "active": false,
 "schedule": [{"time": 900, "active": true}, {"time": 3300, "active": false}]}
```

| Field | Meaning |
|---|---|
| `id` **required**, `name` | Identifier (`R90`, `D55`) and name |
| `kind` **required** | `danger`, `restricted` or `prohibited` |
| `polygon` (3+ points) **or** `circle` `{"center", "radius_nm"}` | The shape. Points as `[x, y]`, `{"lat", "lon"}`, an AIP string or a point name |
| `lower_fl`, `upper_fl` | Vertical limits (default 0 and 999) |
| `active` | Active at T+0 (default `false`) |
| `schedule` | Changes over time: `[{"time": s, "active": true/false}]` |

An active area is drawn on the scope, and flying into it is an infringement. Pilots do not avoid areas by
themselves: that is the controller's job.

**Arrivals into a TMA below the sector.** In the CTA Casablanca the aerodromes are under the sectors: an arrival
whose route ends at one of the airspace's sequencing fixes inside the controlled area (LAKAM, RAVOL, BISMI for GMMN;
FAHIM, ENNAV for GMME; MABAP, OBOGA for GMMX; AGALI, OLOMA, TEKNA for GMAD; MAXOR, LARAP for GMMZ) is handed down
to approach at that fix - on its route, or within 2.5 NM of it on vectors or a direct - and scored against the
letter of agreement (FL200, 10 NM in trail, placeholders).

**Published areas (Agadir).** The airspace file holds the AIP Maroc ENR 5.1 areas: 12 prohibited (GMP 01, 02,
11, 14–22), 7 restricted (GMR 02, 04A, 05, 06, 09, 11, 12) and 26 danger areas (GMD 01–07, 11–13, 21–26, 28–38),
with their published limits (GND / SFC / MSL = 0, UNL = 999, feet and metres converted to flight levels; the
two-circle areas GMP 16 and 17 as the hull of their circles). Every scenario of the airspace has them: the
prohibited ones active (H24), the others drawn inactive (dashed, faint). Switch them with `published_areas`, and
the instructor can activate any of them during the run like a scenario area. An area only counts for the agent's
picture when it reaches the controlled levels (FL195 and above) within 30 NM of the controlled sectors; GMD 25
(MSL–FL460) for example overlaps West North near the 35°50'N boundary.

## Weather

All weather fields are optional. Without `weather`, the air is calm and at standard temperature (ISA). The
[weather modelling](weather.md) page explains what each field does physically.

```json
"weather": {
  "seed": 101,
  "temperature": {"surface_c": 24, "tropopause_fl": 390, "lapse_c_per_kft": 2.1},
  "wind": {
    "profile": [{"fl": 0, "dir": 200, "kt": 10}, {"fl": 350, "dir": 250, "kt": 55}, {"fl": 800, "dir": 90, "kt": 20}],
    "jet": {"core_fl": 340, "dir": 268, "kt": 65, "axis_point": [0, 120], "half_width_nm": 90,
            "depth_below_ft": 11000, "depth_above_ft": 8000},
    "noise_kt": 5
  },
  "convection": [
    {"regime": "scattered", "intensity": "strong", "count": 8, "region": [-40, -120, 60, -40]},
    {"regime": "squall_line", "intensity": "severe",
     "line": {"center": [-20, -95], "length_nm": 170, "orient_deg": 120, "gaps": 0, "gap_list": [[-35, 22], [40, 20]]}}
  ],
  "cells": [{"id": "CB1", "x": -5, "y": 0, "at_time": 300, "top_fl": 460, "dbz": 59, "radius_nm": 5,
             "lifetime_s": 6000, "age_s": 1800, "motion": {"toward": 85, "kt": 20}}],
  "stratiform": [{"center": [-140, -60], "radius_nm": [60, 30], "orient_deg": 30, "dbz": 32}]
}
```

| Field | Meaning |
|---|---|
| `seed` | Integer. The same seed gives the same random storms; change it to get different ones |
| `temperature.surface_c` | Surface temperature (°C). Hot days lower aircraft performance |
| `temperature.tropopause_fl` | Tropopause level (default 361) |
| `temperature.lapse_c_per_kft` | °C lost per 1000 ft (default 1.98; about 2.1 for unstable, stormy air) |
| `isa_deviation_c` | Alternative: deviation from ISA used for performance |
| `wind.profile` | Wind **from** `dir` at `kt`, by level; interpolated in between. Give several levels from 0 to 800 |
| `wind.jet` | A jet stream: core level `core_fl`, direction from `dir`, speed `kt` at the core, axis through `axis_point` [x, y], `half_width_nm`, `depth_below_ft` / `depth_above_ft`; optional `streak_length_nm`, `streak_speed_kt`, `meander_amp_nm`, `meander_wavelength_nm` |
| `wind.noise_kt`, `wind.noise_scale_nm` | Small random wind variations (a few kt over ~40 NM) |
| `convection` | One pattern or a list. `regime`: `isolated`, `scattered`, `embedded`, `squall_line` (or `none`). `intensity`: `moderate`, `strong`, `severe`. For the first three: `count` cells in `region` [x0, y0, x1, y1]. For a squall line: `line` with `center` [x, y], `length_nm`, `orient_deg` (the line's orientation), `gaps` (random gaps), `gap_list` [[position along the line from the centre in NM, width NM]], `spacing_nm` |
| `cells` | Hand-placed thunderstorms: at (`x`, `y`) at `at_time`, aged `age_s` then, living `lifetime_s`, with `top_fl`, `dbz` (core, 45–63), `radius_nm`, optional `motion` {`toward`, `kt`}, `aspect`, `orient_deg`, `id` |
| `stratiform` | Rain areas: ellipse at `center` [x, y], `radius_nm` [a, b], `orient_deg`, `dbz` (about 30), optional `motion` |
| `pilot` | Pilot weather behaviour: `hazard_dbz` (30), `margin_nm` (6), `severe_dbz` (40), `severe_margin_nm` (10), `request_range_nm` (60), `self_deviation_range_nm` (15), `radar_range_nm` |

Typical regimes, as used by the library:

- **Winter westerly:** jet about 65 kt at FL340 north of the area, wind 250–275°, surface 13 °C, tropopause FL340.
- **Summer northerly:** wind 350–020°, 35–55 kt aloft, surface 25 °C, tropopause FL380, no jet.
- **Spring convective:** south-westerly 40–55 kt aloft, surface 24 °C, lapse 2.1, with convection.

## Aerodromes and sequencing

Arrivals are aircraft whose route ends at a **sequencing fix**. They must cross it at the letter-of-agreement level
with the spacing given. Agadir and the training sector have defaults (see the appendix), so you only need these
keys to change them.

```json
"aerodromes": [{"id": "EFLD", "name": "Eastfield", "x": 97, "y": 6, "elev_ft": 300,
                "runways": [{"id": "09", "heading": 90}, {"id": "27", "heading": 270}],
                "runway_in_use": "auto", "runway_schedule": [{"time": 900, "runway": "27"}]}],
"sequencing": [{"fix": "E1", "aerodrome": "EFLD", "spacing_nm": 10, "cross_at_fl": 250, "min_fl": 250}]
```

- **Aerodrome fields:** `id`, `name`, the position (`x`/`y`, `lat`/`lon` or `coord`), `elev_ft`, `runways` (id and
  heading), `runway_in_use` (`"auto"` = into the wind, or a runway id), `runway_schedule` (runway changes),
  `final_nm` (10) and `outbound_nm` (12).
- **Sequencing fields:** `fix` (a point), `aerodrome`, `spacing_nm` (10), `cross_at_fl` or `cross_at_or_below_fl`,
  `min_fl` (the lowest level the controller may give without coordination, 250), `speed_at_fix_kt` (optional).

## Expected and solution

These are optional. They are used by the library build (`tools/build_scenarios.py`) and the designer's *Check &
preview* to prove the scenario contains the problems it claims.

```json
"expected": {
  "passive":       {"los": true, "pairs": [["EXS64M", "TOM3KD"]], "allow_left_sector": true},
  "naive_approve": {"los": true, "pairs": [["CFG7RA", "RAM1416"]], "rvsm_violation": true}
}
```

- **`passive`** is nobody giving any clearance. **`naive_approve`** is approving every pilot request instantly.
- **`los`**: whether any loss of separation happens.
- **`pairs`**: callsign pairs that must lose separation, **in alphabetical order** within each pair.
- **`infringement`**: an active area is entered.
- **`seq_level` / `seq_spacing`**: LoA level / spacing violations at the fixes.
- **`rvsm_violation`**: a non-approved aircraft is in RVSM airspace.
- **`wx_request`**: a callsign that must make a weather request.
- **`allow_left_sector`**: aircraft may leave the sector off their planned exit.

`solution` is a list of steps for a scripted controller. The build checks that it solves everything:

```json
"solution": [
  {"at": 600, "do": [{"callsign": "KLM81", "type": "altitude", "fl": 250}]},
  {"on_request": "RAM508", "type": "altitude", "do": [{"reply": "approved"}]},
  {"on_checkin": "BAW3J", "delay_s": 30, "do": [{"callsign": "BAW3J", "type": "speed", "mach": 0.76}]}
]
```

The instructions are those of the [API](api.md#instructions).

## Difficulty: complexity and density

- **`density`** is measured by the server: the flights inside the controlled sectors during the first hour against
  the sector's declared capacity (Agadir: WN 58, WS 43, ON 56, OS 65, WN+WS 48, ON+OS 51, all four 47 flights per
  hour). Under 50 % gives 1, 50–70 % gives 2, 70–90 % gives 3, 90–105 % gives 4, and over 105 % gives 5. A
  realistic hard scenario stays at about 110–115 % at most.
- **`complexity`** is the designer's judgement:
  - **1:** one isolated problem.
  - **2:** two or three independent conflicts, settled weather.
  - **3:** sleepers, request traps, isolated storms.
  - **4:** storms with deviations, arrival sequences, unable-RVSM, non-RVSM aircraft, near capacity.
  - **5:** several of these at once, severe weather, band-boxed sectors, formations, areas activating.

To fill a sector to a given density in Agadir, count the flights entering the controlled sectors in the first hour.
For WS at 70 % that is about 30 flights per hour (43 × 0.7). Spread them over the published flows, with
`entry_time` from about −1800 (so the sector is busy at T+0) to 3600.

## Checking a scenario

1. Paste the JSON into the designer: **Scenario designer → { } JSON → Apply**, or **⤒ Import** a file.
2. Read the check: **errors** (the scenario does not build), **warnings** (semicircular rule, one-way routes,
   entry/exit points, levels above the aircraft's capability, non-RVSM aircraft in RVSM airspace), **planned
   conflicts**, the **traffic load** against capacity, and the estimated ratings.
3. **Run passive** / **Run naive approve** to see what really happens with the full physics, weather and pilots.
4. **Save** under its name.

From a script, `POST /designer/compile` with `{"scenario": {...}}` returns the same check, and `POST /designer/save`
saves it (see [Building scenarios with JSON](designer.md#building-scenarios-with-json)).

Common errors and what they mean:

| Message | Fix |
|---|---|
| `unknown waypoint XYZ` | Use a name from the appendix, or define it in `extra_waypoints` |
| `route … never enters the controlled airspace` / `has no leg inside` | The route must cross the controlled sectors (check `control`) |
| `needs 'entry_time' or 'reach'` | Give one timing field |
| `reach point is n NM off its route` | The `reach.point` must be on the route (within 1 NM) |
| `reach waypoint X is not on its route` | Use a point of this aircraft's route |
| `would already have left the sector at t=0` | The time is too early (too negative) |
| `duplicate callsign` | Callsigns must be unique |
| `RVSM approved but equipment … has no 'W'` | Add `W` to `equipment`, or set `"rvsm": false` |
| `formation flights are not RVSM approved` | Remove `"rvsm": true` from the formation |
| `mass … outside OEW-MTOW` | Check `mass_kg` / `load` |
| `request for unknown callsign` | The request's callsign must be one of the aircraft |
| Warning `wrong-direction level` | Odd levels southbound, even northbound |
| Warning `flies … against its one-way direction` | Reverse the route or use another routing |
| Warning `… is an exit-only point` / `entry-only` | Start at an entry point, end at an exit point |

## A complete Agadir example

West South alone, 45 minutes, a planted merge and a request trap, settled weather. This example is checked: left
alone, EXS64M and TOM3KD lose separation at VEDOD; with every request approved, RYR81DQ also loses separation with
EZY64LK. (Five aircraft is far below the sector's capacity: a real scenario adds background traffic.)

```json
{
  "name": "ws_example",
  "title": "West South, a merge and a climb trap",
  "author": "Example",
  "complexity": 2,
  "description": "West South open alone, light traffic. Northerly flow, no storms. Southbound traffic on UN857 and TAKAV-VEDOD-TERTO to the Canaries, northbound on UN858.",
  "design_notes": "EXS64M (UN857) and TOM3KD (TAKAV-VEDOD) merge at VEDOD at FL370 25 s apart. RYR81DQ (northbound UN858, FL340) asks FL360 at T+10:00: approved as asked, it climbs to the level of EZY64LK, 4 NM behind it.",
  "airspace": "agadir",
  "control": ["WS"],
  "duration_s": 2700,
  "start_utc": "09:30",
  "weather": {
    "seed": 7,
    "temperature": {"surface_c": 25, "tropopause_fl": 380},
    "wind": {"profile": [{"fl": 0, "dir": 20, "kt": 18}, {"fl": 250, "dir": 355, "kt": 35},
                         {"fl": 350, "dir": 350, "kt": 55}, {"fl": 450, "dir": 340, "kt": 35}, {"fl": 800, "dir": 90, "kt": 22}]}
  },
  "aircraft": [
    {"callsign": "EXS64M", "type": "B738", "route": ["KORNO", "ABODA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"],
     "fl": 370, "reach": {"waypoint": "VEDOD", "time": 2100}, "dep": "EGCC", "dest": "GCRR"},
    {"callsign": "TOM3KD", "type": "B38M", "route": ["TAKAV", "VEDOD", "TERTO"],
     "fl": 370, "reach": {"waypoint": "VEDOD", "time": 2125}, "dep": "EGGW", "dest": "GCFV", "arrival": false},
    {"callsign": "RYR81DQ", "type": "B38M", "route": ["VASTO", "SULAM", "DIMSA", "IBLIV", "SUNID", "AKUDA"],
     "fl": 340, "entry_time": 300, "dep": "GCTS", "dest": "EGSS"},
    {"callsign": "EZY64LK", "type": "A20N", "route": ["VASTO", "SULAM", "DIMSA", "IBLIV", "SUNID", "AKUDA"],
     "fl": 360, "entry_time": 330, "dep": "GCTS", "dest": "EGKK"},
    {"callsign": "IBE3215", "type": "A21N", "route": ["KORNO", "ABODA", "IBALU", "ERMED", "TOVRA", "VEDOD", "TERTO"],
     "fl": 350, "entry_time": -1200, "dep": "LEMD", "dest": "GCLP"}
  ],
  "requests": [
    {"time": 600, "callsign": "RYR81DQ", "type": "altitude", "value": 360, "reason": "optimum level"}
  ],
  "expected": {
    "passive": {"los": true, "pairs": [["EXS64M", "TOM3KD"]], "allow_left_sector": true},
    "naive_approve": {"los": true, "pairs": [["EXS64M", "TOM3KD"], ["EZY64LK", "RYR81DQ"]], "allow_left_sector": true}
  }
}
```

## Prompt template for a language model

Copy this, fill in the brackets, and send it together with this page:

```text
You write scenario files for the ATC SIM en-route radar simulator. Follow the "Scenario JSON reference"
I pasted exactly: use only the point names, routes and aircraft types of its appendix.

Write ONE scenario as a single JSON object, nothing else (no comments, no trailing commas).
- airspace: [agadir | training]; control: [e.g. ["WS"]]
- duration: [e.g. 3600] s; start UTC [e.g. 14:00]
- traffic: about [N] flights in the controlled sectors in the first hour (declared capacity [C]/h),
  spread over the published flows, entry_time from -1800 to 3600 so the sector is busy at T+0
- weather: [e.g. scattered strong thunderstorms over West South, westerly jet]
- planted problems: [e.g. a merge at VEDOD at FL370, a sleeper on UN866, a climb request trap, an unable-RVSM event]
- complexity: [1-5]
Rules: unique upper-case callsigns with a realistic operator prefix; levels follow the semicircular rule
(odd southbound, even northbound); levels within each type's max FL; routes start at entry points and end
at exit points and never fly one-way routings backwards; exactly one timing field per aircraft; requests
and events only for aircraft of the scenario; put the traps in design_notes and a neutral briefing in
description; fill "expected" with the conflicts that must happen when nobody acts.
```

Then paste the result into the designer (**{ } JSON**), read the check, and adjust. Language models are
approximate with timing: the designer's list of **planned conflicts** and the preview runs tell you whether the
traps really happen.

<!-- REFERENCE -->
