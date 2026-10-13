"""
EchoCache A/B benchmark (v2).

Fixes two bugs in v1 that suppressed the effect being measured:
  1. Journeys were regenerated between the warmup and measurement phases,
     so the predictor learned successor pairs for journeys that were never
     walked again. Journeys are now fixed at module level and shared.
  2. Sessions walked their journey back-to-back with a 0.15s gap, which is
     shorter than a pre-warm takes to land. Sessions are now interleaved
     through a pool, so consecutive steps of one session are several
     seconds apart - which is also how real users behave.

Runs the SAME request sequence twice:
  arm=off  reactive caching only             (control)
  arm=on   reactive + predictive pre-warming (treatment)
"""

import random
import time

import pandas as pd
import requests

BASE_URL = "https://echocache.echocache-aws.workers.dev"

N_ITEMS = 200
N_JOURNEYS = 12
JOURNEY_LEN = 4
SESSION_POOL = 6        # concurrent sessions; spaces out steps within a session
JOURNEY_SHARE = 0.65    # fraction of traffic that is journey steps

WARMUP = 80             # trains the predictor; not recorded
MEASURE = 200           # recorded requests per arm
PREDICT_EVERY = 25
DELAY = 0.20
SEED = 42

ITEMS = [f"item_{i:03d}" for i in range(N_ITEMS)]


def zipf_weights(n: int, s: float = 1.1):
    return [1.0 / ((i + 1) ** s) for i in range(n)]


WEIGHTS = zipf_weights(N_ITEMS)

# Fixed once, from a dedicated RNG, so warmup and measurement - and both
# arms - walk the SAME journeys. This was bug 1.
_JRNG = random.Random(SEED + 1)
JOURNEYS = [_JRNG.sample(ITEMS[20:], JOURNEY_LEN) for _ in range(N_JOURNEYS)]


def make_sequence(rng: random.Random, n: int, id_offset: int):
    """Interleaves several active sessions, so consecutive requests belong to
    different sessions and a pre-warm has time to land before the successor
    is asked for. This was bug 2."""
    seq = []
    active = []
    counter = id_offset

    while len(seq) < n:
        if rng.random() < JOURNEY_SHARE:
            while len(active) < SESSION_POOL:
                counter += 1
                active.append({"sid": f"s{counter}", "j": rng.choice(JOURNEYS), "i": 0})
            k = rng.randrange(len(active))
            s = active[k]
            seq.append((s["sid"], s["j"][s["i"]]))
            s["i"] += 1
            if s["i"] >= JOURNEY_LEN:
                active.pop(k)
        else:
            counter += 1
            item = rng.choices(ITEMS, weights=WEIGHTS, k=1)[0]
            seq.append((f"bg{counter}", item))

    return seq[:n]


def run_arm(arm: str, session: requests.Session):
    rng = random.Random(SEED)
    warm = make_sequence(rng, WARMUP, id_offset=0)
    meas = make_sequence(rng, MEASURE, id_offset=100_000)

    print(f"\n=== arm={arm} : warmup ({WARMUP} requests) ===")
    for i, (sid, item) in enumerate(warm, 1):
        session.get(BASE_URL, params={"item": item, "session": sid, "arm": arm})
        if arm == "on" and i % PREDICT_EVERY == 0:
            session.get(f"{BASE_URL}/admin/predict", params={"arm": arm})
        time.sleep(DELAY)

    if arm == "on":
        r = session.get(f"{BASE_URL}/admin/predict", params={"arm": arm})
        try:
            print(f"  model after warmup: {r.json().get('pairs')} successor rules")
        except Exception:
            print(f"  predictor response: {r.text[:200]}")

    print(f"=== arm={arm} : measuring ({MEASURE} requests) ===")
    rows = []
    for i, (sid, item) in enumerate(meas, 1):
        start = time.perf_counter()
        res = session.get(BASE_URL, params={"item": item, "session": sid, "arm": arm})
        latency = (time.perf_counter() - start) * 1000

        source = res.headers.get("X-Cache", "UNKNOWN")
        rows.append(
            {
                "arm": arm,
                "step": i,
                "item": item,
                "session": sid,
                "source": source,
                "hit": 0 if source == "MISS" else 1,
                "latency_ms": latency,
            }
        )

        if i % 50 == 0:
            hr = sum(r["hit"] for r in rows) / len(rows) * 100
            pw = sum(1 for r in rows if r["source"] == "PREWARM")
            print(f"  [{i}/{MEASURE}] hit rate {hr:.1f}%  prewarm hits {pw}")

        if arm == "on" and i % PREDICT_EVERY == 0:
            session.get(f"{BASE_URL}/admin/predict", params={"arm": arm})

        time.sleep(DELAY)

    return rows


def main():
    session = requests.Session()

    print("Seeding origin store and clearing old logs...")
    session.get(f"{BASE_URL}/admin/seed", params={"n": N_ITEMS})
    session.get(f"{BASE_URL}/admin/reset")

    rows = []
    rows += run_arm("off", session)

    print("\nWaiting 70s for cached entries to expire before the treatment arm...")
    time.sleep(70)

    rows += run_arm("on", session)

    df = pd.DataFrame(rows)
    df.to_csv("benchmark_results.csv", index=False)

    print("\n================ RESULTS ================")
    for arm in ("off", "on"):
        d = df[df["arm"] == arm]
        print(
            f"arm={arm:3}  hit rate {d['hit'].mean() * 100:5.1f}%   "
            f"mean latency {d['latency_ms'].mean():6.1f} ms   "
            f"p95 {d['latency_ms'].quantile(0.95):6.1f} ms"
        )

    print("\nBreakdown by source:")
    print(df.groupby(["arm", "source"]).size().unstack(fill_value=0))

    # Is the difference real, or noise?
    try:
        import math
        from statistics import NormalDist

        a = df[df["arm"] == "off"]["hit"]
        b = df[df["arm"] == "on"]["hit"]
        p = (a.sum() + b.sum()) / (len(a) + len(b))
        se = math.sqrt(p * (1 - p) * (1 / len(a) + 1 / len(b)))
        diff = b.mean() - a.mean()
        z = diff / se if se else 0.0
        pv = 2 * (1 - NormalDist().cdf(abs(z)))
        print(
            f"\nDifference {diff * 100:+.1f} percentage points, "
            f"z={z:.2f}, p={pv:.3f}, "
            f"95% CI [{(diff - 1.96 * se) * 100:+.1f}, {(diff + 1.96 * se) * 100:+.1f}] pp"
        )
        if pv >= 0.05:
            print("NOT statistically significant - do not report this as a gain.")
    except Exception as exc:
        print(f"(significance test skipped: {exc})")

    print("\nSaved to benchmark_results.csv")


if __name__ == "__main__":
    main()
