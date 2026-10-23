# Scenario library and progression

The library has two families:

- **Training sector** (`s00`–`s17`): a fictional 120 × 120 NM square. Each scenario isolates one problem (a
  crossing, a climb-request trap, an area activating, a squall line gap, an arrival sequence). Use them to teach
  one thing at a time.
- **Agadir CTA**: the real sectors and routes. These are graded from one sector at about half its declared capacity
  to the whole CTA band-boxed slightly over capacity. **Weather is always present**, and the traffic follows the
  published flows (Europe–Canaries, Europe–South America, Casablanca arrivals and departures, the Azores).

Every scenario is generated and **verified** by `tools/build_scenarios.py`: the planted conflicts must happen when
nobody acts (*passive*), and the request traps must cause a loss when every request is approved as asked (*naive
approve*). Background traffic is de-conflicted so that the problems left are the planted ones, plus whatever the
weather and the pilots' own requests add.

## The Agadir progression

Density is the traffic in the controlled sectors during the first hour against the sector's **declared capacity**
(WN 58, WS 43, OCN 56, OCS 65, WW 48, OCE 51, WW-OCE 47 flights per hour). The steps raise one thing at a time.

| Step | Scenario | Sectors | Traffic / capacity | Complexity | Weather | What it trains |
|---|---|---|---|---|---|---|
| 1 | `p01_ws_two_merges` | WS | ~24/h · 55 % · density 2 | 2 | July ERA5 winds, no storms | Two planted conflicts with one obvious fix each: the NIPRI–ROLRO crossing, a merge at VEDOD (45 min) |
| 2 | `p02_wn_merges_and_climbs` | WN | ~40/h · 69 % · density 2 | 2 | February ERA5 winds | IBALU merge (standard route × UN857); a GMMN departure's climb request that crosses RUNAX traffic; Casablanca departures asking for cruise levels (45 min) |
| 3 | `p03_ocn_isolated_storms` | OCN | ~44/h · 79 % · density 3 | 3 | February ERA5 winds, isolated moderate storms | TIGGI merge; a **sleeper**: M.86 catching M.80 on UN866, the loss 15 min later; a northbound climb request into southbound traffic leaving BEXAL; weather deviation requests |
| 4 | `p04_ocs_embedded_unable_rvsm` | OCS | ~55/h · 85 % · density 3 | 3 | January ERA5 winds, **Embedded** storms around MITLA | SAMAR merge; an aircraft reporting **unable RVSM** that later passes northbound traffic 1000 ft away; reading embedded cells on the CAPPI |
| 5 | `p05_ws_storms_arrivals` | WS | ~41/h · 95 % · density 4 | 4 | October ERA5 winds, scattered strong storms | VEDOD merge next to the storms; **GCRR arrivals** at TERTO (FL250, 10 NM in trail); a non-RVSM ferry asking FL400; weather deviations |
| 6 | `p06_wn_capacity_sequencing` | WN | ~58/h · 100 % · density 4 | 4 | March ERA5 winds | At capacity: unable RVSM crossing a Casablanca arrival; **GMMN arrivals** at MARJA 45 s apart; a descent request through northbound traffic |
| 7 | `o01_oceanic_light_wx` | OCE (ON+OS) | ~50/h · 98 % · density 4 | 4 | February ERA5 winds, isolated moderate storms | Merges at TIGGI and SAMAR, an overtaking sleeper, a crossing from the Azores, unable RVSM near SAMAR, a climb trap at BEXAL |
| 8 | `w01_west_scattered_cb` | WW (WN+WS) | ~48/h · 100 % · density 4 | 4 | October ERA5 winds, scattered strong storms over WS | Merges at IBALU and VEDOD, the NIPRI–ROLRO crossing; a descent trap, a Mach trap with a follower 9 NM behind, a departure climb trap |
| 9 | `o02_oceanic_embedded_cb` | OCE | ~54/h · 106 % · density 5 | 5 | January ERA5 winds, embedded storms | Restricted area **R90** activating across UN866; a fighter pair (formation) against a northbound flight; non-RVSM ferry; Mach trap |
| 10 | `w02_west_squall_line` | WW | ~53/h · 110 % · density 5 | 5 | November ERA5 winds, **Severe squall line**, two gaps | Unable RVSM, a 4×F16 formation crossing UN858, TERTO and MARJA arrival sequences, a non-RVSM ferry asking FL400, traffic funnelled through the gaps |
| 11 | `c01_cta_moderate_wx` | WW-OCE (all) | ~48/h · 102 % · density 4 | 5 | October ERA5 winds, scattered strong storms | The whole CTA: a TIGGI merge and five request traps across sectors; GCRR arrivals merging at VEDOD |
| 12 | `c02_cta_severe_wx` | WW-OCE | ~53/h · 113 % · density 5 | 5 | December ERA5 winds, severe squall line, three gaps | Everything at once: unable RVSM, a state C-130, a fighter formation, SALCA / TERTO / MARJA sequences |

