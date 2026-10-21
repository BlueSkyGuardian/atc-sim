"""The controllers of a headless run: a language model behind an OpenAI-compatible chat API (a RunPod serverless vLLM
endpoint, a RunPod pod running vLLM / TGI / Ollama, or any compatible server), or the built-in baseline.

RunPod serverless vLLM: https://api.runpod.ai/v2/<endpoint id>/openai/v1, the RunPod API key as bearer token, the
model = the Hugging Face model deployed (or OPENAI_SERVED_MODEL_NAME_OVERRIDE).
"""
from __future__ import annotations

import json
import time
from typing import List, Optional, Tuple

from .actions import SCHEMA

DEFAULT_SYSTEM_PROMPT = """You are an en-route radar air traffic controller. You control the aircraft you have assumed in your sector, alone.
Every few moments you get the situation (it starts with TO DO NOW), and you answer with the instructions to give now. Instructions stay in force: an aircraft keeps its cleared level, heading or speed until you change it.

At every decision, in this order:
1. ASSUME every aircraft listed under ASSUME / CALLING YOU, all of them, now (one assume action each). Only assumed aircraft accept clearances; an assume and clearances for the same aircraft may go in one answer, assume first. INBOUND aircraft are not on your frequency: never instruct them.
2. TRANSFER (decontrol) every aircraft listed under TRANSFER / LEFT YOUR SECTOR.
3. ANSWER every pilot request (reply approve or unable; the probe result is given: approve when it says no conflict).
4. SOLVE every predicted conflict between your aircraft that is under 10 minutes away, before it happens:
   - prefer a level change for ONE of the two aircraft, to a level given as conflict-free for it, in its direction of flight;
   - aircraft routing via the same point (the conflict line says so, with both times): a level change, or a speed for one of them to put them in trail; headings only if no level works;
   - a heading turns an aircraft away from its route for good: give one only with a plan (about 30-60 degrees off its track, away from the other), and resume it as soon as it is listed under RESUME.
5. RESUME every aircraft listed under RESUME (on a heading and now clear of conflict). The ones under WATCH are not clear yet: look at them again at every decision.
6. SEQUENCE the arrivals (ARRIVAL SEQUENCES, and SEQUENCE in TO DO): each arrival must cross its fix at the agreed level (letter of agreement, e.g. FL250) and the agreed distance behind the one ahead. Descend them in time (before their latest descent point), keep the order of the sequence, and make the spacing with speed (a lower Mach or IAS for the one behind, or a higher one for the one ahead) or, if speed is not enough, a short heading and a resume. Aircraft on the same route to the same point (en-route traffic) are kept apart the same way, or at different levels.
7. Then efficiency, only when everything above is done: directs to a point further on the route when safe; give a pilot the level it asks for when the probe says it is free.
The exit level (XFL, "exit FL350 to WS") is the level the aircraft leaves your sector at, coordinated with the next sector; it is its current level unless you change it. Change it (clear xfl) only together with a level change that it will keep until the exit, and only to a level of its direction of flight.
Never let two aircraft be closer than the separation minimum horizontally AND vertically. Never instruct an aircraft that is not assumed. Use only the callsigns and points of the situation and the briefing.
Give every needed instruction in one answer (several actions in the list); an empty list only when TO DO NOW is empty and nothing needs solving.

""" + SCHEMA


# earlier default prompts (sha256 of the stripped text): a profile still using one gets the current default
LEGACY_DEFAULT_PROMPTS = {"0ea454d7ef427cd8ef3152721b4249f7be603187c615bc6dbd353f3d8c97eb0b",
                          "cdbe56a568dd65d5f51c80486149ea576d29b2a62cb9e5ec4f552571f7146dd4",
                          "b4875a13f01be8c1a78ffc4f4b94a2fba95fa20d3e8aa3a04fb7b92c5fae6534"}


def system_prompt(p: str) -> str:
    import hashlib
    if not p or hashlib.sha256(p.strip().encode()).hexdigest() in LEGACY_DEFAULT_PROMPTS:
        return DEFAULT_SYSTEM_PROMPT
    return p


def runpod_base(endpoint_id: str) -> str:
    return f"https://api.runpod.ai/v2/{endpoint_id.strip()}/openai/v1"


def base_url(p: dict) -> str:
    if p.get("kind") == "runpod":
        return runpod_base(p.get("endpoint_id") or "")
    return (p.get("base_url") or "").rstrip("/")


class ChatModel:
    """An OpenAI-compatible chat completions endpoint (with retries: serverless endpoints have cold starts)."""

    def __init__(self, p: dict):
        self.p = p
        self.url = base_url(p)
        self.key = p.get("api_key") or ""
        self.model = p.get("model") or ""
        prm = p.get("params") or {}
        self.temperature = float(prm.get("temperature", 0.2))
        self.max_tokens = int(prm.get("max_tokens", 600))
        self.timeout = float(prm.get("timeout_s", 180))
        self.retries = int(prm.get("retries", 3))
        self.json_mode = bool(prm.get("json_mode", False))

    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def chat(self, messages: List[dict]) -> Tuple[str, dict, float]:
        import httpx
        body = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        last = None
        for k in range(self.retries + 1):
            t0 = time.time()
            try:
                r = httpx.post(self.url + "/chat/completions", headers=self.headers(), json=body, timeout=self.timeout)
                if r.status_code >= 500 or r.status_code == 429:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    raise ValueError(f"HTTP {r.status_code}: {r.text[:300]}")      # not retried: configuration
                d = r.json()
                text = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                return text, d.get("usage") or {}, time.time() - t0
            except ValueError:
                raise
            except Exception as e:  # noqa: BLE001 - timeouts, cold starts, 5xx: retry with a pause
                last = e
                time.sleep(min(30, 3 * (k + 1)))
        raise RuntimeError(f"no answer after {self.retries + 1} tries: {last}")

    def models(self) -> list:
        import httpx
        r = httpx.get(self.url + "/models", headers=self.headers(), timeout=min(self.timeout, 60))
        r.raise_for_status()
        return [m.get("id") for m in r.json().get("data", [])]


