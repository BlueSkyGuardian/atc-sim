# Instructor and admin manual

## Home page

The home page has two parts. On top, **Live exercises**: a card per running exercise and a **New exercise** card.
Below, the **Workspace**: one tile per area, each summing it up (how many evaluations are waiting, the latest
recording, the scenarios per group) and opening that area's page. Inside a page the content is grouped in cards.
*Home* in the breadcrumb at the top of a page brings you back.

![The home page: live exercises on top, the other areas below](/static/docs/i_home.jpg "The home page: live exercises on top, the other areas below")

Each live exercise card is a live thumbnail (airspace and traffic, in 3D) with a status bar: elapsed sim time,
LIVE / PAUSED / READY, conflict state (red **LOSS**, amber **CONFLICT** within 3 min, or none), who is connected, a
**TX** tag on whoever is transmitting, aircraft waiting to be assumed. Click one to open it. **New exercise** opens the
scenario picker.

![Live exercises: a thumbnail per exercise with its status, conflicts and who is connected](/static/docs/i_dashboard.jpg "Live exercises: a thumbnail per exercise with its status, conflicts and who is connected")

| Workspace tile | Page |
|---|---|
| **Exercises** | Every exercise you ran or joined, live and past, with a search bar (see below) |
| **Evaluations** | The evaluations you still have to write, those you received and those you gave |
| **Recordings** | The videos of ended exercises: download, archive (hide), re-render, delete |
| **Scenarios** | Every scenario you can use, grouped by family, with author, complexity, density, traffic and duration |
| **Scenario designer**, **Statistics**, **Users** (admin), **Help** | Open those pages |

### Finding a past exercise

The *Exercises* page searches every exercise you ran or joined:

- **Search** matches the exercise name, the scenario name and title, the scenario author, and the names (and
  licence numbers) of the instructor, the controller and the pseudo-pilot. Several words must all match, in any
  field, so `hamza p05` finds Hamza's exercises on p05. Matches are highlighted.
- **From / to** keep the exercises created between two dates.
- **Seat** keeps those where you were instructor, controller or pseudo-pilot; **status** keeps live or past ones.
- **Sort**: newest or oldest first (grouped by month, with the running ones on top), name A–Z or Z–A, complexity
  (hardest or easiest first) or density (busiest or quietest first). Complexity and density are those of the scenario
  as it was when the exercise was created.

Each card shows the scenario's difficulty, who sat where, and **Open** for a live exercise.

## Scenarios

The *Scenarios* page groups the scenarios in grids: yours, those shared with you, the Agadir progression
(p01–p06), the Agadir band-boxed scenarios, and the training sector, each group from the easiest to the hardest.
Search by name, title or author, filter by airspace, or sort the whole list by name, complexity, density or traffic.
![Scenarios grouped by family, with author, complexity, density, airspace and access](/static/docs/i_scenarios.jpg "Scenarios grouped by family, with author, complexity, density, airspace and access")

Each card shows:

- **Name and title**.
- **Author**: who designed it (*library* for the built-in ones without an author).
- **Complexity · density**: five dots each, `~` when estimated rather than set by the designer. Underneath are the
  flights per hour and the duration.
- **Airspace**: `training` or `agadir`.
- **Access**: read/write or read-only.
- **Owner**, and whom it is shared with.

Actions:

- **Start exercise**: builds it paused and opens it.
- **Edit / View**: opens it in the designer. Saving a read-only scenario requires a new name.
- **Copy**: makes a private copy you can edit.
- **Share** (your own scenarios): gives another instructor read or read/write access.
- **Delete** (your own): the file is moved to the server's `deleted_scenarios` folder.

The library scenarios are read-only for everyone. Copy one to change it. See the [scenario library](scenarios.md)
for the progression from step 1 to the full CTA, and the [designer manual](designer.md) to build your own.

### Choosing a scenario for a trainee

