"""BGM library + selector.

Portado de MoneyPrinterTurbo (`get_bgm_file` + `_resolve_bgm_file_path`),
sin dependencia de MoviePy. La mezcla real ocurre en `ffmpeg_stitch.py`
dentro del filter_complex (un solo pass) — este módulo solo descubre y
selecciona el archivo BGM.

Seguridad: archivos BGM solo pueden venir del directorio whitelisted
(`config.bgm.library_dir`). No se permite leer archivos arbitrarios.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path

from shared.config import Config, load_config

logger = logging.getLogger(__name__)

_BGM_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg")


def _resolve_library_dir(config: Config | None = None) -> Path:
    cfg = config or load_config()
    return Path(cfg.bgm.library_dir).expanduser().resolve()


def list_bgm_files(library_dir: Path | None = None) -> list[Path]:
    """Lista archivos BGM en el directorio configurado (recursive=False)."""
    base = Path(library_dir) if library_dir else _resolve_library_dir()
    if not base.exists() or not base.is_dir():
        return []
    files: list[Path] = []
    for ext in _BGM_EXTENSIONS:
        files.extend(base.glob(f"*{ext}"))
    return sorted(files)


def _safe_resolve(library_dir: Path, candidate: str) -> Path | None:
    """Resuelve `candidate` asegurando que esté dentro de `library_dir`.

    Acepta:
      • Filename simple (`output000.mp3`)
      • Path absoluto (debe caer dentro de library_dir)
      • Path relativo al cwd (último recurso)
    Rechaza cualquier path que escape del directorio.
    """
    base = library_dir.resolve()
    candidate_path = Path(candidate).expanduser()

    # 1. Filename simple → join con base.
    if not candidate_path.is_absolute() and len(candidate_path.parts) == 1:
        candidate_path = base / candidate_path
    elif not candidate_path.is_absolute():
        # Relativo a cwd → resolve.
        candidate_path = candidate_path.resolve()

    try:
        resolved = candidate_path.resolve()
        resolved.relative_to(base)
    except (ValueError, OSError):
        logger.warning("BGM path %s fuera del directorio %s — rechazado", candidate, base)
        return None

    if not resolved.exists() or not resolved.is_file():
        logger.warning("BGM path %s no existe", resolved)
        return None
    if resolved.suffix.lower() not in _BGM_EXTENSIONS:
        logger.warning("BGM extension no soportada: %s", resolved.suffix)
        return None
    return resolved


def pick_bgm(
    bgm_type: str = "random",
    bgm_file: str = "",
    config: Config | None = None,
) -> Path | None:
    """Selecciona un archivo BGM según el modo solicitado.

    Args:
        bgm_type: "random" | "none" | "custom"
        bgm_file: ruta o filename (solo cuando bgm_type="custom")

    Returns:
        Path al archivo BGM o None (= sin BGM).
    """
    if not bgm_type or bgm_type == "none":
        return None

    library_dir = _resolve_library_dir(config)

    if bgm_type == "custom":
        if not bgm_file:
            logger.warning("bgm_type=custom pero bgm_file vacío")
            return None
        return _safe_resolve(library_dir, bgm_file)

    if bgm_type == "random":
        files = list_bgm_files(library_dir)
        if not files:
            logger.warning("No hay archivos BGM en %s", library_dir)
            return None
        return random.choice(files)

    logger.warning("bgm_type desconocido: %s", bgm_type)
    return None


def build_bgm_mix_filter(
    voice_input_label: str,
    bgm_input_label: str,
    *,
    voice_weight: float = 1.0,
    bgm_weight: float = 0.2,
    fade_out_s: float = 3.0,
    out_label: str = "a",
) -> str:
    """Construye el segmento de filter_complex que mezcla voz + BGM.

    Estrategia:
      • BGM en loop infinito (`aloop=loop=-1`).
      • Volume scaling con `volume=<bgm_weight>`.
      • Fade-out final (`afade=t=out:st=...:d=...`) — la duración se
        recorta luego con `-shortest` del comando principal.
      • `amix=inputs=2:duration=first` para alinear al primer input (voz).

    El nodo final tiene label `[{out_label}]` listo para `-map "[{out_label}]"`.
    """
    return (
        f"[{voice_input_label}]volume={voice_weight}[voice_norm];"
        f"[{bgm_input_label}]aloop=loop=-1:size=2e+09,"
        f"volume={bgm_weight},afade=t=out:st=0:d={fade_out_s}[bgm_norm];"
        f"[voice_norm][bgm_norm]amix=inputs=2:duration=first:dropout_transition=2[{out_label}]"
    )


__all__ = [
    "list_bgm_files",
    "pick_bgm",
    "build_bgm_mix_filter",
]
