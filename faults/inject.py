"""Break the cluster on purpose, one fault at a time, and label what comes out.

For each fault: wait for green, tell the capture service what is starting, apply the
fault, watch alerts fire, revert, wait for green again. The fault id becomes the
ground-truth label for every alert captured inside its window.

Run: python3 faults/inject.py --namespace demo --catalogue faults/catalogue.yaml
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml

CURRENT = Path("faults/current.json")


def kubectl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run kubectl; with check, a failed call stops the run."""
    result = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"kubectl {' '.join(args[:3])} failed: {result.stderr.strip()[:200]}")
    return result


def announce(fault: dict | None, noise: bool = False) -> None:
    """Record which fault is in flight for the capture service, or that none is."""
    CURRENT.parent.mkdir(parents=True, exist_ok=True)
    CURRENT.write_text(
        json.dumps(
            {
                "fault": None if noise or not fault else fault["id"],
                "category": fault["category"] if fault else None,
                "target": fault.get("target") if fault else None,
                "started_at": time.time() if fault else None,
                "cleared_at": None if fault else time.time(),
            }
        )
    )


def all_ready(namespace: str) -> bool:
    out = kubectl("get", "pods", "-n", namespace, "-o", "json").stdout
    try:
        items = json.loads(out or "{}").get("items", [])
    except json.JSONDecodeError:
        return False
    if not items:
        return False
    for pod in items:
        statuses = pod.get("status", {}).get("containerStatuses") or []
        if not statuses or not all(c.get("ready") for c in statuses):
            return False
    return True


