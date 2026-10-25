#!/usr/bin/env python3
"""Графики части 7: как менялись доля трафика v2 и ошибки во время canary.

Верх: вес v2 в HTTPRoute (решения gate из его лога) и фактическая доля
запросов, пришедших на v2 (inbound-прокси). Низ: доля ошибок v2 и доля
ошибок, которую видит клиент.

usage: canary-graph.py <canary.json> <gate.log> <title> <out.png>
"""
import datetime as dt
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

data = json.load(open(sys.argv[1]))
gate = [json.loads(l) for l in open(sys.argv[2]) if l.startswith("{")]
title, out = sys.argv[3], sys.argv[4]

SURFACE, TEXT2, GRID = "#fcfcfb", "#52514e", "#e4e3df"
WEIGHT, SHARE = "#8a8984", "#2a78d6"
V2ERR, CLIENT = "#d03b3b", "#1baf7a"


def series(name):
    res = data.get(name) or []
    if not res:
        return [], []
    pts = [(int(float(t)), float(v)) for t, v in res[0]["values"] if v not in ("NaN", "+Inf")]
    return [p[0] for p in pts], [p[1] * 100 for p in pts]


# время из лога gate (HH:MM:SS UTC) -> unix, по дате первой точки Prometheus
first = min(int(float(r["values"][0][0])) for v in data.values() for r in v[:1])
day = dt.datetime.fromtimestamp(first, dt.timezone.utc).date()
def to_unix(hms):
    t = dt.datetime.strptime(hms, "%H:%M:%S").time()
    return int(dt.datetime.combine(day, t, dt.timezone.utc).timestamp())

t0 = to_unix(gate[0]["ts"])
gx, gy = [0], [gate[1]["weight"] if len(gate) > 1 else 10]
for e in gate:
    if "new_weight" in e:
        gx.append(to_unix(e["ts"]) - t0); gy.append(e["new_weight"])
end = max(max(series(n)[0] or [t0]) for n in data) - t0
gx.append(end); gy.append(gy[-1])

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.labelcolor": TEXT2, "xtick.color": TEXT2, "ytick.color": TEXT2})
fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True, facecolor=SURFACE,
                             gridspec_kw={"hspace": 0.35})
for ax in (a1, a2):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_ylim(0, 105)

a1.step(gx, gy, where="post", color=WEIGHT, linewidth=2, linestyle="--", label="вес v2 в HTTPRoute (решение gate)")
x, y = series("v2_share")
a1.plot([v - t0 for v in x], y, color=SHARE, linewidth=2, marker="o", markersize=4, label="фактическая доля запросов на v2")
a1.set_ylabel("% трафика")
a1.set_title("Доля трафика v2", loc="left", fontsize=11)
a1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.08), ncol=2, frameon=False)

x, y = series("v2_err")
a2.plot([v - t0 for v in x], y, color=V2ERR, linewidth=2, marker="o", markersize=4, label="ошибки v2")
x, y = series("client_err")
a2.plot([v - t0 for v in x], y, color=CLIENT, linewidth=2, label="ошибки, которые видит клиент")
a2.set_ylabel("% ответов с ошибкой")
a2.set_xlabel("секунды от старта gate")
a2.set_title("Ошибки", loc="left", fontsize=11)
a2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=False)

for e in gate:
    if e["event"] in ("rollback", "done"):
        t = to_unix(e["ts"]) - t0
        for ax in (a1, a2):
            ax.axvline(t, color=TEXT2, linewidth=1, linestyle=":")
        a1.annotate("откат v2 в 0" if e["event"] == "rollback" else "v2 = 100%",
                    (t, 102), xytext=(5, 0), textcoords="offset points", color=TEXT2, va="top")

a2.set_xlim(0, end)
fig.suptitle(title, x=0.06, ha="left", fontsize=12, fontweight="bold")
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
print(out)
