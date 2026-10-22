# Aircraft, pilots and airspace

## Aircraft performance

| Part | Model |
|---|---|
| Atmosphere | ISA with the scenario's temperature. Flight levels are **pressure altitudes**. Exact CAS ↔ TAS ↔ Mach conversions |
| Type data | From **OpenAP** (TU Delft) for 36 civil types: max climb and idle thrust, drag polar with wave drag, fuel flow, MTOW / OEW, ceiling, VMO / MMO, real-world cruise Mach. A **per-type calibration** brings L/D max and the maximum level at mid weight to typical values |
| Vertical and speed | Point-mass total-energy model, (T − D)·V = m·g·ḣ + m·V·V̇, with the energy-share factor for constant-Mach / constant-CAS climbs and descents. Climbs at max climb thrust, descents at idle. Mass decreases with fuel burn |
| Temperature | Drag uses the real air density. Engines are flat-rated: climb thrust falls 0.8 %/°C above ISA+10, so hot days lower the maximum level (about 1000–3000 ft by ISA+20) |
| Speed | Each aircraft flies a **Mach / IAS pair** and converts at the **conversion altitude** (M.78 / 300 kt ≈ FL293). ATC can replace either: *"descend FL250, maintain Mach .76, on conversion 280 knots"*. *"Maintain 280 knots"* applies at every level. Below FL100, 250 kt. VMO / MMO protection always |
| Lateral | 25° bank, 5°/s roll rate. FMS-style navigation: track-to-fix legs, wind correction, fly-by turn anticipation |
| Pilot delay | Instructions execute after `pilot_delay_s` (default 4 s) |

**Civil types (36):** A19N A20N A21N A318 A319 A320 A321 A332 A333 A343 A359 A388 B37M B38M B39M B734 B737 B738 B739
B744 B748 B752 B763 B772 B773 B77W B788 B789 C550 CRJ9 E145 E170 E190 E195 E75L GLF6.

**Military types (12):** F16 F18 EUFI RFAL MIR2 MRF1 F5 AJET (fighters, trainers), C130 A400 (turboprops), K35R C17.
They are parametric models calibrated like the civil types. Fighters, trainers and the C-130H are not RVSM approved
by default.

**What the pilot accepts.** A climb is refused when the aircraft would have less than 300 ft/min residual climb at
the new level and present weight, no 1.3 g buffet margin, or would be above MMO or its ceiling. Speeds are checked
against the type range, buffet, VMO and MMO. Simulated pilots (solo and agents) refuse headings or directs into
weather. In exercises the pseudo-pilot decides.

The **type performance window** (click the aircraft type in a label) and the pseudo-pilot's *Can do now* show these
limits for the aircraft's present weight.

## Levels, RVSM and separation

- **Semicircular rule:** the direction is the bearing from the entry point to the exit point. Southbound
  (090–269) takes odd levels (…290, 310…410, 450). Northbound (270–089) takes even levels (…280, 300…400, 430).
  Filed levels and scripted requests must follow it. A clearance to a wrong-direction level is accepted (it can be
  needed tactically) but flagged and counted. An aircraft leaving at a wrong-direction level is scored. Arrivals
  follow their LoA level instead.
- **RVSM airspace FL290–FL410.** Each aircraft is RVSM approved or not (`W` in item 10). In flight it can become
  **unable RVSM**: the pilot reports it, and the label shows `NRVSM`. A non-approved civil aircraft inside RVSM
  airspace on your frequency is an **RVSM airspace violation**. State aircraft may enter, with 2000 ft from
  everyone.
- **Horizontal minimum:** 7 NM in the Agadir CTA, 5 NM in the training sector. A scenario can override it.
- **Vertical minima:**

  | Case | Minimum |
  |---|---|
  | Below FL290 | 1000 ft |
  | FL290–FL410, both RVSM | 1000 ft |
  | FL290–FL410, either non-RVSM or unable | 2000 ft |
  | Either above FL410 | 2000 ft |
  | A military formation, against anyone | 2000 ft, or the horizontal minimum + the formation's extent |

## Flight plans and surveillance

Every aircraft has a complete ICAO flight plan, correlated with its track by the SSR code (unique in the session)
and the Mode S address:

- item 7 callsign and SSR code;
- item 8 flight rules and type of flight;
- item 9 number (formations), type and wake;
- item 10 equipment and surveillance;
- item 13 departure and EOBT;
- item 15 speed, requested level and route;
- item 16 destination, EET and alternate;
- item 18 STS, PBN, DOF, REG, EET at the FIR boundaries, SEL, CODE, OPR, PER, TYP, RMK;
- item 19 endurance and persons on board.

