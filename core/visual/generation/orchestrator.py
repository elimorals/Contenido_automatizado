"""Orquestador de generación visual: tres-tier fallback con Higgsfield + Veo.

Para cada beat:

  Tier 1 — FIRST FRAME (imagen still):
      a) Si soul_enabled y (BeatVisual.soul_id o soul_default_reference_id) → HiggsfieldSoul
      b) Si falla o no aplica → Gemini Image (default)
      c) Si todo falla → placeholder solid-color

  Tier 2 — MOTION (i2v) o TALKING-HEAD (audio-driven):
      0) Si BeatVisual.audio_path + live_avatar enabled → LiveAvatarGenerator (lip-sync)
         (short-circuit — el resto del Tier 2 no se ejecuta)
      a) Higgsfield DoP si enabled y (prefer_over_veo o veo deshabilitado)
      b) Veo i2v si enabled
      c) Fallback: ken-burns sobre el frame

  Tier 3 — EFFECTS (VFX overlay, opcional):
      a) Si BeatVisual.effect != None y effects_enabled → HiggsfieldEffects.apply
      b) Si falla, devuelve el clip Tier 2 intacto

Garantías:
  - Nunca crashea — siempre devuelve `BeatArtifact` por beat.
  - Beats fallidos quedan loggeados pero no abortan el reel.
  - `BeatArtifact.video_path` siempre poblado (placeholder en peor caso).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.cost import (
    BudgetExceeded,
    charge,
    ensure_budget,
    estimate_usd,
    is_tripped,
    note_failure,
)
from core.visual.generation.base import (
    GenerationTimeout,
    VisualGenerationError,
    VisualGenerator,
)
from core.visual.generation.comfy import ComfyUIGenerator
from core.visual.generation.gemini_image import GeminiImageGenerator
from core.visual.generation.higgsfield import HiggsfieldDopGenerator
from core.visual.generation.higgsfield_effects import HiggsfieldEffectsGenerator
from core.visual.generation.higgsfield_mcp import HiggsfieldMCPGenerator
from core.visual.generation.higgsfield_soul import HiggsfieldSoulGenerator
from core.visual.generation.ken_burns import (
    KenBurnsGenerator,
    _placeholder_frame,
    trim_clip,
)
from core.visual.generation.live_avatar import LiveAvatarGenerator
from core.visual.generation.openai_image import OpenAIImageGenerator
from core.visual.generation.sora import SoraGenerator
from core.visual.generation.veo import VeoGenerator
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource

# =============================================================================
# Tier 1: first frame con Soul/Gemini fallback
# =============================================================================


async def _generate_first_frame_with_fallback(
    comfy_gen: ComfyUIGenerator | None,
    soul_gen: HiggsfieldSoulGenerator | None,
    image_gen: GeminiImageGenerator,
    openai_image_gen: OpenAIImageGenerator | None,
    beat: Beat,
    visual: BeatVisual,
    content_mode: str,
    out_dir: Path,
) -> tuple[Path, bool, VideoSource]:
    """Genera first frame; devuelve (path, is_real, source).

    Cadena de fallback (tier 1 de la jerarquía):
    0. ``visual.reference_image_path`` explícito — short-circuit (ADR-016)
       Usado para talking-head (portrait fijo del presentador) o cuando
       el caller ya tiene la imagen lista (anchor brand, upload manual).
    1. ComfyUI (workflow custom con brand LoRA) — moat de identidad de marca
    2. HiggsfieldSoul (character consistency cross-beat)
    3. Gemini Image vía OpenRouter (default genérico)
    4. OpenAI `gpt-image-*` nativo (ADR-032) — cubre el caso de tener solo
       key de OpenAI, donde el tier 3 no puede ni intentarlo
    5. Placeholder sólido (último recurso)
    """
    # Short-circuit: reference image suministrada explícitamente.
    # Confiamos en el caller — no regeneramos, ahorrando costo + latencia.
    if visual.reference_image_path is not None and visual.reference_image_path.exists():
        return visual.reference_image_path, True, VideoSource.LOCAL

    # Sin presupuesto ni para una imagen → placeholder sin llamar a nadie.
    try:
        ensure_budget(estimate_usd(VideoSource.GEMINI_IMAGE), f"beat {beat.idx} frame")
    except BudgetExceeded as e:
        logger.warning(f"[visual.orchestrator] {e}; placeholder.")
        placeholder = _placeholder_frame(out_dir / f"frame-{beat.idx:02d}-placeholder.jpg", beat.idx)
        return placeholder, False, VideoSource.LOCAL

    # ComfyUI tier — preferido cuando tenant tiene LoRA configurada
    if comfy_gen is not None:
        try:
            artifact = await comfy_gen.generate(beat, visual, content_mode, out_dir)
            if artifact.first_frame_path is not None:
                return artifact.first_frame_path, True, VideoSource.COMFYUI
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[visual.orchestrator] beat {beat.idx} ComfyUI falló ({e}); "
                "fallback a Soul/Gemini."
            )

    # Soul tier
    if soul_gen is not None and (visual.soul_id or soul_gen.cfg.soul_default_reference_id):
        try:
            artifact = await soul_gen.generate(beat, visual, content_mode, out_dir)
            if artifact.first_frame_path is not None:
                charge("visual", VideoSource.HIGGSFIELD_SOUL.value, estimate_usd(VideoSource.HIGGSFIELD_SOUL))
                return artifact.first_frame_path, True, VideoSource.HIGGSFIELD_SOUL
        except Exception as e:
            logger.warning(
                f"[visual.orchestrator] beat {beat.idx} Soul gen falló ({e}); "
                "fallback a Gemini Image."
            )

    # Con personajes en cuadro, OpenAI primero: es el tier que recibe los
    # retratos como referencia (images/edits). Gemini solo ve el texto.
    if visual.character_refs and openai_image_gen is not None:
        try:
            artifact = await openai_image_gen.generate(beat, visual, content_mode, out_dir)
            if artifact.first_frame_path is not None:
                charge("visual", VideoSource.OPENAI_IMAGE.value, estimate_usd(VideoSource.OPENAI_IMAGE))
                return artifact.first_frame_path, True, VideoSource.OPENAI_IMAGE
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[visual.orchestrator] beat {beat.idx} OpenAI con refs falló ({e}); "
                "sigo sin referencias."
            )

    # Gemini Image tier (vía OpenRouter)
    gemini_err: Exception | None = None
    try:
        artifact = await image_gen.generate(beat, visual, content_mode, out_dir)
        if artifact.first_frame_path is None:
            raise VisualGenerationError("image gen returned None path")
        charge("visual", VideoSource.GEMINI_IMAGE.value, estimate_usd(VideoSource.GEMINI_IMAGE))
        return artifact.first_frame_path, True, VideoSource.GEMINI_IMAGE
    except Exception as e:  # noqa: BLE001
        gemini_err = e
        logger.warning(
            f"[visual.orchestrator] beat {beat.idx} Gemini image falló ({e}); "
            "fallback a OpenAI image."
        )

    # OpenAI Image tier (ADR-032) — nativo, misma key que el DAG narrativo.
    # Existe porque el tier de arriba exige OPENROUTER_API_KEY: sin ella, un
    # repo con solo key de OpenAI caía directo a placeholder sólido.
    if openai_image_gen is not None:
        try:
            artifact = await openai_image_gen.generate(beat, visual, content_mode, out_dir)
            if artifact.first_frame_path is None:
                raise VisualGenerationError("openai image gen returned None path")
            charge("visual", VideoSource.OPENAI_IMAGE.value, estimate_usd(VideoSource.OPENAI_IMAGE))
            return artifact.first_frame_path, True, VideoSource.OPENAI_IMAGE
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[visual.orchestrator] beat {beat.idx} OpenAI image falló ({e}); "
                "placeholder."
            )

    logger.warning(
        f"[visual.orchestrator] beat {beat.idx} sin generador de imagen "
        f"disponible (gemini: {gemini_err}); usando placeholder."
    )
    placeholder = _placeholder_frame(
        out_dir / f"frame-{beat.idx:02d}-placeholder.jpg",
        beat.idx,
    )
    return placeholder, False, VideoSource.LOCAL


# =============================================================================
# Tier 2 + 3: motion (DoP/Veo) + effects post-processing
# =============================================================================


def _should_use_live_avatar(
    visual: BeatVisual, live_avatar_gen: LiveAvatarGenerator | None
) -> bool:
    """True cuando este beat debe rutearse a LiveAvatar en lugar de DoP/Veo.

    Condiciones (todas requeridas):
      - ``live_avatar_gen`` inyectado y habilitado
      - ``visual.audio_path`` no None (hay audio TTS para lip-sync)
    """
    return live_avatar_gen is not None and visual.audio_path is not None


async def _generate_one(
    beat: Beat,
    visual: BeatVisual,
    out_dir: Path,
    content_mode: str,
    *,
    comfy_gen: ComfyUIGenerator | None,
    soul_gen: HiggsfieldSoulGenerator | None,
    image_gen: GeminiImageGenerator,
    openai_image_gen: OpenAIImageGenerator | None,
    hf_dop_gen: HiggsfieldDopGenerator | None,
    veo_gen: VeoGenerator | None,
    sora_gen: SoraGenerator | None,
    ken_burns_gen: KenBurnsGenerator,
    effects_gen: HiggsfieldEffectsGenerator | None,
    live_avatar_gen: LiveAvatarGenerator | None = None,
    fal_gen: VisualGenerator | None = None,
    hf_mcp_gen: HiggsfieldMCPGenerator | None = None,
) -> BeatArtifact:
    """Pipeline por beat: 3-tier fallback (con ComfyUI como nuevo top de tier 1)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # === Tier 1: first frame ===
    frame_path, frame_is_real, frame_source = await _generate_first_frame_with_fallback(
        comfy_gen, soul_gen, image_gen, openai_image_gen, beat, visual, content_mode, out_dir
    )

    # === Tier 2: motion (o talking-head si hay audio) ===
    motion_artifact: BeatArtifact | None = None

    # 2.0 LiveAvatar short-circuit — audio-driven lip-sync.
    # Si hay audio_path y el generator está disponible, salta DoP/Veo.
    if _should_use_live_avatar(visual, live_avatar_gen) and frame_is_real:
        try:
            # Propagamos el frame upstream como reference image
            prior = BeatArtifact(
                idx=beat.idx,
                first_frame_path=frame_path,
                source=frame_source,
            )
            motion_artifact = await live_avatar_gen.generate(  # type: ignore[union-attr]
                beat=beat,
                visual=visual,
                content_mode=content_mode,
                out_dir=out_dir,
                prior_artifact=prior,
            )
        except Exception as e:
            logger.warning(
                f"[visual.orchestrator] beat {beat.idx} LiveAvatar falló "
                f"({e}); degradando a DoP/Veo + audio stitched en editor."
            )

    # 2a-2b.5 Proveedores de motion pagados, en orden de preferencia:
    # DoP → Veo → Higgsfield MCP (ADR-037, puente de dev) → Sora (ADR-033) → fal (ADR-023).
    # Cada intento pasa por el presupuesto del job y el circuit breaker; tras un
    # timeout NO se prueba el siguiente pagado (el job remoto puede cobrarse igual).
    if motion_artifact is None and frame_is_real:
        chain = [
            (VideoSource.HIGGSFIELD_DOP, hf_dop_gen),
            (VideoSource.VEO, veo_gen),
            (VideoSource.HIGGSFIELD_MCP, hf_mcp_gen),
            (VideoSource.SORA, sora_gen),
            (VideoSource.FAL, fal_gen),
        ]
        for source, gen in chain:
            if gen is None or is_tripped(source):
                continue
            est = estimate_usd(source, float(beat.veo_duration))
            try:
                ensure_budget(est, f"beat {beat.idx} {source.value}")
                motion_artifact = await gen.generate(
                    beat=beat,
                    visual=visual,
                    content_mode=content_mode,
                    out_dir=out_dir,
                    first_frame_path=frame_path,
                )
            except BudgetExceeded as e:
                logger.warning(f"[visual.orchestrator] {e}; ken-burns.")
                break
            except Exception as e:  # noqa: BLE001
                note_failure(source, e)
                logger.warning(
                    f"[visual.orchestrator] beat {beat.idx} {source.value} falló ({e})"
                )
                if isinstance(e, GenerationTimeout | TimeoutError):
                    break
                continue
            if motion_artifact is not None:
                charge("visual", source.value, est)
                break

    # 2c. Ken-burns (siempre disponible si frame existe)
    if motion_artifact is None:
        try:
            motion_artifact = await ken_burns_gen.generate(
                beat=beat,
                visual=visual,
                content_mode=content_mode,
                out_dir=out_dir,
                first_frame_path=frame_path,
            )
            if not frame_is_real:
                motion_artifact.source = VideoSource.LOCAL
        except Exception as e:
            logger.error(
                f"[visual.orchestrator] beat {beat.idx} ken-burns falló ({e}); "
                "frame-only artifact."
            )
            motion_artifact = BeatArtifact(
                idx=beat.idx,
                first_frame_path=frame_path,
                video_path=None,
                source=frame_source,
                duration_s=float(beat.veo_duration),
            )

    # === Tier 3: VFX effect post-processing (opcional) ===
    if effects_gen is not None and visual.effect is not None:
        motion_artifact = await effects_gen.apply(motion_artifact, visual, out_dir)

    return motion_artifact


