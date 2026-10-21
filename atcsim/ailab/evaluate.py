"""The outcome of a headless run: a score with its components, and a label and a reward for every decision.

Score (0-100): 100 minus penalties, each listed with its count, so a score can always be explained:
  loss of separation 25 each + 2 per minute of loss, infringement of an active area 10, weather penetration 5,
  RVSM violation 5, wrong-direction exit 3, request expired 4 / still pending at the end 2, slow answers (mean over
  60 s) 1 per 30 s, aircraft never assumed after calling 3, assumed more than 30 s after calling 1,
  aircraft not transferred 2 min after leaving 2, transferred late 1, instruction refused (unable / invalid) 1, answer that could not be read 2, extra track miles 0.05 per NM.

Per decision (experience labels), from what happened after it:
  +1   per calling aircraft it assumed, +1 per pilot request it answered
  +3   per predicted conflict it was shown (under 10 min) and acted on, that did not become a loss
  -8   per loss starting in the 10 minutes after it, between aircraft it acted on or was shown in conflict
  -5   per conflict it was shown under 5 min and left alone (no action on either aircraft) that became a loss
  -2   per request waiting more than 2 min that it left unanswered
  +1   per aircraft that had left the sector it transferred (decontrolled)
  +1   per aircraft on a heading shown as clear to resume that it resumed (resume or a direct); -1 if left on it
  -1   per aircraft calling it left unanswered, per aircraft that left over 2 min ago it did not transfer
  -1   per instruction refused, -2 if its answer could not be read
  label: good (reward > 0), bad (< 0), neutral (0).
"""
from __future__ import annotations

from typing import List

LOOK_S = 600.0


def transfers(w) -> dict:
    """Aircraft that left the sector (still assumed) and when they were decontrolled (transferred)."""
    left, rel = {}, {}
    for e in w.events:
        if e["type"] in ("aircraft_exited", "aircraft_left_sector"):
            left.setdefault(e["callsign"], e["time_s"])
        elif e["type"] == "released":
            rel[e["callsign"]] = e["time_s"]
    out = {"left": 0, "late": [], "never": [], "delays": []}
    for cs, t in left.items():
        ac = w.aircraft.get(cs)
        if ac is None or (ac.released and cs in rel and rel[cs] < t):
            continue                                       # decontrolled before it left
        out["left"] += 1
        if cs in rel and rel[cs] >= t:
            out["delays"].append(rel[cs] - t)
            if rel[cs] - t > 120:
                out["late"].append(cs)
        elif ac.assumed and w.t - t > 120:
            out["never"].append(cs)
    return out


def first_calls(w) -> dict:
    """First calls: how many, how many answered (assumed), how long they waited."""
    calls = [a for a in w.aircraft.values() if a.first_contact_t is not None and a.first_contact_t > 1.0
             and (a.assumed_t is not None or w.t - a.first_contact_t > 60)]       # (a call in the last minute: no time yet)
    waits = [a.assumed_t - a.first_contact_t for a in calls if a.assumed_t is not None and a.assumed_t >= a.first_contact_t]
    return {"calls": len(calls), "answered": len(waits), "mean_wait_s": round(sum(waits) / len(waits), 1) if waits else None,
            "max_wait_s": round(max(waits), 1) if waits else None}


