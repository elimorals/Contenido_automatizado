"""Producción de UN episodio de microdrama (ADR-038).

Cierra el hueco que dejó ADR-029: `produce_long_form` solo tenía director
concreto para MONTAGE y TALKING_HEAD; NARRATIVE era un stub.

Lo que distingue a un episodio de microdrama de un montage
-----------------------------------------------------------
1. **Los personajes tienen que ser la misma persona en el shot 1 y en el 10** —
   y en el episodio 47. Por eso el retrato NO se regenera por shot: se genera
   una vez por serie, vive en `editorial/series/<slug>/portraits/` y se reusa.
   Es la diferencia entre una serie y diez videos sueltos con el mismo nombre.
2. **Cada personaje habla con su voz.** `CharacterProfile.voice` mapea a un
   motor TTS distinto por hablante. Un melodrama donde Renata y Maximiliano
   suenan igual no se sostiene treinta segundos.
3. **El audio manda sobre la duración.** El shot dura lo que dura su línea de
   diálogo, medida — no lo que el planner estimó.

Deliberadamente **un episodio, no una serie**: hasta saber si la consistencia de
rostro aguanta diez shots seguidos, producir cien episodios es especular caro.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from loguru import logger

from core.checkpoint import Checkpoint, CheckpointStore, get_checkpoint_store
from core.cost import budgeted
from core.long_form.types import LongFormPlanError
from shared.config import load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    CharacterProfile,
    ConsistencyAnchor,
    Episode,
    MotionHint,
    Scene,
    SeriesBible,
    Shot,
    VideoAspect,
)

PHASE_PORTRAITS = "portraits"
PHASE_TTS = "tts"

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
# Retratos: el ancla de consistencia de la serie
# =============================================================================


def _portrait_prompt(character: CharacterProfile, bible: SeriesBible) -> str:
    """Retrato neutro y reusable, no una escena.

    Se pide expresión neutra y fondo simple a propósito: este frame va a viajar
    como referencia a decenas de shots distintos, así que cualquier cosa
    dramática que se meta aquí (una emoción, una luz concreta) contamina todos.
    """
    return (
        f"Portrait photograph of {character.name}: {character.appearance}. "
        f"Neutral expression, front view, plain uncluttered background, "
        f"even soft lighting. {bible.setting}. "
        "Photorealistic, cinematic, vertical 9:16 frame. No text."
    )


async def ensure_portraits(
    bible: SeriesBible,
    portraits_dir: Path,
    *,
    force: bool = False,
) -> dict[str, Path]:
    """Genera los retratos que falten y los persiste en la biblia.

    Idempotente: un personaje con `portrait_path` existente no se regenera. Es
    lo que hace que el episodio 47 use la misma cara que el 3.
    """
    from core.visual.generation.openai_image import OpenAIImageGenerator

    generator = OpenAIImageGenerator()
    portraits_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}

    for character in bible.characters:
        dest = portraits_dir / f"{_slug(character.name)}.jpg"
        if not force and character.portrait_path and Path(character.portrait_path).exists():
            out[character.name] = Path(character.portrait_path)
            continue
        if not force and dest.exists():
            character.portrait_path = str(dest)
            out[character.name] = dest
            continue

        beat = Beat(idx=0, role=BeatRole.HOOK, text=character.name,
                    target_duration_s=4.0, veo_duration=4)
        visual = BeatVisual(
            image_prompt=_portrait_prompt(character, bible),
            motion_hint=MotionHint.STATIC,
            visual_anchor=character.name,
        )
        try:
            artifact = await generator.generate(beat, visual, "general", portraits_dir)
        except Exception as e:  # noqa: BLE001 — un retrato fallido no tumba la serie
            logger.warning(f"[narrative] retrato de {character.name} falló: {e}")
            continue
        if artifact.first_frame_path is None:
            continue
        Path(artifact.first_frame_path).replace(dest)
        # El generador deja el PNG crudo junto al JPEG recortado; en un
        # directorio versionado como este, ese temporal acabaría en git.
        raw = portraits_dir / f"frame-{beat.idx:02d}-raw.png"
        raw.unlink(missing_ok=True)
        character.portrait_path = str(dest)
        out[character.name] = dest
        logger.info(f"[narrative] retrato ✓ {character.name} → {dest.name}")

    return out


def _slug(name: str) -> str:
    """Nombre de archivo del retrato. Reusa el slug del arquitecto.

    Tenía su propia versión que NO quitaba acentos, así que producía
    `lucía-fernanda-treviño.jpg` — un nombre que sobrevive en macOS pero da
    problemas al versionarlo y al moverlo entre sistemas. Las series ya
    generadas no se ven afectadas: `ensure_portraits` consulta primero el
    `portrait_path` guardado en la biblia, que apunta al archivo real.
    """
    from core.long_form.architect import slugify

    return slugify(name)


# =============================================================================
# Mapeo Shot → Beat / BeatVisual
# =============================================================================


def _veo_bucket(target_s: float) -> int:
    for bucket in (4, 6, 8):
        if bucket >= target_s + 0.3:
            return bucket
    return 8


def _shot_to_beat(shot: Shot, idx: int, total: int) -> Beat:
    duration = max(1.0, shot.target_duration_s)
    role = BeatRole.HOOK if idx == 0 else (
        BeatRole.PAYOFF if idx == total - 1 else BeatRole.MECHANISM
    )
    return Beat(
        idx=idx,
        role=role,
        text=(shot.dialogue or shot.visual_description)[:500],
        target_duration_s=duration,
        veo_duration=_veo_bucket(duration),  # type: ignore[arg-type]
    )


def _shot_prompt(shot: Shot, scene: Scene, bible: SeriesBible) -> str:
    """Prompt del shot con las descripciones de sus personajes y assets.

    El planner marca a los personajes como `<Renata Vargas>`; aquí se sustituye
    por su descripción física de la biblia. Sin esto el generador dibuja a una
    persona distinta cada vez que lee el nombre.
    """
    text = shot.visual_description
    parts = [text]

    for name in shot.characters_present or scene.characters_in_scene:
        character = bible.character(name)
        if character:
            parts.append(f"{name}: {character.appearance}")

    for asset in _assets_in(shot, scene, bible):
        parts.append(f"{asset.name}: {asset.description}")

    if scene.setting:
        parts.append(scene.setting)
    return ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip())


def _assets_in(shot: Shot, scene: Scene, bible: SeriesBible) -> list:
    """Assets de la biblia referidos por el shot o su escena.

    El match exacto por nombre no acertaba nunca (el LLM escribe "Contrato de
    herencia" para el asset "Testamento", o mete ambiente sonoro en
    `environment`): se busca el nombre del asset dentro de todo el texto.
    """
    haystack = " ".join([
        shot.visual_description, *shot.assets_present, scene.environment or "",
        *scene.props, scene.setting or "",
    ]).lower()
    return [a for a in bible.assets if a.description and a.name.lower() in haystack]


def _shot_to_visual(
    shot: Shot,
    scene: Scene,
    bible: SeriesBible,
    *,
    audio_path: Path | None = None,
    refs: list[Path] | None = None,
) -> BeatVisual:
    speaker = shot.speaker or (shot.characters_present or [""])[0]
    return BeatVisual(
        image_prompt=_shot_prompt(shot, scene, bible),
        motion_hint=_CAMERA_TO_MOTION.get(shot.camera_movement, MotionHint.SLOW_ZOOM_IN),
        visual_anchor=speaker or scene.title,
        audio_path=audio_path,
        character_refs=refs or [],
    )


def _on_screen_refs(shot: Shot, bible: SeriesBible) -> list[Path]:
    """Retratos de quienes ESTÁN en cuadro — no de quien habla.

    Antes viajaba solo el retrato del hablante y se usaba como frame literal:
    si hablaba alguien fuera de cuadro (una llamada), su cara aparecía en el
    plano, y los presentes que no hablaban iban sin referencia.
    """
    refs: list[Path] = []
    for name in shot.characters_present:
        c = bible.character(name)
        if c and c.portrait_path and Path(c.portrait_path).exists():
            refs.append(Path(c.portrait_path))
    return refs


# =============================================================================
# Voz por personaje
# =============================================================================


async def _tts_shot(
    shot: Shot,
    idx: int,
    audio_dir: Path,
    bible: SeriesBible,
    *,
    default_voice: str,
) -> tuple[Path, float]:
    """Sintetiza la línea del shot con la voz de QUIEN habla.

    Es lo que separa un melodrama de un audiolibro: `CharacterProfile.voice` da
    una voz distinta por personaje. Sin diálogo se usa el engine `silent`, para
    que el resto del pipeline tenga un solo camino.
    """
    from core.tts import detect_engine_and_voice, get_engine

    shot_dir = audio_dir / f"shot_{idx:03d}"
    line = (shot.dialogue or "").strip()

    if not line:
        artifact = await get_engine("silent").synthesize(
            text=shot.visual_description, voice="", out_dir=shot_dir,
            target_duration_s=max(1.0, shot.target_duration_s),
        )
        return Path(artifact.path), artifact.duration_s

    character = bible.character(shot.speaker or "")
    voice_name = (character.voice if character and character.voice else "") or default_voice
    engine_name, voice = detect_engine_and_voice(voice_name)
    artifact = await get_engine(engine_name).synthesize(
        text=line, voice=voice, out_dir=shot_dir, target_duration_s=None
    )
    return Path(artifact.path), artifact.duration_s


# =============================================================================
# Entry point
# =============================================================================


@budgeted
async def produce_episode(
    bible: SeriesBible,
    episode: Episode,
    *,
    out_dir: Path | None = None,
    default_voice: str = "es-MX-DaliaNeural-Female",
    aspect: VideoAspect | None = None,
    resume: bool | None = None,
) -> Episode:
    """Produce un episodio completo: retratos → TTS → shots → stitch.

    Reanudable como el montage (ADR-024): cada shot se persiste al terminar. Un
    episodio de 15 shots con Sora son ~20 min y unos pocos dólares; morir en el
    shot 12 y repetir los 11 anteriores es dinero tirado.
    """
    cfg = load_config()
    lf = cfg.long_form
    if not lf.enabled:
        raise LongFormPlanError("long_form deshabilitado (LONG_FORM_ENABLED=true)")

    flat: list[tuple[int, Shot, Scene]] = []
    for scene in episode.scenes:
        for shot in scene.shots:
            flat.append((len(flat), shot, scene))
    if not flat:
        raise LongFormPlanError(f"episodio {episode.number} sin shots")

    base = out_dir or (Path(lf.working_dir) / "series" / bible.slug / f"ep{episode.number:03d}")
    audio_dir, shots_dir = base / "audio", base / "shots"
    for d in (base, audio_dir, shots_dir):
        d.mkdir(parents=True, exist_ok=True)

    resume = lf.resume_enabled if resume is None else resume
    job_key = f"{bible.slug}-ep{episode.number:03d}"
    store: CheckpointStore = get_checkpoint_store()
    ckpt: Checkpoint = store.load(job_key) if resume else Checkpoint(task_id=job_key)

    total = len(flat)
    t0 = time.monotonic()
    logger.info(
        f"[narrative] {bible.slug} E{episode.number}: {total} shots, "
        f"{len(bible.characters)} personajes, resume={resume}"
    )

    # === Retratos (una vez por serie, no por episodio) ===
    from core.long_form.series import portraits_dir as series_portraits
    from core.long_form.series import save_bible

    portraits = await ensure_portraits(bible, series_portraits(bible.slug))
    if portraits:
        save_bible(bible)  # persistir los portrait_path recién generados
    ckpt.mark_phase(PHASE_PORTRAITS, {n: str(p) for n, p in portraits.items()})
    store.save(ckpt)

    # === TTS por shot, con la voz de cada personaje ===
    if ckpt.is_phase_done(PHASE_TTS):
        tts_data = ckpt.phase_data(PHASE_TTS)
    else:
        results = await asyncio.gather(
            *(
                _tts_shot(shot, idx, audio_dir, bible, default_voice=default_voice)
                for idx, shot, _ in flat
            ),
            return_exceptions=True,
        )
        tts_data = {}
        for (idx, _s, _sc), result in zip(flat, results, strict=True):
            if isinstance(result, Exception):
                raise LongFormPlanError(f"TTS shot#{idx}: {result}") from result
            wav, duration = result
            tts_data[str(idx)] = {"wav": str(wav), "duration_s": duration}
        ckpt.mark_phase(PHASE_TTS, tts_data)
        store.save(ckpt)

    # La duración medida manda sobre la planificada (mismo principio que ADR-001).
    beats: list[Beat] = []
    visuals: list[BeatVisual] = []
    for idx, shot, scene in flat:
        shot.target_duration_s = max(
            1.0, float(tts_data.get(str(idx), {}).get("duration_s") or shot.target_duration_s)
        )
        beats.append(_shot_to_beat(shot, idx, total))
        visuals.append(_shot_to_visual(shot, scene, bible, refs=_on_screen_refs(shot, bible)))

    # === Shots pendientes ===
    from core.visual.generation import generate_beat_videos

    pending = ckpt.pending_beats(total)
    if pending:
        sub_beats = [beats[i] for i in pending]
        sub_visuals = [visuals[i] for i in pending]
        artifacts = await generate_beat_videos(
            sub_beats, sub_visuals, shots_dir, content_mode="general"
        )
        for beat, artifact in zip(sub_beats, artifacts, strict=True):
            ckpt.mark_beat(beat.idx, {
                "video_path": str(artifact.video_path) if artifact.video_path else "",
                "first_frame_path": (
                    str(artifact.first_frame_path) if artifact.first_frame_path else ""
                ),
                "source": artifact.source.value,
                "duration_s": artifact.duration_s,
            })
            store.save(ckpt)

    # === Reconstruir en orden + stitch ===
    from shared.schemas import VideoSource

    ordered: list[BeatArtifact] = []
    for idx in range(total):
        data = ckpt.beat_data(idx)
        path = data.get("video_path") or ""
        if not path or not Path(path).exists():
            raise LongFormPlanError(f"shot#{idx} sin clip tras el render")
        ordered.append(BeatArtifact(
            idx=idx, video_path=Path(path),
            first_frame_path=Path(data["first_frame_path"]) if data.get("first_frame_path") else None,
            source=VideoSource(data.get("source", "local")),
            duration_s=float(data.get("duration_s") or 0.0),
        ))

    from core.editor import stitch_video
    from core.tts import concat_wavs

    narration = base / "narration.wav"
    concat_wavs([Path(tts_data[str(i)]["wav"]) for i in range(total)], narration)

    stitched = await stitch_video(
        ordered, narration, None, base,
        aspect or bible.aspect_ratio,
        out_name=f"{bible.slug}-ep{episode.number:03d}.mp4",
        config=cfg,
    )

    # El entregable va a `output/`, no a `storage/`. `storage/` es para
    # artefactos REGENERABLES —cachés, chunks, RAG—; un episodio cuesta minutos
    # de cómputo y dinero real, y es lo único que el operador va a querer abrir.
    # Enterrarlo entre los intermedios lo hacía invisible.
    final = deliver_episode(stitched, bible, episode)

    episode.video_path = str(final)
    episode.job_id = job_key
    store.clear(job_key)
    logger.info(
        f"[narrative] {bible.slug} E{episode.number} ✓ {final} "
        f"({time.monotonic() - t0:.0f}s)"
    )
    return episode


def episode_output_dir(slug: str) -> Path:
    """`output/series/<slug>/` — donde el operador busca sus vídeos."""
    d = Path("output") / "series" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def deliver_episode(stitched: Path, bible: SeriesBible, episode: Episode) -> Path:
    """Copia el episodio terminado a `output/` con un nombre legible.

    Se **copia** en vez de moverse: el original sigue en el directorio de
    trabajo junto a sus shots y su audio, que es donde hay que mirar cuando algo
    salió mal. Si el operador borra `output/`, el episodio se recupera sin
    volver a generarlo.

    El nombre lleva número y título — `E001-el-paciente-de-la-302.mp4` — porque
    `vargas-ep001.mp4` no dice qué pasa dentro, y una serie son decenas.
    """
    import shutil

    from core.long_form.architect import slugify

    dest = episode_output_dir(bible.slug) / (
        f"E{episode.number:03d}-{slugify(episode.title)}.mp4"
    )
    shutil.copy2(stitched, dest)
    return dest


def anchors_from(artifacts: list[BeatArtifact], episode: Episode) -> list[ConsistencyAnchor]:
    """Anchors a partir de los frames producidos, para encadenar shots.

    Los consume `ReferenceImageSelector`. Se expone aparte porque el encadenado
    fino (elegir qué frames previos mostrar) es trabajo de la siguiente
    iteración: aquí la consistencia la sostiene el retrato fijo.
    """
    out: list[ConsistencyAnchor] = []
    for artifact in artifacts:
        if artifact.first_frame_path is None:
            continue
        out.append(ConsistencyAnchor(
            shot_idx=artifact.idx, scene_idx=0,
            frame_path=str(artifact.first_frame_path),
            description=f"E{episode.number} shot {artifact.idx}",
            created_at_unix=time.time(),
        ))
    return out


__all__ = ["ensure_portraits", "produce_episode"]
