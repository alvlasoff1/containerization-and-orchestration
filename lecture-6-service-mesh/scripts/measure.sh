#!/bin/bash
# Замер латентности и ресурсов api под фиксированной нагрузкой.
# fortio шлёт POST /order с постоянной частотой на Service shop-api.
# Если MESH=1, pod нагрузки тоже включается в mesh (клиентский прокси).
# После прогона из Prometheus берутся средние CPU и память контейнеров
# api-pod'ов за окно нагрузки (cAdvisor).
#
# usage: MESH=0|1 measure.sh <label> [qps] [duration]
set -eu
NS=lab6-mesh
LABEL=$1
QPS=${2:-100}
DUR=${3:-120s}
MESH=${MESH:-0}
OUT=~/lab6/results/$LABEL
mkdir -p "$OUT"

ann='{}'
[ "$MESH" = 1 ] && ann='{"linkerd.io/inject":"enabled"}'

kubectl -n $NS delete pod fortio --ignore-not-found --wait=true >/dev/null
kubectl -n $NS apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: fortio
  labels: {app: fortio}
  annotations: $ann
spec:
  restartPolicy: Never
  containers:
    - name: fortio
      image: fortio/fortio:1.69.4
      args: ["load", "-qps", "$QPS", "-c", "8", "-t", "$DUR", "-timeout", "3s",
             "-content-type", "application/json", "-payload", '{"item":"book","quantity":1}',
             "http://shop-api.$NS.svc.cluster.local/order"]
      resources:
        requests: {cpu: 200m, memory: 64Mi}
EOF
kubectl -n $NS wait --for=condition=Ready pod/fortio --timeout=120s >/dev/null || true
start=$(date +%s)
# ждём, пока закончится контейнер fortio (в mesh рядом живёт ещё прокси)
term='{.status.containerStatuses[?(@.name=="fortio")].state.terminated.reason}'
until [ -n "$(kubectl -n $NS get pod fortio -o jsonpath="$term")" ]; do sleep 5; done
end=$(date +%s)
kubectl -n $NS logs fortio -c fortio > "$OUT/fortio.log"
# первые три "# target" — латентность ответа (дальше идёт время соединения)
{
  grep -E "target 50%|target 90%|target 99%" "$OUT/fortio.log" | head -3 |
    awk '{printf "latency p%s %.1f ms\n", substr($3,1,length($3)-1), $4*1000}'
  grep -E "^Code|All done" "$OUT/fortio.log"
} > "$OUT/summary.txt" || true

# ресурсы api за окно нагрузки (последние ~DUR секунд)
win=$(( end - start )); [ $win -lt 60 ] && win=60
kubectl -n lab2-observability port-forward svc/prometheus-operated 19091:9090 >/dev/null 2>&1 &
pf=$!; sleep 3
q() { curl -s --data-urlencode "query=$1" --data-urlencode "time=$end" localhost:19091/api/v1/query |
      jq -r '.data.result[] | "\(.metric.container // "total")\t\(.value[1])"'; }
{
  echo "# CPU, millicores (avg over ${win}s, sum over api pods, by container)"
  q "sum by (container) (rate(container_cpu_usage_seconds_total{namespace=\"$NS\",pod=~\"shop-api-.*\",container!=\"\",container!=\"POD\"}[${win}s])) * 1000"
  echo "# Memory working set, MiB (sum over api pods, by container)"
  q "sum by (container) (avg_over_time(container_memory_working_set_bytes{namespace=\"$NS\",pod=~\"shop-api-.*\",container!=\"\",container!=\"POD\"}[${win}s])) / 1048576"
} > "$OUT/resources.txt"
kill $pf
cat "$OUT/summary.txt" "$OUT/resources.txt"
