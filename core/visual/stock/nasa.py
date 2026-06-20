"""Provider: NASA Image & Video Library (images-api.nasa.gov). Keyless. ADR-022.

2 pasos: /search?media_type=video (→ items con href de manifest) + manifest
collection.json (→ url .mp4).
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

SEARCH_URL = "https://images-api.nasa.gov/search"


def _parse_search(data: dict) -> list[dict]:
    """collection.items → [{href, title, description, keywords}]."""
    items = (((data or {}).get("collection") or {}).get("items")) or []
    out: list[dict] = []
    for it in items:
        href = it.get("href")
        if not href:
            continue
        meta = (it.get("data") or [{}])[0]
        out.append(
            {
                "href": href,
                "title": meta.get("title", ""),
                "description": meta.get("description", ""),
                "keywords": list(meta.get("keywords", []) or []),
            }
        )
    return out


def _parse_asset_manifest(urls: list[str]) -> str | None:
    """Lista de urls del manifest → mejor mp4 (prefiere ~mobile/~small sobre ~orig)."""
    mp4s = [u for u in urls if str(u).lower().endswith(".mp4")]
    if not mp4s:
        return None
    for hint in ("~mobile", "~small", "~medium"):
        for u in mp4s:
            if hint in u:
                return u
    return mp4s[0]


class NasaProvider(StockProvider):
    name = "nasa"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        headers = {"User-Agent": BROWSER_UA}
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(
                    SEARCH_URL, params={"q": query, "media_type": "video"}, headers=headers
                )
                resp.raise_for_status()
                items = _parse_search(resp.json())
            except Exception as e:
                logger.warning(f"[nasa] search falló: {e}")
                return []

            out: list[MaterialInfo] = []
            for it in items[:5]:
                try:
                    man = await client.get(it["href"], headers=headers)
                    man.raise_for_status()
                    mp4 = _parse_asset_manifest(man.json())
                except Exception as e:
                    logger.warning(f"[nasa] manifest falló: {e}")
                    continue
                if not mp4:
                    continue
                desc = it["title"] or it["description"]
                out.append(
                    MaterialInfo(
                        provider=VideoSource.NASA,
                        url=mp4,
                        duration_s=0.0,  # el manifest no expone duración
                        description=str(desc),
                        tags=[str(k) for k in it["keywords"]],
                    )
                )
            return out

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["NasaProvider"]
