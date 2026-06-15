"""Generación IA: Gemini Image + Soul (first frames) + Veo / Higgsfield DoP (i2v) + ken-burns fallback + Higgsfield Effects (VFX)."""
from __future__ import annotations

from core.visual.generation.base import (
    VisualGenerationError,
    VisualGenerator,
)
from core.visual.generation.gemini_image import (
    GeminiImageGenerator,
    generate_first_frame,
)
from core.visual.generation.higgsfield import (
    HiggsfieldDopGenerator,
    generate_higgsfield_clip,
    resolve_preset,
)
from core.visual.generation.higgsfield_cli import (
    CLIFallbackError,
    CLINotInstalledError,
    generate_video_via_cli,
    is_cli_available,
)
from core.visual.generation.higgsfield_client import (
    HiggsfieldAPIError,
    HiggsfieldAuthError,
    HiggsfieldBadInputError,
    HiggsfieldClient,
    HiggsfieldError,
    HiggsfieldNotEnoughCreditsError,
    HiggsfieldNSFWError,
    HiggsfieldTimeoutError,
    JobResult,
)
from core.visual.generation.higgsfield_prompts import (
    HIGGSFIELD_IMAGE_MODELS,
    HIGGSFIELD_VIDEO_MODELS,
    SOUL_PHOTO_GUIDE,
    SoulTrainingValidationError,
    augment_dop_prompt,
    augment_soul_prompt,
    quick_safety_check,
    validate_soul_training_set,
)
from core.visual.generation.higgsfield_effects import HiggsfieldEffectsGenerator
from core.visual.generation.higgsfield_soul import (
    HiggsfieldSoulGenerator,
    create_soul_id,
)
from core.visual.generation.ken_burns import (
    KenBurnsGenerator,
    render_ken_burns,
)
from core.visual.generation.orchestrator import generate_beat_videos
from core.visual.generation.veo import (
    VeoGenerator,
    generate_veo_clip,
)

__all__ = [
    "CLIFallbackError",
    "CLINotInstalledError",
    "GeminiImageGenerator",
    "HIGGSFIELD_IMAGE_MODELS",
    "HIGGSFIELD_VIDEO_MODELS",
    "HiggsfieldAPIError",
    "HiggsfieldAuthError",
    "HiggsfieldBadInputError",
    "HiggsfieldClient",
    "HiggsfieldDopGenerator",
    "HiggsfieldEffectsGenerator",
    "HiggsfieldError",
    "HiggsfieldNotEnoughCreditsError",
    "HiggsfieldNSFWError",
    "HiggsfieldSoulGenerator",
    "HiggsfieldTimeoutError",
    "JobResult",
    "KenBurnsGenerator",
    "SOUL_PHOTO_GUIDE",
    "SoulTrainingValidationError",
    "VeoGenerator",
    "VisualGenerationError",
    "VisualGenerator",
    "augment_dop_prompt",
    "augment_soul_prompt",
    "create_soul_id",
    "generate_beat_videos",
    "generate_first_frame",
    "generate_higgsfield_clip",
    "generate_veo_clip",
    "generate_video_via_cli",
    "is_cli_available",
    "quick_safety_check",
    "render_ken_burns",
    "resolve_preset",
    "validate_soul_training_set",
]
