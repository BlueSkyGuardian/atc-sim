# API reference

Everything the radar pages do goes through this API, so scripts and agents can do the same. The full schemas,
with a "try it" button, are in the interactive reference at **[/docs](/docs)** (OpenAPI; instructors and admins).
This page explains how the pieces fit together.

## Basics

- **Base URL:** the server's address, e.g. `https://atc.example.org`. JSON in and out.
- **Authentication:** in the browser, the session cookie from sign-in. Scripts send **`X-API-Key: <ATC_API_KEY>`**
  and get administrator rights. A server run with `ATC_AUTH=off` (your own machine) needs nothing.
- **Coordinates:** NM in the airspace's plane, x east, y north. Levels are flight levels, times sim seconds.
  Headings are degrees (grid / true).
- **Rights:** in a connected exercise each seat has its own rights (see [Seats](#seats-and-rights)). Errors come back
  as HTTP 4xx with `{"detail": "…"}`.

```bash
curl -s -H "X-API-Key: $KEY" $URL/scenarios | jq '.[].name'
```

## Sessions

A **session** is one running simulation of a scenario. An exercise is a session with seats.

| Method | Path | What |
|---|---|---|
| GET | `/scenarios` | Scenario list (name, title, airspace, aircraft, author, complexity, density, traffic load…) |
| GET | `/scenarios/{name}?include_notes=true` | Briefing; `include_notes` reveals the design notes (evaluators) |
| GET | `/scenarios/{name}/raw` | The scenario file |
| POST | `/sessions` | Create: `{"scenario", "mode": "realtime"\|"stepped", "time_factor": 1, "autostart": true, "auto_assume": null}` → briefing with the session `id` |
| GET | `/sessions` | Running sessions |
| GET · DELETE | `/sessions/{id}` | Briefing (sector, weather summary, areas, types, state) · close |
| POST | `/sessions/{id}/connect` | Briefing for a client joining |
| POST | `/sessions/{id}/control` | `{"action": "start"\|"pause"\|"resume"\|"set_speed"\|"set_mode"\|"step"\|"restart"\|"auto_wx", "seconds", "time_factor", "mode", "value"}` |

In **stepped** mode the clock advances only with `{"action": "step", "seconds": 5}`. That is the way to drive an
agent turn by turn.

```bash
SID=$(curl -s -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
      -d '{"scenario": "p01_ws_two_merges", "mode": "stepped"}' $URL/sessions | jq -r .id)
curl -s -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"action": "step", "seconds": 10}' $URL/sessions/$SID/control
```

## The picture

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/state` | The whole picture: time, aircraft (position, level, speeds, heading, track, route, next point, status, assumed / calling, alerts, entries…), transferred aircraft, pending requests, areas, MTCD, special tracks, coordination, UTC |
| GET | `/sessions/{id}/positions` | Compact positions |
| GET | `/sessions/{id}/aircraft` · `/aircraft/{cs}` | Aircraft list · one aircraft with performance at present weight and its weather phase |
| GET | `/sessions/{id}/aircraft/{cs}/performance` | Type summary and, at the present weight, a table by level: schedule speeds, climb rate, idle descent, level available |
| GET | `/sessions/{id}/requests` · `/events` · `/instructions` · `/metrics` | Logs and scores |
| GET · PATCH | `/sessions/{id}/areas` · `/areas/{area}` | Area status · `{"active": true}` |
| GET | `/sessions/{id}/sequence` | Per sequencing fix: runway, LoA, inbound arrivals in ETA order with spacing, TOD and LoA descent point, crossings |
| GET · PATCH | `/sessions/{id}/aerodromes` · `/aerodromes/{ad}` | Aerodromes and runways · `{"runway": "27"}` |
| GET | `/airspace?name=agadir&control=WS` · `/airspaces` · `/types` | Geometry (boundary, sectors, points with lat/lon, airways, RAD, transfer points, minima, capacities) · aircraft types with calibration |

## Live stream (WebSocket)

`wss://…/sessions/{id}/stream?separation=true&trails=true&lookahead_s=600`

Every new sim second the server pushes:

```json
{"type": "tick", "state": {…}, "events": [{"seq": 812, "type": "request", …}], "separation": {…}}
```

`separation` (current pairs and the predicted conflicts) is attached when a new look-ahead result is ready, about
every few seconds. Send operations on the same socket. Each one gets an `{"type": "ack", "ok": true, "result": …}`
back:

```json
{"op": "instruction", "callsign": "RAM101", "type": "altitude", "fl": 360}
{"op": "command", "text": "RAM101 FL360 H250"}
{"op": "entry", "callsign": "RAM101", "cfl": 360}
{"op": "control", "action": "pause"}
```

## Instructions

`POST /sessions/{id}/instructions` takes one instruction or a list. The set is closed:

```json
{"callsign": "RAM101", "type": "altitude", "fl": 360, "rate_fpm": 1500}
{"callsign": "RAM101", "type": "altitude", "fl": 250, "mach": 0.76, "conversion_kt": 280}
{"callsign": "RAM101", "type": "heading", "heading": 250, "turn": "left"}
{"callsign": "RAM101", "type": "direct", "waypoint": "IBALU"}
{"callsign": "RAM101", "type": "reroute", "points": ["3103N01244W", "VASTO"]}
{"callsign": "RAM101", "type": "speed", "mach": 0.76, "conversion_kt": 280}
{"callsign": "RAM101", "type": "speed", "ias_kt": 280}
{"callsign": "RAM101", "type": "speed", "resume_normal": true}
{"callsign": "RAM101", "type": "resume"}
{"callsign": "RAM101", "type": "deviation", "side": "left", "max_nm": 20, "then_direct": "SAMAR"}
{"callsign": "RAM101", "type": "reply", "request_id": "R001", "decision": "approved"}
{"callsign": "RYR8KQ", "type": "reply", "request_id": "R004", "decision": "roger"}
{"callsign": "RAM101", "type": "assume"}
{"callsign": "RAM101", "type": "release"}
{"callsign": "RAM101", "type": "wx_clear"}
```

- The result says `executed` or `rejected` (with the pilot's reason, e.g. *"unable FL410, maximum FL380 at this
  weight"*), with `warnings` (wrong-direction level, below the LoA level…).
- `reply` approves exactly what the pilot asked. Any other clearance given in answer counts as *amended*.
- `POST /sessions/{id}/assume/{cs}` and `/release/{cs}` are shortcuts.

**Shorthand** (`POST /sessions/{id}/command` with `{"text": "…"}`, the radar's command line):

| Shorthand | Meaning |
|---|---|
| `FL360`, `FL360 V1500` | Level, with rate |
| `H270`, `L270`, `R270` | Heading, forcing the turn |
| `D IBALU`, `RES` | Direct, resume own navigation |
| `M76`, `K280`, `S280`, `SN` | Mach, IAS on conversion, IAS now, resume normal speed; after a level they become part of it |
| `DEV`, `DEVL`, `DEVR20` | Weather deviation |
| `APP`, `UNA`, `ROG` [request id] | Approve, refuse, acknowledge |
| `AS`, `REL` | Assume, release |

Several instructions in one line: `RAM101 FL250 M76 K280`.

## Controller entries (label data)

`POST /sessions/{id}/entries` records what the controller **entered** (in an exercise the controller's seat cannot
move aircraft):

```json
{"callsign": "RAM101", "cfl": 330, "xfl": 310, "speed": "M76", "heading": 250, "direct": "IBALU",
 "wx": "L20", "note": "PSN rpt", "coord": "phone", "route": ["3103N01244W", "VASTO"]}
```

Entries drive the labels, MTCD, route adherence and OLDI. An empty string clears a field.

## CPDLC

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/cpdlc?since=0&callsign=` | Messages since an id (one aircraft's or all), our address (`own`), and per equipped aircraft its connection: `state` (`none`, `logon`, `cda`, `transferred`), `cda`, `nda`, `open` dialogues, `recent` ones |
| POST | `/sessions/{id}/cpdlc/parse` | `{"text": "CL350 D ROLRO", "callsign": "RAM201"}` → the elements in clear, whether it would be sent by data link |
| POST | `/sessions/{id}/cpdlc/command` | The controller's command line: `{"text", "callsign", "voice": false}`. An uplink to a connected, assumed aircraft (or an answer to its request: `APP`, `UNA`, `SBY`, `ROG`); `voice: true` or no connection = only the system entries. Also `NDA GCCC`, `EOS` |
| POST | `/sessions/{id}/cpdlc/respond` | The pilot answers an uplink: `{"message_id", "answer": "WILCO" | "UNABLE" | "STANDBY" | "ROGER" | "AFFIRM" | "NEGATIVE", "reason": "weather" | "performance", "text": "free text added"}`. WILCO executes it (400 if the aircraft cannot) |
| POST | `/sessions/{id}/cpdlc/downlink` | A pilot request: `{"callsign", "kind": "level" | "climb" | "descent" | "direct" | "speed" | "deviation" | "text", "value", "reason"}`, or `{"callsign", "request_id"}` to send one of its pending requests |

### Coordination chat

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/chat?since=0&station=` | The stations (`own`: our sector, the other sectors, the adjacent units), the quick answers and the messages, each with `from`, `to`, `text`, `callsign`, `ref` (the message it answers) |
| POST | `/sessions/{id}/chat` | `{"from", "to", "text", "callsign", "ref"}`. The controller writes as our sector (`from` may be left out); the pseudo-pilot as another station; the instructor as any. A sector can be given by its id (`WN` → `GMAC WN`) |

The state carries `chat_last` (the last message id).

The state (`GET /state`, the stream) carries `cpdlc` (the connections and their open or recent dialogues, as above)
and `cpdlc_last` (the last message id). On the solo radar the simulated pilot answers uplinks by itself after 8 to
25 seconds: WILCO, or UNABLE when the aircraft cannot fly it. Message elements carry their ICAO number (`UM20`
CLIMB TO, `UM74` PROCEED DIRECT TO, `UM117` CONTACT, `DM9` REQUEST CLIMB TO, …; FANS 1/A numbering).

## Prediction and tools

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/separation` | Current pairs, **predicted conflicts** (flying the full pilot model, weather included), area infringements and weather penetrations ahead |
| POST | `/sessions/{id}/probe` | What-if: `{"instructions": [...], "horizon_s": 600}`. Runs the candidate clearances on a copy and returns the conflicts they would create or solve. Changes nothing |
| GET | `/sessions/{id}/mtcd` | Medium-term conflicts between the system trajectories over the look-ahead (`look_ahead_s`, 20 min by default) |
| POST | `/sessions/{id}/mtcd/settings` | Set the exercise's MTCD look-ahead: `{"look_ahead_min": 30}`, 5 to 60. Controller or instructor. Also used by the probes; the state carries it as `mtcd_look_s` |
| POST | `/sessions/{id}/mtcd` | Probe one aircraft: `{"callsign", "cfl"}` / `"heading"` / `"direct"`, or `{"callsign", "levels": [310, 330, 350]}` for the level-probe colours |
| GET | `/sessions/{id}/oldi?since=&callsign=` | OLDI messages (ABI, ACT, REV, PHONE; sent / received; LAM) and each aircraft's cleared, exit and coordinated levels |
| GET | `/sessions/{id}/flightplans` · `/flightplans/{cs}` | Every flight plan · one: FPL message, decoded items, estimates over the points ahead, coordination, correlation |

## Weather

All weather endpoints accept `at_s=` to look at another moment.

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/weather` | Temperature, freezing level, tropopause, wind profile, jet, storm motion, every cell (position, motion, top, dBZ, radii, stage), squall lines and gaps |
| GET | `/sessions/{id}/weather/radar?product=cappi\|composite\|echo_tops\|edr\|temperature&fl=350&res_nm=2&bbox=x0,y0,x1,y1&encoding=b64\|json` | Gridded product (uint8 base64 or JSON) |
| GET | `/sessions/{id}/weather/hazards?fl=350&threshold_dbz=30` | Polygons to avoid at a level, with the pilots' margins |
| GET | `/sessions/{id}/weather/column?x=&y=` | Profile ground–FL800: dBZ, wind, temperature, EDR |
| GET | `/sessions/{id}/weather/wind?fl=350&res_nm=15` | Wind vectors |

## Recording

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/recording?since=&every=` | Recorded frames (compact rows: see `frame_fields`), aircraft metadata, timeline events |
| GET | `/sessions/{id}/recording/export` | The whole recording as one file (`kind: atcsim-recording`), for offline replay |

## Scenario design

| Method | Path | Body → result |
|---|---|---|
| POST | `/designer/compile` | `{"scenario"}` → errors, warnings, planned trajectories and conflicts, `traffic_load`, estimated `ratings` |
| POST | `/designer/preview` | `{"scenario", "policy": "passive"\|"naive_approve"}` → a fast-time run: tracks, losses, requests, penetrations, infringements, RVSM, sequencing |
| POST | `/designer/weather` | `{"scenario", "at_s", "fl", "product"}` → weather image |
| POST | `/designer/save` | `{"scenario", "name", "overwrite"}` → writes `scenarios/<name>.json` |

See [Building scenarios with JSON](designer.md#building-scenarios-with-json) for examples.

## Platform API

Used by the home, stats and admin pages (signed-in users):

| Method | Path | What |
|---|---|---|
| GET · PUT | `/api/me` | Your profile |
| GET · PATCH | `/api/users` · `/api/users/{uid}` | User directory · role / status (admin) |
| GET | `/api/scenarios` | Scenarios you can use, with access, owner, shares, author, ratings (instructors) |
| POST · DELETE | `/api/scenarios/{name}/copy` · `/shares` · `/shares/{uid}` · `/api/scenarios/{name}` | Copy, share, unshare, delete |
| GET · POST | `/api/exercises` | Your exercises (each with `scenario_info`: title, author, complexity, density as when run) · start one `{"scenario", "name"}` |
| GET | `/api/exercises/{eid}` | Detail, members, links |
| POST · DELETE | `/api/exercises/{eid}/members` · `/members/{uid}` | Invite `{"user_id", "seat"}` · remove |
| POST | `/api/exercises/{eid}/end` | End: results, recording, evaluations |
| GET | `/api/exercises/{eid}/outline` | Thumbnail geometry |
| GET · POST | `/api/recordings` · `/api/recordings/{rid}/archive` · `/render` · `/file` · DELETE `/api/recordings/{rid}` | Recordings |
| GET · POST | `/api/evaluations` · `/api/evaluations/{eid}/ack` | Evaluations to write / written, acknowledgement |
| GET | `/api/stats` | Statistics by role |
| GET | `/api/help` · `/api/help/{page}` | This documentation |
| GET | `/api/health` | Health check (public) |

## Timeline and rewind

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/timeline` | The points the simulation can be put back to (one every 10 s) |
| POST | `/sessions/{id}/control` | `{"action": "rewind", "seconds": 600}`: the world as it was at the last point at or before T+600 s (aircraft, entries, requests, CPDLC, chat, metrics), paused. The answer gives `rewound_to_s`; the state's `session.rewinds` counts rewinds and restarts, and the event and message ids start again from that point. Instructor, pseudo-pilots, the solo radar's controller |

## Live actions

| Method | Path | What |
|---|---|---|
| GET | `/sessions/{id}/live` | Aircraft on the picture, emergencies in progress, areas, aerodromes, aircraft types |
| POST | `/sessions/{id}/live` | `{"action": "aircraft", "callsign", "type", "fl", "route": [...], "appear": "now" or seconds to its entry}` · `{"action": "emergency", "callsign", "kind": "descent" \| "medical" \| "radio" \| "general", "aerodrome", "why"}` · `{"action": "clear_emergency", "callsign"}` · `{"action": "request", "callsign", "kind": "level" \| "direct" \| "speed" \| "deviation" \| "report", "value", "reason"}` · `{"action": "area", "area", "active"}`. Instructor, pseudo-pilots, the solo radar's controller |

Aircraft snapshots carry `emergency` and `squawk` (7700 / 7600 during one); the special tracks give `EMG` / `RCF`.

## Solo radar

| Method | Path | What |
|---|---|---|
| GET | `/api/solo/scenarios` | The scenarios this user can fly alone (the library for everyone; own and shared ones for instructors) |
| POST | `/api/solo` | `{"scenario", "name"}` → a solo exercise: the user's seat is `atc`, simulated pilots fly the controller's `entries` (the answer carries `_readback`: what the pilot read back, or unable) and answer CPDLC uplinks. The controller may use `/control` on it and end it with `POST /api/exercises/{id}/end` |

## Seats and rights

In a connected exercise:

| Seat | May |
|---|---|
| **instructor** | Everything on the session, including control |
| **atc** | Read everything; `entries`, assume / release, MTCD probe and look-ahead, CPDLC `parse` and `command`, chat as our sector. Instructions do not move aircraft (the pilot executes) |
| **pilot** | Read everything; instructions (the aircraft executes them whether or not the controller assumed it), MTCD probe, CPDLC `respond` and `downlink`, chat as another station, simulation control (run, pause, speed, rewind) |

The WebSocket `/sessions/{id}/comms` carries the radio and telephone audio (16 kHz G.711 μ-law). It is used by the
pages, not by agents.

## Writing an agent

`examples/baseline_agent.py` is a complete reference agent using only this API. It probes each request and approves
it if nothing new appears within 15 minutes, handles weather deviations, and solves conflicts with level, Mach and
heading changes found by `/probe`:

```bash
python examples/baseline_agent.py --scenario p03_ocn_isolated_storms --realtime     # watch it on the radar
```

Agents see the same picture as a controller and never see future requests. Score them with
`/sessions/{id}/metrics`.
