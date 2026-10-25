#!/bin/bash
# Живая проверка матрицы доступа: из каждого источника пробуем каждое
# назначение и сравниваем с ожидаемым результатом.
#
# Источники — реальные pod'ы. В api/worker/postgres (distroless/без утилит)
# добавляется ephemeral-контейнер netshoot: он живёт в том же network
# namespace и с той же Cilium identity, что и pod, поэтому политика к нему
# применяется точно так же.
#
# usage: access-matrix.sh            — таблица expected/actual, exit 1 если не всё зелёное
set -u
NS=lab5-networking
IMG=nicolaka/netshoot:v0.13
T=2   # таймаут одной проверки, с

DESTS=(api worker postgres dns internet)
declare -A EXPECT=(
  [client]="allow deny deny allow deny"
  [api]="deny deny allow allow deny"
  [worker]="deny deny allow allow deny"
  [postgres]="deny deny allow allow deny"
  [stray]="deny deny deny allow deny"
  [prometheus]="allow allow deny allow allow"
)
SOURCES=(client api worker postgres stray prometheus)

ensure_pod() { # name labels
  kubectl -n $NS get pod "$1" >/dev/null 2>&1 && return
  kubectl -n $NS run "$1" --image=$IMG ${2:+--labels=$2} --restart=Never \
    --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"'"$1"'","image":"'"$IMG"'","command":["sleep","infinity"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' >/dev/null
  kubectl -n $NS wait --for=condition=Ready pod/"$1" --timeout=180s >/dev/null
}

ensure_probe() { # ns pod -> prints "ns pod container"
  local ns=$1 pod=$2
  local st
  st=$(kubectl -n "$ns" get pod "$pod" -o jsonpath='{.status.ephemeralContainerStatuses[?(@.name=="netprobe")].state.running}')
  if [ -z "$st" ]; then
    kubectl -n "$ns" debug "$pod" --image=$IMG --container=netprobe --profile=restricted -- sleep infinity >/dev/null 2>&1
    for _ in $(seq 60); do
      st=$(kubectl -n "$ns" get pod "$pod" -o jsonpath='{.status.ephemeralContainerStatuses[?(@.name=="netprobe")].state.running}')
      [ -n "$st" ] && break; sleep 2
    done
  fi
  echo "$ns $pod netprobe"
}

first_pod() { # ns selector
  kubectl -n "$1" get pods -l "$2" --field-selector=status.phase=Running -o json |
    jq -r '[.items[] | select(.metadata.deletionTimestamp == null)][0].metadata.name'
}

target() { # source
  case $1 in
    client) ensure_pod client shop.io/role=client; echo "$NS client client" ;;
    stray)  ensure_pod stray ""; echo "$NS stray stray" ;;
    api)      ensure_probe $NS "$(first_pod $NS app.kubernetes.io/component=api)" ;;
    worker)   ensure_probe $NS "$(first_pod $NS app.kubernetes.io/component=worker)" ;;
    postgres) ensure_probe $NS "$(first_pod $NS cnpg.io/cluster=shop-postgres)" ;;
    prometheus) ensure_probe lab2-observability "$(first_pod lab2-observability app.kubernetes.io/name=prometheus)" ;;
  esac
}

# IP pod'ов компонента, кроме самого источника: трафик pod'а к самому себе
# (в том числе через свой же Service) NetworkPolicy не ограничивает.
peer_ips() { # component self-ip
  kubectl -n $NS get pods -l app.kubernetes.io/component="$1" --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.status.podIP}{"\n"}{end}' | grep -vx "$2" | tr '\n' ' '
}

check() { # ns pod container dest
  local cmd self
  self=$(kubectl -n "$1" get pod "$2" -o jsonpath='{.status.podIP}')
  case $4 in
    api|worker)
      cmd="for ip in $(peer_ips "$4" "$self"); do curl -s -o /dev/null -m $T http://\$ip:8080/health && exit 0; done; exit 1" ;;
    postgres) cmd="nc -z -w $T shop-postgres-rw.$NS 5432" ;;
    dns)      cmd="nslookup -timeout=$T -retry=1 shop-postgres-rw.$NS.svc.cluster.local >/dev/null" ;;
    internet) cmd="nc -z -w $T 1.1.1.1 443" ;;
  esac
  kubectl -n "$1" exec "$2" -c "$3" -- sh -c "$cmd" >/dev/null 2>&1 && echo allow || echo deny
}

G=$'\e[32m'; R=$'\e[31m'; B=$'\e[1m'; N=$'\e[0m'
bad=0
printf "${B}%-12s" "from \\ to"; for d in "${DESTS[@]}"; do printf "%-18s" "$d"; done; printf "${N}\n"
for s in "${SOURCES[@]}"; do
  read -r tns tpod tc < <(target "$s")
  exp=(${EXPECT[$s]})
  printf "%-12s" "$s"
  for i in "${!DESTS[@]}"; do
    act=$(check "$tns" "$tpod" "$tc" "${DESTS[$i]}")
    if [ "$act" = "${exp[$i]}" ]; then c=$G; m=""; else c=$R; m="MISMATCH"; bad=$((bad+1)); fi
    printf "${c}%-18s${N}" "${exp[$i]}/${act} $m"
  done
  printf "\n"
done
# внешний клиент: с хоста через NodePort любой ноды
ext=$(curl -s -o /dev/null -m $T -w '%{http_code}' http://$(kubectl get node lab5-worker -o jsonpath='{.status.addresses[0].address}'):30080/health | grep -q 200 && echo allow || echo deny)
if [ "$ext" = allow ]; then c=$G; m=""; else c=$R; m="MISMATCH"; bad=$((bad+1)); fi
printf "%-12s${c}%-18s${N}%s\n" "external" "allow/${ext} $m" "(NodePort :30080)"
echo
[ $bad -eq 0 ] && echo "${G}${B}matrix: all green${N}" || echo "${R}${B}matrix: $bad mismatches${N}"
exit $bad
