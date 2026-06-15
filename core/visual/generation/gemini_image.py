"""Generación de first frames vía Gemini 2.5 Flash Image (OpenRouter).

Cada beat necesita una imagen still 720×1280 (vertical 9:16, resolución
nativa de Veo). Llamamos a Gemini Image (cuadrado ~1024×1024), guardamos
el raw y hacemos center-crop local a 9:16.

Style blocks varían por content_mode para que reels científicos no
terminen pareciendo anuncios de perfume.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import httpx
from PIL import Image

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource

# Style blocks (EXACTOS del original reels-af).
_GENERAL_STYLE_NOTE = (
    "cinematic documentary still, warm natural light, shallow depth of field, "
    "35mm film grain, VERTICAL portrait composition (taller than wide, the "
    "subject occupies the upper-middle two-thirds), fills the frame, no text "
    "or letters"
)

_SCIENTIFIC_STYLE_NOTE = (
    "documentary photograph from a working research lab, sharp focus throughout, "
    "neutral white-balanced lighting (overhead fluorescent or a single bright "
    "desk lamp, no warm filters), realistic colors, no shallow depth-of-field "
    "blur, no film grain, no lens flares; VERTICAL portrait composition with "
    "the artifact (plot / paper / interface / instrument) occupying the upper "
    "two-thirds; the frame should look like a phone snapshot of an actual "
    "research workspace, not a movie still; no text or letters in frame"
)

# OpenRouter endpoint para chat/completions con modelos image-gen.
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _style_note(content_mode: str) -> str:
    return (
        _SCIENTIFIC_STYLE_NOTE
        if content_mode == "scientific"
        else _GENERAL_STYLE_NOTE
    )


def _augment(prompt: str, content_mode: str = "general") -> str:
    """Append style block (mode-aware) al image prompt."""
    base = prompt.strip().rstrip(".")
    return f"{base}. {_style_note(content_mode)}."


def _crop_to_9x16(src: Path, dest: Path, target_w: int = 720) -> Path:
    """Center-crop a 9:16 vertical para input de Veo i2v.

    Gemini devuelve ~cuadrado (1024×1024); Veo espera vertical 9:16.
    Tomamos una franja centrada 9:16 y resize a 720×1280 (res nativa Veo).

    NO pasamos `image_config={"aspect_ratio": "9:16"}` al SDK — ningún
    provider upstream de OpenRouter expone el param, así que requests
    con él dan 404. Recortamos local.
    """
    target_h = target_w * 16 // 9
    img = Image.open(src).convert("RGB")
    w, h = img.size
    desired_ratio = 9 / 16
    cur_ratio = w / h
    if cur_ratio > desired_ratio:
        new_w = int(h * desired_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    elif cur_ratio < desired_ratio:
        new_h = int(w / desired_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest), format="JPEG", quality=92)
    return dest


async def _call_gemini_image(
    prompt: str,
    model: str,
    api_key: str,
    timeout_s: float = 120.0,
) -> bytes:
    """Invoca Gemini Image vía OpenRouter chat/completions y devuelve PNG bytes.

    OpenRouter expone modelos image-gen vía chat completions usando
    `modalities=["image", "text"]`. La respuesta incluye una imagen
    base64 en `choices[0].message.images[0].image_url.url` (data URL).

    Lanza `VisualGenerationError` si no hay imagen en la respuesta.
    """
    # Strip prefijo "openrouter/" si vino del config.
    if model.startswith("openrouter/"):
        model = model[len("openrouter/") :]

    payload = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise VisualGenerationError(
                f"Gemini Image HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()

    try:
        choice = data["choices"][0]
        images = choice["message"].get("images") or []
        if not images:
            raise VisualGenerationError(
                f"Gemini Image: respuesta sin imágenes ({str(data)[:300]})"
            )
        url = images[0]["image_url"]["url"]
    except (KeyError, IndexError, TypeError) as e:
        raise VisualGenerationError(
            f"Gemini Image: estructura de respuesta inesperada ({e})"
        ) from e

    # Data URL: "data:image/png;base64,...."
    if not url.startswith("data:"):
        # Algunos providers devuelven URL directa — fetcheamos.
        async with httpx.AsyncClient(timeout=60.0) as client:
            r2 = await client.get(url)
            if r2.status_code >= 400:
                raise VisualGenerationError(
                    f"Gemini Image: failed to fetch image URL ({r2.status_code})"
                )
            return r2.content
    try:
        _, b64data = url.split(",", 1)
    except ValueError as e:
        raise VisualGenerationError(f"Gemini Image: data URL malformed ({e})") from e
    return base64.b64decode(b64data)


class GeminiImageGenerator(VisualGenerator):
    """Generador de first frames usando Gemini Image vía OpenRouter."""

    name = "gemini_image"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        canvas_w: int | None = None,
    ) -> None:
        cfg = load_config()
        self.model = model or cfg.visual.gemini_image_model
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
        self.canvas_w = canvas_w or cfg.visual.canvas_w

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
    ) -> BeatArtifact:
        """Genera un first frame 720×1280 para el beat.

        Llama Gemini Image, guarda raw, center-crop a 9:16. Lanza
        `VisualGenerationError` en fallo — el caller decide fallback.
        """
        if not self.api_key:
            raise VisualGenerationError(
                "GeminiImageGenerator: OPENROUTER_API_KEY no configurada"
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"frame-{beat.idx:02d}-raw.png"
        final_path = out_dir / f"frame-{beat.idx:02d}.jpg"

        augmented = _augment(visual.image_prompt, content_mode)
        img_bytes = await _call_gemini_image(
            prompt=augmented,
            model=self.model,
            api_key=self.api_key,
        )
        if not img_bytes:
            raise VisualGenerationError(
                f"GeminiImage: bytes vacíos para beat {beat.idx}"
            )
        raw_path.write_bytes(img_bytes)
        cropped = _crop_to_9x16(raw_path, final_path, target_w=self.canvas_w)
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=cropped,
            video_path=None,
            source=VideoSource.GEMINI_IMAGE,
            duration_s=0.0,
        )


async def generate_first_frame(
    image_prompt: str,
    idx: int,
    out_dir: Path,
    content_mode: str = "general",
    model: str | None = None,
    api_key: str | None = None,
) -> Path:
    """Helper directo: genera un first frame (sin Beat/BeatVisual wrappers).

    Útil para tests y para callers que ya tienen el prompt construido.
    Devuelve el path al JPEG final 720×1280.
    """
    cfg = load_config()
    resolved_model = model or cfg.visual.gemini_image_model
    resolved_key = api_key or os.getenv("OPENROUTER_API_KEY", "")
    if not resolved_key:
        raise VisualGenerationError(
            "generate_first_frame: OPENROUTER_API_KEY no configurada"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"frame-{idx:02d}-raw.png"
    final_path = out_dir / f"frame-{idx:02d}.jpg"
    augmented = _augment(image_prompt, content_mode)
    img_bytes = await _call_gemini_image(
        prompt=augmented,
        model=resolved_model,
        api_key=resolved_key,
    )
    if not img_bytes:
        raise VisualGenerationError(
            f"generate_first_frame: bytes vacíos para beat {idx}"
        )
    raw_path.write_bytes(img_bytes)
    return _crop_to_9x16(raw_path, final_path, target_w=cfg.visual.canvas_w)
