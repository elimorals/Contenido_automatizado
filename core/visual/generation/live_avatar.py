"""LiveAvatarGenerator — audio-driven talking-head con lip-sync.

Encaja como hermano de ``HiggsfieldDopGenerator``/``VeoGenerator`` en
``core/visual/generation/``. Diferencia clave respecto a los i2v existentes:

- DoP/Veo: image → motion clip cinemático (5s fijo o 4/6/8s). Sin audio.
- LiveAvatar: image + audio WAV → video con boca sincronizada al audio.
  Duración determinada por el audio (lip-sync). Aspect ratio respeta input.

El generator espera que el orquestador haya resuelto:
- ``visual.reference_image_path`` o ``first_frame_path`` upstream
- ``visual.audio_path`` (WAV de TTS sample-accurate)

Si falta el audio, NO debe invocarse a este generator — el orchestrator hace
el routing antes (ver ``orchestrator.py:_should_use_live_avatar``).

Fallback: cuando LiveAvatar falla y ``cfg.fallback_to_soul_on_error=True``,
el orchestrator captura ``VisualGenerationError`` y degrada a Soul (retrato
estático) + ken-burns slow zoom, manteniendo el audio en stitch.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.live_avatar_client import (
    LiveAvatarBackendUnavailableError,
    LiveAvatarBadInputError,
    LiveAvatarError,
    LiveAvatarTimeoutError,
    make_backend,
)
from shared.config import LiveAvatarConfig, load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatVisual,
    VideoSource,
)


# Acumulador process-wide del costo USD (mismo patrón que llm_router/pricing).
# Cleared al inicio de cada job por el orquestador.
last_cost_usd: float = 0.0


def _build_prompt(beat: Beat, visual: BeatVisual, content_mode: str) -> str:
    """Construye el prompt textual para LiveAvatar.

    LiveAvatar usa el prompt para context visual (atmósfera, estilo, encuadre),
    NO para el diálogo (el diálogo viene del audio). Combinamos:

    - ``visual.image_prompt`` (descripción del shot)
    - Hint del rol del beat (hook/mechanism/payoff)
    - Style block según ``content_mode`` (scientific vs general)
    """
    parts: list[str] = [visual.image_prompt.strip()]

    role_hints = {
        "hook": "warm engaged expression, looking at camera, opening gesture",
        "mechanism": "explanatory tone, calm gestures, mid-shot framing",
        "payoff": "confident close, slight smile, eye contact",
    }
    role_key = beat.role.value if hasattr(beat.role, "value") else str(beat.role)
    if role_key in role_hints:
        parts.append(role_hints[role_key])

    if content_mode == "scientific":
        parts.append("documentary lighting, neutral background, professional framing")
    else:
        parts.append("cinematic lighting, soft bokeh background, natural skin tones")

    return ". ".join(parts)


def _resolve_reference_image(visual: BeatVisual, prior_artifact: BeatArtifact | None) -> Path | None:
    """Resuelve la imagen-referencia para LiveAvatar, en orden de precedencia:

    1. ``visual.reference_image_path`` (override explícito — anchor brand fijo)
    2. ``prior_artifact.first_frame_path`` (frame upstream de Soul/Comfy/Gemini)
    3. None (caller debe lanzar error)
    """
    if visual.reference_image_path is not None:
        return visual.reference_image_path
    if prior_artifact and prior_artifact.first_frame_path is not None:
        return prior_artifact.first_frame_path
    return None


class LiveAvatarGenerator(VisualGenerator):
    """Generador de talking heads con lip-sync sincronizado al audio TTS.

    Uso típico desde el orquestador (long-form, intent=TALKING_HEAD):

    .. code-block:: python

        gen = LiveAvatarGenerator()
        # Asegurar que upstream se generó el portrait del presentador
        # y se enlazó en visual.reference_image_path o prior_artifact.first_frame_path
        # Y que TTS produjo visual.audio_path
        artifact = await gen.generate(beat, visual, content_mode, out_dir,
                                      prior_artifact=portrait_artifact)
    """

    name = "live_avatar"

    def __init__(self, cfg: LiveAvatarConfig | None = None):
        self.cfg = cfg or load_config().visual.live_avatar
        if not self.cfg.enabled:
            logger.warning(
                "[live_avatar] generator instanciado con cfg.enabled=False — "
                "el orchestrator no debería invocarte. Verifica config."
            )
        self.backend = make_backend(self.cfg)

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        *,
        prior_artifact: BeatArtifact | None = None,
    ) -> BeatArtifact:
        global last_cost_usd

        if visual.audio_path is None:
            raise VisualGenerationError(
                f"[live_avatar] beat#{beat.idx}: visual.audio_path es None. "
                "LiveAvatar requiere audio TTS upstream — el orchestrator debió "
                "rutear a otro generator. Check long_form/director.py wiring."
            )

        ref_image = _resolve_reference_image(visual, prior_artifact)
        if ref_image is None:
            raise VisualGenerationError(
                f"[live_avatar] beat#{beat.idx}: sin reference image. "
                "Set visual.reference_image_path o asegúrate que prior_artifact "
                "tenga first_frame_path (Soul/Comfy/Gemini upstream)."
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"beat_{beat.idx:03d}_live_avatar.mp4"

        prompt = _build_prompt(beat, visual, content_mode)
        logger.info(
            f"[live_avatar] beat#{beat.idx} backend={self.backend.name} "
            f"image={ref_image.name} audio={visual.audio_path.name} "
            f"target_dur={beat.target_duration_s:.2f}s"
        )

        try:
            result = await self.backend.generate(
                image_path=ref_image,
                audio_path=visual.audio_path,
                prompt=prompt,
                out_path=out_path,
                seed=self.cfg.base_seed + beat.idx,  # variación leve por beat
            )
        except LiveAvatarBadInputError as e:
            raise VisualGenerationError(f"[live_avatar] input inválido: {e}") from e
        except LiveAvatarTimeoutError as e:
            raise VisualGenerationError(f"[live_avatar] timeout: {e}") from e
        except LiveAvatarBackendUnavailableError as e:
            raise VisualGenerationError(f"[live_avatar] backend no disponible: {e}") from e
        except LiveAvatarError as e:
            raise VisualGenerationError(f"[live_avatar] error: {e}") from e

        last_cost_usd += result.cost_usd

        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=ref_image,  # propagamos para downstream
            video_path=result.video_path,
            source=VideoSource.LIVE_AVATAR,
            duration_s=result.duration_s,
        )


# =============================================================================
# Convenience function (mirror del patrón higgsfield.generate_higgsfield_clip)
# =============================================================================


async def generate_live_avatar_clip(
    *,
    image_path: Path,
    audio_path: Path,
    prompt: str,
    out_path: Path,
    cfg: LiveAvatarConfig | None = None,
    seed: int | None = None,
) -> tuple[Path, float, float]:
    """Convenience: genera UN clip sin pasar por Beat/BeatVisual.

    Returns:
        ``(video_path, duration_s, cost_usd)``
    """
    cfg = cfg or load_config().visual.live_avatar
    backend = make_backend(cfg)
    result = await backend.generate(
        image_path=image_path,
        audio_path=audio_path,
        prompt=prompt,
        out_path=out_path,
        seed=seed,
    )
    return result.video_path, result.duration_s, result.cost_usd


def reset_cost_tracker() -> None:
    """Reset del acumulador process-wide (llamar al inicio de cada job)."""
    global last_cost_usd
    last_cost_usd = 0.0
