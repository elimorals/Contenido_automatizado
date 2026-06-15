"""HiggsfieldEffectsGenerator — VFX overlay post-procesamiento.

Aplica un action effect (explosion, transformation, fire, lightning, etc.)
sobre un clip ya generado. Trabaja como post-step opcional después de DoP/Veo:

    BeatVisual.effect=None     → no-op (skip)
    BeatVisual.effect=EXPLOSION → genera clip enriquecido con explosion VFX

Política de fallback: si Effects falla, devolvemos el clip original sin tocar
(NUNCA crashea el pipeline solo por un VFX cosmético).
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from core.visual.generation.higgsfield_client import (
    HiggsfieldClient,
    HiggsfieldError,
)
from shared.config import load_config
from shared.schemas import BeatArtifact, BeatVisual, VideoSource


class HiggsfieldEffectsGenerator:
    """Aplica un VFX effect a un video ya generado vía Higgsfield Effects API."""

    name = "higgsfield_effects"

    def __init__(self, credentials: str | None = None) -> None:
        cfg = load_config().visual.higgsfield
        if credentials:
            cfg = cfg.model_copy(update={"credentials": credentials})
        self.cfg = cfg

    @property
    def is_enabled(self) -> bool:
        return self.cfg.effects_enabled and bool(
            self.cfg.credentials or (self.cfg.key_id and self.cfg.key_secret)
        )

    async def apply(
        self,
        artifact: BeatArtifact,
        visual: BeatVisual,
        out_dir: Path,
    ) -> BeatArtifact:
        """Si visual.effect está seteado, aplica el VFX y devuelve nuevo artifact.

        Si effect=None, devuelve `artifact` intacto.
        Si falla, devuelve `artifact` intacto y loggea (no propaga error).
        """
        if visual.effect is None or not self.is_enabled:
            return artifact
        if artifact.video_path is None or not artifact.video_path.exists():
            logger.warning(
                f"[higgsfield-effects] beat {artifact.idx} sin video_path; skip"
            )
            return artifact

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"clip-{artifact.idx:02d}-fx.mp4"

        try:
            async with HiggsfieldClient(self.cfg) as cli:
                # Subir video original
                video_url = await cli.upload_image(artifact.video_path)
                payload = {
                    "effect": visual.effect.value,
                    "strength": visual.effect_strength,
                    "input_video": {"type": "video_url", "video_url": video_url},
                }
                result = await cli.submit_and_wait(self.cfg.effects_endpoint, payload)
                if not result.video_url:
                    raise HiggsfieldError("Effects: respuesta sin video_url")
                video_bytes = await cli.download(result.video_url)
            out_path.write_bytes(video_bytes)
        except HiggsfieldError as e:
            logger.warning(
                f"[higgsfield-effects] beat {artifact.idx} effect "
                f"{visual.effect.value} falló ({e}); usando clip original."
            )
            return artifact
        except OSError as e:
            logger.warning(
                f"[higgsfield-effects] beat {artifact.idx} IO error ({e}); skip."
            )
            return artifact

        logger.info(
            f"[higgsfield-effects] beat {artifact.idx} aplicado "
            f"{visual.effect.value} (strength={visual.effect_strength:.2f})"
        )
        return BeatArtifact(
            idx=artifact.idx,
            first_frame_path=artifact.first_frame_path,
            video_path=out_path,
            source=VideoSource.HIGGSFIELD_EFFECT,
            duration_s=artifact.duration_s,
        )
