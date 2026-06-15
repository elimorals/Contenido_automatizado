"""Editor ffmpeg single-pass + multi-aspect + hardware encoders.

Public API:
  - stitch_video: single-pass entry point (concat + ASS burn + BGM mix).
  - FFmpegCommandBuilder: builder pattern para construir el comando.
  - get_dimensions / build_scale_filter / get_safe_zone_*: multi-aspect helpers.
  - detect_hw_encoder / mark_encoder_failed / get_encoder_args: encoder routing.
  - list_bgm_files / pick_bgm / build_bgm_mix_filter: BGM library.
"""
from __future__ import annotations

from .aspect import (
    build_scale_filter,
    get_dimensions,
    get_safe_zone_insets,
    get_safe_zone_rect,
)
from .bgm import build_bgm_mix_filter, list_bgm_files, pick_bgm
from .encoders import (
    detect_hw_encoder,
    get_encoder_args,
    mark_encoder_failed,
    reset_runtime_state,
)
from .ffmpeg_stitch import FFmpegCommandBuilder, stitch_video

__all__ = [
    # stitch
    "stitch_video",
    "FFmpegCommandBuilder",
    # aspect
    "get_dimensions",
    "build_scale_filter",
    "get_safe_zone_insets",
    "get_safe_zone_rect",
    # encoders
    "detect_hw_encoder",
    "mark_encoder_failed",
    "get_encoder_args",
    "reset_runtime_state",
    # bgm
    "list_bgm_files",
    "pick_bgm",
    "build_bgm_mix_filter",
]
