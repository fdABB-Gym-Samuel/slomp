import asyncio
import io

import httpx
from pydub import AudioSegment

from . import cache

CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def strip_id3(data: bytes) -> bytes:
    """Remove ID3v2 prefix and ID3v1 trailer at the byte level — no re-encode."""
    out = data
    if out[:3] == b"ID3":
        # Synchsafe size at bytes 6..9 (each byte uses only 7 bits)
        size = (out[6] << 21) | (out[7] << 14) | (out[8] << 7) | out[9]
        out = out[10 + size :]
    if len(out) >= 128 and out[-128:-125] == b"TAG":
        out = out[:-128]
    return out


async def fetch_full(track_id: str, preview_url: str) -> bytes:
    rc = cache.client()
    key = f"audio:{track_id}".encode()
    cached = await rc.get(key)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(preview_url, follow_redirects=True)
        resp.raise_for_status()
        raw = resp.content

    stripped = strip_id3(raw)
    await rc.set(key, stripped, ex=CACHE_TTL_SECONDS)
    return stripped


def _slice_sync(full_bytes: bytes, seconds: float) -> bytes:
    audio = AudioSegment.from_file(io.BytesIO(full_bytes), format="mp3")
    millis = max(1, int(seconds * 1000))
    sliced = audio[:millis]
    out = io.BytesIO()
    sliced.export(out, format="mp3")
    return out.getvalue()


async def slice_audio(full_bytes: bytes, seconds: float) -> bytes:
    return await asyncio.to_thread(_slice_sync, full_bytes, seconds)
