import redis.asyncio as redis

from .config import settings

_client: redis.Redis | None = None


async def init_cache() -> None:
    global _client
    _client = redis.Redis.from_url(settings.valkey_url, decode_responses=False)
    await _client.ping()


async def close_cache() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> redis.Redis:
    if _client is None:
        raise RuntimeError("cache not initialized — call init_cache() during lifespan")
    return _client