def wait_green(namespace: str, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if all_ready(namespace):
            return True
        time.sleep(5)
    return False


def apply_fault(fault: dict, namespace: str, workload: str) -> list[list[str]]:
    fid = fault["id"]
    dep = ["deployment", workload, "-n", namespace]

    if fid == "oom_limit_too_low":
        kubectl(
            "set", "resources", *dep, "--requests=memory=8Mi", "--limits=memory=16Mi",
            check=True,
        )
        return [["set", "resources", *dep, "--requests=memory=64Mi", "--limits=memory=512Mi"]]

    if fid == "cpu_throttle":
        kubectl("set", "resources", *dep, "--requests=cpu=5m", "--limits=cpu=10m", check=True)
        return [["set", "resources", *dep, "--requests=cpu=50m", "--limits=cpu=500m"]]

    if fid == "bad_image_tag":
        current = kubectl(
            "get", *dep, "-o", "jsonpath={.spec.template.spec.containers[0].image}"
        ).stdout.strip()
        name = kubectl(
            "get", *dep, "-o", "jsonpath={.spec.template.spec.containers[0].name}"
        ).stdout.strip()
        kubectl("set", "image", *dep, f"{name}=busybox:this-tag-does-not-exist")
        return [["set", "image", *dep, f"{name}={current}"]]

    if fid == "crashloop_bad_command":
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/containers/0/command",'
                  '"value":["/bin/sh","-c","exit 1"]}]',
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]',
        ]]

    if fid == "wrong_service_selector":
        kubectl(
            "patch", "service", workload, "-n", namespace, "--type=merge",
            "-p", '{"spec":{"selector":{"app":"nothing-matches-this"}}}',
        )
        return [[
            "patch", "service", workload, "-n", namespace, "--type=merge",
            "-p", '{"spec":{"selector":{"app":"' + workload + '"}}}',
        ]]

    if fid == "dependency_scaled_to_zero":
        kubectl("scale", *dep, "--replicas=0")
        return [["scale", *dep, "--replicas=1"]]

    if fid == "unschedulable_resources":
        kubectl(
            "set", "resources", *dep,
            "--requests=cpu=64,memory=64Gi", "--limits=cpu=64,memory=64Gi",
            check=True,
        )
        return [[
            "set", "resources", *dep,
            "--requests=cpu=50m,memory=64Mi", "--limits=cpu=500m,memory=512Mi",
        ]]

    if fid == "missing_configmap":
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/volumes/-",'
                  '"value":{"name":"ghost","configMap":{"name":"does-not-exist"}}},'
                  '{"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-",'
                  '"value":{"name":"ghost","mountPath":"/etc/ghost"}}]',
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts/1"},'
                  '{"op":"remove","path":"/spec/template/spec/volumes/1"}]',
        ]]

    if fid == "netpol_blocks_dependency":
        policy = (
            "apiVersion: networking.k8s.io/v1\n"
            "kind: NetworkPolicy\n"
            "metadata:\n"
            f"  name: block-backend\n  namespace: {namespace}\n"
            "spec:\n"
            "  podSelector:\n    matchLabels:\n      app: frontend\n"
            "  policyTypes: [Egress]\n"
            "  egress:\n"
            "    - to:\n        - namespaceSelector:\n            matchLabels:\n"
            "              kubernetes.io/metadata.name: kube-system\n"
            "      ports:\n        - protocol: UDP\n          port: 53\n"
        )
        subprocess.run(["kubectl", "apply", "-f", "-"], input=policy, text=True,
                       capture_output=True)
        return [["delete", "networkpolicy", "block-backend", "-n", namespace, "--ignore-not-found"]]

    if fid == "dns_broken":
        current_type = kubectl(
            "get", "service", "backend", "-n", namespace, "-o", "jsonpath={.spec.type}"
        ).stdout.strip()
        ports = kubectl(
            "get", "service", "backend", "-n", namespace, "-o", "jsonpath={.spec.ports[0].port}"
        ).stdout.strip()
        kubectl("delete", "service", "backend", "-n", namespace, "--ignore-not-found")
        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=(
                "apiVersion: v1\nkind: Service\nmetadata:\n"
                f"  name: backend\n  namespace: {namespace}\n"
                "spec:\n  type: ExternalName\n  externalName: backend.invalid.example\n"
            ),
            text=True, capture_output=True,
        )
        return [
            ["delete", "service", "backend", "-n", namespace, "--ignore-not-found"],
            ["expose", "deployment", "backend", "-n", namespace, "--name=backend",
             f"--port={ports or 8080}", "--target-port=8080",
             f"--type={current_type or 'ClusterIP'}"],
            ["rollout", "restart", "deployment", workload, "-n", namespace],
            ["rollout", "status", "deployment", workload, "-n", namespace, "--timeout=120s"],
        ]

    if fid == "slow_rollout":
        kubectl("patch", *dep, "--type=merge", "-p", '{"spec":{"minReadySeconds":200}}',
                check=True)
        kubectl("rollout", "restart", *dep, check=True)
        return [["patch", *dep, "--type=merge", "-p", '{"spec":{"minReadySeconds":0}}']]

    if fid == "scale_up_burst":
        kubectl("patch", *dep, "--type=merge", "-p", '{"spec":{"minReadySeconds":200}}',
                check=True)
        kubectl("scale", *dep, "--replicas=4", check=True)
        return [
            ["patch", *dep, "--type=merge", "-p", '{"spec":{"minReadySeconds":0}}'],
            ["scale", *dep, "--replicas=1"],
        ]

    if fid == "readiness_probe_too_strict":
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/containers/0/readinessProbe",'
                  '"value":{"httpGet":{"path":"/healthz","port":9999},'
                  '"initialDelaySeconds":3,"periodSeconds":5}}]',
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/containers/0/readinessProbe"}]',
        ]]

    if fid == "liveness_probe_failing":
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/containers/0/livenessProbe",'
                  '"value":{"httpGet":{"path":"/healthz","port":9999},'
                  '"initialDelaySeconds":3,"periodSeconds":5,"failureThreshold":2}}]',
            check=True,
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/containers/0/livenessProbe"}]',
        ]]

    if fid == "missing_secret":
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/volumes/-",'
                  '"value":{"name":"phantom","secret":{"secretName":"does-not-exist"}}},'
                  '{"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-",'
                  '"value":{"name":"phantom","mountPath":"/etc/phantom"}}]',
            check=True,
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts/1"},'
                  '{"op":"remove","path":"/spec/template/spec/volumes/1"}]',
        ]]

    if fid == "resource_quota_exceeded":
        got = kubectl("get", *dep, "-o", "jsonpath={.spec.replicas}")
        was = (got.stdout or "1").strip() or "1"
        pods = kubectl("get", "pods", "-n", namespace, "--no-headers")
        current = len([l for l in (pods.stdout or "").splitlines() if l.strip()]) or 3
        kubectl("delete", "quota", "triage-cap", "-n", namespace, "--ignore-not-found")
        kubectl("create", "quota", "triage-cap", "-n", namespace, f"--hard=pods={current}",
                check=True)
        kubectl("scale", *dep, f"--replicas={int(was) + 2}", check=True)
        return [
            ["delete", "quota", "triage-cap", "-n", namespace, "--ignore-not-found"],
            ["scale", *dep, f"--replicas={was}"],
        ]

    if fid == "init_container_failing":
        img = kubectl("get", *dep, "-o",
                      "jsonpath={.spec.template.spec.containers[0].image}")
        image = (img.stdout or "nginx:1.27-alpine").strip() or "nginx:1.27-alpine"
        kubectl(
            "patch", *dep, "--type=json",
            "-p", '[{"op":"add","path":"/spec/template/spec/initContainers","value":'
                  '[{"name":"preflight","image":"' + image + '",'
                  '"command":["sh","-c","echo preflight failed >&2; exit 1"]}]}]',
            check=True,
        )
        return [[
            "patch", *dep, "--type=json",
            "-p", '[{"op":"remove","path":"/spec/template/spec/initContainers"}]',
        ]]

    raise SystemExit(f"no injector for fault {fid}; add one before running it")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="demo")
    parser.add_argument("--workload", default="frontend")
    parser.add_argument("--catalogue", default="faults/catalogue.yaml")
    parser.add_argument("--only", help="run a single fault id")
    parser.add_argument("--rounds", type=int, default=1)
    args = parser.parse_args()

    spec = yaml.safe_load(Path(args.catalogue).read_text())
    window = spec.get("window", {})
    settle = int(str(window.get("settle_before", "120s")).rstrip("s"))
    observe = int(str(window.get("observe_after_injection", "300s")).rstrip("s"))
    recover = int(str(window.get("revert_and_settle", "180s")).rstrip("s"))

    faults = [f for f in spec["faults"] if not args.only or f["id"] == args.only]
    if not faults:
        raise SystemExit(f"no fault matching {args.only}")

    if not args.only:
        unviable = {
            "requires_cni_policy": "needs a CNI that enforces NetworkPolicy",
            "requires_cpu_load": "needs a workload that actually burns CPU",
            "requires_volume": "needs a workload with a volume claim to fill",
            "requires_memory_growth": "needs a workload that allocates until it is killed",
        }
        keep = []
        for fault in faults:
            reason = next((why for flag, why in unviable.items() if fault.get(flag)), None)
            if reason:
                print(f"skipping {fault['id']}: {reason}", flush=True)
            else:
                keep.append(fault)
        faults = keep

    for round_no in range(1, args.rounds + 1):
        for fault in faults:
            print(f"\n[round {round_no}] {fault['id']} ({fault['category']})", flush=True)
            announce(None)
            if not wait_green(args.namespace, settle):
                print("  cluster not green before injection, skipping")
                continue

            announce(fault, noise=bool(fault.get("noise")))
            try:
                undo = apply_fault(fault, args.namespace, args.workload)
            except SystemExit as err:
                print(f"  {err}")
                announce(None)
                continue

            print(f"  injected, watching for {observe}s", flush=True)
            time.sleep(observe)

            announce(None)
            for command in undo:
                kubectl(*command)
            print(f"  reverted, settling for up to {recover}s", flush=True)
            wait_green(args.namespace, recover)


if __name__ == "__main__":
    main()