Use the two ratings together. **Density** says how busy the frequency will be against the sector's declared
capacity. **Complexity** says how tricky the problems are. A good progression raises one at a time. The library's
`p01`–`p06` steps go from one sector at about half its capacity with two simple conflicts, to one sector at
capacity with sequences, weather and RVSM events. The band-boxed `w`, `o` and `c` scenarios follow, at 100–112 %
of capacity.

## Running an exercise

**Start exercise** builds the scenario paused. A dense scenario takes a few seconds. The exercise page has three
tabs on the side panel:

![Start exercise: name it, and it is built paused](/static/docs/i_new_exercise.jpg "Start exercise: name it, and it is built paused")

![The instructor's exercise page: the radar, the recording timeline and the side panel](/static/docs/i_seat.jpg "The instructor's exercise page: the radar, the recording timeline and the side panel")

### Exercise tab

- **Members and invitations**: invite people from the user list (name, licence) as **controller**,
  **pseudo-pilot** or **co-instructor**, or copy the controller / pseudo-pilot link and send it. Remove a member
  with ×.
- **Run / Pause**, **time factor** (×1 is real time), **Restart**, **Replay**.
- **Simulated pilots deviate around weather by themselves**: off by default. Off, only the pseudo-pilot makes
  aircraft deviate, and an unanswered weather request is closed as a task not handled. On, the old behaviour
  applies: an unanswered request ends in a deviation without clearance.
- **End exercise** closes it for everybody, keeps the results for the statistics and renders the recording.

![The Exercise tab: members and invitations, run controls, the weather option, End exercise](/static/docs/i_exercise_tab.jpg "The Exercise tab: members and invitations, run controls, the weather option, End exercise")

### Aircraft tab

The pseudo-pilot workspace (*To do*, control deck, aircraft list, shorthand) and the telephone. You can fly any
aircraft and call or answer as any unit. See the [pseudo-pilot manual](pilot.md).

### Monitor tab

The selected aircraft's details, the pilot requests, the arrival sequence, the predicted conflicts, the events and
the live metrics.

![The Monitor tab: requests, sequence, predicted conflicts, events and metrics](/static/docs/i_monitor.jpg "The Monitor tab: requests, sequence, predicted conflicts, events and metrics")

The radar is the same as the controller's (see the [controller manual](controller.md)), with the recording
timeline under the scope (**Timeline** in the toolbar hides it). Click or drag the timeline to look back. **▶ Replay** plays from the cursor, and
**Live** returns to the live picture. You hear every radio transmission and every telephone call.

The exercise page has a switcher to your other live exercises and a link back to the grid.

### Live actions

The **Live actions** box of the Exercise tab changes the exercise as it runs. The pseudo-pilots have the same box in
their panel.

![Live actions: add an aircraft, an emergency, a pilot request, switch an area](/static/docs/i_live.jpg "Live actions: add an aircraft, an emergency, a pilot request, switch an area")

| Tab | What it does |
|---|---|
| **Aircraft** | Adds a flight: callsign, type, level, route (entry point first), and when it appears: now at its first point, or entering in 5, 10 or 15 minutes (it then appears before the boundary and calls like any traffic). Its flight plan, SSR code, OLDI and CPDLC follow as for a scenario aircraft |
| **Emergency** | **Emergency descent** (squawk 7700, MAYDAY, the aircraft starts down to FL100), **medical** (7700, PAN PAN, then a request direct to an aerodrome: the one you type, else the nearest), **radio failure** (7600: no reply on the frequency, it flies its flight plan; CPDLC still works), **general** 7700 with your reason. The controller sees **EMG** or **RCF** and the squawk on the label. **Clear** ends it |
| **Pilot request** | An aircraft asks now for a level, a direct, a speed, a weather deviation, or says something in your words (a report to acknowledge) |
| **Areas** | Activates or deactivates an area until its next scheduled change |
| **Read-backs** | Hearback training: about 1 clearance in 20, 10 or 5 is read back wrong. In an exercise the pseudo-pilot gets the prompt (read back FL360 instead of FL350, fly it unless corrected) and reports whether the controller caught it; on the solo radar the simulated pilot does it, and the controller has a minute to correct it (enter it again). The count, caught and missed, goes to the statistics |

The pseudo-pilots read out the calls (MAYDAY, PAN PAN, requests) from their *To do*. Everything is logged, rewound
with the timeline and kept through a restart.

### If the server restarts

Running exercises (and solo runs) are saved every 10 simulated seconds. After a restart or a redeploy they come back
by themselves, paused at the last save (at most 10 simulated seconds earlier), with the controller's entries, CPDLC,
chat, the replay timeline and the rewind points. Everyone reconnects, and you press **Run**. The recording video goes
on, with a gap for the time the server was down.

## Ending, recordings and evaluations

**End exercise** asks for confirmation, then:

![Rating the controller after the exercise](/static/docs/i_evaluation.jpg "Rating the controller after the exercise")

1. Stops the simulation and stores the results (metrics, sim time) for the statistics.
2. Renders one **.mp4 per controller**: their screen as their seat showed it (labels, alerts, RBLs, windows,
   mouse and clicks, a caption log of their actions) with the audio they heard and said. An hour takes about
   10 minutes to render. With no controller connected, a plain radar overview is rendered instead.
3. Opens the evaluations. Rate each controller on phraseology, conflict detection, conflict resolution,
   anticipation and planning, coordination, and an overall grade (each out of 5), with a comment. The controller
   reads it, acknowledges it with a comment, and rates the pseudo-pilot.

## Statistics

*Stats → As instructor*:

- exercises run, hours simulated, pupils, evaluations given;
- the pupils' losses of separation by session number (the mean over pupils of their 1st, 2nd, 3rd… session with
  you: lower is better), one line per pupil;
