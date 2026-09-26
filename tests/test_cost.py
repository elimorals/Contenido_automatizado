"""Tope de gasto, tabla única y no-doble-cobro (auditoría 2026-09)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.cost import (
    BudgetExceeded,
    current_ledger,
    estimate_usd,
    is_tripped,
    job_budget,
    note_failure,
)
from core.visual.generation.base import GenerationTimeout
from core.visual.generation.orchestrator import _generate_one
from shared.schemas import Beat, BeatArtifact, BeatRole, BeatVisual, VideoSource


def test_single_price_table_covers_sora_and_openai():
    assert estimate_usd(VideoSource.SORA, 8) == pytest.approx(0.8)
    assert estimate_usd(VideoSource.OPENAI_IMAGE) > 0
    assert estimate_usd(VideoSource.PEXELS, 8) == 0


def test_ledger_enforces_limit():
    with job_budget(1.0) as ledger:
        ledger.charge("visual", "sora", 0.8)
        with pytest.raises(BudgetExceeded):
            ledger.ensure(0.5, "x")
        assert ledger.breakdown()["total_usd"] == 0.8
    assert current_ledger() is None


def test_breaker_trips_on_account_errors_only():
    note_failure(VideoSource.HIGGSFIELD_DOP, RuntimeError("HTTP 402 Payment Required"))
    note_failure(VideoSource.SORA, RuntimeError("HTTP 503"))
    assert is_tripped(VideoSource.HIGGSFIELD_DOP) and not is_tripped(VideoSource.SORA)


def _gen(result=None, exc=None):
    g = MagicMock()
    g.generate = AsyncMock(side_effect=exc, return_value=result)
    return g


def _kwargs(tmp_path: Path, **gens):
    frame = tmp_path / "f.jpg"
    frame.write_bytes(b"x")
    img = _gen(BeatArtifact(idx=0, first_frame_path=frame))
    kb = _gen(BeatArtifact(idx=0, video_path=frame, source=VideoSource.GEMINI_IMAGE))
    base = {"comfy_gen": None, "soul_gen": None, "image_gen": img, "openai_image_gen": None,
                "hf_dop_gen": None, "veo_gen": None, "sora_gen": None, "ken_burns_gen": kb,
                "effects_gen": None, "fal_gen": None, "hf_mcp_gen": None}
    base.update(gens)
    return base, kb


BEAT = Beat(idx=0, role=BeatRole.HOOK, text="t", target_duration_s=5, veo_duration=6)
VISUAL = BeatVisual(image_prompt="p", visual_anchor="a")


async def test_timeout_does_not_fall_through_to_next_paid_provider(tmp_path):
    fal = _gen(BeatArtifact(idx=0, video_path=tmp_path / "f.jpg", source=VideoSource.FAL))
    kw, kb = _kwargs(tmp_path, sora_gen=_gen(exc=GenerationTimeout("sora timeout")), fal_gen=fal)
    with job_budget(10):
        await _generate_one(BEAT, VISUAL, tmp_path, "general", **kw)
    fal.generate.assert_not_called()  # antes: Sora + fal cobrados por el mismo beat
    kb.generate.assert_called_once()


async def test_budget_stops_paid_motion(tmp_path):
    sora = _gen(BeatArtifact(idx=0, video_path=tmp_path / "f.jpg", source=VideoSource.SORA))
    kw, kb = _kwargs(tmp_path, sora_gen=sora)
    with job_budget(0.10) as ledger:  # alcanza la imagen, no los $0.60 de Sora
        await _generate_one(BEAT, VISUAL, tmp_path, "general", **kw)
    sora.generate.assert_not_called()
    kb.generate.assert_called_once()
    assert ledger.total_usd == pytest.approx(estimate_usd(VideoSource.GEMINI_IMAGE))
