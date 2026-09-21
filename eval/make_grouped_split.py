"""Write a stratified test split that keeps every injection window on one side.

Run: .venv/bin/python eval/make_grouped_split.py
"""

import collections
import datetime as dt
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "alerts.jsonl"
OUT = ROOT / "results" / "split-grouped.json"
FRAC = 0.25
SEED = 7


def window(row: dict) -> tuple:
    """One injection: its alerts share a fault, a target and a start time within seconds."""
    seen = dt.datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
    start = seen - dt.timedelta(seconds=float(row.get("seconds_since_injection") or 0))
    return (row["fault"], str(row.get("fault_target")), start.strftime("%Y-%m-%dT%H:%M"))


def main() -> None:
    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    windows = collections.defaultdict(list)
    for i, row in enumerate(rows):
        windows[window(row)].append(i)

    by_class = collections.defaultdict(list)
    for key, idx in windows.items():
        by_class[key[0]].append((key, idx))

    rng = random.Random(SEED)
    test: list[int] = []
    chosen: list[tuple] = []
    for label in sorted(by_class):
        group = sorted(by_class[label], key=lambda g: g[0])
        rng.shuffle(group)
        want = max(1, round(sum(len(ix) for _, ix in group) * FRAC))
        taken = 0
        for key, idx in group:
            if taken and taken >= want:
                break
            if len(group) == 1:
                break
            test.extend(idx)
            chosen.append(key)
            taken += len(idx)
    test.sort()

    train = [i for i in range(len(rows)) if i not in set(test)]
    per_class = collections.Counter(rows[i]["fault"] for i in test)
    train_windows = {window(rows[i]) for i in train}
    test_windows = {window(rows[i]) for i in test}

    summary = {
        "seed": SEED,
        "frac": FRAC,
        "grouped_by": "injection window (fault, target, start minute)",
        "records": len(rows),
        "train_records": len(train),
        "test_records": len(test),
        "total_windows": len(windows),
        "test_windows": len(test_windows),
        "shared_windows": len(train_windows & test_windows),
        "perClass": dict(sorted(per_class.items())),
        "test": test,
    }
    OUT.write_text(json.dumps(summary, indent=2))
    print(f"windows {len(windows)} | train {len(train)} test {len(test)}")
    print(f"shared windows: {len(train_windows & test_windows)} (must be 0)")
    print("per class in test:", dict(sorted(per_class.items())))


if __name__ == "__main__":
    main()