def test(p: dict) -> dict:
    """Check a model configuration: its model list (when served) and one short answer."""
    if p.get("kind") == "baseline":
        return {"ok": True, "note": "the built-in baseline needs no connection"}
    m = ChatModel(dict(p, params=dict(p.get("params") or {}, retries=0)))
    out = {"url": m.url}
    try:
        out["models"] = m.models()
    except Exception as e:  # noqa: BLE001
        out["models_error"] = str(e)[:300]
    try:
        text, usage, lat = m.chat([{"role": "system", "content": "Answer with a JSON object only."},
                                   {"role": "user", "content": 'Reply with {"ok": true}'}])
        out.update(ok=True, answer=text[:300], latency_s=round(lat, 2), usage=usage)
    except Exception as e:  # noqa: BLE001
        out.update(ok=False, error=str(e)[:400])
    return out


# ------------------------------------------------------------------ the built-in baseline
class Baseline:
    """A scripted controller: assumes what calls, decontrols what has left, answers requests with a conflict probe,
    resolves predicted conflicts under 8 minutes with a level change (or else a heading) checked by the probe.
    A reference point for the models, and a way to produce experience data without a model."""

    def decide(self, w) -> str:
        from .. import atctools
        acts, why = [], []
        st = w.state()
        for a in st["aircraft"]:
            if a.get("on_frequency") and not a.get("assumed") and a.get("status") == "active":
                acts.append({"type": "assume", "callsign": a["callsign"]})
        for a in st.get("transferred") or []:
            if a.get("assumed"):
                acts.append({"type": "decontrol", "callsign": a["callsign"]})
        mine = {a["callsign"]: a for a in st["aircraft"] if a.get("assumed")}
        for r in st.get("pending_requests") or []:
            if r.get("kind") == "checkin" or r["callsign"] not in mine:
                continue
            if r["type"] == "report":
                acts.append({"type": "reply", "request_id": r["id"], "decision": "approve"})
                continue
            ok = True
            try:
                if r["type"] == "altitude" and r.get("value"):
                    ok = not atctools.probe(w, r["callsign"], cfl=int(r["value"])).get("conflicts")
                elif r["type"] == "direct" and r.get("value"):
                    ok = not atctools.probe(w, r["callsign"], direct=str(r["value"])).get("conflicts")
            except Exception:  # noqa: BLE001
                ok = False
            acts.append({"type": "reply", "request_id": r["id"], "decision": "approve" if ok else "unable"})
            why.append(f"{r['id']} {'approved' if ok else 'unable'}")
        touched = set()
        for c in atctools.mtcd(w):
            if c["time_to_conflict_s"] > 480:
                continue
            pair = [x for x in c["aircraft"] if x in mine and x not in touched]
            if not pair or any(x in touched for x in c["aircraft"]):
                continue
            done = False
            for cs in pair:
                a = mine[cs]
                for d in (10, -10, 20, -20):
                    fl = int(a["cleared_fl"]) + d
                    if fl < 100 or fl > w.upper_fl:
                        continue
                    try:
                        if not atctools.probe(w, cs, cfl=fl).get("conflicts"):
                            acts.append({"type": "clear", "callsign": cs, "cfl": fl})
                            why.append(f"{c['aircraft'][0]}×{c['aircraft'][1]}: {cs} to FL{fl}")
                            touched.add(cs)
                            done = True
                            break
                    except Exception:  # noqa: BLE001
                        pass
                if done:
                    break
            if not done:
                cs = pair[0]
                h = (int(mine[cs]["track_deg"]) + 30) % 360 or 360
                acts.append({"type": "clear", "callsign": cs, "heading": h})
                why.append(f"{c['aircraft'][0]}×{c['aircraft'][1]}: {cs} heading {h:03d}")
                touched.add(cs)
        for cs, a in mine.items():               # back on route once clear of conflict
            if a.get("nav_mode") == "heading" and cs not in touched and not any(cs in c["aircraft"] for c in atctools.mtcd(w)):
                acts.append({"type": "resume", "callsign": cs})
        return json.dumps({"reasoning": "; ".join(why) or "routine", "actions": acts})


def make(p: dict):
    return Baseline() if p.get("kind") == "baseline" else ChatModel(p)


def messages(system: str, observation: str, history: List[dict], feedback: Optional[str]) -> List[dict]:
    """The prompt of one decision: the system prompt, the last decisions (short), the situation."""
    msgs = [{"role": "system", "content": system}]
    for h in history:                                   # earlier situations: their TO DO part only (the rest is stale)
        ob = h["observation"]
        cut = ob.find("\nYOUR AIRCRAFT")
        msgs.append({"role": "user", "content": (ob[:cut] + "\n(earlier situation, details omitted)") if cut > 0 else ob})
        msgs.append({"role": "assistant", "content": h["response"]})
    user = observation
    if feedback:
        user = "RESULT OF YOUR LAST INSTRUCTIONS: " + feedback + "\n\n" + observation
    msgs.append({"role": "user", "content": user})
    return msgs
