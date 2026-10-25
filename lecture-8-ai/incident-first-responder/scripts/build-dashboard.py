#!/usr/bin/env python3
"""Генерирует k8s/50-dashboard.yaml: дашборд Grafana для lab8 (ConfigMap
с меткой grafana_dashboard=1, его подхватывает sidecar Grafana из lab2)."""
import json

DS = {"type": "prometheus", "uid": "prometheus"}
WF = 'workflow_id="incidentFirstResp"'


def ts(i, title, targets, x, y, w=12, h=8, unit="short", bars=False, desc=""):
    return {"id": i, "type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "custom": {
                "drawStyle": "bars" if bars else "line", "lineWidth": 2, "fillOpacity": 60 if bars else 10,
                "showPoints": "never", "stacking": {"mode": "normal" if bars else "none"}}}, "overrides": []},
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
            "targets": [{"refId": chr(65 + n), "datasource": DS, "expr": e, "legendFormat": l}
                        for n, (e, l) in enumerate(targets)]}


panels = [
    ts(1, "Запуски workflow по статусу (с запуска n8n)", [
        (f'sum by (status) (n8n_workflow_execution_duration_seconds_count{{{WF}}})', "{{status}}")],
       0, 0, desc="Накопительное число запусков incident first responder по статусу; счётчики n8n обнуляются при рестарте."),
    ts(2, "Длительность запуска workflow", [
        (f'sum(n8n_workflow_execution_duration_seconds_sum{{{WF}}}) / sum(n8n_workflow_execution_duration_seconds_count{{{WF}}})', "среднее"),
        (f'histogram_quantile(0.9, sum by (le) (rate(n8n_workflow_execution_duration_seconds_bucket{{{WF}}}[30m])))', "p90")],
       12, 0, unit="s", desc="Почти всё время — работа LLM на CPU."),
    ts(3, "Вызовы инструментов агентом (MCP Kubernetes)", [
        ('sum by (tool_name) (increase(k8s_mcp_tool_calls_total{tool_name!~"initialize|tools/list|ping"}[5m]))', "{{tool_name}}")],
       0, 8, bars=True),
    ts(4, "Алерты, отправленные Alertmanager в n8n", [
        ('sum(increase(alertmanager_notifications_total{integration="webhook"}[5m]))', "отправлено"),
        ('sum(increase(alertmanager_notifications_failed_total{integration="webhook"}[5m]))', "ошибки доставки")],
       12, 8, bars=True),
    ts(5, "Ollama: память", [
        ('sum by (model) (ollama_model_memory_bytes)', "модель {{model}}"),
        ('sum(container_memory_working_set_bytes{namespace="lab8-n8n",container="ollama"})', "контейнер ollama")],
       0, 16, unit="bytes"),
    ts(6, "Ollama: CPU и доступность", [
        ('sum(rate(container_cpu_usage_seconds_total{namespace="lab8-n8n",container="ollama"}[1m]))', "CPU, ядер"),
        ('min(ollama_up)', "ollama_up")],
       12, 16),
    {"id": 7, "type": "table", "title": "Сработавшие алерты lab8", "datasource": DS,
     "gridPos": {"x": 0, "y": 24, "w": 24, "h": 5},
     "targets": [{"refId": "A", "datasource": DS, "expr": 'ALERTS{namespace="lab8-n8n"}', "instant": True, "format": "table"}],
     "transformations": [{"id": "organize", "options": {"excludeByName": {"Time": True, "Value": True, "__name__": True}}}]},
]
dash = {"uid": "lab8-first-responder", "title": "Incident first responder (n8n + AI)", "timezone": "browser",
        "schemaVersion": 39, "refresh": "30s", "time": {"from": "now-1h", "to": "now"}, "tags": ["lab8"], "panels": panels}
body = json.dumps(dash, ensure_ascii=False, indent=1)
print("""# Дашборд lab8, генерируется scripts/build-dashboard.py
apiVersion: v1
kind: ConfigMap
metadata:
  name: lab8-dashboard
  namespace: lab8-n8n
  labels:
    grafana_dashboard: "1"
data:
  lab8-first-responder.json: |-""")
for line in body.splitlines():
    print("    " + line)
