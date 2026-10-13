"""Deterministic sensor-log simulator used by the live fleet view, the demo and the tests.

Each scenario models a physically plausible failure signature that the manuals describe:
  bearing   - quadratic vibration growth, motor temp creep, impact spikes  (Conveyor C2 p.41)
  rattle    - isolated vibration spikes from a loose/missing fastener      (Packaging L3 p.24)
  lube      - forming-force increase: current and temperature drift up     (Press P1 p.23)
  normal    - healthy machine around its nominal envelope
"""
import numpy as np
import pandas as pd

SCENARIOS = ("normal", "bearing", "rattle", "lube")


def make_log(machine: str, scenario: str = "normal", rows: int = 480, seed: int = 7,
             start: str = "2026-10-13 06:00") -> pd.DataFrame:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=rows, freq="1min")
    x = np.linspace(0, 1, rows)
    zero = np.zeros(rows)

    vib_drift = 3.4 * x**2.2 if scenario == "bearing" else zero
    temp_drift = 11 * x**1.5 if scenario == "bearing" else (6 * x if scenario == "lube" else zero)
    cur_drift = 3.5 * x**1.6 if scenario == "bearing" else (4.2 * x if scenario == "lube" else zero)
    speed_drift = -1.6 * x**2 if scenario == "bearing" else zero
    reject_drift = 4.5 * x**3 if scenario == "bearing" else (1.2 * x**2 if scenario == "rattle" else zero)

    df = pd.DataFrame({
        "timestamp": t,
        "vibration_rms_mm_s": 1.9 + rng.normal(0, 0.12, rows) + vib_drift,
        "motor_temp_c": 54.5 + rng.normal(0, 0.5, rows) + temp_drift,
        "current_a": 17.8 + rng.normal(0, 0.35, rows) + cur_drift,
        "line_speed_m_min": 30 + rng.normal(0, 0.25, rows) + speed_drift,
        "reject_rate_pct": np.clip(0.7 + rng.normal(0, 0.15, rows) + reject_drift, 0, None),
    })
    if scenario == "bearing":  # rolling-element impacts late in the window
        idx = rng.choice(np.arange(int(rows * 0.75), rows), 6, replace=False)
        df.loc[idx, "vibration_rms_mm_s"] += rng.uniform(2.5, 4.5, len(idx))
    if scenario == "rattle":  # loose fastener: frequent spikes, no trend
        idx = rng.choice(np.arange(int(rows * 0.4), rows), 14, replace=False)
        df.loc[idx, "vibration_rms_mm_s"] += rng.uniform(1.8, 3.2, len(idx))
    df.insert(1, "machine", machine)
    num = df.select_dtypes("number").columns
    df[num] = df[num].round(3)
    return df
