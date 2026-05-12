import logging
from typing import Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None


async def create_client(redis_url: str) -> redis.Redis:
    """Create and return a Redis async client."""
    global _client
    _client = redis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    await _client.ping()
    logger.info("Redis client connected")
    return _client


async def close_client() -> None:
    """Close the Redis client connection."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Redis client closed")


def get_client() -> redis.Redis:
    """Return the active Redis client; raises RuntimeError if not initialised."""
    if _client is None:
        raise RuntimeError("Redis client is not initialised")
    return _client


async def get_cached(key: str) -> Optional[str]:
    """Retrieve a value from cache, returning None on miss."""
    client = get_client()
    try:
        return await client.get(key)
    except Exception as exc:
        logger.warning("Cache GET failed for key=%s: %s", key, exc)
        return None


async def set_cached(key: str, value: str, ttl: int) -> None:
    """Write a value to cache with the given TTL in seconds."""
    client = get_client()
    try:
        await client.set(key, value, ex=ttl)
    except Exception as exc:
        logger.warning("Cache SET failed for key=%s: %s", key, exc)


async def delete_key(key: str) -> None:
    """Delete a single cache key."""
    client = get_client()
    try:
        await client.delete(key)
    except Exception as exc:
        logger.warning("Cache DELETE failed for key=%s: %s", key, exc)


async def delete_pattern(pattern: str) -> int:
    """Delete all keys matching a pattern using SCAN + DELETE. Returns deleted count."""
    client = get_client()
    deleted = 0
    try:
        async for key in client.scan_iter(match=pattern, count=100):
            await client.delete(key)
            deleted += 1
    except Exception as exc:
        logger.warning("Cache pattern DELETE failed for pattern=%s: %s", pattern, exc)
    return deleted
