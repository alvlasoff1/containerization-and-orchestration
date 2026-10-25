#!/usr/bin/env python3
"""Сравнение базовой линии (без mesh) и mesh по результатам measure.sh.
usage: cost-graph.py <results_dir> <out.png>
"""
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

root, out = sys.argv[1], sys.argv[2]


def load(name):
    lat = {}
    for l in open(f"{root}/{name}/summary.txt"):
        m = re.match(r"latency (p\d+) ([\d.]+) ms", l)
        if m:
            lat[m[1]] = float(m[2])
    res, section = {"cpu": {}, "mem": {}}, None
    for l in open(f"{root}/{name}/resources.txt"):
        if l.startswith("# CPU"):
            section = "cpu"
        elif l.startswith("# Memory"):
            section = "mem"
        elif l.strip():
            c, v = l.split()
            res[section][c] = float(v)
    return lat, res


bl, br = load("baseline")
ml, mr = load("mesh")
SURFACE, TEXT2, GRID = "#fcfcfb", "#52514e", "#e4e3df"
BASE, APP, PROXY = "#8a8984", "#2a78d6", "#eb6834"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.labelcolor": TEXT2, "xtick.color": TEXT2, "ytick.color": TEXT2})
fig, axes = plt.subplots(1, 3, figsize=(13, 4.6), facecolor=SURFACE, gridspec_kw={"wspace": 0.35})
for ax in axes:
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

ax = axes[0]
ps = ["p50", "p90", "p99"]
x = range(len(ps))
w = 0.38
b1 = ax.bar([i - w / 2 for i in x], [bl[p] for p in ps], w, color=BASE, label="без mesh")
b2 = ax.bar([i + w / 2 for i in x], [ml[p] for p in ps], w, color=APP, label="с mesh")
ax.bar_label(b1, fmt="%.1f", color=TEXT2, fontsize=9)
ax.bar_label(b2, fmt="%.1f", color=TEXT2, fontsize=9)
ax.set_xticks(list(x), ps)
ax.set_title("Латентность POST /order, мс", loc="left", fontsize=11)
ax.legend(frameon=False, loc="upper left")


def stacked(ax, key, title, fmt):
    base = br[key].get("api", 0)
    app, proxy = mr[key].get("api", 0), mr[key].get("linkerd-proxy", 0)
    ax.bar([0], [base], 0.55, color=BASE, label="api (без mesh)")
    ax.bar([1], [app], 0.55, color=APP, label="api (с mesh)")
    ax.bar([1], [proxy], 0.55, bottom=[app], color=PROXY, label="linkerd-proxy")
    ax.text(0, base, fmt % base, ha="center", va="bottom", color=TEXT2, fontsize=9)
    ax.text(1, app + proxy, fmt % (app + proxy), ha="center", va="bottom", color=TEXT2, fontsize=9)
    ax.set_xticks([0, 1], ["без mesh", "с mesh"])
    ax.set_title(title, loc="left", fontsize=11)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    ax.set_ylim(0, (app + proxy) * 1.35)


stacked(axes[1], "cpu", "CPU двух pod'ов api, millicores", "%.0f")
stacked(axes[2], "mem", "Память двух pod'ов api, MiB", "%.1f")
fig.suptitle("Цена mesh при 100 rps: базовая линия и Linkerd", x=0.06, ha="left", fontsize=12, fontweight="bold")
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
print(out)
