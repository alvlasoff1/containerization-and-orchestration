#!/bin/bash
# Выгрузка рядов из Prometheus (query_range) в JSON для графиков.
# usage: prom-range.sh <start_unix> <end_unix> <out.json> name=query [name=query ...]
set -eu
start=$1 end=$2 out=$3; shift 3
kubectl -n lab2-observability port-forward svc/prometheus-operated 19092:9090 >/dev/null 2>&1 &
pf=$!; sleep 3
echo "{" > "$out"
first=1
for kv in "$@"; do
  name=${kv%%=*}; q=${kv#*=}
  r=$(curl -s --data-urlencode "query=$q" --data-urlencode "start=$start" --data-urlencode "end=$end" \
      --data-urlencode "step=10" localhost:19092/api/v1/query_range | jq -c '.data.result')
  [ $first = 1 ] || echo "," >> "$out"; first=0
  printf '"%s": %s' "$name" "$r" >> "$out"
done
echo "}" >> "$out"
kill $pf
