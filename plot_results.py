"""
Plots the A/B result. Two figures:

  echocache_hitrate.png  cumulative hit rate, both arms  <- the headline chart
  echocache_latency.png  latency distribution by why the request was fast
"""

import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv("benchmark_results.csv")

OFF = "#6E7681"   # control  - grey
ON = "#B3452A"    # treatment - brick

# ---------------------------------------------------------------- hit rate
fig, ax = plt.subplots(figsize=(9, 5))

for arm, colour, label in [("off", OFF, "Reactive only (control)"),
                           ("on", ON, "With pre-warming (treatment)")]:
    d = df[df["arm"] == arm].sort_values("step")
    if d.empty:
        continue
    cumulative = d["hit"].expanding().mean() * 100
    ax.plot(d["step"], cumulative, color=colour, linewidth=2, label=label)

ax.set_xlabel("Request number")
ax.set_ylabel("Cumulative cache hit rate (%)")
ax.set_title("EchoCache: hit rate with and without predictive pre-warming")
ax.grid(True, linestyle="--", alpha=0.4)
ax.legend(frameon=False)
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig("echocache_hitrate.png", dpi=300)
print("saved echocache_hitrate.png")

# ---------------------------------------------------------------- latency
fig2, ax2 = plt.subplots(figsize=(9, 5))

order = ["MISS", "REACTIVE", "PREWARM"]
present = [s for s in order if s in df["source"].unique()]
data = [df[df["source"] == s]["latency_ms"].values for s in present]

bp = ax2.boxplot(data, labels=present, patch_artist=True, widths=0.5)
for patch, colour in zip(bp["boxes"], ["#C9CCC8", OFF, ON]):
    patch.set_facecolor(colour)
    patch.set_alpha(0.75)
for median in bp["medians"]:
    median.set_color("black")

ax2.set_ylabel("Latency (ms)")
ax2.set_xlabel("Why the request was served that way")
ax2.set_title("EchoCache: request latency by cache outcome")
ax2.grid(True, axis="y", linestyle="--", alpha=0.4)
ax2.spines[["top", "right"]].set_visible(False)
fig2.tight_layout()
fig2.savefig("echocache_latency.png", dpi=300)
print("saved echocache_latency.png")

# ---------------------------------------------------------------- numbers
print("\nFor the report:")
for arm in ("off", "on"):
    d = df[df["arm"] == arm]
    if d.empty:
        continue
    print(f"  arm={arm:3} n={len(d):4}  hit rate {d['hit'].mean()*100:5.1f}%  "
          f"mean {d['latency_ms'].mean():6.1f}ms  p95 {d['latency_ms'].quantile(0.95):6.1f}ms")

on = df[df["arm"] == "on"]
if not on.empty:
    prewarm_hits = (on["source"] == "PREWARM").sum()
    print(f"\n  pre-warmed hits in treatment arm: {prewarm_hits} "
          f"({prewarm_hits/len(on)*100:.1f}% of requests)")
    print("  divide this by the prewarm_log row count for precision "
          "(query /admin/stats or the D1 table)")
