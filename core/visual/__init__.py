"""Visual pipeline: stock + IA generation + selector híbrido."""
from __future__ import annotations

from core.visual.selector import (
    generate_all_beat_artifacts,
    select_and_generate_beat_artifact,
)

__all__ = [
    "generate_all_beat_artifacts",
    "select_and_generate_beat_artifact",
]
