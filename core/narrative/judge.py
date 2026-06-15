"""Pairwise judge — picks the most viral narration from N candidates.

Why pairwise instead of absolute scoring: research consistently shows
LLM judges are much better at "A or B?" than at "is this a 7 or an 8?"
For more than 2 candidates we compare them in one prompt and ask for
a single winner index — the model can hold all options simultaneously
and trade them off.
"""

from __future__ import annotations

from typing import Any

from shared.schemas import (
    ConversationalScript,
    EssenceCandidate,
    PairwiseVerdict,
)

from core.narrative.runtime import reel

_SYSTEM = """You're picking the BEST of several complete 25-second
vertical-reel narrations. Pairwise comparison — pick the one most
likely to stop a scrolling thumb, hold attention to the close, and
get rewatched.

Priority order (apply in sequence; only break ties on a later rule):

  1. Hook strength — would a thumb stop in <1 second?
  2. Specificity  — named entity / number / year (vague claims lose)
  3. Loop-back execution — does the last line genuinely echo the hook?
  4. Trope avoidance — drops clichés ("studies show", "did you know")
  5. Spoken flow — varied sentence length, no jargon walls
  6. Stake/relevance — would the viewer feel they've gained something?

Ties go to the MORE SPECIFIC candidate (named person + year beats a
generic "researchers showed"). Return the index of the winner, a
composite quality score from 1-10, and one or two sentences explaining
what beat what."""


def _user_prompt(
    topic: str,
    scripts: list[ConversationalScript],
    essences: list[EssenceCandidate],
) -> str:
    blocks: list[str] = [f"TOPIC: {topic}\n"]
    for i, (s, e) in enumerate(zip(scripts, essences)):
        blocks.append(
            f"\n══════ CANDIDATE {i} ({e.angle}) ══════\n"
            f"  novelty pitch : {e.novelty_pitch}\n"
            f"  tease         : {s.tease}\n"
            f"  common_belief : {s.common_belief or '(omitted)'}\n"
            f"  reveal        : {s.reveal}\n"
            f"  payoff        : {s.payoff}\n"
            f"\n  full narration:\n  {s.narration}\n"
        )
    blocks.append(
        "\nReturn winner_idx (0-based), composite_score 1-10, and why."
    )
    return "".join(blocks)


@reel.reasoner("pick_best_narration")
async def pick_best_narration(
    app: Any,
    topic: str,
    scripts: list[ConversationalScript],
    essences: list[EssenceCandidate],
) -> PairwiseVerdict:
    """One .ai() call. Returns the winning script's index + score."""
    return await app.ai(
        system=_SYSTEM,
        user=_user_prompt(topic, scripts, essences),
        schema=PairwiseVerdict,
    )
