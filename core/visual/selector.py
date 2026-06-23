"""Visual selector híbrido: decide stock vs IA por beat con two-tier fallback.

Es el "cerebro" que orquesta los módulos ya portados:
- `core.visual.stock` (Pexels/Pixabay/Coverr)
- `core.visual.generation` (Gemini Image + Veo + ken-burns)

Decisión por beat:
    1. Si `strategy` tiene override explícito → respeta.
    2. EXPRESS → favorece stock (rápido + barato); excepción: hook + veo_enabled → IA.
    3. PREMIUM → más selectivo:
       - HOOK → IA (stop-the-scroll)
       - PAYOFF → stock (callback visual genérico)
       - MECHANISM con evidence específica en `beat.text` → IA, sino stock.
    4. HYBRID → tratado como PREMIUM.

Two-tier fallback (CRÍTICO):
    - Si stock falla → IA.
    - Si IA falla → stock.
    - Si ambos fallan → placeholder (NUNCA raise).

Cost tracking opcional vía dict mutable: `cost_tracker[f"beat_{idx}_{source}"]`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.visual.broll_embedder import get_broll_embedder
from core.visual.broll_rerank import rerank
from core.visual.broll_visual import (
    VisualEmbedder,
    extract_frames,
    get_visual_reranker,
    rerank_visual,
)
from core.visual.generation import generate_beat_videos
from core.visual.generation.ken_burns import _placeholder_frame, render_ken_burns
from core.visual.provider_scoring import DecisionLog, decide_provider
from core.visual.stock import fetch_and_cache, get_all_providers
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Essence,
    GenerationMode,
    MaterialInfo,
    VideoAspect,
    VideoSource,
    VisualStrategy,
)

# Costos aproximados (USD) por fuente — ajustar contra precios reales.
# Higgsfield DoP: $0.13/run base (lite); turbo ~$0.20; preview ~$0.30
# Soul: ~$0.04 por imagen generada (similar a Gemini Flash Image)
# Effects: ~$0.10 por overlay
_COST_ESTIMATES: dict[VideoSource, float] = {
    VideoSource.PEXELS: 0.0,
    VideoSource.PIXABAY: 0.0,
    VideoSource.COVERR: 0.0,
    VideoSource.GEMINI_IMAGE: 0.04,  # ~1 imagen + ken-burns
    VideoSource.VEO: 0.30,  # Veo i2v 4-8s
    VideoSource.LOCAL: 0.0,
    VideoSource.HIGGSFIELD_DOP: 0.20,  # DoP turbo (default)
    VideoSource.HIGGSFIELD_SOUL: 0.05,  # SoulId-guided image
    VideoSource.HIGGSFIELD_EFFECT: 0.10,  # Effect overlay post-step
    VideoSource.COMFYUI: 0.0,  # self-hosted = compute propio; managed override en config
    # Corpus libres (ADR-022): gratis (dominio público / CC / licencia Unsplash).
    VideoSource.ARCHIVE_ORG: 0.0,
    VideoSource.WIKIMEDIA: 0.0,
    VideoSource.NASA: 0.0,
    VideoSource.UNSPLASH: 0.0,
    # Corpus libre histórico (ADR-026): dominio público → gratis.
    VideoSource.LOC: 0.0,
    VideoSource.NARA: 0.0,
    VideoSource.POND5_PD: 0.0,
    VideoSource.FAL: 0.25,  # i2v Kling/Runway/MiniMax (~$0.2-0.5/clip según modelo)
}


# =============================================================================
# DECISIÓN: stock vs IA
# =============================================================================


def _ia_source(cfg: object | None = None) -> VideoSource:
    """Sentinel para 'rama IA': elige entre GEMINI_IMAGE y HIGGSFIELD_SOUL.

    Solo retorna HIGGSFIELD_SOUL si TODAS estas son true:
    - soul_enabled=True
    - credenciales presentes (combined o split, ambas strings no vacías)
    - soul_default_reference_id no vacío

    Si solo algunos beats tienen `visual.soul_id` set (sin default global),
    el sentinel sigue siendo GEMINI_IMAGE y el orchestrator decide por beat.
    """
    c = cfg or load_config()
    hf = c.visual.higgsfield
    cu = c.visual.comfyui

    # Prioridad 1: ComfyUI cuando habilitado, prefer_for_brand_frames=true,
    # y el tenant default tiene LoRA configurada (= moat de marca).
    cu_on = bool(getattr(cu, "enabled", False))
    cu_prefer = bool(getattr(cu, "prefer_for_brand_frames", False))
    default_tenant = getattr(cu, "default_tenant_id", "default")
    cu_tenants = getattr(cu, "tenants", {}) or {}
    cu_has_lora = False
    if default_tenant in cu_tenants:
        entry = cu_tenants[default_tenant]
        cu_has_lora = bool(getattr(entry, "lora_name", ""))
    if cu_on and cu_prefer and cu_has_lora:
        return VideoSource.COMFYUI

    # Prioridad 2: Higgsfield Soul si está completamente configurado
    soul_on = bool(getattr(hf, "soul_enabled", False)) is True
    creds = bool(
        getattr(hf, "credentials", "") or (
            getattr(hf, "key_id", "") and getattr(hf, "key_secret", "")
        )
    )
    default_soul_id = bool(getattr(hf, "soul_default_reference_id", ""))
    if soul_on and creds and default_soul_id:
        return VideoSource.HIGGSFIELD_SOUL

    # Prioridad 3: Gemini Image (fallback genérico siempre disponible)
    return VideoSource.GEMINI_IMAGE


def _decide_source(
    beat: Beat,
    essence: Essence,
    mode: GenerationMode,
    strategy: VisualStrategy,
) -> VideoSource:
    """Devuelve la fuente primaria sugerida para este beat.

    `VideoSource.PEXELS` se usa como sentinel de "rama stock" — el helper
    `_try_stock` itera sobre TODOS los providers configurados.
    `VideoSource.GEMINI_IMAGE` / `VideoSource.HIGGSFIELD_SOUL` se usan como
    sentinel de "rama IA" (la rama real elige Soul si está habilitado).
    """
    cfg = load_config()
    ia_sentinel = _ia_source(cfg)

    # 1. Override explícito por strategy.
    if strategy == VisualStrategy.STOCK:
        return VideoSource.PEXELS
    if strategy == VisualStrategy.IA:
        return ia_sentinel

    # 2. EXPRESS → favorece stock; excepción hook + (veo o higgsfield) enabled.
    if mode == GenerationMode.EXPRESS:
        any_motion_ai = cfg.visual.veo_enabled or cfg.visual.higgsfield.enabled
        if beat.role == BeatRole.HOOK and any_motion_ai:
            return ia_sentinel
        return VideoSource.PEXELS

    # 3. PREMIUM (y HYBRID que cae aquí) → selectivo.
    if mode == GenerationMode.PREMIUM:
        # Hook siempre IA: es el stop-the-scroll.
        if beat.role == BeatRole.HOOK:
            return ia_sentinel
        # Payoff default stock (callback visual genérico).
        if beat.role == BeatRole.PAYOFF:
            return VideoSource.PEXELS
        # Mechanism: si visual_anchor o text referencian evidence específica
        # (números, nombres propios, jerga) → IA. Si no → stock genérico.
        text_lower = beat.text.lower()
        for evidence_item in essence.evidence:
            if evidence_item.lower() in text_lower:
                return ia_sentinel
        return VideoSource.PEXELS

    # 4. HYBRID strategy con mode no-premium (raro) → tratar como premium.
    return _decide_source(beat, essence, GenerationMode.PREMIUM, VisualStrategy.IA)


# =============================================================================
# HELPERS: ramas stock e IA
# =============================================================================


async def _resolve_stock_clip(
    rich_query: str,
    candidates: list[MaterialInfo],
    *,
    out_dir: Path,
    fetcher=fetch_and_cache,
    visual_reranker: VisualEmbedder | None = None,
    top_k: int = 5,
    frames: int = 3,
    frame_extractor=extract_frames,
    provider: object | None = None,
) -> tuple[MaterialInfo, Path] | None:
    """Descarga y elige el mejor candidato de stock para un beat.

    - Sin `visual_reranker` (ADR-018): baja candidatos en orden léxico y devuelve
      el primero que descargue OK (`candidates[0]` en el caso feliz).
    - Con `visual_reranker` (ADR-025): baja el top-`k`, los re-rankea por frames
      reales (CLIP texto↔imagen) y devuelve el de mayor relevancia visual.

    Devuelve `(material, local_path)` o `None` si nada descarga.
    """
    if not candidates:
        return None

    if visual_reranker is None:
        for m in candidates:
            path = await fetcher(m.url, provider=provider)
            if path is not None:
                return m, path
        return None

    downloaded: list[MaterialInfo] = []
    for m in candidates[:top_k]:
        path = await fetcher(m.url, provider=provider)
        if path is None:
            continue
        m.local_path = str(path)
        downloaded.append(m)
    if not downloaded:
        return None

    ordered = rerank_visual(
        rich_query,
        downloaded,
        visual_reranker,
        out_dir=out_dir,
        frames=frames,
        frame_extractor=frame_extractor,
    )
    best = ordered[0]
    return best, Path(best.local_path)


async def _try_stock(
    beat: Beat,
    visual: BeatVisual,
    aspect: str,
    out_dir: Path,
) -> BeatArtifact | None:
    """Busca en todos los stock providers configurados, en orden.

    Devuelve `None` si ninguno encuentra un clip válido.
    """
    providers = get_all_providers()
    if not providers:
        logger.info(f"[selector] beat {beat.idx}: no hay stock providers configurados")
        return None

    # Rerank visual CLIP (ADR-025): None salvo opt-in → flujo léxico intacto.
    cfg = load_config()
    visual_reranker = get_visual_reranker(cfg)

    try:
        video_aspect = VideoAspect(aspect)
    except ValueError:
        logger.warning(f"[selector] aspect inválido '{aspect}', fallback a 9:16")
        video_aspect = VideoAspect.PORTRAIT

    query = visual.visual_anchor.strip() or beat.text.strip()
    min_dur = max(beat.target_duration_s, 3.0)

    for provider in providers:
        try:
            results = await provider.search(
                query=query,
                aspect=video_aspect,
                min_duration_s=min_dur,
            )
        except Exception as e:
            logger.warning(
                f"[selector] beat {beat.idx}: provider {provider.name} search failed: {e}"
            )
            continue

        if not results:
            continue

        # Re-rank semántico (ADR-018): el provider ya filtró por el query corto;
        # aquí reordenamos su top-N contra la descripción RICA del beat
        # (image_prompt + visual_anchor + texto) para elegir el clip más relevante.
        # Usa embedder semántico si está habilitado/disponible; si no, léxico.
        rich = " ".join(
            p for p in (visual.image_prompt, visual.visual_anchor, beat.text) if p
        )
        results = rerank(rich, results, embedder=get_broll_embedder())

        # Resolución del clip: descarga + (opcional) rerank visual CLIP. El global
        # `fetch_and_cache` se pasa explícito para respetar el patch en tests.
        try:
            resolved = await _resolve_stock_clip(
                rich,
                results,
                out_dir=out_dir,
                provider=provider,
                fetcher=fetch_and_cache,
                visual_reranker=visual_reranker,
                top_k=cfg.visual.broll_visual_candidates,
                frames=cfg.visual.broll_visual_frames,
            )
        except Exception as e:
            logger.warning(
                f"[selector] beat {beat.idx}: resolve {provider.name} failed: {e}"
            )
            continue

        if resolved is None:
            continue
        top, local_path = resolved

        # Corpus de imagen (Unsplash, ADR-022): convertir el still a clip vía
        # ken-burns para respetar el contrato de la rama stock (devuelve video).
        if top.media_kind == "image":
            try:
                clip = await render_ken_burns(
                    frame_path=local_path,
                    duration_s=float(beat.veo_duration),
                    out_path=out_dir / f"stock-img-{beat.idx:02d}.mp4",
                )
            except Exception as e:
                logger.warning(
                    f"[selector] beat {beat.idx}: ken-burns sobre imagen "
                    f"({provider.name}) falló: {e}"
                )
                continue
            logger.success(
                f"[selector] beat {beat.idx}: stock-image hit ({provider.name}) {top.url}"
            )
            return BeatArtifact(
                idx=beat.idx,
                first_frame_path=local_path,
                video_path=clip,
                source=top.provider,
                duration_s=float(beat.veo_duration),
            )

        logger.success(
            f"[selector] beat {beat.idx}: stock hit ({provider.name}) {top.url}"
        )
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=None,
            video_path=local_path,
            source=top.provider,
            duration_s=top.duration_s,
        )

    return None


async def _try_ia_generation(
    beat: Beat,
    visual: BeatVisual,
    essence: Essence,
    out_dir: Path,
) -> BeatArtifact | None:
    """Delega al orchestrator IA (que ya tiene su propio two-tier interno).

    Devuelve `None` si el orchestrator no produce video_path (peor caso ya
    cubierto: frame-only artifact). Aquí consideramos eso como "falló" para
    que el caller pueda intentar stock como fallback externo.
    """
    try:
        results = await generate_beat_videos(
            beats=[beat],
            visuals=[visual],
            out_dir=out_dir,
            content_mode=essence.content_mode,
        )
    except Exception as e:
        logger.warning(f"[selector] beat {beat.idx}: IA gen raised: {e}")
        return None

    if not results:
        return None

    artifact = results[0]
    if artifact.video_path is None:
        logger.warning(
            f"[selector] beat {beat.idx}: IA gen devolvió frame-only (sin video_path)"
        )
        return None

    return artifact


# =============================================================================
# PLACEHOLDER (último recurso)
# =============================================================================


def _make_placeholder_artifact(beat: Beat, out_dir: Path) -> BeatArtifact:
    """Genera un BeatArtifact con frame placeholder y sin video_path.

    El pipeline downstream (editor) puede tratar esto como still + ken-burns
    fallback, o saltar el beat. NUNCA raise.
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        frame = _placeholder_frame(
            out_dir / f"frame-{beat.idx:02d}-placeholder.jpg",
            beat.idx,
        )
    except Exception as e:
        logger.error(f"[selector] beat {beat.idx}: placeholder generation falló: {e}")
        frame = None

    return BeatArtifact(
        idx=beat.idx,
        first_frame_path=frame,
        video_path=None,
        source=VideoSource.LOCAL,
        duration_s=float(beat.veo_duration),
    )


