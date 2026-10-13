"""The FactoryPilot agent: perceive -> analyse -> retrieve -> reason (AMD) -> guardrails -> act (Evolus).

`run()` is a generator of trace events so the UI can show every step live (Server-Sent Events).
`diagnose()` drains it and returns the final payload for plain API callers.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterator

from . import knowledge, reasoner, sensors as sensor_mod, vision as vision_mod, workorders
from .config import settings
from .llm import image_part, metrics, parse_json, stream_chat

SEV_ORDER = ["low", "medium", "high", "critical"]


def _step(sid: str, title: str, status: str = "running", detail: str = "", data: dict | None = None,
          tool: str = "") -> dict:
    return {"type": "step", "id": sid, "title": title, "status": status, "detail": detail, "tool": tool,
            "data": data or {}, "ts": time.time()}


def _normalize(d: dict) -> dict:
    out = {
        "defect_detected": bool(d.get("defect_detected", False)),
        "defect_type": str(d.get("defect_type") or "unknown"),
        "location": str(d.get("location") or "-"),
        "severity": d.get("severity") if d.get("severity") in SEV_ORDER else "medium",
        "confidence": float(d.get("confidence") or 0),
        "summary": str(d.get("summary") or ""),
        "likely_root_cause": str(d.get("likely_root_cause") or ""),
        "evidence": [e if isinstance(e, dict) else {"source": "image", "text": str(e)} for e in d.get("evidence") or []],
        "recommended_actions": [str(a) for a in d.get("recommended_actions") or []],
        "safety_notes": [str(a) for a in d.get("safety_notes") or []],
        "manual_citations": [str(c) for c in d.get("manual_citations") or []],
        "estimated_downtime_min": int(d.get("estimated_downtime_min") or 0),
        "work_order_title": str(d.get("work_order_title") or ""),
        "needs_human": bool(d.get("needs_human", False)),
        "human_reason": str(d.get("human_reason") or ""),
    }
    if out["confidence"] > 1:
        out["confidence"] /= 100
    return out


def guardrails(diag: dict, vision: dict, sensors: dict, hits: list[dict]) -> list[dict]:
    """Deterministic checks that run regardless of what the model said. Mutates diag."""
    checks = []
    reasons = []

    ok = diag["confidence"] >= settings.confidence_floor
    checks.append({"name": "Confidence floor", "pass": ok,
                   "detail": f"{diag['confidence']:.2f} vs floor {settings.confidence_floor:.2f}"})
    if not ok:
        reasons.append(f"confidence {diag['confidence']:.2f} below {settings.confidence_floor}")

    ok = diag["severity"] != "critical"
    checks.append({"name": "Severity gate", "pass": ok, "detail": f"severity = {diag['severity']}"})
    if not ok:
        reasons.append("critical severity requires supervisor sign-off")

    known = {h["source"] for h in hits}
    bad = [c for c in diag["manual_citations"] if c not in known]
    diag["manual_citations"] = [c for c in diag["manual_citations"] if c in known]
    checks.append({"name": "Citation check", "pass": not bad,
                   "detail": f"{len(diag['manual_citations'])} verified" + (f", removed {len(bad)} unverifiable: "
                                                                             f"{', '.join(bad)}" if bad else "")})

    strong_visual = vision.get("anomaly_score", 0) >= 0.6
    sensor_alert = sensors.get("available") and sensors.get("status") in {"warn", "alarm", "trip"}
    conflict = (strong_visual and not diag["defect_detected"]) or (
        diag["defect_detected"] and not vision.get("defect_suspected") and not sensor_alert)
    checks.append({"name": "Evidence agreement", "pass": not conflict,
                   "detail": "photo, sensors and model agree" if not conflict else
                   "model conclusion conflicts with pre-screen/sensor evidence"})
    if conflict:
        reasons.append("evidence conflict between photo, sensors and model")

    trip = any(c["status"] == "trip" for c in sensors.get("channels", []))
    checks.append({"name": "Safety interlock", "pass": not trip,
                   "detail": "no channel at trip level" if not trip else "a channel is at TRIP level"})
    if trip:
        reasons.append("sensor at trip level — immediate stop")
        diag["severity"] = "critical"
        if not any("stop" in a.lower() for a in diag["recommended_actions"]):
            diag["recommended_actions"].insert(0, "Stop the machine now (sensor at trip level)")

    if reasons:
        diag["needs_human"] = True
        diag["human_reason"] = "; ".join(dict.fromkeys(([diag["human_reason"]] if diag["human_reason"] else [])
                                                       + reasons))
    elif diag["needs_human"] and not diag["human_reason"]:
        diag["human_reason"] = "model requested human review"
    return checks


def run(image: bytes | None, mime: str, logs_csv: bytes | None, machine: str, note: str = "",
        language: str = "English", pace: float | None = None) -> Iterator[dict]:
    run_id = uuid.uuid4().hex[:8]
    t_start = time.perf_counter()
    pace = (0.0 if settings.use_vllm else 0.35) if pace is None else pace
    yield {"type": "start", "run_id": run_id, "machine": machine, "backend": settings.backend_label,
           "amd": settings.use_vllm}

    # 1. Perceive
    yield _step("vision", "Visual pre-screen", tool="vision.inspect")
    time.sleep(pace)
    if image:
        vis = vision_mod.inspect(image)
        yield _step("vision", "Visual pre-screen", "warn" if vis["defect_suspected"] else "done", vis["summary"],
                    {"overlay": vis["overlay_png"], "regions": vis["regions"], "method": vis["method"],
                     "anomaly_score": vis["anomaly_score"]}, tool="vision.inspect")
    else:
        vis = {"method": "none", "defect_suspected": False, "anomaly_score": 0, "regions": [],
               "summary": "No photo supplied.", "overlay_png": None}
        yield _step("vision", "Visual pre-screen", "done", vis["summary"], tool="vision.inspect")

    # 2. Analyse sensors
    yield _step("sensors", "Sensor analytics", tool="sensors.analyze")
    time.sleep(pace)
    try:
        sens = sensor_mod.analyze(sensor_mod.load_csv(logs_csv))
    except Exception as e:  # noqa: BLE001
        sens = sensor_mod.analyze(None)
        sens["summary"] = f"Sensor log could not be parsed ({type(e).__name__})."
    yield _step("sensors", "Sensor analytics", "warn" if sens.get("status") in {"warn", "alarm", "trip"} else "done",
                sens["summary"], {k: sens.get(k) for k in ("health", "status", "forecast", "findings", "series")},
                tool="sensors.analyze")

    # 3. Retrieve manuals
    query = reasoner.query_for(vis, sens, note)
    yield _step("manuals", "Search equipment manuals", detail=f"query: “{query}”", tool="knowledge.search")
    time.sleep(pace)
    hits, provider = knowledge.search(query, machine, top_k=4)
    safety_hits, _ = knowledge.search("safety lock out LOTO isolator", machine, top_k=1)
    all_hits = hits + [h for h in safety_hits if h["source"] not in {x["source"] for x in hits}]
    yield _step("manuals", "Search equipment manuals", "done",
                f"{len(all_hits)} excerpts from {provider}", {"hits": all_hits, "provider": provider},
                tool="knowledge.search")

    # 4. Reason
    if settings.use_vllm:
        yield _step("reason", "Reason over evidence", detail=settings.backend_label, tool="vlm.diagnose")
        user = [{"type": "text", "text": reasoner.build_user_text(machine, note, vis, sens, all_hits)}]
        if image:
            user.insert(0, image_part(image, mime))
        msgs = [{"role": "system", "content": reasoner.SYSTEM.replace("{language}", language)},
                {"role": "user", "content": user}]
        tele = {}
        try:
            text = ""
            for ev in stream_chat(msgs, json_schema=reasoner.SCHEMA, purpose="diagnosis"):
                if "delta" in ev:
                    yield {"type": "token", "text": ev["delta"]}
                else:
                    text, tele = ev["text"], ev["telemetry"]
            raw = parse_json(text)
            if raw.get("parse_error"):
                raise ValueError("model returned non-JSON output")
            diag = _normalize(raw)
            yield _step("reason", "Reason over evidence", "done",
                        f"{tele['tokens_out']} tokens · TTFT {tele['ttft_s']} s · {tele['tokens_per_s']} tok/s",
                        {"telemetry": tele}, tool="vlm.diagnose")
        except Exception as e:  # noqa: BLE001 - degrade gracefully, never leave the supervisor without an answer
            diag = _normalize(reasoner.offline_diagnosis(machine, note, vis, sens, all_hits, safety_hits))
            diag["confidence"] = min(diag["confidence"], 0.55)
            diag["human_reason"] = f"AMD endpoint unavailable ({type(e).__name__}); offline fallback used"
            tele = {"backend": "offline fallback", "error": str(e)[:200]}
            yield _step("reason", "Reason over evidence", "error",
                        f"AMD endpoint error: {type(e).__name__}: {str(e)[:160]} — used offline reasoner",
                        {"telemetry": tele}, tool="vlm.diagnose")
    else:
        yield _step("reason", "Reason over evidence", detail="Offline demo reasoner (set VLLM_BASE_URL for AMD)",
                    tool="reasoner.offline")
        time.sleep(pace * 2)
        t0 = time.perf_counter()
        diag = _normalize(reasoner.offline_diagnosis(machine, note, vis, sens, all_hits, safety_hits))
        tele = {"backend": "offline demo reasoner", "latency_s": round(time.perf_counter() - t0, 3),
                "simulated": True}
        metrics.appendleft({**tele, "purpose": "diagnosis", "ts": time.time()})
        yield _step("reason", "Reason over evidence", "done", "Diagnosis composed from evidence (offline mode)",
                    {"telemetry": tele}, tool="reasoner.offline")

    # 5. Guardrails
    yield _step("guard", "Safety guardrails", tool="guardrails")
    time.sleep(pace)
    checks = guardrails(diag, vis, sens, all_hits)
    failed = [c for c in checks if not c["pass"]]
    yield _step("guard", "Safety guardrails", "warn" if diag["needs_human"] else "done",
                f"{len(checks) - len(failed)}/{len(checks)} checks passed" +
                (f" → human approval required: {diag['human_reason']}" if diag["needs_human"] else ""),
                {"checks": checks}, tool="guardrails")

    # 6. Act
    order = None
    if diag["defect_detected"]:
        yield _step("act", "Create work order", tool="evolus.workflow")
        time.sleep(pace)
        order = workorders.create(machine, diag, vis.get("overlay_png"))
        msg = (f"{order['id']} held for supervisor approval" if order["status"] == "awaiting_approval"
               else f"{order['id']} dispatched to {order['assigned_to']} via {order['dispatch_channel']}")
        yield _step("act", "Create work order", "warn" if order["escalate"] else "done", msg,
                    {"order_id": order["id"], "status": order["status"]}, tool="evolus.workflow")
    else:
        yield _step("act", "Create work order", "done", "No defect — no work order needed", tool="evolus.workflow")

    order_out = dict(order, image_overlay=None) if order else None
    yield {"type": "result", "run_id": run_id, "diagnosis": diag, "work_order": order_out,
           "manual_hits": all_hits, "manual_provider": provider, "log_summary": sens["summary"],
           "sensors": {k: sens.get(k) for k in ("health", "status", "forecast", "findings", "channels")},
           "vision": {k: vis.get(k) for k in ("method", "anomaly_score", "regions", "summary")},
           "overlay": vis.get("overlay_png"), "guardrails": checks, "telemetry": tele,
           "elapsed_s": round(time.perf_counter() - t_start, 2)}


def diagnose(image: bytes | None, mime: str, logs_csv: bytes | None, machine: str, note: str = "",
             language: str = "English") -> dict:
    result: dict = {}
    for ev in run(image, mime, logs_csv, machine, note, language, pace=0):
        if ev["type"] == "result":
            result = ev
    return result
