from __future__ import annotations

import logging
import time
import traceback
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import cache as cache_module
from app import database as db_module
from app.config import settings
from app.routers import documents, health, search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: create DB pool + Redis client and run migrations. Shutdown: close both."""
    logger.info("Starting up — env=%s", settings.app_env)

    pool = await db_module.create_pool(settings.database_url)
    await db_module.init_db(pool)
    await cache_module.create_client(settings.redis_url)

    yield

    await db_module.close_pool()
    await cache_module.close_client()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Distributed Document Search Service",
    version="1.0.0",
    description="Multi-tenant document ingestion and full-text search powered by PostgreSQL and Redis.",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Request / response logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.monotonic()
    tenant_id = request.headers.get("x-tenant-id", "-")
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        status_code = response.status_code if response is not None else 500
        logger.info(
            "tenant=%s method=%s path=%s status=%s duration_ms=%s",
            tenant_id,
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )


# ---------------------------------------------------------------------------
# Global exception handler — never expose internal details
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s:\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"error": "An internal server error occurred", "code": "INTERNAL_ERROR"},
    )


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(search.router)
