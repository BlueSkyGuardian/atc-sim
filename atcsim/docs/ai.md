# AI lab

*AI lab* (top bar, instructors and administrators) lets a language model control the simulation: it reads the
situation, gives instructions, and each run is evaluated and kept as labelled experience data — to compare models and
prompts, and to train a model (on RunPod or elsewhere) from what worked.

![AI lab: new runs, and a preview of each run with its score](/static/docs/ai_runs.jpg "AI lab: new runs, and a preview of each run with its score")

## How a run works

A run is a scenario played **headlessly**, as fast as the computer goes, with the model as the controller:

1. At each **decision point** the simulation stops and the model gets the situation as text (below), with the result of
   its previous instructions (read-backs, *unable*, errors). Decision points come every *n* simulated seconds (the agent
   profile), and at once when an aircraft calls, a pilot request comes, or a new conflict under 5 minutes appears.
2. The model answers with JSON: a short reasoning and a list of instructions from a fixed set.
3. The instructions are applied exactly like a controller's label entries: **synthetic pilots** fly them and read them
   back, or say *unable* (weather, performance…). Everything is logged.
4. At the end the run is **evaluated** (score, a label and a reward per decision) and kept: open it with **Replay in
   the debrief** — the whole run 1:1, with every timeline tool (branch it at a decision, try another instruction).

Runs go in a queue, a few at a time (each in its own process); a model that does not answer (cold start, error) is
retried, and the traffic goes on if it still does not.

### What the model sees

Once per run, with the system prompt (so with every request): a **sector briefing** — the sector and its
boundary, the coordinates used (x east, y north, in NM; how to get a heading and a distance from them), every point
with its position, the main routes and airways, the entry and exit points, the separation rules (horizontal minimum,
1000 / 2000 ft with RVSM, above FL410, military formations), the semicircular rule, and how aircraft behave (a
heading is flown until the controller clears them again).

At each decision, the situation, starting with the work waiting:

```
TIME T+06:40 (exercise 45:00), UTC 13:07
SEPARATION 7 NM or 1000 ft (2000 ft with a non-RVSM aircraft in FL290-410, and above FL410). Levels you can assign: FL100-FL460.

TO DO NOW:
- ASSUME (they are calling you; answer them all now): IBE4343 (waiting 03:45), BAW9522 (waiting 03:03)
- TRANSFER = decontrol (they have left your sector): RYR1173X, NOZ7983
- ANSWER the pilot requests: R001, R002
- SOLVE the conflicts: NOZ4381P×EZY7537K in 04:10
- WATCH (on a heading, not clear yet: keep the heading or solve otherwise, then resume): EWG3KP

YOUR AIRCRAFT (assumed) — 12:
- EWG3KP · A320 · at (159,131) · FL350= · trk 270 gs 340 · M78 · ON HEADING 270 (off its route, route continues IBALU ERMED)
  · to GCFV · exit FL350 to WS · leaves your sector in 158NM · your last instruction: HEADING 270 (04:40 ago)
  · resuming now would conflict with IBE3247 in 03:20 (resumed at FL370: no conflict)
- IBE3247 · A21N · at (160,123) · FL350= · trk 217 gs 372 · M78 · direct IBALU 43NM then ERMED · to GCLP · exit FL350 to WS
CALLING YOU ... · called 03:45 ago
LEFT YOUR SECTOR, still assumed (transfer them: decontrol) ...
INBOUND (not on frequency yet: plan for them, you cannot instruct them) ...
PILOT REQUESTS waiting for your answer — 2:
- R001: RAM2880A requesting FL340 (requesting cruise level), waiting 05:02 · probe: no conflict at FL340
PREDICTED CONFLICTS (system trajectories as cleared now, next 20 min) — 1:
- NOZ4381P × EZY7537K: in 04:10, closest 2.1 NM / 0 ft (levels 350/350; needs 7 NM or 1000 ft); now 21 NM apart,
  EZY7537K bears 035 from NOZ4381P; tracks 229/236 (same direction, 7°); both route via IBALU: NOZ4381P in 05:12,
  EZY7537K in 05:40 (28 s apart); conflict-free levels for NOZ4381P: 370, 330
```

