"""Shift-supervisor copilot: grounded Q&A over live machine state + manuals.

Routing: Evolus chat agent (if configured, itself backed by our AMD vLLM endpoint)
         -> direct AMD vLLM endpoint -> offline retrieval answer.
"""
from __future__ import annotations

import time
from typing import Iterator

from . import fleet, knowledge, workorders
from .config import settings
from .llm import stream_chat

SYSTEM = """You are FactoryPilot Copilot, helping a factory shift supervisor. Answer briefly (max ~120 words),
concretely and safely. Use ONLY the plant context and manual excerpts provided; cite manual sources in
square brackets like [Conveyor_C2_Manual.pdf p.41]. If the answer is not in the context, say so.
Never tell anyone to bypass safety interlocks. Reply in the user's language."""


def _context(machine_id: str | None, question: str) -> tuple[str, list[dict]]:
    lines = []
    snap = fleet.snapshot(machine_id) if machine_id and fleet.by_id(machine_id) else None
    machine = snap["name"] if snap else ""
    if snap:
        lines.append(f"Machine {snap['name']} ({snap['line']}): status {snap['status']}, health {snap['health']}/100.")
        lines += [f"- {f['text']}" for f in snap["findings"]]
        if snap.get("forecast"):
            lines.append(f"- Forecast: {snap['forecast']['text']}")
    else:
        for m in fleet.fleet():
            lines.append(f"{m['name']}: status {m['status']}, health {m['health']}/100")
    open_orders = [o for o in workorders.list_all() if o["status"] in {"awaiting_approval", "dispatched"}]
    for o in open_orders[:5]:
        lines.append(f"Open work order {o['id']} [{o['priority']}, {o['status']}] {o['title']} → {o['assigned_to']}")
    hits, _ = knowledge.search(question, machine, top_k=3)
    lines.append("\nManual excerpts:")
    lines += [f"[{h['source']}] {h['title']}: {h['text']}" for h in hits]
    return "\n".join(lines), hits


def answer(question: str, machine_id: str | None = None, history: list[dict] | None = None) -> Iterator[dict]:
    ctx, hits = _context(machine_id, question)
    msgs = [{"role": "system", "content": SYSTEM + "\n\nPlant context:\n" + ctx}]
    msgs += [m for m in (history or [])[-6:] if m.get("role") in {"user", "assistant"}]
    msgs.append({"role": "user", "content": question})
    yield {"type": "sources", "hits": [{"source": h["source"], "title": h["title"]} for h in hits]}

    for via, enabled in (("evolus", settings.evolus_chat_live), ("vllm", settings.use_vllm)):
        if not enabled:
            continue
        try:
            for ev in stream_chat(msgs, max_tokens=400, temperature=0.2, purpose="copilot", via=via):
                if "delta" in ev:
                    yield {"type": "token", "text": ev["delta"]}
                else:
                    yield {"type": "done", "telemetry": ev["telemetry"]}
            return
        except Exception as e:  # noqa: BLE001
            yield {"type": "notice", "text": f"{via} unavailable ({type(e).__name__}), falling back"}

    # offline: retrieval-only answer, streamed for a consistent UX
    text = _offline(question, machine_id, hits)
    for i in range(0, len(text), 12):
        yield {"type": "token", "text": text[i:i + 12]}
        time.sleep(0.012)
    yield {"type": "done", "telemetry": {"backend": "offline retrieval (no LLM configured)", "simulated": True}}


def _offline(question: str, machine_id: str | None, hits: list[dict]) -> str:
    parts = []
    if machine_id and fleet.by_id(machine_id):
        s = fleet.snapshot(machine_id)
        parts.append(f"{s['name']} is {s['status'].upper()} (health {s['health']}/100).")
        if s["findings"]:
            parts.append(s["findings"][0]["text"])
        if s.get("forecast") and s["status"] != "ok":
            parts.append(f"Forecast: {s['forecast']['text']}")
    if hits:
        parts.append("From the manuals:")
        for h in hits[:2]:
            parts.append(f"• {h['text']} [{h['source']}]")
    parts.append("(Offline mode: retrieval only. Connect the AMD endpoint for a reasoned answer.)")
    return "\n".join(parts)
