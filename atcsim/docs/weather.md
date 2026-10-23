# Weather modelling

The weather is a **deterministic function of position, flight level and time**, generated from a seed
(`atcsim/weather.py`). Every client, the look-ahead prediction, the designer and a replay all see exactly the same
atmosphere, and a scenario run twice gets the same storms. It is not a numerical weather model. It is a structured
synthetic one, built so that the radar picture, the aircraft performance and the pilots' decisions are consistent
with one another.

Coordinates are NM in the airspace's plane (x east, y north), levels FL0–FL800, time in sim seconds.

## Temperature

```json
"temperature": {"surface_c": 24, "tropopause_fl": 390, "lapse_c_per_kft": 2.1}
```

- The temperature falls linearly from `surface_c` at the lapse rate (default 1.98 °C per 1000 ft, the ISA value) up
  to the tropopause (default FL361), then stays constant.
- A **steeper lapse rate** describes an unstable air mass (convective days use about 2.1).
- The **freezing level** follows from the surface temperature and the lapse rate. It shapes the reflectivity
  profile and the bright band.
- The **ISA deviation** (at FL350 unless `isa_deviation_c` is given) drives aircraft performance. Engines are
  flat-rated: climb thrust falls 0.8 %/°C above ISA+10, so a hot day lowers the maximum levels realistically.

## Wind

```json
"wind": {
  "profile": [{"fl": 0, "dir": 250, "kt": 12}, {"fl": 300, "dir": 270, "kt": 55}, {"fl": 800, "dir": 90, "kt": 20}],
  "jet": {"core_fl": 340, "dir": 268, "kt": 65, "axis_point": [0, 30], "half_width_nm": 90,
          "depth_below_ft": 11000, "depth_above_ft": 8000,
          "streak_length_nm": 600, "streak_speed_kt": 30, "meander_amp_nm": 10, "meander_wavelength_nm": 500},
  "noise_kt": 5, "noise_scale_nm": 45
}
```

The wind is the sum of three parts:

1. **Background profile**: direction (FROM) and speed by level, interpolated between the levels given. It is the
   same everywhere horizontally. The legacy `"layers"` key is accepted, and so is an optional
   `"gradient_kt_per_100nm": {"east", "north"}` that strengthens or weakens it across the area.
2. **Jet stream**: a tube of strong wind along an axis through `axis_point`, blowing toward `dir`'s opposite (FROM
   `dir`).
   - **Across the axis** the speed falls off as a Gaussian of half-width `half_width_nm`.
   - **Vertically** it decays below the core with `depth_below_ft` and above it with `depth_above_ft`. These are
     usually different: the shear below a jet core is what makes clear-air turbulence.
   - A **jet streak** (`streak_length_nm`) concentrates the maximum on a stretch of the axis that travels at
     `streak_speed_kt`.
   - The axis can **meander** (`meander_amp_nm`, `meander_wavelength_nm`), and the wind follows the meander.
3. **Small-scale variability**: smooth random wind of a few knots (`noise_kt`) with a spatial scale of
   `noise_scale_nm`. It drifts with the flow and blends between a low-level and an upper-level field.

Aircraft fly in this wind: ground speeds, drift, the time to a point, the top of descent and the entry times of
scenario aircraft are all computed in it. In the designer, the planned trajectories already include it.

### Climatological wind (ERA5)

On the Agadir airspace (the whole Casablanca FIR), the background wind can be the real climatology instead of a
profile: `"wind": {"climatology": {"month": 1, "mode": 0, "scale": 1}, "noise_kt": 5}`. The data file
`atcsim/data/climate/era5_gmmm.npz` holds, for each month, the ERA5 monthly-mean wind and temperature on pressure
levels (about FL180 to FL450) over 25–38°N, 20°W–0°, and the leading mode of their year-to-year variability; it is
built from a Copernicus download by `tools/era5_climatology.py`. The field varies with position and level, so the
wind along a route changes as it does in reality (the westerly component across TOVRA–TERTO, the jet over southern
Morocco in winter, the weak summer flow). A scenario is one day, not the month's mean: unless `mode` is given, the day's strength of
the leading mode of variability is drawn from the weather seed (normal, within ±2 standard deviations; +1 is a
strong-jet day, −1 a weak one), and a smooth large-scale departure from the mean (about 300 NM across, drifting with
the flow) is added, sized by the local year-to-year spread of the month's mean wind × 1.6. `variability` scales both
(default 1; 0 gives exactly the monthly mean, 2 an unusual day); `mode` fixes the jet strength; `scale` multiplies the
whole field. In January the seeds give, for example, westerlies of 3 to 68 kt at VEDOD FL350 around the 31 kt mean. Below the lowest level the wind weakens towards
the surface; the month's temperatures set the ISA deviation used for aircraft performance unless `temperature` is
given. A jet streak (`jet`), a `gradient_kt_per_100nm` and the small-scale `noise_kt` are added on top when given.

