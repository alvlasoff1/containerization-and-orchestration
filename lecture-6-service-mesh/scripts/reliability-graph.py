#!/usr/bin/env python3
"""График части 4: доля 5xx среди попыток к backend против доли 5xx,
которую видит клиент. Источник — клиентский прокси Linkerd.

usage: reliability-graph.py <reliability.json> <reliability.log> <out.png>
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

data = json.load(open(sys.argv[1]))
marks = {l.split()[0]: int(l.split()[1]) for l in open(sys.argv[2]) if l.strip()}
out = sys.argv[3]

SURFACE, TEXT2, GRID = "#fcfcfb", "#52514e", "#e4e3df"
BACKEND, CLIENT = "#eb6834", "#2a78d6"   # categorical slots 2 and 1

t0 = marks["phaseA_no_retries"]


def series(name):
    res = data[name]
    if not res:
        return [], []
    pts = [(int(float(t)) - t0, float(v)) for t, v in res[0]["values"] if v not in ("NaN", "+Inf")]
    return [p[0] / 60 for p in pts], [p[1] * 100 for p in pts]


plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.labelcolor": TEXT2, "xtick.color": TEXT2, "ytick.color": TEXT2})
fig, ax = plt.subplots(figsize=(12, 5.2), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
for s in ("left", "bottom"):
    ax.spines[s].set_color(GRID)
ax.grid(axis="y", color=GRID, linewidth=0.8)
ax.set_axisbelow(True)

bx, by = series("backend_err")
cx, cy = series("client_err")
ax.plot(bx, by, color=BACKEND, linewidth=2, label="backend: доля 5xx среди попыток")
ax.plot(cx, cy, color=CLIENT, linewidth=2, label="клиент: доля 5xx среди запросов")

tb = (marks["phaseB_retries"] - t0) / 60
end = (marks["end"] - t0) / 60
ax.axvline(tb, color=TEXT2, linewidth=1, linestyle="--")
ax.text(tb / 2, 36, "mesh без retries", ha="center", color=TEXT2)
ax.text((tb + end) / 2, 36, "retries включены (5xx, до 3 повторов)", ha="center", color=TEXT2)

ax.set_xlim(0, end)
ax.set_ylim(0, 40)
ax.set_xlabel("минуты от начала прогона")
ax.set_ylabel("% ответов 5xx")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, frameon=False)
fig.suptitle("FLAKY=true на v1: backend падает в ~30% попыток, клиент после включения retries почти не видит ошибок",
             x=0.06, ha="left", fontsize=12, fontweight="bold")
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
print(out)
