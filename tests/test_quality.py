"""Tests para core/quality/ — slop detection + plateau detection.

Cubre:
1. score_slop: texto limpio, tier1/2/3, bilingüe EN+ES, insensible a acentos,
   density/flag, extra_banned, edge cases (vacío).
2. rank_by_slop / filter_slop: orden cleanest-first, garantía keep_min.
3. PlateauDetector: mejora continua no para, meseta para en la ronda correcta,
   target temprano, modo "min".
4. detect_plateau (forma funcional).
5. Wiring de config: QualityConfig default + override por env var.

Todo puro y determinista — sin LLM, sin I/O.
"""
from __future__ import annotations

import pytest

from core.quality import (
    PlateauDetector,
    SlopReport,
    detect_plateau,
    filter_slop,
    rank_by_slop,
    score_slop,
)

# ──────────────────────────────────────────────────────────────────────────
# score_slop
# ──────────────────────────────────────────────────────────────────────────

def test_clean_text_is_not_flagged() -> None:
    r = score_slop("En 1923 un relojero suizo descubrió por accidente el cuarzo.")
    assert isinstance(r, SlopReport)
    assert r.total_hits == 0
    assert r.penalty == 0.0
    assert r.flagged is False
    assert r.note() == "slop: clean"


def test_empty_text() -> None:
    r = score_slop("")
    assert r.word_count == 0
    assert r.flagged is False
    assert r.total_hits == 0


def test_tier1_banned_word_flags_regardless_of_density() -> None:
    # Un solo "delve" en un texto largo → density baja, pero tier1 fuerza flag.
    text = "Let us delve " + "word " * 200
    r = score_slop(text)
    assert "delve" in r.tier1_hits
    assert r.flagged is True  # tier1 siempre marca


def test_tier2_suspicious_words_counted() -> None:
    r = score_slop("a robust, seamless and scalable solution")
    assert set(r.tier2_hits) == {"robust", "seamless", "scalable"}
    assert not r.tier1_hits


def test_tier3_filler_phrase_english() -> None:
    r = score_slop("It's worth noting that this matters. Let's dive into it.")
    assert len(r.tier3_hits) >= 2


def test_spanish_slop_detected() -> None:
    r = score_slop("Cabe destacar que debemos sumergirnos en este fascinante tapiz.")
    # "sumergirnos" + "tapiz" (tier1) y "cabe destacar que" (tier3)
    assert r.tier1_hits
    assert r.tier3_hits
    assert r.flagged is True


def test_accent_insensitive_matching() -> None:
    # "plétora" con acento debe matchear la entrada normalizada "pletora".
    accented = score_slop("una plétora de opciones")
    plain = score_slop("una pletora de opciones")
    assert accented.tier1_hits == plain.tier1_hits == ["pletora"]


def test_density_threshold_controls_flag() -> None:
    text = "robust comprehensive seamless"  # 3 tier2 = penalty 3 / 3 words = 100/100
    lax = score_slop(text, density_threshold=1000.0)
    strict = score_slop(text, density_threshold=2.0)
    assert lax.flagged is False
    assert strict.flagged is True


def test_extra_banned_words() -> None:
    r = score_slop("our brand is amazing", extra_banned=["amazing"])
    assert "amazing" in r.tier1_hits
    assert r.flagged is True


def test_word_boundaries_no_substring_false_positive() -> None:
    # "myriad" no debe matchear dentro de otra palabra arbitraria.
    r = score_slop("the undelved cavern was robustly built")  # 'delve' substring guard
    assert "delve" not in r.tier1_hits


def test_note_is_compact_and_informative() -> None:
    r = score_slop("delve into the robust tapestry")
    note = r.note()
    assert "banned" in note
    assert "⚑" in note  # flagged


# ──────────────────────────────────────────────────────────────────────────
# rank_by_slop / filter_slop
# ──────────────────────────────────────────────────────────────────────────

def test_rank_by_slop_cleanest_first() -> None:
    texts = [
        "delve into the multifaceted tapestry",  # slop-heavy
        "In 1969 the lander touched down at 20:17 UTC.",  # clean
        "a robust solution",  # mild
    ]
    ranked = rank_by_slop(texts)
    order = [i for i, _ in ranked]
    assert order[0] == 1  # cleanest first
    assert order[-1] == 0  # sloppiest last


def test_rank_is_stable_on_ties() -> None:
    texts = ["clean one", "clean two", "clean three"]
    order = [i for i, _ in rank_by_slop(texts)]
    assert order == [0, 1, 2]  # empate → orden de entrada


