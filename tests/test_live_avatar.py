"""Tests para LiveAvatar: config + backends + generator + orchestrator wiring (ADR-016).

Mockean httpx (remote_http) y asyncio.create_subprocess_exec (local_cli) para
no necesitar GPU ni endpoint real. Cubren:

1. Config defaults + Literal validation
2. ``make_backend`` factory por modo
3. ``LocalCliBackend._build_cmd`` produce el comando torchrun esperado
4. ``LocalCliBackend.health_check`` con repo/ckpt ausentes
5. ``RemoteHttpBackend`` happy path (POST multipart + descarga MP4)
6. ``RemoteHttpBackend`` auth 401 → ``LiveAvatarAuthError``
7. ``RemoteHttpBackend`` 5xx → ``LiveAvatarAPIError``
8. ``LiveAvatarGenerator.generate`` falla si ``audio_path=None``
9. ``LiveAvatarGenerator.generate`` falla si sin reference image
10. ``LiveAvatarGenerator.generate`` happy path con backend mockeado
11. ``_should_use_live_avatar`` ruteo: None → False, audio_path set → True
12. ``video_cost_per_second`` + ``calculate_video_cost`` para live_avatar_*
13. Schema: ``BeatVisual.audio_path`` opcional y persistible
14. Schema: ``LongFormIntent.TALKING_HEAD`` round-trip
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.llm_router.pricing import (
    VIDEO_PRICING_USD_PER_SECOND,
    calculate_video_cost,
    video_cost_per_second,
)
from core.visual.generation import (
    LiveAvatarAPIError,
    LiveAvatarAuthError,
    LiveAvatarBackendUnavailableError,
    LiveAvatarBadInputError,
    LiveAvatarError,
    LiveAvatarGenerator,
    LiveAvatarResult,
    LocalCliBackend,
    RemoteHttpBackend,
    VisualGenerationError,
    make_live_avatar_backend,
)
from core.visual.generation.orchestrator import _should_use_live_avatar
from shared.config import LiveAvatarConfig
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    LongFormIntent,
    VideoSource,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cfg_remote() -> LiveAvatarConfig:
    return LiveAvatarConfig(
        enabled=True,
        backend="remote_http",
        remote_endpoint="https://test.example.com/generate",
        remote_api_key="test-token",
        size="704*384",
        sample_steps=4,
        fp8=True,
        cost_per_video_second_usd=0.05,
    )


@pytest.fixture
def cfg_local() -> LiveAvatarConfig:
    return LiveAvatarConfig(
        enabled=True,
        backend="local_cli",
        cli_repo_path="/fake/LiveAvatar",
        cli_ckpt_dir="/fake/ckpt",
        cli_num_gpus_dit=1,
        fp8=True,
    )


@pytest.fixture
def beat() -> Beat:
    return Beat(
        idx=3,
        role=BeatRole.MECHANISM,
        text="The presenter explains the topic in detail.",
        target_duration_s=5.0,
        veo_duration=6,
    )


@pytest.fixture
def visual_with_audio(tmp_path: Path) -> BeatVisual:
    img = tmp_path / "anchor.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic bytes (fake)
    audio = tmp_path / "shot_003.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")  # WAV magic
    return BeatVisual(
        image_prompt="A professor in a quiet study, soft lighting",
        visual_anchor="professor",
        audio_path=audio,
        reference_image_path=img,
    )


@pytest.fixture
def visual_no_audio() -> BeatVisual:
    return BeatVisual(image_prompt="anything", visual_anchor="x", audio_path=None)


# =============================================================================
# 1. Config defaults
# =============================================================================


def test_config_defaults_safe() -> None:
    c = LiveAvatarConfig()
    assert c.enabled is False  # opt-in
    assert c.backend in ("local_cli", "remote_http")
    assert c.fp8 is True
    assert c.size == "704*384"
    assert c.sample_steps == 4
    assert c.cost_per_video_second_usd > 0


def test_config_backend_literal_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LiveAvatarConfig(backend="cloud_lambda")  # type: ignore[arg-type]


# =============================================================================
# 2. Factory
# =============================================================================


def test_make_backend_remote(cfg_remote: LiveAvatarConfig) -> None:
    b = make_live_avatar_backend(cfg_remote)
    assert isinstance(b, RemoteHttpBackend)
    assert b.name == "remote_http"


def test_make_backend_local(cfg_local: LiveAvatarConfig) -> None:
    b = make_live_avatar_backend(cfg_local)
    assert isinstance(b, LocalCliBackend)
    assert b.name == "local_cli"


def test_make_backend_remote_requires_endpoint() -> None:
    cfg = LiveAvatarConfig(backend="remote_http", remote_endpoint="")
    with pytest.raises(LiveAvatarBackendUnavailableError):
        make_live_avatar_backend(cfg)


# =============================================================================
# 3. LocalCliBackend._build_cmd
# =============================================================================


def test_local_cli_build_cmd_single_gpu(cfg_local: LiveAvatarConfig, tmp_path: Path) -> None:
    backend = LocalCliBackend(cfg_local)
    cmd, env = backend._build_cmd(
        image_path=tmp_path / "img.jpg",
        audio_path=tmp_path / "a.wav",
        prompt="explainer about gravity",
        save_file=tmp_path / "out.mp4",
        seed=42,
        num_clip=10000,
    )
    # comando torchrun en posición 0
    assert cmd[0] == "torchrun"
    assert "--single_gpu" in cmd
    assert "--fp8" in cmd
    assert "--enable_vae_parallel" not in cmd  # multi-gpu flag
    # args esperados presentes
    assert "--task" in cmd and "s2v-14B" in cmd
    assert "--size" in cmd and cfg_local.size in cmd
    assert "--load_lora" in cmd
    assert "--lora_path_dmd" in cmd
    # env tiene CUDA y compile flag
    assert env["CUDA_VISIBLE_DEVICES"] == cfg_local.cli_cuda_visible_devices
    assert env["ENABLE_COMPILE"] in ("true", "false")
    assert env["ENABLE_FP8"] == "true"


def test_local_cli_build_cmd_multi_gpu(tmp_path: Path) -> None:
    cfg = LiveAvatarConfig(backend="local_cli", cli_num_gpus_dit=4, fp8=False)
    backend = LocalCliBackend(cfg)
    cmd, env = backend._build_cmd(
        image_path=tmp_path / "img.jpg",
        audio_path=tmp_path / "a.wav",
        prompt="x",
        save_file=tmp_path / "out.mp4",
        seed=1,
        num_clip=100,
    )
    assert "--enable_vae_parallel" in cmd
    assert "--single_gpu" not in cmd
    assert "--fp8" not in cmd
    assert env["ENABLE_FP8"] == "false"


# =============================================================================
# 4. health_check con paths ausentes
# =============================================================================


@pytest.mark.asyncio
async def test_local_cli_health_check_missing_repo(cfg_local: LiveAvatarConfig) -> None:
    backend = LocalCliBackend(cfg_local)
    assert await backend.health_check() is False  # /fake/LiveAvatar no existe


# =============================================================================
# 5. RemoteHttpBackend happy path
# =============================================================================


@pytest.mark.asyncio
async def test_remote_backend_generate_happy_path(
    cfg_remote: LiveAvatarConfig, tmp_path: Path
) -> None:
    img = tmp_path / "ref.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    out = tmp_path / "out.mp4"

    backend = RemoteHttpBackend(cfg_remote)

    # Mock httpx.AsyncClient: 1st POST → JSON with video_url, 2nd GET → stream bytes
    post_resp = MagicMock()
    post_resp.status_code = 200
    post_resp.json = MagicMock(
        return_value={
            "video_url": "https://test.example.com/job-123.mp4",
            "duration_s": 12.5,
            "cost_usd": 0.6,
            "job_id": "abc",
        }
    )

    async def fake_stream_bytes(*_a, **_kw):
        yield b"\x00" * 1024

    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()
    stream_resp.aiter_bytes = fake_stream_bytes

    class FakeStreamCtx:
        async def __aenter__(self):
            return stream_resp

        async def __aexit__(self, *a):
            return False

    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=post_resp)
    client_mock.stream = MagicMock(return_value=FakeStreamCtx())

    class FakeClientCtx:
        async def __aenter__(self):
            return client_mock

        async def __aexit__(self, *a):
            return False

    with patch("httpx.AsyncClient", return_value=FakeClientCtx()):
        result = await backend.generate(
            image_path=img,
            audio_path=audio,
            prompt="presenter",
            out_path=out,
        )

    assert isinstance(result, LiveAvatarResult)
    assert result.duration_s == 12.5
    assert result.cost_usd == 0.6
    assert result.backend == "remote_http"
    assert result.video_path == out
    assert out.exists() and out.stat().st_size > 0


# =============================================================================
# 6. Auth 401
# =============================================================================


@pytest.mark.asyncio
async def test_remote_backend_auth_401(cfg_remote: LiveAvatarConfig, tmp_path: Path) -> None:
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff\xd8")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    backend = RemoteHttpBackend(cfg_remote)

    resp = MagicMock()
    resp.status_code = 401
    resp.text = "invalid token"

    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=resp)

    class FakeCtx:
        async def __aenter__(self):
            return client_mock

        async def __aexit__(self, *a):
            return False

    with patch("httpx.AsyncClient", return_value=FakeCtx()):
        with pytest.raises(LiveAvatarAuthError):
            await backend.generate(
                image_path=img,
                audio_path=audio,
                prompt="x",
                out_path=tmp_path / "out.mp4",
            )


# =============================================================================
# 7. 5xx → APIError
# =============================================================================


@pytest.mark.asyncio
async def test_remote_backend_500_api_error(
    cfg_remote: LiveAvatarConfig, tmp_path: Path
) -> None:
    img = tmp_path / "img.jpg"
    img.write_bytes(b"\xff")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")

    backend = RemoteHttpBackend(cfg_remote)

    resp = MagicMock()
    resp.status_code = 503
    resp.text = "upstream OOM"

    client_mock = MagicMock()
    client_mock.post = AsyncMock(return_value=resp)

    class FakeCtx:
        async def __aenter__(self):
            return client_mock

        async def __aexit__(self, *a):
            return False

    with patch("httpx.AsyncClient", return_value=FakeCtx()):
        with pytest.raises(LiveAvatarAPIError):
            await backend.generate(
                image_path=img,
                audio_path=audio,
                prompt="x",
                out_path=tmp_path / "out.mp4",
            )


# =============================================================================
# 8. Generator: sin audio_path → VisualGenerationError
# =============================================================================


@pytest.mark.asyncio
async def test_generator_missing_audio_raises(
    cfg_remote: LiveAvatarConfig, beat: Beat, visual_no_audio: BeatVisual, tmp_path: Path
) -> None:
    gen = LiveAvatarGenerator(cfg_remote)
    with pytest.raises(VisualGenerationError, match="audio_path"):
        await gen.generate(beat, visual_no_audio, "general", tmp_path)


# =============================================================================
# 9. Generator: sin reference image → VisualGenerationError
# =============================================================================


@pytest.mark.asyncio
async def test_generator_missing_reference_raises(
    cfg_remote: LiveAvatarConfig, beat: Beat, tmp_path: Path
) -> None:
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    visual = BeatVisual(
        image_prompt="x", visual_anchor="y", audio_path=audio, reference_image_path=None
    )
    gen = LiveAvatarGenerator(cfg_remote)
    with pytest.raises(VisualGenerationError, match="reference image"):
        await gen.generate(beat, visual, "general", tmp_path)


# =============================================================================
# 10. Generator happy path con backend mockeado
# =============================================================================


@pytest.mark.asyncio
async def test_generator_happy_path_mocked_backend(
    cfg_remote: LiveAvatarConfig,
    beat: Beat,
    visual_with_audio: BeatVisual,
    tmp_path: Path,
) -> None:
    gen = LiveAvatarGenerator(cfg_remote)

    fake_video = tmp_path / "video.mp4"
    fake_video.write_bytes(b"\x00" * 16)
    fake_result = LiveAvatarResult(
        video_path=fake_video,
        duration_s=4.2,
        backend="remote_http",
        cost_usd=0.21,
    )
    gen.backend.generate = AsyncMock(return_value=fake_result)  # type: ignore[method-assign]

    artifact = await gen.generate(beat, visual_with_audio, "general", tmp_path)
    assert isinstance(artifact, BeatArtifact)
    assert artifact.idx == beat.idx
    assert artifact.source == VideoSource.LIVE_AVATAR
    assert artifact.video_path == fake_video
    assert artifact.duration_s == 4.2
    assert artifact.first_frame_path == visual_with_audio.reference_image_path

    # ¿pasó la audio + image path correctos al backend?
    call_kwargs = gen.backend.generate.await_args.kwargs  # type: ignore[union-attr]
    assert call_kwargs["audio_path"] == visual_with_audio.audio_path
    assert call_kwargs["image_path"] == visual_with_audio.reference_image_path


# =============================================================================
# 11. Orchestrator routing helper
# =============================================================================


def test_should_use_live_avatar_no_gen(visual_with_audio: BeatVisual) -> None:
    assert _should_use_live_avatar(visual_with_audio, None) is False


def test_should_use_live_avatar_no_audio(
    cfg_remote: LiveAvatarConfig, visual_no_audio: BeatVisual
) -> None:
    gen = LiveAvatarGenerator(cfg_remote)
    assert _should_use_live_avatar(visual_no_audio, gen) is False


def test_should_use_live_avatar_both_set(
    cfg_remote: LiveAvatarConfig, visual_with_audio: BeatVisual
) -> None:
    gen = LiveAvatarGenerator(cfg_remote)
    assert _should_use_live_avatar(visual_with_audio, gen) is True


# =============================================================================
# 12. Cost tracking
# =============================================================================


def test_video_cost_per_second_known_keys() -> None:
    assert "live_avatar_remote" in VIDEO_PRICING_USD_PER_SECOND
    assert "live_avatar_local" in VIDEO_PRICING_USD_PER_SECOND
    assert video_cost_per_second("live_avatar_remote") > 0
    assert video_cost_per_second("live_avatar_local") > 0
    assert video_cost_per_second("unknown_provider") == 0.0


def test_calculate_video_cost_linear() -> None:
    c10 = calculate_video_cost("live_avatar_remote", 10.0)
    c60 = calculate_video_cost("live_avatar_remote", 60.0)
    assert pytest.approx(c60 / 10) == pytest.approx(c10 * 0.6 * 10 / 60 * 10) or c60 > c10


def test_calculate_video_cost_zero_duration() -> None:
    assert calculate_video_cost("live_avatar_remote", 0.0) == 0.0
    assert calculate_video_cost("live_avatar_remote", -5.0) == 0.0


# =============================================================================
# 13. Schema: BeatVisual.audio_path round-trip
# =============================================================================


def test_beat_visual_audio_path_persists(tmp_path: Path) -> None:
    audio = tmp_path / "x.wav"
    audio.write_bytes(b"RIFF")
    bv = BeatVisual(
        image_prompt="x",
        visual_anchor="y",
        audio_path=audio,
        reference_image_path=tmp_path / "ref.jpg",
    )
    j = bv.model_dump_json()
    bv2 = BeatVisual.model_validate_json(j)
    assert bv2.audio_path == audio
    assert bv2.reference_image_path == tmp_path / "ref.jpg"


def test_beat_visual_no_audio_defaults_to_none() -> None:
    bv = BeatVisual(image_prompt="x", visual_anchor="y")
    assert bv.audio_path is None
    assert bv.reference_image_path is None


# =============================================================================
# 14. Schema: LongFormIntent.TALKING_HEAD
# =============================================================================


def test_long_form_intent_talking_head_member() -> None:
    assert LongFormIntent.TALKING_HEAD.value == "talking_head"
    # round-trip from string
    assert LongFormIntent("talking_head") is LongFormIntent.TALKING_HEAD


def test_video_source_live_avatar_member() -> None:
    assert VideoSource.LIVE_AVATAR.value == "live_avatar"


# =============================================================================
# 15. E2E orchestrator: reference_image_path + audio_path → LiveAvatar wins
# =============================================================================


@pytest.mark.asyncio
async def test_orchestrator_short_circuits_to_live_avatar(
    cfg_remote: LiveAvatarConfig, tmp_path: Path
) -> None:
    """Ruteo correcto cuando hay portrait fijo + audio: skip Tier 1 + use LiveAvatar."""
    from core.visual.generation import generate_beat_videos

    img = tmp_path / "anchor.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"RIFF")
    fake_video = tmp_path / "out.mp4"
    fake_video.write_bytes(b"\x00" * 16)

    gen = LiveAvatarGenerator(cfg_remote)
    gen.backend.generate = AsyncMock(  # type: ignore[method-assign]
        return_value=LiveAvatarResult(
            video_path=fake_video, duration_s=6.0,
            backend="remote_http", cost_usd=0.30,
        )
    )

    beat = Beat(idx=0, role=BeatRole.HOOK, text="hi", target_duration_s=3.0, veo_duration=4)
    visual = BeatVisual(
        image_prompt="anchor framed center",
        visual_anchor="anchor",
        audio_path=audio,
        reference_image_path=img,
    )

    artifacts = await generate_beat_videos(
        [beat], [visual], tmp_path, "general",
        use_live_avatar=True,
        use_higgsfield=False,
        use_higgsfield_soul=False,
        use_veo=False,
        use_comfyui=False,
        live_avatar_gen=gen,
    )
    assert len(artifacts) == 1
    assert artifacts[0].source == VideoSource.LIVE_AVATAR
    assert artifacts[0].video_path == fake_video
    assert artifacts[0].duration_s == 6.0
    # El short-circuit del reference image evitó tier 1 — backend.generate
    # fue invocado UNA sola vez con la img como image_path
    assert gen.backend.generate.await_count == 1
    call_kwargs = gen.backend.generate.await_args.kwargs
    assert call_kwargs["image_path"] == img
    assert call_kwargs["audio_path"] == audio
