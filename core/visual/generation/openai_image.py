"""First frames con la API de imágenes nativa de OpenAI (ADR-032).

`GeminiImageGenerator` no es un cliente de Google: llama al modelo de imagen de
Gemini **a través de OpenRouter**, así que exige `OPENROUTER_API_KEY`. Un repo
con solo una key de OpenAI se quedaba sin rama de IA visual y todos los beats
caían a placeholder sólido.

Este módulo habla directo con `POST /v1/images/generations`, la misma key que ya
mueve el DAG narrativo. Dos ventajas sobre la ruta vía OpenRouter:

- **Se puede pedir vertical.** `gpt-image-*` acepta `size="1024x1536"` (2:3), que
  recortado a 9:16 pierde ~10% de altura. Gemini devuelve cuadrado y el crop se
  come ~44% de la imagen — encuadres decapitados incluidos.
- Un proveedor menos en la cadena: menos latencia y sin margen de intermediario.

Reusa `_augment` y `_crop_to_9x16` de `gemini_image`: el style block y el crop
son idénticos y duplicarlos sería garantizar que diverjan.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from core.visual.canvas import current_aspect
from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.gemini_image import _augment, crop_to_canvas
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource

API_URL = "https://api.openai.com/v1/images/generations"
EDITS_URL = "https://api.openai.com/v1/images/edits"
REQUEST_TIMEOUT_S = 180.0

#: Tamaños que acepta la familia `gpt-image-*`. El vertical es el que queremos:
#: recortar 2:3 → 9:16 tira poca imagen; recortar 1:1 → 9:16 tira casi la mitad.
PORTRAIT_SIZE = "1024x1536"
_SIZE_BY_ASPECT = {"9:16": PORTRAIT_SIZE, "16:9": "1536x1024", "1:1": "1024x1024"}


async def _call_openai_image(
    *,
    prompt: str,
    model: str,
    api_key: str,
    size: str = PORTRAIT_SIZE,
    refs: list[Path] | None = None,
) -> bytes:
    """Devuelve los bytes PNG de la imagen generada.

    `gpt-image-*` responde siempre en `b64_json`; los modelos DALL·E antiguos
    pueden responder con `url`, así que se soportan ambos.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        if refs:
            # `images/edits` acepta varias imágenes de entrada: los retratos de
            # los personajes en cuadro. El modelo compone la escena del prompt
            # con esas caras, en vez de pegar un retrato de estudio como frame.
            files = [("image[]", (p.name, p.read_bytes(), "image/jpeg")) for p in refs[:4]]
            data = {k: str(v) for k, v in payload.items()}
            resp = await client.post(EDITS_URL, headers=headers, data=data, files=files)
        else:
            resp = await client.post(API_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise VisualGenerationError(
                f"OpenAIImage HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()

        items = data.get("data") or []
        if not items:
            raise VisualGenerationError("OpenAIImage: respuesta sin `data`")
        first = items[0]

        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):
            img = await client.get(first["url"])
            img.raise_for_status()
            return img.content

    raise VisualGenerationError("OpenAIImage: ni `b64_json` ni `url` en la respuesta")


class OpenAIImageGenerator(VisualGenerator):
    """Generador de first frames con `gpt-image-*` nativo."""

    name = "openai_image"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        canvas_w: int | None = None,
    ) -> None:
        cfg = load_config()
        self.model = model or cfg.visual.openai_image_model
        # La key sale del provider `openai` ya configurado — no de una env var
        # propia, para no tener dos sitios donde poner la misma credencial.
        self.api_key = api_key or cfg.get_llm_provider("openai").api_key
        self.canvas_w = canvas_w or cfg.visual.canvas_w

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
    ) -> BeatArtifact:
        """Genera el first frame del beat. Levanta si falla — el caller decide."""
        if not self.api_key:
            raise VisualGenerationError(
                "OpenAIImageGenerator: OPENAI_API_KEY no configurada"
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"frame-{beat.idx:02d}-raw.png"
        final_path = out_dir / f"frame-{beat.idx:02d}.jpg"

        img_bytes = await _call_openai_image(
            prompt=_augment(visual.image_prompt, content_mode),
            model=self.model,
            api_key=self.api_key,
            size=_SIZE_BY_ASPECT[current_aspect()],
            refs=[p for p in visual.character_refs if p.exists()],
        )
        if not img_bytes:
            raise VisualGenerationError(f"OpenAIImage: bytes vacíos en beat {beat.idx}")

        raw_path.write_bytes(img_bytes)
        cropped = crop_to_canvas(raw_path, final_path)
        logger.info(
            f"[openai_image] beat {beat.idx}: {self.model} → {cropped.name} "
            f"({len(img_bytes) // 1024} KB)"
        )
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=cropped,
            video_path=None,
            source=VideoSource.OPENAI_IMAGE,
            duration_s=0.0,
        )


__all__ = ["PORTRAIT_SIZE", "OpenAIImageGenerator"]