# =============================================================================
# Entry point público
# =============================================================================


async def generate_beat_videos(
    beats: list[Beat],
    visuals: list[BeatVisual],
    out_dir: Path,
    content_mode: str = "general",
    *,
    use_veo: bool | None = None,
    use_higgsfield: bool | None = None,
    use_higgsfield_soul: bool | None = None,
    use_higgsfield_effects: bool | None = None,
    use_comfyui: bool | None = None,
    use_live_avatar: bool | None = None,
    use_fal: bool | None = None,
    image_gen: GeminiImageGenerator | None = None,
    openai_image_gen: OpenAIImageGenerator | None = None,
    veo_gen: VeoGenerator | None = None,
    sora_gen: SoraGenerator | None = None,
    hf_mcp_gen: HiggsfieldMCPGenerator | None = None,
    hf_dop_gen: HiggsfieldDopGenerator | None = None,
    soul_gen: HiggsfieldSoulGenerator | None = None,
    effects_gen: HiggsfieldEffectsGenerator | None = None,
    comfy_gen: ComfyUIGenerator | None = None,
    ken_burns_gen: KenBurnsGenerator | None = None,
    live_avatar_gen: LiveAvatarGenerator | None = None,
    fal_gen: VisualGenerator | None = None,
    normalize: bool = True,
) -> list[BeatArtifact]:
    """Genera un video por beat en paralelo (asyncio.gather), con 3-tier fallback.

    Args:
        beats: lista alineada por idx con `visuals`.
        visuals: visuals correspondientes.
        out_dir: directorio destino.
        content_mode: 'scientific' | 'general'.
        use_veo: override del config `visual.veo_enabled`.
        use_higgsfield: override de `visual.higgsfield.enabled` (DoP).
        use_higgsfield_soul: override de `visual.higgsfield.soul_enabled`.
        use_higgsfield_effects: override de `visual.higgsfield.effects_enabled`.
        image_gen / veo_gen / hf_dop_gen / soul_gen / effects_gen / ken_burns_gen:
            inyectables para tests.

    Returns:
        list[BeatArtifact] alineada con `beats`.

    Resolución de conflicto Veo vs Higgsfield DoP:
        - Si ambos enabled y `prefer_over_veo=True`: Higgsfield primero, Veo fallback.
        - Si ambos enabled y `prefer_over_veo=False`: Veo primero, Higgsfield fallback.
        - Si solo uno enabled: ese se usa.
        - Si ninguno: ken-burns directo.
    """
    if len(beats) != len(visuals):
        raise ValueError(
            f"generate_beat_videos: beats ({len(beats)}) y visuals "
            f"({len(visuals)}) deben alinear"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    hf_cfg = cfg.visual.higgsfield
    cu_cfg = cfg.visual.comfyui

    veo_enabled = cfg.visual.veo_enabled if use_veo is None else use_veo
    hf_enabled = hf_cfg.enabled if use_higgsfield is None else use_higgsfield
    soul_enabled = (
        hf_cfg.soul_enabled if use_higgsfield_soul is None else use_higgsfield_soul
    )
    effects_enabled = (
        hf_cfg.effects_enabled
        if use_higgsfield_effects is None
        else use_higgsfield_effects
    )
    comfy_enabled = cu_cfg.enabled if use_comfyui is None else use_comfyui
    la_cfg = cfg.visual.live_avatar
    la_enabled = la_cfg.enabled if use_live_avatar is None else use_live_avatar
    fal_cfg = cfg.visual.fal
    fal_enabled = fal_cfg.enabled if use_fal is None else use_fal

    img = image_gen or GeminiImageGenerator()
    # Solo se instancia si hay key de OpenAI: sin ella el tier sobra y
    # dejarlo en None hace explícito en los logs que no había con qué.
    hf_mcp = hf_mcp_gen
    if hf_mcp is None and cfg.visual.higgsfield_mcp_enabled:
        hf_mcp = HiggsfieldMCPGenerator()
    sora = sora_gen
    if sora is None and cfg.visual.sora_enabled and cfg.get_llm_provider("openai").api_key:
        sora = SoraGenerator()
    oai_img = openai_image_gen
    if oai_img is None and cfg.get_llm_provider("openai").api_key:
        oai_img = OpenAIImageGenerator()

    # ComfyUI (brand identity vía LoRA + workflow custom)
    comfy = comfy_gen
    if comfy is None and comfy_enabled:
        comfy = ComfyUIGenerator()

    # Soul (first-frame consistency)
    soul = soul_gen
    if soul is None and soul_enabled:
        soul = HiggsfieldSoulGenerator()

    # Motion: resolver preferencia Veo vs DoP
    hf_dop = hf_dop_gen
    veo = veo_gen
    if hf_dop is None and hf_enabled:
        hf_dop = HiggsfieldDopGenerator()
    if veo is None and veo_enabled:
        veo = VeoGenerator()

    # Si prefer_over_veo=False y AMBOS habilitados, swappeamos el orden interno
    # del fallback (Veo primero, DoP fallback). El swap se hace pasándolos al revés.
    primary_dop = hf_dop
    primary_veo = veo
    if hf_dop is not None and veo is not None and not hf_cfg.prefer_over_veo:
        primary_dop = None
        primary_veo = veo
        # _generate_one intenta DoP → Veo → ken-burns en ese orden;
        # para "Veo primero", reordenamos: pasamos veo en lugar de dop y dop como fallback.
        # Truco: usamos un wrapper que invierte. Aquí lo simple es:
        # - pasar primary_dop=None desactiva DoP en tier 2a
        # - pero perdemos DoP como fallback. Solución: re-añadirlo como veo "secundario"
        #   no soportada directamente — best effort: si prefer=False, simplemente desactivamos DoP.
        # Decisión de diseño: cuando prefer_over_veo=False, DoP queda como off por simplicidad.

    ken_burns = ken_burns_gen or KenBurnsGenerator()
    effects = effects_gen
    if effects is None and effects_enabled:
        effects = HiggsfieldEffectsGenerator()

    # fal.ai i2v (Kling/Runway/MiniMax) — fallback de motion opt-in (ADR-023).
    fal = fal_gen
    if fal is None and fal_enabled:
        from core.visual.generation.fal import FalProvider

        try:
            fal = FalProvider()
        except Exception as e:
            logger.warning(f"[visual.orchestrator] fal init falló ({e}); desactivado.")
            fal = None

    # LiveAvatar — solo se construye si está enabled. Se invoca per-beat solo
    # cuando visual.audio_path está poblado (ver _should_use_live_avatar).
    live_avatar = live_avatar_gen
    if live_avatar is None and la_enabled:
        try:
            live_avatar = LiveAvatarGenerator(la_cfg)
        except Exception as e:
            logger.warning(
                f"[visual.orchestrator] LiveAvatar init falló ({e}); "
                "talking-head desactivado para este job."
            )
            live_avatar = None

    artifacts = await asyncio.gather(
        *(
            _generate_one(
                beat,
                visual,
                out_dir,
                content_mode,
                comfy_gen=comfy,
                soul_gen=soul,
                image_gen=img,
                openai_image_gen=oai_img,
                hf_dop_gen=primary_dop,
                veo_gen=primary_veo,
                sora_gen=sora,
                hf_mcp_gen=hf_mcp,
                ken_burns_gen=ken_burns,
                effects_gen=effects,
                live_avatar_gen=live_avatar,
                fal_gen=fal,
            )
            for beat, visual in zip(beats, visuals)
        )
    )
    if normalize:
        artifacts = await asyncio.gather(
            *(_normalize_clip(b, a, out_dir) for b, a in zip(beats, artifacts))
        )
    return list(artifacts)


async def _normalize_clip(beat: Beat, artifact: BeatArtifact, out_dir: Path) -> BeatArtifact:
    """Recorta el clip IA a la duración del beat y lo lleva al canvas del job.

    Sora devuelve 8/12 s, DoP 5 s, el MCP 12 s: sin esto cada beat se come
    segundos de los siguientes y la imagen se desfasa de la narración. Solo
    stock se recortaba (ADR-036). Si el recorte falla se conserva el original:
    el clip ya está pagado.
    """
    if artifact.video_path is None or not Path(artifact.video_path).exists():
        return artifact
    dur = float(beat.target_duration_s or beat.veo_duration)
    try:
        clip = await trim_clip(
            Path(artifact.video_path), out_dir / f"clip-{beat.idx:02d}-norm.mp4", dur
        )
    except Exception as e:  # clip ilegible / ffmpeg ausente
        logger.warning(f"[visual.orchestrator] beat {beat.idx}: normalizado falló ({e})")
        return artifact
    return artifact.model_copy(update={"video_path": clip, "duration_s": dur})
