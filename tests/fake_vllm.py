"""A tiny OpenAI-compatible server that mimics vLLM's streaming chat API.

Used by the tests to exercise the real AMD code path (streaming, JSON schema, telemetry)
without a GPU. Run standalone for UI work:  uvicorn tests.fake_vllm:app --port 8001
"""
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
LAST_REQUEST: dict = {}

DIAG = {
    "defect_detected": True, "defect_type": "scratch", "location": "upper surface, left of pocket",
    "severity": "high", "confidence": 0.84,
    "summary": "Longitudinal scratch from worn guide-rail pad; drive-end bearing degrading in parallel.",
    "likely_root_cause": "Guide-rail pad worn past 1 mm plus bearing wear raising lateral load",
    "evidence": [{"source": "image", "text": "bright linear scratch across upper surface"},
                 {"source": "sensors", "text": "vibration in warning zone with rising trend"},
                 {"source": "manual", "text": "Conveyor_C2_Manual.pdf p.37 links pad wear to scratches"}],
    "recommended_actions": ["Replace both GRP-220 pads; torque screws to 8 Nm", "Re-grease drive-end bearing (6 g NLGI 2)"],
    "safety_notes": ["LOTO on isolator Q1"],
    "manual_citations": ["Conveyor_C2_Manual.pdf p.37", "Made_Up_Manual.pdf p.99"],
    "estimated_downtime_min": 50, "work_order_title": "Replace guide-rail pads C2",
    "needs_human": False, "human_reason": "",
}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "factorypilot-vl", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    LAST_REQUEST.clear()
    LAST_REQUEST.update(body)
    wants_json = bool(body.get("response_format"))
    text = json.dumps(DIAG) if wants_json else "Vibration is rising because the drive-end bearing is wearing [Conveyor_C2_Manual.pdf p.41]."

    def gen():
        pieces = [text[i:i + 16] for i in range(0, len(text), 16)]
        for p in pieces:
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
        end = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": body["model"],
               "choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": len(pieces) * 4, "total_tokens": 0}}
        yield f"data: {json.dumps(end)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
