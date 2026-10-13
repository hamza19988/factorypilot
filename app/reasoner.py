"""Diagnosis prompt + schema for the AMD-hosted VLM, and an offline rule-based reasoner.

The offline reasoner exists so the whole product runs without a GPU (CI, laptops, judges
cloning the repo). It is grounded in the same evidence the VLM receives — the visual
pre-screen, the sensor analytics and the retrieved manual excerpts — and the UI labels it
clearly as "Offline demo reasoner" whenever it is used.
"""
from __future__ import annotations

import re

SCHEMA = {
    "type": "object",
    "properties": {
        "defect_detected": {"type": "boolean"},
        "defect_type": {"type": "string"},
        "location": {"type": "string"},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "likely_root_cause": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["image", "sensors", "manual"]}, "text": {"type": "string"}},
            "required": ["source", "text"]}},
        "recommended_actions": {"type": "array", "items": {"type": "string"}},
        "safety_notes": {"type": "array", "items": {"type": "string"}},
        "manual_citations": {"type": "array", "items": {"type": "string"}},
        "estimated_downtime_min": {"type": "integer"},
        "work_order_title": {"type": "string"},
        "needs_human": {"type": "boolean"},
        "human_reason": {"type": "string"},
    },
    "required": ["defect_detected", "defect_type", "severity", "confidence", "summary", "likely_root_cause",
                 "evidence", "recommended_actions", "manual_citations", "needs_human"],
}

SYSTEM = """You are FactoryPilot, a senior maintenance engineer assisting a factory shift supervisor.
You receive: a photo of a part or machine, a visual pre-screen (automated, may be wrong), computed
sensor-log analytics, and excerpts from the equipment manuals with their sources.

Think like a reliability engineer: correlate what you SEE in the photo with what the SENSORS show and
what the MANUAL says. Prefer the simplest root cause that explains all the evidence.

Return ONLY a JSON object with these keys:
defect_detected (bool), defect_type (short snake_case, e.g. scratch, crack, corrosion, missing_component,
dent, bearing_wear, none), location (where on the part/machine), severity (low|medium|high|critical),
confidence (0-1, calibrated: 0.9 only when photo, sensors and manual all agree), summary (one sentence a
supervisor can read in 3 seconds), likely_root_cause, evidence (list of {source: image|sensors|manual, text}),
recommended_actions (ordered, concrete, imperative, with values from the manual such as torque or limits),
safety_notes (list), manual_citations (ONLY sources exactly as given, e.g. "Conveyor_C2_Manual.pdf p.37"),
estimated_downtime_min (int), work_order_title (max 8 words), needs_human (bool), human_reason (str).

Rules:
- Never invent manual content. Cite only the sources provided. If the manuals do not cover it, say so.
- Set needs_human=true if confidence < 0.6, severity is critical, a safety-critical part is involved,
  or the photo and sensors disagree.
- Severity: critical = safety risk or imminent breakdown; high = stop this shift; medium = schedule
  within 48 h; low = cosmetic / monitor.
- Write all free-text fields in {language}."""


def build_user_text(machine: str, note: str, vision: dict, sensors: dict, hits: list[dict]) -> str:
    manual_text = "\n".join(f"[{h['source']}] {h.get('title', '')}: {h['text']}" for h in hits) or "None found."
    return (f"Machine: {machine}\nOperator note: {note or '-'}\n\n"
            f"Visual pre-screen:\n{vision.get('summary', 'n/a')}\n\n"
            f"Sensor analytics:\n{sensors.get('summary', 'n/a')}\n\n"
            f"Manual excerpts:\n{manual_text}")


# ---------------------------------------------------------------- offline reasoner

