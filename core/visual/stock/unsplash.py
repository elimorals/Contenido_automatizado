"""Provider: Unsplash (FOTOS bajo licencia Unsplash). Requiere Access Key. ADR-022.

Unsplash NO tiene video → devuelve MaterialInfo con `media_kind="image"`. El selector
convierte la imagen a clip vía ken-burns (igual que el fallback de stills).
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.config import load_config
from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import AllKeysExhaustedError, APIKeyRotator, NoAPIKeyError, StockProvider

SEARCH_URL = "https://api.unsplash.com/search/photos"


def _orientation(aspect: VideoAspect) -> str:
    if aspect is VideoAspect.PORTRAIT:
        return "portrait"
    if aspect is VideoAspect.LANDSCAPE:
        return "landscape"
    return "squarish"


def _parse_unsplash(data: dict) -> list[MaterialInfo]:
    """results[] → MaterialInfo[] (media_kind='image')."""
    out: list[MaterialInfo] = []
    for r in (data or {}).get("results", []) or []:
        urls = r.get("urls") or {}
        url = urls.get("regular") or urls.get("full") or urls.get("raw") or ""
        if not url:
            continue
        desc = r.get("description") or r.get("alt_description") or ""
        tags = [t.get("title", "") for t in (r.get("tags") or []) if t.get("title")]
        out.append(
            MaterialInfo(
                provider=VideoSource.UNSPLASH,
                url=url,
                duration_s=0.0,
                width=int(r.get("width") or 0),
                height=int(r.get("height") or 0),
                description=str(desc),
                tags=tags,
                media_kind="image",
            )
        )
    return out


class UnsplashProvider(StockProvider):
    name = "unsplash"

    def __init__(self, api_keys: list[str] | None = None) -> None:
        cfg = load_config()
        keys = api_keys if api_keys is not None else list(cfg.stock.unsplash_api_keys)
        self._rotator = APIKeyRotator(keys, self.name)

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        if not self._rotator.has_keys:
            raise NoAPIKeyError(
                "Unsplash: no hay keys en config.stock.unsplash_api_keys"
            )
        params = {"query": query, "per_page": 10, "orientation": _orientation(aspect)}
        attempts = max(1, self._rotator.total)
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            for _ in range(attempts):
                try:
                    key = await self._rotator.acquire()
                except AllKeysExhaustedError:
                    break
                headers = {"User-Agent": BROWSER_UA, "Authorization": f"Client-ID {key}"}
                try:
                    resp = await client.get(SEARCH_URL, params=params, headers=headers)
                    if resp.status_code in (401, 403, 429):
                        await self._rotator.mark_exhausted(key)
                        continue
                    resp.raise_for_status()
                    return _parse_unsplash(resp.json())
                except httpx.HTTPError as e:
                    logger.warning(f"[unsplash] search falló: {e}")
                    continue
        return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["UnsplashProvider"]
