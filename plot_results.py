"""
Plots the A/B result from benchmark_results.csv.

Refuses to plot a run that contains broken responses, because a chart drawn
from those looks clean and says something false.

Produces:
  echocache_hitrate.png   cumulative hit rate, both arms   <- the headline chart
  echocache_served.png    how each request was served
"""

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e1"
CONTROL = "#2a78d6"   # validated categorical slot 1
TREAT = "#eb6834"     # validated categorical slot 2

df = pd.read_csv("benchmark_results.csv")

# ---- refuse to plot a corrupted run -------------------------------------
bad = df["hit"].isna().sum() if "hit" in df else len(df)
if "source" in df:
    bad = max(bad, (df["source"] == "UNKNOWN").sum())

if bad:
    print(f"REFUSING TO PLOT: {bad} of {len(df)} rows have no valid cache status.")
    print("The Worker was erroring during this run (usually an exhausted KV")
    print("write quota). Re-run the benchmark; do not plot this data.")
    sys.exit(1)

df = df[df["source"] != "UNKNOWN"].copy()
df["hit"] = df["hit"].astype(int)


def frame(ax, title, subtitle, ylabel):
    ax.set_facecolor(SURFACE)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#cfcec9")
    ax.tick_params(axis="both", length=0, colors=INK2)
    ax.set_ylabel(ylabel, fontsize=11, color=INK2)
    ax.figure.text(0.105, 0.945, title, fontsize=13.5, color=INK)
    if subtitle:
        ax.figure.text(0.105, 0.896, subtitle, fontsize=9.5, color=INK2)


# ---- 1. cumulative hit rate ---------------------------------------------
summary = []
for arm in ("off", "on"):
    d = df[df["arm"] == arm]
    if not d.empty:
        summary.append(f"{arm}: {d['hit'].mean() * 100:.1f}%")

fig, ax = plt.subplots(figsize=(8.6, 4.8), facecolor=SURFACE)

for arm, colour, label in [("off", CONTROL, "Reactive only (control)"),
                           ("on", TREAT, "With pre-warming (treatment)")]:
    d = df[df["arm"] == arm].sort_values("step")
    if d.empty:
        continue
    cumulative = d["hit"].expanding().mean() * 100
    ax.plot(d["step"], cumulative, color=colour, linewidth=2, label=label)
    # direct label at the line end, so identity is never colour-alone
    ax.annotate(f"{cumulative.iloc[-1]:.1f}%",
                xy=(d["step"].iloc[-1], cumulative.iloc[-1]),
                xytext=(6, 0), textcoords="offset points",
                fontsize=10, color=INK2, va="center")

frame(ax, "Cumulative cache hit rate", "  ·  ".join(summary),
      "Hit rate (%)")
ax.set_xlabel("Request number", fontsize=11, color=INK2)
ax.legend(frameon=False, fontsize=10, loc="lower right", labelcolor=INK2)
fig.tight_layout(rect=[0, 0, 1, 0.865])
fig.savefig("echocache_hitrate.png", dpi=200, facecolor=SURFACE)
print("saved echocache_hitrate.png")

# ---- 2. how each request was served -------------------------------------
cats = ["MISS", "REACTIVE", "PREWARM"]
counts = {
    arm: [int((df[(df["arm"] == arm) & (df["source"] == c)]).shape[0]) for c in cats]
    for arm in ("off", "on")
}

x = np.arange(len(cats))
w = 0.38
fig2, ax2 = plt.subplots(figsize=(8.2, 4.6), facecolor=SURFACE)

bars = [
    ax2.bar(x - w / 2 - 0.012, counts["off"], w, color=CONTROL,
            label="Reactive only (control)"),
    ax2.bar(x + w / 2 + 0.012, counts["on"], w, color=TREAT,
            label="With pre-warming (treatment)"),
]
for group in bars:
    for bar in group:
        h = bar.get_height()
        ax2.text(bar.get_x() + bar.get_width() / 2, h + max(counts["off"] + counts["on"]) * 0.015,
                 str(int(h)), ha="center", va="bottom", fontsize=10, color=INK2)

ax2.set_xticks(x)
ax2.set_xticklabels(["MISS", "REACTIVE hit", "PREWARM hit"], fontsize=11, color=INK)
frame(ax2, "How each request was served", None, f"Requests (of {len(df[df['arm'] == 'off'])})")
ax2.legend(frameon=False, fontsize=10, loc="upper right", labelcolor=INK2)
fig2.tight_layout(rect=[0, 0, 1, 0.9])
fig2.savefig("echocache_served.png", dpi=200, facecolor=SURFACE)
print("saved echocache_served.png")

# ---- numbers for the report ---------------------------------------------
print("\nFor the report:")
for arm in ("off", "on"):
    d = df[df["arm"] == arm]
    if d.empty:
        continue
    print(f"  arm={arm:3} n={len(d):4}  hit rate {d['hit'].mean() * 100:5.1f}%  "
          f"mean {d['latency_ms'].mean():6.1f}ms  p95 {d['latency_ms'].quantile(0.95):6.1f}ms")

on = df[df["arm"] == "on"]
if not on.empty:
    pw = (on["source"] == "PREWARM").sum()
    print(f"\n  pre-warmed hits: {pw} ({pw / len(on) * 100:.1f}% of treatment requests)")
