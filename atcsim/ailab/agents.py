"""The controllers of a headless run: a language model behind an OpenAI-compatible chat API (a RunPod serverless vLLM
endpoint, a RunPod pod running vLLM / TGI / Ollama, or any compatible server), or the built-in baseline.

RunPod serverless vLLM: https://api.runpod.ai/v2/<endpoint id>/openai/v1, the RunPod API key as bearer token, the
model = the Hugging Face model deployed (or OPENAI_SERVED_MODEL_NAME_OVERRIDE).
"""
from __future__ import annotations

import json
import math
import time
from typing import List, Optional, Tuple

from .actions import SCHEMA

DEFAULT_SYSTEM_PROMPT = """You are an en-route radar air traffic controller. You control the aircraft you have assumed in your sector, alone.
Every few moments you get the situation (it starts with TO DO NOW, and with what you left undone last time), and you answer with the instructions to give now. Every situation is complete and current: work from it, not from your earlier answers. Instructions stay in force: an aircraft keeps its cleared level, heading or speed until you change it.

At every decision, in this order:
1. ASSUME every aircraft listed under ASSUME / CALLING YOU, all of them, now (one assume action each). Only assumed aircraft accept clearances; an assume and clearances for the same aircraft may go in one answer, assume first. INBOUND aircraft are not on your frequency: never instruct them.
2. TRANSFER (decontrol) every aircraft listed under TRANSFER / LEFT YOUR SECTOR.
3. ANSWER every pilot request (reply approve or unable; the probe result is given: approve when it says no conflict). A request in conflict is not refused while a speed or a vector makes it separated: give it with that clearance (the SOLVER line on the request says how), and refuse only when nothing does. A request you refused is not forgotten: when the reason is gone (listed under ACCOMMODATE), give it (clear the level or the direct) without waiting for the pilot to ask again.
4. SOLVE every predicted conflict between your aircraft as soon as it is listed (under 10 minutes): act at the first decision you see it, not when the aircraft are close. A conflict listed again "still unsolved" means your last decision did not solve it.
   - PRIORITY: move only ONE aircraft, the one without priority. An aircraft maintaining its level has priority over one climbing or descending; between two level aircraft, the one that entered your sector FIRST keeps its level. Do not move an aircraft that did nothing wrong. The conflict line says which one to move.
   - ORDER OF SOLUTIONS, always: 1) SPEED (Mach "M74" or IAS "280", for traffic in trail, merging or converging: slow the one behind or speed up the one ahead), 2) HEADING (a turn of 10-45 degrees, or a delaying vector), 3) LEVEL, last; a level may be given with a rate ("rate": "1500-") to climb or descend two aircraft together. The conflict line lists the conflict-free options in this order: take the first that works.
   - LIMITS: every heading and every speed is given with its limit, as the regulations require: a heading for minutes or miles ("limit": "4MIN", "then": the point to proceed to at the limit), a speed for minutes, miles or until a point past the traffic ("limit": "8MIN" or "IBALU"). At the limit the pilot goes back to its route or its normal speed by itself: choose the limit so that this happens once the traffic is passed, and do not cancel a speed or end a vector early.
   - POSITIVE SEPARATION: give a clearance, or approve a request, only when separation is assured for the whole manoeuvre (the probe or the solver says so); never one that only might work.
   - LEVELS: never give an aircraft a level it did not ask for without asking first: {"type": "ask", "callsign": "X", "cfl": 370}; the pilot answers at the next decision (affirm, or negative with a reason), and only after "affirm" do you clear it. Exceptions: the level it requested, its flight-plan cruise level, arrivals descending, and a conflict under 2 minutes or a loss of separation (then give the level at once). A level given without asking is refused.
5. RESUME an aircraft listed under RESUME (on a heading without a limit, now clear of conflict). The ones under WATCH are not clear yet: look at them again at every decision.
6. SEQUENCE the arrivals (SEQUENCE in TO DO): each crosses its fix cleared to the agreed level (it may still be descending) and the agreed distance behind the one ahead (measured when it crosses). Make the spacing first (speed; else headings with their limit then direct the fix), then descend each one, the leader first, with a descent speed: {"type": "clear", "callsign": "X", "cfl": 250, "speed": "M78/K280", "limit": "TERTO"}; one behind that stays high needs a lower speed (never below 250 kt). Traffic level in the way is not moved: time the descent, give a rate, or stop above it.
7. Then efficiency, only when everything above is done: directs to a point further on the route when safe; give a pilot the level it asks for when the probe says it is free.
The exit level (XFL, "exit FL350 to WS") is the level the aircraft leaves your sector at, coordinated with the next sector; it is its current level unless you change it. Change it (clear xfl) only together with a level change that it will keep until the exit, and only to a level of its direction of flight.
SEMICIRCULAR RULE, always: a cruising level or an exit level must be of the aircraft's direction (southbound odd: 290, 310, 330, 350, 370, 390, 410; northbound even: 280, 300, 320, 340, 360, 380, 400); only arrivals descending to their destination are exempt. A clearance to a wrong level is refused.
When an answer of yours is refused (ERRORS), read why and correct it: assume before clearing, use only the fields of the instruction set, levels of the right direction.
Never let two aircraft be closer than the separation minimum horizontally AND vertically. Never instruct an aircraft that is not assumed. Use only the callsigns and points of the situation and the briefing.
Give every needed instruction in one answer (several actions in the list); an empty list only when TO DO NOW is empty and nothing needs solving.

""" + SCHEMA


