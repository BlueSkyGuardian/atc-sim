# Pseudo-pilot manual

The pseudo-pilot speaks for every aircraft on the frequency and flies them: you read out the calls and requests,
listen to the controller, read back, and make the aircraft do what was cleared. You also play the **adjacent
units** on the telephone (Casablanca, Lisboa, Canarias, Sevilla, Madeira).

![The pseudo-pilot seat: the radar on the left, the workspace on the right (To do, control deck, aircraft list)](/static/docs/p_seat.jpg "The pseudo-pilot seat: the radar on the left, the workspace on the right (To do, control deck, aircraft list)")

Your seat has the radar (the same as the controller's, with every display option) and a workspace in three parts:
**To do**, the **control deck** of the selected aircraft, and the **aircraft list**.

> Selecting an aircraft on the radar does not move the keyboard into a text field. The **space bar** stays free for
> the radio. Click a field when you want to type in it.

## To do: what to say

The list shows what to say on the frequency, oldest first:

![The To do list: a weather request to read out, a first call, and two aircraft dealing with weather](/static/docs/p_todo.jpg "The To do list: a weather request to read out, a first call, and two aircraft dealing with weather")

- **First calls**: *"RAM101, FL350, checking in"*. These stay until the controller assumes the aircraft.
- **Pilot requests and reports** to read out: level, direct, speed, weather deviations (*"request heading 170 for 65
  miles due weather, then direct SAMAR"*), climbs over weather tops, turbulence (ride) level changes, descent at
  the top of descent, unable RVSM.
- Each item has **Approved** (the aircraft does it), **Refused** and **Acknowledged** (reports). Answer what the
  controller answered on the frequency.
- **WX items** list the aircraft that are dealing with weather (see [Weather](#weather)).

The simulated pilots generate the requests: a deviation when their onboard radar sees weather ahead on their path,
a ride level after a while in turbulence, a descent at their top of descent. The scenario adds scripted ones. You
choose how to phrase them.

## CPDLC: data link

Most aircraft are CPDLC equipped (J1 to J7 in item 10). The controller can send them clearances by data link
instead of the frequency. You answer for the crew.

![The CPDLC section: an uplink to answer, then the message log](/static/docs/p_cpdlc.jpg "The CPDLC section: an uplink to answer, then the message log")

- **Uplinks** appear in the **CPDLC** section of the side panel, under *To do*, with the message in clear (`CLIMB TO
  FL350 / MAINTAIN M.78`). A new one is highlighted, the section title turns cyan with the count to answer, and the
  aircraft's label on your radar shows `CPDLC↓` until you answer. Under them, the log of the last messages
  (from ATC, sent), with their state.
  - **WILCO**: the aircraft does all of it, and the clearance goes in the controller's system. If the aircraft cannot
    fly it (performance, weather), WILCO is refused with the reason. Answer **Unable** instead.
  - **Standby** keeps it open. **Unable** refuses it; **…wx** and **…perf** add *due to weather* / *due to aircraft
    performance*.
  - Free text is answered **Roger**.
  - **CONTACT / MONITOR**: WILCO is the transfer to the next unit. The data link connection goes there too.
- **Requests by data link**: on a REQ item of a connected aircraft, **By CPDLC** sends it as a downlink instead of
  saying it. The item then shows *sent by CPDLC* until the controller answers. The answer arrives as an uplink (the
  clearance, to WILCO), or as UNABLE / STANDBY shown in the CPDLC section.
- The deck has a **CPDLC** row: the connection state, and a request of your own: level, direct, speed, weather
  deviation (`L20`, `R15`, `20` either side) or free text, with an optional reason.

The controller can still use the frequency for anything. What you answer on the frequency is answered with the
usual buttons.

## The control deck

Select an aircraft (in the list, in *To do*, or on the radar) to get its deck. The deck shows the present level,
speed, heading and navigation, what the aircraft is executing, and its limits now. Then comes one row per action:

![The control deck of the selected aircraft: what it can do now, then one row per action](/static/docs/p_deck.jpg "The control deck of the selected aircraft: what it can do now, then one row per action")

| Row | What you can do |
|---|---|
| **Level** | A value, ±10 / ±20 buttons, an optional minimum rate, then *Climb / Descend FLxxx* |
| **Heading** | Left / right / shortest turn to a heading; *resume own navigation* |
| **Speed** | Mach, knots now, knots on conversion; *resume normal* |
| **Direct** | The next points of the route as buttons, or any point typed |
| **Route** | *Fly via* the points typed (a re-routing), or *Edit on map…* (the same editor as the controller's) |
| **Weather** | Deviation left / right / a heading, with a distance; **Clear of weather** |

The result, or the aircraft's *unable …*, appears under the row. <kbd>Enter</kbd> validates a field.

### Can do now

The deck shows the aircraft's **present limits** and refreshes them every 10 s:

- the levels it may be given (FL100 to the sector's top, or FL50–FL600 once it is in another sector) and its
  maximum level at this weight;
- the climb rate available now, the idle descent rate (and with speed brakes), the turn radius;
- the assignable Mach and IAS range and its own speed schedule (Mach / IAS / conversion level).

Rates the aircraft cannot fly are greyed out. Use these limits to make realistic requests when you go off script
(*"unable FL390 at this weight, can accept FL370"*).

### Shorthand line

The *Shorthand* line under the aircraft list accepts the command syntax, for speed:

| Command | Meaning |
|---|---|
| `RAM101 FL360` | Climb / descend FL360 (`V1500` after it sets the rate) |
| `RAM101 H270`, `L270`, `R270` | Heading (forcing the turn direction with L / R) |
| `RAM101 D IBALU`, `RES` | Direct to a point, resume own navigation |
| `RAM101 M76`, `K280`, `S280`, `SN` | Mach, IAS on conversion, IAS now, resume normal speed |
| `RAM101 FL250 M76 K280` | Descend FL250, maintain Mach .76, on conversion 280 kt |
| `RAM101 DEVL20` | Weather deviation left up to 20 NM (`DEV`, `DEVR`…) |
| `RAM101 APP` / `UNA` / `ROG` | Approve / refuse a request, acknowledge a report |

## Weather

In an exercise the aircraft **never deviate by themselves**. You make them deviate when the controller approves
it, from the deck's Weather row or the request's *Approved* button.

- A heading or direct into weather is executed when you give it, with a note. Nothing blocks you.
- The system tracks every aircraft **dealing with weather**: it asked for a deviation, was cleared one, the
  controller entered a WX tag, or you made it deviate. Weather such an aircraft flies through is **not** counted as
  a penetration for the controller.
- When the aircraft is clear, press **Clear of weather** (deck) or the *WX* item's button, and say *"clear of
  weather"* on the frequency. From then on, weather counts again.
- A weather request that nobody answers before the weather is 15 NM ahead is closed as *not answered*. The
  controller's statistics count it as a task not handled.

The instructor can switch back to the old behaviour (simulated pilots deviate by themselves) in the Exercise tab.

## Aircraft list

The list holds every aircraft with its level, speed, navigation and status (calling, request, on frequency, still
with the previous sector), with the ones needing something first.

## Telephone: playing the adjacent units

- When the controller calls a unit, your telephone rings: answer as that unit.
- Call the controller as any unit from the *Telephone* panel: pick the unit, call, and talk. Calls are full duplex
  until hung up.
- The OLDI window (<kbd>O</kbd>) and the deck show the coordination the system made (ABI / ACT / REV, the exit
  level coordinated for the selected aircraft). This is what the next unit "knows" when the controller calls.

## Coordination chat

Below the telephone, write to the controller as another station: an Agadir sector not controlled in the exercise
(`GMAC WN`…) or an adjacent unit (`GCCC`…). Pick the station in *As* and the destination in *to* (the controller's
sector by default). *about* takes the selected aircraft. The newest messages are on top, each with its sender →
destination. Messages from the controller have quick answers (APPROVED, UNABLE, STANDBY, ROGER), sent back as the
station they were addressed to. The instructor can write as any station.

![The pseudo-pilot's chat: writing as GCCC to GMAC WS](/static/docs/p_chat.jpg "The pseudo-pilot's chat: writing as GCCC to GMAC WS")

## Radio

Hold the **space bar** (outside a text field) or the on-screen button to transmit. Everyone hears it, and the
radar shows who is talking.

## After the exercise

The controller rates you: readbacks, execution of clearances, reaction time, R/T, realism and an overall grade,
with a comment. *Stats → As pseudo-pilot* shows the ratings over time, your average profile and the comments.