def _track_cost(
    cost_tracker: dict | None,
    beat_idx: int,
    source: VideoSource,
) -> None:
    if cost_tracker is None:
        return
    cost = _COST_ESTIMATES.get(source, 0.0)
    cost_tracker[f"beat_{beat_idx}_{source.value}"] = cost


def _audit_candidates(cfg: object) -> list[VideoSource]:
    """Fuentes plausibles para el scoring auditable (ADR-027), según el config.

    Siempre incluye GEMINI_IMAGE (fallback IA universal) + los stock providers
    activos + las fuentes IA habilitadas. Deduplica preservando orden.
    """
    cands: list[VideoSource] = [VideoSource.GEMINI_IMAGE]
    for p in get_all_providers():
        try:
            cands.append(VideoSource(p.name))
        except ValueError:
            continue
    if getattr(cfg.visual, "veo_enabled", False):
        cands.append(VideoSource.VEO)
    hf = cfg.visual.higgsfield
    if getattr(hf, "enabled", False):
        cands.append(VideoSource.HIGGSFIELD_DOP)
    if getattr(hf, "soul_enabled", False):
        cands.append(VideoSource.HIGGSFIELD_SOUL)
    if getattr(cfg.visual.comfyui, "enabled", False):
        cands.append(VideoSource.COMFYUI)
    if getattr(cfg.visual.fal, "enabled", False):
        cands.append(VideoSource.FAL)
    seen: set[VideoSource] = set()
    out: list[VideoSource] = []
    for c in cands:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _record_decision(
    decision_log: DecisionLog,
    beat: Beat,
    essence: Essence,
    mode: GenerationMode,
    artifact: BeatArtifact,
) -> None:
    """Registra la decisión de fuente del beat en el log auditable (ADR-027).

    Audita en paralelo a la heurística: puntúa las fuentes plausibles con el
    scorer 7-dim e incluye la fuente REAL elegida en el contexto, para poder
    comparar offline heurística vs scoring. Nunca crashea el pipeline.
    """
    try:
        cfg = load_config()
        candidates = _audit_candidates(cfg)
        if artifact.source not in candidates:
            candidates.append(artifact.source)
        text_lower = beat.text.lower()
        has_evidence = any(e.lower() in text_lower for e in essence.evidence)
        context = {
            "beat_role": beat.role.value,
            "mode": mode.value,
            "motion_required": mode == GenerationMode.PREMIUM,
            "has_evidence": has_evidence,
            "actual_source": artifact.source.value,
        }
        decision = decide_provider(candidates, context, label=f"beat_{beat.idx}")
        decision_log.record(decision)
    except Exception as e:  # auditoría nunca debe tumbar la generación
        logger.warning(f"[selector] beat {beat.idx}: audit decision falló: {e}")


