"""Hardware encoder detection + runtime fallback.

Portado de MoneyPrinterTurbo (`_ffmpeg_encoder_exists`, `_get_effective_video_codec`)
y rediseñado para:
  • Detección por OS (macOS → videotoolbox, NVIDIA → nvenc, etc.)
  • Validación con `ffmpeg -encoders` (cache LRU).
  • Fallback en runtime: si el encoder elegido falla durante encoding,
    deshabilita el encoder en proceso y retorna el siguiente disponible.

`libx264` siempre existe en cualquier build estándar de ffmpeg, así que
es el bottom de la cadena.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from functools import lru_cache

from shared.config import Config, load_config

logger = logging.getLogger(__name__)

_DEFAULT_VIDEO_CODEC = "libx264"

# Orden por OS — el primer match probable según hardware típico.
_OS_PRIORITY: dict[str, list[str]] = {
    "Darwin": ["h264_videotoolbox", "libx264"],
    "Linux": ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"],
    "Windows": ["h264_nvenc", "h264_amf", "h264_qsv", "h264_mf", "libx264"],
}

_SUPPORTED_VIDEO_CODECS = frozenset(
    {
        "libx264",
        "h264_nvenc",
        "h264_amf",
        "h264_qsv",
        "h264_mf",
        "h264_videotoolbox",
    }
)

# Encoders deshabilitados en runtime (set mutable, scoped al proceso).
_runtime_disabled: set[str] = set()


def _ffmpeg_binary() -> str:
    """Devuelve path a ffmpeg en PATH (o crashea si no existe)."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError(
            "ffmpeg no encontrado en PATH. "
            "Instalar: `brew install ffmpeg` (macOS) o `apt install ffmpeg` (Linux)."
        )
    return path


@lru_cache(maxsize=32)
def _encoder_available(ffmpeg_bin: str, codec: str) -> bool:
    """¿El binario ffmpeg declara soporte para este codec?

    Solo prueba que esté compilado-in. No garantiza que el hardware/driver
    realmente lo soporten — para eso hay fallback en runtime.
    """
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Inspección de ffmpeg encoders falló: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning(
            "ffmpeg -encoders devolvió error: %s",
            (result.stderr or result.stdout or "").strip()[:300],
        )
        return False
    # ffmpeg lista encoders como ` V..... libx264              ...`
    return codec in result.stdout


def _build_priority(config: Config | None = None) -> list[str]:
    """Combina prioridad por OS + override de config + sanitización.

    1. Toma `config.editor.hw_encoder_fallback` si existe (override).
    2. Si vacío, usa la prioridad por OS.
    3. Filtra a `_SUPPORTED_VIDEO_CODECS`.
    4. Garantiza que `libx264` esté al final (siempre disponible).
    """
    cfg = config or load_config()
    candidates = list(cfg.editor.hw_encoder_fallback or [])
    if not candidates:
        candidates = list(_OS_PRIORITY.get(platform.system(), ["libx264"]))
    # Sanitizar a whitelist.
    candidates = [c for c in candidates if c in _SUPPORTED_VIDEO_CODECS]
    # libx264 always last + sin duplicados, preservando orden.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        if c == _DEFAULT_VIDEO_CODEC:
            continue
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    ordered.append(_DEFAULT_VIDEO_CODEC)
    return ordered


@lru_cache(maxsize=1)
def _detect_cached(ffmpeg_bin: str, key: tuple[str, ...]) -> str:
    """Cache helper — `key` permite invalidar si cambia la prioridad."""
    for codec in key:
        if codec in _runtime_disabled:
            continue
        if codec == _DEFAULT_VIDEO_CODEC:
            return codec
        if _encoder_available(ffmpeg_bin, codec):
            logger.info("Hardware encoder seleccionado: %s", codec)
            return codec
    return _DEFAULT_VIDEO_CODEC


def detect_hw_encoder(config: Config | None = None) -> str:
    """Detecta el mejor encoder disponible.

    Orden de prueba:
      1. `config.editor.hw_encoder_fallback` (si está definido)
      2. Default por OS (videotoolbox/nvenc/amf/qsv/mf)
      3. libx264 (siempre disponible)

    Cachea el resultado (lru_cache por combinación binario+prioridad).
    """
    ffmpeg_bin = _ffmpeg_binary()
    priority = tuple(_build_priority(config=config))
    return _detect_cached(ffmpeg_bin, priority)


def mark_encoder_failed(codec: str, reason: str = "") -> str:
    """Deshabilita un encoder en runtime; devuelve el siguiente fallback.

    Se llama desde el caller cuando el subprocess de ffmpeg termina con
    exit != 0 usando este encoder. La próxima invocación de
    `detect_hw_encoder()` ya no lo elegirá.
    """
    if codec == _DEFAULT_VIDEO_CODEC:
        # libx264 fallando es problema gordo — no lo deshabilitamos.
        logger.error("libx264 falló — no hay fallback. Razón: %s", reason)
        return _DEFAULT_VIDEO_CODEC
    _runtime_disabled.add(codec)
    _detect_cached.cache_clear()
    logger.warning(
        "Encoder %s deshabilitado runtime. Razón: %s. Fallback → libx264",
        codec,
        reason[:200],
    )
    return _DEFAULT_VIDEO_CODEC


def reset_runtime_state() -> None:
    """Resetea cache + disabled set. Útil en tests."""
    _runtime_disabled.clear()
    _detect_cached.cache_clear()
    _encoder_available.cache_clear()


def get_encoder_args(
    codec: str,
    *,
    crf: int = 23,
    preset: str = "fast",
) -> list[str]:
    """Argumentos `-c:v ... -preset ... -crf ...` para el codec dado.

    Los encoders hardware no aceptan `-crf` igual que x264 — usamos sus
    flags equivalentes para mantener calidad razonable.
    """
    if codec == "libx264":
        return ["-c:v", codec, "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "h264_videotoolbox":
        # videotoolbox: -q:v en lugar de crf (1-100, ~65 ≈ crf 23).
        # Mapeamos crf 23 → q:v 65; crf 18 → q:v 80.
        qv = max(30, min(100, 100 - (crf * 2)))
        return ["-c:v", codec, "-q:v", str(qv), "-pix_fmt", "yuv420p"]
    if codec == "h264_nvenc":
        # nvenc usa -cq (constant quality, 0-51 similar a crf).
        return ["-c:v", codec, "-preset", "p4", "-cq", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "h264_amf":
        return ["-c:v", codec, "-quality", "balanced", "-qp_i", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "h264_qsv":
        return ["-c:v", codec, "-global_quality", str(crf), "-pix_fmt", "yuv420p"]
    if codec == "h264_mf":
        return ["-c:v", codec, "-rate_control", "quality", "-quality", str(crf), "-pix_fmt", "yuv420p"]
    # Fallback paranoico.
    return ["-c:v", codec, "-pix_fmt", "yuv420p"]


__all__ = [
    "detect_hw_encoder",
    "mark_encoder_failed",
    "reset_runtime_state",
    "get_encoder_args",
    "_DEFAULT_VIDEO_CODEC",
    "_SUPPORTED_VIDEO_CODECS",
]