def score(w, steps: List[dict]) -> dict:
    m = w.metrics()
    sep, asp, wx = m.get("separation") or {}, m.get("airspace") or {}, m.get("weather") or {}
    req, asm, tr = m.get("requests") or {}, m.get("assumption") or {}, m.get("traffic") or {}
    refused = sum(1 for s in steps for r in s.get("results") or [] if not r.get("ok"))
    unread = sum(1 for s in steps if s.get("parse_error") or s.get("unreadable_first"))
    mean_rt = req.get("mean_response_time_s")
    xf, fc = transfers(w), first_calls(w)
    pen = [
        ("losses of separation", sep.get("loss_events", 0), 25),
        ("minutes in loss of separation", round((sep.get("loss_seconds") or 0) / 60, 1), 2),
        ("area infringements", asp.get("infringement_events", 0), 10),
        ("weather penetrations", len(wx.get("penetrations") or []), 5),
        ("RVSM violations", len((m.get("rvsm") or {}).get("violations") or []), 5),
        ("wrong-direction exits", len((m.get("semicircular") or {}).get("wrong_direction_exits") or []), 3),
        ("requests expired", (req.get("by_status") or {}).get("expired", 0), 4),
        ("requests still pending at the end", (req.get("by_status") or {}).get("pending", 0), 2),
        ("slow answers (30 s over 60 s mean)", round(max(0.0, ((mean_rt or 0) - 60) / 30), 1), 1),
        ("aircraft never assumed", len(asm.get("never_assumed") or []), 3),
        ("assumed late (over 30 s)", asm.get("late_over_30s", 0), 1),
        ("aircraft not transferred (still assumed 2 min after leaving)", len(xf["never"]), 2),
        ("transferred late (over 2 min after leaving)", len(xf["late"]), 1),
        ("instructions refused or invalid", refused, 1),
        ("answers that could not be read", unread, 2),
        ("extra track miles", round(tr.get("extra_track_miles") or 0, 1), 0.05),
    ]
    comps = [{"what": k, "count": c, "per": p, "penalty": round(c * p, 1)} for k, c, p in pen if c]
    total = max(0.0, 100.0 - sum(x["penalty"] for x in comps))
    return {"score": round(total, 1), "penalties": comps,
            "losses": sep.get("loss_events", 0), "min_sep_nm": min([x["min_horizontal_nm"] for x in sep.get("losses") or []
                                                                   if x.get("min_horizontal_nm") is not None], default=None),
            "infringements": asp.get("infringement_events", 0), "requests": req.get("total", 0),
            "requests_expired": (req.get("by_status") or {}).get("expired", 0), "mean_response_s": mean_rt,
            "assume_mean_s": asm.get("mean_delay_s"), "never_assumed": len(asm.get("never_assumed") or []),
            "first_calls": fc, "transfers": {"left": xf["left"], "never": len(xf["never"]), "late": len(xf["late"]),
                                             "mean_delay_s": round(sum(xf["delays"]) / len(xf["delays"]), 1) if xf["delays"] else None},
            "decisions": len(steps), "actions": sum(len(s.get("results") or []) for s in steps),
            "refused": refused, "unreadable": unread, "aircraft": tr.get("total"), "sim_time_s": round(w.t, 1)}


