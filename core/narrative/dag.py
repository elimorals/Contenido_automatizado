"""DAG del topic path — la ÚNICA copia.

hunters (4, paralelo) → critic (top 3) → narrators (3, paralelo) → slop gate
(opt-in) → judge. Antes estaba copiado en `pipeline.py` y dos veces en
`apps/api/main.py`, y las copias de la API ya habían perdido el slop gate y el
brief de referencia.

Los índices que devuelve el LLM (critic, judge) se validan aquí: un
`winner_idx=-1` elegía en silencio al último candidato y uno fuera de rango
tumbaba el reel después de pagar 9 llamadas.
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from core.narrative.critic import pick_top_essences
from core.narrative.hunters import hunt_all_parallel
from core.narrative.judge import pick_best_narration
from core.narrative.narrator import write_narrations
from core.narrative.runtime import app as narrative_app
from shared.config import load_config
from shared.schemas import (
    ConversationalScript,
    Essence,
    EssenceCandidate,
    PairwiseVerdict,
)

PhaseHook = Callable[[str, dict[str, float]], Awaitable[None]]


@dataclass
class TopicDagResult:
    candidates: list[EssenceCandidate]
    top_essences: list[EssenceCandidate]
    scripts: list[ConversationalScript]
    verdict: PairwiseVerdict
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def winner_script(self) -> ConversationalScript:
        return self.scripts[self.verdict.winner_idx]

    @property
    def winner_essence(self) -> Essence:
        c = self.top_essences[self.verdict.winner_idx]
        return Essence(
            core_claim=c.core_claim,
            mechanism=c.mechanism,
            evidence=c.evidence,
            content_mode=c.content_mode,
            domain=c.domain,
        )


def valid_top_indices(indices: list[int], n: int, k: int = 3) -> list[int]:
    """Índices del critic en rango y sin duplicados; rellena con los primeros si faltan."""
    out = [i for i in dict.fromkeys(indices) if 0 <= i < n]
    out += [i for i in range(n) if i not in out]
    return out[: min(k, n)]


async def run_topic_dag(
    topic: str,
    *,
    reference_brief: Any = None,
    timings: dict[str, float] | None = None,
    on_phase: PhaseHook | None = None,
) -> TopicDagResult:
    """Corre el DAG completo. `on_phase(nombre, timings)` tras cada fase (progreso)."""
    timings = timings if timings is not None else {}

    async def _phase(name: str, coro: Awaitable[Any]) -> Any:
        t = time.perf_counter()
        try:
            return await coro
        finally:
            timings[name] = time.perf_counter() - t
            if on_phase is not None:
                await on_phase(name, timings)

    candidates = await _phase("hunters", hunt_all_parallel(narrative_app, topic))
    if not candidates:
        raise RuntimeError(f"hunters no devolvieron candidatos para {topic!r}")

    critic = await _phase("critic", pick_top_essences(narrative_app, topic, candidates))
    top = [candidates[i] for i in valid_top_indices(critic.top_3_indices, len(candidates))]

    scripts = await _phase("narrators", write_narrations(narrative_app, top, reference_brief))
    scripts, top = _slop_gate(scripts, top)

    verdict = await _phase("judge", pick_best_narration(narrative_app, topic, scripts, top))
    if not 0 <= verdict.winner_idx < len(scripts):
        logger.warning(f"[dag] judge winner_idx={verdict.winner_idx} fuera de rango; uso 0")
        verdict = verdict.model_copy(update={"winner_idx": 0})

    return TopicDagResult(candidates, top, scripts, verdict, timings)


def _slop_gate(
    scripts: list[ConversationalScript], top: list[EssenceCandidate]
) -> tuple[list[ConversationalScript], list[EssenceCandidate]]:
    """Descarta narraciones AI-slop antes del Judge (opt-in, $0, nunca vacía)."""
    q = load_config().quality
    if not q.slop_gate_enabled or len(scripts) <= 1:
        return scripts, top
    try:
        from core.quality import filter_slop

        kept, reports = filter_slop(
            [s.narration for s in scripts],
            extra_banned=q.slop_extra_banned,
            density_threshold=q.slop_density_threshold,
        )
        for i, r in enumerate(reports):
            logger.info(f"[dag] slop[{i}] {r.note()}")
        if 0 < len(kept) < len(scripts):
            logger.info(f"[dag] slop gate: {len(scripts) - len(kept)} descartado(s)")
            return [scripts[i] for i in kept], [top[i] for i in kept]
    except Exception as e:  # noqa: BLE001 — el gate nunca tumba el pipeline
        logger.warning(f"[dag] slop gate falló (non-fatal): {e}")
    return scripts, top


__all__ = ["TopicDagResult", "run_topic_dag", "valid_top_indices"]
