#!/usr/bin/env python3
"""Canary gate: каждые INTERVAL секунд смотрит на здоровье v2 в Prometheus
и двигает вес v2 в HTTPRoute.

Правило:
  - трафика на v2 слишком мало (< MIN_RPS)  -> ждём, вес не трогаем;
  - доля ошибок v2 > MAX_ERROR_RATIO или p95 v2 > MAX_P95_SECONDS
                                            -> откат: вес v2 = 0, gate останавливается;
  - иначе                                   -> вес v2 += STEP (до 100), на 100 — релиз завершён.

Только stdlib: Prometheus HTTP API и Kubernetes API (токен ServiceAccount).
"""
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

PROM = os.getenv("PROMETHEUS_URL", "http://prometheus-operated.lab2-observability.svc:9090")
NS = os.getenv("NAMESPACE", "lab6-mesh")
ROUTE = os.getenv("ROUTE", "shop-api-split")
V2_BACKEND = os.getenv("V2_BACKEND", "shop-api-v2")
INTERVAL = int(os.getenv("INTERVAL", "15"))
STEP = int(os.getenv("STEP", "10"))
MIN_RPS = float(os.getenv("MIN_RPS", "0.5"))
MAX_ERROR_RATIO = float(os.getenv("MAX_ERROR_RATIO", "0.05"))
MAX_P95_SECONDS = float(os.getenv("MAX_P95_SECONDS", "0.3"))
WINDOW = os.getenv("WINDOW", "30s")

# Метрики v2 со стороны mesh: inbound-прокси pod'ов v2, только mTLS-трафик
# клиентов (пробы kubelet и скрейп Prometheus идут без mTLS и не считаются).
SEL = os.getenv("V2_SELECTOR", f'namespace="{NS}",deployment="shop-api-v2",direction="inbound",tls="true"')
Q_RPS = os.getenv("Q_RPS", f"sum(rate(response_total{{{SEL}}}[{WINDOW}]))")
Q_ERR = os.getenv("Q_ERR", f'sum(rate(response_total{{{SEL},classification="failure"}}[{WINDOW}])) / sum(rate(response_total{{{SEL}}}[{WINDOW}]))')
Q_P95 = os.getenv("Q_P95", f"histogram_quantile(0.95, sum by (le) (rate(response_latency_ms_bucket{{{SEL}}}[{WINDOW}]))) / 1000")

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = "https://kubernetes.default.svc"


def log(**kv):
    kv["ts"] = time.strftime("%H:%M:%S")
    print(json.dumps(kv, ensure_ascii=False), flush=True)


def prom(query):
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=10) as r:
        res = json.load(r)["data"]["result"]
    if not res:
        return None
    v = float(res[0]["value"][1])
    return None if v != v else v  # NaN -> None


def kube(method, path, body=None, ctype="application/json"):
    token = open(f"{SA}/token").read()
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.load(r)


ROUTE_PATH = f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes/{ROUTE}"


def get_weight():
    route = kube("GET", ROUTE_PATH)
    for b in route["spec"]["rules"][0]["backendRefs"]:
        if b["name"] == V2_BACKEND:
            return int(b.get("weight", 0))
    return 0


def set_weight(v2):
    route = kube("GET", ROUTE_PATH)
    refs = route["spec"]["rules"][0]["backendRefs"]
    for b in refs:
        b["weight"] = v2 if b["name"] == V2_BACKEND else 100 - v2
    patch = [{"op": "replace", "path": "/spec/rules/0/backendRefs", "value": refs}]
    kube("PATCH", ROUTE_PATH, patch, "application/json-patch+json")


def main():
    log(event="start", route=ROUTE, step=STEP, max_error_ratio=MAX_ERROR_RATIO,
        max_p95_s=MAX_P95_SECONDS, interval_s=INTERVAL)
    while True:
        time.sleep(INTERVAL)
        weight = get_weight()
        try:
            rps, err, p95 = prom(Q_RPS), prom(Q_ERR), prom(Q_P95)
        except Exception as e:  # Prometheus недоступен — ничего не меняем
            log(event="error", error=str(e), weight=weight)
            continue
        state = dict(weight=weight, v2_rps=round(rps or 0, 2),
                     v2_error_ratio=None if err is None else round(err, 3),
                     v2_p95_s=None if p95 is None else round(p95, 3))
        if rps is None or rps < MIN_RPS:
            log(event="hold", reason="not enough v2 traffic", **state)
            continue
        if (err or 0) > MAX_ERROR_RATIO or (p95 or 0) > MAX_P95_SECONDS:
            set_weight(0)
            log(event="rollback", reason="v2 unhealthy", new_weight=0, **state)
            break
        new = min(100, weight + STEP)
        set_weight(new)
        log(event="promote", new_weight=new, **state)
        if new == 100:
            log(event="done", reason="v2 takes 100% of traffic")
            break
    # gate закончил решение и просто живёт, чтобы pod не перезапускался
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