Each aircraft: position, level and trend, route ahead, RVSM status when not approved, exit level and next unit,
distance to leaving the sector, weather ahead, **the last instruction it was given**, and for an aircraft on a
heading whether resuming now is conflict-free (or at which level it would be). Each conflict: the geometry (distance
and bearing now, the angle between the tracks, a common point with both times) and the conflict-free levels — the
probe, as in the controller's label menu. Earlier decisions are sent again (the profile's *memory*) as their TO DO
part and the model's answer only, so a stale picture does not mislead it.

What is shown is set in the agent profile: TO DO list, positions and geometry, probe results, conflicts, weather,
inbound traffic, routes, list length, and the sector briefing. About 1,500 tokens of briefing and prompt, plus
1,000-3,000 per situation (busy sectors): give the endpoint a context of 8192 tokens, 16384 for dense scenarios.

### Rules and corrections

With **rules** on (agent profile, on by default), a clearance that breaks the semicircular rule — a cruising or exit
level not of the aircraft's direction — is refused with the levels to use (*FL360 is not a level of IBE4343's
direction (southbound: odd levels; e.g. 350, 370)*); arrivals descending to their destination are exempt. A
descent given to an arrival long before its top of descent, when it has not asked, is given but noted (*early*) and
counts against the decision; a field the instruction set does not have is ignored and reported.

With **corrections** (1 round by default), an answer with refused instructions, unknown fields or that cannot be read
goes straight back to the model in the same decision (*YOUR ANSWER HAD ERRORS. The other instructions were given;
these were NOT: ...*), and its corrected instructions are given. The first mistakes still count in the evaluation; the
correction turns are kept in the chat datasets. The next situation starts with the errors of the last decision.

Arrivals show their **top of descent** (*top of descent in 60NM (it will ask for descent there: keep it at its level
until then), latest descent for the agreed level in 66NM*): the pilot asks for descent there.

### Priority, speed, what was left undone

Each conflict line says which aircraft to move: an aircraft maintaining its level has priority in it, so the one
climbing or descending (or not at its requested level) moves (*PRIORITY: IBE3247 is level at FL350 and keeps
priority; EWG3KP is changing level → move EWG3KP, not the other*), and the conflict-free levels are given for that
one. For aircraft in trail to a common point it says whether speed works and which (*speed works: IBE3247 Mach 0.74
(now 0.78) gives 9 NM behind EWG3KP at IBALU*), or that it does not. A conflict still there at the next decisions
is marked *shown since T+05:30 and still unsolved*.

After each decision, what the model left undone from its TO DO is fed back with the next situation (*LEFT UNDONE
FROM YOUR TO DO: not assumed (still calling you): IBE4343; conflicts left unsolved: EWG3KP×IBE3247*), and shown
in *Decisions*.

### Arrival sequences

Arrivals are the flights whose exit is a sequencing fix (e.g. MARJA and SALCA for GMMN). Each one's line says so
with the letter of agreement (*ARRIVAL via SALCA: level FL250, 10 NM in trail, latest descent in 34NM*), and
**ARRIVAL SEQUENCES** lists them in order at each fix, with the time to the fix and the distance behind the one
ahead (*TOO CLOSE* under the agreed spacing). TO DO adds *SEQUENCE* when one is too close or must start down soon.
Aircraft on the same route (en-route traffic) appear in the conflict lines with their common point and both times.

### Queries: the model asks before it decides

With *queries* on (agent profile, on by default, up to 3 rounds per decision), the model may answer
`{"reasoning": "...", "queries": [...]}` instead of instructions; the simulator answers with the radar's own tools,
and the model answers again (instructions, or more queries). The simulation waits meanwhile.

| Query | Answer |
|---|---|
| `{"q": "probe", "callsign": "X", "cfl": 370}` (or `heading`, `direct`, together) | the conflicts that clearance would give, over the MTCD look-ahead (and whether the level is of its direction) |
| `{"q": "levels", "callsign": "X"}` | its conflict-free levels within ±6000 ft, and the others with their conflict; in a loss of separation, the levels that separate soonest |
| `{"q": "geometry", "a": "X", "b": "Y"}` | distance and bearing now, tracks, closing speed, vertical distance, closest point of approach straight ahead |
| `{"q": "distance", "from": "X", "to": "IBALU"}` | distance, bearing (and time, from an aircraft), between aircraft and/or points |
| `{"q": "route", "callsign": "X"}` | its route ahead: points with distances and times |
| `{"q": "near", "point": "IBALU", "radius_nm": 30}` | traffic within a radius of a point or an aircraft |
| `{"q": "sequence", "fix": "MARJA"}` | the arrivals to that fix in order, spacing, the agreed level |

