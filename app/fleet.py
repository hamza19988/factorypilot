"""Plant model: machines, their simulated live telemetry, and demo samples.

The live feed replays a deterministic 8-hour log per machine; the "now" pointer advances in
real time (SIM_SECONDS_PER_SAMPLE) so the dashboard moves during a demo, and Conveyor C2
drifts from warning into alarm while you present.
"""
from __future__ import annotations

import time
from functools import lru_cache

import pandas as pd

from .config import settings
from .sensors import analyze
from .simulate import make_log

MACHINES = [
    {"id": "conveyor-c2", "name": "Conveyor C2", "line": "Line 2 · Transfer", "model": "BeltMaster 3000",
     "scenario": "bearing", "seed": 21, "start_row": 345, "manual": "Conveyor_C2_Manual.pdf",
     "sample": "conveyor_c2_scratch.png", "last_service": "2026-08-29"},
    {"id": "press-p1", "name": "Press P1", "line": "Line 1 · Forming", "model": "HydroForm 250t",
     "scenario": "lube", "seed": 22, "start_row": 300, "manual": "Press_P1_Manual.pdf",
     "sample": "press_p1_crack.png", "last_service": "2026-09-17"},
    {"id": "cnc-m4", "name": "CNC M4", "line": "Line 3 · Machining", "model": "VMC-850",
     "scenario": "normal", "seed": 23, "start_row": 380, "manual": "CNC_M4_Manual.pdf",
     "sample": "cnc_m4_corrosion.png", "last_service": "2026-10-02"},
    {"id": "packaging-l3", "name": "Packaging Line L3", "line": "Line 4 · End of line", "model": "PackPro L3",
     "scenario": "rattle", "seed": 24, "start_row": 380, "manual": "Packaging_L3_Manual.pdf",
     "sample": "packaging_l3_missing_bolt.png", "last_service": "2026-07-30"},
]
ROWS = 480
_T0 = time.time()


def by_id(mid: str) -> dict | None:
    return next((m for m in MACHINES if m["id"] == mid), None)


def by_name(name: str) -> dict | None:
    n = name.lower()
    return next((m for m in MACHINES if m["name"].lower() == n or m["id"] == n), None)


@lru_cache(maxsize=16)
def _full_log(mid: str) -> pd.DataFrame:
    m = by_id(mid)
    return make_log(m["name"], m["scenario"], ROWS, seed=m["seed"])


def _now_row(m: dict) -> int:
    elapsed = (time.time() - _T0) / max(settings.sim_speed, 0.1)
    return int(min(ROWS, m["start_row"] + elapsed))


def window(mid: str, rows: int | None = None) -> pd.DataFrame:
    m = by_id(mid)
    end = _now_row(m)
    df = _full_log(mid).iloc[:end]
    return df.iloc[-rows:] if rows else df


def snapshot(mid: str) -> dict:
    m = by_id(mid)
    a = analyze(window(mid), max_points=120)
    latest = {c["name"]: c["last"] for c in a["channels"]}
    return {**{k: m[k] for k in ("id", "name", "line", "model", "manual", "sample", "last_service")},
            "status": a["status"], "health": a["health"], "findings": a["findings"][:3],
            "forecast": a["forecast"], "latest": latest, "series": a["series"], "channels": a["channels"]}


def fleet() -> list[dict]:
    return [snapshot(m["id"]) for m in MACHINES]


def log_csv(mid: str) -> bytes:
    return window(mid).to_csv(index=False).encode()


def reset_clock() -> None:
    global _T0
    _T0 = time.time()
