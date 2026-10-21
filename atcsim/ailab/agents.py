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

DEFAULT_SYSTEM_PROMPT = """You are an en-route air traffic controller (area control centre, radar). You control the aircraft you have assumed in your sector.
Your goals, in order: 1) keep every pair of aircraft separated (the minimum is given; never let two aircraft be closer horizontally AND vertically); 2) keep aircraft out of active restricted areas and out of weather they cannot fly through; 3) answer every first call at once (assume the aircraft listed as CALLING YOU, at the first decision you see them) and answer pilot requests promptly (approve when it is safe); transfer (decontrol) every aircraft listed as LEFT YOUR SECTOR; 4) let aircraft leave at their exit level, as efficiently as possible (few heading vectors, direct routings when safe).
Resolve predicted conflicts early, usually with a level change of one aircraft (1000 ft steps; respect RVSM), or a heading for a few minutes, or speed control on converging tracks. Do not give a level that creates a new conflict. Give few, clear instructions.
Only aircraft in YOUR AIRCRAFT accept clearances. An aircraft in CALLING YOU must be assumed first (the assume and its clearances may go in the same answer, assume first). INBOUND aircraft are not on your frequency yet: plan for them, do not instruct them.
Every few moments you receive the situation; answer with the instructions to give now.

""" + SCHEMA


# earlier default prompts (sha256 of the stripped text): a profile still using one gets the current default
LEGACY_DEFAULT_PROMPTS = {"0ea454d7ef427cd8ef3152721b4249f7be603187c615bc6dbd353f3d8c97eb0b"}


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
    for h in history:
        msgs.append({"role": "user", "content": h["observation"]})
        msgs.append({"role": "assistant", "content": h["response"]})
    user = observation
    if feedback:
        user = "RESULT OF YOUR LAST INSTRUCTIONS: " + feedback + "\n\n" + observation
    msgs.append({"role": "user", "content": user})
    return msgs