## Convection (thunderstorms)

### Cells and their life cycle

Every thunderstorm is made of **cells**. Each cell has a peak core reflectivity, an echo top (18 dBZ), a core
radius, an elongation and orientation, its own texture, and a **life cycle**:

| Stage | What happens |
|---|---|
| **Developing** | The top rises from about 45 % to 100 % of its peak, the core strengthens (up to 15 dB weaker at birth), the cell widens |
| **Mature** | Full top and core; an **anvil** spreads downwind from the top |
| **Dissipating** | The core weakens (down to −22 dB), the top sinks by up to 30 %, the cell spreads; the anvil remains |

A typical life is 45–90 minutes. Cells **move** with the steering wind: 75 % of the mean FL050–FL300 wind, 25° to
the right of it. They **tilt** with the vertical shear, so a cell's top is displaced downwind of its base. The anvil
is blown downwind from the top.

### Intensity presets

| Intensity | Core | Echo tops | Core radius |
|---|---|---|---|
| `moderate` | 44–50 dBZ | FL280–380 | 2–3 NM |
| `strong` | 50–57 dBZ | FL360–450 | 2.5–4 NM |
| `severe` | 55–63 dBZ | FL420–520 | 3–5 NM |

Values are drawn at random within the range (from the seed).

### Regimes

`convection` is one pattern or a **list of patterns**, generated in turn from the same seed:

| Regime | Pattern |
|---|---|
| `isolated` | `count` cells (default 3) at random places and times in `region` `[x0, y0, x1, y1]` |
| `scattered` | `count` cells (default 10); 60 % are **multicell** clusters that spawn 2–4 daughter cells about 6 NM apart on the right-forward flank, one every ~12 minutes |
| `embedded` | `count` cells (default 6) with lower tops (FL280–360), hidden in a **stratiform rain shield** covering the region: hard to see on the composite, clear on a CAPPI at their level |
| `squall_line` | A line of cells along `line.center`, `length_nm` (default 170), `orient_deg`, with cells every `spacing_nm` (default 9). It has random `gaps` (16–26 NM wide) and/or explicit `gap_list` `[[position along the line, width], …]`. New generations of cells keep the line alive for the whole scenario. The line moves with the component of the steering wind normal to it (at least 15 kt), and trails a stratiform region about 30 NM behind |

```json
"convection": [
  {"regime": "scattered", "intensity": "strong", "count": 8, "region": [-40, -120, 60, -40]},
  {"regime": "squall_line", "intensity": "severe",
   "line": {"center": [-20, -95], "length_nm": 170, "orient_deg": 120, "gaps": 0, "gap_list": [[-35, 22], [40, 20]]}}
]
```

### Explicit cells and rain areas

For a designed trap you place cells yourself:

```json
"cells": [{"id": "CB1", "x": -5, "y": 0, "at_time": 300, "top_fl": 460, "dbz": 59, "radius_nm": 5,
           "lifetime_s": 6000, "age_s": 1800, "motion": {"toward": 85, "kt": 20}, "aspect": 1.4, "orient_deg": 30}],
"stratiform": [{"center": [-140, -60], "radius_nm": [60, 30], "orient_deg": 30, "dbz": 32, "motion": {"toward": 60, "kt": 15}}]
```