RULES = {
    "scratch": dict(sev="medium", down=45,
                    cause="Worn guide-rail pad (wear > 1 mm) letting the steel rail backing contact parts in transfer",
                    query="scratch scratches guide rail pads wear surface marks"),
    "crack": dict(sev="critical", down=120,
                  cause="Cracked or chipped die insert producing repeat-location cracks in formed parts",
                  query="crack cracks die insert cracked chipped"),
    "corrosion": dict(sev="medium", down=60,
                      cause="Coolant concentration below 5 % causing flash rust on finished steel parts",
                      query="corrosion rust orange staining coolant concentration"),
    "missing_component": dict(sev="high", down=30,
                              cause="Missing M8 frame bolt — loose structure causing rattling and misaligned folds",
                              query="missing bolt fasteners loose rattling torque"),
    "dent": dict(sev="low", down=20,
                 cause="Excessive accumulation back-pressure or a jammed accumulation table denting packages",
                 query="dents crushed packages accumulation back-pressure"),
    "surface_anomaly": dict(sev="medium", down=30,
                            cause="Unclassified surface anomaly — needs visual confirmation by a technician",
                            query="surface defect troubleshooting inspect"),
    "bearing_wear": dict(sev="high", down=90,
                         cause="Drive-end bearing degradation (accelerating vibration, temperature creep, impact spikes)",
                         query="bearing vibration failure re-grease replace"),
    "loose_fastener": dict(sev="medium", down=30,
                           cause="Loose or missing fastener — impulsive vibration spikes without a wear trend",
                           query="missing loose fasteners bolts rattling vibration spikes"),
    "lubrication": dict(sev="medium", down=40,
                        cause="Insufficient lubrication raising load: current and temperature drifting up",
                        query="lubrication motor current temperature forming force"),
}

_VERBS = ("inspect", "replace", "check", "clean", "re-grease", "torque", "run", "reduce", "verify", "correct",
          "mount", "remove", "lock", "adjust", "test", "lubricate", "measure")


def _lc(text: str) -> str:
    """Lower-case the first letter only (keeps part numbers like M8 intact)."""
    return text[:1].lower() + text[1:]


def sensor_signature(sensors: dict) -> str | None:
    """Classify the dominant sensor pattern, if any."""
    if not sensors.get("available"):
        return None
    ch = {c["name"]: c for c in sensors["channels"]}
    vib = ch.get("vibration_rms_mm_s")
    temp = ch.get("motor_temp_c")
    cur = ch.get("current_a")
    if vib and vib["status"] in {"warn", "alarm", "trip"} and vib["slope_per_h"] > 0.2:
        return "bearing_wear"
    if vib and vib["spikes"] >= 6 and vib["status"] == "ok":
        return "loose_fastener"
    if cur and temp and cur["status"] != "ok" and (vib is None or vib["status"] == "ok"):
        return "lubrication"
    return None


def query_for(vision: dict, sensors: dict, note: str) -> str:
    parts = []
    if vision.get("defect_suspected") and vision["regions"]:
        parts.append(RULES.get(vision["regions"][0]["type"], RULES["surface_anomaly"])["query"])
    sig = sensor_signature(sensors)
    if sig:
        parts.append(RULES[sig]["query"])
    if note:
        parts.append(note)
    return " ".join(parts) or "normal operating envelope inspection"


def _actions_from(hits: list[dict], limit: int = 5) -> list[str]:
    out: list[str] = []
    for h in hits:
        for sent in re.split(r"(?<=[.;])\s+", h["text"]):
            s = sent.strip().rstrip(".;")
            if not s:
                continue
            first = s.split()[0].lower().strip(",")
            if first in _VERBS or " must be replaced" in s or " must be removed" in s:
                s = s[0].upper() + s[1:]
                tagged = f"{s} ({h['source'].split('.pdf ')[-1]})"
                if tagged not in out:
                    out.append(tagged)
            if len(out) >= limit:
                return out
    return out


