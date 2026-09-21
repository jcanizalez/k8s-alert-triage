"""Run injection windows until every fault class reaches a record target.

Resumable: each pass recomputes the shortfall from data/alerts.jsonl, and the namespace is checked
for leftovers between windows.

Run: .venv/bin/python -u harness/collect.py --target 30 --max-hours 16
"""

import argparse
import collections
import json
import subprocess
import sys
import urllib.error
import urllib.request
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "alerts.jsonl"
PY = str(ROOT / ".venv" / "bin" / "python")

PROMETHEUS = "http://127.0.0.1:30090"

NEVER_INJECT = {"none", "slow_rollout", "scale_up_burst"}
NOISE_FAULTS = ["slow_rollout", "scale_up_burst"]


def counts() -> collections.Counter:
    if not DATA.exists():
        return collections.Counter()
    return collections.Counter(
        json.loads(line)["fault"] for line in DATA.read_text().splitlines() if line.strip()
    )


def catalogue_ids() -> list[str]:
    import yaml

    doc = yaml.safe_load((ROOT / "faults" / "catalogue.yaml").read_text())
    skip = {"oom_limit_too_low", "cpu_throttle", "netpol_blocks_dependency", "pvc_full"}
    return [f["id"] for f in doc["faults"] if f["id"] not in skip]


def kubectl(*args: str) -> str:
    out = subprocess.run(["kubectl", "--request-timeout=30s", *args],
                         capture_output=True, text=True)
    return (out.stdout or "").strip()


def residue(namespace: str, workload: str) -> list[str]:
    """What a failed revert leaves behind. Any of these poisons later windows."""
    found = []
    if kubectl("get", "quota", "-n", namespace, "-o", "name"):
        found.append("resourcequota")
    dep = ["get", "deployment", workload, "-n", namespace, "-o"]
    if kubectl(*dep, "jsonpath={.spec.template.spec.initContainers}"):
        found.append("initContainers")
    vols = kubectl(*dep, "jsonpath={.spec.template.spec.volumes[*].name}")
    for bad in ("ghost", "phantom"):
        if bad in vols:
            found.append(f"volume/{bad}")
    if kubectl(*dep, "jsonpath={.spec.template.spec.containers[0].livenessProbe}"):
        found.append("livenessProbe")
    if kubectl(*dep, "jsonpath={.spec.template.spec.containers[0].readinessProbe}"):
        found.append("readinessProbe")

    if kubectl("get", "service", "backend", "-n", namespace,
               "-o", "jsonpath={.spec.type}") == "ExternalName":
        found.append("service/backend=ExternalName")
    selector = kubectl("get", "service", workload, "-n", namespace,
                       "-o", "jsonpath={.spec.selector.app}")
    if selector and selector != workload:
        found.append(f"service/{workload} selector={selector}")
    return found


def cluster_ok() -> bool:
    """True when both the apiserver and Prometheus are ready."""
    if kubectl("get", "--raw", "/readyz") != "ok":
        return False
    try:
        with urllib.request.urlopen(PROMETHEUS + "/-/ready", timeout=10) as res:
            return res.status == 200
    except (OSError, urllib.error.URLError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=30)
    ap.add_argument("--max-hours", type=float, default=16.0)
    ap.add_argument("--namespace", default="demo")
    ap.add_argument("--workload", default="frontend")
    args = ap.parse_args()

    ids = [f for f in catalogue_ids() if f not in NEVER_INJECT]
    deadline = time.time() + args.max_hours * 3600

    capture = subprocess.Popen(
        [PY, "-u", str(ROOT / "harness" / "capture.py"), "--out", str(DATA)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"capture pid {capture.pid}", flush=True)
    time.sleep(8)
    if capture.poll() is not None:
        raise SystemExit("capture died immediately; not injecting into a blind cluster")

    windows = 0
    try:
        while time.time() < deadline:
            have = counts()
            short = {f: args.target - have.get(f, 0) for f in ids
                     if have.get(f, 0) < args.target}
            if have.get("none", 0) < args.target:
                short["none"] = args.target - have.get("none", 0)
            if not short:
                print("every class at target", flush=True)
                break

            if not cluster_ok():
                print("apiserver not ready; waiting 2m", flush=True)
                time.sleep(120)
                continue

            left = residue(args.namespace, args.workload)
            if left:
                print(f"ABORT: residue in {args.namespace}: {left}", file=sys.stderr, flush=True)
                return 2

            fault = max(short, key=lambda f: short[f])
            missing = short[fault]
            if fault == "none":
                fault = NOISE_FAULTS[windows % len(NOISE_FAULTS)]
            total = sum(short.values())
            print(f"[{time.strftime('%H:%M:%S')}] window {windows + 1}: {fault} "
                  f"(short {missing}, {total} records to go, "
                  f"{(deadline - time.time()) / 3600:.1f}h left)", flush=True)

            rc = subprocess.run(
                [PY, "-u", str(ROOT / "faults" / "inject.py"), "--only", fault,
                 "--namespace", args.namespace, "--workload", args.workload],
                cwd=ROOT, capture_output=True, text=True,
            )
            windows += 1
            if rc.returncode != 0:
                print(f"  inject failed: {rc.stderr.strip()[-300:]}", file=sys.stderr, flush=True)
                if capture.poll() is not None:
                    raise SystemExit("capture also died; stopping")
                time.sleep(30)

            if capture.poll() is not None:
                print("  capture died; restarting it", flush=True)
                capture = subprocess.Popen(
                    [PY, "-u", str(ROOT / "harness" / "capture.py"), "--out", str(DATA)],
                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(8)
    finally:
        capture.terminate()
        try:
            capture.wait(timeout=30)
        except subprocess.TimeoutExpired:
            capture.kill()

    have = counts()
    print(f"\nwindows run: {windows}", flush=True)
    for f in sorted(ids):
        print(f"  {f:28s} {have.get(f, 0)}/{args.target}", flush=True)
    print(f"  {'none':28s} {have.get('none', 0)} (not injected)", flush=True)
    print(f"total records: {sum(have.values())}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
