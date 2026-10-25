#!/bin/bash
# Постоянная клиентская нагрузка изнутри кластера: pod с меткой клиента шлёт
# POST /order на Service по DNS-имени, каждый запрос — новое соединение и
# новый DNS-lookup. Каждая строка лога: "<unix-time> <http-code>",
# код 000 — ошибка соединения/таймаут.
#
# usage: client-load.sh [duration_sec] [out.log]
set -eu
NS=lab5-networking
DURATION=${1:-300}
OUT=${2:-/tmp/chaos/client.log}
URL=http://shop-api.$NS.svc.cluster.local/order

kubectl -n $NS delete pod shop-client --ignore-not-found --wait=true >/dev/null
kubectl -n $NS apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: shop-client
  labels:
    shop.io/role: client
spec:
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    runAsUser: 100
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: client
      image: curlimages/curl:8.10.1
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: {drop: ["ALL"]}
      command: ["sh", "-c"]
      args:
        - |
          end=\$(( \$(date +%s) + $DURATION ))
          while [ \$(date +%s) -lt \$end ]; do
            code=\$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 5 \
              -X POST -H 'Content-Type: application/json' -d '{"item":"book","quantity":1}' $URL)
            echo "\$(date +%s) \$code"
            sleep 0.05
          done
EOF
kubectl -n $NS wait --for=condition=Ready pod/shop-client --timeout=120s >/dev/null
echo "client started: $URL for ${DURATION}s"
kubectl -n $NS wait --for=jsonpath='{.status.phase}'=Succeeded pod/shop-client --timeout=$((DURATION + 120))s >/dev/null
mkdir -p "$(dirname "$OUT")"
kubectl -n $NS logs shop-client > "$OUT"
awk '{n++; if ($2 ~ /^2/) ok++; else fail++} END {printf "requests=%d ok=%d failed=%d\n", n, ok, fail}' "$OUT"
