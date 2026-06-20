"""Provider: Archive.org (películas de dominio público). Keyless. ADR-022.

2 pasos: advancedsearch.php (→ identifiers) + /metadata/<id> (→ archivo mp4).
Concepto inspirado en el corpus libre de OpenMontage; código propio Apache-2.0.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from shared.schemas import MaterialInfo, VideoAspect, VideoSource

from ._http import BROWSER_UA, download_to_file, get_tls_verify, search_timeout
from .base import StockProvider

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata"
DOWNLOAD_URL = "https://archive.org/download"

# Formatos/extensiones que consideramos "video utilizable".
_VIDEO_FORMAT_HINTS = ("h.264", "mpeg4", "512kb mpeg4", "h.264 hd", "mp4")


def _parse_search(data: dict) -> list[str]:
    """advancedsearch.php → lista de identifiers (orden de relevancia)."""
    docs = (((data or {}).get("response") or {}).get("docs")) or []
    return [d["identifier"] for d in docs if d.get("identifier")]


def _parse_length(raw: str | None) -> float:
    """Parsea `length` de Archive.org: '30.5', '0:30', '1:02:03' → segundos."""
    if not raw:
        return 0.0
    s = str(raw).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        secs = 0.0
        for n in nums:
            secs = secs * 60 + n
        return secs
    try:
        return float(s)
    except ValueError:
        return 0.0


def _is_video_file(f: dict) -> bool:
    fmt = str(f.get("format", "")).lower()
    name = str(f.get("name", "")).lower()
    if name.endswith(".mp4"):
        return True
    return any(h in fmt for h in _VIDEO_FORMAT_HINTS)


def _parse_metadata(identifier: str, data: dict, min_duration_s: float) -> MaterialInfo | None:
    """/metadata/<id> → primer archivo de video válido como MaterialInfo (o None)."""
    files = (data or {}).get("files") or []
    title = ((data or {}).get("metadata") or {}).get("title") or identifier
    if isinstance(title, list):
        title = title[0] if title else identifier
    for f in files:
        if not _is_video_file(f):
            continue
        duration = _parse_length(f.get("length"))
        if duration and duration < min_duration_s:
            continue
        name = f["name"]
        try:
            w = int(float(f.get("width") or 0))
            h = int(float(f.get("height") or 0))
        except (TypeError, ValueError):
            w = h = 0
        return MaterialInfo(
            provider=VideoSource.ARCHIVE_ORG,
            url=f"{DOWNLOAD_URL}/{identifier}/{name}",
            duration_s=duration,
            width=w,
            height=h,
            description=str(title),
        )
    return None


class ArchiveOrgProvider(StockProvider):
    name = "archive_org"

    async def search(
        self, query: str, aspect: VideoAspect, min_duration_s: float = 3.0
    ) -> list[MaterialInfo]:
        params = {
            "q": f"({query}) AND mediatype:movies",
            "fl[]": "identifier",
            "rows": 5,
            "output": "json",
        }
        headers = {"User-Agent": BROWSER_UA}
        async with httpx.AsyncClient(
            timeout=search_timeout(), verify=get_tls_verify(), follow_redirects=True
        ) as client:
            try:
                resp = await client.get(SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                identifiers = _parse_search(resp.json())
            except Exception as e:
                logger.warning(f"[archive_org] search falló: {e}")
                return []

            out: list[MaterialInfo] = []
            for ident in identifiers:
                try:
                    meta_resp = await client.get(f"{METADATA_URL}/{ident}", headers=headers)
                    meta_resp.raise_for_status()
                    mat = _parse_metadata(ident, meta_resp.json(), min_duration_s)
                except Exception as e:
                    logger.warning(f"[archive_org] metadata {ident} falló: {e}")
                    continue
                if mat is not None:
                    out.append(mat)
            return out

    async def download(self, url: str, dest: Path) -> Path:
        return await download_to_file(url, dest)


__all__ = ["ArchiveOrgProvider"]
