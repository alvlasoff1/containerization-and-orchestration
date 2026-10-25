#!/bin/bash
# Непрерывный хаос: каждые INTERVAL секунд убивает случайный pod api или worker.
# Параллельно раз в секунду пишет, какие IP сейчас стоят за Service shop-api
# (ready-адреса из EndpointSlice), — чтобы на графике было видно смену IP.
#
# usage: pod-chaos.sh [duration_sec] [interval_sec] [logdir]
set -u
NS=lab5-networking
DURATION=${1:-300}
INTERVAL=${2:-3}
LOG=${3:-/tmp/chaos}
mkdir -p "$LOG"
: > "$LOG/kills.log"; : > "$LOG/endpoints.log"

end=$(( $(date +%s) + DURATION ))

# сэмплер EndpointSlice
(
  while [ "$(date +%s)" -lt "$end" ]; do
    ips=$(kubectl -n $NS get endpointslices -l kubernetes.io/service-name=shop-api \
      -o jsonpath='{range .items[*].endpoints[?(@.conditions.ready==true)]}{.addresses[0]}{" "}{end}')
    echo "$(date +%s) $ips" >> "$LOG/endpoints.log"
    sleep 1
  done
) &

while [ "$(date +%s)" -lt "$end" ]; do
  comp=$( (( RANDOM % 2 )) && echo api || echo worker )
  # только живые pod'ы: уже удаляемые (deletionTimestamp) не трогаем
  mapfile -t pods < <(kubectl -n $NS get pods -l app.kubernetes.io/component=$comp \
    --field-selector=status.phase=Running -o json |
    jq -r '.items[] | select(.metadata.deletionTimestamp == null) | "\(.metadata.name) \(.status.podIP)"')
  if [ ${#pods[@]} -gt 0 ]; then
    victim=${pods[RANDOM % ${#pods[@]}]}
    name=${victim% *}; ip=${victim#* }
    kubectl -n $NS delete pod "$name" --wait=false >/dev/null
    echo "$(date +%s) $comp $name $ip" | tee -a "$LOG/kills.log"
  fi
  sleep "$INTERVAL"
done
wait
echo "kills: $(wc -l < "$LOG/kills.log")"
