"""Provider: Coverr (https://coverr.co).

Port async de `search_videos_coverr` (MPT material.py:168-241).

Endpoint: https://api.coverr.co/videos
Auth:     header `Authorization: Bearer <api_key>`

Notas (de la docstring original de MPT):
- `?urls=true` devuelve mp4 directos en la búsqueda.
- Las URLs son signed JWT (atadas a la API key, sin expiración).
- Coverr es mayormente 16:9 — no se filtra por aspect; el resize/letterbox
  lo maneja el editor downstream.
- `duration` puede venir como number (11.625) o string ("10.500000").
- GET sobre `urls.mp4_download` cuenta como descarga en stats de Coverr;
  no hace falta llamar a `PATCH /stats/downloads`.
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

COVERR_SEARCH_URL = "https://api.coverr.co/videos"


class CoverrProvider(StockProvider):
    name = "coverr"

    def __init__(self, api_keys: list[str] | None = None) -> None:
        cfg = load_config()
        keys = api_keys if api_keys is not None else list(cfg.stock.coverr_api_keys)
        self._rotator = APIKeyRotator(keys, self.name)

    async def search(
        self,
        query: str,
        aspect: VideoAspect,  # noqa: ARG002 - Coverr ignora aspect (mostly 16:9)
        min_duration_s: float = 3.0,
    ) -> list[MaterialInfo]:
        if not self._rotator.has_keys:
            raise NoAPIKeyError("Coverr: no hay API keys configuradas en config.stock.coverr_api_keys")

        params = {
            "query": query,
            "page_size": 20,
            "urls": "true",
            "sort": "popular",
        }
        query_url = f"{COVERR_SEARCH_URL}?{urlencode(params)}"
        logger.info(f"[coverr] searching: {query_url}")

        attempts = max(1, self._rotator.total)
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                api_key = await self._rotator.acquire()
            except AllKeysExhaustedError as e:
                raise e

            headers = {"Authorization": f"Bearer {api_key}"}
            try:
                async with httpx.AsyncClient(
                    timeout=search_timeout(),
                    verify=get_tls_verify(),
                ) as client:
                    resp = await client.get(query_url, headers=headers)

                if resp.status_code in (401, 429):
                    logger.warning(
                        f"[coverr] key inválida/limitada ({resp.status_code}), rotando…"
                    )
                    await self._rotator.mark_exhausted(api_key)
                    last_error = httpx.HTTPStatusError(
                        f"status={resp.status_code}", request=resp.request, response=resp
                    )
                    continue

                resp.raise_for_status()
                data = resp.json()
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.error(f"[coverr] search failed: {e}")
                last_error = e
                continue

            if not isinstance(data, dict) or "hits" not in data:
                logger.error(f"[coverr] respuesta inesperada: {data}")
                return []

            items: list[MaterialInfo] = []
            for v in data["hits"]:
                # duration: puede ser number o string
                try:
                    duration = float(v.get("duration") or 0)
                except (TypeError, ValueError):
                    continue
                if duration < min_duration_s:
                    continue

                video_id = v.get("id")
                mp4_download_url = (v.get("urls") or {}).get("mp4_download")
                if not video_id or not mp4_download_url:
                    continue

                items.append(
                    MaterialInfo(
                        provider=VideoSource.COVERR,
                        url=mp4_download_url,
                        duration_s=duration,
                        width=int(v.get("width") or 0),
                        height=int(v.get("height") or 0),
                    )
                )
            return items

        if last_error:
            logger.error(f"[coverr] todas las keys fallaron: {last_error}")
            try:
                await self._rotator.acquire()
            except AllKeysExhaustedError:
                raise
        return []

    async def download(self, url: str, dest: Path) -> Path:
        # GET sobre urls.mp4_download cuenta como descarga (ver docstring del módulo).
        return await download_to_file(url, dest)