def test_filter_slop_drops_flagged_keeps_clean() -> None:
    texts = [
        "delve into the tapestry of synergy",  # flagged (tier1)
        "A clean specific sentence about 1923.",  # clean
    ]
    kept, reports = filter_slop(texts)
    assert kept == [1]
    assert len(reports) == 2  # reports alineados a entrada


def test_filter_slop_never_returns_empty() -> None:
    # Todos flagged → keep_min cleanest sobreviven.
    texts = [
        "delve delve myriad tapestry",  # muy sloppy
        "robust seamless synergy holistic",  # flagged también
    ]
    kept, _ = filter_slop(texts, keep_min=1)
    assert len(kept) == 1  # nunca vacío


def test_filter_slop_keep_min_two() -> None:
    texts = ["delve tapestry", "myriad synergy", "robust seamless"]
    kept, _ = filter_slop(texts, keep_min=2)
    assert len(kept) == 2


# ──────────────────────────────────────────────────────────────────────────
# PlateauDetector
# ──────────────────────────────────────────────────────────────────────────

def test_continuous_improvement_never_stops() -> None:
    det = PlateauDetector(patience=2, min_delta=0.1)
    stops = [det.update(s) for s in [5.0, 6.0, 7.0, 8.0]]
    assert not any(stops)
    assert det.best == 8.0
    assert det.should_stop is False


def test_plateau_stops_after_patience() -> None:
    det = PlateauDetector(patience=2, min_delta=0.1)
    # 7.0 (best), 7.0 (no mejora #1), 7.05 (<delta, no mejora #2) → stop
    results = [det.update(s) for s in [7.0, 7.0, 7.05]]
    assert results == [False, False, True]
    assert det.reason == "plateau"
    assert det.best == 7.0
    assert det.best_round == 0


def test_min_delta_requires_real_improvement() -> None:
    det = PlateauDetector(patience=1, min_delta=0.5)
    # 6.0 best, 6.3 mejora < 0.5 → cuenta como no-mejora → patience=1 → stop
    assert det.update(6.0) is False
    assert det.update(6.3) is True
    assert det.reason == "plateau"


def test_target_stops_early() -> None:
    det = PlateauDetector(patience=5, min_delta=0.1, target=8.5)
    assert det.update(7.0) is False
    assert det.update(8.6) is True
    assert det.reason == "target"


def test_min_mode_lower_is_better() -> None:
    det = PlateauDetector(patience=1, min_delta=0.1, mode="min")
    assert det.update(5.0) is False       # best
    assert det.update(4.0) is False       # improved (lower)
    assert det.update(4.0) is True        # no improvement → plateau
    assert det.best == 4.0


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        PlateauDetector(patience=0)
    with pytest.raises(ValueError):
        PlateauDetector(min_delta=-1.0)


# ──────────────────────────────────────────────────────────────────────────
# detect_plateau (functional)
# ──────────────────────────────────────────────────────────────────────────

def test_detect_plateau_returns_stop_index() -> None:
    idx = detect_plateau([7.0, 7.0, 7.0], patience=2, min_delta=0.1)
    assert idx == 2


def test_detect_plateau_none_when_always_improving() -> None:
    idx = detect_plateau([1.0, 2.0, 3.0, 4.0], patience=2, min_delta=0.1)
    assert idx is None


def test_detect_plateau_target() -> None:
    idx = detect_plateau([5.0, 9.0, 9.0], patience=5, min_delta=0.1, target=8.0)
    assert idx == 1  # cruza target en la ronda 1


# ──────────────────────────────────────────────────────────────────────────
# config wiring
# ──────────────────────────────────────────────────────────────────────────

def test_quality_config_defaults() -> None:
    from shared.config import QualityConfig

    q = QualityConfig()
    assert q.slop_gate_enabled is False  # opt-in
    assert q.slop_density_threshold == 2.0
    assert q.plateau_patience == 2
    assert q.plateau_min_delta == 0.1


def test_quality_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.config import reload_config

    monkeypatch.setenv("QUALITY_SLOP_GATE_ENABLED", "true")
    monkeypatch.setenv("QUALITY_SLOP_DENSITY_THRESHOLD", "3.5")
    monkeypatch.setenv("QUALITY_PLATEAU_PATIENCE", "4")
    try:
        cfg = reload_config()
        assert cfg.quality.slop_gate_enabled is True
        assert cfg.quality.slop_density_threshold == 3.5
        assert cfg.quality.plateau_patience == 4
    finally:
        reload_config()  # restaura cache limpia para otros tests