- the mean grade by session number, exercises per month;
- a table per pupil (first / last / mean losses, mean grade).

A controller's sessions carry the scenario's complexity and density, so progress can be read against the
difficulty.

## Solo radar

*Solo radar* is open to every account: pick a scenario and fly it alone on the controller's screen, with
simulated pilots (see the [controller manual](controller.md#solo-radar-practising-alone)). Your own scenarios and
those shared with you are listed with the library. Solo runs count as practice in the statistics and are not
evaluated.

**Classic sandbox** (a button on the solo page, instructors only) is the former single-user radar: bare sessions from
any scenario, the instructor's side panel, instructions that move the aircraft directly through the shorthand line or
the aircraft panel. It is meant for demonstrations and for agents using the API.

### The control bar

In an exercise, the instructor runs the simulation from the control bar at the top right of the radar: **Run** /
**Pause** (key <kbd>P</kbd>), the time factor ×1 to ×30, **+10 s** (pause and advance), **⟲** restart. The state
(READY, RUNNING, PAUSED) and the exercise time are shown in it. The pseudo-pilots have the same bar.

### Rewinding the simulation

The timeline under the scope records the run. Click or drag it to look back; the picture shows that moment (REPLAY
in the information box) while the simulation goes on. **⤺ Resume from T+…** then puts the simulation itself back to
that moment for every seat: aircraft, clearances in the system, requests, CPDLC and chat as they were, and the
statistics too. It stays paused until **Run**. The simulation keeps a point every 10 seconds, so the cursor goes back
to the 10-second mark before it. What happened after that point is undone (the recording video keeps it).

## Administrator

The administrator account (`ATC_ADMIN_EMAILS`) has the **Users** page: every account with name, licence, role and
status. Set the roles there:

![The Users page: set each account's role](/static/docs/a_users.jpg "The Users page: set each account's role")

| Role | Can |
|---|---|
| **Administrator** | Everything, plus user roles |
| **Instructor** | Scenarios (designer, sharing), exercises, recordings, statistics of their pupils, solo radar |
| **Controller** (default for new accounts) | Join exercises as invited, their recordings, evaluations and statistics |
| **Pseudo-pilot** | Join exercises as invited, their ratings |

Users complete their profile (first name, last name, licence number) at the first sign-in. Deployment, Google
sign-in and HTTPS are described in `DEPLOY.md` in the repository.
