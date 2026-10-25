#!/usr/bin/env python3
"""Prometheus-экспортер для Ollama (у самой Ollama нет /metrics).

Каждые SCRAPE_INTERVAL секунд опрашивает API Ollama и отдаёт на :9101/metrics:
  ollama_up                         1, если API отвечает
  ollama_api_latency_seconds        время ответа /api/version
  ollama_loaded_models              сколько моделей сейчас в памяти
  ollama_model_memory_bytes{model}  сколько памяти занимает загруженная модель
  ollama_model_expires_seconds{model} через сколько модель будет выгружена
  ollama_models_available           сколько моделей скачано
Только stdlib.
"""
import datetime as dt
import http.server
import json
import os
import threading
import time
import urllib.request

OLLAMA = os.getenv("OLLAMA_URL", "http://ollama:11434")
INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "15"))
PORT = int(os.getenv("PORT", "9101"))
state = {"text": ""}


def get(path):
    with urllib.request.urlopen(OLLAMA + path, timeout=10) as r:
        return json.load(r)


def collect():
    lines = []
    t0 = time.monotonic()
    try:
        get("/api/version")
        up, lat = 1, time.monotonic() - t0
    except Exception:
        up, lat = 0, 0
    lines += ["# HELP ollama_up Ollama API is reachable", "# TYPE ollama_up gauge", f"ollama_up {up}",
              "# HELP ollama_api_latency_seconds Latency of /api/version", "# TYPE ollama_api_latency_seconds gauge",
              f"ollama_api_latency_seconds {lat:.4f}"]
    if up:
        ps = get("/api/ps").get("models", [])
        tags = get("/api/tags").get("models", [])
        now = dt.datetime.now(dt.timezone.utc)
        lines += ["# TYPE ollama_loaded_models gauge", f"ollama_loaded_models {len(ps)}",
                  "# TYPE ollama_models_available gauge", f"ollama_models_available {len(tags)}",
                  "# TYPE ollama_model_memory_bytes gauge", "# TYPE ollama_model_expires_seconds gauge"]
        for m in ps:
            name = m.get("name", "")
            lines.append(f'ollama_model_memory_bytes{{model="{name}"}} {m.get("size", 0)}')
            exp = m.get("expires_at")
            if exp:
                try:
                    left = (dt.datetime.fromisoformat(exp.replace("Z", "+00:00")) - now).total_seconds()
                    lines.append(f'ollama_model_expires_seconds{{model="{name}"}} {max(0, left):.0f}')
                except ValueError:
                    pass
    state["text"] = "\n".join(lines) + "\n"


def loop():
    while True:
        try:
            collect()
        except Exception as e:  # экспортер не должен падать из-за Ollama
            state["text"] = f"ollama_up 0\n# collect error: {e}\n"
        time.sleep(INTERVAL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = state["text"].encode() if self.path == "/metrics" else b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
