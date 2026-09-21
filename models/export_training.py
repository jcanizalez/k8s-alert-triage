#!/usr/bin/env python3
"""Export captured alerts as chat-format train.jsonl and eval.jsonl for fine-tuning.

The split comes from results/split-grouped.json and the prompt from eval/baseline.py.

Run: python3 models/export_training.py
"""
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from baseline import FAULTS, SYSTEM, prompt_for  # noqa: E402
from make_grouped_split import window  # noqa: E402

DATA = ROOT / "data" / "alerts.jsonl"
SPLIT = ROOT / "results" / "split-grouped.json"
OUT = ROOT / "models" / "data"


def chat(row: dict) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt_for(row)},
            {"role": "assistant", "content": row["fault"]},
        ]
    }


def spread(rows: list[dict]) -> str:
    counts = Counter(r["fault"] for r in rows)
    return "  ".join(f"{k} {v}" for k, v in sorted(counts.items()))


def main() -> int:
    if not SPLIT.exists():
        print("no results/split-grouped.json; run eval/make_grouped_split.py first", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    test_idx = set(json.loads(SPLIT.read_text())["test"])

    unknown = {r["fault"] for r in rows} - set(FAULTS)
    if unknown:
        print(f"labels not in the scorer's label space: {sorted(unknown)}", file=sys.stderr)
        return 1

    train = [r for i, r in enumerate(rows) if i not in test_idx]
    held = [r for i, r in enumerate(rows) if i in test_idx]

    assert len(train) + len(held) == len(rows)

    shared = {window(r) for r in train} & {window(r) for r in held}
    assert not shared, f"{len(shared)} injection windows appear in both train and eval: {sorted(shared)[:3]}"

    OUT.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", train), ("eval", held)):
        path = OUT / f"{name}.jsonl"
        path.write_text("".join(json.dumps(chat(r), ensure_ascii=False) + "\n" for r in part))
        print(f"{name}: {len(part)} rows -> {path.relative_to(ROOT)}")
        print(f"   {spread(part)}")

    missing = set(FAULTS) - {r["fault"] for r in train}
    if missing:
        print(f"warning: no training examples for {sorted(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
