#!/bin/bash
# Печатает PID основного процесса pod'а на ноде kind — по нему можно зайти
# в network namespace pod'а: docker exec <node> nsenter -t <PID> -n <cmd>
# usage: pod-netns.sh <namespace> <pod>
set -euo pipefail
ns=$1 pod=$2
node=$(kubectl -n "$ns" get pod "$pod" -o jsonpath='{.spec.nodeName}')
sandbox=$(docker exec "$node" crictl pods -q --name "$pod" --namespace "$ns")
cid=$(docker exec "$node" crictl ps -q --pod "$sandbox" | head -1)
docker exec "$node" crictl inspect -o go-template --template '{{.info.pid}}' "$cid"
