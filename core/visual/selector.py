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

from core.visual.generation import generate_beat_videos
from core.visual.generation.ken_burns import _placeholder_frame
from core.visual.stock import fetch_and_cache, get_all_providers
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Essence,
    GenerationMode,
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
    soul_on = bool(getattr(hf, "soul_enabled", False)) is True
    creds = bool(
        getattr(hf, "credentials", "") or (
            getattr(hf, "key_id", "") and getattr(hf, "key_secret", "")
        )
    )
    default_soul_id = bool(getattr(hf, "soul_default_reference_id", ""))
    if soul_on and creds and default_soul_id:
        return VideoSource.HIGGSFIELD_SOUL
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

        top = results[0]
        try:
            local_path = await fetch_and_cache(top.url, provider=provider)
        except Exception as e:
            logger.warning(
                f"[selector] beat {beat.idx}: download {top.url} failed: {e}"
            )
            continue

        if local_path is None:
            continue

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
            )
            for beat, visual in zip(beats, visuals)
        )
    )
    return list(artifacts)


__all__ = [
    "generate_all_beat_artifacts",
    "select_and_generate_beat_artifact",
]
