from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, copilot, fleet, knowledge, workorders
from .config import ROOT, settings
from .evolus import evolus
from .llm import metrics_summary, ping

VERSION = "1.0.0"
app = FastAPI(title="FactoryPilot", version=VERSION,
              description="Photo + sensor log → diagnosis → work order. VLM on AMD Instinct via vLLM; actions via Evolus.")

STATIC = ROOT / "static"
SAMPLES = settings.data_dir / "samples"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")

_ping_cache: dict = {"t": 0, "v": None}


def _sse(events: Iterator[dict]) -> StreamingResponse:
    def gen():
        try:
            for ev in events:
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # noqa: BLE001 - surface errors in the stream instead of a dead socket
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ pages & status

@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "mock": settings.mock, "model": settings.vllm_model, "amd": settings.use_vllm,
            "version": VERSION}


@app.get("/api/status")
def status():
    if time.time() - _ping_cache["t"] > 30 or _ping_cache["v"] is None:
        _ping_cache.update(t=time.time(), v=ping())
    return {
        "version": VERSION,
        "llm": {"amd": settings.use_vllm, "label": settings.backend_label, "model": settings.vllm_model,
                "hardware": settings.hardware_label, "endpoint": _ping_cache["v"]},
        "evolus": evolus.status(),
        "confidence_floor": settings.confidence_floor,
    }


# ------------------------------------------------------------------ plant

@app.get("/api/fleet")
def get_fleet():
    return fleet.fleet()


@app.get("/api/machines/{mid}")
def get_machine(mid: str):
    if not fleet.by_id(mid):
        raise HTTPException(404, "unknown machine")
    return fleet.snapshot(mid)


@app.get("/api/machines/{mid}/log.csv")
def get_machine_log(mid: str):
    if not fleet.by_id(mid):
        raise HTTPException(404, "unknown machine")
    return Response(fleet.log_csv(mid), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{mid}_log.csv"'})


@app.get("/api/samples")
def samples():
    owners = {m["sample"]: m for m in fleet.MACHINES}
    out = []
    for p in sorted(SAMPLES.glob("*.png")):
        m = owners.get(p.name)
        label = p.stem.replace("_", " ").title()
        out.append({"name": p.name, "url": f"/samples/{p.name}", "label": label,
                    "machine_id": m["id"] if m else None})
    return out


@app.get("/api/manuals")
def manuals():
    return knowledge.all_sources()


# ------------------------------------------------------------------ agent

async def _inputs(image: UploadFile | None, sample: str, logs: UploadFile | None, machine: str,
                  live_logs: bool) -> tuple[bytes | None, str, bytes | None]:
    img, mime = None, "image/png"
    if image is not None and image.filename:
        img, mime = await image.read(), image.content_type or "image/png"
    elif sample:
        p = (SAMPLES / Path(sample).name)
        if not p.exists():
            raise HTTPException(400, "unknown sample")
        img = p.read_bytes()
    csv = await logs.read() if logs is not None and logs.filename else None
    if csv is None and live_logs:
        m = fleet.by_name(machine)
        if m:
            csv = fleet.log_csv(m["id"])
    if img is None and csv is None:
        raise HTTPException(400, "provide a photo, a sensor log, or both")
    return img, mime, csv


@app.post("/api/diagnose/stream")
async def diagnose_stream(machine: str = Form(...), note: str = Form(""), language: str = Form("English"),
                          sample: str = Form(""), live_logs: bool = Form(True),
                          image: UploadFile | None = File(None), logs: UploadFile | None = File(None)):
    img, mime, csv = await _inputs(image, sample, logs, machine, live_logs)
    return _sse(agent.run(img, mime, csv, machine, note, language))


@app.post("/diagnose")
@app.post("/api/diagnose")
async def diagnose_json(machine: str = Form(...), note: str = Form(""), language: str = Form("English"),
                        sample: str = Form(""), live_logs: bool = Form(False),
                        image: UploadFile | None = File(None), logs: UploadFile | None = File(None)):
    img, mime, csv = await _inputs(image, sample, logs, machine, live_logs)
    res = agent.diagnose(img, mime, csv, machine, note, language)
    res.pop("overlay", None)
    return JSONResponse(res)


# ------------------------------------------------------------------ work orders

@app.get("/api/workorders")
def list_orders():
    return workorders.list_all()


@app.get("/api/workorders/{oid}")
def get_order(oid: str):
    o = workorders.get(oid)
    if not o:
        raise HTTPException(404, "unknown work order")
    return o


@app.post("/api/workorders/{oid}/{action}")
def act_order(oid: str, action: str, body: dict = Body(default={})):
    if action not in {"approve", "reject", "complete", "reopen"}:
        raise HTTPException(400, "unknown action")
    try:
        return workorders.transition(oid, action, body.get("by", "Shift supervisor"), body.get("comment", ""))
    except KeyError:
        raise HTTPException(404, "unknown work order") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


# ------------------------------------------------------------------ copilot, metrics, demo

@app.post("/api/copilot")
def copilot_stream(body: dict = Body(...)):
    q = (body.get("question") or "").strip()
    if not q:
        raise HTTPException(400, "empty question")
    return _sse(copilot.answer(q, body.get("machine_id"), body.get("history")))


@app.get("/api/metrics")
def get_metrics():
    return {"llm": metrics_summary(), "benchmarks": _benchmarks()}


def _benchmarks() -> list[dict]:
    rows = []
    for p in sorted((ROOT / "docs" / "benchmarks").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({"file": p.name, "concurrency": d.get("max_concurrency"),
                     "output_tok_s": d.get("output_throughput"), "total_tok_s": d.get("total_token_throughput"),
                     "req_s": d.get("request_throughput"), "mean_ttft_ms": d.get("mean_ttft_ms"),
                     "p99_ttft_ms": d.get("p99_ttft_ms"), "mean_tpot_ms": d.get("mean_tpot_ms"),
                     "model": d.get("model_id") or d.get("model")})
    return sorted(rows, key=lambda r: r["concurrency"] or 0)


@app.post("/api/demo/reset")
def demo_reset():
    workorders.reset()
    fleet.reset_clock()
    return {"ok": True}
