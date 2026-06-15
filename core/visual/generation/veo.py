"""Generación image-to-video con Google Veo 3.1 Lite vía OpenRouter.

Toma un first frame JPEG (720×1280) + motion prompt y produce un MP4
sized al bucket fijo del beat (4/6/8s). Si Veo falla, devuelve None
para que el caller aplique ken-burns fallback.
"""
from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import httpx

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, MotionHint, VideoSource

# OpenRouter endpoint para video gen (similar a chat completions pero
# con modalities=["video", "text"] y first_frame en el payload).
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _image_to_data_url(path: Path) -> str:
    """Encode local JPEG/PNG como data URL para Veo first_frame input."""
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return (
        f"data:image/{suffix};base64,"
        f"{base64.b64encode(path.read_bytes()).decode()}"
    )


def _motion_clause(hint: MotionHint | str) -> str:
    """Mapea BeatVisual.motion_hint a clause de Veo prompt.

    Veo acepta un único prompt string sin motion field separado, así que
    appendeamos "Camera: ...". `static` recibe phrasing explícito para
    que Veo no drifte.
    """
    raw = hint.value if isinstance(hint, MotionHint) else str(hint)
    if raw == MotionHint.STATIC.value:
        return "Camera: static, no movement"
    return f"Camera: {raw.replace('_', ' ')}"


async def _call_veo_i2v(
    prompt: str,
    first_frame_data_url: str,
    model: str,
    duration_s: int,
    api_key: str,
    timeout_s: float = 600.0,
) -> bytes:
    """Invoca Veo i2v y devuelve los bytes del MP4.

    Veo es long-running; usamos timeout amplio. Lanza
    `VisualGenerationError` si la respuesta no incluye video bytes.
    """
    if model.startswith("openrouter/"):
        model = model[len("openrouter/") :]

    payload = {
        "model": model,
        "modalities": ["video", "text"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": first_frame_data_url},
                    },
                ],
            }
        ],
        "video_config": {"duration_seconds": int(duration_s)},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(_OPENROUTER_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise VisualGenerationError(
                f"Veo HTTP {resp.status_code}: {resp.text[:300]}"
            )
        data = resp.json()

    try:
        choice = data["choices"][0]
        videos = choice["message"].get("videos") or []
        if not videos:
            raise VisualGenerationError(
                f"Veo: respuesta sin videos ({str(data)[:300]})"
            )
        url = videos[0].get("video_url", {}).get("url") or videos[0].get("url")
        if not url:
            raise VisualGenerationError("Veo: video URL ausente")
    except (KeyError, IndexError, TypeError) as e:
        raise VisualGenerationError(
            f"Veo: estructura de respuesta inesperada ({e})"
        ) from e

    if url.startswith("data:"):
        try:
            _, b64data = url.split(",", 1)
        except ValueError as e:
            raise VisualGenerationError(f"Veo: data URL malformed ({e})") from e
        return base64.b64decode(b64data)

    async with httpx.AsyncClient(timeout=300.0) as client:
        r2 = await client.get(url)
        if r2.status_code >= 400:
            raise VisualGenerationError(
                f"Veo: failed to fetch video URL ({r2.status_code})"
            )
        return r2.content


class VeoGenerator(VisualGenerator):
    """Generador i2v con Google Veo 3.1 Lite vía OpenRouter."""

    name = "veo"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        cfg = load_config()
        self.model = model or cfg.visual.veo_model
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY", "")

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        """Genera un clip Veo i2v para el beat.

        Requiere `first_frame_path` (Veo es i2v, no t2v puro). Lanza
        `VisualGenerationError` si no hay frame o si Veo falla.
        """
        if not self.api_key:
            raise VisualGenerationError(
                "VeoGenerator: OPENROUTER_API_KEY no configurada"
            )
        if first_frame_path is None or not first_frame_path.exists():
            raise VisualGenerationError(
                f"VeoGenerator: first_frame_path requerido para beat {beat.idx}"
            )

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"clip-{beat.idx:02d}.mp4"

        prompt = (
            f"{visual.image_prompt}. {_motion_clause(visual.motion_hint)}."
        )
        first_frame_url = _image_to_data_url(first_frame_path)
        video_bytes = await _call_veo_i2v(
            prompt=prompt,
            first_frame_data_url=first_frame_url,
            model=self.model,
            duration_s=int(beat.veo_duration),
            api_key=self.api_key,
        )
        if not video_bytes:
            raise VisualGenerationError(
                f"VeoGenerator: bytes vacíos para beat {beat.idx}"
            )
        out_path.write_bytes(video_bytes)
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=out_path,
            source=VideoSource.VEO,
            duration_s=float(beat.veo_duration),
        )


async def generate_veo_clip(
    beat: Beat,
    visual: BeatVisual,
    first_frame_path: Path,
    out_dir: Path,
    model: str | None = None,
    api_key: str | None = None,
) -> Path | None:
    """Helper directo: genera un Veo clip y devuelve path al MP4.

    Devuelve None si Veo falla — el caller decide fallback (ken-burns).
    """
    try:
        gen = VeoGenerator(model=model, api_key=api_key)
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
    except (httpx.HTTPError, asyncio.TimeoutError):
        return None
