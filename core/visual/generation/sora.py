"""Motion i2v con Sora 2 de OpenAI (ADR-033).

Cierra el último hueco del pipeline visual. Con solo una key de OpenAI, los
beats se quedaban en ken-burns sobre stills y el guard anti-slideshow (ADR-019)
lo cantaba correctamente:

    [ERROR] slideshow: 6/6 beats son estáticos (ratio 100% > umbral 60%)

Sora es la contraparte natural de `openai_image` (ADR-032): la **misma key**
que ya mueve el DAG narrativo y las imágenes mueve también el vídeo.

Diferencias con `VeoGenerator`, que condicionan el diseño
-------------------------------------------------------
- **Es un job asíncrono**, no una llamada que devuelve bytes: crear → sondear →
  descargar. Tarda ~90 s por clip de 4 s.
- **`input_reference` viaja como multipart**, no como data-URL en JSON.
- **`size="720x1280"` es nativo.** Coincide con el canvas del repo, así que a
  diferencia de todo lo demás aquí no hay recorte ni pérdida.
- **`seconds` solo acepta 4, 8 y 12** (verificado contra la API: un 5 devuelve
  `Invalid value`). Los buckets del repo son 4/6/8, así que hay que mapear.
- **El MP4 trae audio** generado por el modelo. El pipeline lo descarta al
  hacer stitch —la narración TTS manda— pero conviene saberlo.

El enlace de descarga **expira a las 48 h**, así que se baja en cuanto termina
en vez de guardar el id para después.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from core.visual.generation.base import GenerationTimeout, VisualGenerationError, VisualGenerator
from core.visual.generation.veo import _motion_clause
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource

API_BASE = "https://api.openai.com/v1/videos"

#: Duraciones que acepta la API. Verificado: `seconds=5` → HTTP 400.
VALID_SECONDS: tuple[int, ...] = (4, 8, 12)

#: Estados terminales del job.
_DONE = "completed"
_FAILED = "failed"


def _clamp_seconds(target: float) -> int:
    """Bucket válido más chico que cubra `target`.

    Se redondea hacia ARRIBA a propósito: un clip corto deja hueco negro al
    final del beat, uno largo simplemente se recorta en el stitch.
    """
    for s in VALID_SECONDS:
        if s >= target:
            return s
    return VALID_SECONDS[-1]


def _build_prompt(visual: BeatVisual) -> str:
    """Prompt de movimiento. Reusa `_motion_clause` de Veo — mismo mapeo.

    El first frame ya fija el contenido, así que el prompt describe sobre todo
    QUÉ SE MUEVE. Se pide movimiento contenido: en un reel de 4 s, una cámara
    agitada lee como error, no como estilo.
    """
    return (
        f"{visual.image_prompt}. {_motion_clause(visual.motion_hint)}. "
        "Subtle, restrained motion. Cinematic. No text or watermarks."
    )



def _sora_size() -> str:
    """Sora acepta 720x1280 y 1280x720; 1:1 no existe → vertical y se recorta después."""
    from core.visual.canvas import current_aspect

    return "1280x720" if current_aspect() == "16:9" else "720x1280"

class SoraGenerator(VisualGenerator):
    """Genera clips i2v con `sora-2` a partir del first frame del beat."""

    name = "sora"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        poll_interval_s: float | None = None,
        max_poll_time_s: float | None = None,
    ) -> None:
        cfg = load_config()
        self.model = model or cfg.visual.sora_model
        # Misma credencial que el DAG narrativo y que openai_image.
        self.api_key = api_key or cfg.get_llm_provider("openai").api_key
        self.poll_interval_s = poll_interval_s or cfg.visual.sora_poll_interval_s
        self.max_poll_time_s = max_poll_time_s or cfg.visual.sora_max_poll_time_s
        # Vacío = según el aspect del job en curso (ver `_sora_size`).
        self.size = ""

    # ------------------------------------------------------------------
    # Ciclo del job
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    async def _create_job(
        self, client: httpx.AsyncClient, *, prompt: str, seconds: int, frame: Path
    ) -> str:
        with frame.open("rb") as fh:
            resp = await client.post(
                API_BASE,
                headers=self._headers(),
                data={
                    "model": self.model,
                    "prompt": prompt,
                    "seconds": str(seconds),
                    "size": self.size or _sora_size(),
                },
                files={"input_reference": (frame.name, fh, "image/jpeg")},
            )
        if resp.status_code >= 400:
            raise VisualGenerationError(
                f"Sora create HTTP {resp.status_code}: {resp.text[:300]}"
            )
        job_id = (resp.json() or {}).get("id")
        if not job_id:
            raise VisualGenerationError("Sora: respuesta de creación sin `id`")
        return str(job_id)

    async def _await_job(self, client: httpx.AsyncClient, job_id: str) -> None:
        """Sondea hasta que el job termine. Levanta si falla o expira el plazo."""
        waited = 0.0
        last_progress = -1
        while waited < self.max_poll_time_s:
            resp = await client.get(f"{API_BASE}/{job_id}", headers=self._headers())
            if resp.status_code >= 400:
                raise VisualGenerationError(
                    f"Sora poll HTTP {resp.status_code}: {resp.text[:200]}"
                )
            data: dict[str, Any] = resp.json() or {}
            status = data.get("status")
            progress = int(data.get("progress") or 0)
            if progress != last_progress:
                logger.debug(f"[sora] {job_id[:20]}… {status} {progress}%")
                last_progress = progress

            if status == _DONE:
                return
            if status == _FAILED:
                err = (data.get("error") or {}).get("message") or "sin detalle"
                raise VisualGenerationError(f"Sora job falló: {err}")

            await asyncio.sleep(self.poll_interval_s)
            waited += self.poll_interval_s

        raise GenerationTimeout(
            f"Sora: job {job_id} no terminó en {self.max_poll_time_s:.0f}s"
        )

    async def _download(self, client: httpx.AsyncClient, job_id: str, dest: Path) -> Path:
        """Baja el MP4. El enlace expira a las 48 h — por eso se baja ya."""
        resp = await client.get(f"{API_BASE}/{job_id}/content", headers=self._headers())
        if resp.status_code >= 400:
            raise VisualGenerationError(
                f"Sora download HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if not resp.content:
            raise VisualGenerationError("Sora: contenido vacío")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        """Anima el first frame del beat. Levanta si falla — el caller decide."""
        if not self.api_key:
            raise VisualGenerationError("SoraGenerator: OPENAI_API_KEY no configurada")
        if first_frame_path is None or not first_frame_path.exists():
            raise VisualGenerationError(
                f"SoraGenerator: beat {beat.idx} sin first_frame_path (es i2v)"
            )

        seconds = _clamp_seconds(beat.target_duration_s)
        dest = out_dir / f"clip-{beat.idx:02d}.mp4"
        prompt = _build_prompt(visual)

        # Timeout del cliente holgado: el POST sube la imagen y el GET de
        # contenido baja megabytes; el que marca el ritmo es `_await_job`.
        async with httpx.AsyncClient(timeout=self.max_poll_time_s) as client:
            job_id = await self._create_job(
                client, prompt=prompt, seconds=seconds, frame=first_frame_path
            )
            logger.info(
                f"[sora] beat {beat.idx}: job {job_id[:24]}… ({seconds}s, {self.size or _sora_size()})"
            )
            await self._await_job(client, job_id)
            clip = await self._download(client, job_id, dest)

        logger.info(f"[sora] beat {beat.idx} ✓ {clip.name} ({clip.stat().st_size // 1024} KB)")
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=clip,
            source=VideoSource.SORA,
            duration_s=float(seconds),
        )


__all__ = ["VALID_SECONDS", "SoraGenerator"]
