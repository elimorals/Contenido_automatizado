"""Generación image-to-video vía fal.ai (Kling / Runway / MiniMax). ADR-023.

fal.ai unifica varios modelos i2v tras una sola API REST. Es un fallback de motion
adicional (tier 2, junto a Veo/DoP) — opt-in por config (`visual.fal.enabled`).
Toma el first frame + motion prompt y produce un MP4. Sin SDK: httpx puro.
"""
from __future__ import annotations

import base64
from pathlib import Path

import httpx
from loguru import logger

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, MotionHint, VideoSource

FAL_RUN_URL = "https://fal.run"

# Variantes → model id de fal.ai (i2v).
_MODEL_IDS: dict[str, str] = {
    "kling": "fal-ai/kling-video/v1/standard/image-to-video",
    "runway": "fal-ai/runway-gen3/turbo/image-to-video",
    "minimax": "fal-ai/minimax/video-01/image-to-video",
}


def _model_id(variant: str) -> str:
    """Mapea 'kling'|'runway'|'minimax' al model id de fal. Un id completo pasa tal cual."""
    v = (variant or "").strip().lower()
    if "/" in variant:  # ya es un id completo de fal
        return variant
    if v not in _MODEL_IDS:
        raise ValueError(
            f"Variante fal desconocida '{variant}'. Usa {list(_MODEL_IDS)} o un id 'fal-ai/...'."
        )
    return _MODEL_IDS[v]


def _image_to_data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    if suffix == "jpg":
        suffix = "jpeg"
    return f"data:image/{suffix};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _motion_clause(hint: MotionHint | str) -> str:
    raw = hint.value if isinstance(hint, MotionHint) else str(hint)
    if raw == MotionHint.STATIC.value:
        return "Camera: static, no movement"
    return f"Camera: {raw.replace('_', ' ')}"


def _build_request(
    prompt: str, image_data_url: str, duration_s: int | None = None
) -> dict:
    """Payload JSON para fal i2v. `duration` se incluye sólo si se provee."""
    req: dict = {"image_url": image_data_url, "prompt": prompt}
    if duration_s is not None:
        req["duration"] = str(duration_s)
    return req


def _parse_response(data: dict) -> str | None:
    """Extrae la url del video de la respuesta de fal."""
    video = (data or {}).get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    videos = (data or {}).get("videos")
    if isinstance(videos, list) and videos and isinstance(videos[0], dict):
        return videos[0].get("url")
    return None


class FalProvider(VisualGenerator):
    name = "fal"

    def __init__(self, variant: str | None = None, api_key: str | None = None) -> None:
        cfg = load_config().visual.fal
        self._variant = variant or cfg.model
        self._api_key = api_key if api_key is not None else cfg.api_key
        self._timeout_s = 600.0

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        if not self._api_key:
            raise VisualGenerationError("fal: api_key no configurada (visual.fal.api_key)")
        if first_frame_path is None or not first_frame_path.exists():
            raise VisualGenerationError("fal: requiere first_frame_path válido (i2v)")

        model = _model_id(self._variant)
        prompt = f"{visual.image_prompt}. {_motion_clause(visual.motion_hint)}"
        req = _build_request(
            prompt=prompt,
            image_data_url=_image_to_data_url(first_frame_path),
            duration_s=int(beat.veo_duration),
        )
        headers = {"Authorization": f"Key {self._api_key}"}
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"fal-{beat.idx:02d}.mp4"

        async with httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=True) as client:
            resp = await client.post(f"{FAL_RUN_URL}/{model}", json=req, headers=headers)
            if resp.status_code >= 400:
                raise VisualGenerationError(
                    f"fal {model} HTTP {resp.status_code}: {resp.text[:200]}"
                )
            video_url = _parse_response(resp.json())
            if not video_url:
                raise VisualGenerationError(f"fal {model}: respuesta sin video url")
            dl = await client.get(video_url)
            dl.raise_for_status()
            out_path.write_bytes(dl.content)

        logger.success(f"[fal] beat {beat.idx}: i2v {model} OK")
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=out_path,
            source=VideoSource.FAL,
            duration_s=float(beat.veo_duration),
        )


__all__ = ["FalProvider"]
