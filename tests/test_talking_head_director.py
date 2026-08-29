"""Tests para core/long_form/talking_head_director.py.

`test_live_avatar.py` cubre los backends y el generator (25 tests) pero NO la
capa director. Ese hueco dejó pasar un bug real: `_synthesize_shot_audio`
importaba `core.tts.tts_synthesize`, que no existe, dentro de un
`try/except ImportError` — así que `produce_talking_head()` fallaba SIEMPRE en
el paso de TTS con un mensaje que culpaba a la instalación ("core.tts no
disponible") en vez de al import inventado.

Estos tests fijan el contrato de esa capa:
1. `_synthesize_shot_audio` habla con la API REAL de `core.tts`.
2. Nunca produce silencio: un talking-head mudo no es un entregable degradado,
   es uno roto, y `detect_engine_and_voice("")` devuelve ("silent", "").
3. `produce_talking_head` valida sus precondiciones antes de gastar GPU.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.long_form.talking_head_director import (
    _shot_to_beat,
    _shot_to_visual,
    _synthesize_shot_audio,
    produce_talking_head,
)
from core.long_form.types import LongFormPlanError
from shared.schemas import (
    AudioArtifact,
    BeatArtifact,
    LongFormIntent,
    LongFormJob,
    LongFormScript,
    MotionHint,
    NarrativeArc,
    Scene,
    Shot,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def script() -> LongFormScript:
    return LongFormScript(
        arc=NarrativeArc(
            title="Curso de tipografía",
            logline="Por qué la kerning arruina un logo",
            act1_setup="Qué es el kerning",
            act2_confrontation="Los tres errores clásicos",
            act3_resolution="Cómo revisarlo en dos minutos",
            themes=["diseño"],
            target_minutes=5.0,
        ),
        intent=LongFormIntent.TALKING_HEAD,
        scenes=[
            Scene(
                idx=0,
                title="Intro",
                setting="Estudio, fondo neutro",
                summary="El presentador plantea el problema",
                shots=[
                    Shot(
                        idx=0,
                        scene_idx=0,
                        visual_description="Plano medio del presentador",
                        dialogue="El kerning es el espacio entre dos letras.",
                        speaker="host",
                        target_duration_s=4.0,
                    ),
                    Shot(
                        idx=1,
                        scene_idx=0,
                        visual_description="Mismo plano, gesto de la mano",
                        dialogue="Y casi nadie lo revisa.",
                        speaker="host",
                        target_duration_s=3.0,
                    ),
                ],
            )
        ],
    )


@pytest.fixture
def job() -> LongFormJob:
    return LongFormJob(job_id="th001", source_kind="idea", intent=LongFormIntent.TALKING_HEAD)


@pytest.fixture
def portrait(tmp_path: Path) -> Path:
    p = tmp_path / "anchor.jpg"
    p.write_bytes(b"jpeg")
    return p


class _FakeEngine:
    """Engine con la firma REAL de `TTSEngine.synthesize`."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def synthesize(self, text, voice, out_dir, target_duration_s=None):  # noqa: ANN001
        self.calls.append({"text": text, "voice": voice, "out_dir": out_dir})
        out_dir.mkdir(parents=True, exist_ok=True)
        wav = out_dir / "full.wav"
        wav.write_bytes(b"RIFF")
        return AudioArtifact(path=wav, duration_s=2.5, word_timings=[], engine="edge")


# =============================================================================
# 1. El helper de TTS — el que estaba roto
# =============================================================================


