# Distributed Document Search Service

A multi-tenant document ingestion and full-text search service built with FastAPI, Neon Serverless PostgreSQL, and Redis Cloud.

---

## Architecture

- **FastAPI** — async HTTP layer with BackgroundTasks for non-blocking indexing
- **Neon PostgreSQL** — stores documents; `tsvector` + `ts_rank` for FTS, `pg_trgm` for fuzzy fallback
- **Redis Cloud** — response cache and sliding-window rate limiter
- **Docker Compose** — single `app` container; Neon and Redis are external cloud services

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Docker + Docker Compose | v2+ recommended |
| [Neon](https://neon.tech) account | Free tier is sufficient |
| [Redis Cloud](https://cloud.redis.io) account | Free 30 MB tier works |
| Python 3.11+ | Only needed to run tests locally |

---

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd distributed-search-service
```

### 2. Create a Neon database

1. Sign in at [console.neon.tech](https://console.neon.tech).
2. Create a new project and database.
3. Copy the **connection string** — it looks like:
   ```
   postgresql://user:password@ep-xxx-yyy.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
4. Prefix the scheme with `+asyncpg`:
   ```
   postgresql+asyncpg://user:password@ep-xxx-yyy.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

### 3. Create a Redis Cloud database

1. Sign in at [cloud.redis.io](https://cloud.redis.io).
2. Create a free subscription and a database.
3. Copy the **Public endpoint** and **password**. Compose your URL:
   ```
   rediss://default:<password>@redis-xxxxx.c1.us-east-1-mz.ec2.cloud.redislabs.com:6379
   ```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the values:

```
DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.neon.tech/neondb?sslmode=require
REDIS_URL=rediss://default:password@redis-xxxxx.cloud.redis.io:6379
RATE_LIMIT_PER_MINUTE=60
CACHE_TTL_SECONDS=60
APP_ENV=development
```

### 5. Start the service

```bash
docker compose up --build
```

The application starts on **http://localhost:8000**.  
On first boot `init_db()` runs automatically and creates the schema.

### 6. Verify health

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "postgres": "ok",
  "redis": "ok",
  "timestamp": "2026-05-12T08:00:00+00:00"
}
```

---

## API Reference

All document and search endpoints require the `X-Tenant-ID` header.

### POST /documents — Ingest a document

```bash
curl -s -X POST http://localhost:8000/documents \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: acme-corp" \
  -d '{
    "title": "Introduction to Full-Text Search",
    "content": "Full-text search allows users to search for specific words or phrases within a large body of text.",
    "metadata": {"author": "Alice", "category": "engineering"}
  }'
```

Response `202 Accepted`:
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "indexing",
  "tenant_id": "acme-corp"
}
```

### GET /search — Search documents

```bash
curl -s "http://localhost:8000/search?q=full+text+search&tenant=acme-corp&limit=5&offset=0" \
  -H "X-Tenant-ID: acme-corp"
```

Response `200 OK`:
```json
{
  "results": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "title": "Introduction to Full-Text Search",
      "metadata": {"author": "Alice", "category": "engineering"},
      "score": 0.0759,
      "snippet": "...allows users to <b>search</b> for specific words..."
    }
  ],
  "total": 1,
  "took_ms": 12,
  "cached": false
}
```

### GET /documents/{id} — Fetch a document

```bash
curl -s http://localhost:8000/documents/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-Tenant-ID: acme-corp"
```

Response `200 OK`:
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "tenant_id": "acme-corp",
  "title": "Introduction to Full-Text Search",
  "content": "Full-text search allows users to search...",
  "metadata": {"author": "Alice", "category": "engineering"},
  "created_at": "2026-05-12T08:00:00+00:00",
  "updated_at": "2026-05-12T08:00:01+00:00"
}
```

### DELETE /documents/{id} — Soft-delete a document

```bash
curl -s -X DELETE http://localhost:8000/documents/3fa85f64-5717-4562-b3fc-2c963f66afa6 \
  -H "X-Tenant-ID: acme-corp"
```

Response: `204 No Content`

### GET /health — Service health check

```bash
curl -s http://localhost:8000/health
```

---

## Running Tests

Install dependencies locally:

```bash
pip install -r requirements.txt
```

Run the test suite:

```bash
pytest tests/ -v
```

Tests use `httpx.AsyncClient` with mocked database and Redis layers — no external services required.

---

## Rate Limiting

Each tenant is limited to `RATE_LIMIT_PER_MINUTE` requests per minute (default: 60).  
When exceeded, the service returns:

```
HTTP 429 Too Many Requests
Retry-After: 60
```

```json
{"error": "Rate limit exceeded", "code": "RATE_LIMIT_EXCEEDED"}
```

---

## Multi-Tenancy

- The `X-Tenant-ID` header is **required** on every document/search request.
- All database queries are scoped to the tenant; cross-tenant data access is impossible at the query level.
- Cache keys always include the `tenant_id` prefix.
- A missing or empty `X-Tenant-ID` returns `400 Bad Request`.

---

## Error Codes

| HTTP | Code | Meaning |
|---|---|---|
| 400 | `MISSING_TENANT_ID` | `X-Tenant-ID` header missing or empty |
| 404 | `NOT_FOUND` | Document not found (or belongs to another tenant) |
| 429 | `RATE_LIMIT_EXCEEDED` | Too many requests in the current minute |
| 500 | `INTERNAL_ERROR` | Unexpected server error (details logged, never exposed) |
# deeprunner-distributed-search-service
