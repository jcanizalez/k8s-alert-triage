"""Capture every alert Alertmanager holds, with its namespace context, labelled with the fault in flight.

Run: python3 harness/capture.py --out data/alerts.jsonl
"""

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CURRENT = Path("faults/current.json")


def kubectl(*args: str) -> str:
    """Read-only kubectl call. Returns an empty string on failure so capture never dies."""
    try:
        return subprocess.run(
            ["kubectl", *args], capture_output=True, text=True, timeout=20
        ).stdout
    except Exception:
        return ""


def current_fault() -> dict:
    try:
        return json.loads(CURRENT.read_text())
    except Exception:
        return {"fault": None, "category": None, "target": None, "started_at": None}


def fetch_alerts(base: str) -> list[dict]:
    try:
        with urllib.request.urlopen(f"{base}/api/v2/alerts?active=true", timeout=10) as res:
            return json.loads(res.read())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return []


def context_bundle(namespace: str, workload: str) -> dict:
    """What an on-call engineer opens first: pod health and recent events."""
    bundle: dict = {"namespace": namespace, "workload": workload, "pods": [], "events": []}
    if not namespace:
        return bundle

    try:
        for item in json.loads(kubectl("get", "pods", "-n", namespace, "-o", "json") or "{}").get(
            "items", []
        )[:20]:
            statuses = item.get("status", {}).get("containerStatuses") or []
            bundle["pods"].append(
                {
                    "name": item["metadata"]["name"],
                    "phase": item.get("status", {}).get("phase"),
                    "restarts": sum(c.get("restartCount", 0) for c in statuses),
                    "ready": all(c.get("ready") for c in statuses) if statuses else False,
                    "waiting": [
                        (c.get("state", {}).get("waiting") or {}).get("reason")
                        for c in statuses
                        if (c.get("state", {}).get("waiting") or {}).get("reason")
                    ],
                    "last_terminated": [
                        (c.get("lastState", {}).get("terminated") or {}).get("reason")
                        for c in statuses
                        if (c.get("lastState", {}).get("terminated") or {}).get("reason")
                    ],
                }
            )
    except Exception:
        pass

    bundle["services"] = []
    try:
        for item in json.loads(kubectl("get", "services", "-n", namespace, "-o", "json") or "{}").get(
            "items", []
        )[:10]:
            spec = item.get("spec", {})
            bundle["services"].append(
                {
                    "name": item["metadata"]["name"],
                    "type": spec.get("type"),
                    "selector": spec.get("selector") or {},
                    "externalName": spec.get("externalName"),
                }
            )
    except Exception:
        pass

    try:
        for item in json.loads(
            kubectl("get", "events", "-n", namespace, "--sort-by=.lastTimestamp", "-o", "json")
            or "{}"
        ).get("items", [])[-25:]:
            bundle["events"].append(
                {
                    "type": item.get("type"),
                    "reason": item.get("reason"),
                    "object": item.get("involvedObject", {}).get("name"),
                    "message": (item.get("message") or "")[:300],
                    "count": item.get("count"),
                }
            )
    except Exception:
        pass

    return bundle


IGNORED_ALERTS = {
    "Watchdog",
    "InfoInhibitor",
    "KubeAPIDown",
}


def fingerprint(alert: dict) -> str:
    """Alertmanager's own fingerprint, or the labels when an older version omits it."""
    return alert.get("fingerprint") or json.dumps(alert.get("labels", {}), sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alertmanager", default="http://127.0.0.1:30093")
    parser.add_argument("--out", default="data/alerts.jsonl")
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--namespace", default="demo",
                        help="only record alerts from this namespace")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    seen: set[tuple[str, str]] = set()
    print(f"polling {args.alertmanager} every {args.interval}s, writing {out}")

    quiet_after_revert = 150

    while True:
        label = current_fault()
        window = label.get("fault") or "none"
        cleared = label.get("cleared_at")
        if not label.get("fault") and cleared and time.time() - cleared < quiet_after_revert:
            time.sleep(args.interval)
            continue
        rows = []
        skipped = 0

        for alert in fetch_alerts(args.alertmanager):
            labels = alert.get("labels", {})
            if labels.get("alertname") in IGNORED_ALERTS:
                continue

            key = (fingerprint(alert), f"{window}:{label.get('started_at') or ''}")
            if key in seen:
                continue
            seen.add(key)

            namespace = labels.get("namespace") or ""
            if namespace != args.namespace:
                skipped += 1
                continue
            workload = (
                labels.get("deployment")
                or labels.get("statefulset")
                or labels.get("pod")
                or labels.get("service")
                or ""
            )
            started = label.get("started_at")
            rows.append(
                {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "alert": {
                        "status": (alert.get("status") or {}).get("state"),
                        "labels": labels,
                        "annotations": alert.get("annotations", {}),
                        "startsAt": alert.get("startsAt"),
                    },
                    "context": context_bundle(namespace, workload),
                    "fault": window,
                    "category": label.get("category") or "none",
                    "fault_target": label.get("target"),
                    "seconds_since_injection": (
                        round(time.time() - started, 1) if started else None
                    ),
                }
            )

        if rows:
            with out.open("a") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            print(f"{len(rows)} new alert(s) under fault={window}", flush=True)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
