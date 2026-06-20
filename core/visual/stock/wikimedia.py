"""Provider: Wikimedia Commons (video CC/dominio público). Keyless. ADR-022.

1 paso: action=query&generator=search con prop=imageinfo filtrado a mediatype=VIDEO.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

API_URL = "https://commons.wikimedia.org/w/api.php"


def _title_to_desc(title: str) -> str:
    """'File:Ocean waves.webm' → 'Ocean waves'."""
    t = title
    if t.startswith("File:"):
        t = t[len("File:"):]
    if "." in t:
        t = t.rsplit(".", 1)[0]
    return t.replace("_", " ").strip()


def _parse_wikimedia(data: dict, min_duration_s: float) -> list[MaterialInfo]:
    """query.pages → MaterialInfo[] de video. Duración desconocida no filtra."""
    pages = (((data or {}).get("query") or {}).get("pages")) or {}
    out: list[MaterialInfo] = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime", "")).lower()
        mediatype = str(info.get("mediatype", "")).upper()
        is_video = mediatype == "VIDEO" or mime.startswith("video/") or mime == "application/ogg"
        if not is_video:
            continue
        duration = float(info.get("duration") or 0.0)
        if duration and duration < min_duration_s:
            continue
        out.append(
            MaterialInfo(
                provider=VideoSource.WIKIMEDIA,
                url=info.get("url", ""),
                duration_s=duration,
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
                description=_title_to_desc(page.get("title", "")),
            )
        )
    return out


class WikimediaProvider(StockProvider):
    name = "wikimedia"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:video {query}",
            "gsrnamespace": 6,
            "gsrlimit": 10,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|mediatype|metadata",
        }
        headers = {"User-Agent": BROWSER_UA}
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(API_URL, params=params, headers=headers)
                resp.raise_for_status()
                return _parse_wikimedia(resp.json(), min_duration_s)
            except Exception as e:
                logger.warning(f"[wikimedia] search falló: {e}")
                return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["WikimediaProvider"]
