# HealthTrack Performance Optimization

## Scope note

The original HealthTrack backend/dashboard project files from earlier development
phases were not present in this environment, so a representative reconstruction of
the backend (FastAPI + SQLAlchemy, matching the documented architecture: Patient,
VitalsReading, RiskReport entities, reusing the risk-scoring engine from the prior
capstone phase) was built specifically for this performance evaluation. It was seeded
with 182,400 vitals readings across 2,000 patients so that performance differences are
large enough to measure honestly rather than lost in noise on a tiny dataset. Every
number in the four documents below is a real, measured result — not an estimate — and
can be independently reproduced with the scripts in `benchmarks/`.

## Deliverables

| File | What it is |
|---|---|
| `docs/Performance_Analysis_Report.docx` | Bottleneck analysis, resource usage, scalability findings (methodology + evidence) |
| `docs/Benchmark_Results.docx` | Full before/after numbers, isolated per-optimization impact, charts |
| `docs/Recommendations.docx` | Prioritized future improvements not yet implemented (Redis, Postgres, multi-worker, etc.) |
| `docs/Technical_Documentation.docx` | How each optimization works, project structure, how to run/reproduce everything |

## What was optimized

1. **Database queries** — added a composite index (`vitals_readings(patient_id, timestamp)`)
   confirmed via `EXPLAIN QUERY PLAN` (SCAN → SEARCH USING INDEX); eliminated an N+1 query
   pattern in the dashboard summary endpoint (2,001 queries → 2, ~184x latency improvement).
2. **API performance** — added pagination and lightweight (field-trimmed) responses to
   list endpoints; bounded the risk-report trend calculation to a fixed recent window
   instead of a patient's entire history.
3. **Caching** — an in-memory TTL cache for computed risk reports, keyed so the cache key
   itself changes the moment new data arrives (no manual invalidation to get wrong); HTTP
   `Cache-Control` headers on cacheable responses.
4. **Frontend** — real minification (rjsmin/rcssmin) and gzip measurement of dashboard
   assets (78.5% combined size reduction), plus IntersectionObserver-based lazy loading
   so only 1 of 6 dashboard charts renders on initial page load.

## Headline results (real, measured)

| Metric | Before | After | Improvement |
|---|---|---|---|
| Dashboard summary (median latency) | 22,352 ms | 121 ms | ~184x |
| Dashboard summary (SQL queries) | 2,001 | 2 | — |
| List/readings endpoints (median latency) | ~51-53 ms | ~4.3-4.5 ms | ~12x |
| Risk report throughput @ 20 concurrent | 30.7 req/s | 238.2 req/s | ~7.8x |
| Frontend JS+CSS payload (minified+gzip) | 7,003 B | 1,506 B | -78.5% |

See `docs/Benchmark_Results.docx` for the complete numbers, including the honest finding
that the dashboard endpoint's throughput plateaus at ~7.6 req/s regardless of concurrency
on a single worker process — a real scalability ceiling addressed in the Recommendations
document, not hidden from this report.

## Project layout

```
app/                    FastAPI backend (v1 baseline + v2 optimized routes, side by side)
benchmarks/              All benchmark scripts + their raw JSON output
docs/                    The four deliverable documents + source charts
frontend_demo/           Before/after dashboard HTML/CSS/JS
```

## Quick start

```bash
pip install fastapi uvicorn sqlalchemy httpx psutil rjsmin rcssmin
python3 -m app.seed                    # regenerate the seeded database
python3 -m app.migrations.add_indexes  # apply the index migration
uvicorn app.main:app --reload          # v1 at /api/v1/..., v2 at /api/v2/...

# Reproduce every benchmark in the reports:
python3 benchmarks/bench_harness.py
python3 benchmarks/bench_optimized.py
python3 benchmarks/bench_cache.py
python3 benchmarks/bench_load.py
```
