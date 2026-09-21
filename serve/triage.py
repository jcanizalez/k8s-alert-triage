#!/usr/bin/env python3
"""Alertmanager webhook that triages each firing alert with the in-cluster model.

Reads the alert's namespace through a read-only ServiceAccount, asks llama-server for a fault
id, and returns the verdict. SYSTEM, FAULTS and the prompt match eval/baseline.py.
"""
import json
import logging
import os
import re

import httpx
from fastapi import FastAPI, Request
from kubernetes import client, config

LLAMA = os.environ.get("LLAMA_URL", "http://llama-server:8080/v1/chat/completions")
VERDICT_SINK = os.environ.get("VERDICT_URL", "")
TIMEOUT = float(os.environ.get("TIMEOUT_S", "60"))

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

log = logging.getLogger("triage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
app = FastAPI()

config.load_incluster_config()
core = client.CoreV1Api()


def context_bundle(namespace: str, workload: str) -> dict:
    """What an on-call engineer opens first, read through the read-only ServiceAccount."""
    bundle: dict = {"namespace": namespace, "workload": workload, "pods": [], "services": [],
                    "events": []}
    if not namespace:
        return bundle
    try:
        for pod in core.list_namespaced_pod(namespace).items[:20]:
            statuses = pod.status.container_statuses or []
            bundle["pods"].append({
                "name": pod.metadata.name,
                "phase": pod.status.phase,
                "restarts": sum(c.restart_count for c in statuses),
                "ready": all(c.ready for c in statuses) if statuses else False,
                "waiting": [c.state.waiting.reason for c in statuses
                            if c.state and c.state.waiting and c.state.waiting.reason],
                "last_terminated": [c.last_state.terminated.reason for c in statuses
                                    if c.last_state and c.last_state.terminated
                                    and c.last_state.terminated.reason],
            })
    except Exception as exc:
        log.warning("pods unavailable: %s", exc)

    try:
        for svc in core.list_namespaced_service(namespace).items[:10]:
            bundle["services"].append({
                "name": svc.metadata.name,
                "type": svc.spec.type,
                "selector": svc.spec.selector or {},
                "externalName": svc.spec.external_name,
            })
    except Exception as exc:
        log.warning("services unavailable: %s", exc)

    try:
        events = core.list_namespaced_event(namespace).items
        for event in sorted(events, key=lambda e: e.last_timestamp or e.event_time or 0)[-25:]:
            bundle["events"].append({
                "type": event.type, "reason": event.reason,
                "object": event.involved_object.name,
                "message": (event.message or "")[:300], "count": event.count,
            })
    except Exception as exc:
        log.warning("events unavailable: %s", exc)
    return bundle


def prompt_for(labels: dict, annotations: dict, ctx: dict) -> str:
    lines = [
        f"ALERT: {labels.get('alertname')}",
        f"severity: {labels.get('severity', 'unknown')}",
        f"namespace: {ctx.get('namespace') or labels.get('namespace', '')}",
        f"object: {labels.get('pod') or labels.get('service') or labels.get('deployment') or ''}",
        f"summary: {annotations.get('summary', '')}",
        f"description: {annotations.get('description', '')}",
        "",
        "PODS:",
    ]
    for p in ctx.get("pods", [])[:8]:
        lines.append(f"  {p['name']}: phase={p.get('phase')} ready={p.get('ready')} "
                     f"restarts={p.get('restarts')} waiting={p.get('waiting') or []} "
                     f"lastTerminated={p.get('last_terminated') or []}")
    if ctx.get("services"):
        lines += ["", "SERVICES:"]
        for s in ctx["services"][:8]:
            external = f" externalName={s['externalName']}" if s.get("externalName") else ""
            lines.append(f"  {s['name']}: type={s.get('type')} "
                         f"selector={s.get('selector') or {}}{external}")
    lines += ["", "RECENT EVENTS:"]
    for e in ctx.get("events", [])[-10:]:
        lines.append(f"  {e.get('type')} {e.get('reason')} {e.get('object')}: "
                     f"{(e.get('message') or '')[:160]}")
    return "\n".join(lines)


def parse(text: str) -> str:
    low = (text or "").lower()
    hits = [f for f in FAULTS if re.search(rf"\b{re.escape(f)}\b", low)]
    return max(hits, key=lambda f: low.rfind(f)) if hits else "unparseable"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/alert")
async def alert(request: Request) -> dict:
    payload = await request.json()
    verdicts = []
    for item in payload.get("alerts", []):
        if item.get("status") != "firing":
            continue
        labels, annotations = item.get("labels", {}), item.get("annotations", {})
        namespace = labels.get("namespace", "")
        workload = (labels.get("deployment") or labels.get("pod")
                    or labels.get("service") or "")
        ctx = context_bundle(namespace, workload)

        body = {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt_for(labels, annotations, ctx)},
            ],
            "temperature": 0,
            "max_tokens": 48,
        }
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as http:
                answer = await http.post(LLAMA, json=body)
                said = answer.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.error("model unreachable: %s", exc)
            said = ""

        fault = parse(said)
        verdict = {
            "alert": labels.get("alertname"),
            "namespace": namespace,
            "object": workload,
            "fault": fault,
            "escalate": fault == "unparseable",
        }
        verdicts.append(verdict)
        log.info("verdict %s", json.dumps(verdict))

        if VERDICT_SINK:
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(VERDICT_SINK, json=verdict)
            except Exception as exc:
                log.warning("verdict sink unreachable: %s", exc)

    return {"verdicts": verdicts}
