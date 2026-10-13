import time
import random
import requests
import pandas as pd

# Your live Cloudflare Worker URL
BASE_URL = "https://echocache.ujansarkar7.workers.dev"
ITEMS = ["item_A", "item_B", "item_C", "item_D", "item_E"]
WEIGHTS = [0.50, 0.25, 0.15, 0.07, 0.03] 
results = []

print("Running baseline traffic simulation...")
for step in range(1, 41):
    item = random.choices(ITEMS, weights=WEIGHTS, k=1)[0]
    session = f"session_{step % 5}"
    
    start = time.time()
    res = requests.get(f"{BASE_URL}?item={item}&session={session}")
    latency = (time.time() - start) * 1000
    hit = 1 if "CACHE HIT" in res.text else 0
    
    results.append({"step": step, "item": item, "hit": hit, "latency_ms": latency})
    print(f"[{step}/40] {item} | {'HIT' if hit else 'MISS'} | {latency:.1f}ms")
    time.sleep(0.5)

df = pd.DataFrame(results)
print(f"\nHit Rate: {(df['hit'].sum() / len(df)) * 100:.2f}% | Avg Latency: {df['latency_ms'].mean():.2f} ms")
df.to_csv("benchmark_results.csv", index=False)