The questions and answers are kept with the decision (shown in *Decisions* and in *Watch live*), and the **chat**
datasets include them as turns of the conversation, so a model trained on them learns to check before acting.

### The instruction set

```
{"reasoning": "...", "actions": [
  {"type": "assume", "callsign": "IBE4343"},
  {"type": "clear", "callsign": "EWG3KP", "cfl": 360},          also "heading", "direct", "speed" ("M76", "280"), "xfl"
  {"type": "resume", "callsign": "EWG3KP"},                      back on its route after a heading
  {"type": "reply", "request_id": "R001", "decision": "approve"},   or "unable"
  {"type": "decontrol", "callsign": "NOZ7983"}]}
```

Only assumed aircraft take clearances: an aircraft calling is answered with *assume* (the assume and its clearances
may go in the same answer), and an aircraft that has left the sector is transferred with *decontrol*. An answer that
is not readable JSON, or an instruction that is refused, is fed back to the model at the next decision point with
what to do instead (e.g. *IBS7794 is not assumed, nothing was given to the pilot; it is calling you: answer it first
with {"type": "assume", "callsign": "IBS7794"}, then clear it*), and counts in the evaluation. A field the
instruction set does not have is ignored and reported.

The radio log of the replay shows the controller's side too: *identified* when the model assumes an aircraft,
*contact (next unit)* when it transfers one.

## Models

**Models** tab → *Add a model*:

- **RunPod serverless (vLLM)**: the endpoint id of a vLLM worker and your RunPod API key. The simulator calls its
  OpenAI-compatible route `https://api.runpod.ai/v2/<endpoint id>/openai/v1`; the *model name* is the Hugging Face
  model deployed (or its `OPENAI_SERVED_MODEL_NAME_OVERRIDE`). A serverless endpoint may take a minute to wake up:
  keep the timeout generous (180 s) and a few retries.
- **OpenAI-compatible server**: any server with `/v1/chat/completions` — a RunPod pod running vLLM, TGI or Ollama
  (`https://<pod id>-8000.proxy.runpod.net/v1`), or another provider. Its base URL, model name and key.
- **Built-in baseline**: a scripted controller (assumes what calls, answers requests with a conflict probe, resolves
  conflicts under 8 minutes with a level change checked by the probe). A reference score for the models, and a way to
  produce experience data without a model.

**Thinking** (off by default): reasoning models — Qwen3, DeepSeek-R1 and their kin — write a long hidden reasoning
before every answer, often thousands of tokens: minutes per decision on a 27B model. *Off* sends
`enable_thinking: false` (vLLM chat templates) and, for Qwen3, `/no_think`; a `<think>` part left in an answer is
never taken for the answer. Each decision in *Decisions* shows the tokens it generated and the speed (tokens per
second), so a model that thinks shows at once.

