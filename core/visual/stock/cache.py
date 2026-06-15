"""Cache local de stock footage con validación ffprobe.

Port async de `save_video` (MPT material.py:244-300), pero sin MoviePy:
la validación de duration/fps se hace vía `ffprobe` en subprocess.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from shared.config import load_config

from ._http import download_to_file
from .base import StockProvider


@dataclass(frozen=True)
class ProbeResult:
    duration_s: float
    fps: float

    @property
    def valid(self) -> bool:
        return self.duration_s > 0 and self.fps > 0


def _parse_fps(rate_str: str | None) -> float:
    """`r_frame_rate` viene como "num/den" (e.g. "30000/1001")."""
    if not rate_str:
        return 0.0
    if "/" in rate_str:
        try:
            num, den = rate_str.split("/", 1)
            d = float(den)
            if d == 0:
                return 0.0
            return float(num) / d
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(rate_str)
    except ValueError:
        return 0.0


def probe_video(path: Path) -> ProbeResult:
    """Valida el archivo con ffprobe. Devuelve ProbeResult(0,0) si falla."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=duration,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning(f"ffprobe no disponible o timeout sobre {path}: {e}")
        return ProbeResult(0.0, 0.0)

    if proc.returncode != 0:
        logger.warning(f"ffprobe rc={proc.returncode} sobre {path}: {proc.stderr.strip()}")
        return ProbeResult(0.0, 0.0)

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        logger.warning(f"ffprobe JSON inválido sobre {path}: {e}")
        return ProbeResult(0.0, 0.0)

    streams = data.get("streams") or []
    if not streams:
        return ProbeResult(0.0, 0.0)
    s = streams[0]
    duration = 0.0
    if s.get("duration") is not None:
        try:
            duration = float(s["duration"])
        except (TypeError, ValueError):
            duration = 0.0
    fps = _parse_fps(s.get("r_frame_rate"))
    return ProbeResult(duration_s=duration, fps=fps)


def _cache_path(url: str, cache_dir: Path) -> Path:
    url_no_query = url.split("?", 1)[0]
    url_hash = hashlib.md5(url_no_query.encode("utf-8")).hexdigest()
    return cache_dir / f"vid-{url_hash}.mp4"


def _resolve_cache_dir(cache_dir: str | Path | None) -> Path:
    if cache_dir:
        return Path(cache_dir)
    return Path(load_config().stock.cache_dir)


async def fetch_and_cache(
    url: str,
    cache_dir: str | Path | None = None,
    provider: StockProvider | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Path | None:
    """Descarga `url` al cache local con validación ffprobe.

    Returns:
        Path al archivo cacheado si todo OK, o `None` si falla.
    """
    base_dir = _resolve_cache_dir(cache_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    dest = _cache_path(url, base_dir)

    # Cache hit
    if dest.exists() and dest.stat().st_size > 0:
        logger.info(f"[cache] hit: {dest}")
        return dest

    # Cache miss → descargar
    logger.info(f"[cache] miss, downloading: {url}")
    try:
        if provider is not None:
            await provider.download(url, dest)
        else:
            await download_to_file(url, dest, extra_headers=extra_headers)
    except Exception as e:
        logger.error(f"[cache] descarga falló para {url}: {e}")
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return None

    if not dest.exists() or dest.stat().st_size == 0:
        logger.warning(f"[cache] archivo vacío: {dest}")
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return None

    # Validar con ffprobe en thread pool (subprocess sync)
    probe = await asyncio.to_thread(probe_video, dest)
    if not probe.valid:
        logger.warning(
            f"[cache] archivo inválido (duration={probe.duration_s}, fps={probe.fps}): {dest}"
        )
        try:
            dest.unlink()
        except OSError as e:
            logger.warning(f"[cache] no se pudo borrar archivo inválido {dest}: {e}")
        return None

    logger.success(f"[cache] guardado: {dest} (duration={probe.duration_s:.2f}s, fps={probe.fps:.2f})")
    return dest
