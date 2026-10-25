# Incident first responder

Автоматизация для дежурных: алерт из Alertmanager запускает workflow n8n, AI-агент через два MCP-сервера собирает контекст инцидента в Kubernetes и Prometheus и сохраняет короткий отчёт.

## Состав

| Путь | Что это |
|---|---|
| workflow/incident-first-responder.json | экспорт workflow n8n |
| workflow/credentials-ollama.json | credentials для локальной Ollama (OpenAI-совместимый API) |
| skills/first-responder.md | skill агента: порядок действий, правила, системный промпт, toolkit |
| k8s/ | манифесты: Ollama, MCP-серверы, n8n, мониторинг, дашборд |
| ollama/Dockerfile | Ollama только с CPU-бэкендом |
| exporter/ | Prometheus-экспортер для Ollama |
| monitoring/operator-values.yaml | стек из lab2 с маршрутом алертов в n8n |
| scripts/ | генераторы workflow и дашборда, настройка n8n, просмотр выполнений |

## Запуск

Нужен кластер со стеком мониторинга из lab2 (kube-prometheus-stack, Grafana с sidecar дашбордов).

1. Собрать образы и загрузить в кластер (в kind: `kind load docker-image`):
   - `ollama/` — предварительно распаковать CPU-часть релиза Ollama в `ollama/ollama-dist` (команда в Dockerfile);
   - `exporter/`.
2. Применить манифесты: `kubectl apply -f k8s/`.
3. Скачать модель: `kubectl -n lab8-n8n exec deploy/ollama -- ollama pull qwen3:1.7b`.
4. Настроить n8n (владелец, credentials, импорт и публикация workflow): `scripts/setup-n8n.sh`.
5. Направить алерты в n8n: `helm upgrade prometheus-operator prometheus-community/kube-prometheus-stack -n lab2-observability -f monitoring/operator-values.yaml`.
6. Проверить вручную: `curl -X POST -H 'Content-Type: application/json' --data @scripts/alertmanager-payload.json http://<node>:30567/webhook/alert`.

Отчёты появляются в pod'е n8n в `/home/node/reports`.
