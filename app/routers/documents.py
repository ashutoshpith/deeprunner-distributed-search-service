from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Response

from app import cache as cache_module
from app import search as search_module
from app.models import DocumentCreateRequest, DocumentCreateResponse, DocumentResponse
from app.rate_limit import rate_limit_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=202, response_model=DocumentCreateResponse)
async def create_document(
    body: DocumentCreateRequest,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(rate_limit_dependency),
) -> DocumentCreateResponse:
    """
    Ingest a document for a tenant.

    Immediately persists the row to Postgres with search_vec = NULL and
    enqueues a background task that populates the full-text search vector.
    Returns 202 Accepted with the assigned document id and status="indexing".
    """
    doc_id = await search_module.insert_document(
        tenant_id=tenant_id,
        title=body.title,
        content=body.content,
        metadata=body.metadata,
        doc_id=body.id,
    )
    background_tasks.add_task(search_module.update_search_vec, doc_id)
    logger.info("Document created: id=%s tenant=%s", doc_id, tenant_id)
    return DocumentCreateResponse(id=doc_id, status="indexing", tenant_id=tenant_id)


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    doc_id: uuid.UUID,
    tenant_id: str = Depends(rate_limit_dependency),
) -> DocumentResponse:
    """
    Retrieve a single document by its UUID.

    Enforces tenant isolation — a document belonging to another tenant is
    returned as 404 (not 403) to avoid leaking existence information.
    Results are cached for 5 minutes.
    """
    cache_key = f"doc:{tenant_id}:{doc_id}"
    cached = await cache_module.get_cached(cache_key)
    if cached is not None:
        import json
        return DocumentResponse.model_validate(json.loads(cached))

    row = await search_module.get_document(doc_id=doc_id, tenant_id=tenant_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "Document not found", "code": "NOT_FOUND"},
        )

    doc = DocumentResponse(**row)
    import json
    await cache_module.set_cached(cache_key, doc.model_dump_json(), ttl=300)
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id: uuid.UUID,
    tenant_id: str = Depends(rate_limit_dependency),
) -> Response:
    """
    Soft-delete a document.

    Sets deleted_at on the row and invalidates both the individual document
    cache entry and all search result cache entries for the tenant.
    Returns 204 No Content on success, 404 if the document does not exist.
    """
    deleted = await search_module.soft_delete_document(doc_id=doc_id, tenant_id=tenant_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"error": "Document not found", "code": "NOT_FOUND"},
        )

    await cache_module.delete_key(f"doc:{tenant_id}:{doc_id}")
    await cache_module.delete_pattern(f"search:{tenant_id}:*")
    logger.info("Document deleted: id=%s tenant=%s", doc_id, tenant_id)
    return Response(status_code=204)