The exact figures of each file are on the home page (aircraft per hour) and in its `traffic_load` block.

**Suggested path:** steps 1–4 with a new trainee, one sector at a time, rising traffic. Steps 5–6 bring arrivals,
LoA levels and RVSM events at capacity. Steps 7–8 band-box two sectors at capacity. Steps 9–12 are the top of the
scale: slightly over capacity, severe or hidden weather, everything at once.

All the Agadir and Casablanca scenarios run on the ERA5 wind climatology of their month (each one a
particular day drawn from its weather seed, see [weather](weather.md#climatological-wind-era5)).

## Casablanca ACC

| Scenario | Sectors | Traffic | What it trains |
|---|---|---|---|
| `s20_casablanca_north` | N | 10 aircraft, 45 min | A first look at the North sector: a crossing over RBT, a climb request into it, an area activating over MARJA–LAKAM |
| `s21_casablanca_north_gmmn_gmme` | N | 30 aircraft, 60 min | GMMN arrival sequences at LAKAM (two waves), RAVOL and BISMI, GMME arrivals at FAHIM / ENNAV; GMMN / GMME departures climbing against arrivals and into the UN871 and UB11 streams; RBT crossing, a UN871 overtake, a UB11 speed trap, an opposite-direction climb request on UG5; GMD 21 active across UN726 |
| `s22_casablanca_south_gmmx_gmad` | C + SS | 26 aircraft, 60 min | GMMX arrivals at MABAP from three airways, GMAD arrivals merging at AGALI, TEKNA and OLOMA; GMMX / GMAD / GMMZ departures climbing against the arrivals; crossings at AGOVA and TOPMA, a UR975 overtake, the ADM convergence; GMD 30 active across UL102 |

## Training sector scenarios

| Scenario | Weather | What it tests |
|---|---|---|
| `s00_free_flow` | Westerly jet | Over-control: no conflicts, 3 safe requests |
| `s01_basic_crossing` | Calm | Same-level crossing at the centre |
| `s02_climb_request_into_crossing` | Westerly jet | Climb into a crossing; the tempting FL390 amendment also fails |
| `s03_descent_through_opposite` | Northerly | Descent through opposite traffic |
| `s04_direct_request_reroute` | Westerly jet | A direct that cuts across same-level traffic |
| `s05_slowdown_on_converging_exit` | Westerly jet | The leader slows on converging routes |
| `s06_speed_up_into_crossing` | Northerly | A speed request erases an 8 NM crossing, with a distractor request at the same time |
| `s07_restricted_area_activation` | Westerly jet | An area activates across a route; the north detour runs into traffic |
| `s08_simultaneous_level_requests` | Westerly jet | Two same-level requests 2 s apart |
| `s09_area_vertical_limits` | Northerly | A descent into an active restricted area |
| `s10_busy_mixed` | Westerly jet | 11 aircraft, 4 conflicts, a danger area, 4 requests |
| `s11_weather_deviation_into_traffic` | Jet + one CB | The requested deviation runs into opposite traffic |
| `s12_squall_line_gap` | Severe squall line, one gap | Two same-level flights funnelled into the same gap |
| `s13_turbulence_level_request` | 155 kt jet, CAT | A ride request climbs a heavy B789 into crossing traffic |
| `s14_climb_over_tops` | Broken line, tops FL350 | A climb over the tops into traffic at FL380 |
| `s15_convective_afternoon` | Scattered multicells, restricted area | Open problem: deviations push aircraft into each other and into R71 |
| `s16_e1_arrival_sequencing` | Westerly jet | Five arrivals converging on E1, all high |
| `s17_s4_runway_change` | Northerly | The runway change moves the pilots' top of descent past the LoA point |

## Making your own

Copy a library scenario (home page → *Copy*) and change it in the [designer](designer.md), or build one from
scratch. Set its **complexity** and check its **density** against the declared capacity. For a realistic hard
scenario, stay at about 105–115 % of capacity at most, and make it harder through the problems rather than the
number of aircraft.
