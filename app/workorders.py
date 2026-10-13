"""Work-order store (SQLite) + dispatch through the Evolus workflow.

Lifecycle:  awaiting_approval --approve--> dispatched --complete--> closed
                              \\--reject--> rejected
Orders that pass every guardrail go straight to `dispatched`. Dispatch is the moment the
Evolus workflow runs (building block #2); the returned run id is stored on the order.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import settings
from .evolus import evolus

_lock = threading.Lock()

ROSTER = [
    {"name": "Giulia Rossi", "skills": ["conveyor", "packaging"], "shift": "A"},
    {"name": "Marco Bianchi", "skills": ["press", "hydraulic"], "shift": "A"},
    {"name": "Sara Conti", "skills": ["cnc", "spindle"], "shift": "A"},
    {"name": "Luca Ferrari", "skills": ["conveyor", "press", "cnc", "packaging"], "shift": "A"},
]
PRIORITY_SLA_H = {"critical": 1, "high": 4, "medium": 24, "low": 72}


def _conn() -> sqlite3.Connection:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(settings.db_path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS work_orders (
        id TEXT PRIMARY KEY, created_at REAL, updated_at REAL, machine TEXT, priority TEXT, status TEXT,
        title TEXT, summary TEXT, root_cause TEXT, actions TEXT, citations TEXT, escalate INTEGER,
        escalate_reason TEXT, assigned_to TEXT, evolus_ref TEXT, dispatch_channel TEXT, diagnosis TEXT,
        image_overlay TEXT, history TEXT, est_downtime_min INTEGER)""")
    return c


def _next_id(c: sqlite3.Connection) -> str:
    n = c.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] + 1
    return f"WO-{time.strftime('%y%m')}-{n:04d}"


def _assign(machine: str) -> str:
    m = machine.lower()
    for t in ROSTER:
        if any(s in m for s in t["skills"]) and len(t["skills"]) < 4:
            return t["name"]
    return ROSTER[-1]["name"]


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("actions", "citations", "diagnosis", "history"):
        d[k] = json.loads(d[k] or "null")
    d["escalate"] = bool(d["escalate"])
    d["sla_h"] = PRIORITY_SLA_H.get(d["priority"], 24)
    return d


def _dispatch(order: dict) -> tuple[str, str]:
    """Run the Evolus work-order workflow; fall back to the local queue."""
    if settings.evolus_workflow_live:
        try:
            res = evolus.create_work_order({k: order[k] for k in (
                "id", "machine", "priority", "title", "summary", "root_cause", "actions", "citations",
                "assigned_to", "escalate")})
            return res.get("evolus_ref") or "submitted", "Evolus workflow"
        except Exception as e:  # noqa: BLE001
            return "", f"local queue (Evolus error: {type(e).__name__})"
    return "", "local queue"


def create(machine: str, diagnosis: dict, overlay: str | None = None) -> dict:
    with _lock, _conn() as c:
        now = time.time()
        oid = _next_id(c)
        esc = bool(diagnosis.get("needs_human"))
        order = {
            "id": oid, "created_at": now, "updated_at": now, "machine": machine,
            "priority": diagnosis.get("severity", "medium"),
            "status": "awaiting_approval" if esc else "dispatched",
            "title": diagnosis.get("work_order_title") or f"{diagnosis.get('defect_type', 'issue')} — {machine}",
            "summary": diagnosis.get("summary", ""), "root_cause": diagnosis.get("likely_root_cause", ""),
            "actions": diagnosis.get("recommended_actions", []), "citations": diagnosis.get("manual_citations", []),
            "escalate": esc, "escalate_reason": diagnosis.get("human_reason", ""),
            "assigned_to": _assign(machine), "evolus_ref": "", "dispatch_channel": "",
            "diagnosis": diagnosis, "image_overlay": overlay,
            "history": [{"ts": now, "event": "created by FactoryPilot agent"}],
            "est_downtime_min": int(diagnosis.get("estimated_downtime_min") or 0),
        }
        if not esc:
            order["evolus_ref"], order["dispatch_channel"] = _dispatch(order)
            order["history"].append({"ts": now, "event": f"dispatched to {order['assigned_to']} via "
                                                         f"{order['dispatch_channel']}"})
        else:
            order["history"].append({"ts": now, "event": f"held for supervisor approval: {order['escalate_reason']}"})
        _save(c, order)
        return order


def _save(c: sqlite3.Connection, o: dict) -> None:
    c.execute("""INSERT OR REPLACE INTO work_orders VALUES (:id,:created_at,:updated_at,:machine,:priority,:status,
        :title,:summary,:root_cause,:actions,:citations,:escalate,:escalate_reason,:assigned_to,:evolus_ref,
        :dispatch_channel,:diagnosis,:image_overlay,:history,:est_downtime_min)""",
              {**o, "actions": json.dumps(o["actions"]), "citations": json.dumps(o["citations"]),
               "diagnosis": json.dumps(o["diagnosis"]), "history": json.dumps(o["history"]),
               "escalate": int(o["escalate"])})


def get(oid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM work_orders WHERE id=?", (oid,)).fetchone()
        return _row(r) if r else None


def list_all(include_images: bool = False) -> list[dict]:
    with _conn() as c:
        rows = [_row(r) for r in c.execute("SELECT * FROM work_orders ORDER BY created_at DESC")]
    if not include_images:
        for r in rows:
            r["image_overlay"] = None
    return rows


def transition(oid: str, action: str, by: str = "supervisor", comment: str = "") -> dict:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM work_orders WHERE id=?", (oid,)).fetchone()
        if not r:
            raise KeyError(oid)
        o = _row(r)
        o.pop("sla_h", None)
        now = time.time()
        allowed = {"approve": ("awaiting_approval",), "reject": ("awaiting_approval",),
                   "complete": ("dispatched",), "reopen": ("closed", "rejected")}
        if o["status"] not in allowed.get(action, ()):
            raise ValueError(f"cannot {action} an order in status {o['status']}")
        note = f" — {comment}" if comment else ""
        if action == "approve":
            o["status"] = "dispatched"
            o["evolus_ref"], o["dispatch_channel"] = _dispatch(o)
            o["history"].append({"ts": now, "event": f"approved by {by}{note}; dispatched to {o['assigned_to']} "
                                                     f"via {o['dispatch_channel']}"})
        elif action == "reject":
            o["status"] = "rejected"
            o["history"].append({"ts": now, "event": f"rejected by {by}{note}"})
        elif action == "complete":
            o["status"] = "closed"
            o["history"].append({"ts": now, "event": f"closed by {by}{note}"})
        else:
            o["status"] = "awaiting_approval"
            o["history"].append({"ts": now, "event": f"reopened by {by}{note}"})
        o["updated_at"] = now
        _save(c, o)
        return _row(c.execute("SELECT * FROM work_orders WHERE id=?", (oid,)).fetchone())


def reset() -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM work_orders")
