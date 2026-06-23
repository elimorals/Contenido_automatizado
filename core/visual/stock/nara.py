"""Provider: U.S. National Archives (catalog.archives.gov). ADR-026.

API JSON oficial (v2). Key opcional (`x-api-key`) solo para rate limits mayores
→ keyless por default, activado por flag. WWII, Apollo, civil rights, gobierno;
dominio público. Concepto inspirado en el corpus libre de OpenMontage; código
propio Apache-2.0.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.config import load_config
from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

SEARCH_URL = "https://catalog.archives.gov/api/v2/records/search"
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def _is_video(obj: dict) -> bool:
    mime = str(obj.get("mimeType") or obj.get("mimetype") or "").lower()
    url = str(obj.get("url") or "").lower()
    return mime.startswith("video/") or url.endswith(_VIDEO_EXT)


def _parse_nara(data: dict, min_duration_s: float) -> list[MaterialInfo]:
    """results[] → primer objeto de video por record."""
    out: list[MaterialInfo] = []
    for r in (data or {}).get("results") or []:
        title = str(r.get("title") or "")
        note = str(r.get("scopeAndContentNote") or "")
        desc = (f"{title} {note}").strip()
        for obj in r.get("objects") or []:
            url = obj.get("url")
            if not url or not _is_video(obj):
                continue
            dur_raw = obj.get("duration")
            try:
                duration = float(dur_raw) if dur_raw is not None else 0.0
            except (TypeError, ValueError):
                duration = 0.0
            if duration and duration < min_duration_s:
                continue
            try:
                w = int(float(obj.get("width") or 0))
                h = int(float(obj.get("height") or 0))
            except (TypeError, ValueError):
                w = h = 0
            out.append(
                MaterialInfo(
                    provider=VideoSource.NARA,
                    url=str(url),
                    duration_s=duration,
                    width=w,
                    height=h,
                    description=desc,
                )
            )
            break
    return out


class NaraProvider(StockProvider):
    name = "nara"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        params = {"q": query, "limit": 5, "objectType": "moving image"}
        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
        api_key = getattr(load_config().stock, "nara_api_key", "")
        if api_key:
            headers["x-api-key"] = api_key
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                return _parse_nara(resp.json(), min_duration_s)
            except Exception as e:
                logger.warning(f"[nara] search falló: {e}")
                return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["NaraProvider"]
