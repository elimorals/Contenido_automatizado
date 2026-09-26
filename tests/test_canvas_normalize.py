"""Aspect de punta a punta y recorte de clips IA (auditoría 2026-09)."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from core.visual.canvas import canvas_for, current_canvas, use_aspect
from core.visual.generation.orchestrator import _normalize_clip
from shared.schemas import Beat, BeatArtifact, BeatRole, VideoSource


def test_canvas_per_aspect():
    assert canvas_for("9:16") == (720, 1280)
    assert canvas_for("16:9") == (1280, 720)
    assert canvas_for("1:1") == (720, 720)


async def test_aspect_survives_gather():
    async def probe():
        await asyncio.sleep(0)
        return current_canvas()

    with use_aspect("16:9"):
        assert await asyncio.gather(probe(), probe()) == [(1280, 720)] * 2
    assert current_canvas() == (720, 1280)


def _probe(path: Path) -> tuple[float, int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return float(out[2]), int(out[0]), int(out[1])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="requiere ffmpeg")
async def test_ai_clip_trimmed_to_beat_and_canvas(tmp_path):
    src = tmp_path / "sora.mp4"  # 12 s vertical, como devuelve el MCP
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=s=720x1280:d=12:r=30", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", str(src)], check=True)
    beat = Beat(idx=0, role=BeatRole.HOOK, text="x", target_duration_s=4.2, veo_duration=6)
    art = BeatArtifact(idx=0, video_path=src, source=VideoSource.SORA)

    with use_aspect("16:9"):
        out = await _normalize_clip(beat, art, tmp_path)

    dur, w, h = _probe(Path(out.video_path))
    assert abs(dur - 4.2) < 0.15
    assert (w, h) == (1280, 720)
