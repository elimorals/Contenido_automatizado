"""Utilidades HTTP compartidas para los providers (httpx async)."""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.config import load_config

# Mismo UA que MPT — algunos CDNs filtran requests "anónimos".
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
)


def get_tls_verify() -> bool:
    """Equivalente a `_get_tls_verify()` de MPT."""
    cfg = load_config()
    verify = cfg.app.tls_verify
    if not verify:
        logger.warning(
            "TLS certificate verification is disabled (config.app.tls_verify=false). "
            "Use only in trusted proxy environments."
        )
    return bool(verify)


def search_timeout() -> httpx.Timeout:
    """Timeout (connect=30, read=60) — paridad con MPT (30, 60)."""
    return httpx.Timeout(60.0, connect=30.0)


def download_timeout() -> httpx.Timeout:
    """Timeout (connect=60, read=240) — paridad con MPT (60, 240)."""
    return httpx.Timeout(240.0, connect=60.0)


async def download_to_file(
    url: str,
    dest: Path,
    extra_headers: dict[str, str] | None = None,
) -> Path:
    """Descarga binario a `dest` usando httpx async stream."""
    headers = {"User-Agent": BROWSER_UA}
    if extra_headers:
        headers.update(extra_headers)

    dest.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(
        timeout=download_timeout(),
        verify=get_tls_verify(),
        follow_redirects=True,
    ) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
    return dest
