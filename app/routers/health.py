from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.cache import get_client as get_redis
from app.database import get_pool
from app.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> JSONResponse:
    """
    Liveness and readiness probe.

    Checks connectivity to both Postgres and Redis.
    Returns 200 when both are healthy, 503 when either is degraded.
    """
    postgres_status = "ok"
    redis_status = "ok"
    http_status = 200

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except Exception as exc:
        logger.error("Health check — Postgres failure: %s", exc)
        postgres_status = "error"
        http_status = 503

    try:
        client = get_redis()
        await client.ping()
    except Exception as exc:
        logger.error("Health check — Redis failure: %s", exc)
        redis_status = "error"
        http_status = 503

    body = HealthResponse(
        status="healthy" if http_status == 200 else "degraded",
        postgres=postgres_status,
        redis=redis_status,
        timestamp=datetime.now(tz=timezone.utc),
    )
    return JSONResponse(content=body.model_dump(mode="json"), status_code=http_status)
