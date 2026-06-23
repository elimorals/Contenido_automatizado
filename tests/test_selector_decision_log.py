"""Cableo del DecisionLog al selector estilo cost_tracker (ADR-027).

Verifica que `select_and_generate_beat_artifact` / `generate_all_beat_artifacts`,
cuando reciben un `DecisionLog`, registran una decisión auditable por beat (con la
fuente real elegida en el contexto) — sin alterar el comportamiento si es None.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.visual import selector as sel_mod
from core.visual.provider_scoring import DecisionLog
from core.visual.selector import (
    generate_all_beat_artifacts,
    select_and_generate_beat_artifact,
)
from shared.config import Config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Essence,
    GenerationMode,
    MotionHint,
    VideoSource,
    VisualStrategy,
)


@pytest.fixture
def beat() -> Beat:
    return Beat(idx=0, role=BeatRole.HOOK, text="a quiet city at dawn", target_duration_s=2.5, veo_duration=6)


@pytest.fixture
def visual() -> BeatVisual:
    return BeatVisual(image_prompt="city at dawn", motion_hint=MotionHint.SLOW_ZOOM_IN, visual_anchor="city dawn")


@pytest.fixture
def essence() -> Essence:
    return Essence(
        core_claim="x", mechanism="y", evidence=["dawn"], content_mode="general", domain="general"
    )


def _stock_artifact(idx: int, tmp_path: Path) -> BeatArtifact:
    clip = tmp_path / f"stock-{idx}.mp4"
    clip.write_bytes(b"v")
    return BeatArtifact(
        idx=idx, first_frame_path=None, video_path=clip, source=VideoSource.PEXELS, duration_s=8.0
    )


@pytest.mark.asyncio
async def test_decision_log_records_one_per_beat(beat, visual, essence, tmp_path) -> None:
    log = DecisionLog()
    with (
        patch.object(sel_mod, "load_config", return_value=Config()),
        patch.object(sel_mod, "get_all_providers", return_value=[]),
        patch.object(sel_mod, "_try_stock", new=AsyncMock(return_value=_stock_artifact(0, tmp_path))),
    ):
        await select_and_generate_beat_artifact(
            beat=beat,
            visual=visual,
            essence=essence,
            mode=GenerationMode.EXPRESS,
            strategy=VisualStrategy.STOCK,
            aspect="9:16",
            out_dir=tmp_path,
            decision_log=log,
        )
    assert len(log.decisions) == 1
    d = log.decisions[0]
    assert d.label == "beat_0"
    # el contexto auditado refleja la fuente REAL elegida por la heurística
    assert d.context["actual_source"] == VideoSource.PEXELS.value
    # la fuente real siempre es uno de los candidatos puntuados (comparabilidad)
    assert VideoSource.PEXELS.value in {a.provider for a in d.alternatives}


@pytest.mark.asyncio
async def test_decision_log_none_is_noop(beat, visual, essence, tmp_path) -> None:
    with (
        patch.object(sel_mod, "load_config", return_value=Config()),
        patch.object(sel_mod, "get_all_providers", return_value=[]),
        patch.object(sel_mod, "_try_stock", new=AsyncMock(return_value=_stock_artifact(0, tmp_path))),
    ):
        artifact = await select_and_generate_beat_artifact(
            beat=beat,
            visual=visual,
            essence=essence,
            mode=GenerationMode.EXPRESS,
            strategy=VisualStrategy.STOCK,
            aspect="9:16",
            out_dir=tmp_path,
            decision_log=None,
        )
    assert artifact.video_path is not None  # sigue funcionando igual


@pytest.mark.asyncio
async def test_generate_all_records_per_beat(visual, essence, tmp_path) -> None:
    beats = [
        Beat(idx=i, role=BeatRole.MECHANISM, text=f"beat {i}", target_duration_s=3.0, veo_duration=4)
        for i in range(3)
    ]
    visuals = [visual, visual, visual]
    log = DecisionLog()

    async def _fake_stock(beat, *a, **k):
        return _stock_artifact(beat.idx, tmp_path)

    with (
        patch.object(sel_mod, "load_config", return_value=Config()),
        patch.object(sel_mod, "get_all_providers", return_value=[]),
        patch.object(sel_mod, "_try_stock", new=_fake_stock),
    ):
        await generate_all_beat_artifacts(
            beats=beats,
            visuals=visuals,
            essence=essence,
            mode=GenerationMode.EXPRESS,
            strategy=VisualStrategy.STOCK,
            aspect="9:16",
            out_dir=tmp_path,
            decision_log=log,
        )
    assert len(log.decisions) == 3
    assert {d.label for d in log.decisions} == {"beat_0", "beat_1", "beat_2"}