# =============================================================================
# API PÚBLICA
# =============================================================================


async def select_and_generate_beat_artifact(
    beat: Beat,
    visual: BeatVisual,
    essence: Essence,
    mode: GenerationMode,
    strategy: VisualStrategy,
    aspect: str,
    out_dir: Path,
    cost_tracker: dict | None = None,
    decision_log: DecisionLog | None = None,
) -> BeatArtifact:
    """Decide stock vs IA vs mixto y delega a la rama correcta.

    NUNCA crashea — siempre devuelve `BeatArtifact` (con placeholder si todo
    falla).

    Args:
        beat: unidad narrativa con role + texto + duración.
        visual: prompt + motion hint + visual anchor.
        essence: claim core + evidence (usado para decidir si beat es específico).
        mode: EXPRESS (favorece stock) o PREMIUM (selectivo).
        strategy: STOCK / IA / HYBRID override.
        aspect: "9:16" | "16:9" | "1:1".
        out_dir: directorio donde se materializan los artefactos.
        cost_tracker: dict mutable opcional para acumular costos por beat.

    Returns:
        BeatArtifact con video_path poblado (o placeholder + frame en peor caso).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    primary_source = _decide_source(beat, essence, mode, strategy)
    logger.info(
        f"[selector] beat {beat.idx} ({beat.role.value}) mode={mode.value} "
        f"strategy={strategy.value} → primary={primary_source.value}"
    )

    try:
        if primary_source == VideoSource.PEXELS:
            # Rama stock primero.
            artifact = await _try_stock(beat, visual, aspect, out_dir)
            if artifact is None:
                logger.info(
                    f"[selector] beat {beat.idx}: stock no encontró, fallback a IA"
                )
                artifact = await _try_ia_generation(beat, visual, essence, out_dir)
        else:
            # Rama IA primero.
            artifact = await _try_ia_generation(beat, visual, essence, out_dir)
            if artifact is None or artifact.video_path is None:
                logger.info(
                    f"[selector] beat {beat.idx}: IA falló, fallback a stock"
                )
                artifact = await _try_stock(beat, visual, aspect, out_dir)
    except Exception as e:
        logger.warning(
            f"[selector] beat {beat.idx}: ambas fuentes fallaron inesperadamente: {e}"
        )
        artifact = None

    if artifact is None or artifact.video_path is None:
        logger.warning(
            f"[selector] beat {beat.idx}: usando placeholder (último recurso)"
        )
        artifact = _make_placeholder_artifact(beat, out_dir)

    _track_cost(cost_tracker, beat.idx, artifact.source)
    if decision_log is not None:
        _record_decision(decision_log, beat, essence, mode, artifact)
    return artifact


async def generate_all_beat_artifacts(
    beats: list[Beat],
    visuals: list[BeatVisual],
    essence: Essence,
    mode: GenerationMode,
    strategy: VisualStrategy,
    aspect: str,
    out_dir: Path,
    cost_tracker: dict | None = None,
    decision_log: DecisionLog | None = None,
) -> list[BeatArtifact]:
    """Fan-out paralelo de `select_and_generate_beat_artifact()`.

    Mantiene orden alineado con `beats`. `beats` y `visuals` deben tener
    misma longitud — si no, raise ValueError.
    """
    if len(beats) != len(visuals):
        raise ValueError(
            f"generate_all_beat_artifacts: beats ({len(beats)}) y visuals "
            f"({len(visuals)}) deben alinear"
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts = await asyncio.gather(
        *(
            select_and_generate_beat_artifact(
                beat=beat,
                visual=visual,
                essence=essence,
                mode=mode,
                strategy=strategy,
                aspect=aspect,
                out_dir=out_dir,
                cost_tracker=cost_tracker,
                decision_log=decision_log,
            )
            for beat, visual in zip(beats, visuals)
        )
    )
    return list(artifacts)


__all__ = [
    "generate_all_beat_artifacts",
    "select_and_generate_beat_artifact",
]