class TestSynthesizeShotAudio:
    @pytest.mark.asyncio
    async def test_uses_the_real_core_tts_api(self, tmp_path: Path, monkeypatch) -> None:
        """Regresión: antes llamaba a `core.tts.tts_synthesize`, que no existe."""
        engine = _FakeEngine()
        monkeypatch.setattr("core.tts.get_engine", lambda name: engine)

        path, duration = await _synthesize_shot_audio(
            text="Hola mundo.",
            out_dir=tmp_path / "shot_000",
            voice="en-US-AvaNeural-Female",
        )
        assert path.exists()
        assert duration == 2.5
        # El voice canónico se normaliza antes de llegar al engine.
        assert engine.calls[0]["voice"] == "en-US-AvaNeural"

    @pytest.mark.asyncio
    async def test_canonical_voice_selects_the_engine(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        seen: list[str] = []

        def _get(name):  # noqa: ANN001
            seen.append(name)
            return _FakeEngine()

        monkeypatch.setattr("core.tts.get_engine", _get)
        await _synthesize_shot_audio(
            text="Hola.", out_dir=tmp_path / "a", voice="gemini:Zephyr-Female"
        )
        # `engine` ni se pasa: la voz canónica manda.
        assert seen == ["gemini_flash"]

    @pytest.mark.asyncio
    async def test_no_voice_refuses_instead_of_producing_a_mute_avatar(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(LongFormPlanError, match="voz real"):
            await _synthesize_shot_audio(
                text="Hola.", out_dir=tmp_path / "a", voice="no-voice"
            )

    @pytest.mark.asyncio
    async def test_empty_default_voice_refuses(self, tmp_path: Path, monkeypatch) -> None:
        """El caso por defecto sin config: antes habría caído en engine=silent."""
        from shared.config import load_config

        cfg = load_config()
        monkeypatch.setattr(cfg, "tts", {})  # sin `[tts.edge].default_voice`
        with pytest.raises(LongFormPlanError, match="voz real"):
            await _synthesize_shot_audio(text="Hola.", out_dir=tmp_path / "a")

    @pytest.mark.asyncio
    async def test_falls_back_to_configured_default_voice(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from shared.config import TTSEngineConfig, load_config

        cfg = load_config()
        engine = _FakeEngine()
        monkeypatch.setattr(
            cfg, "tts", {"edge": TTSEngineConfig(default_voice="es-MX-DaliaNeural")}
        )
        monkeypatch.setattr("core.tts.get_engine", lambda name: engine)

        await _synthesize_shot_audio(text="Hola.", out_dir=tmp_path / "a", engine="edge")
        assert engine.calls[0]["voice"] == "es-MX-DaliaNeural"


# =============================================================================
# 2. Mapeo Shot → Beat / BeatVisual
# =============================================================================


class TestShotMapping:
    def test_visual_routes_to_live_avatar(self, script: LongFormScript, tmp_path: Path) -> None:
        shot = script.scenes[0].shots[0]
        portrait = tmp_path / "p.jpg"
        audio = tmp_path / "a.wav"
        visual = _shot_to_visual(shot, portrait_path=portrait, audio_path=audio)
        # audio_path + reference_image_path son lo que el orchestrator usa para
        # enrutar a LiveAvatarGenerator en vez de al pipeline visual normal.
        assert visual.audio_path == audio
        assert visual.reference_image_path == portrait
        assert visual.motion_hint == MotionHint.STATIC

    def test_beat_prefers_dialogue_over_description(self, script: LongFormScript) -> None:
        beat = _shot_to_beat(script.scenes[0].shots[0], 0)
        assert beat.text.startswith("El kerning")


# =============================================================================
# 3. produce_talking_head: precondiciones y happy path
# =============================================================================


@pytest.fixture
def th_env(tmp_path: Path, monkeypatch):
    from shared.config import TTSEngineConfig, load_config

    cfg = load_config()
    monkeypatch.setattr(cfg.long_form, "enabled", True)
    monkeypatch.setattr(cfg.long_form, "working_dir", str(tmp_path))
    monkeypatch.setattr(cfg.visual.live_avatar, "enabled", True)
    monkeypatch.setattr(cfg, "tts", {"edge": TTSEngineConfig(default_voice="es-MX-DaliaNeural")})
    monkeypatch.setattr("core.tts.get_engine", lambda name: _FakeEngine())

    async def _fake_generate(beats, visuals, out_dir, **kwargs):  # noqa: ANN001
        out = []
        for beat in beats:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            clip = Path(out_dir) / f"beat_{beat.idx}.mp4"
            clip.write_bytes(b"mp4")
            out.append(BeatArtifact(idx=beat.idx, video_path=clip, duration_s=2.5))
        return out

    async def _fake_stitch(artifacts, audio_paths, final_path):  # noqa: ANN001
        Path(final_path).parent.mkdir(parents=True, exist_ok=True)
        Path(final_path).write_bytes(b"final")

    monkeypatch.setattr("core.visual.generation.generate_beat_videos", _fake_generate)
    monkeypatch.setattr("core.visual.generation.LiveAvatarGenerator", lambda cfg_: object())
    monkeypatch.setattr(
        "core.long_form.talking_head_director._stitch_artifacts", _fake_stitch
    )


class TestProduceTalkingHead:
    @pytest.mark.asyncio
    async def test_missing_portrait_raises_before_spending_gpu(
        self, job: LongFormJob, script: LongFormScript, th_env, tmp_path: Path
    ) -> None:
        with pytest.raises(LongFormPlanError, match="portrait_path no existe"):
            await produce_talking_head(
                job, script, portrait_path=tmp_path / "no-existe.jpg"
            )

    @pytest.mark.asyncio
    async def test_happy_path_completes(
        self, job: LongFormJob, script: LongFormScript, portrait: Path, th_env
    ) -> None:
        result = await produce_talking_head(job, script, portrait_path=portrait)
        assert result.status == "completed"
        assert Path(result.final_video_path).exists()

    @pytest.mark.asyncio
    async def test_shot_without_text_fails_the_job_not_the_process(
        self, job: LongFormJob, script: LongFormScript, portrait: Path, th_env
    ) -> None:
        script.scenes[0].shots[0].dialogue = None
        script.scenes[0].shots[0].visual_description = "   "
        result = await produce_talking_head(job, script, portrait_path=portrait)
        assert result.status == "failed"
        assert "TTS shot#0" in result.error_message

    @pytest.mark.asyncio
    async def test_no_voice_surfaces_as_a_failed_job(
        self, job: LongFormJob, script: LongFormScript, portrait: Path, th_env
    ) -> None:
        result = await produce_talking_head(
            job, script, portrait_path=portrait, tts_voice="no-voice"
        )
        assert result.status == "failed"
        assert "voz real" in result.error_message

    @pytest.mark.asyncio
    async def test_empty_script_raises(
        self, job: LongFormJob, script: LongFormScript, portrait: Path, th_env
    ) -> None:
        script.scenes[0].shots = []
        with pytest.raises(LongFormPlanError, match="nada que producir"):
            await produce_talking_head(job, script, portrait_path=portrait)

    @pytest.mark.asyncio
    async def test_requires_live_avatar_enabled(
        self, job: LongFormJob, script: LongFormScript, portrait: Path, th_env, monkeypatch
    ) -> None:
        from shared.config import load_config

        monkeypatch.setattr(load_config().visual.live_avatar, "enabled", False)
        with pytest.raises(LongFormPlanError, match="live_avatar"):
            await produce_talking_head(job, script, portrait_path=portrait)
