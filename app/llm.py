"""Client for the vision-language model served by vLLM on AMD Instinct (ROCm).

Every call streams so we can measure time-to-first-token and decode throughput; those
numbers feed the live "AMD inference" panel in the UI (`metrics`).
"""
from __future__ import annotations

import base64
import json
import re
import time
from collections import deque
from typing import Iterator

from openai import OpenAI

from .config import settings

metrics: deque[dict] = deque(maxlen=200)

_clients: dict[str, OpenAI] = {}


def client(kind: str = "vllm") -> OpenAI:
    if kind not in _clients:
        if kind == "evolus":
            _clients[kind] = OpenAI(base_url=settings.evolus_chat_base_url, api_key=settings.evolus_api_key,
                                    default_headers={"X-ApiKey": settings.evolus_api_key,
                                                     **({"X-Application-Id": settings.evolus_app_id}
                                                        if settings.evolus_app_id else {})},
                                    timeout=settings.vllm_timeout_s)
        else:
            _clients[kind] = OpenAI(base_url=settings.vllm_base_url, api_key=settings.vllm_api_key,
                                    timeout=settings.vllm_timeout_s)
    return _clients[kind]


def image_part(image_bytes: bytes, mime: str = "image/png") -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"raw": text, "parse_error": True}


def stream_chat(messages: list[dict], *, max_tokens: int = 900, temperature: float = 0.1,
                json_schema: dict | None = None, purpose: str = "chat", via: str = "vllm") -> Iterator[dict]:
    """Yield {"delta": str} events, then a final {"done": True, "text": str, "telemetry": {...}}."""
    kwargs: dict = {}
    if json_schema is not None:
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": "diagnosis", "schema": json_schema}}
    model = settings.evolus_chat_model if via == "evolus" else settings.vllm_model
    t0 = time.perf_counter()
    ttft = None
    chunks: list[str] = []
    usage_tokens = None
    try:
        stream = client(via).chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True,
            stream_options={"include_usage": True}, **kwargs)
    except Exception:
        if json_schema is None:
            raise
        # Older vLLM builds: fall back to plain JSON mode
        stream = client(via).chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, temperature=temperature, stream=True,
            response_format={"type": "json_object"})
    for ev in stream:
        if getattr(ev, "usage", None):
            usage_tokens = ev.usage.completion_tokens
        if not ev.choices:
            continue
        piece = ev.choices[0].delta.content or ""
        if piece:
            if ttft is None:
                ttft = time.perf_counter() - t0
            chunks.append(piece)
            yield {"delta": piece}
    total = time.perf_counter() - t0
    text = "".join(chunks)
    tokens = usage_tokens or max(1, len(text) // 4)
    decode_s = max(1e-3, total - (ttft or 0))
    tele = {
        "backend": settings.backend_label if via == "vllm" else f"Evolus agent → {settings.hardware_label}",
        "model": model, "purpose": purpose, "ttft_s": round(ttft or total, 3), "latency_s": round(total, 3),
        "tokens_out": tokens, "tokens_per_s": round(tokens / decode_s, 1), "ts": time.time(),
    }
    metrics.appendleft(tele)
    yield {"done": True, "text": text, "telemetry": tele}


def ping() -> dict:
    """Cheap reachability check for the /api/status endpoint."""
    if not settings.use_vllm:
        return {"reachable": False, "reason": "no endpoint configured"}
    t0 = time.perf_counter()
    try:
        models = [m.id for m in client().models.list().data]
        return {"reachable": True, "models": models, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "reason": f"{type(e).__name__}: {e}"[:200]}


def metrics_summary() -> dict:
    items = list(metrics)
    if not items:
        return {"calls": 0, "recent": []}
    real = [m for m in items if not m.get("simulated")]
    use = real or items

    def avg(k):
        vals = [m[k] for m in use if m.get(k) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {"calls": len(items), "avg_ttft_s": avg("ttft_s"), "avg_latency_s": avg("latency_s"),
            "avg_tokens_per_s": avg("tokens_per_s"), "simulated": not real, "recent": items[:20]}