# earlier default prompts (sha256 of the stripped text): a profile still using one gets the current default
LEGACY_DEFAULT_PROMPTS = {"c711665f058340f1f688c1741ea3daa219678dbd431de0099d94650d1159df8b",
                          "99659c9a465b96fb771c3c6764a008bb45801a5a2f0681349bebd9d027956d5e",
                          "53a5624a4c0f4b4979ef146b41b629fc4f701e1837aba0fa648eab3a04738d93",
                          "0ea454d7ef427cd8ef3152721b4249f7be603187c615bc6dbd353f3d8c97eb0b",
                          "8e537bb41ac6b8514fab28e5397556850845c71db08acdd8aa724a2ed110af61",
                          "0792535b15f97a9805bdd21dea9f589a6bd1606a13ce9781995f22d8cca22496",
                          "cdbe56a568dd65d5f51c80486149ea576d29b2a62cb9e5ec4f552571f7146dd4",
                          "9950dd11812cc5ef998b7940e97d603867928b95fab03bf8dc13175d307f04df",
                          "b4875a13f01be8c1a78ffc4f4b94a2fba95fa20d3e8aa3a04fb7b92c5fae6534",
                          "a34586a01e13850d72651aea46ea0874a1e60bb13160dab8aca08f738cb1c54c",
                          "29440cc7e0259f984332c4bd848bf7cf26352fa0b45dbe94d2d1190070bc56bf"}


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
        self.context = int(prm.get("context_tokens") or 0)
        self.timeout = float(prm.get("timeout_s", 180))
        self.retries = int(prm.get("retries", 3))
        self.json_mode = bool(prm.get("json_mode", False))
        # reasoning models (Qwen3, DeepSeek-R1...) think before answering: thousands of tokens, minutes per decision
        self.thinking = str(prm.get("thinking", "off")).lower()

    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def chat(self, messages: List[dict]) -> Tuple[str, dict, float]:
        import httpx
        mt = self.max_tokens
        if self.context:                     # prompt + answer must fit the endpoint's context: cap the answer length
            est_prompt = int(sum(len(m.get("content") or "") for m in messages) / 2.5) + 300
            mt = max(256, min(mt, self.context - est_prompt))
        body = {"model": self.model, "messages": messages, "temperature": self.temperature, "max_tokens": mt}
        plain = getattr(self, "_plain", False)                 # this endpoint refused the optional fields before
        if self.json_mode and not plain:
            body["response_format"] = {"type": "json_object"}
        if self.thinking == "off":
            if not plain:
                # Qwen3 / Qwen3.x templates: enable_thinking; Qwen3.8 also reasoning_effort (unknown keys are ignored)
                body["chat_template_kwargs"] = {"enable_thinking": False, "reasoning_effort": "none"}
            if "qwen3" in self.model.lower() and messages and "/no_think" not in messages[0]["content"]:
                body["messages"] = [dict(messages[0], content=messages[0]["content"] + "\n/no_think")] + list(messages[1:])
        extras = [k for k in ("response_format", "chat_template_kwargs") if k in body]
        last = None
        first = not getattr(self, "_answered", False)
        # the first call may wake a serverless endpoint (cold start: minutes): it waits longer
        tmo = max(self.timeout, float(getattr(self, "first_timeout", 600))) if first else self.timeout
        for k in range(self.retries + 1):
            t0 = time.time()
            try:
                r = httpx.post(self.url + "/chat/completions", headers=self.headers(), json=body,
                               timeout=httpx.Timeout(tmo, connect=30.0))
                if r.status_code >= 400 and extras and r.status_code != 429 and time.time() - t0 < 60:
                    # refused at once: maybe an optional field this server does not take (llama.cpp workers and
                    # response_format / chat_template_kwargs): once more without them, and so from now on
                    self._plain = True
                    for x in extras:
                        body.pop(x, None)
                    extras = []
                    r = httpx.post(self.url + "/chat/completions", headers=self.headers(), json=body,
                                   timeout=httpx.Timeout(tmo, connect=30.0))
                if r.status_code >= 400:
                    txt = r.text.strip()
                    if txt.startswith("<"):
                        txt = ("an HTML error page from the endpoint's gateway (the worker failed or rejected the request:"
                               " see the endpoint's logs)")
                    if r.status_code >= 500 or r.status_code == 429:
                        raise RuntimeError(f"HTTP {r.status_code}: {txt[:300]}")
                    raise ValueError(f"HTTP {r.status_code}: {txt[:300]}")      # not retried: configuration
                d = r.json()
                msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
                text = msg.get("content") or ""
                usage = dict(d.get("usage") or {})
                tm = d.get("timings") or {}                          # llama.cpp: prompt and generation speeds
                for k_ in ("prompt_per_second", "predicted_per_second", "prompt_n", "cache_n"):
                    if isinstance(tm.get(k_), (int, float)):
                        usage[k_] = round(tm[k_], 1)
                rc = msg.get("reasoning_content") or msg.get("reasoning") or ""
                if rc:
                    usage["reasoning_chars"] = len(rc)
                if "<think>" in text:
                    usage["reasoning_chars"] = usage.get("reasoning_chars", 0) + len(text.split("</think>")[0])
                self._answered = True
                return text, usage, time.time() - t0
            except ValueError:
                raise
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
                # the request reached the server, which may still be working on it (a RunPod job keeps running):
                # sending it again would only queue another job and bill both. Give up on this decision.
                raise RuntimeError(f"no answer within {tmo:.0f} s (not re-sent: the endpoint may still be processing it)") from e
            except Exception as e:  # noqa: BLE001 - timeouts, cold starts, 5xx: retry with a pause
                last = e
                time.sleep(min(30, 3 * (k + 1)))
        if first and str(last).startswith(("HTTP 5", "HTTP 429")):
            # the first call: a serverless worker may still be starting (minutes: loading, compiling) and its
            # gateway answer 502/503 at once meanwhile. Keep asking, every 15 s, up to the first-call limit.
            t_start, n = time.time(), 0
            while time.time() - t_start < tmo:
                n += 1
                cb = getattr(self, "on_wait", None)
                if cb:
                    cb(f"the endpoint is not ready yet ({str(last)[:120]}): asking again every 15 s, "
                       f"{int(time.time() - t_start)} s so far")
                time.sleep(15)
                try:
                    r = httpx.post(self.url + "/chat/completions", headers=self.headers(), json=body,
                                   timeout=httpx.Timeout(tmo, connect=30.0))
                    if r.status_code < 400:
                        d = r.json()
                        msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
                        self._answered = True
                        return msg.get("content") or "", dict(d.get("usage") or {}), time.time() - t_start
                    if r.status_code < 500 and r.status_code != 429:
                        raise ValueError(f"HTTP {r.status_code}: {r.text.strip()[:300]}")
                    last = RuntimeError(f"HTTP {r.status_code}")
                except ValueError:
                    raise
                except (httpx.ReadTimeout, httpx.WriteTimeout) as e:
                    raise RuntimeError(f"no answer within {tmo:.0f} s") from e
                except Exception as e:  # noqa: BLE001
                    last = e
        raise RuntimeError(f"no answer after {self.retries + 1} tries: {last}")

    def models(self) -> list:
        import httpx
        r = httpx.get(self.url + "/models", headers=self.headers(), timeout=min(self.timeout, 60))
        r.raise_for_status()
        return [m.get("id") for m in r.json().get("data", [])]


