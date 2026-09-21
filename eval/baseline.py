"""Score a model on alert triage over the generated dataset, through OpenRouter.

One alert plus its context goes in, one fault id comes out. The injected fault is the truth.
Writes a summary and the per-record predictions, so any score can be recomputed without a rerun.

Run: .venv/bin/python eval/baseline.py --model anthropic/claude-opus-5
"""

import argparse
import collections
import http.client
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"

DATA = ROOT / "data" / "alerts.jsonl"
SPLIT = ROOT / "results" / "split-grouped.json"
OUT = ROOT / "results" / "headtohead"

FAULTS = [
    "bad_image_tag",
    "crashloop_bad_command",
    "dependency_scaled_to_zero",
    "dns_broken",
    "init_container_failing",
    "liveness_probe_failing",
    "missing_configmap",
    "missing_secret",
    "readiness_probe_too_strict",
    "resource_quota_exceeded",
    "unschedulable_resources",
    "wrong_service_selector",
    "none",
]

SYSTEM = f"""You triage Kubernetes alerts. Given one alert and the state of its namespace, name
the single fault that caused it.

Answer with exactly one of these ids and nothing else:
{chr(10).join('- ' + f for f in FAULTS)}

Use `none` when the alert is routine noise rather than the result of one of those faults."""


def prompt_for(row: dict) -> str:
    """What an on-call engineer would see: the alert, then the namespace around it."""
    a = row["alert"]
    ctx = row["context"]
    lines = [
        f"ALERT: {a['labels'].get('alertname')}",
        f"severity: {a['labels'].get('severity', 'unknown')}",
        f"namespace: {ctx.get('namespace') or a['labels'].get('namespace', '')}",
        f"object: {a['labels'].get('pod') or a['labels'].get('service') or a['labels'].get('deployment') or ''}",
        f"summary: {a.get('annotations', {}).get('summary', '')}",
        f"description: {a.get('annotations', {}).get('description', '')}",
        "",
        "PODS:",
    ]
    for p in ctx.get("pods", [])[:8]:
        lines.append(
            f"  {p['name']}: phase={p.get('phase')} ready={p.get('ready')} "
            f"restarts={p.get('restarts')} waiting={p.get('waiting') or []} "
            f"lastTerminated={p.get('last_terminated') or []}"
        )
    services = ctx.get("services") or []
    if services:
        lines.append("")
        lines.append("SERVICES:")
        for s in services[:8]:
            external = f" externalName={s['externalName']}" if s.get("externalName") else ""
            lines.append(
                f"  {s['name']}: type={s.get('type')} selector={s.get('selector') or {}}{external}"
            )

    lines.append("")
    lines.append("RECENT EVENTS:")
    for e in ctx.get("events", [])[-10:]:
        lines.append(f"  {e.get('type')} {e.get('reason')} {e.get('object')}: {(e.get('message') or '')[:160]}")
    return "\n".join(lines)


def call(model: str, key: str, user: str, endpoint: str = OPENROUTER, retries: int = 3
         ) -> tuple[str, dict, float]:
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 2000,
        }
    ).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(endpoint, data=body, headers=headers)
    for attempt in range(retries):
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                payload = json.loads(res.read())
            elapsed = time.time() - started
            choice = (payload.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or "").strip()
            if not text:
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                reason = choice.get("finish_reason") or "unknown"
                return f"EMPTY (finish_reason={reason})", payload.get("usage", {}), elapsed
            return text, payload.get("usage", {}), elapsed
        except (OSError, http.client.HTTPException, KeyError, json.JSONDecodeError) as err:
            if attempt == retries - 1:
                return f"ERROR: {err}", {}, time.time() - started
            time.sleep(2 * (attempt + 1))
    return "ERROR", {}, 0.0


def parse(text: str) -> str:
    """Return the fault id the model names last, or "unparseable" when it names none."""
    low = text.lower()
    hits = [f for f in FAULTS if re.search(rf"\b{re.escape(f)}\b", low)]
    if not hits:
        return "unparseable"
    return max(hits, key=lambda f: low.rfind(f))


