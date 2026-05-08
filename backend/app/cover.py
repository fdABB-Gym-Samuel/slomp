"""Album-cover proxy with server-side progressive obfuscation. The point
of doing this server-side rather than via CSS is that a curious player
can't just open devtools and read the unblurred image off the wire — the
bytes their browser receives are already obscured to their bracket.

Two modes are supported: a Gaussian blur (radius in px) and a pixel-block
mosaic (block size in px on the source image)."""

import asyncio
import hashlib
import io
from typing import Literal

import httpx
from PIL import Image, ImageFilter

from . import cache

ObscureMode = Literal["blur", "pixelate"]

_HTTP_TIMEOUT_SECONDS = 10.0
_ORIGINAL_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days — covers are immutable
_RENDERED_TTL_SECONDS = 60 * 60 * 24  # 1 day — cheap to re-derive


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


async def _fetch_original(url: str) -> bytes:
    rc = cache.client()
    key = f"cover_orig:{_url_hash(url)}".encode()
    cached = await rc.get(key)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        raw = resp.content
    await rc.set(key, raw, ex=_ORIGINAL_TTL_SECONDS)
    return raw


def _blur_sync(raw: bytes, radius: int) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if radius > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def _pixelate_sync(raw: bytes, side: int) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    # `side` is the target side length of the down-sampled image — 1 means
    # a single solid-colour pixel; values ≥ max(w, h) mean "no effect".
    long_side = max(w, h)
    target = max(1, min(side, long_side))
    if target < long_side:
        sw = max(1, round(w * target / long_side))
        sh = max(1, round(h * target / long_side))
        # Downscale with BILINEAR for stable colour averaging, upscale with
        # NEAREST so each pixel becomes a flat square (the classic mosaic).
        img = img.resize((sw, sh), Image.BILINEAR).resize((w, h), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


async def render(url: str, mode: ObscureMode, intensity: float) -> bytes:
    """Fetch the original (cached) and apply the requested obfuscation.
    Quantize the intensity to an integer so the rendered-variant cache hits
    across nearby requests."""
    quantized = max(0, int(round(intensity)))
    rc = cache.client()
    key = f"cover_render:{_url_hash(url)}:{mode}:{quantized}".encode()
    cached = await rc.get(key)
    if cached is not None:
        return cached
    raw = await _fetch_original(url)
    if mode == "pixelate":
        rendered = await asyncio.to_thread(_pixelate_sync, raw, quantized)
    else:
        rendered = await asyncio.to_thread(_blur_sync, raw, quantized)
    await rc.set(key, rendered, ex=_RENDERED_TTL_SECONDS)
    return rendered
