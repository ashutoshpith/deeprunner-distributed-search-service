import time
import logging
from fastapi import Header, HTTPException, Request

from app.cache import get_client
from app.config import settings

logger = logging.getLogger(__name__)


async def rate_limit_dependency(
    request: Request,
    x_tenant_id: str = Header(..., description="Tenant identifier"),
) -> str:
    """
    Sliding window rate limiter using Redis.

    Increments a per-tenant counter keyed to the current UTC minute.
    Returns the tenant_id on success, raises HTTP 429 if the limit is exceeded.
    """
    tenant_id = x_tenant_id.strip()
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "X-Tenant-ID header is required", "code": "MISSING_TENANT_ID"},
        )

    minute_bucket = int(time.time() // 60)
    rl_key = f"rl:{tenant_id}:{minute_bucket}"

    client = get_client()
    try:
        pipe = client.pipeline()
        pipe.incr(rl_key)
        pipe.expire(rl_key, 60)
        results = await pipe.execute()
        count = results[0]
    except Exception as exc:
        logger.error("Rate limit check failed for tenant=%s: %s", tenant_id, exc)
        return tenant_id

    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail={"error": "Rate limit exceeded", "code": "RATE_LIMIT_EXCEEDED"},
            headers={"Retry-After": "60"},
        )

    return tenant_id
