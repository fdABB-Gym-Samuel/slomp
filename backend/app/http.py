"""Shared httpx.AsyncClient used by audio/cover/deezer for outbound calls.

A single client shares the underlying connection pool, avoiding a fresh
TLS handshake per request. Initialised in `main.lifespan`."""

import httpx

_client: httpx.AsyncClient | None = None


async def init_http(timeout: float = 15.0) -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=timeout)


async def close_http() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError(
            "http client not initialized — call init_http() during lifespan"
        )
    return _client
