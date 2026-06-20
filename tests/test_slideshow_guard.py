"""Tests para core/editorial/slideshow_guard: detección de "PowerPoint animado".

Idea portada (reimplementada, NO copiada) de OpenMontage lib/slideshow_risk.py +
lib/delivery_promise.py: si el reel prometió movimiento ("cinético") pero la mayoría
de los beats son stills (ken-burns sobre imagen) o placeholders, hay que bloquear/avisar.

Cobertura:
1. is_static_artifact: clasifica still vs movimiento real por source + video_path.
2. Todo movimiento real (stock) → ok, no slideshow.
3. Todo placeholder (video_path None) + promised_motion → error.
4. Todo ken-burns (LOCAL) + promised_motion → error.
5. Mixto bajo umbral → ok.
6. Mixto sobre umbral → error.
7. promised_motion=False → nunca error de slideshow (pero placeholder avisa).
8. Lista vacía → ok, n_beats=0.
9. Baja diversidad de fuentes con stills → warning.
"""
from __future__ import annotations

from pathlib import Path

from core.editorial.slideshow_guard import (
    MOTION_SOURCES,
    SlideshowReport,
    assess_slideshow_risk,
    is_static_artifact,
)
from shared.schemas import BeatArtifact, VideoSource


def _art(idx: int, source: VideoSource, *, has_video: bool = True) -> BeatArtifact:
    return BeatArtifact(
        idx=idx,
        first_frame_path=Path(f"/tmp/frame-{idx}.jpg"),
        video_path=Path(f"/tmp/clip-{idx}.mp4") if has_video else None,
        source=source,
        duration_s=5.0,
    )


# =============================================================================
# 1. is_static_artifact
# =============================================================================


def test_is_static_real_motion_stock_is_not_static():
    assert is_static_artifact(_art(0, VideoSource.PEXELS)) is False


def test_is_static_veo_motion_is_not_static():
    assert is_static_artifact(_art(0, VideoSource.VEO)) is False


def test_is_static_gemini_image_with_video_is_static():
    # Imagen + ken-burns = still-derived → slideshow risk.
    assert is_static_artifact(_art(0, VideoSource.GEMINI_IMAGE)) is True


def test_is_static_local_kenburns_is_static():
    assert is_static_artifact(_art(0, VideoSource.LOCAL)) is True


def test_is_static_missing_video_is_static():
    assert is_static_artifact(_art(0, VideoSource.PEXELS, has_video=False)) is True


# =============================================================================
# 2-8. assess_slideshow_risk
# =============================================================================


def test_all_real_motion_is_ok():
    arts = [_art(i, VideoSource.PEXELS) for i in range(3)]
    report = assess_slideshow_risk(arts, promised_motion=True)
    assert isinstance(report, SlideshowReport)
    assert report.ok is True
    assert report.is_slideshow is False
    assert report.static_ratio == 0.0
    assert report.static_beats == 0


def test_all_placeholder_promised_motion_errors():
    arts = [_art(i, VideoSource.PEXELS, has_video=False) for i in range(3)]
    report = assess_slideshow_risk(arts, promised_motion=True)
    assert report.ok is False
    assert report.placeholder_beats == 3
    assert report.static_ratio == 1.0


def test_all_kenburns_promised_motion_errors():
    arts = [_art(i, VideoSource.LOCAL) for i in range(3)]
    report = assess_slideshow_risk(arts, promised_motion=True)
    assert report.ok is False
    assert report.is_slideshow is True


def test_mixed_under_threshold_is_ok():
    # 2 movimiento + 1 still = 0.33 < 0.6 umbral.
    arts = [
        _art(0, VideoSource.PEXELS),
        _art(1, VideoSource.VEO),
        _art(2, VideoSource.GEMINI_IMAGE),
    ]
    report = assess_slideshow_risk(arts, promised_motion=True, max_static_ratio=0.6)
    assert report.ok is True
    assert abs(report.static_ratio - 1 / 3) < 1e-9


def test_mixed_over_threshold_errors():
    # 1 movimiento + 2 still = 0.66 > 0.6 umbral.
    arts = [
        _art(0, VideoSource.PEXELS),
        _art(1, VideoSource.GEMINI_IMAGE),
        _art(2, VideoSource.LOCAL),
    ]
    report = assess_slideshow_risk(arts, promised_motion=True, max_static_ratio=0.6)
    assert report.ok is False
    assert report.is_slideshow is True


def test_not_promised_motion_never_slideshow_error():
    arts = [_art(i, VideoSource.LOCAL) for i in range(3)]
    report = assess_slideshow_risk(arts, promised_motion=False)
    assert report.is_slideshow is False
    assert report.ok is True  # stills OK cuando no se prometió movimiento


def test_not_promised_motion_still_warns_on_placeholder():
    arts = [_art(i, VideoSource.PEXELS, has_video=False) for i in range(3)]
    report = assess_slideshow_risk(arts, promised_motion=False)
    # Placeholder = render roto: avisa aunque no sea "slideshow".
    assert len(report.result.warnings) >= 1
    assert report.is_slideshow is False


def test_empty_artifacts_is_ok():
    report = assess_slideshow_risk([], promised_motion=True)
    assert report.ok is True
    assert report.n_beats == 0
    assert report.static_ratio == 0.0


def test_low_source_diversity_with_stills_warns():
    arts = [_art(i, VideoSource.GEMINI_IMAGE) for i in range(4)]
    report = assess_slideshow_risk(arts, promised_motion=True)
    assert report.distinct_sources == 1
    # Sobre umbral + baja diversidad → al menos un error y un warning de diversidad.
    assert report.ok is False
    assert any("diversidad" in i.message.lower() for i in report.result.issues)


def test_motion_sources_constant_excludes_image_generators():
    assert VideoSource.PEXELS in MOTION_SOURCES
    assert VideoSource.VEO in MOTION_SOURCES
    assert VideoSource.HIGGSFIELD_DOP in MOTION_SOURCES
    assert VideoSource.GEMINI_IMAGE not in MOTION_SOURCES
    assert VideoSource.LOCAL not in MOTION_SOURCES