**Context length** is the endpoint's maximum model length (RunPod: *MAX_MODEL_LEN*): every prompt is kept under it,
minus the answer length — earlier decisions are left out first, then further queries (each decision says so, with
its prompt size in tokens). A prompt over the endpoint's limit is refused or cut by the server, and the model then
loses its instructions: set it right. Before the clock starts, the simulation is advanced to its first picture (the scenario's aircraft appear at the first second) and the model is woken with a one-line test call: a model that does not answer then ends the run before anything is simulated. That first call waits at least 10 minutes (a serverless endpoint may be waking up); after that, a call
that gets no answer within the timeout is **not sent again** — the endpoint may still be working on it, and on RunPod
a timed-out request stays queued and billed — the decision is skipped and the traffic goes on. Retries are only for
requests that never reached the server (connection refused, 5xx, 429). After 3 decisions in a row without an answer
the run stops by itself, to save cost. **Stop** interrupts a run at once, even in the middle of a model call, and it
is still evaluated.

Temperature, answer length, timeout, retries and JSON mode (`response_format`, when the server supports it) are per
model. **Test** checks the connection (the models served, one short answer and its latency). API keys are stored
encrypted and never shown again (leave the field empty when editing to keep the key).

![A model's settings](/static/docs/ai_models.jpg "A model's settings")

## Agent profiles

The **system prompt** (the role, the priorities, and the instruction set — keep it at the end: answers are read as
that JSON), how often the model decides, how many previous decisions it is sent again (memory), how many instructions
it may give at once, and what the situation shows. **Default prompt** restores the original text.

## New runs

Choose scenarios (several at once), the model, the agent profile, how many runs per scenario, and the environment:

- **pilots avoid weather by themselves** (on by default) — off: they fly what they are cleared to and only ask;
- **pilots comply with everything** — no *unable* for weather or performance;
- **read-back errors** — synthetic pilots sometimes read back a wrong value;
- **assume automatically** / **transfer automatically** — the system does them, the model only separates and
  sequences (a simpler task, to start with or for a small model);
- **stop at** — a shorter run than the scenario.

### Watch live

**● Watch live** on a running run opens it on the radar (look only): the traffic as the model controls it, and a
panel with its latest decisions — the reasoning, each instruction with the pilot's answer, *the model is deciding*
while it waits for the model (the simulation waits too). **Speed while you watch** paces the run at ×1 to ×16
(simulated seconds per second); **Full** lets it go as fast as the model answers (the picture jumps). Nobody
watching: the run goes at full speed again. When it ends: **Replay in the debrief**.

Each run's card shows its progress (simulated time, decisions, the last instructions), then its **preview** (every
track, red circles where separation was lost), its score, and buttons: **Decisions** (every decision: the situation
shown, the reasoning, the instructions and the pilots' answers, the label and its reasons, the raw answer), **Replay in
the debrief**, **Judge**, **Stop**, **Delete**.

![A run's decisions, with their labels](/static/docs/ai_steps.jpg "A run's decisions, with their labels")

## Evaluation

**Score** (0–100): 100 minus penalties, each listed with its count — loss of separation 25 each and 2 per minute of
loss; infringement of an active area 10; weather penetration 5; RVSM violation 5; wrong-direction exit 3; request
expired 4, still pending at the end 2; slow answers (mean over 60 s) 1 per 30 s; aircraft never assumed 3, assumed more
than 30 s after calling 1; aircraft not transferred 2 minutes after leaving the sector 2, transferred late 1;
instruction refused 1; unreadable answer 2; extra track miles 0.05 per NM.

**Each decision** gets a reward and a label from what happened after it:

| | |
|---|---|
| +1 | per calling aircraft assumed, per pilot request answered, per aircraft that had left transferred, per aircraft resumed when shown clear to resume |
| −1 | per aircraft left on a heading though shown clear to resume |
| −1 | per aircraft calling it left unanswered, per aircraft that left over 2 min ago it did not transfer |
| +3 | per predicted conflict it was shown (under 10 min) and acted on, that did not become a loss |
| −8 | per loss starting in the 10 minutes after it, between aircraft it acted on |
| −5 | per conflict it was shown under 5 min and left alone, that became a loss |
| −2 | per request waiting more than 2 min it left unanswered |
| −1 / −2 | per instruction refused / for an unreadable answer |

*good* (reward above 0), *bad* (below 0), *neutral*.

**Judge** (optional): a second model grades each decision (or every *n*-th) from 1 to 5 with a critique, from the
situation, the instructions and what followed. Its grades go with the experiences.

## Experiences and datasets

Every decision of a finished run is an experience. The **Experiences & datasets** tab selects them (scenarios,
models, runs scoring at least…, labels, judge grade at least…) and exports them as a **dataset** (JSONL):

- **chat** — one fine-tuning example per decision: `{"messages": [system, situation, the model's answer], "meta":
  {label, reward, episode score, judge grade…}}`. Typically: the *good* decisions, for supervised fine-tuning.
- **trajectories** — one line per run: every decision with its reward, and the run's score and penalties (for
  reinforcement learning or offline RL).
- **steps** — the decisions as recorded (the situation's structure too).

**Download** it, or use its **RunPod link** on the training machine (`wget -O /workspace/data/atc.jsonl "<link>"`):
whoever has the link can download the dataset, so give it to the pod, not in public places.

The run card also shows *first calls answered* and *transferred* (out of how many).

![Selecting experiences and exporting a dataset](/static/docs/ai_data.jpg "Selecting experiences and exporting a dataset")
