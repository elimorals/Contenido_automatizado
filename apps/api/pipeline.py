"""Pipeline orchestrator — ejecuta los 3 entry points end-to-end.

  - run_article_pipeline(params) → TaskInfo (article path, 10 reasoners)
  - run_topic_pipeline(params)   → TaskInfo (topic path, 18 reasoners)
  - run_subject_pipeline(params) → TaskInfo (legacy MPT path, 1 LLM call)
  - run_pipeline(params)         → TaskInfo (dispatch automático según entry)

Convención: cada fase mide su `time.perf_counter()` y persiste el delta en
`TaskInfo.timings_s[<phase>]`. El progress (0-100) se actualiza tras cada fase
mayor — útil para el SSE polling de la UI.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from loguru import logger

from core.distribution import get_distribution_service
from core.editor import stitch_video
from core.editorial import assess_slideshow_risk
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
    plan_beat_accents,
    plan_beat_visuals,
    write_narrations,
)
from core.planning import pack_cards, plan_beats
from core.reference import ReferenceAnalysisError, analyze_reference
from core.subtitles import (
    subtitles_from_word_timings,
    write_reel_ass_with_accents,
    write_srt,
)
from core.tts import detect_engine_and_voice, get_engine
from core.visual import generate_all_beat_artifacts
from shared.config import load_config
from shared.schemas import (
    ConversationalScript,
    Essence,
    GenerationMode,
    HookVariant,
    ReferenceBrief,
    ScriptDraft,
    SubtitleStyle,
    TaskInfo,
    TaskState,
    VideoParams,
    VideoSource,
    VisualStrategy,
)

# =============================================================================
# Helpers
# =============================================================================


def _log_prefix(task_id: str) -> str:
    return f"[task:{task_id[:8]}]"


def _split_sentences(text: str, max_sentences: int = 4, min_sentences: int = 2) -> list[str]:
    """Split a paragraph into 2-4 sentences. Robust to missing/extra punctuation."""
    import re

    raw = re.split(r"(?<=[.!?])\s+", text.strip()) if text else []
    sentences = [s.strip() for s in raw if s and s.strip()]
    if not sentences:
        return [text.strip() or "..."]
    if len(sentences) >= min_sentences:
        return sentences[:max_sentences]
    # Si solo hay 1 sentence intentar split por comas
    if len(sentences) < min_sentences:
        chunks = [c.strip() for c in re.split(r",\s+", sentences[0]) if c.strip()]
        if len(chunks) >= min_sentences:
            return chunks[:max_sentences]
    # Fallback: replicar para cumplir min_length
    while len(sentences) < min_sentences:
        sentences.append(sentences[-1])
    return sentences[:max_sentences]


def _adapt_conversational_to_draft(conv: ConversationalScript) -> ScriptDraft:
    """Adapt ConversationalScript (topic path) → ScriptDraft (downstream).

    Mapping:
      - hook         ← conv.tease
      - mechanism    ← split(conv.reveal) en 2-4 sentences
      - payoff_line  ← conv.payoff
      - narration    ← conv.narration
    """
    mechanism_lines = _split_sentences(conv.reveal)
    # Loop-back: ScriptDraft validador exige que el keyword más largo del hook
    # aparezca en el payoff_line. Si la cadena ya lo cumple, ok. Si no, fall back
    # appendeando un tease keyword.
    try:
        return ScriptDraft(
            hook=conv.tease.strip(),
            hook_variant=HookVariant.CURIOSITY_GAP,
            mechanism_lines=mechanism_lines,
            payoff_line=conv.payoff.strip(),
            target_wpm=conv.target_wpm,
            narration=conv.narration.strip(),
        )
    except ValueError:
        # Loop-back validator falló → forzar keyword al payoff
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "to", "of",
            "in", "on", "at", "for", "with", "by", "as", "and", "or", "but",
            "this", "that", "you", "they", "we", "i", "why", "what", "how",
        }
        keywords = [
            w.lower().strip(".,!?;:\"'")
            for w in conv.tease.split()
            if w.lower().strip(".,!?;:\"'") not in stopwords
            and len(w.strip(".,!?;:\"'")) > 2
        ]
        if not keywords:
            keywords = ["story"]
        anchor = max(keywords, key=len)
        patched_payoff = f"{conv.payoff.rstrip('.!?')} — {anchor}."
        patched_narration = conv.narration.rstrip() + f" {anchor}."
        return ScriptDraft(
            hook=conv.tease.strip(),
            hook_variant=HookVariant.CURIOSITY_GAP,
            mechanism_lines=mechanism_lines,
            payoff_line=patched_payoff,
            target_wpm=conv.target_wpm,
            narration=patched_narration,
        )


def _build_synthetic_essence(
    *, topic: str = "", subject: str = "", url: str = "", script_text: str = ""
) -> Essence:
    """Build a minimal Essence for paths sin extracción real (subject/topic-fallback)."""
    core_claim = (script_text or topic or subject or url)[:200] or "Untitled"
    domain = (topic.split()[0] if topic else (subject.split()[0] if subject else "general"))[:30]
    evidence = [core_claim[:80]] if core_claim else ["context"]
    return Essence(
        core_claim=core_claim,
        mechanism="(auto-generated essence for non-narrative path)",
        evidence=evidence,
        content_mode="general",
        domain=domain or "general",
    )


async def _update_state(
    state_manager: Any,
    task_id: str,
    *,
    progress: int | None = None,
    **fields: Any,
) -> TaskInfo:
    """Update TaskInfo en state_manager. Defensive: si la task no existe, crea."""
    try:
        info = await state_manager.get(task_id)
    except Exception:
        info = None
    if info is None:
        # No task — no-op; algunos backends necesitan que exista antes.
        logger.warning(f"{_log_prefix(task_id)} state.update sobre task inexistente")
        return TaskInfo(
            task_id=task_id,
            state=TaskState.PROCESSING,
            mode=fields.get("mode"),
            progress=progress or 0,
        )

    update_fields: dict[str, Any] = dict(fields)
    if progress is not None:
        update_fields["progress"] = progress

    if not update_fields:
        return info

    try:
        return await state_manager.update(task_id, **update_fields)
    except Exception as e:
        logger.warning(f"{_log_prefix(task_id)} state.update falló: {e}")
        # Fall back a set crudo si update rompió.
        for k, v in update_fields.items():
            setattr(info, k, v)
        await state_manager.set(task_id, info)
        return info


async def _compute_reference_brief(
    params: VideoParams,
    task_id: str,
    state_manager: Any,
    timings: dict[str, float],
) -> ReferenceBrief | None:
    """Analiza `params.reference_url` (no-fatal). Stampa el brief + registra timing.

    Devuelve None si no hay url o si el análisis falla. Se llama UNA vez por run:
    los entry pipelines (topic/article) lo invocan antes de componer para inyectar
    el brief al prompt; el subject path lo deja a `_run_shared_downstream`.
    """
    if not params.reference_url:
        return None
    prefix = _log_prefix(task_id)
    logger.info(f"{prefix} reference: analizando {params.reference_url}")
    t = time.perf_counter()
    brief = None
    try:
        brief = await analyze_reference(params.reference_url)
        logger.info(
            f"{prefix} [reference] hook={brief.hook_style} wpm={brief.wpm} "
            f"avg_shot={brief.avg_shot_s:.1f}s → suggested_beats={brief.suggested_beats} "
            f"target_wpm={brief.target_wpm}"
        )
        await _update_state(state_manager, task_id, reference_brief=brief)
    except ReferenceAnalysisError as e:
        logger.warning(f"{prefix} [reference] análisis falló (non-fatal): {e}")
    timings["reference"] = time.perf_counter() - t
    return brief


# =============================================================================
# SHARED DOWNSTREAM (article + topic paths convergen aquí)
# =============================================================================


async def _run_shared_downstream(
    script_draft: ScriptDraft,
    essence: Essence,
    params: VideoParams,
    task_id: str,
    state_manager: Any,
    initial_timings: dict[str, float] | None = None,
    reference_brief: ReferenceBrief | None = None,
) -> TaskInfo:
    """Fases compartidas: TTS → plan → visuals/accents → media → subs → stitch → dist.

    Asume que el script_draft + essence ya fueron generados upstream y se anotó
    `info.script` / `info.essence` por el caller.

    `reference_brief` opcional: si el caller ya lo computó (topic/article, para
    inyectarlo al prompt), se reutiliza aquí (sin re-fetch). Si es None y hay
    `params.reference_url` (p.ej. subject path), se computa aquí.
    """
    prefix = _log_prefix(task_id)
    out_dir = Path("./output") / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = dict(initial_timings or {})
    cost_breakdown: dict[str, float] = {}

    # ─── Phase 0 (opcional): análisis de referencia (input-by-reference, ADR-017) ─
    # Si el caller (topic/article) ya lo computó para inyectarlo al prompt, se
    # reutiliza. Si no (subject path), se computa aquí. No-fatal en ambos casos.
    if reference_brief is None:
        reference_brief = await _compute_reference_brief(
            params, task_id, state_manager, timings
        )

    # ─── Phase A: Audio (TTS + sample-accurate timing) ───────────────────────
    logger.info(f"{prefix} Phase A: TTS synth")
    t = time.perf_counter()
    try:
        engine_name, voice = detect_engine_and_voice(params.voice_name)
        engine = get_engine(engine_name)
        audio_artifact = await engine.synthesize(
            text=script_draft.narration,
            voice=voice,
            out_dir=out_dir,
            target_duration_s=None,
        )
    except Exception as e:
        logger.error(f"{prefix} TTS falló: {e}")
        timings["tts"] = time.perf_counter() - t
        timings["error"] = f"tts: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["tts"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=40,
        audio_path=str(audio_artifact.path),
        audio_duration_s=audio_artifact.duration_s,
        timings_s=dict(timings),
    )

    # ─── Phase B (paralelo): Cards + Beats ───────────────────────────────────
    logger.info(f"{prefix} Phase B: pack cards + plan beats")
    t = time.perf_counter()
    try:
        cards, beats = await asyncio.gather(
            asyncio.to_thread(pack_cards, audio_artifact.word_timings),
            asyncio.to_thread(plan_beats, script_draft, audio_artifact.duration_s),
        )
    except Exception as e:
        logger.error(f"{prefix} plan falló: {e}")
        timings["plan"] = time.perf_counter() - t
        timings["error"] = f"plan: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["plan"] = time.perf_counter() - t

    # ─── Phase C (paralelo): Visuals + Accents ───────────────────────────────
    logger.info(f"{prefix} Phase C: plan visuals + accents")
    t = time.perf_counter()
    try:
        visuals, accents = await asyncio.gather(
            plan_beat_visuals(narrative_app, beats, essence, script_draft.narration),
            plan_beat_accents(narrative_app, beats, essence),
        )
    except Exception as e:
        logger.error(f"{prefix} visual/accent plan falló: {e}")
        timings["visual_accent"] = time.perf_counter() - t
        timings["error"] = f"visual_accent: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["visual_accent"] = time.perf_counter() - t

    await _update_state(state_manager, task_id, progress=60, timings_s=dict(timings))

    # ─── Phase D: Media (per-beat con selector híbrido) ──────────────────────
    logger.info(f"{prefix} Phase D: generate beat artifacts")
    t = time.perf_counter()
    try:
        artifacts = await generate_all_beat_artifacts(
            beats=beats,
            visuals=visuals,
            essence=essence,
            mode=params.mode,
            strategy=params.visual_strategy,
            aspect=params.aspect.value,
            out_dir=out_dir,
            cost_tracker=cost_breakdown,
        )
    except Exception as e:
        logger.error(f"{prefix} media gen falló: {e}")
        timings["media"] = time.perf_counter() - t
        timings["error"] = f"media: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["media"] = time.perf_counter() - t

    # ─── Quality gate (advisory): anti-slideshow guard ───────────────────────
    # No-fatal: el pipeline NUNCA crashea por assets (cae a ken-burns/placeholder),
    # así que aquí sólo medimos si el reel prometió movimiento pero quedó como
    # slideshow, y lo dejamos como señal observable (logs + quality_flags). Ver ADR-019.
    promised_motion = (
        params.mode == GenerationMode.PREMIUM
        or params.use_veo
        or params.visual_strategy in (VisualStrategy.IA, VisualStrategy.HYBRID)
    )
    slideshow = assess_slideshow_risk(artifacts, promised_motion=promised_motion)
    for issue in slideshow.result.errors:
        logger.warning(f"{prefix} [slideshow-guard] {issue}")
    for issue in slideshow.result.warnings:
        logger.info(f"{prefix} [slideshow-guard] {issue}")
    logger.info(
        f"{prefix} [slideshow-guard] risk={slideshow.risk_score:.2f} "
        f"static={slideshow.static_beats}/{slideshow.n_beats} "
        f"sources={slideshow.distinct_sources}"
    )

    await _update_state(
        state_manager,
        task_id,
        progress=75,
        materials=[str(a.video_path) for a in artifacts if a.video_path],
        timings_s=dict(timings),
        cost_breakdown=dict(cost_breakdown),
        quality_flags={
            "slideshow_risk": round(slideshow.risk_score, 3),
            "static_ratio": round(slideshow.static_ratio, 3),
            "is_slideshow": float(slideshow.is_slideshow),
        },
    )

    # ─── Phase E: Subtitles ──────────────────────────────────────────────────
    logger.info(f"{prefix} Phase E: subtitles ({params.subtitle_style.value})")
    t = time.perf_counter()
    try:
        if params.subtitle_style == SubtitleStyle.WORD_BURST:
            ass_path = out_dir / "subs.ass"
            await asyncio.to_thread(
                write_reel_ass_with_accents,
                cards,
                accents,
                params.font_name,
                ass_path,
                beats,
            )
            subtitle_path: Path = ass_path
        else:  # SRT
            srt_path = out_dir / "subs.srt"
            await asyncio.to_thread(
                subtitles_from_word_timings,
                audio_artifact.word_timings,
                srt_path,
            )
            subtitle_path = srt_path
    except Exception as e:
        logger.error(f"{prefix} subtitles falló: {e}")
        timings["subtitles"] = time.perf_counter() - t
        timings["error"] = f"subtitles: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["subtitles"] = time.perf_counter() - t

    # ─── Phase F: Stitch (single-pass ffmpeg) ────────────────────────────────
    logger.info(f"{prefix} Phase F: stitch video")
    t = time.perf_counter()
    try:
        from core.editor.bgm import pick_bgm

        bgm_path = pick_bgm(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        final_path = await stitch_video(
            artifacts=artifacts,
            audio_path=audio_artifact.path,
            subtitle_ass_path=subtitle_path
            if params.subtitle_style == SubtitleStyle.WORD_BURST
            else None,
            out_dir=out_dir,
            aspect=params.aspect,
            bgm_path=bgm_path,
            bgm_volume=params.bgm_volume,
            voice_volume=params.voice_volume,
        )
    except Exception as e:
        logger.error(f"{prefix} stitch falló: {e}")
        timings["stitch"] = time.perf_counter() - t
        timings["error"] = f"stitch: {e}"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["stitch"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=90,
        subtitle_path=str(subtitle_path),
        videos=[str(final_path)],
        timings_s=dict(timings),
    )

    # ─── Phase G: Distribution (opcional) ────────────────────────────────────
    cross_post_results: list[dict] = []
    if params.auto_upload:
        logger.info(f"{prefix} Phase G: distribution upload")
        t = time.perf_counter()
        try:
            dist = get_distribution_service(load_config())
            if dist is not None:
                platforms = params.upload_platforms or ["tiktok"]
                result = await dist.upload_video(
                    video_path=final_path,
                    platforms=platforms,
                    caption=script_draft.hook,
                )
                cross_post_results.append(result.model_dump())
            else:
                logger.info(f"{prefix} distribution: servicio no configurado, skip")
        except Exception as e:
            logger.warning(f"{prefix} distribution falló (non-fatal): {e}")
        timings["distribution"] = time.perf_counter() - t

    # ─── Final state ────────────────────────────────────────────────────────
    timings["total"] = sum(v for k, v in timings.items() if k != "total" and isinstance(v, (int, float)))

    info = await _update_state(
        state_manager,
        task_id,
        progress=100,
        script=script_draft.narration,
        essence=essence,
        audio_path=str(audio_artifact.path),
        audio_duration_s=audio_artifact.duration_s,
        subtitle_path=str(subtitle_path),
        videos=[str(final_path)],
        timings_s=dict(timings),
        cost_breakdown=dict(cost_breakdown),
        cross_post_results=cross_post_results,
    )
    return info


# =============================================================================
# ARTICLE PIPELINE (10 reasoners)
# =============================================================================


async def run_article_pipeline(
    params: VideoParams,
    task_id: str,
    state_manager: Any,
) -> TaskInfo:
    """Article path: URL → Essence → ScriptDraft → shared downstream."""
    prefix = _log_prefix(task_id)
    logger.info(f"{prefix} ARTICLE pipeline starting (url={params.url})")
    timings: dict[str, float] = {}

    # ─── Phase 1: extract essence ───────────────────────────────────────────
    t = time.perf_counter()
    try:
        essence = await extract_essence(narrative_app, params.url)
    except Exception as e:
        logger.error(f"{prefix} extract_essence falló: {e}")
        timings["extract_essence"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["extract_essence"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=15,
        essence=essence,
        timings_s=dict(timings),
    )

    # ─── Phase 1.5 (opcional): referencia → brief (para inyectar al prompt) ───
    reference_brief = await _compute_reference_brief(params, task_id, state_manager, timings)

    # ─── Phase 2: compose script ────────────────────────────────────────────
    t = time.perf_counter()
    try:
        script_draft = await compose_script(narrative_app, essence, reference_brief)
    except Exception as e:
        logger.error(f"{prefix} compose_script falló: {e}")
        timings["compose_script"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["compose_script"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=25,
        script=script_draft.narration,
        timings_s=dict(timings),
    )

    # ─── Phases 3-9: shared downstream ──────────────────────────────────────
    return await _run_shared_downstream(
        script_draft=script_draft,
        essence=essence,
        params=params,
        task_id=task_id,
        state_manager=state_manager,
        initial_timings=timings,
        reference_brief=reference_brief,
    )


# =============================================================================
# TOPIC PIPELINE (18 reasoners)
# =============================================================================


async def run_topic_pipeline(
    params: VideoParams,
    task_id: str,
    state_manager: Any,
) -> TaskInfo:
    """Topic path: 4 hunters → critic → 3 narrators → judge → adapt → shared downstream."""
    prefix = _log_prefix(task_id)
    logger.info(f"{prefix} TOPIC pipeline starting (topic={params.topic})")
    timings: dict[str, float] = {}

    # ─── Phase 1 (paralelo): 4 hunters → 12 candidates ──────────────────────
    t = time.perf_counter()
    try:
        candidates_lists = await asyncio.gather(
            hunt_specific_figure(narrative_app, params.topic),
            hunt_reversal(narrative_app, params.topic),
            hunt_temporal(narrative_app, params.topic),
            hunt_cross_domain(narrative_app, params.topic),
        )
        candidates = [c for lst in candidates_lists for c in lst]
    except Exception as e:
        logger.error(f"{prefix} hunters fallaron: {e}")
        timings["hunters"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["hunters"] = time.perf_counter() - t
    logger.info(f"{prefix} hunters → {len(candidates)} candidates")

    await _update_state(state_manager, task_id, progress=15, timings_s=dict(timings))

    # ─── Phase 2: critic (top 3) ────────────────────────────────────────────
    t = time.perf_counter()
    try:
        critic_output = await pick_top_essences(narrative_app, params.topic, candidates)
        top_essences = [candidates[i] for i in critic_output.top_3_indices]
    except Exception as e:
        logger.error(f"{prefix} critic falló: {e}")
        timings["critic"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["critic"] = time.perf_counter() - t

    # ─── Phase 2.5 (opcional): referencia → brief (para inyectar al prompt) ───
    reference_brief = await _compute_reference_brief(params, task_id, state_manager, timings)

    # ─── Phase 3 (paralelo): 3 narrators ────────────────────────────────────
    t = time.perf_counter()
    try:
        scripts = await write_narrations(narrative_app, top_essences, reference_brief)
    except Exception as e:
        logger.error(f"{prefix} narrators fallaron: {e}")
        timings["narrators"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["narrators"] = time.perf_counter() - t

    await _update_state(state_manager, task_id, progress=22, timings_s=dict(timings))

    # ─── Phase 4: judge ─────────────────────────────────────────────────────
    t = time.perf_counter()
    try:
        verdict = await pick_best_narration(
            narrative_app, params.topic, scripts, top_essences
        )
    except Exception as e:
        logger.error(f"{prefix} judge falló: {e}")
        timings["judge"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["judge"] = time.perf_counter() - t

    winner_script = scripts[verdict.winner_idx]
    winner_essence_candidate = top_essences[verdict.winner_idx]
    logger.info(
        f"{prefix} judge winner={verdict.winner_idx} score={verdict.composite_score}"
    )

    # ─── Phase 5: adapt ConversationalScript → ScriptDraft ──────────────────
    script_draft = _adapt_conversational_to_draft(winner_script)
    # Promote candidate (EssenceCandidate hereda de Essence) → Essence pura para downstream.
    essence = Essence(
        core_claim=winner_essence_candidate.core_claim,
        mechanism=winner_essence_candidate.mechanism,
        evidence=winner_essence_candidate.evidence,
        content_mode=winner_essence_candidate.content_mode,
        domain=winner_essence_candidate.domain,
    )

    await _update_state(
        state_manager,
        task_id,
        progress=28,
        chosen_candidate=winner_essence_candidate,
        winner_composite=verdict.composite_score,
        essence=essence,
        script=script_draft.narration,
        timings_s=dict(timings),
    )

    # ─── Phases 6-12: shared downstream ─────────────────────────────────────
    return await _run_shared_downstream(
        script_draft=script_draft,
        essence=essence,
        params=params,
        task_id=task_id,
        state_manager=state_manager,
        initial_timings=timings,
        reference_brief=reference_brief,
    )


# =============================================================================
# SUBJECT PIPELINE (legacy MPT, 1 LLM call)
# =============================================================================


_SUBJECT_SCRIPT_SYSTEM = """You are a viral short-form video script writer.
Write a tight, engaging spoken narration. Plain language. No "did you know",
no "hey guys", no CTAs. Every sentence is a beat downstream. No padding."""


_SUBJECT_TERMS_SYSTEM = """You return ONLY a JSON list of 5-8 short search
terms (1-3 words each) suitable for stock footage providers (Pexels/Pixabay).
Format strictly: ["term one", "term two", ...]"""


async def run_subject_pipeline(
    params: VideoParams,
    task_id: str,
    state_manager: Any,
) -> TaskInfo:
    """Legacy MPT path: subject → 1 LLM script + terms + stock + stitch.

    Sin reasoners narrativos; sin Essence real. Construye una Essence sintética
    para mantener compatibilidad con `_run_shared_downstream` cuando el caller
    quiera reusar la vía visual, pero por default usa un branch más simple
    (stock-only) acorde al modo EXPRESS.
    """
    prefix = _log_prefix(task_id)
    logger.info(f"{prefix} SUBJECT pipeline starting (subject={params.subject})")
    timings: dict[str, float] = {}

    # ─── Phase 1: script (legacy MPT) ───────────────────────────────────────
    cfg = load_config()
    provider_name = cfg.llm_default_provider_express

    t = time.perf_counter()
    try:
        if params.script:
            script_text = params.script.strip()
        else:
            script_text = await complete(
                prompt=(
                    f"Write a {params.paragraph_number}-paragraph spoken script about: "
                    f"{params.subject}"
                ),
                provider=provider_name,
                system=_SUBJECT_SCRIPT_SYSTEM,
                temperature=0.7,
                max_tokens=1200,
            )
    except Exception as e:
        logger.error(f"{prefix} subject script gen falló: {e}")
        timings["script"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["script"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=20,
        script=script_text,
        timings_s=dict(timings),
    )

    # ─── Phase 2: terms (stock search) ──────────────────────────────────────
    t = time.perf_counter()
    try:
        if params.terms:
            terms = list(params.terms)
        else:
            terms_raw = await complete(
                prompt=f"Subject: {params.subject}\n\nScript: {script_text}\n\nReturn 5-8 short search terms.",
                provider=provider_name,
                system=_SUBJECT_TERMS_SYSTEM,
                temperature=0.3,
                max_tokens=300,
            )
            # Parse best-effort.
            import json
            import re

            try:
                terms = json.loads(terms_raw.strip())
            except Exception:
                # Strip code fences si los hay
                clean = re.sub(r"^```(?:json)?|```$", "", terms_raw.strip(), flags=re.MULTILINE)
                terms = json.loads(clean.strip())
            if not isinstance(terms, list):
                raise ValueError(f"terms no es lista: {type(terms)}")
            terms = [str(t).strip() for t in terms if str(t).strip()][:8]
    except Exception as e:
        logger.warning(f"{prefix} terms gen falló ({e}); fallback al subject")
        terms = [params.subject or "abstract"]
    timings["terms"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=30,
        terms=terms,
        timings_s=dict(timings),
    )

    # ─── Phase 3: TTS ───────────────────────────────────────────────────────
    out_dir = Path("./output") / task_id
    out_dir.mkdir(parents=True, exist_ok=True)

    t = time.perf_counter()
    try:
        engine_name, voice = detect_engine_and_voice(params.voice_name)
        engine = get_engine(engine_name)
        audio_artifact = await engine.synthesize(
            text=script_text,
            voice=voice,
            out_dir=out_dir,
            target_duration_s=None,
        )
    except Exception as e:
        logger.error(f"{prefix} subject TTS falló: {e}")
        timings["tts"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["tts"] = time.perf_counter() - t

    await _update_state(
        state_manager,
        task_id,
        progress=55,
        audio_path=str(audio_artifact.path),
        audio_duration_s=audio_artifact.duration_s,
        timings_s=dict(timings),
    )

    # ─── Phase 4: stock material (no IA en este modo) ───────────────────────
    from core.visual.stock import fetch_and_cache, get_all_providers

    t = time.perf_counter()
    materials: list[Path] = []
    providers = get_all_providers()
    try:
        if providers:
            for term in terms[: max(1, params.video_count)]:
                for provider in providers:
                    try:
                        results = await provider.search(
                            query=term,
                            aspect=params.aspect,
                            min_duration_s=float(params.video_clip_duration),
                        )
                    except Exception as e:
                        logger.warning(f"{prefix} stock {provider.name} {term}: {e}")
                        continue
                    if not results:
                        continue
                    cached = await fetch_and_cache(results[0].url, provider=provider)
                    if cached is not None:
                        materials.append(cached)
                        break
                if len(materials) >= params.video_count:
                    break
        else:
            logger.info(f"{prefix} no hay stock providers; usando placeholders")
    except Exception as e:
        logger.warning(f"{prefix} stock fetch falló (non-fatal): {e}")
    timings["materials"] = time.perf_counter() - t

    # Construir BeatArtifacts mínimos a partir del material descargado.
    from shared.schemas import BeatArtifact

    if materials:
        artifacts = [
            BeatArtifact(
                idx=i,
                video_path=p,
                source=VideoSource.PEXELS,
                duration_s=float(params.video_clip_duration),
            )
            for i, p in enumerate(materials)
        ]
    else:
        # Placeholder absoluto para no crashear el stitch downstream.
        artifacts = []

    await _update_state(
        state_manager,
        task_id,
        progress=70,
        materials=[str(p) for p in materials],
        timings_s=dict(timings),
    )

    # ─── Phase 5: SRT subtitles (estilo legacy) ─────────────────────────────
    t = time.perf_counter()
    try:
        srt_path = out_dir / "subs.srt"
        if audio_artifact.word_timings:
            await asyncio.to_thread(
                subtitles_from_word_timings, audio_artifact.word_timings, srt_path
            )
        else:
            # Fallback: re-derivar con Whisper desde audio.
            await asyncio.to_thread(write_srt, script_text, audio_artifact.path, srt_path)
        subtitle_path = srt_path
    except Exception as e:
        logger.warning(f"{prefix} subject subtitles falló (non-fatal): {e}")
        subtitle_path = None  # type: ignore[assignment]
    timings["subtitles"] = time.perf_counter() - t

    # ─── Phase 6: Stitch ────────────────────────────────────────────────────
    if not artifacts:
        # No hay material → no se puede stitch. Marcamos error.
        timings["error"] = "no_materials"
        await _update_state(state_manager, task_id, timings_s=timings)
        raise RuntimeError(
            "subject_pipeline: no se obtuvo material de stock y no hay placeholders"
        )

    t = time.perf_counter()
    try:
        from core.editor.bgm import pick_bgm

        bgm_path = pick_bgm(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
        final_path = await stitch_video(
            artifacts=artifacts,
            audio_path=audio_artifact.path,
            subtitle_ass_path=None,  # SRT no se quema en subtitles= filter; legacy MPT lo deja afuera
            out_dir=out_dir,
            aspect=params.aspect,
            bgm_path=bgm_path,
            bgm_volume=params.bgm_volume,
            voice_volume=params.voice_volume,
        )
    except Exception as e:
        logger.error(f"{prefix} subject stitch falló: {e}")
        timings["stitch"] = time.perf_counter() - t
        await _update_state(state_manager, task_id, timings_s=timings)
        raise
    timings["stitch"] = time.perf_counter() - t

    timings["total"] = sum(
        v for k, v in timings.items() if k != "total" and isinstance(v, (int, float))
    )

    info = await _update_state(
        state_manager,
        task_id,
        progress=100,
        videos=[str(final_path)],
        subtitle_path=str(subtitle_path) if subtitle_path else None,
        timings_s=dict(timings),
    )
    return info


# =============================================================================
# DISPATCH PRINCIPAL
# =============================================================================


async def run_pipeline(
    params: VideoParams,
    task_id: str,
    state_manager: Any,
) -> TaskInfo:
    """Dispatch principal — decide qué pipeline correr según entry point."""
    prefix = _log_prefix(task_id)

    # Inicializar TaskInfo en state.
    info = TaskInfo(
        task_id=task_id,
        state=TaskState.PROCESSING,
        mode=params.mode,
        params=params,
        progress=0,
    )
    await state_manager.set(task_id, info)

    try:
        if params.url:
            logger.info(f"{prefix} dispatch → article pipeline")
            info = await run_article_pipeline(params, task_id, state_manager)
        elif params.topic:
            logger.info(f"{prefix} dispatch → topic pipeline")
            info = await run_topic_pipeline(params, task_id, state_manager)
        elif params.subject:
            logger.info(f"{prefix} dispatch → subject pipeline")
            info = await run_subject_pipeline(params, task_id, state_manager)
        else:
            raise ValueError(
                "VideoParams sin entry point: se requiere uno de url|topic|subject"
            )

        # Marcar COMPLETE solo si llegamos aquí sin excepciones.
        info = await _update_state(
            state_manager, task_id, state=TaskState.COMPLETE, progress=100
        )
        logger.success(f"{prefix} pipeline COMPLETE")
        return info

    except Exception as e:
        logger.error(f"{prefix} pipeline FAILED: {e}")
        try:
            current = await state_manager.get(task_id)
            current_timings = dict(current.timings_s) if current else {}
        except Exception:
            current_timings = {}
        current_timings["error"] = str(e)
        await _update_state(
            state_manager,
            task_id,
            state=TaskState.FAILED,
            timings_s=current_timings,
        )
        raise


__all__ = [
    "run_article_pipeline",
    "run_pipeline",
    "run_subject_pipeline",
    "run_topic_pipeline",
]