def macro_f1(pairs: list[tuple[str, str]]) -> float:
    labels = {g for g, _ in pairs}
    total = 0.0
    for label in labels:
        tp = sum(1 for g, p in pairs if g == label and p == label)
        fp = sum(1 for g, p in pairs if g != label and p == label)
        fn = sum(1 for g, p in pairs if g == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        total += 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return total / len(labels) if labels else 0.0


def load_split(rows: list[dict], frac: float, seed: int, split_path: Path = SPLIT) -> list[int]:
    """Load the frozen test split, or write a stratified one on first run."""
    if split_path.exists():
        return json.loads(split_path.read_text())["test"]
    rng = random.Random(seed)
    by_class: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        by_class.setdefault(row["fault"], []).append(i)

    test: list[int] = []
    for label, idx in sorted(by_class.items()):
        rng.shuffle(idx)
        take = max(min(2, len(idx)), int(len(idx) * frac + 0.5))
        test.extend(idx[:take])
    test.sort()

    per_class = {label: sum(1 for i in test if rows[i]["fault"] == label) for label in sorted(by_class)}
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text(
        json.dumps(
            {"seed": seed, "frac": frac, "stratified": True, "perClass": per_class, "test": test},
            indent=2,
        )
    )
    return test


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="anthropic/claude-opus-5")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N of the split")
    ap.add_argument("--frac", type=float, default=1.0, help="share of the dataset in the test split")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--split", default=str(SPLIT), help="path to the frozen test split")
    ap.add_argument("--endpoint", default=OPENROUTER,
                    help="OpenAI-compatible chat-completions URL; a local llama-server needs no key")
    args = ap.parse_args()

    local = "openrouter.ai" not in args.endpoint
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key and not local:
        raise SystemExit("OPENROUTER_API_KEY not set (source the repo's .env)")

    rows = [json.loads(line) for line in DATA.read_text().splitlines() if line.strip()]
    test = load_split(rows, args.frac, args.seed, Path(args.split))
    if args.limit:
        test = test[: args.limit]
    print(f"{args.model}: scoring {len(test)} of {len(rows)} records")

    records, pairs = [], []
    tokens_in = tokens_out = 0
    latencies = []

    for n, i in enumerate(test, 1):
        row = rows[i]
        gold = row["fault"]
        text, usage, elapsed = call(args.model, key, prompt_for(row), args.endpoint)
        if text.startswith("ERROR"):
            OUT.mkdir(parents=True, exist_ok=True)
            partial = OUT / f"{args.model.replace('/', '_')}-partial.json"
            partial.write_text(json.dumps(records, indent=2))
            raise SystemExit(
                f"\ntransport failure at record {n}/{len(test)}: {text}\n"
                f"{len(records)} answered records kept in {partial}; no score written"
            )
        pred = parse(text)
        pairs.append((gold, pred))
        latencies.append(elapsed)
        tokens_in += usage.get("prompt_tokens", 0)
        tokens_out += usage.get("completion_tokens", 0)
        records.append(
            {
                "index": i,
                "alertname": row["alert"]["labels"].get("alertname"),
                "gold": gold,
                "pred": pred,
                "correct": gold == pred,
                "seconds": round(elapsed, 2),
                "raw": text[:200],
            }
        )
        mark = "ok " if gold == pred else "MISS"
        print(f"  [{n}/{len(test)}] {mark} gold={gold:26s} pred={pred:26s} {elapsed:.1f}s")

    correct = sum(1 for g, p in pairs if g == p)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
    per_class = {}
    for label in sorted({g for g, _ in pairs}):
        got = [(g, p) for g, p in pairs if g == label]
        per_class[label] = {
            "n": len(got),
            "correct": sum(1 for g, p in got if g == p),
        }

    summary = {
        "model": args.model,
        "scored_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records_scored": len(test),
        "dataset_records": len(rows),
        "accuracy": round(correct / len(test), 4) if test else 0.0,
        "macro_f1": round(macro_f1(pairs), 4),
        "unparseable": sum(1 for _, p in pairs if p == "unparseable"),
        "latency_mean_s": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "latency_p95_s": round(p95, 2),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "per_class": per_class,
        "confusion_top": collections.Counter(
            f"{g}->{p}" for g, p in pairs if g != p
        ).most_common(8),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    stem = args.model.replace("/", "_")
    (OUT / f"{stem}.json").write_text(json.dumps(summary, indent=2))
    (OUT / f"{stem}-records.json").write_text(json.dumps(records, indent=2))

    print(
        f"\naccuracy {summary['accuracy']:.1%} | macro F1 {summary['macro_f1']:.3f} "
        f"| p95 {summary['latency_p95_s']}s | tokens {tokens_in}/{tokens_out}"
    )
    print(f"wrote {OUT}/{stem}.json")


if __name__ == "__main__":
    main()
