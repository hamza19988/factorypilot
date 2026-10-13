"""Sensor-log analytics: anomaly detection, trend, threshold forecast and a health score.

Pure numpy/pandas, so the numbers the agent reasons over are computed, not guessed by the LLM.
Limits follow the equipment manuals (ISO 10816-3 zones for vibration velocity).
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Limit:
    label: str
    unit: str
    warn: float
    alarm: float
    trip: float | None = None
    low: bool = False  # True when *low* values are bad (e.g. line speed)


LIMITS: dict[str, Limit] = {
    "vibration_rms_mm_s": Limit("Vibration", "mm/s RMS", 2.8, 4.5, 7.1),
    "motor_temp_c": Limit("Motor temp", "°C", 60, 68, 80),
    "current_a": Limit("Motor current", "A", 20, 22.5),
    "reject_rate_pct": Limit("Reject rate", "%", 2.0, 4.0),
    "line_speed_m_min": Limit("Line speed", "m/min", 29.0, 28.0, low=True),
}


def load_csv(csv_bytes: bytes | None) -> pd.DataFrame | None:
    if not csv_bytes:
        return None
    df = pd.read_csv(io.BytesIO(csv_bytes))
    return df if len(df) else None


def _time_index(df: pd.DataFrame) -> pd.Series | None:
    for c in df.columns:
        if "time" in c.lower() or c.lower() in {"ts", "date", "datetime"}:
            ts = pd.to_datetime(df[c], errors="coerce")
            if ts.notna().mean() > 0.9:
                return ts
    return None


def _status(value: float, lim: Limit | None) -> str:
    if lim is None:
        return "info"
    if lim.low:
        return "alarm" if value <= lim.alarm else "warn" if value <= lim.warn else "ok"
    if lim.trip is not None and value >= lim.trip:
        return "trip"
    return "alarm" if value >= lim.alarm else "warn" if value >= lim.warn else "ok"


def _fmt_t(ts: pd.Series | None, i: int) -> str:
    return ts.iloc[i].strftime("%H:%M") if ts is not None and pd.notna(ts.iloc[i]) else f"row {i}"


def analyze(df: pd.DataFrame | None, max_points: int = 160) -> dict:
    """Return a structured analysis of a sensor log."""
    if df is None:
        return {"available": False, "summary": "No sensor logs provided.", "channels": [], "findings": [],
                "health": None, "forecast": None, "series": {}}

    ts = _time_index(df)
    minutes_per_row = 1.0
    if ts is not None and len(ts) > 1:
        minutes_per_row = max((ts.iloc[-1] - ts.iloc[0]).total_seconds() / 60 / (len(ts) - 1), 1e-6)
    num = df.select_dtypes("number")
    n = len(df)
    base_n = max(10, n // 4)
    recent_n = max(5, n // 10)
    channels, findings = [], []
    worst = "ok"
    rank = {"ok": 0, "info": 0, "warn": 1, "alarm": 2, "trip": 3}
    health = 100.0

    for col in num.columns:
        s = num[col].astype(float).reset_index(drop=True)
        lim = LIMITS.get(col)
        base = s.iloc[:base_n]
        mu, sd = float(base.mean()), float(base.std() or 1e-6)
        recent = float(s.iloc[-recent_n:].mean())
        shift_sigma = (recent - mu) / sd
        # spikes: robust z against a rolling median
        med = s.rolling(15, center=True, min_periods=3).median()
        mad = (s - med).abs().rolling(31, center=True, min_periods=5).median() * 1.4826 + 1e-6
        rz = ((s - med) / mad).abs()
        spike_idx = list(np.where(rz > 6)[0])
        # trend over the last third (units per hour)
        k = max(10, n // 3)
        y = s.iloc[-k:].to_numpy()
        xh = np.arange(k) * minutes_per_row / 60
        slope = float(np.polyfit(xh, y, 1)[0]) if k > 2 else 0.0
        status = _status(recent, lim)
        first_cross = None
        if lim is not None and not lim.low:
            above = np.where(s.rolling(5, min_periods=1).mean().to_numpy() >= lim.warn)[0]
            if len(above):
                first_cross = _fmt_t(ts, int(above[0]))
        ch = {
            "name": col, "label": lim.label if lim else col, "unit": lim.unit if lim else "",
            "baseline": round(mu, 3), "last": round(float(s.iloc[-1]), 3), "recent": round(recent, 3),
            "max": round(float(s.max()), 3), "shift_sigma": round(shift_sigma, 1),
            "slope_per_h": round(slope, 3), "spikes": len(spike_idx), "status": status,
            "limits": {"warn": lim.warn, "alarm": lim.alarm, "trip": lim.trip} if lim else None,
        }
        channels.append(ch)
        if rank[status] > rank[worst]:
            worst = status

        # findings in plain language
        if status in {"warn", "alarm", "trip"}:
            zone = {"warn": "warning", "alarm": "alarm", "trip": "trip"}[status]
            since = f" (first above {lim.warn} {lim.unit} at {first_cross})" if first_cross else ""
            findings.append({"severity": status, "channel": col,
                             "text": f"{ch['label']} at {recent:.2f} {ch['unit']} — {zone} zone vs baseline "
                                     f"{mu:.2f}{since}; trend {slope:+.2f} {ch['unit']}/h."})
        elif abs(shift_sigma) > 4 and lim is not None:
            findings.append({"severity": "info", "channel": col,
                             "text": f"{ch['label']} shifted {shift_sigma:+.1f}σ from baseline "
                                     f"({mu:.2f} → {recent:.2f} {ch['unit']})."})
        if len(spike_idx) >= 3:
            times = ", ".join(_fmt_t(ts, i) for i in spike_idx[-3:])
            findings.append({"severity": "warn" if col.startswith("vibration") else "info", "channel": col,
                             "text": f"{len(spike_idx)} impulsive spikes in {ch['label'].lower()} (latest at {times}) "
                                     f"— consistent with impacts or a loose component."})

        # health penalty
        if lim is not None:
            health -= {"ok": 0, "info": 0, "warn": 12, "alarm": 25, "trip": 40}[status]
            health -= min(10, len(spike_idx) * 1.5) if col.startswith("vibration") else 0
            health -= min(6, max(0, abs(shift_sigma) - 3))

    for f in findings:
        if rank.get(f["severity"], 0) > rank[worst]:
            worst = f["severity"]

    forecast = _forecast(df, ts, minutes_per_row)
    if forecast and forecast.get("hours_to_alarm") is not None and forecast["hours_to_alarm"] < 8:
        health -= 8

    series = _series(df, ts, max_points)
    summary_lines = [f"{n} samples, {n * minutes_per_row / 60:.1f} h window, overall status: {worst.upper()}"]
    summary_lines += [f"- {f['text']}" for f in findings] or ["- All channels inside the normal operating envelope."]
    if forecast and forecast.get("text"):
        summary_lines.append(f"- Forecast: {forecast['text']}")

    return {
        "available": True, "rows": n, "status": worst, "health": int(max(0, min(100, round(health)))),
        "channels": channels, "findings": findings, "forecast": forecast,
        "summary": "\n".join(summary_lines), "series": series,
    }


def _forecast(df: pd.DataFrame, ts, minutes_per_row: float) -> dict | None:
    col = "vibration_rms_mm_s"
    if col not in df.columns or len(df) < 40:
        return None
    lim = LIMITS[col]
    s = df[col].astype(float).rolling(9, center=True, min_periods=1).median().to_numpy()
    n = len(s)
    k = n // 2
    xh = np.arange(n - k, n) * minutes_per_row / 60
    coeff = np.polyfit(xh, s[-k:], 2)
    now_h = (n - 1) * minutes_per_row / 60
    current = float(np.polyval(coeff, now_h))
    out = {"channel": col, "model": "quadratic fit on last 50% (median-filtered)", "current": round(current, 2)}

    def hours_to(level: float) -> float | None:
        if current >= level:
            return 0.0
        horizon = np.linspace(now_h, now_h + 72, 2000)
        hit = np.where(np.polyval(coeff, horizon) >= level)[0]
        return round(float(horizon[hit[0]] - now_h), 1) if len(hit) else None

    out["hours_to_alarm"] = hours_to(lim.alarm)
    out["hours_to_trip"] = hours_to(lim.trip)
    if out["hours_to_alarm"] == 0 and out["hours_to_trip"] not in (None, 0):
        out["text"] = (f"vibration already in alarm zone; projected to reach trip level {lim.trip} mm/s "
                       f"in ~{out['hours_to_trip']} h — plan a stop this shift.")
    elif out["hours_to_alarm"] == 0:
        out["text"] = "vibration at or above trip level — stop the machine."
    elif out["hours_to_alarm"] is not None:
        out["text"] = f"vibration projected to cross alarm level {lim.alarm} mm/s in ~{out['hours_to_alarm']} h."
    else:
        out["text"] = "no threshold crossing projected in the next 72 h."
    return out


def _series(df: pd.DataFrame, ts, max_points: int) -> dict:
    step = max(1, len(df) // max_points)
    sub = df.iloc[::step]
    out = {"t": [x.strftime("%H:%M") for x in ts.iloc[::step]] if ts is not None else list(range(0, len(df), step))}
    for col in df.select_dtypes("number").columns:
        out[col] = [round(float(v), 3) for v in sub[col]]
    return out
