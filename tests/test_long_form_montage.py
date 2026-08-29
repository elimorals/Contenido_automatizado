"""Tests para core/long_form/montage_director.py + el veredicto por shot (ADR-029).

Cubre:
1. Mapeo Shot → Beat / BeatVisual (buckets, roles, camera→motion)
2. `_enrich_prompt`: los assets anclados viajan al prompt (consistency)
3. `_script_to_essence`: proyección arco → Essence válida para el selector
4. `ShotVerdict` / `BestImageSelector.judge`: umbral, fallbacks permisivos
5. `produce_montage` E2E con todo mockeado — y el caso que justifica el
   módulo entero: **reanudar salta lo ya renderizado**.

Nada de esto llama a un LLM, a ffmpeg ni a un proveedor de stock.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.checkpoint import CheckpointStore
from core.long_form.consistency import BestImageSelector, ShotVerdict, _BestImageResponse
from core.long_form.montage_director import (
    _enrich_prompt,
    _role_for,
    _script_to_essence,
    _shot_to_beat,
    _shot_to_visual,
    _veo_bucket,
    flatten_shots,
    produce_montage,
)
from shared.schemas import (
    AssetProfile,
    AudioArtifact,
    BeatArtifact,
    BeatRole,
    LongFormIntent,
    LongFormJob,
    LongFormScript,
    MotionHint,
    NarrativeArc,
    Scene,
    Shot,
    VideoSource,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def arc() -> NarrativeArc:
    return NarrativeArc(
        title="Cierre",
        logline="Un café de barrio echa la persiana tras cuarenta años",
        act1_setup="Las mañanas de siempre: el vapor, los habituales, la rutina",
        act2_confrontation="Suben los costes, bajan los clientes, la duda se instala",
        act3_resolution="La última taza servida, las sillas boca arriba, la llave girando",
        themes=["memoria", "barrio", "tiempo"],
        target_minutes=6.0,
    )


@pytest.fixture
def assets() -> list[AssetProfile]:
    return [
        AssetProfile(
            name="Café Luna",
            kind="environment",
            description="local estrecho, azulejo verde, luz de neón azul en la ventana",
        ),
        AssetProfile(
            name="cafetera",
            kind="prop",
            description="máquina italiana de latón, abollada en la base",
        ),
    ]


@pytest.fixture
def script(arc: NarrativeArc, assets: list[AssetProfile]) -> LongFormScript:
    scenes = [
        Scene(
            idx=0,
            title="Apertura",
            setting="Amanecer, calle vacía",
            summary="El local abre por última vez",
            environment="Café Luna",
            props=["cafetera"],
            shots=[
                Shot(
                    idx=0,
                    scene_idx=0,
                    visual_description="La persiana sube y deja entrar la luz gris",
                    camera_movement="dolly_in",
                    target_duration_s=4.0,
                    assets_present=["cafetera"],
                ),
                Shot(
                    idx=1,
                    scene_idx=0,
                    visual_description="Vapor saliendo de una taza sobre la barra",
                    camera_movement="static",
                    target_duration_s=3.0,
                ),
            ],
        ),
        Scene(
            idx=1,
            title="Cierre",
            setting="Noche, la calle otra vez vacía",
            summary="Las luces se apagan",
            environment="Café Luna",
            shots=[
                Shot(
                    idx=0,
                    scene_idx=1,
                    visual_description="Una mano gira la llave en la cerradura",
                    camera_movement="pan_left",
                    target_duration_s=5.0,
                ),
            ],
        ),
    ]
    return LongFormScript(
        arc=arc,
        intent=LongFormIntent.MONTAGE,
        characters=[],
        assets=assets,
        scenes=scenes,
        source_kind="idea",
    )


@pytest.fixture
def job() -> LongFormJob:
    return LongFormJob(job_id="mtg001", source_kind="idea", intent=LongFormIntent.MONTAGE)


# =============================================================================
# 1. Mapeo Shot → Beat / BeatVisual
# =============================================================================


class TestShotMapping:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [(1.0, 4), (3.0, 4), (3.8, 6), (5.0, 6), (5.8, 8), (12.0, 8)],
    )
    def test_veo_bucket_is_smallest_that_covers_target(
        self, target: float, expected: int
    ) -> None:
        assert _veo_bucket(target) == expected

    def test_roles_bookend_the_sequence(self) -> None:
        assert _role_for(0, 5) == BeatRole.HOOK
        assert _role_for(4, 5) == BeatRole.PAYOFF
        assert _role_for(2, 5) == BeatRole.MECHANISM

    def test_single_shot_is_hook_not_payoff(self) -> None:
        # idx==0 gana sobre idx==total-1 cuando ambos aplican.
        assert _role_for(0, 1) == BeatRole.HOOK

    def test_flatten_assigns_global_index(self, script: LongFormScript) -> None:
        flat = flatten_shots(script)
        assert [i for i, _, _ in flat] == [0, 1, 2]
        # El índice global ignora el `Shot.idx` local a la escena.
        assert flat[2][1].visual_description.startswith("Una mano gira")
        assert flat[2][2].title == "Cierre"

    def test_beat_text_prefers_dialogue(self) -> None:
        shot = Shot(
            idx=0,
            scene_idx=0,
            visual_description="Un plano largo del pasillo",
            dialogue="No queda nadie.",
            target_duration_s=3.0,
        )
        assert _shot_to_beat(shot, 0, 3).text == "No queda nadie."

    def test_camera_movement_maps_to_motion_hint(self, script: LongFormScript) -> None:
        flat = flatten_shots(script)
        hints = [_shot_to_visual(s, sc, script).motion_hint for _, s, sc in flat]
        assert hints == [MotionHint.SLOW_ZOOM_IN, MotionHint.STATIC, MotionHint.PAN_LEFT]

    def test_unknown_camera_movement_falls_back(self, script: LongFormScript) -> None:
        shot = script.scenes[0].shots[0]
        shot.camera_movement = "corkscrew_barrel_roll"
        visual = _shot_to_visual(shot, script.scenes[0], script)
        assert visual.motion_hint == MotionHint.SLOW_ZOOM_IN


# =============================================================================
# 2. Assets en el prompt — el punto de "unified style consistency"
# =============================================================================


class TestAssetGrounding:
    def test_shot_asset_description_reaches_the_prompt(
        self, script: LongFormScript
    ) -> None:
        scene = script.scenes[0]
        prompt = _enrich_prompt(scene.shots[0], scene, script)
        assert "máquina italiana de latón" in prompt

    def test_scene_environment_applies_to_every_shot(
        self, script: LongFormScript
    ) -> None:
        scene = script.scenes[0]
        # El shot 1 NO declara assets_present, pero hereda el entorno de la escena.
        prompt = _enrich_prompt(scene.shots[1], scene, script)
        assert "azulejo verde" in prompt

    def test_same_environment_yields_same_anchor_text_across_scenes(
        self, script: LongFormScript
    ) -> None:
        # Esto es lo que evita que el café se reinvente entre la escena 0 y la 1.
        first = _enrich_prompt(script.scenes[0].shots[0], script.scenes[0], script)
        last = _enrich_prompt(script.scenes[1].shots[0], script.scenes[1], script)
        assert "azulejo verde" in first
        assert "azulejo verde" in last

    def test_unknown_asset_name_is_ignored_not_fatal(
        self, script: LongFormScript
    ) -> None:
        scene = script.scenes[0]
        scene.shots[0].assets_present = ["no-existe"]
        prompt = _enrich_prompt(scene.shots[0], scene, script)
        assert "no-existe" not in prompt
        assert prompt  # sigue habiendo prompt

    def test_assets_by_name_index(self, script: LongFormScript) -> None:
        index = script.assets_by_name()
        assert set(index) == {"Café Luna", "cafetera"}
        assert index["cafetera"].kind == "prop"


# =============================================================================
# 3. Essence sintética
# =============================================================================


class TestEssenceProjection:
    def test_projects_arc_into_valid_essence(self, script: LongFormScript) -> None:
        essence = _script_to_essence(script)
        assert essence.core_claim == script.arc.logline
        assert essence.evidence == ["memoria", "barrio", "tiempo"]
        assert essence.domain == "memoria"

    def test_falls_back_to_logline_when_no_themes(self, script: LongFormScript) -> None:
        script.arc.themes = []
        essence = _script_to_essence(script)
        assert essence.evidence == [script.arc.logline[:200]]
        assert essence.domain == "montage"


# =============================================================================
# 4. Veredicto por shot
# =============================================================================


class TestShotVerdict:
    def test_scored_flag(self) -> None:
        assert ShotVerdict(Path("a.jpg"), "x", score=7.0).scored is True
        assert ShotVerdict(Path("a.jpg"), "x").scored is False

    def test_defaults_are_permissive(self) -> None:
        # Un VLM que ignore las claves nuevas se comporta como antes de existir.
        parsed = _BestImageResponse.model_validate_json('{"best_image_index": 0}')
        assert parsed.acceptable is True
        assert parsed.score == -1.0

    @pytest.mark.asyncio
    async def test_single_candidate_short_circuits_without_threshold(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "c.jpg"
        p.write_bytes(b"x")
        selector = BestImageSelector()
        selector.accept_threshold = 0.0
        verdict = await selector.judge([p], [], "target")
        assert verdict.acceptable is True
        assert "single" in verdict.reason

    @pytest.mark.asyncio
    async def test_vlm_failure_never_rejects(self, tmp_path: Path, monkeypatch) -> None:
        # Un gate caído no debe convertirse en una fuente nueva de rework.
        paths = []
        for name in ("a.jpg", "b.jpg"):
            p = tmp_path / name
            p.write_bytes(b"x")
            paths.append(p)
        monkeypatch.setattr(
            "core.long_form.consistency.get_provider",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("502 upstream")),
        )
        selector = BestImageSelector()
        verdict = await selector.judge(paths, [], "target")
        assert verdict.acceptable is True
        assert verdict.path == paths[0]

    @pytest.mark.asyncio
    async def test_score_below_threshold_rejects(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        paths = []
        for name in ("a.jpg", "b.jpg"):
            p = tmp_path / name
            p.write_bytes(b"x")
            paths.append(p)

        from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider

        # Instancia real sin __init__ (necesita credenciales): `judge` hace un
        # isinstance() contra esta clase, así que un duck-type no sirve.
        fake = object.__new__(OpenAICompatibleProvider)
        fake.model_name = "fake"
        fake._post = AsyncMock(return_value={})
        fake._extract_text = lambda data: json.dumps(  # noqa: ARG005
            {"best_image_index": 1, "score": 3.0, "acceptable": True, "reason": "flojo"}
        )
        monkeypatch.setattr(
            "core.long_form.consistency.get_provider", lambda *a, **k: fake
        )
        selector = BestImageSelector()
        selector.accept_threshold = 6.0
        verdict = await selector.judge(paths, [], "target")
        assert verdict.acceptable is False  # el VLM dijo acceptable, el umbral no
        assert verdict.score == 3.0
        assert verdict.path == paths[1]


# =============================================================================
# 5. produce_montage E2E (mockeado) + reanudación
# =============================================================================


@pytest.fixture
def montage_env(tmp_path: Path, monkeypatch):
    """Aísla config + mockea TTS, selector, concat y stitch. Devuelve el espía."""
    from shared.config import load_config

    cfg = load_config()
    monkeypatch.setattr(cfg.long_form, "enabled", True)
    monkeypatch.setattr(cfg.long_form, "working_dir", str(tmp_path))
    monkeypatch.setattr(cfg.long_form, "shot_accept_threshold", 0.0)
    monkeypatch.setattr(cfg.long_form, "parallel_shot_concurrency", 2)

    rendered: list[int] = []

    class _FakeEngine:
        name = "silent"

        async def synthesize(self, text, voice, out_dir, target_duration_s=None):  # noqa: ANN001
            out_dir.mkdir(parents=True, exist_ok=True)
            wav = out_dir / "full.wav"
            wav.write_bytes(b"RIFF")
            return AudioArtifact(
                path=wav,
                duration_s=target_duration_s or 3.0,
                word_timings=[],
                engine="silent",
            )

    async def _fake_select(*, beat, out_dir, **kwargs):  # noqa: ANN001
        rendered.append(beat.idx)
        out_dir.mkdir(parents=True, exist_ok=True)
        clip = out_dir / f"beat_{beat.idx}.mp4"
        clip.write_bytes(b"mp4")
        return BeatArtifact(
            idx=beat.idx,
            video_path=clip,
            source=VideoSource.PEXELS,
            duration_s=beat.target_duration_s,
        )

    async def _fake_stitch(artifacts, audio, subs, out_dir, aspect, **kwargs):  # noqa: ANN001
        final = Path(out_dir) / kwargs.get("out_name", "out.mp4")
        final.write_bytes(b"final")
        return final

    monkeypatch.setattr("core.tts.get_engine", lambda name: _FakeEngine())
    monkeypatch.setattr("core.tts.detect_engine_and_voice", lambda v: ("silent", ""))
    monkeypatch.setattr("core.tts.concat_wavs", lambda paths, out: out.write_bytes(b"RIFF"))
    monkeypatch.setattr(
        "core.visual.selector.select_and_generate_beat_artifact", _fake_select
    )
    monkeypatch.setattr("core.editor.stitch_video", _fake_stitch)
    monkeypatch.setattr("core.editor.bgm.pick_bgm", lambda *a, **k: None)
    return rendered


class TestProduceMontage:
    @pytest.mark.asyncio
    async def test_end_to_end_renders_every_shot(
        self, job: LongFormJob, script: LongFormScript, montage_env
    ) -> None:
        result = await produce_montage(job, script)
        assert result.status == "completed"
        assert sorted(montage_env) == [0, 1, 2]
        assert Path(result.final_video_path).exists()

    @pytest.mark.asyncio
    async def test_resume_skips_already_rendered_shots(
        self, job: LongFormJob, script: LongFormScript, montage_env, tmp_path: Path
    ) -> None:
        """El caso que justifica el módulo: morir a mitad y no volver a pagar."""
        # Primera pasada completa.
        await produce_montage(job, script)
        first_pass = list(montage_env)
        assert sorted(first_pass) == [0, 1, 2]

        # produce_montage limpia el checkpoint al terminar; lo re-sembramos como
        # si el proceso hubiese muerto tras el shot 0 y el 1.
        store = CheckpointStore(tmp_path / "checkpoints")
        ckpt = store.load(job.job_id)
        clips_dir = tmp_path / job.job_id / "montage" / "shots"
        for idx in (0, 1):
            ckpt.mark_beat(
                idx,
                {
                    "video_path": str(clips_dir / f"beat_{idx}.mp4"),
                    "first_frame_path": "",
                    "source": "pexels",
                    "duration_s": 3.0,
                    "needs_rework": False,
                },
            )
        ckpt.mark_phase(
            "tts",
            {
                str(i): {
                    "wav": str(
                        tmp_path / job.job_id / "montage" / "audio" / f"shot_{i:03d}" / "full.wav"
                    ),
                    "duration_s": 3.0,
                }
                for i in range(3)
            },
        )
        store.save(ckpt)

        montage_env.clear()
        result = await produce_montage(job, script)
        assert result.status == "completed"
        # Solo el shot 2 se re-renderiza: los otros dos salen del checkpoint.
        assert montage_env == [2]

    @pytest.mark.asyncio
    async def test_resume_false_rerenders_everything(
        self, job: LongFormJob, script: LongFormScript, montage_env, tmp_path: Path
    ) -> None:
        store = CheckpointStore(tmp_path / "checkpoints")
        ckpt = store.load(job.job_id)
        ckpt.mark_beat(0, {"video_path": "/no/existe.mp4", "source": "pexels"})
        store.save(ckpt)

        await produce_montage(job, script, resume=False)
        assert sorted(montage_env) == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_checkpoint_cleared_after_success(
        self, job: LongFormJob, script: LongFormScript, montage_env, tmp_path: Path
    ) -> None:
        await produce_montage(job, script)
        assert CheckpointStore(tmp_path / "checkpoints").exists(job.job_id) is False

    @pytest.mark.asyncio
    async def test_empty_script_raises(self, job: LongFormJob, script: LongFormScript, montage_env) -> None:
        from core.long_form.types import LongFormPlanError

        for scene in script.scenes:
            scene.shots = []
        with pytest.raises(LongFormPlanError, match="sin shots"):
            await produce_montage(job, script)

    @pytest.mark.asyncio
    async def test_measured_audio_duration_overrides_plan(
        self, job: LongFormJob, script: LongFormScript, montage_env, monkeypatch
    ) -> None:
        """El audio medido manda: así el video no acumula drift shot a shot."""
        captured: list[float] = []

        async def _spy(*, beat, out_dir, **kwargs):  # noqa: ANN001
            captured.append(beat.target_duration_s)
            out_dir.mkdir(parents=True, exist_ok=True)
            clip = out_dir / f"beat_{beat.idx}.mp4"
            clip.write_bytes(b"mp4")
            return BeatArtifact(idx=beat.idx, video_path=clip, duration_s=9.0)

        class _LongEngine:
            async def synthesize(self, text, voice, out_dir, target_duration_s=None):  # noqa: ANN001
                out_dir.mkdir(parents=True, exist_ok=True)
                wav = out_dir / "full.wav"
                wav.write_bytes(b"RIFF")
                return AudioArtifact(
                    path=wav, duration_s=9.0, word_timings=[], engine="silent"
                )

        monkeypatch.setattr("core.tts.get_engine", lambda name: _LongEngine())
        monkeypatch.setattr(
            "core.visual.selector.select_and_generate_beat_artifact", _spy
        )
        await produce_montage(job, script)
        # Planificados 4.0/3.0/5.0 — el audio dijo 9.0 para los tres.
        assert captured == [9.0, 9.0, 9.0]

    @pytest.mark.asyncio
    async def test_missing_clip_after_render_fails_loudly(
        self, job: LongFormJob, script: LongFormScript, montage_env, monkeypatch
    ) -> None:
        async def _no_video(*, beat, out_dir, **kwargs):  # noqa: ANN001
            return BeatArtifact(idx=beat.idx, video_path=None)

        monkeypatch.setattr(
            "core.visual.selector.select_and_generate_beat_artifact", _no_video
        )
        result = await produce_montage(job, script)
        assert result.status == "failed"
        assert "sin video_path" in result.error_message


# =============================================================================
# 6. Routing del director
# =============================================================================


class TestDirectorRouting:
    @pytest.mark.asyncio
    async def test_montage_intent_delegates_to_montage_director(
        self, job: LongFormJob, script: LongFormScript, monkeypatch, tmp_path: Path
    ) -> None:
        from shared.config import load_config

        cfg = load_config()
        monkeypatch.setattr(cfg.long_form, "enabled", True)
        monkeypatch.setattr(cfg.long_form, "working_dir", str(tmp_path))

        spy = AsyncMock(return_value=job)
        monkeypatch.setattr("core.long_form.montage_director.produce_montage", spy)

        from core.long_form.director import produce_long_form

        job.intent = LongFormIntent.MONTAGE
        await produce_long_form(job, script)
        spy.assert_awaited_once()
