"""
Tests for the Distributed Document Search Service.

External dependencies (Postgres and Redis) are fully mocked so the suite
runs without any live services.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# We need to patch heavy imports BEFORE the app module is loaded.
# ---------------------------------------------------------------------------

FAKE_DOC_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT = "acme-corp"

SAMPLE_DOC = {
    "id": FAKE_DOC_ID,
    "tenant_id": TENANT,
    "title": "FastAPI Testing Guide",
    "content": "A comprehensive guide to testing FastAPI applications.",
    "metadata": {"author": "Bob"},
    "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch):
    """Inject dummy env vars so pydantic-settings doesn't complain."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("APP_ENV", "test")


@pytest_asyncio.fixture()
async def client() -> AsyncIterator[AsyncClient]:
    """
    Async HTTP client wired to the FastAPI app.

    The lifespan is bypassed by patching the pool / redis initialisation so
    that tests are hermetic and fast.
    """
    # Patch create_pool / init_db / create_client so lifespan never touches
    # real services.
    fake_pool = MagicMock()
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with (
        patch("app.database.create_pool", new=AsyncMock(return_value=fake_pool)),
        patch("app.database.init_db", new=AsyncMock()),
        patch("app.database.close_pool", new=AsyncMock()),
        patch("app.database._pool", fake_pool),
        patch("app.cache.create_client", new=AsyncMock(return_value=fake_redis)),
        patch("app.cache.close_client", new=AsyncMock()),
        patch("app.cache._client", fake_redis),
    ):
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient) -> None:
    """GET /health returns 200 with postgres=ok and redis=ok."""
    fake_conn = AsyncMock()
    fake_conn.fetchval = AsyncMock(return_value=1)
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)

    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(return_value=fake_conn)

    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with (
        patch("app.routers.health.get_pool", return_value=fake_pool),
        patch("app.routers.health.get_redis", return_value=fake_redis),
    ):
        resp = await client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["postgres"] == "ok"
    assert body["redis"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_health_degraded_postgres(client: AsyncClient) -> None:
    """GET /health returns 503 when Postgres is unreachable."""
    fake_conn = AsyncMock()
    fake_conn.fetchval = AsyncMock(side_effect=Exception("connection refused"))
    fake_conn.__aenter__ = AsyncMock(return_value=fake_conn)
    fake_conn.__aexit__ = AsyncMock(return_value=False)

    fake_pool = MagicMock()
    fake_pool.acquire = MagicMock(return_value=fake_conn)

    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(return_value=True)

    with (
        patch("app.routers.health.get_pool", return_value=fake_pool),
        patch("app.routers.health.get_redis", return_value=fake_redis),
    ):
        resp = await client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["postgres"] == "error"


# ---------------------------------------------------------------------------
# POST /documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_document_returns_202(client: AsyncClient) -> None:
    """POST /documents returns 202 with id, status='indexing', tenant_id."""
    with (
        patch("app.routers.documents.search_module.insert_document", new=AsyncMock(return_value=FAKE_DOC_ID)),
        patch("app.routers.documents.search_module.update_search_vec", new=AsyncMock()),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.post(
            "/documents",
            json={
                "title": "FastAPI Testing Guide",
                "content": "A comprehensive guide to testing FastAPI applications.",
                "metadata": {"author": "Bob"},
            },
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["id"] == str(FAKE_DOC_ID)
    assert body["status"] == "indexing"
    assert body["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_create_document_missing_tenant(client: AsyncClient) -> None:
    """POST /documents without X-Tenant-ID returns 422 (FastAPI header validation)."""
    resp = await client.post(
        "/documents",
        json={"title": "T", "content": "C"},
    )
    # FastAPI returns 422 for missing required headers declared via Header(...)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_results(client: AsyncClient) -> None:
    """GET /search returns results with expected shape on cache miss."""
    fake_rows = [
        {
            "id": FAKE_DOC_ID,
            "title": "FastAPI Testing Guide",
            "metadata": {"author": "Bob"},
            "score": 0.075,
            "snippet": "A comprehensive <b>guide</b>",
        }
    ]

    with (
        patch("app.routers.search.cache_module.get_cached", new=AsyncMock(return_value=None)),
        patch("app.routers.search.cache_module.set_cached", new=AsyncMock()),
        patch("app.routers.search.search_module.fts_search", new=AsyncMock(return_value=(fake_rows, 1))),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.get(
            "/search",
            params={"q": "fastapi guide", "tenant": TENANT},
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert len(body["results"]) == 1
    assert body["cached"] is False
    result = body["results"][0]
    assert result["title"] == "FastAPI Testing Guide"
    assert result["score"] == pytest.approx(0.075)


@pytest.mark.asyncio
async def test_search_returns_cached_result(client: AsyncClient) -> None:
    """GET /search returns cached=True when Redis has a hit."""
    cached_payload = json.dumps(
        {
            "results": [
                {
                    "id": str(FAKE_DOC_ID),
                    "title": "FastAPI Testing Guide",
                    "metadata": {},
                    "score": 0.075,
                    "snippet": "cached snippet",
                }
            ],
            "total": 1,
        }
    )

    with (
        patch("app.routers.search.cache_module.get_cached", new=AsyncMock(return_value=cached_payload)),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.get(
            "/search",
            params={"q": "fastapi", "tenant": TENANT},
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 200
    assert resp.json()["cached"] is True


# ---------------------------------------------------------------------------
# GET /documents/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_returns_document(client: AsyncClient) -> None:
    """GET /documents/{id} returns the full document on cache miss."""
    with (
        patch("app.routers.documents.cache_module.get_cached", new=AsyncMock(return_value=None)),
        patch("app.routers.documents.cache_module.set_cached", new=AsyncMock()),
        patch("app.routers.documents.search_module.get_document", new=AsyncMock(return_value=SAMPLE_DOC)),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.get(
            f"/documents/{FAKE_DOC_ID}",
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(FAKE_DOC_ID)
    assert body["title"] == "FastAPI Testing Guide"
    assert body["tenant_id"] == TENANT


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient) -> None:
    """GET /documents/{id} returns 404 when document does not exist."""
    with (
        patch("app.routers.documents.cache_module.get_cached", new=AsyncMock(return_value=None)),
        patch("app.routers.documents.search_module.get_document", new=AsyncMock(return_value=None)),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.get(
            f"/documents/{FAKE_DOC_ID}",
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# DELETE /documents/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_returns_204(client: AsyncClient) -> None:
    """DELETE /documents/{id} returns 204 and invalidates cache."""
    with (
        patch("app.routers.documents.search_module.soft_delete_document", new=AsyncMock(return_value=True)),
        patch("app.routers.documents.cache_module.delete_key", new=AsyncMock()),
        patch("app.routers.documents.cache_module.delete_pattern", new=AsyncMock()),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.delete(
            f"/documents/{FAKE_DOC_ID}",
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_document_not_found(client: AsyncClient) -> None:
    """DELETE /documents/{id} returns 404 when the document does not exist."""
    with (
        patch("app.routers.documents.search_module.soft_delete_document", new=AsyncMock(return_value=False)),
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=1)),
    ):
        resp = await client.delete(
            f"/documents/{FAKE_DOC_ID}",
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_returns_429(client: AsyncClient) -> None:
    """Exceeding RATE_LIMIT_PER_MINUTE returns 429 with Retry-After header."""
    with (
        patch("app.rate_limit.get_client", return_value=_make_rl_client(count=9999)),
    ):
        resp = await client.get(
            "/search",
            params={"q": "test", "tenant": TENANT},
            headers={"X-Tenant-ID": TENANT},
        )

    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert resp.headers.get("retry-after") == "60"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rl_client(count: int) -> MagicMock:
    """Return a fake Redis client whose pipeline INCR returns `count`.

    incr/expire are synchronous MagicMocks because redis-py pipelines queue
    commands without awaiting each one individually; only execute() is async.
    """
    pipe = MagicMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[count, True])

    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)
    return client
