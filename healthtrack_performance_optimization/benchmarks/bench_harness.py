"""
Benchmark harness for the HealthTrack performance testbed.

Measures, for a given set of (name, method, url) requests:
  - wall-clock latency (min/mean/median/p95/max) over N repeated calls
  - number of SQL statements executed per call (via SQLAlchemy event hooks),
    which is what actually proves an N+1 pattern was fixed, not just "it got faster"
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import statistics
import json
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.database import engine


class QueryCounter:
    """Counts SQL statements executed against the engine during a `with` block."""
    def __init__(self):
        self.count = 0

    def __enter__(self):
        self.count = 0
        event.listen(engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *args):
        event.remove(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1


def bench(client: TestClient, method: str, url: str, n: int = 20, warmup: int = 1):
    """Runs `n` requests (after `warmup` untimed requests) and returns latency stats + query count for the LAST call."""
    for _ in range(warmup):
        client.request(method, url)

    latencies = []
    status = None
    for i in range(n):
        qc = QueryCounter()
        with qc:
            t0 = time.perf_counter()
            resp = client.request(method, url)
            t1 = time.perf_counter()
        status = resp.status_code
        latencies.append((t1 - t0) * 1000)  # ms
        last_query_count = qc.count

    latencies.sort()
    p95_idx = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))

    return {
        "url": url,
        "status": status,
        "n_requests": n,
        "min_ms": round(latencies[0], 2),
        "mean_ms": round(statistics.mean(latencies), 2),
        "median_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(latencies[p95_idx], 2),
        "max_ms": round(latencies[-1], 2),
        "sql_queries_last_call": last_query_count,
    }


def run_suite(app, endpoints, n=20, label=""):
    client = TestClient(app)
    results = []
    print(f"\n=== {label} ===")
    for entry in endpoints:
        if len(entry) == 4:
            name, method, url, endpoint_n = entry
        else:
            name, method, url = entry
            endpoint_n = n
        r = bench(client, method, url, n=endpoint_n)
        r["name"] = name
        results.append(r)
        print(f"{name:44s} | median={r['median_ms']:9.2f}ms  p95={r['p95_ms']:9.2f}ms  "
              f"mean={r['mean_ms']:9.2f}ms  sql_queries={r['sql_queries_last_call']:4d}  status={r['status']}  (n={endpoint_n})")
    return results


if __name__ == "__main__":
    from app.main import app as v1_app

    endpoints = [
        ("List patients (no pagination)", "GET", "/api/v1/patients"),
        ("Patient readings (heavy patient, unindexed)", "GET", "/api/v1/patients/5/readings"),
        ("Dashboard summary (N+1 pattern)", "GET", "/api/v1/dashboard/summary", 3),
        ("Risk report (heavy patient, no cache)", "GET", "/api/v1/patients/5/risk-report"),
    ]

    results = run_suite(v1_app, endpoints, n=20, label="BASELINE (v1, no indexes, no cache, no pagination)")

    os.makedirs(os.path.join(os.path.dirname(__file__), "..", "benchmarks"), exist_ok=True)
    out_path = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "baseline_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {out_path}")
