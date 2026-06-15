"""Tests para core/visual/selector: heurísticas + two-tier fallback.

Cobertura mínima (todos con mocks; sin red, sin API real):
1. EXPRESS + hook + stock disponible → usa stock.
2. PREMIUM + hook → usa IA.
3. PREMIUM + mechanism con evidence en text → usa IA.
4. PREMIUM + mechanism SIN evidence match → usa stock.
5. Stock falla → fallback a IA.
6. IA falla → fallback a stock.
7. Ambos fallan → placeholder (NUNCA raise).
8. strategy=STOCK override → ignora modo.
9. Fan-out paralelo respeta orden.
10. Cost tracker captura por beat.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.visual import selector as sel_mod
from core.visual.selector import (
    _decide_source,
    _make_placeholder_artifact,
    generate_all_beat_artifacts,
    select_and_generate_beat_artifact,
)
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Essence,
    GenerationMode,
    MaterialInfo,
    MotionHint,
    VideoAspect,
    VideoSource,
    VisualStrategy,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def hook_beat() -> Beat:
    return Beat(
        idx=0,
        role=BeatRole.HOOK,
        text="The 12,000 hour rule that no one talks about",
        target_duration_s=2.5,
        veo_duration=6,
    )


@pytest.fixture
def mechanism_beat() -> Beat:
    return Beat(
        idx=1,
        role=BeatRole.MECHANISM,
        text="Researchers tracked 1,200 violinists in Berlin in 1993",
        target_duration_s=4.0,
        veo_duration=4,
    )


@pytest.fixture
def mechanism_beat_generic() -> Beat:
    return Beat(
        idx=2,
        role=BeatRole.MECHANISM,
        text="It really comes down to consistent practice over time",
        target_duration_s=4.0,
        veo_duration=4,
    )


@pytest.fixture
def payoff_beat() -> Beat:
    return Beat(
        idx=3,
        role=BeatRole.PAYOFF,
        text="So the rule is not just hours — it is deliberate hours",
        target_duration_s=2.0,
        veo_duration=4,
    )


@pytest.fixture
def sample_visual() -> BeatVisual:
    return BeatVisual(
        image_prompt="violinist practicing alone in a sunlit room",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor="violinist practicing",
    )


@pytest.fixture
def sample_essence() -> Essence:
    return Essence(
        core_claim="Deliberate practice beats raw hours by 3x",
        mechanism="Targeted feedback loops compound faster than repetition",
        evidence=[
            "1,200 violinists in Berlin in 1993",
            "12,000 hour threshold",
            "Ericsson 2007 meta-review",
        ],
        content_mode="general",
        domain="psychology",
    )


def _stock_artifact(idx: int, tmp_path: Path) -> BeatArtifact:
    """Devuelve un BeatArtifact simulando un hit de stock."""
    clip = tmp_path / f"stock-{idx}.mp4"
    clip.write_bytes(b"fake-stock-mp4")
    return BeatArtifact(
        idx=idx,
        first_frame_path=None,
        video_path=clip,
        source=VideoSource.PEXELS,
        duration_s=8.0,
    )


def _ia_artifact(idx: int, tmp_path: Path) -> BeatArtifact:
    """Devuelve un BeatArtifact simulando un hit de IA generation."""
    clip = tmp_path / f"ia-{idx}.mp4"
    clip.write_bytes(b"fake-ia-mp4")
    return BeatArtifact(
        idx=idx,
        first_frame_path=tmp_path / f"frame-{idx}.jpg",
        video_path=clip,
        source=VideoSource.GEMINI_IMAGE,
        duration_s=4.0,
    )


# =============================================================================
# 1-4: HEURÍSTICAS DE DECISIÓN (_decide_source)
# =============================================================================


def test_express_hook_uses_stock_by_default(hook_beat, sample_essence) -> None:
    """EXPRESS + hook + veo OFF (default) → stock."""
    src = _decide_source(
        hook_beat,
        sample_essence,
        GenerationMode.EXPRESS,
        VisualStrategy.HYBRID,
    )
    assert src == VideoSource.PEXELS


def test_express_hook_with_veo_enabled_uses_ia(
    hook_beat, sample_essence
) -> None:
    """EXPRESS + hook + veo_enabled=True → IA (premium-bound)."""
    fake_cfg = MagicMock()
    fake_cfg.visual.veo_enabled = True
    fake_cfg.visual.higgsfield.enabled = False
    fake_cfg.visual.higgsfield.soul_enabled = False
    fake_cfg.visual.higgsfield.credentials = ""
    fake_cfg.visual.higgsfield.key_id = ""
    fake_cfg.visual.higgsfield.key_secret = ""
    fake_cfg.visual.higgsfield.soul_default_reference_id = ""
    with patch("core.visual.selector.load_config", return_value=fake_cfg):
        src = _decide_source(
            hook_beat,
            sample_essence,
            GenerationMode.EXPRESS,
            VisualStrategy.HYBRID,
        )
    assert src == VideoSource.GEMINI_IMAGE


def test_premium_hook_always_uses_ia(hook_beat, sample_essence) -> None:
    """PREMIUM + hook → IA (stop-the-scroll)."""
    src = _decide_source(
        hook_beat,
        sample_essence,
        GenerationMode.PREMIUM,
        VisualStrategy.HYBRID,
    )
    assert src == VideoSource.GEMINI_IMAGE


def test_premium_mechanism_with_evidence_match_uses_ia(
    mechanism_beat, sample_essence
) -> None:
    """PREMIUM + mechanism que referencia evidence en text → IA."""
    # mechanism_beat.text contiene "1,200 violinists in Berlin in 1993",
    # que matchea evidence[0].
    src = _decide_source(
        mechanism_beat,
        sample_essence,
        GenerationMode.PREMIUM,
        VisualStrategy.HYBRID,
    )
    assert src == VideoSource.GEMINI_IMAGE


def test_premium_mechanism_without_evidence_match_uses_stock(
    mechanism_beat_generic, sample_essence
) -> None:
    """PREMIUM + mechanism genérico (sin evidence match) → stock."""
    src = _decide_source(
        mechanism_beat_generic,
        sample_essence,
        GenerationMode.PREMIUM,
        VisualStrategy.HYBRID,
    )
    assert src == VideoSource.PEXELS


def test_premium_payoff_defaults_to_stock(payoff_beat, sample_essence) -> None:
    """PREMIUM + payoff → stock (callback visual genérico)."""
    src = _decide_source(
        payoff_beat,
        sample_essence,
        GenerationMode.PREMIUM,
        VisualStrategy.HYBRID,
    )
    assert src == VideoSource.PEXELS


def test_strategy_stock_override_ignores_mode(hook_beat, sample_essence) -> None:
    """strategy=STOCK fuerza stock incluso en PREMIUM + hook."""
    src = _decide_source(
        hook_beat,
        sample_essence,
        GenerationMode.PREMIUM,
        VisualStrategy.STOCK,
    )
    assert src == VideoSource.PEXELS


def test_strategy_ia_override_ignores_mode(payoff_beat, sample_essence) -> None:
    """strategy=IA fuerza IA incluso para payoff en EXPRESS."""
    src = _decide_source(
        payoff_beat,
        sample_essence,
        GenerationMode.EXPRESS,
        VisualStrategy.IA,
    )
    assert src == VideoSource.GEMINI_IMAGE


# =============================================================================
# 5: STOCK FAILS → FALLBACK A IA
# =============================================================================


@pytest.mark.asyncio
async def test_stock_fails_falls_back_to_ia(
    mechanism_beat_generic, sample_visual, sample_essence, tmp_path
) -> None:
    """Si primary=PEXELS pero stock devuelve None → debe llamar IA."""
    ia_art = _ia_artifact(mechanism_beat_generic.idx, tmp_path)

    with (
        patch(
            "core.visual.selector._try_stock",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.visual.selector._try_ia_generation",
            new=AsyncMock(return_value=ia_art),
        ) as mock_ia,
    ):
        result = await select_and_generate_beat_artifact(
            beat=mechanism_beat_generic,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert result.video_path == ia_art.video_path
    assert result.source == VideoSource.GEMINI_IMAGE
    mock_ia.assert_awaited_once()


# =============================================================================
# 6: IA FAILS → FALLBACK A STOCK
# =============================================================================


@pytest.mark.asyncio
async def test_ia_fails_falls_back_to_stock(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """Si primary=GEMINI_IMAGE pero IA devuelve None → debe llamar stock."""
    stock_art = _stock_artifact(hook_beat.idx, tmp_path)

    with (
        patch(
            "core.visual.selector._try_ia_generation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.visual.selector._try_stock",
            new=AsyncMock(return_value=stock_art),
        ) as mock_stock,
    ):
        result = await select_and_generate_beat_artifact(
            beat=hook_beat,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert result.video_path == stock_art.video_path
    assert result.source == VideoSource.PEXELS
    mock_stock.assert_awaited_once()


# =============================================================================
# 7: AMBOS FALLAN → PLACEHOLDER (NUNCA raise)
# =============================================================================


@pytest.mark.asyncio
async def test_both_fail_returns_placeholder_never_raises(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """Si stock e IA devuelven None → placeholder. Nunca raise."""
    with (
        patch(
            "core.visual.selector._try_ia_generation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "core.visual.selector._try_stock",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await select_and_generate_beat_artifact(
            beat=hook_beat,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )

    # Nunca raise → siempre BeatArtifact
    assert isinstance(result, BeatArtifact)
    assert result.idx == hook_beat.idx
    # Placeholder: no video_path, fuente LOCAL
    assert result.video_path is None
    assert result.source == VideoSource.LOCAL
    # El frame placeholder debe haberse creado
    assert result.first_frame_path is not None
    assert result.first_frame_path.exists()


@pytest.mark.asyncio
async def test_unexpected_exception_still_returns_placeholder(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """Si _try_ia lanza excepción no atrapada por su helper → placeholder."""
    with (
        patch(
            "core.visual.selector._try_ia_generation",
            new=AsyncMock(side_effect=RuntimeError("catastrophic")),
        ),
        patch(
            "core.visual.selector._try_stock",
            new=AsyncMock(side_effect=RuntimeError("also dead")),
        ),
    ):
        result = await select_and_generate_beat_artifact(
            beat=hook_beat,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert isinstance(result, BeatArtifact)
    assert result.source == VideoSource.LOCAL


# =============================================================================
# 8: STRATEGY OVERRIDE (end-to-end con _try_stock real-ish)
# =============================================================================


@pytest.mark.asyncio
async def test_strategy_stock_override_takes_stock_path_first(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """strategy=STOCK fuerza la rama stock primero incluso para hook PREMIUM."""
    stock_art = _stock_artifact(hook_beat.idx, tmp_path)
    mock_stock = AsyncMock(return_value=stock_art)
    mock_ia = AsyncMock(return_value=_ia_artifact(hook_beat.idx, tmp_path))

    with (
        patch("core.visual.selector._try_stock", new=mock_stock),
        patch("core.visual.selector._try_ia_generation", new=mock_ia),
    ):
        result = await select_and_generate_beat_artifact(
            beat=hook_beat,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.STOCK,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert result.source == VideoSource.PEXELS
    mock_stock.assert_awaited_once()
    mock_ia.assert_not_awaited()


# =============================================================================
# EXTRAS: cost tracking + fan-out paralelo + _try_stock con providers reales
# =============================================================================


@pytest.mark.asyncio
async def test_cost_tracker_records_source(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """Cost tracker debe popularse con `beat_<idx>_<source>` → cost."""
    ia_art = _ia_artifact(hook_beat.idx, tmp_path)
    tracker: dict = {}

    with patch(
        "core.visual.selector._try_ia_generation",
        new=AsyncMock(return_value=ia_art),
    ):
        await select_and_generate_beat_artifact(
            beat=hook_beat,
            visual=sample_visual,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
            cost_tracker=tracker,
        )

    assert f"beat_{hook_beat.idx}_gemini_image" in tracker
    assert tracker[f"beat_{hook_beat.idx}_gemini_image"] > 0


@pytest.mark.asyncio
async def test_generate_all_fan_out_preserves_order(
    hook_beat, mechanism_beat, payoff_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """`generate_all_beat_artifacts` debe devolver lista en orden de beats."""
    beats = [hook_beat, mechanism_beat, payoff_beat]
    visuals = [sample_visual, sample_visual, sample_visual]

    async def fake_select(beat, visual, essence, mode, strategy, aspect, out_dir, cost_tracker=None):
        # Devuelve un artifact con idx = beat.idx
        clip = out_dir / f"clip-{beat.idx}.mp4"
        clip.write_bytes(b"x")
        return BeatArtifact(
            idx=beat.idx,
            video_path=clip,
            source=VideoSource.GEMINI_IMAGE,
            duration_s=float(beat.veo_duration),
        )

    with patch(
        "core.visual.selector.select_and_generate_beat_artifact",
        new=AsyncMock(side_effect=fake_select),
    ):
        results = await generate_all_beat_artifacts(
            beats=beats,
            visuals=visuals,
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert len(results) == 3
    assert [r.idx for r in results] == [0, 1, 3]


@pytest.mark.asyncio
async def test_generate_all_mismatched_lengths_raises(
    hook_beat, sample_visual, sample_essence, tmp_path
) -> None:
    """beats y visuals con largos distintos debe raise ValueError."""
    with pytest.raises(ValueError):
        await generate_all_beat_artifacts(
            beats=[hook_beat],
            visuals=[sample_visual, sample_visual],
            essence=sample_essence,
            mode=GenerationMode.PREMIUM,
            strategy=VisualStrategy.HYBRID,
            aspect="9:16",
            out_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_try_stock_returns_none_when_no_providers(
    mechanism_beat_generic, sample_visual, tmp_path
) -> None:
    """`_try_stock` con 0 providers configurados → None (no raise)."""
    with patch("core.visual.selector.get_all_providers", return_value=[]):
        result = await sel_mod._try_stock(
            beat=mechanism_beat_generic,
            visual=sample_visual,
            aspect="9:16",
            out_dir=tmp_path,
        )
    assert result is None


@pytest.mark.asyncio
async def test_try_stock_iterates_providers_until_hit(
    mechanism_beat_generic, sample_visual, tmp_path
) -> None:
    """`_try_stock` debe probar providers en orden hasta encontrar un resultado."""
    # Provider 1: search devuelve [] → skip
    p1 = MagicMock()
    p1.name = "pexels"
    p1.search = AsyncMock(return_value=[])

    # Provider 2: search devuelve un hit
    p2 = MagicMock()
    p2.name = "pixabay"
    material = MaterialInfo(
        provider=VideoSource.PIXABAY,
        url="https://px.example/ok.mp4",
        duration_s=8.0,
        width=1080,
        height=1920,
    )
    p2.search = AsyncMock(return_value=[material])

    target = tmp_path / "cached.mp4"
    target.write_bytes(b"fake")

    with (
        patch(
            "core.visual.selector.get_all_providers",
            return_value=[p1, p2],
        ),
        patch(
            "core.visual.selector.fetch_and_cache",
            new=AsyncMock(return_value=target),
        ),
    ):
        result = await sel_mod._try_stock(
            beat=mechanism_beat_generic,
            visual=sample_visual,
            aspect="9:16",
            out_dir=tmp_path,
        )

    assert result is not None
    assert result.source == VideoSource.PIXABAY
    assert result.video_path == target
    p1.search.assert_awaited_once()
    p2.search.assert_awaited_once()


# =============================================================================
# Placeholder helper directo
# =============================================================================


def test_placeholder_artifact_has_correct_shape(hook_beat, tmp_path) -> None:
    """`_make_placeholder_artifact` produce un BeatArtifact LOCAL sin video."""
    art = _make_placeholder_artifact(hook_beat, tmp_path)
    assert art.idx == hook_beat.idx
    assert art.video_path is None
    assert art.source == VideoSource.LOCAL
    assert art.duration_s == float(hook_beat.veo_duration)
    assert art.first_frame_path is not None
    assert art.first_frame_path.exists()
