"""Tests del scoring de proveedor auditable (ADR-027).

7 dimensiones ponderadas + audit trail de alternativas. El scorer NO reemplaza
la heurística del selector; produce una decisión trazable (por qué se eligió X y
qué alternativas se consideraron). Tests sobre propiedades, no constantes mágicas.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.visual.provider_scoring import (
    DecisionLog,
    ProviderScore,
    ScoreWeights,
    decide_provider,
    score_providers,
)
from shared.schemas import VideoSource


def _ctx(**kw: object) -> dict:
    base = {"beat_role": "mechanism", "mode": "premium", "motion_required": False}
    base.update(kw)
    return base


def test_score_weights_sum_to_one() -> None:
    w = ScoreWeights()
    total = (
        w.task_fit + w.quality + w.control + w.reliability + w.cost + w.latency + w.continuity
    )
    assert total == pytest.approx(1.0)


def test_score_providers_returns_one_score_per_candidate_in_range() -> None:
    cands = [VideoSource.PEXELS, VideoSource.VEO, VideoSource.GEMINI_IMAGE]
    scores = score_providers(cands, _ctx())
    assert len(scores) == 3
    assert {s.provider for s in scores} == {c.value for c in cands}
    assert all(0.0 <= s.score <= 1.0 for s in scores)
    # cada score reporta las 7 dimensiones y una razón
    for s in scores:
        assert isinstance(s, ProviderScore)
        assert s.reasons


def test_decide_picks_highest_score() -> None:
    cands = [VideoSource.PEXELS, VideoSource.VEO]
    decision = decide_provider(cands, _ctx())
    best = max(score_providers(cands, _ctx()), key=lambda s: s.score)
    assert decision.chosen == best.provider


def test_decide_records_all_alternatives_sorted_desc() -> None:
    cands = [VideoSource.PEXELS, VideoSource.VEO, VideoSource.HIGGSFIELD_DOP]
    decision = decide_provider(cands, _ctx())
    assert len(decision.alternatives) == 3
    scores = [a.score for a in decision.alternatives]
    assert scores == sorted(scores, reverse=True)
    assert decision.alternatives[0].provider == decision.chosen


def test_motion_required_favors_motion_source_over_still() -> None:
    # Con motion_required, un generador de motion (VEO) debe puntuar task_fit
    # >= que una imagen estática (GEMINI_IMAGE) que necesitaría ken-burns.
    ctx = _ctx(motion_required=True)
    scores = {s.provider: s for s in score_providers([VideoSource.VEO, VideoSource.GEMINI_IMAGE], ctx)}
    assert scores[VideoSource.VEO.value].dimensions.task_fit >= scores[
        VideoSource.GEMINI_IMAGE.value
    ].dimensions.task_fit


def test_cost_dimension_favors_free_stock() -> None:
    # En la dimensión costo, el stock gratis debe superar a un generador de pago.
    scores = {s.provider: s for s in score_providers([VideoSource.PEXELS, VideoSource.VEO], _ctx())}
    assert scores[VideoSource.PEXELS.value].dimensions.cost > scores[VideoSource.VEO.value].dimensions.cost


def test_custom_weights_change_ranking() -> None:
    cands = [VideoSource.PEXELS, VideoSource.VEO]
    # Peso total en costo → PEXELS (gratis) debe ganar.
    cost_only = ScoreWeights(
        task_fit=0.0, quality=0.0, control=0.0, reliability=0.0, cost=1.0, latency=0.0, continuity=0.0
    )
    decision = decide_provider(cands, _ctx(), weights=cost_only)
    assert decision.chosen == VideoSource.PEXELS.value


def test_decision_log_serializes_with_reasons() -> None:
    log = DecisionLog()
    decision = decide_provider([VideoSource.PEXELS, VideoSource.VEO], _ctx(), label="beat_0")
    log.record(decision)
    data = log.to_dict()
    assert len(data["decisions"]) == 1
    d0 = data["decisions"][0]
    assert d0["chosen"]
    assert d0["label"] == "beat_0"
    assert len(d0["alternatives"]) == 2
    assert "reasons" in d0["alternatives"][0]


def test_decision_log_save_writes_json(tmp_path: Path) -> None:
    log = DecisionLog()
    log.record(decide_provider([VideoSource.PEXELS], _ctx(), label="b0"))
    out = tmp_path / "decisions.json"
    log.save(out)
    assert out.exists()
    loaded = json.loads(out.read_text())
    assert loaded["decisions"][0]["label"] == "b0"


def test_decide_empty_candidates_raises() -> None:
    with pytest.raises(ValueError):
        decide_provider([], _ctx())
