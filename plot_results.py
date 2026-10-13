import pandas as pd
import matplotlib.pyplot as plt

# Load the benchmark data
df = pd.read_csv("benchmark_results.csv")

# Set up the graph
plt.figure(figsize=(10, 5))
plt.plot(df["step"], df["latency_ms"], marker='o', linestyle='-', color='dodgerblue', label="Request Latency")

# Highlight the cache misses in red
misses = df[df["hit"] == 0]
plt.scatter(misses["step"], misses["latency_ms"], color='red', zorder=5, label="Cache Miss (Slow)", s=80)

# Formatting
plt.title("Echocache Performance: Latency & Prediction Accuracy")
plt.xlabel("Request Number")
plt.ylabel("Latency (ms)")
plt.axhline(y=df["latency_ms"].mean(), color='green', linestyle='--', label=f"Avg Latency: {df['latency_ms'].mean():.1f}ms")

plt.legend()
plt.grid(True, linestyle='--', alpha=0.5)

# Save the image for your PPT
plt.savefig("latency_chart.png", dpi=300, bbox_inches='tight')
print("✅ Graph successfully saved as 'latency_chart.png'")
plt.show()