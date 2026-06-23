"""Provider: Pond5 Public Domain (pond5.com/free). ADR-026.

API JSON. Key opcional → keyless por default, activado por flag. ~10K clips
históricos de dominio público (WWI/WWII, cine temprano, space race, discursos).
Las claves del payload vienen abreviadas (`v`, `dur`, `kw`, `w`/`h`) con alias
largos en algunas respuestas. Concepto inspirado en el corpus libre de
OpenMontage; código propio Apache-2.0.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.config import load_config
from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

SEARCH_URL = "https://www.pond5.com/api/v2/search"


def _parse_pond5(data: dict, min_duration_s: float) -> list[MaterialInfo]:
    """results[] (o items[]) → MaterialInfo, tolerando claves abreviadas/largas."""
    rows = (data or {}).get("results") or (data or {}).get("items") or []
    out: list[MaterialInfo] = []
    for r in rows:
        url = r.get("v") or r.get("preview_url") or r.get("url")
        if not url:
            continue
        dur_raw = r.get("dur") if r.get("dur") is not None else r.get("duration")
        try:
            duration = float(dur_raw) if dur_raw is not None else 0.0
        except (TypeError, ValueError):
            duration = 0.0
        if duration and duration < min_duration_s:
            continue
        try:
            w = int(float(r.get("w") or r.get("width") or 0))
            h = int(float(r.get("h") or r.get("height") or 0))
        except (TypeError, ValueError):
            w = h = 0
        kw = r.get("kw") or r.get("keywords") or r.get("tags") or []
        if isinstance(kw, str):
            kw = [kw]
        title = r.get("title") or r.get("t") or ""
        desc = r.get("desc") or r.get("description") or ""
        out.append(
            MaterialInfo(
                provider=VideoSource.POND5_PD,
                url=str(url),
                duration_s=duration,
                width=w,
                height=h,
                description=str(title or desc),
                tags=[str(k) for k in kw],
            )
        )
    return out


class Pond5PdProvider(StockProvider):
    name = "pond5_pd"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        params = {"kw": query, "ps": 5, "free": 1, "mt": "footage"}
        headers = {"User-Agent": BROWSER_UA, "Accept": "application/json"}
        api_key = getattr(load_config().stock, "pond5_api_key", "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                return _parse_pond5(resp.json(), min_duration_s)
            except Exception as e:
                logger.warning(f"[pond5_pd] search falló: {e}")
                return []

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["Pond5PdProvider"]
