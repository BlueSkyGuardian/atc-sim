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

```
TIME T+05:00 (exercise 45:00), UTC 13:05
SEPARATION 7 NM or 1000 ft (2000 ft for non-RVSM aircraft in RVSM airspace). Levels you can assign: FL100-FL460.

YOUR AIRCRAFT (assumed) — 12:
- EWG3KP · A320 · FL350= · trk 229 gs 356 · M78 · direct IBALU 50NM then ERMED · to GCFV · exit FL350 to WS
- RAM2880A · B738 · FL240= · trk 012 gs 444 · 300kt · direct OSLAD 94NM · to LEMD · exit FL240 to LECS
CALLING YOU (first call on your frequency, waiting for you: answer with assume) — 1:
- IBE4343 · B738 · FL350= · trk 204 gs 407 · M78 · direct CTAVE 184NM · to GCRR · exit FL350 to WS · called 00:12 ago
LEFT YOUR SECTOR, still assumed (transfer them: decontrol) — 1:
- NOZ7983 FL300, left 00:40 ago
INBOUND (not on frequency yet) — 7:
- EZY7537K A319 FL330 entering at KORNO in 01:55, route KORNO ABODA IBALU ERMED to GCFV
PILOT REQUESTS waiting for your answer — 1:
- R001: RAM2880A requesting FL340 (requesting cruise level), waiting 03:22
PREDICTED CONFLICTS (system trajectories, next 20 min) — 1:
- EWG3KP × IBE3247: in 03:20, closest 3.7 NM / 0 ft (levels 350/350)
```

plus weather ahead of an aircraft, current losses of separation and active areas. What is shown is set in the agent
profile (conflicts, weather, inbound traffic, routes, list length).

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
| +1 | per calling aircraft assumed, per pilot request answered, per aircraft that had left transferred |
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
