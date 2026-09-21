# Running the harness

Tested on macOS with Colima. Any Docker-compatible runtime works.

## 1. Cluster and monitoring

```sh
colima start --cpu 4 --memory 6 --disk 60
kind create cluster --config harness/kind-cluster.yaml --wait 180s

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  -f harness/prometheus-values.yaml --wait --timeout 12m

kubectl apply -f harness/alert-rules.yaml
kubectl apply -f harness/demo-app.yaml
kubectl wait --for=condition=available deployment --all -n demo --timeout=180s
```

Prometheus is on http://localhost:30090, Alertmanager on http://localhost:30093, Grafana on http://localhost:30300 (admin / triage).

## 2. Generate the dataset

Once, from the repo root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then two terminals, both from the repo root.

```sh
# terminal 1: record every alert, labelled with the fault in flight
.venv/bin/python harness/capture.py --out data/alerts.jsonl

# terminal 2: break things on purpose, one fault at a time
.venv/bin/python faults/inject.py --namespace demo --workload frontend --rounds 3
```

One round walks the whole catalogue. Each fault takes about ten minutes including settle time, so a round is roughly an hour and a half and yields a few hundred labelled alerts.

Single fault, for development:

```sh
python3 faults/inject.py --only crashloop_bad_command
```

## 3. Check what you collected

```sh
wc -l data/alerts.jsonl
python3 -c "
import json, collections
rows = [json.loads(l) for l in open('data/alerts.jsonl')]
print(collections.Counter(r['fault'] for r in rows))
print(collections.Counter(r['alert']['labels'].get('alertname') for r in rows))
"
```

A healthy run has several alert names per fault and a `none` class from alerts that fired between windows.

## 3b. If a run dies mid-fault

A run killed in the middle of a window leaves the fault applied and the marker set, so every
alert that follows is labelled with a fault nobody is running.

```sh
./harness/recover.sh          # defaults to namespace demo, workload frontend
```

It clears the marker, restores the deployment's image, resources, command, probe and volumes,
puts the service selector back, removes any NetworkPolicy, and rebuilds the backend Service if
`dns_broken` left it as an ExternalName (rolling the callers, because nginx caches the old
ClusterIP). Then check what the interrupted window recorded:

```sh
tail -3 data/alerts.jsonl | python3 -m json.tool | grep -E '"fault"|alertname'
```

**Memory.** The VM takes 6 GB and a full collection runs for hours. Prefer several short runs
over one long one, since a killed run loses the current window.

## 4. Tear down

```sh
kind delete cluster --name triage
colima stop
```

## Notes

- The alert rules fire in one to two minutes on purpose. Production rules wait far longer; these are tuned for dataset throughput.
- `faults/current.json` is written by the injector and read by capture. Delete it if a run is interrupted, so stray alerts are not labelled with a fault that is no longer applied.
- Faults in the catalogue without an injector raise an error rather than silently recording nothing. Add the injector before running that fault.
