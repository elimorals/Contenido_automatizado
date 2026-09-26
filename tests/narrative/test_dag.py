"""DAG único del topic path: índices del LLM validados (auditoría 2026-09)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.narrative.dag import run_topic_dag, valid_top_indices
from shared.schemas import (
    ConversationalScript,
    CriticOutput,
    EssenceCandidate,
    PairwiseVerdict,
)


def test_valid_top_indices_filters_and_fills():
    assert valid_top_indices([4, 4, 9], n=5) == [4, 0, 1]
    assert valid_top_indices([-1, 2, 1], n=5) == [2, 1, 0]
    assert valid_top_indices([0, 1, 2], n=2) == [0, 1]


def _cand(i: int) -> EssenceCandidate:
    return EssenceCandidate(
        core_claim=f"c{i}", mechanism="m", evidence=["e"], domain="d",
        angle="reversal", novelty_pitch="n",
    )


async def test_invalid_llm_indices_do_not_break_or_mislead():
    conv = ConversationalScript(tease="t", reveal="r", payoff="p", narration="n")
    critic = CriticOutput(top_3_indices=[3, 3, 99], rankings=[])
    verdict = PairwiseVerdict(winner_idx=-1, composite_score=5, why="x")
    with (
        patch("core.narrative.dag.hunt_all_parallel",
              AsyncMock(return_value=[_cand(i) for i in range(4)])),
        patch("core.narrative.dag.pick_top_essences", AsyncMock(return_value=critic)),
        patch("core.narrative.dag.write_narrations", AsyncMock(return_value=[conv] * 3)),
        patch("core.narrative.dag.pick_best_narration", AsyncMock(return_value=verdict)),
    ):
        dag = await run_topic_dag("x")

    assert dag.verdict.winner_idx == 0  # antes: -1 elegía el último en silencio
    assert [c.core_claim for c in dag.top_essences] == ["c3", "c0", "c1"]
    assert set(dag.timings) == {"hunters", "critic", "narrators", "judge"}
