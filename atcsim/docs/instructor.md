# Instructor and admin manual

## Home page

| Section | What it holds |
|---|---|
| **Live exercises** | A live thumbnail of each running exercise (airspace and traffic) with a status bar: elapsed sim time, LIVE / PAUSED / READY, conflict state (red **LOSS**, amber **CONFLICT** within 3 min, or none), who is connected, a **TX** tag on whoever is transmitting, aircraft waiting to be assumed. Click one to open it. **+ New exercise** starts one |
| **Recordings** | The videos of ended exercises: download, archive (hide), re-render, delete |
| **Scenarios** | Every scenario you can use, with author, complexity, density, aircraft per hour and duration |
| **Evaluations** | The evaluations you still have to write, and the ones written |

![Live exercises: a thumbnail per exercise with its status, conflicts and who is connected](/static/docs/i_dashboard.jpg "Live exercises: a thumbnail per exercise with its status, conflicts and who is connected")

## Scenarios

The list shows, for each scenario:

![The scenario list with author, complexity, density, airspace and access](/static/docs/i_scenarios.jpg "The scenario list with author, complexity, density, airspace and access")

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
timeline under the scope. Click or drag the timeline to look back. **▶ Replay** plays from the cursor, and
**Live** returns to the live picture. You hear every radio transmission and every telephone call.

The exercise page has a switcher to your other live exercises and a link back to the grid.

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

*Solo radar* opens the single-user radar. It creates a session from any scenario without seats, radio or
evaluations. It is for practice, demonstrations and agents. The controller's commands there move the aircraft
directly, through the shorthand line or the aircraft panel. Solo sessions count as practice in the statistics.

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
