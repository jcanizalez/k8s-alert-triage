#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

ns="${1:-demo}"
workload="${2:-frontend}"

marker=faults/current.json
if [ -s "$marker" ]; then
  echo "marker said: $(cat "$marker")"
fi
printf '{"fault": null, "category": null, "target": null, "started_at": null}' > "$marker"
echo "marker cleared"

echo "restoring $ns/$workload to its baseline"
kubectl set resources deployment "$workload" -n "$ns" \
  --requests=cpu=50m,memory=64Mi --limits=cpu=500m,memory=512Mi >/dev/null 2>&1 || true
kubectl set image deployment "$workload" -n "$ns" "$workload=nginx:1.27-alpine" >/dev/null 2>&1 || true
kubectl scale deployment backend -n "$ns" --replicas=1 >/dev/null 2>&1 || true

for patch in \
  '[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]' \
  '[{"op":"remove","path":"/spec/template/spec/containers/0/readinessProbe"}]' \
  '[{"op":"remove","path":"/spec/template/spec/containers/0/livenessProbe"}]' \
  '[{"op":"remove","path":"/spec/template/spec/initContainers"}]' \
  '[{"op":"remove","path":"/spec/template/spec/containers/0/volumeMounts/1"},{"op":"remove","path":"/spec/template/spec/volumes/1"}]'
do
  kubectl patch deployment "$workload" -n "$ns" --type=json -p "$patch" >/dev/null 2>&1 || true
done

kubectl delete resourcequota triage-cap -n "$ns" --ignore-not-found >/dev/null 2>&1 || true
kubectl scale deployment "$workload" -n "$ns" --replicas=1 >/dev/null 2>&1 || true

kubectl patch service "$workload" -n "$ns" --type=merge \
  -p "{\"spec\":{\"selector\":{\"app\":\"$workload\"}}}" >/dev/null 2>&1 || true
kubectl delete networkpolicy block-backend -n "$ns" --ignore-not-found >/dev/null 2>&1 || true

if [ "$(kubectl get service backend -n "$ns" -o jsonpath='{.spec.type}' 2>/dev/null)" = "ExternalName" ]; then
  echo "backend is an ExternalName; rebuilding it"
  kubectl delete service backend -n "$ns" --ignore-not-found >/dev/null 2>&1
  kubectl expose deployment backend -n "$ns" --name=backend --port=8080 --target-port=8080 >/dev/null 2>&1
  kubectl rollout restart deployment "$workload" -n "$ns" >/dev/null 2>&1
fi

kubectl rollout status deployment "$workload" -n "$ns" --timeout=120s 2>&1 | tail -1
kubectl get pods -n "$ns" --no-headers | awk '{print "  ", $2, $3, $1}'
