"""FastAPI app — entrypoint principal.

Implementación real (Ola 4):
- POST /videos encola tareas en queue (memory o Redis), worker las procesa.
- GET /tasks/{id}, /tasks (paginado), DELETE /tasks/{id} contra StateManager.
- Endpoints utilitarios (/scripts, /terms, /audio, /subtitle, /narratives,
  /hunters) ejecutan la pieza relevante del pipeline en proceso (no encolan).
- Health checks reales que verifican conectividad de queue/state.

Las rutas críticas NUNCA crashean el server: si una sub-fase falla, se
devuelve 500 con el detalle, pero el state queda consistente y el server
sigue procesando otras requests.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field

from apps.api.pipeline import (
    _adapt_conversational_to_draft,
    _build_synthetic_essence,
)
from core.distribution import get_distribution_service
from core.llm_router import complete
from core.narrative import (
    app as narrative_app,
)
from core.narrative import (
    compose_script,
    extract_essence,
    hunt_cross_domain,
    hunt_reversal,
    hunt_specific_figure,
    hunt_temporal,
    pick_best_narration,
    pick_top_essences,
    write_narrations,
)
from core.reference import ReferenceAnalysisError, analyze_reference
from core.subtitles import (
    subtitles_from_word_timings,
    write_reel_ass_with_accents,
    write_srt,
)
from core.tts import detect_engine_and_voice, get_engine
from orchestration.queue import QueueFullError, get_task_manager
from orchestration.state import get_state_manager
from shared.config import load_config
from shared.schemas import (
    BaseResponse,
    ScriptDraft,
    SubtitleStyle,
    TaskInfo,
    TaskState,
    VideoParams,
)

# =============================================================================
# Lifespan: queue + state + distribution boot/shutdown
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa queue, state, distribution. Cierra en shutdown."""
    config = load_config()
    app.state.config = config
    # Factories son sync — devuelven la instancia ya conectada (Redis usa
    # lazy connection, así que no hay nada que await acá).
    app.state.task_manager = get_task_manager(config)
    app.state.state_manager = get_state_manager(config)
    app.state.distribution = get_distribution_service(config)
    logger.info(
        f"[contenido] API arranca: env={config.app.env}, "
        f"default_mode={config.app.default_mode}, "
        f"redis={config.app.enable_redis}, "
        f"distribution={'on' if app.state.distribution else 'off'}"
    )
    try:
        yield
    finally:
        logger.info("[contenido] API shutdown — cerrando recursos")
        try:
            await app.state.task_manager.close()
        except Exception as e:  # pragma: no cover
            logger.warning(f"task_manager close: {e}")
        try:
            await app.state.state_manager.close()
        except Exception as e:  # pragma: no cover
            logger.warning(f"state_manager close: {e}")


