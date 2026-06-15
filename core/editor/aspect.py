"""Multi-aspect helpers — dimensions, scale-filter, safe-zones.

Portado de MoneyPrinterTurbo (`VideoAspect.to_resolution`) + extensiones para
ffmpeg filter-graph (scale + center crop preserving aspect ratio).

Lee `aspect_ratios` de `shared.config.load_config()` para permitir override
sin tocar código.
"""
from __future__ import annotations

from shared.config import Config, load_config
from shared.schemas import VideoAspect

# Default safe-zone insets (px) por aspect — usados por overlays/subtítulos.
# Coordenadas son: (top, right, bottom, left) en píxeles desde el borde.
_SAFE_ZONE_INSETS: dict[VideoAspect, tuple[int, int, int, int]] = {
    # 9:16 — TikTok/Reels: UI inferior ~280px, top ~220px, laterales 60px
    VideoAspect.PORTRAIT: (220, 60, 280, 60),
    # 16:9 — YouTube: top 80, bottom 120 (titles/cards), laterales 100
    VideoAspect.LANDSCAPE: (80, 100, 120, 100),
    # 1:1 — IG feed: simétrico
    VideoAspect.SQUARE: (100, 100, 100, 100),
}


def get_dimensions(
    aspect: VideoAspect | str,
    config: Config | None = None,
) -> tuple[int, int]:
    """Devuelve (width, height) para el aspect dado.

    Primero busca en `config.aspect_ratios` (overrideable vía TOML); cae al
    default del enum si no está presente.
    """
    aspect_enum = VideoAspect(aspect) if not isinstance(aspect, VideoAspect) else aspect
    cfg = config or load_config()
    spec = cfg.aspect_ratios.get(aspect_enum.value)
    if spec and "width" in spec and "height" in spec:
        return int(spec["width"]), int(spec["height"])
    return aspect_enum.dimensions()


def build_scale_filter(
    aspect: VideoAspect | str,
    *,
    fps: int | None = None,
    pix_fmt: str = "yuv420p",
    config: Config | None = None,
) -> str:
    """Construye el filtro ffmpeg para resize → center crop → normalize.

    Usa `scale=...:force_original_aspect_ratio=increase` (preserve aspect,
    sin distorsión) seguido de `crop=W:H` (center crop). Esto evita
    letterbox y mantiene el frame "full bleed" — el mismo approach del
    stitch.py original de reels-af, generalizado para multi-aspect.

    Args:
        aspect: VideoAspect target.
        fps: si se especifica, añade `fps=N` al filtro.
        pix_fmt: pixel format final (libx264 prefiere yuv420p).
        config: override de config (None → load_config()).
    """
    w, h = get_dimensions(aspect, config=config)
    parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
        "setsar=1",
    ]
    if fps is not None and fps > 0:
        parts.append(f"fps={fps}")
    parts.append(f"format={pix_fmt}")
    return ",".join(parts)


def get_safe_zone_insets(aspect: VideoAspect | str) -> tuple[int, int, int, int]:
    """(top, right, bottom, left) en píxeles — para posicionar overlays."""
    aspect_enum = VideoAspect(aspect) if not isinstance(aspect, VideoAspect) else aspect
    return _SAFE_ZONE_INSETS[aspect_enum]


def get_safe_zone_rect(
    aspect: VideoAspect | str,
    config: Config | None = None,
) -> tuple[int, int, int, int]:
    """Rect (x, y, w, h) del área segura dentro del canvas."""
    w, h = get_dimensions(aspect, config=config)
    top, right, bottom, left = get_safe_zone_insets(aspect)
    return left, top, w - left - right, h - top - bottom


__all__ = [
    "get_dimensions",
    "build_scale_filter",
    "get_safe_zone_insets",
    "get_safe_zone_rect",
]
