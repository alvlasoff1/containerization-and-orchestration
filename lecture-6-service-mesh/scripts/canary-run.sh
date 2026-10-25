#!/bin/bash
# Сценарий части 7: выкатить v2 (bad — FAIL=true, good — FAIL=false) со
# split 90/10 и запустить gate. Дальше всё решает gate.
# usage: canary-run.sh bad|good [max_wait_sec]
set -eu
cd ~/lab6
NS=lab6-mesh
MODE=$1
WAIT=${2:-400}
FAIL=false; [ "$MODE" = bad ] && FAIL=true

# Исходное состояние: работает только v1, сплита и gate нет.
helm upgrade shop ./shop -n $NS --reset-values >/dev/null
kubectl -n $NS wait --for=delete pod -l app.kubernetes.io/version=v2 --timeout=120s >/dev/null 2>&1 || true
sleep 30
# Выкатка v2: новые pod'ы, HTTPRoute 90/10 и gate создаются заново.
helm upgrade shop ./shop -n $NS --reset-values \
  --set api.versions.v2.enabled=true --set api.versions.v2.env.FAIL=$FAIL \
  --set trafficSplit.enabled=true --set trafficSplit.v2Weight=10 \
  --set gate.enabled=true >/dev/null
kubectl -n $NS rollout status deploy/shop-api-v2 --timeout=180s >/dev/null
kubectl -n $NS rollout status deploy/shop-gate --timeout=180s >/dev/null
echo "start_$MODE $(date -u +%s) $(date -u +%T)"
# ждём решения gate: откат или 100%
end=$(( $(date +%s) + WAIT ))
while [ "$(date +%s)" -lt "$end" ]; do
  if kubectl -n $NS logs deploy/shop-gate 2>/dev/null | grep -q -E '"event": "(rollback|done)"'; then break; fi
  sleep 10
done
sleep 60   # ещё минута, чтобы на графике было видно состояние после решения
echo "end_$MODE $(date -u +%s) $(date -u +%T)"
kubectl -n $NS logs deploy/shop-gate > results/gate-$MODE.log
cat results/gate-$MODE.log
