#!/bin/bash
# Сценарий части 4: FLAKY=true на v1 и постоянная нагрузка от loadgen.
# Фаза A — mesh без retries, фаза B — retries включены аннотацией Service.
# usage: reliability-run.sh [phaseA_sec] [phaseB_sec]
set -eu
cd ~/lab6
A=${1:-180}; B=${2:-240}
base="--reset-values --set mesh.enabled=true --set monitoring.linkerdProxy.enabled=true --set api.versions.v1.env.FLAKY=true"
helm upgrade shop ./shop -n lab6-mesh $base >/dev/null
echo "phaseA_no_retries $(date -u +%s) $(date -u +%T)"
sleep "$A"
helm upgrade shop ./shop -n lab6-mesh $base --set mesh.retries.enabled=true >/dev/null
echo "phaseB_retries $(date -u +%s) $(date -u +%T)"
sleep "$B"
echo "end $(date -u +%s) $(date -u +%T)"