def test(p: dict) -> dict:
    """Check a model configuration: its model list (when served) and one short answer."""
    if p.get("kind") in ("baseline", "solver"):
        return {"ok": True, "note": f"the built-in {p.get('kind')} needs no connection"}
    m = ChatModel(dict(p, params=dict(p.get("params") or {}, retries=0)))
    m._answered = True                      # a quick check: no waiting for a cold worker here
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
    resolves predicted conflicts under 8 minutes moving the aircraft without priority, by speed, else a heading (then
    resume), else a level (asked to the pilot first unless urgent), each checked by the probe; speeds back to normal
    once no longer needed.
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
            a_, w_ = self._answer_request(w, r)
            acts += a_
            why += w_
        # requests refused earlier: given as soon as nothing stands against them any more (probe, positive separation,
        # direction of flight, the pilot's own check) - the pilot is not asked to call again
        from . import actions as act_mod
        pending_cs = {r["callsign"] for r in st.get("pending_requests") or []}
        for q in act_mod.refused_requests(w):
            if q.callsign in mine and q.callsign not in pending_cs:
                try:
                    a_, w_ = self._accommodate_later(w, q)
                except Exception:  # noqa: BLE001
                    continue
                acts += a_
                why += w_
        touched = self._resolve(w, st, mine, acts, why)
        for cs, a in mine.items():               # speeds back to normal once no longer needed
            if (a.get("speed_assigned_by_atc") and cs not in touched and "speed" not in (a.get("clearance_limits") or {})
                    and cs in getattr(self, "_spd_given", set())                 # only a speed this controller gave
                    and not any(cs in c["aircraft"] for c in atctools.mtcd(w))):
                nm = (a.get("speed_pair") or {}).get("mach")
                ok_ = True
                if nm:
                    try:
                        ok_ = self._free(w, cs, speed=f"M{int(round(nm * 100))}", within_s=600)
                    except Exception:  # noqa: BLE001
                        ok_ = False
                if ok_:
                    acts.append({"type": "clear", "callsign": cs, "speed": "SN"})
                    touched.add(cs)
        for cs, a in mine.items():               # back on route once resuming is conflict-free (the probe)
            if (a.get("nav_mode") == "heading" and cs not in touched and w.t - getattr(self, "_acted", {}).get(cs, -1e9) >= 60
                    and "heading" not in (a.get("clearance_limits") or {})):      # with a limit: the pilot resumes at it
                acx = w.aircraft[cs]
                ahead = [p for p in acx.route[acx.next_index:] if not str(p).startswith("@") and p in w.waypoints]
                if ahead:
                    try:
                        free = self._free(w, cs, direct=ahead[0], within_s=600)
                    except Exception:  # noqa: BLE001
                        free = False
                    if free:
                        acts.append({"type": "resume", "callsign": cs})
        given = getattr(self, "_spd_given", set())
        for x in acts:
            if x.get("type") == "clear" and x.get("speed") and "/" not in str(x["speed"]):   # not a descent speed
                (given.discard if str(x["speed"]).upper() == "SN" else given.add)(x["callsign"])
        self._spd_given = given
        return json.dumps({"reasoning": "; ".join(why) or "routine", "actions": acts})


    def _answer_request(self, w, r: dict):
        """(actions, notes) answering a pending request: approved when the probe finds it clear, else unable."""
        ok = True
        try:
            if r["type"] == "altitude" and r.get("value"):
                from ..aircraft import level_parity_ok
                acx, fl = w.aircraft[r["callsign"]], int(r["value"])
                ok = self._free(w, r["callsign"], cfl=fl) and (
                    level_parity_ok(fl, acx.southbound) or (fl * 100 < acx.alt_ft and w.descending_to_destination(acx, fl)))
            elif r["type"] == "direct" and r.get("value"):
                ok = self._free(w, r["callsign"], direct=str(r["value"]))
            elif r["type"] == "speed" and r.get("value"):
                v = str(r["value"]).upper()
                ok = self._free(w, r["callsign"], speed=v if v.startswith("M") or not v.replace(".", "").isdigit() or float(v) > 2
                                else f"M{int(round(float(v) * 100))}")
        except Exception:  # noqa: BLE001
            ok = False
        return ([{"type": "reply", "request_id": r["id"], "decision": "approve" if ok else "unable"}],
                [f"{r['id']} {'approved' if ok else 'unable'}"])

    def _accommodate_later(self, w, q):
        """(actions, notes) for a request refused earlier: given when nothing stands against it any more."""
        from . import actions as act_mod
        from ..aircraft import level_parity_ok
        cs, acx = q.callsign, w.aircraft[q.callsign]
        t_ = int(q.resolved_time or q.time)
        if q.type == "altitude":
            fl = act_mod._fl(q.value)
            if ((level_parity_ok(fl, acx.southbound) or (fl * 100 < acx.alt_ft and w.descending_to_destination(acx, fl)))
                    and act_mod._flyable_now(w, acx, {"type": "altitude", "fl": fl}) and self._free(w, cs, cfl=fl)):
                return ([{"type": "clear", "callsign": cs, "cfl": fl}],
                        [f"{cs} FL{fl}: its request {q.id}, refused at T+{t_ // 60:02d}:{t_ % 60:02d}, now possible"])
        else:
            wp = str(q.value).upper()
            if act_mod._flyable_now(w, acx, {"type": "direct", "waypoint": wp}) and self._free(w, cs, direct=wp):
                return ([{"type": "clear", "callsign": cs, "direct": wp}], [f"{cs} direct {wp}: its request {q.id}, refused earlier, now possible"])
        return [], []

    def _free(self, w, cs: str, cfl=None, direct=None, speed=None, within_s: Optional[float] = None) -> bool:
        """No predicted conflict for cs with this clearance (the radar's probe, over its look-ahead or within_s)."""
        from .. import atctools
        cl = atctools.probe(w, cs, cfl=cfl, direct=direct, speed=speed).get("conflicts") or []
        return not [x for x in cl if within_s is None or x["time_to_conflict_s"] <= within_s]

    def _resolve(self, w, st, mine, acts, why) -> set:
        """Predicted conflicts: the aircraft without priority, by speed, else a heading, else a level (asked first)."""
        from .. import atctools
        touched = set()
        from .observe import priority, solutions
        from . import actions as act_mod
        asked = getattr(self, "asked", None) or {}
        st_by = {a["callsign"]: a for a in st["aircraft"]}
        for c in atctools.mtcd(w):
            if c["time_to_conflict_s"] > 480:
                continue
            pair = [x for x in c["aircraft"] if x in mine and x not in touched]
            if not pair or any(x in touched for x in c["aircraft"]):
                continue
            if len(pair) == 2:                      # priority keeps its level: move the other first
                mv, _ = priority(st_by[pair[0]], st_by[pair[1]], w)
                if mv == pair[1]:
                    pair = [pair[1], pair[0]]
            done = False
            urgent = c.get("current") or c["time_to_conflict_s"] <= act_mod.URGENT_S
            last = getattr(self, "_acted", {})
            if not urgent and any(w.t - last.get(x, -1e9) < 120 for x in pair):
                continue                            # acted on it less than 2 min ago: let the clearance work
            for cs in pair:                         # 1) speed, 2) heading (then resume), 3) level (asked first)
                so = solutions(w, st_by[cs])
                tag = f"{c['aircraft'][0]}×{c['aircraft'][1]}: {cs}"
                # every heading and speed with its limit: past the encounter (time to it, and four minutes more)
                past = f"{int(math.ceil((max(0, c['time_to_conflict_s']) + 240) / 60))}MIN"
                if so["speed"]:
                    acts.append({"type": "clear", "callsign": cs, "speed": so["speed"][0], "limit": past})
                    why.append(f"{tag} speed {so['speed'][0]} for {past}")
                elif so["heading"]:
                    acts.append({"type": "clear", "callsign": cs, "heading": int(so["heading"][0]), "limit": "4MIN"})
                    why.append(f"{tag} heading {so['heading'][0]} for 4 min")
                elif so["level"]:
                    fl = int(so["level"][0])
                    q = (asked.get(cs) or {}).get(fl)
                    if urgent or (q and q["ok"] and q["t"] < w.t):
                        acts.append({"type": "clear", "callsign": cs, "cfl": fl})
                        why.append(f"{tag} FL{fl}")
                    elif not q:
                        acts.append({"type": "ask", "callsign": cs, "cfl": fl})
                        why.append(f"{tag} asked FL{fl}")
                    else:
                        continue
                else:
                    continue
                touched.add(cs)
                self._acted = dict(getattr(self, "_acted", {}), **{cs: w.t})
                done = True
                break
            if not done and urgent:
                cs = pair[0]
                h = (int(mine[cs]["track_deg"]) + 30) % 360 or 360
                acts.append({"type": "clear", "callsign": cs, "heading": h, "limit": "3MIN"})
                why.append(f"{c['aircraft'][0]}×{c['aircraft'][1]}: {cs} heading {h:03d} (nothing better)")
                touched.add(cs)
        return touched


