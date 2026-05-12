import logging
import asyncpg
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


def _strip_driver_prefix(url: str) -> str:
    """Convert SQLAlchemy-style URL to raw asyncpg DSN."""
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def create_pool(database_url: str) -> asyncpg.Pool:
    """Create and return the asyncpg connection pool."""
    global _pool
    dsn = _strip_driver_prefix(database_url)
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
        ssl="require",
    )
    logger.info("Database connection pool created")
    return _pool


async def close_pool() -> None:
    """Close the asyncpg connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


def get_pool() -> asyncpg.Pool:
    """Return the active pool; raises RuntimeError if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised")
    return _pool


async def init_db(pool: asyncpg.Pool) -> None:
    """Create extensions, tables, and indexes on first startup."""
    async with pool.acquire() as conn:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id   TEXT NOT NULL,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL,
                metadata    JSONB DEFAULT '{}',
                search_vec  TSVECTOR,
                created_at  TIMESTAMPTZ DEFAULT now(),
                updated_at  TIMESTAMPTZ DEFAULT now(),
                deleted_at  TIMESTAMPTZ
            );
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_tenant
                ON documents (tenant_id)
                WHERE deleted_at IS NULL;
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_fts
                ON documents USING GIN (search_vec);
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_trgm
                ON documents USING GIN (title gin_trgm_ops);
        """)
    logger.info("Database schema initialised")
