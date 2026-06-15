"""HiggsfieldDopGenerator — image-to-video con DoP (Director of Photography).

DoP transforma un first frame estático en un clip cinemático de 5s con un
camera preset nombrado (50+ disponibles: dolly_in, super_dolly_out, fpv_drone,
360_orbit, etc.).

Encaja en el pipeline como hermano de `VeoGenerator`. El orquestador decide
cuál usar vía `selector.py` + config (`visual.higgsfield.prefer_over_veo`).

Mapping clave:
    BeatVisual.motion_hint (MotionHint enum, pipeline)
        ↓ (default)
    BeatVisual.higgsfield_preset (HiggsfieldPreset enum, override explícito)
        ↓ (resolve via API motion catalog)
    motion_id (UUID que la API entiende)

Si el preset no resuelve a un motion_id, se pasa como string en el prompt
(DoP también lo entiende, solo con menos fidelity).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.higgsfield_cli import (
    CLIFallbackError,
    CLINotInstalledError,
    generate_video_via_cli,
)
from core.visual.generation.higgsfield_client import (
    HiggsfieldAuthError,
    HiggsfieldClient,
    HiggsfieldError,
    HiggsfieldTimeoutError,
)
from core.visual.generation.higgsfield_prompts import (
    augment_dop_prompt,
    quick_safety_check,
)
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatVisual,
    HiggsfieldPreset,
    MotionHint,
    VideoSource,
)


# =============================================================================
# Motion hint → Higgsfield preset (default mapping)
# =============================================================================

_MOTION_HINT_TO_PRESET: dict[MotionHint, HiggsfieldPreset] = {
    MotionHint.STATIC: HiggsfieldPreset.STATIC,
    MotionHint.SLOW_ZOOM_IN: HiggsfieldPreset.ZOOM_IN,
    MotionHint.SLOW_ZOOM_OUT: HiggsfieldPreset.ZOOM_OUT,
    MotionHint.PAN_LEFT: HiggsfieldPreset.PAN_LEFT,
    MotionHint.PAN_RIGHT: HiggsfieldPreset.PAN_RIGHT,
    MotionHint.KEN_BURNS: HiggsfieldPreset.DOLLY_IN,  # ken-burns ≈ slow dolly
}


def resolve_preset(visual: BeatVisual) -> HiggsfieldPreset:
    """Decide qué preset usar para este beat, en orden de precedencia:

    1. `visual.higgsfield_preset` explícito (mayor prioridad)
    2. Mapping default desde `visual.motion_hint`
    3. Fallback: ZOOM_IN (motion universal)
    """
    if visual.higgsfield_preset is not None:
        return visual.higgsfield_preset
    if visual.motion_hint in _MOTION_HINT_TO_PRESET:
        return _MOTION_HINT_TO_PRESET[visual.motion_hint]
    return HiggsfieldPreset.ZOOM_IN


# =============================================================================
# Generador
# =============================================================================


class HiggsfieldDopGenerator(VisualGenerator):
    """Generador i2v con Higgsfield DoP (3 variantes: lite / turbo / preview)."""

    name = "higgsfield_dop"

    def __init__(
        self,
        model: str | None = None,
        credentials: str | None = None,
    ) -> None:
        cfg = load_config().visual.higgsfield
        self.cfg = cfg
        if credentials:
            # Override con credencial explícita (útil para tests)
            self.cfg = cfg.model_copy(update={"credentials": credentials})
        self.model = model or cfg.dop_model

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        if not (self.cfg.credentials or (self.cfg.key_id and self.cfg.key_secret)):
            raise VisualGenerationError(
                "HiggsfieldDopGenerator: credenciales ausentes "
                "(HIGGSFIELD_CREDENTIALS o HIGGSFIELD_KEY_ID + HIGGSFIELD_KEY_SECRET)"
            )
        if first_frame_path is None or not first_frame_path.exists():
            raise VisualGenerationError(
                f"HiggsfieldDopGenerator: first_frame_path requerido para beat {beat.idx}"
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"clip-{beat.idx:02d}-hf.mp4"

        preset = resolve_preset(visual)
        preset_name = visual.higgsfield_motion_id or preset.value
        # Prompt: enriched siguiendo las skills oficiales (motion verbs + style block).
        prompt = augment_dop_prompt(
            image_prompt=visual.image_prompt,
            motion_preset=preset.value,
            content_mode=content_mode,
        )
        # Heurística local de seguridad — evita gastar el call si el prompt
        # contiene patrones que el filtro server-side va a bloquear.
        safe, safety_msg = quick_safety_check(prompt)
        if not safe:
            logger.warning(
                f"[higgsfield] beat {beat.idx} prompt flagged: {safety_msg}"
            )

        rest_failed_reason: str | None = None
        try:
            async with HiggsfieldClient(self.cfg) as cli:
                # Resolve preset → motion_id (UUID). Si no resuelve, queda como
                # string en el prompt (DoP también lo entiende).
                motion_id: str | None = visual.higgsfield_motion_id
                if motion_id is None:
                    try:
                        motion_id = await cli.resolve_motion_id(preset.value)
                    except HiggsfieldError as e:
                        logger.debug(
                            f"[higgsfield] beat {beat.idx} motion catalog falló ({e}); "
                            "uso prompt-only fallback."
                        )
                        motion_id = None

                # Subir first frame al CDN (más estable que data URL para Higgsfield)
                try:
                    first_frame_url = await cli.upload_image(first_frame_path)
                except HiggsfieldError as e:
                    logger.debug(
                        f"[higgsfield] beat {beat.idx} upload falló ({e}); "
                        "fallback a data URL inline."
                    )
                    first_frame_url = HiggsfieldClient._image_to_data_url(first_frame_path)

                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "input_images": [
                        {"type": "image_url", "image_url": first_frame_url}
                    ],
                }
                if motion_id:
                    payload["motions"] = [
                        {"id": motion_id, "strength": self.cfg.dop_motion_strength}
                    ]
                # Algunos forks aceptan motion_preset como string directo
                payload["motion_preset"] = preset.value
                payload["duration"] = self.cfg.dop_clip_duration_s

                result = await cli.submit_and_wait(self.cfg.dop_endpoint, payload)
                if not result.video_url:
                    raise VisualGenerationError(
                        f"HiggsfieldDoP beat {beat.idx}: respuesta sin video_url"
                    )
                video_bytes = await cli.download(result.video_url)
        except HiggsfieldAuthError as e:
            # Auth fail no se cae al CLI (mismas credentials).
            raise VisualGenerationError(f"Higgsfield auth: {e}") from e
        except HiggsfieldTimeoutError as e:
            rest_failed_reason = f"REST timeout: {e}"
            video_bytes = None
        except HiggsfieldError as e:
            rest_failed_reason = f"REST error: {e}"
            video_bytes = None

        # === CLI fallback (opt-in) ===
        if (not video_bytes) and rest_failed_reason and self.cfg.cli_fallback_enabled:
            logger.warning(
                f"[higgsfield] beat {beat.idx} REST falló ({rest_failed_reason}); "
                "intentando CLI fallback."
            )
            try:
                await generate_video_via_cli(
                    prompt=prompt,
                    first_frame_path=first_frame_path,
                    duration_s=self.cfg.dop_clip_duration_s,
                    out_path=out_path,
                    cfg=self.cfg,
                )
                logger.info(
                    f"[higgsfield] beat {beat.idx} CLI fallback exitoso"
                )
                return BeatArtifact(
                    idx=beat.idx,
                    first_frame_path=first_frame_path,
                    video_path=out_path,
                    source=VideoSource.HIGGSFIELD_DOP,
                    duration_s=float(self.cfg.dop_clip_duration_s),
                )
            except CLINotInstalledError as e:
                raise VisualGenerationError(
                    f"Higgsfield REST falló y CLI no instalada: {e}"
                ) from e
            except CLIFallbackError as e:
                raise VisualGenerationError(
                    f"Higgsfield ambas rutas fallaron — REST: {rest_failed_reason}; "
                    f"CLI: {e}"
                ) from e

        if rest_failed_reason and not video_bytes:
            raise VisualGenerationError(f"Higgsfield: {rest_failed_reason}")
        if not video_bytes:
            raise VisualGenerationError(
                f"HiggsfieldDoP beat {beat.idx}: bytes vacíos"
            )
        out_path.write_bytes(video_bytes)

        logger.info(
            f"[higgsfield] beat {beat.idx} clip generado con preset={preset_name} "
            f"({len(video_bytes)} bytes)"
        )

        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=out_path,
            source=VideoSource.HIGGSFIELD_DOP,
            # DoP es fijo 5s. El editor's concat filter es sample-accurate.
            duration_s=float(self.cfg.dop_clip_duration_s),
        )


# =============================================================================
# Helper directo (paralelo a generate_veo_clip)
# =============================================================================


async def generate_higgsfield_clip(
    beat: Beat,
    visual: BeatVisual,
    first_frame_path: Path,
    out_dir: Path,
    model: str | None = None,
    credentials: str | None = None,
) -> Path | None:
    """Helper directo: genera un clip Higgsfield DoP y devuelve path al MP4.

    Devuelve None si Higgsfield falla — el caller decide fallback.
    """
    try:
        gen = HiggsfieldDopGenerator(model=model, credentials=credentials)
        artifact = await gen.generate(
            beat=beat,
            visual=visual,
            content_mode="general",
            out_dir=out_dir,
            first_frame_path=first_frame_path,
        )
        return artifact.video_path
    except VisualGenerationError:
        return None
    except asyncio.TimeoutError:
        return None
