"""Provider: Pixabay Videos API.

Port async de `search_videos_pixabay` (MPT material.py:112-165).

Endpoint: https://pixabay.com/api/videos/
Auth:     query param `key=<api_key>`

Pixabay devuelve un mapa `videos: {large, medium, small, tiny}` con cada
formato. MPT elige el primer formato cuyo width >= target (matching flexible).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx
from loguru import logger

from shared.config import load_config
from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import download_to_file, get_tls_verify, search_timeout
from .base import (
    AllKeysExhaustedError,
    APIKeyRotator,
    NoAPIKeyError,
    StockProvider,
)

PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"


class PixabayProvider(StockProvider):
    name = "pixabay"

    def __init__(self, api_keys: list[str] | None = None) -> None:
        cfg = load_config()
        keys = api_keys if api_keys is not None else list(cfg.stock.pixabay_api_keys)
        self._rotator = APIKeyRotator(keys, self.name)

    async def search(
        self,
        query: str,
        aspect: VideoAspect,
        min_duration_s: float = 3.0,
    ) -> list[MaterialInfo]:
        if not self._rotator.has_keys:
            raise NoAPIKeyError("Pixabay: no hay API keys configuradas en config.stock.pixabay_api_keys")

        target_w, _target_h = aspect.dimensions()

        attempts = max(1, self._rotator.total)
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                api_key = await self._rotator.acquire()
            except AllKeysExhaustedError as e:
                raise e

            params = {
                "q": query,
                "video_type": "all",  # all | film | animation
                "per_page": 50,
                "key": api_key,
            }
            query_url = f"{PIXABAY_SEARCH_URL}?{urlencode(params)}"
            logger.info(f"[pixabay] searching: {query_url}")

            try:
                async with httpx.AsyncClient(
                    timeout=search_timeout(),
                    verify=get_tls_verify(),
                ) as client:
                    resp = await client.get(query_url)

                if resp.status_code in (401, 429):
                    logger.warning(
                        f"[pixabay] key inválida/limitada ({resp.status_code}), rotando…"
                    )
                    await self._rotator.mark_exhausted(api_key)
                    last_error = httpx.HTTPStatusError(
                        f"status={resp.status_code}", request=resp.request, response=resp
                    )
                    continue

                resp.raise_for_status()
                data = resp.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.error(f"[pixabay] search failed: {e}")
                last_error = e
                continue

            if "hits" not in data:
                logger.error(f"[pixabay] respuesta inesperada: {data}")
                return []

            items: list[MaterialInfo] = []
            for v in data["hits"]:
                duration = float(v.get("duration") or 0)
                if duration < min_duration_s:
                    continue
                videos_map = v.get("videos") or {}
                # Tags vienen como string separado por comas (para rerank, ADR-018).
                tags = [t.strip() for t in str(v.get("tags") or "").split(",") if t.strip()]
                # Iterar en el orden insertion (large → medium → small → tiny)
                for _video_type, video in videos_map.items():
                    try:
                        w = int(video["width"])
                        h = int(video["height"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    # Width matching flexible: primer formato cuyo width >= target
                    if w >= target_w:
                        items.append(
                            MaterialInfo(
                                provider=VideoSource.PIXABAY,
                                url=video.get("url", ""),
                                duration_s=duration,
                                width=w,
                                height=h,
                                tags=tags,
                            )
                        )
                        break
            return items

        if last_error:
            logger.error(f"[pixabay] todas las keys fallaron: {last_error}")
            try:
                await self._rotator.acquire()
            except AllKeysExhaustedError:
                raise
        return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)