def offline_diagnosis(machine: str, note: str, vision: dict, sensors: dict, hits: list[dict],
                      safety_hits: list[dict]) -> dict:
    region = vision["regions"][0] if vision.get("defect_suspected") and vision.get("regions") else None
    sig = sensor_signature(sensors)
    status = sensors.get("status", "ok") if sensors.get("available") else "n/a"
    order = ["low", "medium", "high", "critical"]

    if region is None and sig is None:
        return {
            "defect_detected": False, "defect_type": "none", "location": "-", "severity": "low",
            "confidence": 0.8 if sensors.get("available") else 0.7,
            "summary": f"No defect found on {machine}: part looks clean and sensors are inside the envelope.",
            "likely_root_cause": "n/a", "evidence": [
                {"source": "image", "text": vision.get("summary", "")},
                {"source": "sensors", "text": "All channels within normal limits." if sensors.get("available")
                 else "No sensor log supplied."}],
            "recommended_actions": ["Continue production; keep the normal inspection interval."],
            "safety_notes": [], "manual_citations": [], "estimated_downtime_min": 0,
            "work_order_title": "", "needs_human": False, "human_reason": "",
        }

    kind = region["type"] if region else sig
    rule = RULES[kind]
    sev = rule["sev"]
    if status in {"alarm", "trip"}:
        sev = max(sev, "high", key=order.index)
    fc = sensors.get("forecast") or {}
    if fc.get("hours_to_trip") is not None and fc["hours_to_trip"] < 2:
        sev = max(sev, "high", key=order.index)

    cause = rule["cause"]
    if region and sig and sig != kind:
        cause += f"; sensors also show {_lc(RULES[sig]['cause'])}"

    used = [h for h in hits if h.get("score", 0) >= 0.35][:3] or hits[:2]
    actions = _actions_from(used)
    if sig == "bearing_wear" and fc.get("hours_to_trip"):
        actions.insert(0, f"Plan a controlled stop within {max(0.5, fc['hours_to_trip'] - 0.5):.1f} h — "
                          f"vibration projected to hit trip level ({fc['hours_to_trip']} h)")
    if sev == "critical":
        actions.insert(0, "Stop the machine and quarantine the last batch pending supervisor sign-off")
    safety = []
    for h in safety_hits[:1]:
        safety += [s.strip() for s in re.split(r"(?<=[.])\s+", h["text"]) if s.strip()][:2]

    # calibrated-ish confidence: agreement between independent evidence sources
    conf = 0.35
    if region:
        conf += 0.35 * region["score"] * (1.0 if vision["method"].startswith("golden") else 0.7)
    if sig:
        conf += 0.2
    if region and sig:
        conf += 0.05
    if used and used[0].get("score", 0) >= 0.6:
        conf += 0.12
    conf = round(min(conf, 0.93), 2)

    ev = []
    if region:
        ev.append({"source": "image", "text": f"{kind.replace('_', ' ')} at {region['location']} "
                                              f"(pre-screen score {region['score']}, {region['area_pct']}% of frame)"})
    for f in (sensors.get("findings") or [])[:3]:
        ev.append({"source": "sensors", "text": f["text"]})
    for h in used[:2]:
        ev.append({"source": "manual", "text": f"{h['source']}: {h['title']}"})

    loc = region["location"] if region else "drive train (from sensors)"
    title = f"{kind.replace('_', ' ').title()} — {machine}"
    return {
        "defect_detected": True, "defect_type": kind, "location": loc, "severity": sev, "confidence": conf,
        "summary": f"{kind.replace('_', ' ').capitalize()} on {machine} ({loc}); likely {_lc(cause.split(';')[0])}.",
        "likely_root_cause": cause, "evidence": ev,
        "recommended_actions": actions or ["Inspect the affected area and confirm the finding"],
        "safety_notes": safety, "manual_citations": list(dict.fromkeys([h["source"] for h in used] + [h["source"] for h in safety_hits[:1]])),
        "estimated_downtime_min": rule["down"], "work_order_title": title,
        "needs_human": False, "human_reason": "",
    }
