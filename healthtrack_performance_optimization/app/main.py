from fastapi import FastAPI
from app.routes_v1_baseline import router as v1_router
from app.routes_v2_optimized import router as v2_router

app = FastAPI(title="HealthTrack Performance Testbed")
app.include_router(v1_router)
app.include_router(v2_router)


@app.get("/health")
def health():
    return {"status": "ok"}
