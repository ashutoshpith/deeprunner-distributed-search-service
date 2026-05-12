from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DocumentCreateRequest(BaseModel):
    id: Optional[uuid.UUID] = Field(default=None, description="Optional client-supplied UUID")
    title: str = Field(..., min_length=1, description="Document title")
    content: str = Field(..., min_length=1, description="Document body")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary JSON metadata")


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DocumentCreateResponse(BaseModel):
    id: uuid.UUID
    status: str = "indexing"
    tenant_id: str


class DocumentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: str
    title: str
    content: str
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SearchResult(BaseModel):
    id: uuid.UUID
    title: str
    metadata: Dict[str, Any]
    score: float
    snippet: str


class SearchResponse(BaseModel):
    results: List[SearchResult]
    total: int
    took_ms: int
    cached: bool


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    error: str
    code: str