A cell is **at (x, y) at `at_time`**, aged `age_s` then (default 40 % of its lifetime). It is born `age_s` before
and lives `lifetime_s`. Without `motion` it moves with the steering wind. A stratiform area is an ellipse of
moderate rain (semi-axes `radius_nm`).

## Radar reflectivity

The reflectivity of a cell at a point depends on the distance to the core (tilted with height) and on height:

- **Vertical profile:** the core maximum sits below or near the freezing level. Above it, reflectivity falls to
  18 dBZ at the echo top, with overshooting tops on strong cells.
- **Stratiform rain** has a **bright band** (enhanced reflectivity) just below the freezing level.
- Cells **combine in linear Z** (overlapping cells add up like real echoes), anvils by maximum.

### Products

| Product | Meaning |
|---|---|
| **CAPPI at FL** | Reflectivity at one flight level: what a pilot's radar sees at that level |
| **Composite** | Maximum reflectivity in the column: the usual ATC picture. Embedded cells are masked by the rain around them |
| **Echo tops** | Highest level of 18 dBZ |
| **EDR** | Turbulence |
| **Temperature**, **wind vectors**, **vertical column** (`/weather/column`), **hazard polygons** at a level (for agents) | |

The scope colours the echoes on a radar scale from weak to intense; the weather opacity is set in ⚙ Display.

## Turbulence

Turbulence is given as **EDR** (eddy dissipation rate): about 0.1 light, 0.3 moderate, 0.5 severe.

- **Clear-air turbulence** comes from vertical wind shear. About 15 kt per 1000 ft gives moderate. It forms
  patches (not uniform layers) that drift with the wind. It is typically found below and above a jet core.
- **Convective turbulence** occurs inside cells (up to the top + 2000 ft) and above them, stronger with stronger
  cores.

## How pilots use the weather

The simulated pilots (`atcsim/pilot.py`) see the same weather:

- **Onboard radar:** every 10 s each pilot scans 80 NM ahead along the path it will actually fly (route, heading or
  deviation in progress), at the altitude it will have at each point.
- **What a pilot avoids:** 30 dBZ or more at its level, and, with the radar tilted down, any storm whose top is
  not at least 2000 ft below with a core of 40 dBZ or more. It keeps 6 NM from hazards and 10 NM from severe ones.
  Tune these with the scenario's `"pilot"` block: `hazard_dbz`, `margin_nm`, `severe_dbz`, `severe_margin_nm`,
  `request_range_nm` (default 60), `self_deviation_range_nm` (default 15), `radar_range_nm`.
- **Requests:** when its path is blocked within 60 NM, the pilot works out the shortest dogleg that clears with
  margin, preferring the upwind side: *"request heading 170 for 65 miles due weather, then direct SAMAR"*. If
  climbing over the tops (at least 3000 ft above them) is shorter and possible, it asks for that level instead.
  Without an answer it repeats after about 75 s.
- **No answer by 15 NM:** in solo sessions and for agents, the pilot deviates anyway (PIC authority, logged as a
  deviation without clearance). In exercises, aircraft never deviate by themselves: the request is closed as *not
  answered* and only the pseudo-pilot moves the aircraft.
- **During a deviation**, if the weather moves onto the new path, the pilot asks for more. When clear: *"clear of
  weather, proceeding direct …"*.
- **Turbulence:** after about 45 s in continuous moderate or severe turbulence, the pilot asks for the nearest
  smooth level it can reach, or files a report.
- The pilots' planner ignores restricted areas: protecting them is the controller's job.

The look-ahead prediction (STCA, `/separation`, `/probe`) flies this pilot model too. A pending request with no
answer is predicted as an unauthorised deviation at 15 NM.

## Metrics

Weather is scored per aircraft on the frequency: seconds inside ≥30 / 40 / 50 dBZ, penetration events, deviations
without clearance, unanswered weather requests (exercises), and seconds in moderate or severe turbulence.
Penetrations by an aircraft that is **dealing with weather** (it asked, was cleared, has a WX entry, or the
pseudo-pilot made it deviate) are kept apart, until it reports clear of weather.

## Limits

Storm life cycles are scripted from the seed, so storms do not interact. Icing, hail outside the cores, lightning
and surface weather are not modelled. The radar has no attenuation or position noise.
