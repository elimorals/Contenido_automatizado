"""Critic — picks the top 3 EssenceCandidates from the 12 hunters produced.

Runs at lower temperature (0.5) so judgment is consistent. Scores each
candidate on four dimensions and picks a DIVERSE top-3 (not just three
flavors of the same angle).
"""

from __future__ import annotations

from typing import Any

from shared.schemas import CriticOutput, EssenceCandidate

from core.narrative.runtime import reel


# NOTE: el `CriticOutput` unificado en shared/schemas.py expone
# `top_3_indices` (no `chosen_indices` como en reels-af original). El
# prompt se actualiza para alinear con el nuevo nombre del campo.
_SYSTEM = """You are a viral-content editor. You're picking the 3 most
viral candidate claims from a list. Score each on these four dimensions:

  • novelty       (1-10) — how many viewers have heard this? 10 = almost
                          nobody. 1 = it's all over Reddit.
  • specificity   (1-10) — does it name a person, year, number, place?
                          10 = "Lynn Margulis, 1967, mitochondria as
                          captured bacteria"; 1 = "philosophers argue".
  • hookability   (1-10) — would a scrolling thumb stop in 1 second
                          if you said this aloud? 10 = visceral wait-
                          what; 1 = headline drone.
  • narratability (1-10) — does it work as a 25-30s spoken story?
                          10 = clear hook + mechanism + payoff; 1 =
                          requires diagrams or jargon.

composite = a 1-10 number reflecting OVERALL viral pick — not a literal
average. A claim that's 10/10 novel but 4/10 narratable scores 6-7,
not 9. You're picking what will WORK on screen.

Then pick the top 3 indices. CRITICAL: prefer DIVERSITY across hunter
angles. If specific_figure had the top 2, the third pick should be a
non-specific_figure candidate even if its composite is slightly lower.
Three same-flavored claims make a boring reel run.

Return:
  rankings        — score every candidate (one RankedItem each).
  top_3_indices   — the 3 candidate indices you pick, ordered best→third.
"""


def _format_candidates(candidates: list[EssenceCandidate]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        lines.append(
            f"[{i}] angle={c.angle}\n"
            f"    claim     : {c.core_claim}\n"
            f"    mechanism : {c.mechanism}\n"
            f"    evidence  : {c.evidence}\n"
            f"    domain    : {c.domain}\n"
            f"    novelty   : {c.novelty_pitch}\n"
        )
    return "\n".join(lines)


@reel.reasoner("pick_top_essences")
async def pick_top_essences(
    app: Any,
    topic: str,
    candidates: list[EssenceCandidate],
    n: int = 3,
) -> CriticOutput:
    """Rank all candidates; return top N indices in top_3_indices."""
    user = (
        f"TOPIC: {topic}\n\n"
        f"CANDIDATES (n={len(candidates)}):\n\n"
        f"{_format_candidates(candidates)}\n\n"
        f"Score every candidate. Then return the top {n} indices in "
        f"top_3_indices. Prefer angle DIVERSITY when scores are close."
    )
    return await app.ai(
        system=_SYSTEM,
        user=user,
        schema=CriticOutput,
        temperature=0.5,
    )
