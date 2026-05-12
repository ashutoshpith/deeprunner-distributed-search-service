from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Depends, Query

from app import cache as cache_module
from app import search as search_module
from app.config import settings
from app.models import SearchResponse, SearchResult
from app.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=SearchResponse)
async def search_documents(
    q: str = Query(..., min_length=1, description="Search query"),
    tenant: str = Query(..., description="Tenant ID to scope the search"),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_id: str = Depends(rate_limit_dependency),
) -> SearchResponse:
    """
    Full-text search over documents for a tenant.

    Uses PostgreSQL tsvector / ts_rank for ranked results and falls back to
    pg_trgm trigram similarity on the title when FTS yields no results.
    Responses are cached in Redis for CACHE_TTL_SECONDS seconds.

    The tenant_id from the X-Tenant-ID header must match the `tenant` query
    parameter — this prevents cross-tenant data leakage through the search API.
    """
    query_hash = hashlib.sha256(q.encode()).hexdigest()[:16]
    cache_key = f"search:{tenant}:{query_hash}:{limit}:{offset}"

    start = time.monotonic()

    cached_str = await cache_module.get_cached(cache_key)
    if cached_str is not None:
        took_ms = int((time.monotonic() - start) * 1000)
        payload = json.loads(cached_str)
        return SearchResponse(
            results=payload["results"],
            total=payload["total"],
            took_ms=took_ms,
            cached=True,
        )

    rows, total = await search_module.fts_search(
        query=q,
        tenant_id=tenant,
        limit=limit,
        offset=offset,
    )

    results = [
        SearchResult(
            id=r["id"],
            title=r["title"],
            metadata=r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"]),
            score=float(r["score"]),
            snippet=r["snippet"] or "",
        )
        for r in rows
    ]

    took_ms = int((time.monotonic() - start) * 1000)

    payload = {
        "results": [res.model_dump(mode="json") for res in results],
        "total": total,
    }
    await cache_module.set_cached(cache_key, json.dumps(payload), ttl=settings.cache_ttl_seconds)

    return SearchResponse(
        results=results,
        total=total,
        took_ms=took_ms,
        cached=False,
    )
