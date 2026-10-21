"""A stand-in for a model server (OpenAI-compatible chat completions), for the AI lab tests: it reads the situation
it is sent and answers like a lazy controller - assume who calls, transfer what has left, approve the requests - with, every fifth call,
an answer that cannot be read, to exercise the error paths.   python -m tests.mock_llm <port>"""
import asyncio
import json
import os
import re
import sys

from fastapi import FastAPI, Request

app = FastAPI()
calls = {"n": 0, "last": None}


@app.get("/v1/models")
def models():
    return {"data": [{"id": "mock-controller"}]}


@app.get("/last")
def last():
    return calls["last"] or {}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    if os.environ.get("MOCK_STRICT") and ("chat_template_kwargs" in body or "response_format" in body):
        from fastapi.responses import HTMLResponse          # like a llama.cpp worker behind RunPod's gateway
        return HTMLResponse("<!DOCTYPE html><html><body>502 Bad gateway</body></html>", status_code=502)
    calls["n"] += 1
    calls["last"] = body
    user = body["messages"][-1]["content"]
    if "Reply with" in user:
        text = '{"ok": true}'
    elif calls["n"] % 3 == 0 and "ANSWERS TO YOUR QUERIES" not in user and "QUERIES (optional)" in body["messages"][0]["content"] \
            and len(re.findall(r"^- ([A-Z0-9]+) ·", user.split("YOUR AIRCRAFT")[1].split("\n\n")[0], re.M)) >= 2:
        mine = re.findall(r"^- ([A-Z0-9]+) ·", user.split("YOUR AIRCRAFT")[1].split("\n\n")[0], re.M)
        text = json.dumps({"reasoning": "checking", "queries": [{"q": "geometry", "a": mine[0], "b": mine[1]},
                                                                  {"q": "levels", "callsign": mine[0]},
                                                                  {"q": "probe", "callsign": mine[0], "cfl": 370},
                                                                  {"q": "route", "callsign": mine[1]}, {"q": "nonsense"}]})
    elif calls["n"] % 5 == 0:
        text = "I think we should climb someone."             # not JSON: an unreadable answer
    else:
        calling = re.findall(r"^- ([A-Z0-9]+) ·", user.split("CALLING YOU")[1].split("\n\n")[0], re.M) if "CALLING YOU" in user else []
        reqs = re.findall(r"^- (R\d+):", user.split("PILOT REQUESTS")[1].split("\n\n")[0], re.M) if "PILOT REQUESTS" in user else []
        gone = re.findall(r"^- ([A-Z0-9]+) FL", user.split("LEFT YOUR SECTOR")[1].split("\n\n")[0], re.M) if "LEFT YOUR SECTOR" in user else []
        acts = [{"type": "assume", "callsign": c} for c in calling] + [{"type": "decontrol", "callsign": c} for c in gone] + [{"type": "reply", "request_id": r, "decision": "approve"} for r in reqs]
        acts.append({"type": "clear", "callsign": "NOSUCH1", "cfl": 300}) if calls["n"] % 7 == 0 else None
        text = "```json\n" + json.dumps({"reasoning": f"{len(calling)} calling, {len(reqs)} requests", "actions": acts}) + "\n```"
    if os.environ.get("MOCK_DELAY") and "Reply with" not in user:
        await asyncio.sleep(float(os.environ["MOCK_DELAY"]))
    return {"id": "x", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(text) // 4}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
