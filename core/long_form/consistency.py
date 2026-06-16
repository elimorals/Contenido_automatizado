"""ReferenceImageSelector + BestImageSelector — VLM consistency cross-shot.

ReferenceImageSelector:
- Filtra los `max_reference_anchors` (default 8) mejores anchors históricos
- 2-stage: primero text-only (fast), luego multimodal (preciso)

BestImageSelector:
- Toma N candidatos generados en paralelo del MISMO shot
- VLM rankea por Character + Spatial + Description consistency
- Devuelve el path al mejor

LLM/VLM backend: `core.llm_router` con providers que soporten vision
(Gemini, OpenAI gpt-4o, Anthropic Claude — todos via OpenRouter).
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from core.llm_router import get_provider
from core.long_form.prompts import (
    BEST_IMAGE_SELECTOR_SYSTEM,
    REF_IMAGE_SELECTOR_MULTIMODAL_SYSTEM,
    REF_IMAGE_SELECTOR_TEXT_SYSTEM,
)
from core.long_form.types import LongFormError
from shared.config import load_config
from shared.schemas import ConsistencyAnchor


class _RefImageResponse(BaseModel):
    ref_image_indices: list[int] = Field(..., max_length=8)
    text_prompt: str = Field("", description="Guidance for image gen referring to selected images")


class _BestImageResponse(BaseModel):
    best_image_index: int = Field(..., ge=0)
    reason: str = Field("", max_length=600)


def _image_to_data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"data:image/{suffix};base64,{base64.b64encode(path.read_bytes()).decode()}"


class ReferenceImageSelector:
    """Selecciona qué reference images mostrar al image gen del próximo shot.

    Inputs:
    - `available_anchors`: lista de ConsistencyAnchor (frames previos + portraits)
    - `target_frame_description`: descripción textual del shot a generar

    Output:
    - subset de anchors (con sus paths) + text_prompt para guiar la generación
    """

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or cfg.vlm_model_provider
        self.model = cfg.vlm_model_name
        self.max_anchors = cfg.max_reference_anchors

    async def select(
        self,
        available_anchors: list[ConsistencyAnchor],
        target_frame_description: str,
    ) -> tuple[list[ConsistencyAnchor], str]:
        """Devuelve (subset, text_prompt) — ambos van al image generator."""
        if not available_anchors:
            return [], target_frame_description

        # Stage 1: text-only filter (si tenemos ≥8 anchors, reducimos primero)
        candidates = available_anchors
        if len(candidates) > self.max_anchors:
            candidates = await self._text_filter(candidates, target_frame_description)

        # Stage 2: multimodal selection (manda imágenes al VLM)
        if not candidates:
            return [], target_frame_description
        return await self._multimodal_select(candidates, target_frame_description)

    async def _text_filter(
        self,
        anchors: list[ConsistencyAnchor],
        target: str,
    ) -> list[ConsistencyAnchor]:
        descriptions = "\n".join(
            f"Image {i}: {a.description}" for i, a in enumerate(anchors)
        )
        user = (
            f"{descriptions}\n\n"
            f"<FRAME_DESC>\n{target}\n</FRAME_DESC>"
        )
        try:
            provider = get_provider(self.provider, model_name=self.model)
            response = await provider.complete_structured(
                prompt=user,
                schema=_RefImageResponse,
                system=REF_IMAGE_SELECTOR_TEXT_SYSTEM,
                temperature=0.0,
                max_tokens=600,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[long_form.consistency] text filter falló ({e}); "
                "uso los {self.max_anchors} más recientes"
            )
            return anchors[-self.max_anchors:]
        # Validar indices
        valid = [i for i in response.ref_image_indices if 0 <= i < len(anchors)]
        if not valid:
            return anchors[-self.max_anchors:]
        return [anchors[i] for i in valid[: self.max_anchors]]

    async def _multimodal_select(
        self,
        anchors: list[ConsistencyAnchor],
        target: str,
    ) -> tuple[list[ConsistencyAnchor], str]:
        """Manda images + descriptions al VLM, devuelve subset + text_prompt.

        Por simplicidad usamos `httpx` directo a OpenRouter chat/completions
        con `modalities=["image","text"]` — el patrón ya usado por GeminiImageGenerator.
        """
        # Construir messages multimodal estilo OpenAI
        content_items: list[dict] = []
        for i, anchor in enumerate(anchors):
            content_items.append({
                "type": "text",
                "text": f"Image {i}: {anchor.description}",
            })
            try:
                path = Path(anchor.frame_path)
                if path.exists():
                    content_items.append({
                        "type": "image_url",
                        "image_url": {"url": _image_to_data_url(path)},
                    })
            except OSError:
                continue
        content_items.append({
            "type": "text",
            "text": f"<FRAME_DESC>\n{target}\n</FRAME_DESC>",
        })

        try:
            provider = get_provider(self.provider, model_name=self.model)
            # Usamos complete() con custom messages — la mayoría de providers
            # OpenAI-compat soporta content como lista
            from core.llm_router.providers.openai_compatible import (
                OpenAICompatibleProvider,
            )
            if not isinstance(provider, OpenAICompatibleProvider):
                raise LongFormError(
                    f"VLM provider {self.provider} no es OpenAI-compat"
                )
            # Construir mensajes manualmente (multimodal)
            messages = [
                {"role": "system", "content": REF_IMAGE_SELECTOR_MULTIMODAL_SYSTEM},
                {"role": "user", "content": content_items},
            ]
            payload = {
                "model": provider.model_name,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 800,
                "response_format": {"type": "json_object"},
            }
            data = await provider._post(payload)
            text = provider._extract_text(data)
            response = _RefImageResponse.model_validate_json(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[long_form.consistency] multimodal selection falló ({e}); "
                f"uso TODOS los {len(anchors)} anchors"
            )
            return anchors, target

        valid = [i for i in response.ref_image_indices if 0 <= i < len(anchors)]
        selected = [anchors[i] for i in valid] if valid else anchors
        text_prompt = response.text_prompt or target
        return selected, text_prompt


class BestImageSelector:
    """Best-of-N: dados N candidatos, VLM elige el más consistente.

    Algoritmo (de ViMax):
    1. Para cada candidato i ∈ [0, N), se manda imagen + reference images + target text
    2. VLM rankea por Character / Spatial / Description consistency
    3. Devuelve path al mejor + reason
    """

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or cfg.vlm_model_provider
        self.model = cfg.vlm_model_name

    async def select_best(
        self,
        candidates: list[Path],
        reference_anchors: list[ConsistencyAnchor],
        target_description: str,
    ) -> tuple[Path, str]:
        """Devuelve (best_path, reason)."""
        if not candidates:
            raise LongFormError("BestImageSelector: 0 candidatos")
        if len(candidates) == 1:
            return candidates[0], "single candidate"

        # Construir mensaje multimodal
        content_items: list[dict] = []

        # Reference images (ground truth para consistency)
        for i, anchor in enumerate(reference_anchors):
            content_items.append({
                "type": "text",
                "text": f"Reference Image {i}: {anchor.description}",
            })
            try:
                p = Path(anchor.frame_path)
                if p.exists():
                    content_items.append({
                        "type": "image_url",
                        "image_url": {"url": _image_to_data_url(p)},
                    })
            except OSError:
                continue

        # Candidate images (a evaluar)
        for i, cand_path in enumerate(candidates):
            content_items.append({
                "type": "text",
                "text": f"Candidate Image {i}",
            })
            content_items.append({
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(cand_path)},
            })

        # Target description
        content_items.append({
            "type": "text",
            "text": (
                f"<TARGET_DESCRIPTION_START>\n{target_description}\n"
                f"<TARGET_DESCRIPTION_END>"
            ),
        })

        try:
            provider = get_provider(self.provider, model_name=self.model)
            from core.llm_router.providers.openai_compatible import (
                OpenAICompatibleProvider,
            )
            if not isinstance(provider, OpenAICompatibleProvider):
                raise LongFormError(
                    f"VLM provider {self.provider} no es OpenAI-compat"
                )
            messages = [
                {"role": "system", "content": BEST_IMAGE_SELECTOR_SYSTEM},
                {"role": "user", "content": content_items},
            ]
            payload = {
                "model": provider.model_name,
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 400,
                "response_format": {"type": "json_object"},
            }
            data = await provider._post(payload)
            text = provider._extract_text(data)
            response = _BestImageResponse.model_validate_json(text)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[long_form.consistency] best-of-N falló ({e}); uso primer candidate"
            )
            return candidates[0], f"fallback (VLM error: {e})"

        idx = response.best_image_index
        if idx < 0 or idx >= len(candidates):
            logger.warning(
                f"[long_form.consistency] VLM devolvió idx={idx} fuera de rango; uso 0"
            )
            return candidates[0], "fallback (out of range)"
        return candidates[idx], response.reason

    async def generate_and_select(
        self,
        generator_coros: list,
        reference_anchors: list[ConsistencyAnchor],
        target_description: str,
    ) -> tuple[Path, str]:
        """Helper: ejecuta N generador coros en paralelo y selecciona el mejor.

        `generator_coros` son corutinas que retornan `Path` (al frame generado).
        """
        if not generator_coros:
            raise LongFormError("generate_and_select: 0 generadores")
        candidates_results = await asyncio.gather(
            *generator_coros, return_exceptions=True,
        )
        valid_paths: list[Path] = []
        for r in candidates_results:
            if isinstance(r, Exception):
                logger.warning(f"[long_form.consistency] candidate gen falló: {r}")
                continue
            if isinstance(r, Path) and r.exists():
                valid_paths.append(r)
        if not valid_paths:
            raise LongFormError("0 candidatos válidos generados")
        return await self.select_best(valid_paths, reference_anchors, target_description)
