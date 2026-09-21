#!/usr/bin/env python3
"""Alertmanager webhook that triages each firing alert with the in-cluster model.

Reads the alert's namespace through a read-only ServiceAccount, asks llama-server for a fault
id, and returns the verdict. The prompt, labels and parsing come from eval/baseline.py.
"""
import asyncio
import json
import logging
import os

import httpx
from fastapi import FastAPI, Request
from kubernetes import client, config

from baseline import SYSTEM, parse, prompt_for

LLAMA = os.environ.get("LLAMA_URL", "http://llama-server:8080/v1/chat/completions")
VERDICT_SINK = os.environ.get("VERDICT_URL", "")
TIMEOUT = float(os.environ.get("TIMEOUT_S", "60"))


log = logging.getLogger("triage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
app = FastAPI()
http = httpx.AsyncClient(timeout=TIMEOUT)

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
        ctx = await asyncio.to_thread(context_bundle, namespace, workload)

        body = {
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt_for({"alert": {"labels": labels, "annotations": annotations},
                                                      "context": ctx})},
            ],
            "temperature": 0,
            "max_tokens": 48,
        }
        try:
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
                await http.post(VERDICT_SINK, json=verdict, timeout=10)
            except Exception as exc:
                log.warning("verdict sink unreachable: %s", exc)

    return {"verdicts": verdicts}
