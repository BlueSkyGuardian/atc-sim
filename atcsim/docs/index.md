# ATC SIM documentation

ATC SIM is an en-route radar simulator for controller training and for evaluating ATC agents. It flies traffic
in a synthetic training sector or in the **real Agadir CTA**: the AIP points, airways, RAD routings and the four
sectors with their declared capacities. Around the radar it adds type-specific aircraft performance, a 3D weather
model, simulated pilots, flight plans, OLDI coordination, radio and telephone, recording and statistics.

![The sign-in page](/static/docs/landing.jpg "The sign-in page")

## Who uses what

| You are… | Start here | You use |
|---|---|---|
| **Controller** (ATCO, trainee) | [Controller manual](controller.md) | The radar seat of an exercise: labels, menus, tools, radio, telephone. Your statistics and evaluations. |
| **Pseudo-pilot** | [Pseudo-pilot manual](pilot.md) | The pilot seat: the *To do* list, the control deck, the aircraft list, the radio and the telephone (as the adjacent units). |
| **Instructor** | [Instructor and admin manual](instructor.md) | Exercises (start, invite, run, monitor, end), scenarios, recordings, evaluations, statistics. |
| **Administrator** | [Instructor and admin manual](instructor.md#administrator) | User roles. |
| **Scenario designer** | [Scenario designer](designer.md) and the [Scenario JSON reference](scenario-json.md) | The visual designer; every field of a scenario file, with examples and a prompt template for writing scenarios with a language model |
| **Developer / agent builder** | [API reference](api.md) | The HTTP and WebSocket API: sessions, instructions, the picture, prediction, weather products. |

The [weather modelling](weather.md) and [aircraft, pilots and airspace](aircraft.md) pages describe how the
simulator works inside. They explain why an aircraft refuses a level, or how a squall line moves.

## A typical exercise

1. The instructor picks a scenario on the home page, sees its **complexity**, **traffic density** and author,
   and clicks **Start exercise**. The scenario is built and paused.
2. The instructor invites a controller and a pseudo-pilot from the user list, or sends them their links.
3. Everyone opens the exercise. The controller has the radar. The pseudo-pilot has the radar and the aircraft
   workspace. The instructor has everything, including the run controls.
4. The instructor presses **Run**. Aircraft call in on the frequency (the pseudo-pilot says the calls). The
   controller assumes them, gives clearances by voice and enters them in the labels. The pseudo-pilot makes the
   aircraft fly them.
5. The instructor ends the exercise. The server renders a video of the controller's screen with the audio. The
   instructor rates the controller, the controller rates the pseudo-pilot, and the results go into everyone's
   statistics.

## Difficulty: complexity and density

Every scenario has two ratings, each from 1 to 5, shown as five coloured dots:

- **Complexity** reflects how hard the problems are: the number of planted conflicts, the traps hidden in
  pilot requests, the weather, restricted areas, unable-RVSM events, arrival sequences and formations. The
  designer sets it.
- **Traffic density** compares the flights in the controlled sectors during the first hour with the sector's
  **declared capacity**:

  | Density | Flights / declared capacity |
  |---|---|
  | 1 very low | under 50 % |
  | 2 low | 50–70 % |
  | 3 moderate | 70–90 % |
  | 4 high | 90–105 % |
  | 5 very high | over 105 % (the library goes up to about 112 %, a slight overshoot) |

  Agadir declared capacities, in flights per hour:

  | Sectors | WN | WS | OCN (ON) | OCS (OS) | WW (WN+WS) | OCE (ON+OS) | WW-OCE (all) |
  |---|---|---|---|---|---|---|---|
  | Capacity | 58 | 43 | 56 | 65 | 48 | 51 | 47 |

When the designer has not set a rating, the system estimates it from the content and shows it with a `~`.
Every exercise keeps the ratings the scenario had when it was run. The controller sees them in their exercise
list, their statistics and their evaluations.

The [scenario library](scenarios.md) is a progression. It starts with one Agadir sector at about half its
capacity with two simple conflicts. It ends with the whole CTA band-boxed slightly over capacity, under a severe
squall line.

## Getting around

- **Home**: live exercises, scenarios (instructors), recordings, evaluations.
- **Scenario designer** (instructors): build and edit scenarios.
- **Solo radar** (everyone): fly a scenario alone on the controller's screen, with simulated pilots.
- **Stats**: your statistics by role.
- **News**: aviation news, air traffic control first - open to everyone, without signing in. Your account gets the
  aviation newsletter every morning; unsubscribe (or subscribe again) in your **Profile**.
- **Help**: this documentation. The search box searches every page.
- **The bell** (top right): your notifications - invited to an exercise or a debrief, a scenario shared with you,
  an evaluation of your work, your account or role. The number is how many you have not opened; click one to go to
  it, or *Mark all read*. You get each one by e-mail too; turn the e-mails off in your **Profile** (the bell stays).
- On the radar, **?** (or the <kbd>?</kbd> key) shows the keyboard shortcuts.

The land and coastlines on the maps come from [Natural Earth](https://www.naturalearthdata.com) (1:10m, public domain).
