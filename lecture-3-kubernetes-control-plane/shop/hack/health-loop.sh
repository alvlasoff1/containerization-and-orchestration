#!/usr/bin/env bash
# Hammer an endpoint in a tight loop to prove a rolling update drops nothing.
# Each curl opens a FRESH connection (that's what catches the kube-proxy race).
# A request "fails" if curl errors (refused/reset/timeout) OR status != 200.
# Ctrl-C to stop — it prints totals.
#
# Usage:
#   ./health-loop.sh <url>
#   ./health-loop.sh http://<node-ip>:<nodeport>/health
set -u

URL="${1:-http://localhost:8080/health}"

total=0 ok=0 fail=0

summary() {
  echo
  echo "==================== summary ===================="
  echo "target : $URL"
  echo "total  : $total"
  echo "ok     : $ok"
  echo "failed : $fail"
  echo "================================================="
  exit 0
}
trap summary INT TERM

echo "hammering $URL — Ctrl-C to stop"
while true; do
  total=$((total + 1))
  # -s silent, write only the http code; bounded timeouts so a hang counts fast.
  code=$(curl -s -o /dev/null -w '%{http_code}' \
              --connect-timeout 2 --max-time 5 "$URL")
  rc=$?
  if [ "$rc" -ne 0 ] || [ "$code" != "200" ]; then
    fail=$((fail + 1))
    printf '[%s] #%d FAILED  curl_rc=%d  http=%s\n' "$(date +%T)" "$total" "$rc" "$code"
  else
    ok=$((ok + 1))
  fi
done
