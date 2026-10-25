#!/usr/bin/env python3
"""Собирает workflow n8n (JSON для import:workflow) из описания ниже.
Системный промпт агента берётся из skills/first-responder.md.

usage: build-workflow.py > workflow/incident-first-responder.json
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
skill = (ROOT / "skills/first-responder.md").read_text()
SYSTEM = re.search(r"<!-- SYSTEM-PROMPT-START -->\n(.*?)<!-- SYSTEM-PROMPT-END -->", skill, re.S).group(1).strip()

PARSE = r"""
// Не-AI шаг: разбираем payload Alertmanager. Берём только firing-алерты,
// достаём namespace, описание и PromQL-выражение из generatorURL.
const body = $input.first().json.body || {};
const out = [];
for (const a of body.alerts || []) {
  if (a.status !== 'firing') continue;
  const l = a.labels || {}, an = a.annotations || {};
  // g0.expr из generatorURL: form-urlencoded, '+' означает пробел
  const m = /[?&]g0\.expr=([^&]*)/.exec(a.generatorURL || '');
  const expr = m ? decodeURIComponent(m[1].replace(/\+/g, ' ')).replace(/\s+/g, ' ').trim() : '';
  const ns = l.namespace || l.destination_namespace || l.source_namespace || 'default';
  const prompt = [
    `Alert: ${l.alertname} (severity: ${l.severity || 'none'})`,
    `Namespace: ${ns}`,
    `Summary: ${an.summary || ''}`,
    `Description: ${an.description || ''}`,
    `Started at: ${a.startsAt}`,
    `PromQL expression of the alert: ${expr}`,
    '',
    'Plan, execute all four tool calls in this order:',
    `1. pods_list_in_namespace with namespace "${ns}"`,
    `2. events_list with namespace "${ns}" and fieldSelector "type=Warning"`,
    `3. pods_log with namespace "${ns}", a pod name taken from step 1, container "api", tail 20`,
    `4. execute_query with query: ${expr}`,
    'Write the report only after the fourth tool result.',
    '/no_think',
  ].join('\n');
  out.push({ json: { alertname: l.alertname, namespace: ns, severity: l.severity || '',
                     summary: an.summary || '', startsAt: a.startsAt, expr, prompt } });
}
return out;
"""

BUILD = r"""
// Не-AI шаг: собираем Markdown-отчёт и имя файла.
const alert = $('Разобрать алерты').item.json;
const ai = $json;
const steps = (ai.intermediateSteps || []).map((s, i) => {
  const a = s.action || {};
  return `${i + 1}. \`${a.tool}\` ${JSON.stringify(a.toolInput || {})}`;
});
const ts = new Date().toISOString().replace(/[:.]/g, '-');
const fileName = `${ts}-${alert.alertname}.md`;
const report = [
  `# ${alert.alertname} — ${alert.namespace}`,
  '',
  `- severity: ${alert.severity}`,
  `- начало: ${alert.startsAt}`,
  `- описание: ${alert.summary}`,
  `- выражение: \`${alert.expr}\``,
  '',
  ai.output || '(агент не вернул ответ)',
  '',
  '---',
  '#### Вызовы инструментов агентом',
  steps.length ? steps.join('\n') : '(нет)',
  '',
].join('\n');
return [{ json: { fileName, report, toolCalls: steps.length } }];
"""

nodes = [
    {"id": "n-webhook", "name": "Alertmanager webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2.1,
     "position": [0, 0], "webhookId": "6a0f6c1e-1b1e-4c55-9f4e-alert0000001",
     "parameters": {"httpMethod": "POST", "path": "alert", "responseMode": "onReceived", "options": {}}},
    {"id": "n-parse", "name": "Разобрать алерты", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [240, 0], "parameters": {"jsCode": PARSE.strip()}},
    {"id": "n-agent", "name": "AI-агент first responder", "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 3.1,
     "position": [500, 0],
     "parameters": {"promptType": "define", "text": "={{ $json.prompt }}",
                    "options": {"systemMessage": SYSTEM, "maxIterations": 8, "returnIntermediateSteps": True}}},
    # Локальная Ollama через её OpenAI-совместимый API (/v1/chat/completions):
    # клиент OpenAI в n8n корректно разбирает несколько tool calls в одном
    # ответе модели. Responses API выключен — Ollama его поддерживает не полностью.
    {"id": "n-llm", "name": "Ollama qwen3:1.7b", "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi", "typeVersion": 1.3,
     "position": [380, 240],
     "parameters": {"model": {"__rl": True, "mode": "id", "value": "qwen3:1.7b"}, "responsesApiEnabled": False,
                    "options": {"temperature": 0.1, "maxTokens": 1500, "maxRetries": 0, "timeout": 900000}},
     "credentials": {"openAiApi": {"id": "ollamaLab8", "name": "Ollama (lab8, OpenAI API)"}}},
    {"id": "n-mcp-k8s", "name": "MCP Kubernetes", "type": "@n8n/n8n-nodes-langchain.mcpClientTool", "typeVersion": 1.4,
     "position": [560, 240],
     "parameters": {"endpointUrl": "http://mcp-kubernetes.lab8-n8n.svc:8080/mcp", "serverTransport": "httpStreamable",
                    "include": "selected", "includeTools": ["pods_list_in_namespace", "events_list", "pods_log"],
                    "options": {"timeout": 30000}}},
    {"id": "n-mcp-prom", "name": "MCP Prometheus", "type": "@n8n/n8n-nodes-langchain.mcpClientTool", "typeVersion": 1.4,
     "position": [740, 240],
     "parameters": {"endpointUrl": "http://mcp-prometheus.lab8-n8n.svc:8080/mcp", "serverTransport": "httpStreamable",
                    "include": "selected", "includeTools": ["execute_query"], "options": {"timeout": 30000}}},
    {"id": "n-build", "name": "Собрать отчёт", "type": "n8n-nodes-base.code", "typeVersion": 2,
     "position": [820, 0], "parameters": {"jsCode": BUILD.strip()}},
    {"id": "n-tofile", "name": "В файл", "type": "n8n-nodes-base.convertToFile", "typeVersion": 1.1,
     "position": [1040, 0],
     "parameters": {"operation": "toText", "sourceProperty": "report", "options": {"fileName": "={{ $json.fileName }}"}}},
    {"id": "n-write", "name": "Сохранить отчёт", "type": "n8n-nodes-base.readWriteFile", "typeVersion": 1,
     "position": [1260, 0],
     "parameters": {"operation": "write", "fileName": "=/home/node/reports/{{ $('Собрать отчёт').item.json.fileName }}",
                    "dataPropertyName": "data", "options": {}}},
]

main = lambda a, b: {a: {"main": [[{"node": b, "type": "main", "index": 0}]]}}
connections = {}
for a, b in [("Alertmanager webhook", "Разобрать алерты"), ("Разобрать алерты", "AI-агент first responder"),
             ("AI-агент first responder", "Собрать отчёт"), ("Собрать отчёт", "В файл"), ("В файл", "Сохранить отчёт")]:
    connections.update(main(a, b))
connections["Ollama qwen3:1.7b"] = {"ai_languageModel": [[{"node": "AI-агент first responder", "type": "ai_languageModel", "index": 0}]]}
for t in ("MCP Kubernetes", "MCP Prometheus"):
    connections[t] = {"ai_tool": [[{"node": "AI-агент first responder", "type": "ai_tool", "index": 0}]]}

workflow = {"id": "incidentFirstResp", "name": "Incident first responder", "active": False,
            "nodes": nodes, "connections": connections, "settings": {"executionOrder": "v1"},
            "pinData": {}, "tags": []}
print(json.dumps(workflow, ensure_ascii=False, indent=2))
