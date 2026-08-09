import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import statistics
import warnings
warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient
from app.main import app
from app.cache import risk_report_cache

client = TestClient(app)
HEAVY_PATIENT = 5

# --- True cache MISS timings: clear the specific key before every call ---
miss_latencies = []
for _ in range(15):
    risk_report_cache._store.clear()  # force every call to be a genuine miss
    t0 = time.perf_counter()
    r = client.get(f"/api/v2/patients/{HEAVY_PATIENT}/risk-report")
    t1 = time.perf_counter()
    assert r.headers.get("x-cache") == "MISS", r.headers
    miss_latencies.append((t1 - t0) * 1000)

# --- True cache HIT timings: prime once, then repeat without clearing ---
client.get(f"/api/v2/patients/{HEAVY_PATIENT}/risk-report")  # prime
hit_latencies = []
for _ in range(15):
    t0 = time.perf_counter()
    r = client.get(f"/api/v2/patients/{HEAVY_PATIENT}/risk-report")
    t1 = time.perf_counter()
    assert r.headers.get("x-cache") == "HIT", r.headers
    hit_latencies.append((t1 - t0) * 1000)

print(f"Cache MISS -- mean: {statistics.mean(miss_latencies):.2f}ms  median: {statistics.median(miss_latencies):.2f}ms")
print(f"Cache HIT  -- mean: {statistics.mean(hit_latencies):.2f}ms  median: {statistics.median(hit_latencies):.2f}ms")
print(f"Speedup from cache hit: {statistics.mean(miss_latencies) / statistics.mean(hit_latencies):.1f}x")

import json
with open(os.path.join(os.path.dirname(__file__), "cache_results.json"), "w") as f:
    json.dump({
        "miss_mean_ms": round(statistics.mean(miss_latencies), 3),
        "miss_median_ms": round(statistics.median(miss_latencies), 3),
        "hit_mean_ms": round(statistics.mean(hit_latencies), 3),
        "hit_median_ms": round(statistics.median(hit_latencies), 3),
        "speedup_x": round(statistics.mean(miss_latencies) / statistics.mean(hit_latencies), 2),
    }, f, indent=2)
