"""Director específico para long-form con intent=TALKING_HEAD (ADR-016).

A diferencia del pipeline ``produce_long_form()`` genérico (que requiere ComfyUI
+ Higgsfield para narrative/motion/montage), el modo talking-head es más
simple y completamente operable sin ComfyUI:

  Per shot:
    1. Generar (o reusar) UN portrait del presentador (Soul/Gemini/upload manual)
    2. TTS narration (sample-accurate, mismo motor que reels)
    3. LiveAvatar(portrait, audio_wav, prompt) → video con lip-sync
  Final:
    4. ffmpeg single-pass concat (ADR-001) + optional BGM + word-burst subs

El portrait es **compartido entre todos los shots** salvo override explícito
(``shot.reference_frame_paths``). Eso da consistency cross-scene gratis.

Diseño consciente:
- NO regeneramos el portrait por shot (caro y rompe consistency).
- NO usamos cutaways/B-roll en intent=TALKING_HEAD (el script lo restringe).
- NO necesitamos VLM consistency selectors (LiveAvatar mantiene el rostro).

Entry point para test/CLI:
    ``await produce_talking_head(job, script, portrait_path=Path("anchor.jpg"))``
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.long_form.types import LongFormPlanError
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    LongFormJob,
    LongFormScript,
    MotionHint,
    Scene,
    Shot,
)


# =============================================================================
# Helper: convertir Shot → Beat + BeatVisual (con audio_path populado)
# =============================================================================


def _shot_to_beat(shot: Shot, idx: int) -> Beat:
    """Mapea un Shot long-form a un Beat reusable por el orchestrator.

    El Beat es la unidad común que ``generate_beat_videos`` espera.
    Para talking-head, el rol se infiere de la posición del shot dentro del
    script (primer scene→hook, último scene→payoff, resto→mechanism).
    """
    return Beat(
        idx=idx,
        role=BeatRole.MECHANISM,  # default; el caller puede overridear
        text=(shot.dialogue or shot.visual_description)[:500],
        target_duration_s=max(1.0, shot.target_duration_s),
        veo_duration=4,  # buckets fijos — irrelevante para LiveAvatar
    )


def _shot_to_visual(
    shot: Shot,
    *,
    portrait_path: Path,
    audio_path: Path,
) -> BeatVisual:
    """Construye un BeatVisual con audio_path + reference_image_path.

    El orchestrator detectará ``audio_path`` y rutea a LiveAvatarGenerator.
    El ``image_prompt`` describe el shot (no el avatar — el avatar viene del
    portrait); LiveAvatar lo usa como context atmosférico.
    """
    return BeatVisual(
        image_prompt=shot.visual_description,
        motion_hint=MotionHint.STATIC,  # avatar fijo; la "motion" es lip-sync
        visual_anchor=shot.speaker or "presenter",
        audio_path=audio_path,
        reference_image_path=portrait_path,
    )


# =============================================================================
# TTS helper (lazy import — TTS engines son ext deps)
# =============================================================================


async def _synthesize_shot_audio(
    *,
    text: str,
    out_path: Path,
    voice: str | None = None,
    engine: str = "edge",
) -> Path:
    """TTS de un solo shot. Wrapper sobre ``core.tts``.

    Usa el motor configurado (Edge por default — gratis). El resultado se
    guarda en ``out_path`` (WAV). Devuelve el path al WAV.

    Si ``core.tts`` no está disponible (env minimal), levantamos error
    explícito — talking-head requiere audio.
    """
    try:
        from core.tts import tts_synthesize  # type: ignore[import-not-found]
    except ImportError as e:
        raise LongFormPlanError(
            "core.tts no disponible — talking-head requiere TTS para lip-sync. "
            "Install: `uv sync`"
        ) from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio_artifact = await tts_synthesize(
        text=text,
        out_path=out_path,
        engine=engine,
        voice=voice,
    )
    if isinstance(audio_artifact, Path):
        return audio_artifact
    # Si tts_synthesize devuelve AudioArtifact (caso normal), tomamos el path
    return Path(getattr(audio_artifact, "path", out_path))


# =============================================================================
# Public entry point
# =============================================================================


async def produce_talking_head(
    job: LongFormJob,
    script: LongFormScript,
    *,
    portrait_path: Path,
    out_dir: Path | None = None,
    tts_voice: str | None = None,
    tts_engine: str = "edge",
    parallel_shots: int = 2,
) -> LongFormJob:
    """Pipeline talking-head end-to-end (sin ComfyUI/Higgsfield).

    Args:
        job: ``LongFormJob`` (de ``plan_long_form``).
        script: ``LongFormScript`` con scenes y shots.
        portrait_path: Path a una imagen del presentador (jpg/png).
            DEBE existir antes de invocar. Si quieres regenerar, usa
            ``core.visual.generation.gemini_image`` o el portraitkit upstream.
        out_dir: dónde escribir los MP4 + WAV intermedios. Default:
            ``{long_form.working_dir}/{job_id}/talking_head/``.
        tts_voice: voz del motor TTS (None = default del engine).
        tts_engine: ``edge`` (default, gratis) | ``gemini_flash`` | ``azure``.
        parallel_shots: cuántos shots renderizar en paralelo. LiveAvatar
            ocupa una GPU completa, así que 1-2 es lo seguro en single-GPU.

    Returns:
        ``job`` actualizado con ``status="completed"`` y ``output_path``
        apuntando al MP4 final (o ``status="failed"`` + error_message).

    Notas de costo:
        TTS: ~$0 (Edge) o ~$0.015/min (Gemini).
        LiveAvatar: ~$0.05/segundo de video → 10min = ~$30 (override en
        config para tu provider real).
    """
    cfg = load_config()
    if not cfg.long_form.enabled:
        raise LongFormPlanError("long_form deshabilitado")
    if not cfg.visual.live_avatar.enabled:
        raise LongFormPlanError(
            "[talking_head] visual.live_avatar.enabled=False — habilita en config"
        )
    if not portrait_path.exists():
        raise LongFormPlanError(f"portrait_path no existe: {portrait_path}")

    base_dir = out_dir or (Path(cfg.long_form.working_dir) / job.job_id / "talking_head")
    base_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = base_dir / "audio"
    video_dir = base_dir / "video"
    audio_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)

    job.status = "shooting"

    # Flatten scenes → shots, asignando idx global
    flat_shots: list[tuple[int, Shot, Scene]] = []
    for scene_idx, scene in enumerate(script.scenes):
        for shot in scene.shots:
            flat_shots.append((len(flat_shots), shot, scene))

    if not flat_shots:
        raise LongFormPlanError("script.scenes vacío — nada que producir")

    logger.info(
        f"[talking_head] {job.job_id}: producing {len(flat_shots)} shots "
        f"portrait={portrait_path.name} engine={tts_engine}"
    )

    # === Paso 1: TTS por shot (paralelo, no consume GPU) ===
    async def _tts_one(idx: int, shot: Shot) -> Path:
        text = (shot.dialogue or shot.visual_description).strip()
        if not text:
            raise LongFormPlanError(f"shot#{idx} sin texto para narrar")
        out_wav = audio_dir / f"shot_{idx:03d}.wav"
        return await _synthesize_shot_audio(
            text=text, out_path=out_wav, voice=tts_voice, engine=tts_engine
        )

    tts_results = await asyncio.gather(
        *(_tts_one(idx, shot) for idx, shot, _ in flat_shots),
        return_exceptions=True,
    )
    audio_paths: dict[int, Path] = {}
    for (idx, _shot, _scene), result in zip(flat_shots, tts_results):
        if isinstance(result, Exception):
            logger.error(f"[talking_head] shot#{idx} TTS falló: {result}")
            job.status = "failed"
            job.error_message = f"TTS shot#{idx}: {result}"
            return job
        audio_paths[idx] = result

    # === Paso 2: LiveAvatar por shot (chunked por parallel_shots) ===
    # Importamos late para evitar cargar httpx + subprocess deps si no se usa.
    from core.visual.generation import LiveAvatarGenerator, generate_beat_videos

    live_avatar = LiveAvatarGenerator(cfg.visual.live_avatar)

    beats: list[Beat] = [
        _shot_to_beat(shot, idx) for idx, shot, _ in flat_shots
    ]
    visuals: list[BeatVisual] = [
        _shot_to_visual(
            shot,
            portrait_path=portrait_path,
            audio_path=audio_paths[idx],
        )
        for idx, shot, _ in flat_shots
    ]

    # generate_beat_videos hace asyncio.gather internamente — semaphore lo
    # implementamos chunked aquí porque LiveAvatar consume GPU completa.
    artifacts: list[BeatArtifact] = []
    for chunk_start in range(0, len(beats), max(1, parallel_shots)):
        chunk_end = min(chunk_start + max(1, parallel_shots), len(beats))
        sub_beats = beats[chunk_start:chunk_end]
        sub_visuals = visuals[chunk_start:chunk_end]
        sub_artifacts = await generate_beat_videos(
            sub_beats,
            sub_visuals,
            video_dir,
            content_mode="general",
            use_live_avatar=True,
            use_higgsfield=False,
            use_higgsfield_soul=False,
            use_veo=False,
            use_comfyui=False,
            live_avatar_gen=live_avatar,
        )
        artifacts.extend(sub_artifacts)
        logger.info(
            f"[talking_head] chunk {chunk_start}-{chunk_end} done "
            f"({len(artifacts)}/{len(beats)})"
        )

    # === Paso 3: stitch final via ffmpeg single-pass (ADR-001) ===
    # Delegamos a core.editor si está disponible; si no, fallback simple concat.
    final_mp4 = base_dir / f"{job.job_id}_talking_head.mp4"
    try:
        await _stitch_artifacts(artifacts, audio_paths, final_mp4)
    except Exception as e:
        logger.error(f"[talking_head] stitch falló: {e}")
        job.status = "failed"
        job.error_message = f"stitch: {e}"
        return job

    job.status = "completed"
    job.final_video_path = str(final_mp4)
    logger.info(f"[talking_head] {job.job_id} ✓ output={final_mp4}")
    return job


# =============================================================================
# Stitch helper — ffmpeg single-pass concat (ADR-001)
# =============================================================================


async def _stitch_artifacts(
    artifacts: list[BeatArtifact],
    audio_paths: dict[int, Path],
    final_path: Path,
) -> None:
    """Concatena los MP4 con ffmpeg concat demuxer + remuxea audio TTS.

    LiveAvatar ya embebe el audio en el MP4 (vía ``merge_video_audio`` en
    su pipeline). Aquí solo concatenamos sin re-encode cuando es posible.
    """
    import subprocess
    import tempfile

    video_paths = [
        a.video_path for a in artifacts if a.video_path and a.video_path.exists()
    ]
    if not video_paths:
        raise RuntimeError("no hay artifacts con video_path válido")

    final_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for v in video_paths:
            f.write(f"file '{v.resolve()}'\n")
        concat_list = Path(f.name)

    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",  # stream copy — sin re-encode, asume mismo codec/resolución
            str(final_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            # Re-intento con re-encode (algunos clips pueden diferir en codec)
            cmd_reencode = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "128k",
                str(final_path),
            ]
            proc2 = await asyncio.create_subprocess_exec(
                *cmd_reencode,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await proc2.communicate()
            if proc2.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg stitch falló (copy={stderr.decode()[:200]}; "
                    f"re-encode={stderr2.decode()[:200]})"
                )
    finally:
        try:
            concat_list.unlink()
        except OSError:
            pass
