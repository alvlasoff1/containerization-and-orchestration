#!/usr/bin/env python3
"""График непрерывности под хаосом pod'ов.

Вход — логи pod-chaos.sh и client-load.sh:
  client.log     "<ts> <http-code>"           — каждый клиентский запрос
  kills.log      "<ts> <api|worker> <pod> <ip>" — каждое убийство pod'а
  endpoints.log  "<ts> <ip> <ip> ..."         — ready-адреса shop-api раз в секунду

Верхняя панель: запросы клиента в секунду (успешные / неуспешные).
Нижняя панель: какие IP стояли за Service shop-api (каждая строка — один pod),
крестиками — моменты убийства pod'ов api и worker. Ось времени общая.

usage: continuity-graph.py <logdir> <out.png>
"""
import collections
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logdir, out = sys.argv[1], sys.argv[2]

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
GRID = "#e4e3df"
GOOD = "#0ca30c"       # status: good
CRITICAL = "#d03b3b"   # status: critical
API = "#2a78d6"        # categorical slot 1
WORKER = "#eb6834"     # categorical slot 2
LIFELINE = "#8a8984"

client = [l.split() for l in open(f"{logdir}/client.log") if l.strip()]
kills = [l.split() for l in open(f"{logdir}/kills.log") if l.strip()]
endpoints = [l.split() for l in open(f"{logdir}/endpoints.log") if l.strip()]

t0 = int(client[0][0])
ok = collections.Counter()
bad = collections.Counter()
for ts, code in client:
    (ok if code.startswith("2") else bad)[int(ts) - t0] += 1
total = len(client)
failed = sum(bad.values())
secs = range(0, max(ok.keys() | bad.keys()) + 1)

# lifeline для каждого IP: интервалы секунд, когда он был ready за Service
seen = collections.OrderedDict()
for row in endpoints:
    t = int(row[0]) - t0
    for ip in row[1:]:
        seen.setdefault(ip, []).append(t)
ips = list(seen)
api_kills = [(int(k[0]) - t0, k[3]) for k in kills if k[1] == "api"]
worker_kills = [int(k[0]) - t0 for k in kills if k[1] == "worker"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "text.color": TEXT, "axes.labelcolor": TEXT2,
                     "xtick.color": TEXT2, "ytick.color": TEXT2})
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8.5), sharex=True,
                               gridspec_kw={"height_ratios": [1, 1.6], "hspace": 0.12},
                               facecolor=SURFACE)
for ax in (ax1, ax2):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

xs = list(secs)
ax1.bar(xs, [ok[s] for s in xs], width=0.8, color=GOOD, label=f"успешно (2xx): {total - failed}")
ax1.bar(xs, [bad[s] for s in xs], width=0.8, bottom=[ok[s] for s in xs], color=CRITICAL,
        label=f"ошибка (не 2xx / нет ответа): {failed}")
ax1.set_ylabel("запросов клиента / с")
ax1.legend(loc="upper left", ncol=2, frameon=False, bbox_to_anchor=(0, 1.16))
for t in [k for k, _ in api_kills]:
    ax1.axvline(t, color=API, alpha=0.18, linewidth=1)
for t in worker_kills:
    ax1.axvline(t, color=WORKER, alpha=0.18, linewidth=1)

# нижняя панель: lifelines IP за shop-api
for row, ip in enumerate(ips):
    ts = seen[ip]
    start = prev = ts[0]
    for t in ts[1:] + [None]:
        if t is None or t - prev > 2:
            ax2.plot([start, prev + 1], [row, row], color=LIFELINE, linewidth=2,
                     solid_capstyle="round")
            if t is not None:
                start = t
        if t is not None:
            prev = t
ip_row = {ip: i for i, ip in enumerate(ips)}
ax2.scatter([t for t, ip in api_kills if ip in ip_row], [ip_row[ip] for t, ip in api_kills if ip in ip_row],
            marker="X", s=70, color=API, edgecolors=SURFACE, linewidths=1.5, zorder=3,
            label=f"убит pod api: {len(api_kills)}")
ax2.scatter(worker_kills, [-1.6] * len(worker_kills), marker="X", s=70, color=WORKER,
            edgecolors=SURFACE, linewidths=1.5, zorder=3, label=f"убит pod worker: {len(worker_kills)}")
ax2.set_yticks(range(len(ips)))
ax2.set_yticklabels(ips, fontsize=7)
ax2.set_ylim(-2.5, len(ips))
ax2.invert_yaxis()
ax2.set_ylabel(f"ready IP за Service shop-api ({len(ips)} разных)")
ax2.set_xlabel("секунды с начала нагрузки")
ax2.legend(loc="lower left", ncol=2, frameon=False, bbox_to_anchor=(0, -0.2))
ax2.grid(axis="x", color=GRID, linewidth=0.8)

fig.suptitle(f"Клиент: {total} запросов POST /order по DNS-имени shop-api, ошибок: {failed}. "
             f"Убито pod'ов: {len(kills)}", x=0.06, ha="left", fontsize=13, fontweight="bold")
fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
print(out, "requests", total, "failed", failed, "kills", len(kills), "ips", len(ips))
