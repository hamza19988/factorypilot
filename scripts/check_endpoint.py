"""Smoke-test the AMD vLLM endpoint: models, text, image, JSON mode, and throughput.

Usage: python scripts/check_endpoint.py [path/to/image.png]
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
base = os.environ.get("VLLM_BASE_URL", "")
if not base or "<" in base:
    sys.exit("Set VLLM_BASE_URL (and VLLM_API_KEY) in .env first.")
client = OpenAI(base_url=base, api_key=os.environ.get("VLLM_API_KEY", "x"))
model = os.getenv("VLLM_MODEL", "factorypilot-vl")

print("Endpoint:", base)
print("Models served:", [m.id for m in client.models.list().data])


def timed(messages, **kw):
    t0 = time.perf_counter()
    ttft, out, toks = None, [], 0
    for ev in client.chat.completions.create(model=model, messages=messages, stream=True,
                                             stream_options={"include_usage": True}, **kw):
        if getattr(ev, "usage", None):
            toks = ev.usage.completion_tokens
        if ev.choices and ev.choices[0].delta.content:
            ttft = ttft or time.perf_counter() - t0
            out.append(ev.choices[0].delta.content)
    total = time.perf_counter() - t0
    return "".join(out), ttft or total, total, toks


text, ttft, total, toks = timed([{"role": "user", "content": "Say 'AMD endpoint OK' and nothing else."}], max_tokens=20)
print(f"Text  -> {text!r}  TTFT {ttft:.2f}s, total {total:.2f}s")

text, ttft, total, toks = timed([{"role": "user", "content": "Explain in 150 words how bearing wear shows up in vibration data."}],
                                max_tokens=300)
print(f"Decode -> {toks} tokens in {total - ttft:.2f}s = {toks / max(total - ttft, 1e-3):.0f} tok/s (single stream)")

img_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/samples/conveyor_c2_scratch.png")
b64 = base64.b64encode(img_path.read_bytes()).decode()
msgs = [{"role": "user", "content": [
    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    {"type": "text", "text": 'Return JSON {"defect": bool, "type": str, "where": str} for this part.'}]}]
text, ttft, total, _ = timed(msgs, max_tokens=120, response_format={"type": "json_object"})
print(f"Image+JSON -> {text.strip()}  ({total:.2f}s)")
json.loads(text)
print("All checks passed.")
