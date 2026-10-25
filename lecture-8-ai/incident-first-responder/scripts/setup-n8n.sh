#!/bin/bash
# Первичная настройка n8n в кластере: владелец, credentials Ollama, импорт и
# публикация workflow. Пароль владельца генерируется и хранится только в
# ~/lab8/n8n-owner.txt (не в репозитории).
set -eu
cd "$(dirname "$0")/.."
NS=lab8-n8n
POD=$(kubectl -n $NS get pod -l app=n8n -o jsonpath='{.items[0].metadata.name}')
N8N=http://$(kubectl -n $NS get svc n8n -o jsonpath='{.spec.clusterIP}'):5678

OWNER=~/lab8/n8n-owner.txt
if [ ! -f "$OWNER" ]; then
  umask 077
  echo "email=admin@lab8.local" > "$OWNER"
  echo "password=Lab8-$(openssl rand -hex 8)" >> "$OWNER"
fi
EMAIL=$(sed -n 's/^email=//p' "$OWNER"); PASS=$(sed -n 's/^password=//p' "$OWNER")
kubectl -n $NS exec "$POD" -- wget -q -O- --header 'Content-Type: application/json' \
  --post-data "{\"email\":\"$EMAIL\",\"firstName\":\"Lab\",\"lastName\":\"Eight\",\"password\":\"$PASS\"}" \
  http://localhost:5678/rest/owner/setup >/dev/null 2>&1 && echo "owner created" || echo "owner already set up"

kubectl -n $NS cp workflow/credentials-ollama.json "$POD":/tmp/creds.json
kubectl -n $NS cp workflow/incident-first-responder.json "$POD":/tmp/workflow.json
kubectl -n $NS exec "$POD" -- n8n import:credentials --input=/tmp/creds.json
kubectl -n $NS exec "$POD" -- n8n import:workflow --input=/tmp/workflow.json
kubectl -n $NS exec "$POD" -- n8n publish:workflow --id=incidentFirstResp
# публикация через CLI применяется после рестарта процесса n8n
kubectl -n $NS rollout restart deploy/n8n
kubectl -n $NS rollout status deploy/n8n --timeout=300s
echo "n8n: $N8N"