class SolverAgent(Baseline):
    """The solver as the controller (atcsim/solve): each predicted conflict resolved by the cheapest clearance that
    leaves no conflict with anyone, in the controller's order (speed < direct / heading < level, the aircraft without
    priority), checked leg by leg and confirmed by running the simulation ahead; levels asked to the pilot first unless
    urgent. The routine work (assume, transfer, requests, resume, speeds back to normal) as the baseline."""

    def _request_solution(self, w, cs: str, req: dict):
        """The request made possible (resolve.accommodate): as asked, or with a speed / vector of the requester or of
        the aircraft in the way; None when nothing makes it separated. The direction of flight and the pilot's own
        check come first."""
        from ..solve import resolve
        from . import actions as act_mod
        from ..aircraft import level_parity_ok
        acx = w.aircraft[cs]
        if req.get("cfl") is not None:
            fl = int(req["cfl"])
            if not (level_parity_ok(fl, acx.southbound) or (fl * 100 < acx.alt_ft and w.descending_to_destination(acx, fl))):
                return None
            if not act_mod._flyable_now(w, acx, {"type": "altitude", "fl": fl}):
                return None
        elif not act_mod._flyable_now(w, acx, {"type": "direct", "waypoint": req["direct"]}):
            return None
        return resolve.accommodate(w, cs, req, getattr(self, "asked", None) or {})

    def _answer_request(self, w, r: dict):
        if r["type"] not in ("altitude", "direct") or not r.get("value"):
            return super()._answer_request(w, r)
        cs = r["callsign"]
        if r.get("kind") == "descent" or r["type"] == "altitude":
            # an arrival in a sequence plan: its descent is the plan's, given when its separation is certain
            try:
                self._arrival_plans(w, {c for c, a in w.aircraft.items() if a.assumed and a.active})
            except Exception:  # noqa: BLE001
                pass
            for key, pl in (self.__dict__.get("_seq_plans") or {}).items():
                if key.startswith("ARR:") and any(st["callsign"] == cs and "cfl" in st["action"] and i not in pl["issued"]
                                                  for i, st in enumerate(pl["plan"]["steps"])):
                    return [], []                 # the request stays open: answered by the plan's descent
        req = {"cfl": int(r["value"])} if r["type"] == "altitude" else {"direct": str(r["value"]).upper()}
        try:
            sol = self._request_solution(w, cs, req)
        except Exception:  # noqa: BLE001
            sol = None
        if sol is None:
            return [{"type": "reply", "request_id": r["id"], "decision": "unable"}], [f"{r['id']} unable (nothing makes it separated)"]
        if len(sol["actions"]) == 1 and set(sol["actions"][0]) <= {"type", "callsign", "cfl", "direct"} and not sol.get("intermediate"):
            return [{"type": "reply", "request_id": r["id"], "decision": "approve"}], [f"{r['id']} approved"]
        self._acted = dict(getattr(self, "_acted", {}), **{a_["callsign"]: w.t for a_ in sol["actions"]})
        return list(sol["actions"]), [f"{r['id']} approved: {sol['text']}"]

    def _accommodate_later(self, w, q):
        req = {"cfl": int(str(q.value).upper().lstrip("FL").lstrip("F"))} if q.type == "altitude" else {"direct": str(q.value).upper()}
        sol = self._request_solution(w, q.callsign, req)
        if sol is None:
            return [], []
        if sol.get("intermediate") is not None:          # only a step closer to the level asked for, never a step back
            cur = w.aircraft[q.callsign].cleared_alt_ft / 100.0
            if abs(sol["intermediate"] - req["cfl"]) >= abs(cur - req["cfl"]) - 5:
                return [], []
        t_ = int(q.resolved_time or q.time)
        self._acted = dict(getattr(self, "_acted", {}), **{a_["callsign"]: w.t for a_ in sol["actions"]})
        return list(sol["actions"]), [f"its request {q.id} (refused at T+{t_ // 60:02d}:{t_ % 60:02d}) now given: {sol['text']}"]

    def _free(self, w, cs: str, cfl=None, direct=None, speed=None, within_s: Optional[float] = None) -> bool:
        """The probe, and the solver's own check: the exact closest approach of the trajectory with this clearance to
        every other one over 10 minutes, with the solver's margin (a sampled probe can miss a climb or descent through
        another aircraft's level)."""
        from ..solve import resolve, traj
        if not super()._free(w, cs, cfl=cfl, direct=direct, speed=speed, within_s=within_s):
            return False
        key = round(w.t, 1)
        if getattr(self, "_trajs_t", None) != key:
            self._trajs, self._trajs_t = traj.build_all(w, resolve.H_DEFAULT), key
        prm = {k: v for k, v in (("speed", speed), ("direct", direct), ("cfl", cfl)) if v is not None}
        return resolve.check_clearance(w, w.aircraft[cs], resolve.H_DEFAULT, prm, self._trajs, float(w.sep_h))[0]

    def _sequences(self, w, mine, acts, why) -> set:
        """In-trail sequences (solve/sequence.py) for aircraft that would leave by the same exit too close: planned once
        for the whole group, each clearance issued when it is due (a speed at the latest moment that still works, the
        vectors from the back of the sequence); the plan's aircraft are left to it while it holds."""
        from ..solve import exits, sequence
        plans = self.__dict__.setdefault("_seq_plans", {})
        touched = set()
        for x in list(plans):                             # a plan holds 30 minutes, or until it is done
            if (not x.startswith("ARR:") and w.t - plans[x]["t"] > 1800) or not any(w.aircraft[c].active for c in plans[x]["plan"]["sequence"] if c in w.aircraft):
                plans.pop(x)
        try:
            viol = exits.conflicts(w)
        except Exception:  # noqa: BLE001
            viol = []
        targets = {v["exit"] for v in viol}
        # a conflict between two aircraft whose routes join ahead at levels not separated: a merge to sequence as a
        # group (to the last point they share in the sector), rather than pair by pair
        try:
            from ..solve import cpa as cpa_mod, traj as traj_mod
            T_ = traj_mod.build_all(w, 600.0)
            for pc in cpa_mod.matrix(T_, float(w.sep_h), only=sorted(mine))["conflicts"]:
                ra = traj_mod.route_ahead(w, w.aircraft[pc.a])
                rb = traj_mod.route_ahead(w, w.aircraft[pc.b])
                common = [x_ for x_ in ra if x_ in rb]
                if common and pc.a in mine and pc.b in mine:
                    last = common[0]
                    for x_ in common[1:]:
                        if ra.index(x_) == ra.index(last) + 1 and rb.index(x_) == rb.index(last) + 1:
                            last = x_
                        else:
                            break
                    xa = exits.exit_of(w, w.aircraft[pc.a])
                    targets.add(xa if xa in common else last)     # to the exit when they leave together
        except Exception:  # noqa: BLE001
            pass
        self._arrival_plans(w, set(mine))
        for x in sorted(targets):
            mem = [c for c in sequence.members_of(w, x) if c in mine]
            if mem and all(w.aircraft[c].destination and (w.aircraft[c].arr or {}).get("fix") for c in mem):
                continue                                  # arrivals: their sequencing fix's plan
            if x in plans or any(x in pl["plan"].get("sequence_points", []) for pl in plans.values()):
                continue
            try:
                P = sequence.plan(w, x, members=[c for c in sequence.members_of(w, x) if c in mine])
            except Exception:  # noqa: BLE001
                continue
            if P.get("ok") and P.get("steps"):
                plans[x] = {"plan": P, "t": w.t, "issued": set()}
                why.append(f"sequence at {P['point']} for {x}: " + " → ".join(P["sequence"]))
        for x, pl in plans.items():
            for i, stp in enumerate(list(pl["plan"]["steps"])):
                if i in pl["issued"] or stp["at_s"] > w.t + 1:
                    continue
                if x.startswith("ARR:") and "cfl" in stp["action"] and not stp.get("rechecked"):
                    sq_ = pl["plan"]["sequence"]
                    fx = (getattr(w, "seq", None) or {}).get(pl["plan"]["fix"])
                    lo = int(fx.cross_at_fl or fx.cross_at_or_below_fl or fx.min_fl) * 100 + 50 if fx else 25050
                    if stp["callsign"] in sq_ and any(w.aircraft[c].active and w.aircraft[c].cleared_alt_ft > lo
                                                      for c in sq_[:sq_.index(stp["callsign"])] if c in w.aircraft):
                        stp["at_s"] = w.t + 20.0          # the ones ahead are given their descent (and speed) first
                        continue
                    # a descent: checked again against the traffic as it is now (some may not have been there)
                    sp = str(stp["action"].get("speed") or "")
                    mach = int(sp[1:3]) / 100.0 if sp.startswith("M") else None
                    try:
                        k_ = sq_.index(stp["callsign"]) if stp["callsign"] in sq_ else 0
                        d = sequence.descent_now(w, stp["callsign"], pl["plan"]["fix"], mach, sq_[k_ - 1] if k_ > 0 else None)
                    except Exception:  # noqa: BLE001
                        d = None
                    if d is None or not d["steps"]:
                        stp["at_s"] = w.t + 60.0          # nothing clear now: kept level, looked at again in a minute
                        continue
                    first = d["steps"][0]
                    if first["at_s"] > w.t + 1:
                        stp["at_s"] = first["at_s"]       # later than planned: looked at again when it is due
                        continue
                    stp.update(action=first["action"], why=first["why"], at_s=first["at_s"], rechecked=True)
                    for more in d["steps"][1:]:           # a speed for the one ahead, a restriction now, a second step
                        pl["plan"]["steps"].append({**more, "rechecked": True})
                acts.append(stp["action"])
                why.append(stp["why"])
                pl["issued"].add(i)
                self._acted = dict(getattr(self, "_acted", {}), **{stp["callsign"]: w.t})
            touched |= set(pl["plan"]["sequence"])
        return touched

    def _arrival_plans(self, w, mine: set) -> None:
        """Arrivals to a sequencing fix (TERTO for GCRR, MARJA and SALCA for GMMN): one plan per fix for the assumed
        arrivals still to be sequenced or cleared down to the LoA level - the in-trail sequence and each descent;
        planned again when an arrival joins (the clearances given stay in force)."""
        from ..solve import sequence
        if getattr(self, "_arr_t", None) == w.t:
            return
        self._arr_t = w.t
        plans = self.__dict__.setdefault("_seq_plans", {})
        for fix, sq in (getattr(w, "seq", None) or {}).items():
            fl = int(sq.cross_at_fl or sq.cross_at_or_below_fl or sq.min_fl)
            members = sorted(c for c in sequence.arrivals_to(w, fix) if c in mine)
            todo = [c for c in members if w.aircraft[c].cleared_alt_ft > fl * 100 + 50]
            key = "ARR:" + fix
            old = plans.get(key)
            if not members:
                plans.pop(key, None)
                continue
            if old is not None and (old["members"] == members or not todo):
                continue                                  # the plan holds until a new arrival joins
            if not todo:
                continue
            try:
                P = sequence.plan(w, fix, members=members)
            except Exception:  # noqa: BLE001
                continue
            if P.get("ok") and P.get("steps"):
                plans[key] = {"plan": P, "t": w.t, "issued": set(), "members": members}
            elif old is not None:
                old["t"] = w.t                            # the plan in force is kept (no better one now)

    def _resolve(self, w, st, mine, acts, why) -> set:
        from ..solve import resolve
        from . import actions as act_mod
        in_plan = self._sequences(w, mine, acts, why)
        touched = set(in_plan)
        asked = getattr(self, "asked", None) or {}
        last = getattr(self, "_acted", {})
        res = resolve.solve(w, asked=asked, rollout=1)
        for s_ in sorted(res["sets"], key=lambda x: x["t_first"] if x["t_first"] is not None else 1e9):
            urgent = (s_["t_first"] or 0) <= act_mod.URGENT_S
            if not urgent and set(s_["pair"]) <= in_plan:
                continue                                    # both in a sequence plan that keeps them separated
            if not urgent and any(w.t - last.get(x, -1e9) < 120 for x in s_["pair"]):
                continue                                    # a clearance given less than 2 min ago: let it work
            best = (s_["optimal"] or s_["solutions"] or [None])[0]
            if best is None or set(best.mover.split("+")) & touched:
                continue
            tag = f"{s_['pair'][0]}×{s_['pair'][1]}"
            if best.needs_ask and not urgent:
                for a_ in best.actions:
                    if a_.get("cfl") is not None:
                        q = (asked.get(a_["callsign"]) or {}).get(int(a_["cfl"]))
                        if q and q["ok"] and q["t"] < w.t:
                            acts.append(a_)
                            why.append(f"{tag}: {best.text}")
                        elif not q:
                            acts.append({"type": "ask", "callsign": a_["callsign"], "cfl": a_["cfl"]})
                            why.append(f"{tag}: asked {a_['callsign']} FL{a_['cfl']}")
                    else:
                        acts.append(a_)
            else:
                acts.extend(best.actions)
                why.append(f"{tag}: {best.text}")
            for m in best.mover.split("+"):
                touched.add(m)
                self._acted = dict(getattr(self, "_acted", {}), **{m: w.t})
        return touched

def make(p: dict):
    if p.get("kind") == "baseline":
        return Baseline()
    if p.get("kind") == "solver":
        return SolverAgent()
    return ChatModel(p)


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
