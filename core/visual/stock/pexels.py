"""Provider: Pexels Videos API.

Port async de `search_videos_pexels` (MPT material.py:55-109).

Endpoint: https://api.pexels.com/videos/search
Auth:     header `Authorization: <api_key>`
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx
from loguru import logger

from shared.config import load_config
from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import (
    BROWSER_UA,
    download_to_file,
    get_tls_verify,
    search_timeout,
)
from .base import (
    AllKeysExhaustedError,
    APIKeyRotator,
    NoAPIKeyError,
    StockProvider,
)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"


def _orientation(aspect: VideoAspect) -> str:
    if aspect is VideoAspect.PORTRAIT:
        return "portrait"
    if aspect is VideoAspect.LANDSCAPE:
        return "landscape"
    return "square"


class PexelsProvider(StockProvider):
    name = "pexels"

    def __init__(self, api_keys: list[str] | None = None) -> None:
        cfg = load_config()
        keys = api_keys if api_keys is not None else list(cfg.stock.pexels_api_keys)
        self._rotator = APIKeyRotator(keys, self.name)

    async def search(
        self,
        query: str,
        aspect: VideoAspect,
        min_duration_s: float = 3.0,
    ) -> list[MaterialInfo]:
        if not self._rotator.has_keys:
            raise NoAPIKeyError("Pexels: no hay API keys configuradas en config.stock.pexels_api_keys")

        target_w, target_h = aspect.dimensions()
        params = {
            "query": query,
            "per_page": 20,
            "orientation": _orientation(aspect),
        }
        query_url = f"{PEXELS_SEARCH_URL}?{urlencode(params)}"
        logger.info(f"[pexels] searching: {query_url}")

        # Retry sobre rotación de keys (401/429)
        attempts = max(1, self._rotator.total)
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                api_key = await self._rotator.acquire()
            except AllKeysExhaustedError as e:
                raise e

            headers = {"Authorization": api_key, "User-Agent": BROWSER_UA}
            try:
                async with httpx.AsyncClient(
                    timeout=search_timeout(),
                    verify=get_tls_verify(),
                ) as client:
                    resp = await client.get(query_url, headers=headers)

                if resp.status_code in (401, 429):
                    logger.warning(
                        f"[pexels] key inválida/limitada ({resp.status_code}), rotando…"
                    )
                    await self._rotator.mark_exhausted(api_key)
                    last_error = httpx.HTTPStatusError(
                        f"status={resp.status_code}", request=resp.request, response=resp
                    )
                    continue

                resp.raise_for_status()
                data = resp.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.error(f"[pexels] search failed: {e}")
                last_error = e
                continue

            if "videos" not in data:
                logger.error(f"[pexels] respuesta inesperada: {data}")
                return []

            items: list[MaterialInfo] = []
            for v in data["videos"]:
                duration = float(v.get("duration") or 0)
                if duration < min_duration_s:
                    continue
                for vf in v.get("video_files", []):
                    try:
                        w = int(vf["width"])
                        h = int(vf["height"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if w == target_w and h == target_h:
                        items.append(
                            MaterialInfo(
                                provider=VideoSource.PEXELS,
                                url=vf["link"],
                                duration_s=duration,
                                width=w,
                                height=h,
                            )
                        )
                        break
            return items

        # Todos los intentos consumidos. Si todas las keys fueron marcadas exhaustas
        # por 401/429, propagar AllKeysExhaustedError. Si no, devolver [] silencioso.
        if last_error:
            logger.error(f"[pexels] todas las keys fallaron: {last_error}")
            try:
                await self._rotator.acquire()
            except AllKeysExhaustedError:
                raise
        return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)
