"""Provider: Library of Congress (loc.gov). Keyless. API JSON oficial. ADR-026.

1 paso: /search/?fo=json&fa=original-format:film/video → results[] con
resources[].files[] (a veces anidados como lista-de-listas). Cine temprano y
newsreels de dominio público. Concepto inspirado en el corpus libre de
OpenMontage; código propio Apache-2.0.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
from loguru import logger

from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

SEARCH_URL = "https://www.loc.gov/search/"
_VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def _iter_files(resources: list) -> Iterator[dict]:
    """Aplana `resources[].files` (LoC anida los files como lista-de-listas)."""
    for res in resources or []:
        for f in (res or {}).get("files") or []:
            if isinstance(f, list):
                yield from (x for x in f if isinstance(x, dict))
            elif isinstance(f, dict):
                yield f


def _is_video(f: dict) -> bool:
    mime = str(f.get("mimetype") or f.get("mimeType") or "").lower()
    url = str(f.get("url") or "").lower()
    return mime.startswith("video/") or url.endswith(_VIDEO_EXT)


def _parse_loc(data: dict, min_duration_s: float) -> list[MaterialInfo]:
    """results[] → primer archivo de video por item (LoC no expone duración fiable)."""
    out: list[MaterialInfo] = []
    for r in (data or {}).get("results") or []:
        title = r.get("title") or ""
        if isinstance(title, list):
            title = title[0] if title else ""
        desc = r.get("description")
        if isinstance(desc, list):
            desc = " ".join(str(x) for x in desc)
        for f in _iter_files(r.get("resources") or []):
            if not _is_video(f):
                continue
            url = f.get("url")
            if not url:
                continue
            try:
                w = int(float(f.get("width") or 0))
                h = int(float(f.get("height") or 0))
            except (TypeError, ValueError):
                w = h = 0
            out.append(
                MaterialInfo(
                    provider=VideoSource.LOC,
                    url=str(url),
                    duration_s=0.0,  # LoC no expone duración consistente
                    width=w,
                    height=h,
                    description=str(title or desc or ""),
                )
            )
            break  # un clip por result
    return out


class LocProvider(StockProvider):
    name = "loc"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        params = {
            "q": query,
            "fo": "json",
            "c": 5,
            "fa": "original-format:film, video",
        }
        headers = {"User-Agent": BROWSER_UA}
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                return _parse_loc(resp.json(), min_duration_s)
            except Exception as e:
                logger.warning(f"[loc] search falló: {e}")
                return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["LocProvider"]
