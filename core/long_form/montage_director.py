"""Director concreto para long-form con intent=MONTAGE (ADR-029).

Es el primer intent genérico con `produce` real: NARRATIVE y MOTION siguen en
stub. Se eligió MONTAGE para estrenar el camino porque es el más barato de
validar — sin diálogo obligatorio, sin consistencia de personaje cross-shot,
sin ComfyUI ni GPU. La cadena entera reusa lo que ya existe:

    Shot → Beat + BeatVisual → core.visual.selector (stock vs IA + audit trail)
         → core.tts (sample-accurate, silent engine si no hay diálogo)
         → core.editor.stitch_video (single-pass ffmpeg + BGM, ADR-001)

Lo único nuevo aquí es el **pegamento**: mapeo Shot↔Beat, el bucle
checkpointeado y el veredicto por shot.

Reanudabilidad (ADR-024)
------------------------
Cada shot renderizado se marca en el `CheckpointStore` inmediatamente. Un
re-run del mismo `job_id` salta lo ya hecho: si el proceso muere en el shot
40 de 120, reanudar cuesta 80 generaciones, no 120. Para un job de $16-80 eso
es la diferencia entre reintentar y no reintentar.

Veredicto por shot (opt-in)
---------------------------
Con `long_form.shot_accept_threshold > 0`, cada shot pasa por
`BestImageSelector.judge()`. Si el veredicto lo rechaza se re-genera hasta
`shot_max_rework` veces; si sigue rechazado se acepta igual y se anota
`needs_rework: true` en el checkpoint, para que quede el rastro sin bloquear
la entrega. Con el default (0.0) nada de esto corre y no cuesta un token.

Entry point:
    ``await produce_montage(job, script)``
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from core.checkpoint import Checkpoint, CheckpointStore, get_checkpoint_store
from core.long_form.types import LongFormPlanError
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Essence,
    GenerationMode,
    LongFormJob,
    LongFormScript,
    MotionHint,
    Scene,
    Shot,
    VideoAspect,
    VideoSource,
    VisualStrategy,
)

# Fases del checkpoint (nombres estables — cambiarlos invalida checkpoints viejos).
PHASE_TTS = "tts"
PHASE_STITCH = "stitch"

# camera_movement (lenguaje de storyboard) → MotionHint (lo que entiende el
# generador visual). Lo que no esté mapeado cae en SLOW_ZOOM_IN, que es el
# default del pipeline de reels y nunca queda mal en un montage.
_CAMERA_TO_MOTION: dict[str, MotionHint] = {
    "static": MotionHint.STATIC,
    "dolly_in": MotionHint.SLOW_ZOOM_IN,
    "zoom": MotionHint.SLOW_ZOOM_IN,
    "dolly_out": MotionHint.SLOW_ZOOM_OUT,
    "pan_left": MotionHint.PAN_LEFT,
    "pan_right": MotionHint.PAN_RIGHT,
    "track": MotionHint.KEN_BURNS,
    "tilt": MotionHint.KEN_BURNS,
}


# =============================================================================
# Mapeo Shot → Beat / BeatVisual
# =============================================================================


def _veo_bucket(target_s: float) -> int:
    """Bucket Veo fijo (4/6/8) más chico que cubra `target_s` + margen."""
    for bucket in (4, 6, 8):
        if bucket >= target_s + 0.3:
            return bucket
    return 8


def _role_for(idx: int, total: int) -> BeatRole:
    if idx == 0:
        return BeatRole.HOOK
    if idx == total - 1:
        return BeatRole.PAYOFF
    return BeatRole.MECHANISM


def _shot_to_beat(shot: Shot, idx: int, total: int) -> Beat:
    duration = max(1.0, shot.target_duration_s)
    return Beat(
        idx=idx,
        role=_role_for(idx, total),
        text=(shot.dialogue or shot.visual_description)[:500],
        target_duration_s=duration,
        veo_duration=_veo_bucket(duration),  # type: ignore[arg-type]
    )


def _enrich_prompt(shot: Shot, scene: Scene, script: LongFormScript) -> str:
    """Prompt visual del shot + descripciones de sus assets anclados.

    Aquí se gana la "unified style consistency": si el shot declara
    `assets_present=["café Luna"]` y ese asset existe en el script, su
    descripción viaja EN EL PROMPT, así que el café se dibuja igual en la
    escena 2 y en la 9 en vez de reinventarse cada vez.
    """
    parts = [shot.visual_description]

    index = script.assets_by_name()
    names = list(shot.assets_present)
    # El entorno de la escena aplica a todos sus shots aunque el shot no lo liste.
    if scene.environment and scene.environment not in names:
        names.append(scene.environment)
    for name in names:
        asset = index.get(name)
        if asset is not None and asset.description:
            parts.append(f"{asset.name}: {asset.description}")

    if scene.setting and scene.setting not in shot.visual_description:
        parts.append(scene.setting)
    return ". ".join(p.strip().rstrip(".") for p in parts if p.strip())


def _shot_to_visual(shot: Shot, scene: Scene, script: LongFormScript) -> BeatVisual:
    return BeatVisual(
        image_prompt=_enrich_prompt(shot, scene, script),
        motion_hint=_CAMERA_TO_MOTION.get(shot.camera_movement, MotionHint.SLOW_ZOOM_IN),
        visual_anchor=(shot.assets_present or shot.characters_present or [scene.title])[0],
    )


def _script_to_essence(script: LongFormScript) -> Essence:
    """`Essence` sintética para que el selector pueda decidir stock vs IA.

    El selector la usa para juzgar cuán *específico* es un beat. Un montage no
    tiene "claim + evidencia" como un reel científico, así que se proyecta el
    arco: logline como claim, acto 2 como mecanismo, temas como evidencia.
    """
    arc = script.arc
    evidence = [t for t in arc.themes if t.strip()][:3] or [arc.logline[:200]]
    return Essence(
        core_claim=arc.logline[:200],
        mechanism=(arc.act2_confrontation or arc.logline)[:500],
        evidence=evidence,
        content_mode="general",
        domain=(arc.themes[0][:30] if arc.themes else "montage"),
    )


def flatten_shots(script: LongFormScript) -> list[tuple[int, Shot, Scene]]:
    """Aplana scenes→shots con índice global estable (el del checkpoint)."""
    flat: list[tuple[int, Shot, Scene]] = []
    for scene in script.scenes:
        for shot in scene.shots:
            flat.append((len(flat), shot, scene))
    return flat


# =============================================================================
# TTS por shot
# =============================================================================


async def _tts_shot(
    shot: Shot,
    idx: int,
    audio_dir: Path,
    *,
    engine_name: str,
    voice: str,
) -> tuple[Path, float]:
    """Sintetiza el audio de UN shot. Devuelve (wav_path, duration_s).

    Sin diálogo (el caso normal en montage: "emotion through composition") se
    usa el engine `silent`, que produce silencio de la duración planificada.
    Así el resto del pipeline tiene un único camino en vez de dos.
    """
    from core.tts import get_engine

    shot_dir = audio_dir / f"shot_{idx:03d}"
    text = (shot.dialogue or "").strip()

    if not text:
        engine = get_engine("silent")
        artifact = await engine.synthesize(
            text=shot.visual_description,
            voice="",
            out_dir=shot_dir,
            target_duration_s=max(1.0, shot.target_duration_s),
        )
    else:
        engine = get_engine(engine_name)
        artifact = await engine.synthesize(
            text=text,
            voice=voice,
            out_dir=shot_dir,
            target_duration_s=None,
        )
    return Path(artifact.path), artifact.duration_s


# =============================================================================
# Render de un shot (con veredicto + rework opcional)
# =============================================================================


async def _render_shot(
    beat: Beat,
    visual: BeatVisual,
    essence: Essence,
    shots_dir: Path,
    *,
    aspect: str,
    mode: GenerationMode,
    strategy: VisualStrategy,
    cost_tracker: dict,
    decision_log: object | None,
    judge: object | None,
    max_rework: int,
) -> tuple[BeatArtifact, bool]:
    """Genera el clip de un shot. Devuelve (artifact, needs_rework).

    `needs_rework=True` significa "se entregó igual, pero el veredicto no lo
    aprobó" — queda anotado en el checkpoint para poder re-lanzar solo esos.
    """
    from core.visual.selector import select_and_generate_beat_artifact

    attempts = max(1, max_rework + 1) if judge is not None else 1
    artifact: BeatArtifact | None = None

    for attempt in range(attempts):
        artifact = await select_and_generate_beat_artifact(
            beat=beat,
            visual=visual,
            essence=essence,
            mode=mode,
            strategy=strategy,
            aspect=aspect,
            out_dir=shots_dir,
            cost_tracker=cost_tracker,
            decision_log=decision_log,  # type: ignore[arg-type]
        )
        if judge is None or artifact.first_frame_path is None:
            return artifact, False

        try:
            verdict = await judge.judge(  # type: ignore[attr-defined]
                [artifact.first_frame_path], [], visual.image_prompt
            )
        except Exception as e:  # noqa: BLE001 — el gate nunca tumba el render
            logger.warning(f"[montage] shot {beat.idx}: veredicto falló ({e}); acepto")
            return artifact, False

        if verdict.acceptable:
            return artifact, False
        logger.info(
            f"[montage] shot {beat.idx}: rechazado "
            f"(intento {attempt + 1}/{attempts}) — {verdict.reason}"
        )

    assert artifact is not None  # attempts >= 1
    return artifact, True


# =============================================================================
# Entry point
# =============================================================================


async def produce_montage(
    job: LongFormJob,
    script: LongFormScript,
    *,
    out_dir: Path | None = None,
    voice_name: str = "no-voice",
    aspect: VideoAspect = VideoAspect.LANDSCAPE,
    mode: GenerationMode = GenerationMode.PREMIUM,
    strategy: VisualStrategy = VisualStrategy.HYBRID,
    bgm_type: str = "random",
    resume: bool | None = None,
) -> LongFormJob:
    """Pipeline montage end-to-end: shots → clips → stitch. Reanudable.

    Args:
        job: `LongFormJob` de `plan_long_form`.
        script: `LongFormScript` con scenes/shots. Debe tener ≥1 shot.
        out_dir: dónde escribir intermedios y final.
            Default `{long_form.working_dir}/{job_id}/montage/`.
        voice_name: voz TTS para los shots CON diálogo. `"no-voice"` (default)
            fuerza silencio en todos — el montage clásico va con BGM sola.
        aspect: 16:9 por default (long-form es horizontal, no vertical).
        mode / strategy: se pasan tal cual al selector visual.
        bgm_type: `"random"` | `"none"` | `"custom"`.
        resume: saltar shots ya hechos. `None` = `long_form.resume_enabled`.

    Returns:
        `job` con `status="completed"` + `final_video_path`, o `status="failed"`
        + `error_message`. No levanta excepciones por fallos de render: el
        selector siempre devuelve un artifact (placeholder en el peor caso).
    """
    cfg = load_config()
    lf = cfg.long_form
    if not lf.enabled:
        raise LongFormPlanError("long_form deshabilitado (LONG_FORM_ENABLED=true)")

    flat = flatten_shots(script)
    if not flat:
        raise LongFormPlanError("script sin shots — nada que producir")

    base_dir = out_dir or (Path(lf.working_dir) / job.job_id / "montage")
    audio_dir = base_dir / "audio"
    shots_dir = base_dir / "shots"
    for d in (base_dir, audio_dir, shots_dir):
        d.mkdir(parents=True, exist_ok=True)

    resume = lf.resume_enabled if resume is None else resume
    store: CheckpointStore = get_checkpoint_store()
    ckpt: Checkpoint = store.load(job.job_id) if resume else Checkpoint(task_id=job.job_id)

    total = len(flat)
    job.status = "shooting"
    job.total_shots = total
    logger.info(
        f"[montage] {job.job_id}: {total} shots, resume={resume}, "
        f"ya hechos={total - len(ckpt.pending_beats(total))}"
    )

    # === Fase 1: TTS por shot (barato, paralelo, checkpointeado como fase) ===
    if ckpt.is_phase_done(PHASE_TTS):
        tts_data = ckpt.phase_data(PHASE_TTS)
        logger.info(f"[montage] {job.job_id}: TTS ya hecho, reusando {len(tts_data)} wavs")
    else:
        from core.tts import detect_engine_and_voice

        engine_name, voice = detect_engine_and_voice(voice_name)
        results = await asyncio.gather(
            *(
                _tts_shot(shot, idx, audio_dir, engine_name=engine_name, voice=voice)
                for idx, shot, _ in flat
            ),
            return_exceptions=True,
        )
        tts_data = {}
        for (idx, _shot, _scene), result in zip(flat, results, strict=True):
            if isinstance(result, Exception):
                job.status = "failed"
                job.error_message = f"TTS shot#{idx}: {result}"
                logger.error(f"[montage] {job.job_id}: {job.error_message}")
                return job
            wav, duration = result
            tts_data[str(idx)] = {"wav": str(wav), "duration_s": duration}
        ckpt.mark_phase(PHASE_TTS, tts_data)
        store.save(ckpt)

    # La duración medida del audio manda sobre la planificada — así el video y
    # la narración no acumulan drift shot a shot (mismo principio que ADR-001).
    beats: list[Beat] = []
    visuals: list[BeatVisual] = []
    for idx, shot, scene in flat:
        measured = float(tts_data.get(str(idx), {}).get("duration_s") or shot.target_duration_s)
        shot.target_duration_s = max(1.0, measured)
        beats.append(_shot_to_beat(shot, idx, total))
        visuals.append(_shot_to_visual(shot, scene, script))

    # === Fase 2: render de shots pendientes (caro — de aquí el checkpoint) ===
    essence = _script_to_essence(script)
    cost_tracker: dict = dict(job.cost_breakdown)
    judge = None
    if lf.shot_accept_threshold > 0:
        from core.long_form.consistency import BestImageSelector

        judge = BestImageSelector()

    decision_log = None
    try:
        from core.visual.provider_scoring import DecisionLog

        decision_log = DecisionLog()
    except Exception as e:  # noqa: BLE001 — la auditoría es opcional
        logger.warning(f"[montage] DecisionLog no disponible ({e}); sigo sin audit trail")

    pending = ckpt.pending_beats(total)
    semaphore = asyncio.Semaphore(max(1, lf.parallel_shot_concurrency))
    lock = asyncio.Lock()

    async def _one(idx: int) -> None:
        async with semaphore:
            artifact, needs_rework = await _render_shot(
                beats[idx],
                visuals[idx],
                essence,
                shots_dir,
                aspect=aspect.value,
                mode=mode,
                strategy=strategy,
                cost_tracker=cost_tracker,
                decision_log=decision_log,
                judge=judge,
                max_rework=lf.shot_max_rework,
            )
        # Persistir inmediatamente: si el proceso muere en el próximo shot,
        # este ya no se vuelve a pagar. El lock protege el JSON compartido.
        async with lock:
            ckpt.mark_beat(
                idx,
                {
                    "video_path": str(artifact.video_path) if artifact.video_path else "",
                    "first_frame_path": (
                        str(artifact.first_frame_path) if artifact.first_frame_path else ""
                    ),
                    "source": artifact.source.value,
                    "duration_s": artifact.duration_s,
                    "needs_rework": needs_rework,
                },
            )
            store.save(ckpt)

    if pending:
        await asyncio.gather(*(_one(i) for i in pending))
    logger.info(f"[montage] {job.job_id}: {len(pending)} shots renderizados de {total}")

    # === Fase 3: reconstruir artifacts EN ORDEN desde el checkpoint ===
    artifacts: list[BeatArtifact] = []
    for idx in range(total):
        data = ckpt.beat_data(idx)
        video_path = data.get("video_path") or ""
        if not video_path or not Path(video_path).exists():
            job.status = "failed"
            job.error_message = f"shot#{idx} sin video_path válido tras el render"
            logger.error(f"[montage] {job.job_id}: {job.error_message}")
            return job
        artifacts.append(
            BeatArtifact(
                idx=idx,
                video_path=Path(video_path),
                first_frame_path=Path(data["first_frame_path"])
                if data.get("first_frame_path")
                else None,
                source=VideoSource(data.get("source", VideoSource.PEXELS.value)),
                duration_s=float(data.get("duration_s") or 0.0),
            )
        )

    reworks = [i for i in range(total) if ckpt.beat_data(i).get("needs_rework")]
    if reworks:
        logger.warning(
            f"[montage] {job.job_id}: {len(reworks)} shots entregados SIN aprobar "
            f"el veredicto: {reworks}"
        )

    # === Fase 4: narración completa + stitch single-pass ===
    from core.tts import concat_wavs

    narration = base_dir / "narration.wav"
    try:
        concat_wavs([Path(tts_data[str(i)]["wav"]) for i in range(total)], narration)
    except Exception as e:  # noqa: BLE001
        job.status = "failed"
        job.error_message = f"concat de narración falló: {e}"
        logger.error(f"[montage] {job.job_id}: {job.error_message}")
        return job

    from core.editor import stitch_video
    from core.editor.bgm import pick_bgm

    job.status = "stitching"
    try:
        final = await stitch_video(
            artifacts,
            narration,
            None,  # sin subtítulos quemados: el montage vive de la imagen
            base_dir,
            aspect,
            bgm_path=pick_bgm(bgm_type, config=cfg),
            out_name=f"{job.job_id}_montage.mp4",
            config=cfg,
        )
    except Exception as e:  # noqa: BLE001
        job.status = "failed"
        job.error_message = f"stitch: {e}"
        logger.error(f"[montage] {job.job_id}: {job.error_message}")
        return job

    job.status = "completed"
    job.final_video_path = str(final)
    job.actual_duration_s = sum(a.duration_s for a in artifacts)
    job.cost_breakdown = cost_tracker
    ckpt.mark_phase(PHASE_STITCH, {"final": str(final)})
    store.save(ckpt)

    if decision_log is not None:
        try:
            decision_log.save(base_dir / "visual_decisions.json")  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[montage] no pude persistir el audit trail: {e}")

    # Job cerrado: el checkpoint ya no protege nada y solo confundiría un
    # re-run futuro con el mismo job_id.
    store.clear(job.job_id)
    logger.info(f"[montage] {job.job_id} ✓ {final} ({job.actual_duration_s:.1f}s)")
    return job


__all__ = ["flatten_shots", "produce_montage"]
