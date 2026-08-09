"""
High-load / scalability test: starts a real uvicorn server process, fires
concurrent requests at it with httpx.AsyncClient + asyncio.gather (not the
in-process TestClient, which bypasses networking and thread pool behavior),
and samples the server process's CPU/RSS memory with psutil while the load
runs. This is what the "evaluate scalability" and "high-load conditions"
requirements actually need -- sequential single-request timing alone doesn't
show how the system behaves when many users hit it at once.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import subprocess
import time
import asyncio
import statistics
import json
import psutil
import httpx

HOST = "127.0.0.1"
PORT = 8931
BASE_URL = f"http://{HOST}:{PORT}"


def start_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=1)
            if r.status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("server did not start in time")


async def fire_concurrent(url, n_concurrent):
    async with httpx.AsyncClient(timeout=60) as client:
        async def one():
            t0 = time.perf_counter()
            r = await client.get(f"{BASE_URL}{url}")
            t1 = time.perf_counter()
            return (t1 - t0) * 1000, r.status_code

        results = await asyncio.gather(*[one() for _ in range(n_concurrent)])
    return results


def monitor_and_run(proc, url, n_concurrent, label):
    ps_proc = psutil.Process(proc.pid)
    ps_proc.cpu_percent()  # prime the counter
    cpu_samples, mem_samples = [], []

    async def run_with_monitoring():
        monitor_task = asyncio.create_task(sample_resources(ps_proc, cpu_samples, mem_samples))
        t0 = time.perf_counter()
        results = await fire_concurrent(url, n_concurrent)
        wall_time = time.perf_counter() - t0
        monitor_task.cancel()
        return results, wall_time

    async def sample_resources(ps_proc, cpu_samples, mem_samples):
        try:
            while True:
                cpu_samples.append(ps_proc.cpu_percent())
                mem_samples.append(ps_proc.memory_info().rss / (1024 * 1024))
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass

    results, wall_time = asyncio.run(run_with_monitoring())
    latencies = sorted(r[0] for r in results)
    statuses = [r[1] for r in results]
    p95_idx = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))

    summary = {
        "label": label,
        "url": url,
        "n_concurrent": n_concurrent,
        "wall_time_s": round(wall_time, 3),
        "throughput_rps": round(n_concurrent / wall_time, 2),
        "latency_min_ms": round(latencies[0], 2),
        "latency_median_ms": round(statistics.median(latencies), 2),
        "latency_p95_ms": round(latencies[p95_idx], 2),
        "latency_max_ms": round(latencies[-1], 2),
        "all_200": all(s == 200 for s in statuses),
        "peak_cpu_percent": round(max(cpu_samples), 1) if cpu_samples else None,
        "peak_rss_mb": round(max(mem_samples), 1) if mem_samples else None,
    }
    print(f"{label:55s} | n={n_concurrent:3d} throughput={summary['throughput_rps']:7.1f} req/s  "
          f"p50={summary['latency_median_ms']:9.1f}ms  p95={summary['latency_p95_ms']:9.1f}ms  "
          f"peak_cpu={summary['peak_cpu_percent']}%  peak_rss={summary['peak_rss_mb']}MB")
    return summary


if __name__ == "__main__":
    proc = start_server()
    try:
        results = []
        # Dashboard summary under load -- v1 (N+1) can only survive a small
        # burst before this becomes impractical, so it uses fewer concurrent
        # requests than v2; that gap is itself part of the scalability finding.
        results.append(monitor_and_run(proc, "/api/v1/dashboard/summary", 5, "v1 dashboard/summary (N+1) @ 5 concurrent"))
        results.append(monitor_and_run(proc, "/api/v2/dashboard/summary", 5, "v2 dashboard/summary (optimized) @ 5 concurrent"))
        results.append(monitor_and_run(proc, "/api/v2/dashboard/summary", 50, "v2 dashboard/summary (optimized) @ 50 concurrent"))

        results.append(monitor_and_run(proc, "/api/v1/patients/5/risk-report", 20, "v1 risk-report (no cache) @ 20 concurrent"))
        results.append(monitor_and_run(proc, "/api/v2/patients/5/risk-report", 20, "v2 risk-report (cached) @ 20 concurrent"))
        results.append(monitor_and_run(proc, "/api/v2/patients/5/risk-report", 100, "v2 risk-report (cached) @ 100 concurrent"))

        out_path = os.path.join(os.path.dirname(__file__), "load_test_results.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved to {out_path}")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
