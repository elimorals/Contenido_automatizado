"""HiggsfieldSoulGenerator — first frames con character consistency.

Soul es text2image con un `reference_id` (SoulId) entrenado sobre fotos del
mismo personaje. Cada call con el mismo SoulId produce imágenes del mismo
rostro/cuerpo aunque cambien escena, ropa, pose, lighting.

Encaja como alternativa a `GeminiImageGenerator`:
- Si `BeatVisual.soul_id` está presente → usar Soul
- Si no → Gemini Image (default, no consistency)

El orquestador decide cuál instanciar según config + presencia del campo.

Output: JPEG 720×1280 (default, configurable vía `soul_width`/`soul_height`),
listo para consumir por `HiggsfieldDopGenerator` o `VeoGenerator` como first frame.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from PIL import Image

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.gemini_image import _crop_to_9x16
from core.visual.generation.higgsfield_client import (
    HiggsfieldAuthError,
    HiggsfieldClient,
    HiggsfieldError,
    HiggsfieldTimeoutError,
)
from core.visual.generation.higgsfield_prompts import (
    augment_soul_prompt,
    validate_soul_training_set,
)
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource


class HiggsfieldSoulGenerator(VisualGenerator):
    """Genera first frames con SoulId para character consistency.

    Si el BeatVisual no trae `soul_id` y `soul_default_reference_id` está
    vacío en config, lanza `VisualGenerationError` — el caller debe usar
    Gemini Image en su lugar.
    """

    name = "higgsfield_soul"

    def __init__(
        self,
        model: str | None = None,
        credentials: str | None = None,
    ) -> None:
        cfg = load_config().visual.higgsfield
        if credentials:
            cfg = cfg.model_copy(update={"credentials": credentials})
        self.cfg = cfg
        self.model = model or cfg.soul_model

    def _resolve_soul_id(self, visual: BeatVisual) -> str:
        """Precedencia: visual.soul_id > config.soul_default_reference_id."""
        if visual.soul_id:
            return visual.soul_id
        if self.cfg.soul_default_reference_id:
            return self.cfg.soul_default_reference_id
        raise VisualGenerationError(
            "HiggsfieldSoul: ni BeatVisual.soul_id ni "
            "config.visual.higgsfield.soul_default_reference_id están seteados"
        )

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
    ) -> BeatArtifact:
        if not (self.cfg.credentials or (self.cfg.key_id and self.cfg.key_secret)):
            raise VisualGenerationError(
                "HiggsfieldSoul: credenciales ausentes"
            )

        soul_id = self._resolve_soul_id(visual)
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"frame-{beat.idx:02d}-soul-raw.png"
        final_path = out_dir / f"frame-{beat.idx:02d}.jpg"

        # Augment usando reglas oficiales de las skills (Soul aesthetic/cinematic).
        cinematic = self.model in ("soul_cinematic", "soul_cinema_studio")
        augmented = augment_soul_prompt(
            visual.image_prompt, content_mode, cinematic=cinematic,
        )
        payload = {
            "model": self.model,
            "prompt": augmented,
            "width_and_height": f"{self.cfg.soul_width}x{self.cfg.soul_height}",
            "batch_size": 1,
            "custom_reference_id": soul_id,
            "custom_reference_strength": self.cfg.soul_reference_strength,
        }
        if self.cfg.soul_default_style_id:
            payload["style_id"] = self.cfg.soul_default_style_id
            payload["style_strength"] = 0.7  # razonable default

        try:
            async with HiggsfieldClient(self.cfg) as cli:
                result = await cli.submit_and_wait(self.cfg.soul_endpoint, payload)
                if not result.image_urls:
                    raise VisualGenerationError(
                        f"HiggsfieldSoul beat {beat.idx}: sin image_urls en respuesta"
                    )
                img_bytes = await cli.download(result.image_urls[0])
        except HiggsfieldAuthError as e:
            raise VisualGenerationError(f"Higgsfield auth: {e}") from e
        except HiggsfieldTimeoutError as e:
            raise VisualGenerationError(f"Higgsfield Soul timeout: {e}") from e
        except HiggsfieldError as e:
            raise VisualGenerationError(f"Higgsfield Soul error: {e}") from e

        if not img_bytes:
            raise VisualGenerationError(
                f"HiggsfieldSoul beat {beat.idx}: bytes vacíos"
            )
        raw_path.write_bytes(img_bytes)
        # Si las dimensiones ya son 9:16 exactas, _crop_to_9x16 es no-op.
        # Si no, recorta+resize a canvas_w del config global (default 720×1280).
        canvas_w = load_config().visual.canvas_w
        cropped = _crop_to_9x16(raw_path, final_path, target_w=canvas_w)

        logger.info(
            f"[higgsfield-soul] beat {beat.idx} frame con SoulId={soul_id[:8]}…"
        )

        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=cropped,
            video_path=None,
            source=VideoSource.HIGGSFIELD_SOUL,
            duration_s=0.0,
        )


# =============================================================================
# Helper: train a new SoulId from reference images
# =============================================================================


async def create_soul_id(
    name: str,
    reference_images: list[Path],
    credentials: str | None = None,
    *,
    strict_validation: bool = False,
) -> str:
    """Entrena un nuevo SoulId desde N imágenes de referencia y devuelve el ID.

    Valida el set contra el photo guide oficial de Higgsfield (5-20 fotos,
    8-12 sweet spot, variedad de ángulos/iluminación). Si `strict_validation=True`
    lanza error ante violaciones; sino solo loggea warnings.

    Útil para CLI: `contenido higgsfield train-soul <name> img1.jpg img2.jpg ...`
    """
    # Validación pre-flight según photo-guide oficial.
    warnings = validate_soul_training_set(reference_images, strict=strict_validation)
    for w in warnings:
        logger.warning(f"[soul-train] {w}")

    cfg = load_config().visual.higgsfield
    if credentials:
        cfg = cfg.model_copy(update={"credentials": credentials})

    async with HiggsfieldClient(cfg) as cli:
        # Upload todas las referencias
        urls = []
        for path in reference_images:
            if not path.exists():
                raise ValueError(f"reference image no existe: {path}")
            urls.append(await cli.upload_image(path))
        return await cli.create_soul_id(name=name, image_urls=urls, wait=True)
