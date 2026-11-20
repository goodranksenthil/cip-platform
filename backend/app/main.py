"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import ingestion, platform, smart_intake, transactions
from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.security.phi_guard import PHIViolation
from app.services import sla
from app.workers import WorkerThread

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        sla.ensure_policies(db)
    if settings.seed_demo_data:
        from app.seed import seed_if_empty

        seed_if_empty()
    worker = WorkerThread() if settings.workers_enabled else None
    if worker:
        worker.start()
    yield
    if worker:
        worker.stop()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
              description="Clinical Intake Platform — integrity-first document ingestion, "
                          "transactional outbox, PHI-safe events and AI guard rails.")

app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_origin_regex=settings.cors_origin_regex or None, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"  # responses may contain PHI
    return response


@app.exception_handler(PHIViolation)
async def phi_violation_handler(_: Request, exc: PHIViolation):
    return JSONResponse(status_code=422, content={"detail": {"message": str(exc), "problems": exc.problems}})


app.include_router(ingestion.router)
app.include_router(smart_intake.router)
app.include_router(transactions.router)
app.include_router(platform.router)


# ---------------------------------------------------------------------------------------
# Serve the built React app (frontend/dist) from the same origin when present, so the
# portable Windows bundle needs no Node.js. In development Vite serves it instead.
# ---------------------------------------------------------------------------------------
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (DIST / full_path).resolve()
        if full_path and candidate.is_file() and DIST in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
