import asyncio
import io
from urllib.parse import urlparse

from pydub import AudioSegment

from . import cache, http as http_client

CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
SLICE_CACHE_TTL_SECONDS = 60 * 60 * 24  # 1 day — slices are cheap to re-derive

# Bound the URLs we'll fetch on a player's behalf to Deezer's CDN. The
# `preview_url` arrives via Deezer's API and is already trusted, but a
# host-allowlist costs nothing and turns "compromised upstream sneaks an
# internal URL through" from an SSRF into a 4xx.
_ALLOWED_HOST_SUFFIXES = (".dzcdn.net",)


def _assert_allowed_host(url: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not any(
        host == s.lstrip(".") or host.endswith(s) for s in _ALLOWED_HOST_SUFFIXES
    ):
        raise ValueError(f"refusing to fetch unallowed host: {host!r}")


def strip_id3(data: bytes) -> bytes:
    """Remove ID3v2 prefix and ID3v1 trailer at the byte level — no re-encode."""
    out = data
    # An ID3v2 header is 10 bytes (magic + version + flags + synchsafe size).
    # Anything shorter is either truncated or just not tagged — leave it.
    if len(out) >= 10 and out[:3] == b"ID3":
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

    _assert_allowed_host(preview_url)
    resp = await http_client.client().get(
        preview_url, follow_redirects=True, timeout=15.0
    )
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


async def get_slice(track_id: str, full_bytes: bytes, seconds: float) -> bytes:
    """Cached audio slice. Quantized to centiseconds so two identical
    requests for the same bracket reuse the cached slice — pydub's MP3
    re-encode dwarfs the network round-trip and Postgres lookup."""
    quantum = max(1, int(round(seconds * 100)))
    rc = cache.client()
    key = f"audio_slice:{track_id}:{quantum}".encode()
    cached = await rc.get(key)
    if cached is not None:
        return cached
    sliced = await slice_audio(full_bytes, quantum / 100)
    await rc.set(key, sliced, ex=SLICE_CACHE_TTL_SECONDS)
    return sliced
