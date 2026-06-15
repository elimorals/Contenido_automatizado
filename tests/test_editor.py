"""Tests para core.editor (single-pass ffmpeg, multi-aspect, encoder fallback).

Mocks de `asyncio.create_subprocess_exec` para evitar invocar ffmpeg real
durante CI/unit tests.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.editor import (
    FFmpegCommandBuilder,
    build_bgm_mix_filter,
    build_scale_filter,
    detect_hw_encoder,
    get_dimensions,
    get_encoder_args,
    list_bgm_files,
    pick_bgm,
    reset_runtime_state,
    stitch_video,
)
from shared.schemas import BeatArtifact, VideoAspect, VideoSource


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_encoder_state():
    """Cada test arranca con cache de encoder limpio."""
    reset_runtime_state()
    yield
    reset_runtime_state()


@pytest.fixture
def sample_artifacts(tmp_path: Path) -> list[BeatArtifact]:
    artifacts = []
    for i in range(3):
        clip = tmp_path / f"beat-{i:02d}.mp4"
        clip.write_bytes(b"\x00" * 16)  # placeholder
        artifacts.append(
            BeatArtifact(
                idx=i,
                video_path=clip,
                source=VideoSource.VEO,
                duration_s=4.0,
            )
        )
    return artifacts


@pytest.fixture
def sample_audio(tmp_path: Path) -> Path:
    p = tmp_path / "voice.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 100)
    return p


# ─────────────────────────────────────────────────────────────────────
# Test 1 — Aspect dimensions
# ─────────────────────────────────────────────────────────────────────


def test_get_dimensions_for_all_aspects():
    assert get_dimensions(VideoAspect.PORTRAIT) == (1080, 1920)
    assert get_dimensions(VideoAspect.LANDSCAPE) == (1920, 1080)
    assert get_dimensions(VideoAspect.SQUARE) == (1080, 1080)
    # String input también funciona.
    assert get_dimensions("9:16") == (1080, 1920)


def test_build_scale_filter_portrait():
    f = build_scale_filter(VideoAspect.PORTRAIT, fps=30)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in f
    assert "crop=1080:1920" in f
    assert "setsar=1" in f
    assert "fps=30" in f
    assert "format=yuv420p" in f


def test_build_scale_filter_landscape_no_fps():
    f = build_scale_filter(VideoAspect.LANDSCAPE)
    assert "scale=1920:1080" in f
    assert "crop=1920:1080" in f
    assert "fps=" not in f  # fps no pasado → no se añade


# ─────────────────────────────────────────────────────────────────────
# Test 2 — Hardware encoder detection + fallback
# ─────────────────────────────────────────────────────────────────────


def test_detect_hw_encoder_falls_back_to_libx264_when_none_available():
    """Si solo libx264 está en `-encoders`, lo devuelve."""
    fake_encoders_output = "Encoders:\n V..... libx264 ...\n"
    with (
        patch("core.editor.encoders.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_encoders_output, stderr="")
        codec = detect_hw_encoder()
        assert codec == "libx264"


def test_detect_hw_encoder_picks_hardware_when_available():
    """Si videotoolbox aparece en `-encoders`, lo elige antes que libx264."""
    fake_encoders_output = (
        "Encoders:\n"
        " V..... libx264 ...\n"
        " V..... h264_videotoolbox ...\n"
    )
    with (
        patch("core.editor.encoders.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.platform.system", return_value="Darwin"),
        patch("core.editor.encoders.subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_encoders_output, stderr="")
        codec = detect_hw_encoder()
        assert codec == "h264_videotoolbox"


def test_encoder_args_for_each_codec():
    """Cada codec emite sus flags propios (no crf hardcoded)."""
    x264 = get_encoder_args("libx264", crf=23, preset="fast")
    assert "-crf" in x264 and "23" in x264 and "-preset" in x264

    vt = get_encoder_args("h264_videotoolbox", crf=23, preset="fast")
    assert "-q:v" in vt  # videotoolbox usa q:v, no crf
    assert "-crf" not in vt

    nv = get_encoder_args("h264_nvenc", crf=23, preset="fast")
    assert "-cq" in nv and "-preset" in nv


# ─────────────────────────────────────────────────────────────────────
# Test 3 — filter_complex correcto (concat + ass + amix)
# ─────────────────────────────────────────────────────────────────────


def test_bgm_mix_filter_structure():
    """build_bgm_mix_filter emite voice + bgm + amix con weights correctos."""
    f = build_bgm_mix_filter(
        "1:a", "2:a", voice_weight=1.0, bgm_weight=0.2, out_label="a"
    )
    assert "[1:a]volume=1.0" in f
    assert "[2:a]aloop=loop=-1" in f
    assert "volume=0.2" in f
    assert "amix=inputs=2:duration=first" in f
    assert "[a]" in f


@pytest.mark.asyncio
async def test_stitch_video_builds_correct_filter_complex(
    sample_artifacts, sample_audio, tmp_path
):
    """Verifica que el comando ffmpeg generado tenga el filter_complex esperado."""
    captured_cmds: list[list[str]] = []

    fake_encoders = "Encoders:\n V..... libx264 ...\n"

    async def fake_communicate(self):
        return (b"", b"")

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    async def fake_exec(*cmd, stdout=None, stderr=None):
        captured_cmds.append(list(cmd))
        return fake_proc

    with (
        patch("core.editor.ffmpeg_stitch.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.subprocess.run") as mock_run,
        patch(
            "core.editor.ffmpeg_stitch.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_encoders, stderr="")
        out = await stitch_video(
            artifacts=sample_artifacts,
            audio_path=sample_audio,
            subtitle_ass_path=None,
            out_dir=tmp_path / "out",
            aspect=VideoAspect.PORTRAIT,
        )
        assert out.name == "reel.mp4"

    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    # filter_complex está presente.
    assert "-filter_complex" in cmd
    fc_idx = cmd.index("-filter_complex")
    fc = cmd[fc_idx + 1]
    assert "scale=1080:1920" in fc
    assert "[1:a]volume=1.0[a]" in fc  # voice-only path
    # Concat demuxer input.
    assert "-f" in cmd and "concat" in cmd and "-safe" in cmd
    # Libx264 args.
    assert "-c:v" in cmd
    cv_idx = cmd.index("-c:v")
    assert cmd[cv_idx + 1] == "libx264"


# ─────────────────────────────────────────────────────────────────────
# Test 4 — BGM mix path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stitch_video_with_bgm_adds_third_input_and_amix(
    sample_artifacts, sample_audio, tmp_path
):
    bgm = tmp_path / "song.mp3"
    bgm.write_bytes(b"ID3\x00" * 4)

    captured_cmds: list[list[str]] = []
    fake_encoders = "Encoders:\n V..... libx264 ...\n"

    fake_proc = MagicMock()
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))
    fake_proc.returncode = 0

    async def fake_exec(*cmd, stdout=None, stderr=None):
        captured_cmds.append(list(cmd))
        return fake_proc

    with (
        patch("core.editor.ffmpeg_stitch.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.subprocess.run") as mock_run,
        patch(
            "core.editor.ffmpeg_stitch.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_encoders, stderr="")
        await stitch_video(
            artifacts=sample_artifacts,
            audio_path=sample_audio,
            subtitle_ass_path=None,
            out_dir=tmp_path / "out",
            aspect=VideoAspect.PORTRAIT,
            bgm_path=bgm,
            bgm_volume=0.25,
        )

    cmd = captured_cmds[0]
    # 3 inputs: concat list, audio, bgm.
    input_count = sum(1 for token in cmd if token == "-i")
    assert input_count == 3
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "amix=inputs=2" in fc
    assert "volume=0.25" in fc


# ─────────────────────────────────────────────────────────────────────
# Test 5 — Encoder runtime fallback (videotoolbox falla → libx264)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stitch_video_falls_back_to_libx264_when_hw_encoder_fails(
    sample_artifacts, sample_audio, tmp_path
):
    """Si el primer encoder devuelve rc != 0, marca failed y reintenta con libx264."""
    fake_encoders = (
        "Encoders:\n"
        " V..... libx264 ...\n"
        " V..... h264_videotoolbox ...\n"
    )
    call_count = {"n": 0}

    async def fake_exec(*cmd, stdout=None, stderr=None):
        call_count["n"] += 1
        proc = MagicMock()
        # First call: fail. Second: success.
        if call_count["n"] == 1:
            proc.returncode = 1
            proc.communicate = AsyncMock(
                return_value=(b"", b"Error: videotoolbox session failed")
            )
        else:
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    with (
        patch("core.editor.ffmpeg_stitch.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("core.editor.encoders.platform.system", return_value="Darwin"),
        patch("core.editor.encoders.subprocess.run") as mock_run,
        patch(
            "core.editor.ffmpeg_stitch.asyncio.create_subprocess_exec",
            side_effect=fake_exec,
        ),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout=fake_encoders, stderr="")
        out = await stitch_video(
            artifacts=sample_artifacts,
            audio_path=sample_audio,
            subtitle_ass_path=None,
            out_dir=tmp_path / "out",
            aspect=VideoAspect.PORTRAIT,
        )

    # Se invocó ffmpeg 2 veces: 1ª videotoolbox falló, 2ª libx264 OK.
    assert call_count["n"] == 2
    assert out.exists() or out.name == "reel.mp4"


# ─────────────────────────────────────────────────────────────────────
# Test 6 — BGM library + picker
# ─────────────────────────────────────────────────────────────────────


def test_list_bgm_files_finds_only_supported_extensions(tmp_path):
    lib = tmp_path / "songs"
    lib.mkdir()
    (lib / "a.mp3").write_bytes(b"id3")
    (lib / "b.wav").write_bytes(b"riff")
    (lib / "c.txt").write_bytes(b"not music")
    files = list_bgm_files(lib)
    names = sorted(f.name for f in files)
    assert names == ["a.mp3", "b.wav"]


def test_pick_bgm_none_returns_none(tmp_path):
    assert pick_bgm(bgm_type="none") is None
    assert pick_bgm(bgm_type="") is None


def test_pick_bgm_custom_rejects_paths_outside_library(tmp_path, monkeypatch):
    lib = tmp_path / "songs"
    lib.mkdir()
    (lib / "ok.mp3").write_bytes(b"id3")

    # Patch library_dir to point at tmp_path/songs.
    from core.editor import bgm as bgm_mod

    monkeypatch.setattr(bgm_mod, "_resolve_library_dir", lambda config=None: lib)

    # Filename simple → resuelve OK.
    out = pick_bgm(bgm_type="custom", bgm_file="ok.mp3")
    assert out is not None and out.name == "ok.mp3"

    # Path absoluto fuera de library → None.
    evil = tmp_path / "evil.mp3"
    evil.write_bytes(b"id3")
    out = pick_bgm(bgm_type="custom", bgm_file=str(evil))
    assert out is None
