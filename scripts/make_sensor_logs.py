"""Write synthetic sensor logs (normal + failing) to CSV for uploads / demos.

Usage: python scripts/make_sensor_logs.py --out data/logs
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.simulate import make_log  # noqa: E402

FAILING = {"Conveyor C2": "bearing", "Press P1": "lube", "CNC M4": "bearing", "Packaging Line L3": "rattle"}


def make(machine: str, failing: bool, rows: int, rng=None):  # kept for backwards compatibility
    return make_log(machine, FAILING[machine] if failing else "normal", rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/logs")
    ap.add_argument("--rows", type=int, default=480)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, (machine, scenario) in enumerate(FAILING.items()):
        slug = machine.lower().replace(" ", "_")
        for name, sc in (("normal", "normal"), ("failing", scenario)):
            p = out / f"{slug}_{name}.csv"
            make_log(machine, sc, a.rows, seed=7 + i).to_csv(p, index=False)
            print("wrote", p)


if __name__ == "__main__":
    main()