app = FastAPI(
    title="contenido",
    description="Plataforma de generación de reels — fusión MPT × reels-af",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Health
# =============================================================================


@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Health check real: verifica queue + state están operativos."""
    queue_ok = False
    state_ok = False
    queue_size: int | None = None
    try:
        queue_size = await request.app.state.task_manager.queue_size()
        queue_ok = True
    except Exception as e:
        logger.warning(f"health: queue check falló: {e}")
    try:
        # Listar 0 items es la operación más barata sobre el state.
        await request.app.state.state_manager.list(page=1, page_size=1)
        state_ok = True
    except Exception as e:
        logger.warning(f"health: state check falló: {e}")

    overall = "ok" if queue_ok and state_ok else "degraded"
    return {
        "status": overall,
        "version": "0.1.0",
        "env": os.getenv("CONTENIDO_ENV", "dev"),
        "queue": {"connected": queue_ok, "size": queue_size},
        "state": {"connected": state_ok},
        "distribution": request.app.state.distribution is not None,
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "contenido",
        "docs": "/docs",
        "health": "/health",
    }


# =============================================================================
# Videos: encolá y devolvé task_id (no espera al pipeline)
# =============================================================================


@app.post("/videos", response_model=BaseResponse, status_code=202)
async def create_video(params: VideoParams, request: Request) -> BaseResponse:
    """Crea una task de video. Encola y vuelve inmediatamente con task_id.

    Dispatch (article|topic|subject) lo decide el worker via `run_pipeline`.
    """
    cfg = request.app.state.config
    queue = request.app.state.task_manager
    state = request.app.state.state_manager

    # Backpressure rápido: chequea size antes de intentar encolar.
    try:
        size = await queue.queue_size()
    except Exception as e:
        # Si el queue está caído, no podemos aceptar más trabajo.
        logger.error(f"create_video: queue.queue_size falló: {e}")
        raise HTTPException(status_code=503, detail=f"queue unavailable: {e}") from e

    if size >= cfg.app.max_queued_tasks:
        raise HTTPException(
            status_code=429,
            detail=f"queue full ({size}/{cfg.app.max_queued_tasks}), retry later",
        )

    task_id = str(uuid.uuid4())

    # Crear TaskInfo en QUEUED ANTES de encolar — así si el worker es
    # rapidísimo y dequeue ocurre antes del set, igual encuentra el state.
    info = TaskInfo(
        task_id=task_id,
        state=TaskState.QUEUED,
        mode=params.mode,
        params=params,
        progress=0,
    )
    await state.set(task_id, info)

    try:
        await queue.enqueue(task_id, params)
    except QueueFullError as e:
        # Race: alguien encoló entre el size check y nuestro enqueue. Limpiar.
        await state.delete(task_id)
        raise HTTPException(status_code=429, detail=str(e)) from e
    except Exception as e:
        await state.delete(task_id)
        logger.error(f"create_video: enqueue falló: {e}")
        raise HTTPException(status_code=503, detail=f"enqueue failed: {e}") from e

    new_size = await queue.queue_size()
    return BaseResponse(
        status=202,
        message="task queued",
        data={
            "task_id": task_id,
            "state": TaskState.QUEUED.value,
            "queue_position": new_size,
        },
    )


# =============================================================================
# Tasks (estado, listado, cancelación)
# =============================================================================


@app.get("/tasks/{task_id}", response_model=BaseResponse)
async def get_task(task_id: str, request: Request) -> BaseResponse:
    state = request.app.state.state_manager
    info = await state.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return BaseResponse(data=info.model_dump(mode="json"))


@app.get("/tasks", response_model=BaseResponse)
async def list_tasks(
    request: Request, page: int = 1, page_size: int = 10
) -> BaseResponse:
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be >= 1")
    if not (1 <= page_size <= 100):
        raise HTTPException(status_code=400, detail="page_size must be in [1, 100]")
    state = request.app.state.state_manager
    tasks, total = await state.list(page, page_size)
    return BaseResponse(
        data={
            "tasks": [t.model_dump(mode="json") for t in tasks],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@app.delete("/tasks/{task_id}", response_model=BaseResponse)
async def delete_task(task_id: str, request: Request) -> BaseResponse:
    state = request.app.state.state_manager
    ok = await state.delete(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Best-effort cleanup de artifacts en disco.
    out_dir = Path("./output") / task_id
    if out_dir.exists():
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception as e:  # pragma: no cover
            logger.warning(f"delete_task: rmtree falló (non-fatal): {e}")

    return BaseResponse(message=f"task {task_id} deleted")


# =============================================================================
# Costs / timings (vistas individuales sobre TaskInfo)
# =============================================================================


@app.get("/costs/{task_id}", response_model=BaseResponse)
async def get_costs(task_id: str, request: Request) -> BaseResponse:
    state = request.app.state.state_manager
    info = await state.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return BaseResponse(
        data={
            "task_id": task_id,
            "cost_breakdown": dict(info.cost_breakdown),
            "total_usd": sum(info.cost_breakdown.values()),
        }
    )


@app.get("/timings/{task_id}", response_model=BaseResponse)
async def get_timings(task_id: str, request: Request) -> BaseResponse:
    state = request.app.state.state_manager
    info = await state.get(task_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return BaseResponse(
        data={
            "task_id": task_id,
            "timings_s": dict(info.timings_s),
            "progress": info.progress,
            "state": info.state.value,
        }
    )


# =============================================================================
# Endpoints individuales (script, terms, audio, subtitle, narratives, hunters)
# =============================================================================

_SUBJECT_SCRIPT_SYSTEM = """You are a viral short-form video script writer.
Write a tight, engaging spoken narration. Plain language. No "did you know",
no "hey guys", no CTAs. Every sentence is a beat downstream. No padding."""

_SUBJECT_TERMS_SYSTEM = """You return ONLY a JSON list of 5-8 short search
terms (1-3 words each) suitable for stock footage providers (Pexels/Pixabay).
Format strictly: ["term one", "term two", ...]"""


class ReferenceRequest(BaseModel):
    """Body para POST /reference."""

    url: str = Field(..., min_length=4, description="URL del video de referencia")
    reel_target_s: float = Field(
        25.0, gt=0.5, le=120, description="Duración objetivo del reel (para suggested_beats)"
    )


@app.post("/reference", response_model=BaseResponse)
async def analyze_reference_endpoint(req: ReferenceRequest) -> BaseResponse:
    """Analiza un video de referencia → ReferenceBrief (ADR-017). Espejo del CLI.

    No genera reel: devuelve pacing, hook, estructura y transcript. Requiere el
    extra `reference` (yt-dlp) y ffmpeg en PATH.
    """
    t0 = time.perf_counter()
    try:
        brief = await analyze_reference(req.url, reel_target_s=req.reel_target_s)
    except ReferenceAnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception(f"analyze_reference falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return BaseResponse(
        data={"brief": brief.model_dump(mode="json"), "elapsed_s": time.perf_counter() - t0}
    )


@app.post("/scripts", response_model=BaseResponse)
async def generate_script(params: VideoParams, request: Request) -> BaseResponse:
    """Genera solo el script (sin TTS/video). Dispatch por entry point.

    - url     → extract_essence + compose_script (10 reasoners)
    - topic   → hunters + critic + narrators + judge (18 reasoners)
    - subject → 1 LLM call (legacy MPT)
    """
    t0 = time.perf_counter()
    try:
        if params.url:
            essence = await extract_essence(narrative_app, params.url)
            draft = await compose_script(narrative_app, essence)
            return BaseResponse(
                data={
                    "entry": "article",
                    "essence": essence.model_dump(mode="json"),
                    "script": draft.model_dump(mode="json"),
                    "elapsed_s": time.perf_counter() - t0,
                }
            )

        if params.topic:
            # 4 hunters → 12 candidates
            candidates_lists = await asyncio.gather(
                hunt_specific_figure(narrative_app, params.topic),
                hunt_reversal(narrative_app, params.topic),
                hunt_temporal(narrative_app, params.topic),
                hunt_cross_domain(narrative_app, params.topic),
            )
            candidates = [c for lst in candidates_lists for c in lst]
            # critic → top 3
            critic_output = await pick_top_essences(
                narrative_app, params.topic, candidates
            )
            top_essences = [candidates[i] for i in critic_output.top_3_indices]
            # narrators → 3 scripts
            scripts = await write_narrations(narrative_app, top_essences)
            # judge
            verdict = await pick_best_narration(
                narrative_app, params.topic, scripts, top_essences
            )
            winner_conv = scripts[verdict.winner_idx]
            adapted = _adapt_conversational_to_draft(winner_conv)
            return BaseResponse(
                data={
                    "entry": "topic",
                    "winner_idx": verdict.winner_idx,
                    "composite_score": verdict.composite_score,
                    "conversational": winner_conv.model_dump(mode="json"),
                    "script": adapted.model_dump(mode="json"),
                    "elapsed_s": time.perf_counter() - t0,
                }
            )

        if params.subject:
            cfg = request.app.state.config
            provider_name = cfg.llm_default_provider_express
            script_text = await complete(
                prompt=(
                    f"Write a {params.paragraph_number}-paragraph spoken script "
                    f"about: {params.subject}"
                ),
                provider=provider_name,
                system=_SUBJECT_SCRIPT_SYSTEM,
                temperature=0.7,
                max_tokens=1200,
            )
            return BaseResponse(
                data={
                    "entry": "subject",
                    "script": script_text.strip(),
                    "elapsed_s": time.perf_counter() - t0,
                }
            )

        # Validator de VideoParams ya garantiza al menos uno, pero por seguridad:
        raise HTTPException(
            status_code=400, detail="se requiere uno de: url, topic, subject"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"generate_script falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/terms", response_model=BaseResponse)
async def generate_terms(params: VideoParams, request: Request) -> BaseResponse:
    """Genera 5-8 search terms para stock footage (Pexels/Pixabay)."""
    cfg = request.app.state.config
    provider_name = cfg.llm_default_provider_express
    base = params.subject or params.topic or (params.url or "")
    if not base.strip():
        raise HTTPException(status_code=400, detail="no input para generar terms")

    seed = params.script or base
    try:
        raw = await complete(
            prompt=(
                f"Subject: {base}\n\nContext: {seed}\n\nReturn 5-8 short search terms."
            ),
            provider=provider_name,
            system=_SUBJECT_TERMS_SYSTEM,
            temperature=0.3,
            max_tokens=300,
        )
    except Exception as e:
        logger.exception(f"generate_terms falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        clean = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE)
        terms = json.loads(clean.strip())
    except Exception:
        terms = [base]
    if not isinstance(terms, list):
        terms = [base]
    terms = [str(x).strip() for x in terms if str(x).strip()][:8]
    return BaseResponse(data={"terms": terms})


@app.post("/audio", response_model=BaseResponse)
async def generate_audio(params: VideoParams, request: Request) -> BaseResponse:
    """Genera audio (TTS) desde `params.script`. Devuelve path + word_timings."""
    if not params.script:
        raise HTTPException(
            status_code=400, detail="params.script requerido para /audio"
        )

    # Output dir aislado por request (UUID corto). El caller puede luego
    # subir o limpiar.
    audio_id = str(uuid.uuid4())[:12]
    out_dir = Path("./output") / "audio" / audio_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        engine_name, voice = detect_engine_and_voice(params.voice_name)
        engine = get_engine(engine_name)
        artifact = await engine.synthesize(
            text=params.script,
            voice=voice,
            out_dir=out_dir,
            target_duration_s=None,
        )
    except Exception as e:
        logger.exception(f"generate_audio falló: {e}")
        raise HTTPException(status_code=500, detail=f"tts: {e}") from e

    return BaseResponse(
        data={
            "audio_path": str(artifact.path),
            "duration_s": artifact.duration_s,
            "engine": artifact.engine,
            "word_timings": [w.model_dump() for w in artifact.word_timings],
        }
    )


@app.post("/subtitle", response_model=BaseResponse)
async def generate_subtitle(params: VideoParams, request: Request) -> BaseResponse:
    """Genera subtítulos desde `params.script`.

    - Si `params.subtitle_style == WORD_BURST` y `params.custom_audio_file` está
      presente → derivamos timings via TTS sobre el script (best-effort).
    - SRT: derivado de los word_timings del TTS.
    """
    if not params.script:
        raise HTTPException(
            status_code=400, detail="params.script requerido para /subtitle"
        )

    sub_id = str(uuid.uuid4())[:12]
    out_dir = Path("./output") / "subtitle" / sub_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Re-synthesize para obtener word_timings; en producción se prefiere
        # llamar al worker con la pipeline completa.
        engine_name, voice = detect_engine_and_voice(params.voice_name)
        engine = get_engine(engine_name)
        artifact = await engine.synthesize(
            text=params.script,
            voice=voice,
            out_dir=out_dir,
            target_duration_s=None,
        )

        if params.subtitle_style == SubtitleStyle.SRT:
            srt_path = out_dir / "subs.srt"
            if artifact.word_timings:
                await asyncio.to_thread(
                    subtitles_from_word_timings, artifact.word_timings, srt_path
                )
            else:
                await asyncio.to_thread(
                    write_srt, params.script, artifact.path, srt_path
                )
            return BaseResponse(
                data={
                    "subtitle_path": str(srt_path),
                    "style": "srt",
                    "audio_path": str(artifact.path),
                }
            )

        # WORD_BURST: requiere `cards` + `accents` + `beats`. Sin pipeline
        # completo no tenemos beats/accents → devolvemos SRT como fallback y
        # avisamos en `note`.
        from core.planning import pack_cards

        cards = await asyncio.to_thread(pack_cards, artifact.word_timings)
        ass_path = out_dir / "subs.ass"
        # Sin beats/accents: pasamos listas vacías (el helper soporta este caso).
        await asyncio.to_thread(
            write_reel_ass_with_accents,
            cards,
            [],  # accents vacíos
            params.font_name,
            ass_path,
            [],  # beats vacíos
        )
        return BaseResponse(
            data={
                "subtitle_path": str(ass_path),
                "style": "word_burst",
                "audio_path": str(artifact.path),
                "cards": len(cards),
            }
        )
    except Exception as e:
        logger.exception(f"generate_subtitle falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# =============================================================================
# Premium-only (reasoners exposed)
# =============================================================================


@app.post("/narratives", response_model=BaseResponse)
async def get_narrative(params: VideoParams, request: Request) -> BaseResponse:
    """Devuelve solo el ScriptDraft delayed-reveal (sin generar video).

    - topic: hunters + critic + narrators + judge (sin downstream visual).
    - url:   extract_essence + compose_script.
    - subject: no soportado (no es delayed-reveal).
    """
    t0 = time.perf_counter()
    try:
        if params.url:
            essence = await extract_essence(narrative_app, params.url)
            draft = await compose_script(narrative_app, essence)
            return BaseResponse(
                data={
                    "entry": "article",
                    "essence": essence.model_dump(mode="json"),
                    "script_draft": draft.model_dump(mode="json"),
                    "elapsed_s": time.perf_counter() - t0,
                }
            )
        if params.topic:
            candidates_lists = await asyncio.gather(
                hunt_specific_figure(narrative_app, params.topic),
                hunt_reversal(narrative_app, params.topic),
                hunt_temporal(narrative_app, params.topic),
                hunt_cross_domain(narrative_app, params.topic),
            )
            candidates = [c for lst in candidates_lists for c in lst]
            critic_output = await pick_top_essences(
                narrative_app, params.topic, candidates
            )
            top_essences = [candidates[i] for i in critic_output.top_3_indices]
            scripts = await write_narrations(narrative_app, top_essences)
            verdict = await pick_best_narration(
                narrative_app, params.topic, scripts, top_essences
            )
            winner_conv = scripts[verdict.winner_idx]
            adapted: ScriptDraft = _adapt_conversational_to_draft(winner_conv)
            return BaseResponse(
                data={
                    "entry": "topic",
                    "winner_idx": verdict.winner_idx,
                    "composite_score": verdict.composite_score,
                    "conversational": winner_conv.model_dump(mode="json"),
                    "script_draft": adapted.model_dump(mode="json"),
                    "all_scripts": [s.model_dump(mode="json") for s in scripts],
                    "elapsed_s": time.perf_counter() - t0,
                }
            )
        raise HTTPException(
            status_code=400,
            detail="/narratives requiere url o topic (no soporta subject)",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"get_narrative falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/hunters", response_model=BaseResponse)
async def run_hunters(params: VideoParams, request: Request) -> BaseResponse:
    """Devuelve los 12 candidates de los 4 hunters (A/B testing externo).

    Solo válido si `params.topic` está presente.
    """
    if not params.topic:
        raise HTTPException(
            status_code=400, detail="/hunters requiere params.topic"
        )
    t0 = time.perf_counter()
    try:
        candidates_lists = await asyncio.gather(
            hunt_specific_figure(narrative_app, params.topic),
            hunt_reversal(narrative_app, params.topic),
            hunt_temporal(narrative_app, params.topic),
            hunt_cross_domain(narrative_app, params.topic),
        )
    except Exception as e:
        logger.exception(f"run_hunters falló: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    hunters_labels = ["specific_figure", "reversal", "temporal", "cross_domain"]
    flat: list[dict[str, Any]] = []
    for label, lst in zip(hunters_labels, candidates_lists, strict=True):
        for c in lst:
            flat.append({"hunter": label, **c.model_dump(mode="json")})
    return BaseResponse(
        data={
            "topic": params.topic,
            "total_candidates": len(flat),
            "candidates": flat,
            "by_hunter": {
                label: [c.model_dump(mode="json") for c in lst]
                for label, lst in zip(hunters_labels, candidates_lists, strict=True)
            },
            "elapsed_s": time.perf_counter() - t0,
        }
    )


# Keep a reference so static analysis doesn't flag unused imports.
_ = _build_synthetic_essence
