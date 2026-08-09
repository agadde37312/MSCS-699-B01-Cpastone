import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import warnings
warnings.filterwarnings("ignore")

from benchmarks.bench_harness import run_suite
from app.main import app

HEAVY_PATIENT = 5  # one of the 20 patients seeded with 15x the normal reading count

v1_endpoints = [
    ("v1: List patients (no pagination)", "GET", "/api/v1/patients"),
    ("v1: Patient readings (heavy patient)", "GET", f"/api/v1/patients/{HEAVY_PATIENT}/readings"),
    ("v1: Dashboard summary (N+1)", "GET", "/api/v1/dashboard/summary", 3),
    ("v1: Risk report (heavy patient)", "GET", f"/api/v1/patients/{HEAVY_PATIENT}/risk-report"),
]

v2_endpoints = [
    ("v2: List patients (paginated)", "GET", "/api/v2/patients?limit=50"),
    ("v2: Patient readings (paginated)", "GET", f"/api/v2/patients/{HEAVY_PATIENT}/readings?limit=50"),
    ("v2: Dashboard summary (aggregated SQL)", "GET", "/api/v2/dashboard/summary"),
    ("v2: Risk report, 1st call (cache MISS)", "GET", f"/api/v2/patients/{HEAVY_PATIENT}/risk-report"),
    ("v2: Risk report, repeat call (cache HIT)", "GET", f"/api/v2/patients/{HEAVY_PATIENT}/risk-report"),
]

print("Indexes are already applied at this point (via app.migrations.add_indexes).")
print("This run shows: v1 routes WITH indexes (isolates index-only impact), then v2 (fully optimized).")

results_v1_with_index = run_suite(app, v1_endpoints, n=20, label="v1 ROUTES + INDEXES (index-only impact)")
results_v2 = run_suite(app, v2_endpoints, n=20, label="v2 ROUTES + INDEXES + N+1 FIX + PAGINATION + CACHE (fully optimized)")

all_results = {
    "v1_with_indexes": results_v1_with_index,
    "v2_fully_optimized": results_v2,
}

out_path = os.path.join(os.path.dirname(__file__), "optimized_results.json")
with open(out_path, "w") as f:
    json.dump(all_results, f, indent=2)
print(f"\nSaved to {out_path}")