def label(w, steps: List[dict]) -> None:
    """Reward and label each decision in place (see the module's text)."""
    losses = [(float(x.get("start_s") or 0), set(x.get("aircraft") or [])) for x in (w.metrics().get("separation") or {}).get("losses") or []]
    for i, s in enumerate(steps):
        t = float(s["t"])
        acted = {str(r["action"].get("callsign", "")).upper() for r in s.get("results") or [] if r.get("ok")}
        acted.discard("")
        shown = s.get("struct") or {}
        conf = [c for c in shown.get("conflicts") or [] if c.get("in_s", 1e9) <= LOOK_S]
        r, why = 0.0, []
        n_asm = sum(1 for x in s.get("results") or [] if x.get("ok") and x["action"].get("type") == "assume")
        if n_asm:
            r += n_asm
            why.append(f"+{n_asm} assumed")
        n_rep = sum(1 for x in s.get("results") or [] if x.get("ok") and x["action"].get("type") == "reply")
        if n_rep:
            r += n_rep
            why.append(f"+{n_rep} request{'s' if n_rep > 1 else ''} answered")
        for c in conf:
            pair = set(c["aircraft"])
            became = any(lt >= t - 1 and lt <= t + c["in_s"] + 120 and pair == la for lt, la in losses)
            if pair & acted and not became:
                r += 3
                why.append(f"+3 {'×'.join(c['aircraft'])} solved")
            elif not pair & acted and became and c["in_s"] <= 300:
                r -= 5
                why.append(f"-5 {'×'.join(c['aircraft'])} left alone, lost")
        for lt, la in losses:
            if t < lt <= t + LOOK_S and (la & acted):
                r -= 8
                why.append(f"-8 loss {'×'.join(sorted(la))} at T+{int(lt // 60):02d}:{int(lt % 60):02d}")
        gone = {x["callsign"] for x in shown.get("to_decontrol") or []}
        n_dec = sum(1 for x in s.get("results") or [] if x.get("ok") and x["action"].get("type") == "decontrol"
                    and str(x["action"].get("callsign", "")).upper() in gone)
        if n_dec:
            r += n_dec
            why.append(f"+{n_dec} transferred")
        stale_out = [x for x in shown.get("to_decontrol") or [] if (x.get("since_s") or 0) > 120 and x["callsign"] not in acted]
        if stale_out:
            r -= len(stale_out)
            why.append(f"-{len(stale_out)} not transferred (left over 2 min ago)")
        td = shown.get("todo") or {}
        res_cs = set(td.get("resume") or [])
        resumed = {str(x["action"].get("callsign", "")).upper() for x in s.get("results") or [] if x.get("ok")
                   and (x["action"].get("type") == "resume" or x["action"].get("direct"))}
        if res_cs & resumed:
            r += len(res_cs & resumed)
            why.append(f"+{len(res_cs & resumed)} resumed when clear")
        if res_cs - acted:
            r -= len(res_cs - acted)
            why.append(f"-{len(res_cs - acted)} left on a heading though clear to resume")
        waiting = [x for x in shown.get("calling") or [] if x["callsign"] not in acted]
        if waiting:
            r -= len(waiting)
            why.append(f"-{len(waiting)} first call{'s' if len(waiting) > 1 else ''} not answered")
        answered = {str(x["action"].get("request_id")) for x in s.get("results") or [] if x["action"].get("type") == "reply"}
        stale = [q for q in shown.get("requests") or [] if q.get("age_s", 0) > 120 and q["id"] not in answered]
        if stale:
            r -= 2 * len(stale)
            why.append(f"-{2 * len(stale)} requests left waiting")
        n_early = sum(1 for x in s.get("results") or [] if x.get("early"))
        if n_early:
            r -= n_early
            why.append(f"-{n_early} arrival descended early (before its top of descent, not asked)")
        n_unk = sum(1 for x in s.get("results") or [] if x.get("unknown_fields"))
        if n_unk:
            r -= n_unk
            why.append(f"-{n_unk} unknown field(s) used")
        if s.get("corrections"):
            why.append(f"needed {s['corrections']} correction(s)")
        nref = sum(1 for x in s.get("results") or [] if not x.get("ok"))
        if nref:
            r -= nref
            why.append(f"-{nref} refused/invalid")
        if s.get("parse_error") or s.get("unreadable_first"):
            r -= 2
            why.append("-2 unreadable answer")
        s["reward"] = round(r, 1)
        s["label"] = "good" if r > 0 else "bad" if r < 0 else "neutral"
        s["label_why"] = why


JUDGE_PROMPT = """You are a senior air traffic control instructor assessing a trainee's decisions (here an AI controller).
For the decision below you get the situation it saw, the instructions it gave, what the pilots answered, and what happened in the following minutes.
Grade the decision from 1 (dangerous or wrong) to 5 (what an expert would do), and say in one or two sentences why and what would have been better.
Answer with JSON only: {"grade": <1-5>, "critique": "<text>"}"""


def judge_messages(step: dict, after: str) -> list:
    acts = "\n".join(f"- {r['action']} -> {'OK' if r.get('ok') else 'REFUSED'}: {r.get('text', '')}" for r in step.get("results") or []) or "- none"
    return [{"role": "system", "content": JUDGE_PROMPT},
            {"role": "user", "content": f"SITUATION (T+{int(step['t'] // 60):02d}:{int(step['t'] % 60):02d}):\n{step['observation']}\n\n"
                                         f"REASONING GIVEN: {step.get('reasoning') or '-'}\nINSTRUCTIONS:\n{acts}\n\nAFTERWARDS: {after}"}]
