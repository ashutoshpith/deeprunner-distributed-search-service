from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from app.database import get_pool

logger = logging.getLogger(__name__)


async def insert_document(
    tenant_id: str,
    title: str,
    content: str,
    metadata: Dict[str, Any],
    doc_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """
    Insert a new document row with search_vec = NULL.
    Returns the assigned UUID.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        if doc_id is not None:
            row = await conn.fetchrow(
                """
                INSERT INTO documents (id, tenant_id, title, content, metadata)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                RETURNING id
                """,
                doc_id,
                tenant_id,
                title,
                content,
                _json_dumps(metadata),
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO documents (tenant_id, title, content, metadata)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id
                """,
                tenant_id,
                title,
                content,
                _json_dumps(metadata),
            )
    return row["id"]


async def update_search_vec(doc_id: uuid.UUID) -> None:
    """
    Background task: populate search_vec for the given document.
    Called after insert so the write path stays fast.
    """
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE documents
                SET search_vec = to_tsvector('english', title || ' ' || content),
                    updated_at  = now()
                WHERE id = $1
                """,
                doc_id,
            )
        logger.debug("search_vec updated for doc_id=%s", doc_id)
    except Exception as exc:
        logger.error("Failed to update search_vec for doc_id=%s: %s", doc_id, exc)


async def get_document(
    doc_id: uuid.UUID,
    tenant_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single document by id scoped to the tenant.
    Returns None if not found or soft-deleted.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, title, content, metadata, created_at, updated_at
            FROM documents
            WHERE id = $1
              AND tenant_id = $2
              AND deleted_at IS NULL
            """,
            doc_id,
            tenant_id,
        )
    if row is None:
        return None
    result = dict(row)
    if isinstance(result.get("metadata"), str):
        result["metadata"] = json.loads(result["metadata"])
    return result


async def soft_delete_document(doc_id: uuid.UUID, tenant_id: str) -> bool:
    """
    Soft-delete a document. Returns True if a row was affected.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE documents
            SET deleted_at = now()
            WHERE id = $1
              AND tenant_id = $2
              AND deleted_at IS NULL
            """,
            doc_id,
            tenant_id,
        )
    # asyncpg returns "UPDATE n"
    affected = int(result.split()[-1])
    return affected > 0


async def fts_search(
    query: str,
    tenant_id: str,
    limit: int,
    offset: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Run PostgreSQL full-text search with ts_rank scoring and ts_headline snippets.

    Primary path: plainto_tsquery against search_vec (handles multi-word and
    unknown tokens gracefully — no custom token preparation needed).

    Fallback path: pg_trgm similarity against BOTH title and content, so a
    misspelled word like "Postgress" still matches documents whose content
    contains "PostgreSQL".

    Returns (rows, total_count).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        # Primary: FTS with plainto_tsquery (robust multi-word AND query)
        rows = await conn.fetch(
            """
            SELECT
                id,
                title,
                metadata,
                ts_rank(search_vec, query) AS score,
                ts_headline(
                    'english',
                    content,
                    query,
                    'MaxWords=30, MinWords=10'
                ) AS snippet
            FROM documents,
                 plainto_tsquery('english', $1) AS query
            WHERE search_vec @@ query
              AND tenant_id = $2
              AND deleted_at IS NULL
            ORDER BY score DESC
            LIMIT $3 OFFSET $4
            """,
            query,
            tenant_id,
            limit,
            offset,
        )

        if rows:
            total_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM documents,
                     plainto_tsquery('english', $1) AS query
                WHERE search_vec @@ query
                  AND tenant_id = $2
                  AND deleted_at IS NULL
                """,
                query,
                tenant_id,
            )
            total = total_row["cnt"]
        else:
            # Fallback: trigram similarity on title OR content so that a
            # misspelled word matches even when it only appears in the body.
            rows = await conn.fetch(
                """
                SELECT
                    id,
                    title,
                    metadata,
                    GREATEST(
                        similarity(title,   $1),
                        similarity(content, $1)
                    ) AS score,
                    left(content, 200) AS snippet
                FROM documents
                WHERE (title % $1 OR content % $1)
                  AND tenant_id = $2
                  AND deleted_at IS NULL
                ORDER BY score DESC
                LIMIT $3 OFFSET $4
                """,
                query,
                tenant_id,
                limit,
                offset,
            )
            total_row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM documents
                WHERE (title % $1 OR content % $1)
                  AND tenant_id = $2
                  AND deleted_at IS NULL
                """,
                query,
                tenant_id,
            )
            total = total_row["cnt"] if total_row else 0

    parsed = []
    for r in rows:
        row = dict(r)
        if isinstance(row.get("metadata"), str):
            row["metadata"] = json.loads(row["metadata"])
        parsed.append(row)
    return parsed, total


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj)
