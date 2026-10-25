#!/usr/bin/env python3
"""Печатает результат последнего выполнения workflow n8n: статус, время и
вывод каждой ноды (через внутренний REST API n8n, логин владельцем).

usage: last-execution.py <n8n_url> <owner_file> [node_name_filter]
"""
import http.cookiejar
import json
import sys
import urllib.request

url, owner = sys.argv[1], sys.argv[2]
flt = sys.argv[3] if len(sys.argv) > 3 else ""
cred = dict(l.strip().split("=", 1) for l in open(owner) if "=" in l)

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(path, body=None):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with op.open(req, timeout=30) as r:
        return json.load(r)


call("/rest/login", {"emailOrLdapLoginId": cred["email"], "password": cred["password"]})
lst = call("/rest/executions?limit=1")["data"]
items = lst["results"] if isinstance(lst, dict) else lst
ex = call(f"/rest/executions/{items[0]['id']}")["data"]


def unflatten(s):
    arr = json.loads(s)
    seen = {}

    def res(v):
        if isinstance(v, str) and v.isdigit():
            i = int(v)
            if i in seen:
                return seen[i]
            seen[i] = None
            seen[i] = walk(arr[i], i)
            return seen[i]
        return v

    def walk(v, i=None):
        if isinstance(v, dict):
            out = {}
            if i is not None:
                seen[i] = out
            for k, x in v.items():
                out[k] = res(x)
            return out
        if isinstance(v, list):
            out = []
            if i is not None:
                seen[i] = out
            out.extend(res(x) for x in v)
            return out
        return v

    return walk(arr[0], 0)


data = unflatten(ex["data"]) if isinstance(ex.get("data"), str) else ex.get("data")
print(f"execution {ex['id']} status={ex.get('status')} started={ex.get('startedAt')} stopped={ex.get('stoppedAt')}")
run = data["resultData"]["runData"]
for node, runs in run.items():
    if flt and flt not in node:
        continue
    for r in runs:
        print(f"--- {node}: {r.get('executionStatus')} {r.get('executionTime')} ms")
        if r.get("error"):
            print("ERROR:", json.dumps(r["error"], ensure_ascii=False)[:800])
        out = r.get("data", {})
        print(json.dumps(out, ensure_ascii=False)[:1500])