EOBT and EETs are computed so the flight is at its entry point on time. The **surveillance** part of item 10 decides
what the radar gets: `L` / `H` give Mode S enhanced surveillance (downlinked parameters); `S`, `E`, `P`, `I`, `X`
give elementary surveillance (address only); `A` / `C` give Mode A/C only.

Each flight knows the unit it comes from and the one it goes to (the Agadir sector not controlled, or Lisboa,
Madeira, Sevilla, Canarias, Casablanca), which OLDI and the labels use.

## The Agadir CTA

- **Boundary** from the AIP (CTA AGADIR), simulated FL195–FL460.
- **Sectors** and their declared capacities, in flights per hour:

  | Sector | Name | Frequency | Capacity |
  |---|---|---|---|
  | WN | West North | 128.800 | 58 |
  | WS | West South | 127.000 | 43 |
  | ON (OCN) | Oceanic North | 136.000 | 56 |
  | OS (OCS) | Oceanic South | 124.500 | 65 |
  | WW = WN+WS | West band-boxed | | 48 |
  | OCE = ON+OS | Oceanic band-boxed | | 51 |
  | WW-OCE | Whole CTA | | 47 |

  A scenario picks the controlled sectors with `"control"`. The others are flown by "the adjacent sector"
  (coordinated by OLDI and the telephone).
- **Airways:** UN866, UN873 (southbound only), UN858 (northbound only), UN857, UR722, UR724, UH899 and UA411.
- **Standard routes:** RUNAX–IBALU–ERMED–TOVRA–VEDOD–TERTO and DESUM–SULAM–UN858–AKUDA.
- **RAD routings** (one way, dashed on the radar): RUNAX–IBALU–SAMAR, SUBAL–IBALU–SALCA, TAKAV–DOMOG,
  BEXAL–IBALU–SALCA, TAKAV–VEDOD–TERTO, VASTO–NIDEB–TIGGI, VASTO–BAROK, SAMAR–BEXAL, SAMAR–TIGGI and
  NIPRI–ROLRO.
- **Entry / exit points:** KORNO, RUNAX, VASTO and DESUM are entry only; TERTO, TIGGI and AKUDA are exit only.
- **Transfer points:** named points on the controlled boundary. Where a route crosses it between two points, a
  coordination point is generated, named C + two letters of each neighbour (e.g. `CTAVE` on TAKAV–VEDOD).
- **Flight plans beyond the CTA** follow the onward-route rules of the airspace file (Lisboa, Madeira, Sevilla,
  Canarias, Casablanca points).
- **Projection:** Lambert conformal conic. Headings are grid/true. The Mode S magnetic heading applies the
  scenario's `magnetic_variation`.

## Arrivals and sequencing

Agadir arrivals: **GCRR** (Lanzarote) via **TERTO**, **GMMN** (Casablanca) via **MARJA** and **SALCA**. The letter of
agreement asks for arrivals **cleared to FL250** when they cross the fix (they may still be descending to it) and
**10 NM in trail**. These values are placeholders until the unit's
real ones are entered. The training sector has E1 (Eastfield) and S4 (Southport).

- Each arrival's pilot computes its **top of descent** continuously: remaining route + approach path against an
  idle descent with its own performance, the wind, and the speed restrictions. At the TOD it asks *"request
  descent"*. Without a clearance it stays high and repeats every 60 s.
- The **runway in use** changes the approach path (straight-in or reversal) and moves the TOD by up to about 30 NM.
  A late TOD means you have to start the descent yourself.
- At the fix the system checks the level (±300 ft), the spacing to the previous arrival, and the optional speed.
  Violations are scored.
- Clearing below the LoA level is accepted with a reminder to coordinate with the next sector.

Scenario keys (training airspace example):

```json
"aerodromes": [{"id": "EFLD", "x": 97, "y": 6, "elev_ft": 300,
                "runways": [{"id": "09", "heading": 90}, {"id": "27", "heading": 270}],
                "runway_in_use": "auto", "runway_schedule": [{"time": 900, "runway": "27"}]}],
"sequencing": [{"fix": "E1", "aerodrome": "EFLD", "spacing_nm": 10, "cross_at_fl": 250, "min_fl": 250}]
```

## Traffic before and after the sector

- Aircraft **appear** 30 NM before their entry point, still with the previous unit (they refuse instructions).
  They **call** 10 NM before the boundary. You can assume them earlier.
- After the exit, an aircraft stays assumed until you decontrol it, and keeps flying its flight plan. Once
  decontrolled it turns grey and leaves the picture when well clear.
- Losses, infringements and weather penetrations count when at least one aircraft involved is on your frequency.
