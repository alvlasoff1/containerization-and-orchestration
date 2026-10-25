#!/bin/bash
# Скрытая поломка связности: выбирает случайную неисправность из списка и
# применяет её через values (helm upgrade -f), ничего не печатая о выборе.
# После upgrade перезапускает api и worker, как при обычном релизе, — так
# сбрасываются уже открытые соединения к БД из пула.
#
# Выбор записывается в ~/.lab5-fault (после --fix — ~/.lab5-fault.last);
# открывать только после диагностики.
#
# usage: hidden-fault.sh          — внести случайную поломку
#        hidden-fault.sh --reveal — показать, что было сломано
#        hidden-fault.sh --fix    — вернуть чистый values.yaml
set -eu
NS=lab5-networking
CHART=$(dirname "$0")/../shop
STATE=~/.lab5-fault
FAULT_VALUES=/tmp/.lab5-fault-values.yaml

faults=(api-port worker-label no-dns postgres-port)

write_fault() {
  case $1 in
    # NetworkPolicy указывает порт Service (80), а не порт контейнера (8080)
    api-port) cat <<'EOF'
networkPolicy:
  api:
    port: 80
EOF
    ;;
    # опечатка в метке клиента postgres: правило не выбирает ни одного worker'а
    worker-label) cat <<'EOF'
networkPolicy:
  postgres:
    clients:
      - name: api
        podLabels:
          app.kubernetes.io/component: api
      - name: worker
        podLabels:
          app.kubernetes.io/component: workers
EOF
    ;;
    # забыли разрешить DNS при default-deny
    no-dns) cat <<'EOF'
networkPolicy:
  dns:
    enabled: false
EOF
    ;;
    # порт БД в правилах не совпадает с портом Service/pod'а
    postgres-port) cat <<'EOF'
networkPolicy:
  postgres:
    port: 5433
EOF
    ;;
  esac
}

case ${1:-} in
  --reveal) cat "$STATE" 2>/dev/null || cat "$STATE.last"; exit 0 ;;
  --fix)
    helm upgrade shop "$CHART" -n $NS --reset-values >/dev/null
    kubectl -n $NS rollout restart deploy/shop-api deploy/shop-worker >/dev/null
    [ -f "$STATE" ] && mv "$STATE" "$STATE.last"
    rm -f "$FAULT_VALUES"
    echo "clean values applied"; exit 0 ;;
esac

f=${faults[RANDOM % ${#faults[@]}]}
write_fault "$f" > "$FAULT_VALUES"
echo "$(date -Is) $f" > "$STATE"
helm upgrade shop "$CHART" -n $NS --reset-values -f "$CHART/values.yaml" -f "$FAULT_VALUES" >/dev/null
kubectl -n $NS rollout restart deploy/shop-api deploy/shop-worker >/dev/null
echo "release upgraded (one of ${#faults[@]} faults applied)"
