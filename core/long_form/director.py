"""Director — orquestador top-level del pipeline long-form.

Reusa TODO lo que ya existe:
- `core.llm_router` para script planning + scene extraction + storyboard
- `core.tts` para narration de cada shot (sample-accurate timing)
- `core.visual.generation.comfy` para shot rendering con LoRA + ControlNet/IPAdapter
- `core.editor` para stitch final ffmpeg single-pass

Output: 1 MP4 de 5-60 min con narration + word-burst subs.

Modelo de 2 fases:
1. `plan_long_form()` — input → LongFormScript persisted to disk (cheap, ~$1-3)
2. `produce_long_form()` — script → shots → final video (expensive, ~$5-30)

Razón: el gate humano editorial se ajusta natural — generas el script, lo
revisas/editas a mano (o con LLM), luego lanzas el shooting.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from loguru import logger

from core.long_form.compressor import NovelCompressor
from core.long_form.consistency import BestImageSelector, ReferenceImageSelector
from core.long_form.rag import RAGStore, chunk_text
from core.long_form.scenes import SceneExtractor, StoryboardArtist
from core.long_form.script_planner import ScriptPlanner, detect_intent
from core.long_form.types import LongFormPlanError
from shared.config import load_config
from shared.schemas import (
    CharacterProfile,
    LongFormIntent,
    LongFormJob,
    LongFormScript,
    NarrativeArc,
    Scene,
)


# =============================================================================
# Fase 1: PLAN
# =============================================================================


async def plan_long_form(
    *,
    source_text: str,
    source_kind: str = "idea",
    target_minutes: float = 10.0,
    intent: LongFormIntent | None = None,
    characters_hint: list[CharacterProfile] | None = None,
    job_id: str | None = None,
    user_requirement: str = "",
) -> tuple[LongFormScript, LongFormJob]:
    """Genera el script completo (sin renderizar nada).

    Pipeline:
    1. (Si source_kind=novel) Compress + chunk + index en RAG
    2. ScriptPlanner: source → NarrativeArc (3 actos)
    3. SceneExtractor: arc → N scenes
    4. StoryboardArtist: cada scene → M shots con decomposition
    5. Persistir todo en working_dir/<job_id>/script.json

    Returns: (script, job) — script para inspección, job para producción downstream.
    """
    cfg = load_config().long_form
    if not cfg.enabled:
        raise LongFormPlanError(
            "long_form deshabilitado. Setea LONG_FORM_ENABLED=true en .env"
        )
    if target_minutes < 0.5 or target_minutes > cfg.max_target_minutes:
        raise LongFormPlanError(
            f"target_minutes {target_minutes} fuera de [0.5, {cfg.max_target_minutes}]"
        )

    job_id = job_id or str(uuid.uuid4())[:8]
    working_dir = Path(cfg.working_dir) / job_id
    working_dir.mkdir(parents=True, exist_ok=True)

    job = LongFormJob(
        job_id=job_id,
        source_kind=source_kind,  # type: ignore[arg-type]
        target_minutes=target_minutes,
        intent=intent or LongFormIntent.NARRATIVE,
        status="planning",
    )
    t_start = time.monotonic()

    # === 1. Compress + chunk (solo si es novel grande) ===
    if source_kind == "novel" and len(source_text) > 20_000:
        logger.info(f"[long_form.director] {job_id}: compressing novel ({len(source_text)} chars)")
        compressor = NovelCompressor()
        chunks = compressor.split(source_text)
        results = await compressor.compress_all(chunks)
        source_text_for_planning = compressor.aggregate_compressed(results)

        # Persistir chunks + RAG store (útil para downstream retrieval)
        chunks_dir = working_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)
        for i, chunk in enumerate(chunks):
            (chunks_dir / f"chunk_{i:03d}.txt").write_text(chunk, encoding="utf-8")
        compressed_dir = working_dir / "compressed"
        compressed_dir.mkdir(exist_ok=True)
        for r in results:
            (compressed_dir / f"chunk_{r.index:03d}.txt").write_text(
                r.compressed_text, encoding="utf-8"
            )

        # Index in RAG store (para queries downstream tipo "what does X say in chapter 3?")
        try:
            store = RAGStore()
            store.add(
                chunks=[r.compressed_text for r in results],
                metadatas=[{"chunk_index": r.index} for r in results],
            )
            store.save_to_dir(working_dir / "rag")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[long_form.director] {job_id}: RAG indexing falló ({e}); continúo sin store")

        job.chunks_dir = str(chunks_dir)
        job.compressed_dir = str(compressed_dir)
        job.total_chunks = len(chunks)
    else:
        source_text_for_planning = source_text

    # === 2. Detect intent + plan arc ===
    actual_intent = intent or await detect_intent(source_text_for_planning)
    job.intent = actual_intent
    planner = ScriptPlanner()
    arc: NarrativeArc = await planner.plan(
        basic_idea=source_text_for_planning,
        target_minutes=target_minutes,
        intent=actual_intent,
    )
    t_planned = time.monotonic()
    job.timings_s["plan_arc"] = round(t_planned - t_start, 2)

    # === 3. Extract scenes ===
    characters = list(characters_hint or [])
    extractor = SceneExtractor()
    # Aim for roughly 0.6-1.0 scenes per minute
    target_scenes = max(3, int(target_minutes * 0.7))
    max_scenes = min(12, int(target_minutes * 1.2))
    scenes: list[Scene] = await extractor.extract(
        arc, characters, target_scenes=target_scenes, max_scenes=max_scenes,
    )
    t_scenes = time.monotonic()
    job.timings_s["extract_scenes"] = round(t_scenes - t_planned, 2)
    job.total_scenes = len(scenes)

    # === 4. Storyboard cada scene (paralelo limitado) ===
    artist = StoryboardArtist()
    # Aim for 4-10 shots per scene
    avg_shots = max(4, min(10, int((target_minutes * 60) / (len(scenes) * 5))))
    for scene in scenes:
        scene.shots = await artist.draw_scene(
            scene, characters,
            user_requirement=user_requirement,
            min_shots=max(3, avg_shots - 2),
            max_shots=avg_shots + 4,
        )
    t_storyboard = time.monotonic()
    job.timings_s["storyboard"] = round(t_storyboard - t_scenes, 2)
    job.total_shots = sum(len(s.shots) for s in scenes)

    # === 5. Build LongFormScript + persist ===
    import hashlib
    script = LongFormScript(
        arc=arc,
        intent=actual_intent,
        characters=characters,
        scenes=scenes,
        source_kind=source_kind,  # type: ignore[arg-type]
        source_text_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:16],
    )

    script_path = working_dir / "script.json"
    script_path.write_text(
        script.model_dump_json(indent=2, exclude_none=True),
        encoding="utf-8",
    )
    job.script_path = str(script_path)
    job.status = "planning"  # technically completed planning, not yet shooting

    job_path = working_dir / "job.json"
    job_path.write_text(job.model_dump_json(indent=2), encoding="utf-8")

    logger.info(
        f"[long_form.director] {job_id}: PLAN done — {len(scenes)} scenes × "
        f"{job.total_shots // len(scenes) if scenes else 0} avg shots = "
        f"{job.total_shots} shots, est {script.estimated_duration_s/60:.1f}min, "
        f"saved to {script_path}"
    )
    return script, job


# =============================================================================
# Fase 2: PRODUCE
# =============================================================================


async def produce_long_form(
    job: LongFormJob,
    script: LongFormScript | None = None,
    *,
    portrait_path: Path | None = None,
    tts_voice: str | None = None,
    tts_engine: str = "edge",
) -> LongFormJob:
    """Renderiza shots + stitch final.

    Branching por intent:

    - ``intent == TALKING_HEAD`` → delega a ``produce_talking_head()``
      (concreto, NO requiere ComfyUI/Higgsfield; usa LiveAvatar + TTS + ffmpeg).
      Requiere ``portrait_path`` o ``job.portraits_dir`` con el anchor del
      presentador resuelto upstream.

    - Otros intents (NARRATIVE/MOTION/MONTAGE) → STUB legacy. Flujo esperado:
      1. Para cada character sin portrait → generate_portrait (ComfyUI flux_basic)
      2. Para cada scene:
         a. Para cada shot: build prompt + reference_anchors + N candidates
         b. BestImageSelector picks one
         c. Higgsfield DoP / Veo / ComfyUI AnimateDiff: first frame → motion clip
         d. Update consistency anchors
      3. TTS narration per shot dialogue (sample-accurate)
      4. ffmpeg stitch all clips + audio + subs
    """
    if script is None:
        if job.script_path is None or not Path(job.script_path).exists():
            raise LongFormPlanError("produce: ni `script` arg ni job.script_path válido")
        script = LongFormScript.model_validate_json(
            Path(job.script_path).read_text(encoding="utf-8")
        )

    cfg = load_config().long_form
    if not cfg.enabled:
        raise LongFormPlanError("long_form deshabilitado")

    # === Branch: talking-head (ADR-016) ===
    if job.intent == LongFormIntent.TALKING_HEAD:
        from core.long_form.talking_head_director import produce_talking_head

        # Resolver portrait: arg explícito > job.portraits_dir/anchor.* > error
        if portrait_path is None and job.portraits_dir:
            portraits = sorted(Path(job.portraits_dir).glob("anchor.*"))
            portrait_path = portraits[0] if portraits else None
        if portrait_path is None:
            job.status = "failed"
            job.error_message = (
                "talking_head: portrait_path no resuelto. Pasa `portrait_path=`"
                " o set job.portraits_dir/anchor.{jpg,png}"
            )
            return job
        return await produce_talking_head(
            job,
            script,
            portrait_path=Path(portrait_path),
            tts_voice=tts_voice,
            tts_engine=tts_engine,
        )

    # === STUB legacy (narrative/motion/montage) ===
    job.status = "shooting"
    working_dir = Path(cfg.working_dir) / job.job_id

    # Por ahora, instanciar selectors para validar dependencias OK
    ref_selector = ReferenceImageSelector()
    best_selector = BestImageSelector()
    logger.info(
        f"[long_form.director] {job.job_id}: PRODUCE phase wiring OK — "
        f"ReferenceImageSelector({ref_selector.model}) + "
        f"BestImageSelector({best_selector.model})"
    )

    # TODO real: per-shot generation con ComfyUI + Higgsfield + TTS + stitch.
    # Ver docs/LONG_FORM.md sección "Production (TODO: requires GPU)"

    # Por ahora, marcar como pending hasta tener GPU para validar E2E
    job.status = "pending"
    job.error_message = (
        "produce phase requires GPU + ComfyUI running. "
        "See docs/LONG_FORM.md for the production pipeline."
    )
    return job


# =============================================================================
# Director high-level API
# =============================================================================


class Director:
    """Wrapper agradable sobre `plan_long_form` + `produce_long_form`."""

    async def plan(self, **kwargs) -> tuple[LongFormScript, LongFormJob]:
        return await plan_long_form(**kwargs)

    async def produce(
        self,
        job: LongFormJob,
        script: LongFormScript | None = None,
        *,
        portrait_path: Path | None = None,
        tts_voice: str | None = None,
        tts_engine: str = "edge",
    ) -> LongFormJob:
        return await produce_long_form(
            job,
            script,
            portrait_path=portrait_path,
            tts_voice=tts_voice,
            tts_engine=tts_engine,
        )

    @staticmethod
    def load_job(job_id: str) -> tuple[LongFormJob, LongFormScript]:
        """Recupera un job + script previamente persistido."""
        cfg = load_config().long_form
        working_dir = Path(cfg.working_dir) / job_id
        job_path = working_dir / "job.json"
        script_path = working_dir / "script.json"
        if not job_path.exists():
            raise LongFormPlanError(f"job {job_id} no encontrado en {working_dir}")
        if not script_path.exists():
            raise LongFormPlanError(f"script {job_id} no encontrado en {working_dir}")
        job = LongFormJob.model_validate_json(job_path.read_text(encoding="utf-8"))
        script = LongFormScript.model_validate_json(script_path.read_text(encoding="utf-8"))
        return job, script
