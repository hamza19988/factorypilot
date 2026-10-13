"""Diagnose -> ground in manuals -> decide -> work order."""
import io

import pandas as pd

from .evolus import evolus
from .llm import chat_json, image_part

SYSTEM = """You are FactoryPilot, a maintenance engineer assistant on a factory floor.
Given a photo of a part/machine, recent sensor logs and manual excerpts, return ONLY JSON:
{"defect_detected": bool, "defect_type": str, "location": str,
 "severity": "low"|"medium"|"high"|"critical", "confidence": float 0-1,
 "likely_root_cause": str, "evidence_from_logs": [str],
 "recommended_actions": [str], "manual_citations": [str], "needs_human": bool}
Set needs_human=true if confidence < 0.6, severity is critical, or the evidence conflicts.
Never invent manual content; cite only the excerpts given."""

CONFIDENCE_FLOOR = 0.6


def summarize_logs(csv_bytes: bytes | None) -> str:
    if not csv_bytes:
        return "No sensor logs provided."
    df = pd.read_csv(io.BytesIO(csv_bytes))
    num = df.select_dtypes("number")
    lines = [f"{len(df)} rows, columns: {', '.join(df.columns)}"]
    for col in num.columns:
        s = num[col]
        z = (s - s.mean()) / (s.std() or 1)
        n_anom = int((z.abs() > 3).sum())
        lines.append(f"{col}: mean={s.mean():.2f} max={s.max():.2f} last={s.iloc[-1]:.2f} anomalies(|z|>3)={n_anom}")
    return "\n".join(lines)


def diagnose(image: bytes, mime: str, logs_csv: bytes | None, machine: str, note: str = "") -> dict:
    log_summary = summarize_logs(logs_csv)
    manuals = evolus.search_manuals(f"{machine} {note} defect troubleshooting")
    manual_text = "\n".join(f"[{m.get('source', '?')}] {m.get('text', '')}" for m in manuals) or "None found."

    parts = [
        image_part(image, mime),
        {"type": "text", "text": f"Machine: {machine}\nOperator note: {note or '-'}\n\n"
                                 f"Sensor log summary:\n{log_summary}\n\nManual excerpts:\n{manual_text}"},
    ]
    result, telemetry = chat_json(SYSTEM, parts)

    # Guardrail: enforce human handoff regardless of what the model said.
    conf = float(result.get("confidence", 0) or 0)
    if conf < CONFIDENCE_FLOOR or result.get("severity") == "critical":
        result["needs_human"] = True

    order = None
    if result.get("defect_detected"):
        order = evolus.create_work_order({
            "machine": machine,
            "priority": result.get("severity", "medium"),
            "summary": f"{result.get('defect_type')} at {result.get('location')}",
            "root_cause": result.get("likely_root_cause"),
            "actions": result.get("recommended_actions", []),
            "escalate_to_human": result.get("needs_human", False),
        })

    return {"diagnosis": result, "work_order": order, "manual_hits": manuals,
            "log_summary": log_summary, "telemetry": telemetry}
