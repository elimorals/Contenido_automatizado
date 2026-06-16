"""Four parallel hunters that generate candidate viral claims from a topic.

Each hunter has a DIFFERENT angle-constrained system prompt that forces
the model out of its safe attractor. Without these constraints, asking
an LLM "tell me something interesting about philosophy" reliably returns
Plato's cave / trolley problem / ship of Theseus. The four angle prompts
force four different generative regions.

  • specific_figure  →  a NAMED person most people haven't heard of
  • reversal         →  the obvious interpretation is BACKWARDS
  • temporal         →  a specific year/event reframes the field
  • cross_domain     →  unexpected bridge to another field entirely

Each hunter runs at temperature=1.1 for diversity. All four run in
parallel via asyncio.gather. Total: 12 candidate essences per call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import EssenceCandidate

from core.narrative.runtime import reel


# ─────────────────────────────────────────────────────────────────────
# NOTE: `HuntBatch` no existe en `shared/schemas.py` — es un wrapper local
# trivial usado solo por los hunters para forzar 3-candidate outputs en la
# llamada a `.ai()`. Se mantiene aquí por ahora; si lo necesitan otros
# reasoners, prometerlo a `shared/schemas.py`.
# ─────────────────────────────────────────────────────────────────────
class HuntBatch(BaseModel):
    """One hunter's output: 3 EssenceCandidates."""

    model_config = ConfigDict(extra="forbid")
    candidates: list[EssenceCandidate] = Field(
        ..., min_length=3, max_length=3,
    )


# Shared anti-cliché block applied to ALL hunters. The model is told
# explicitly which examples are off-limits so it has to dig deeper.
_ANTI_CLICHE = """
HARD ANTI-CLICHÉ RULES (any claim that resembles these is auto-rejected):
  • Plato's cave, the trolley problem, the ship of Theseus, Mary's room,
    the brain in the vat, the chinese room, the simulation hypothesis.
  • Quantum entanglement explainers, "edge of the universe", generic
    "consciousness is mysterious" mood pieces.
  • Evolutionary just-so stories with no named researcher.
  • "Did you know your brain ___" health-influencer mood content.
  • Any claim that begins "Most people don't realize…" without naming
    a specific source.

If your candidate fits any of these patterns, scrap it and try again.
"""


def _facts_block() -> str:
    """Bloque anti-alucinación inyectado en system prompts.

    Carga editorial/facts.json via `core.editorial.loader`. Si la capa editorial
    no está disponible (módulo ausente, error de import), devuelve string vacío
    — los hunters siguen funcionando como antes.
    """
    try:
        from core.editorial.loader import facts_anti_hallucination_block
        return "\n" + facts_anti_hallucination_block() + "\n"
    except Exception:  # noqa: BLE001
        return ""


_OUTPUT_SHAPE = """
OUTPUT: exactly 3 EssenceCandidates. Each:
  • core_claim       — ≤25 words, specific (named entity / specific
                        number / specific year). NOT a vague generality.
  • mechanism        — 1-2 sentences explaining WHY/HOW it's true. This
                        is the body of the future narration.
  • evidence         — 1-3 verifiable specifics: named person + year,
                        study with sample size, documented event. Reject
                        "studies show" / "scientists found" without
                        names.
  • domain           — one word.
  • angle            — your hunter angle (set by the code).
  • novelty_pitch    — one sentence: WHY most people haven't heard this.
                        If you can't argue novelty, the claim is dead.
"""


def _specific_figure_system() -> str:
    return f"""You are a viral-content researcher. Find 3 surprising claims
about the user's TOPIC that involve a specific NAMED person most viewers
haven't heard of.

For each: name the person (not "a scientist", an actual name). Give the
year their finding/work/event happened. State what they did or proved or
discovered. The person should be real and the claim should be verifiable.

GOOD examples (across topics):
  • "Lynn Margulis, 1967 — proposed that mitochondria were captured
    bacteria. Every cell in your body is a colony."
  • "Hapheastion of Macedonia, 324 BCE — Alexander the Great's
    grief shaped the timeline of Greek philosophy."
  • "Ronald Coase, 1937 — explained why companies exist at all using
    transaction costs."

BAD examples:
  • "Aristotle said…" (too famous)
  • "Some philosophers argue…" (no name)
  • "Plato, 380 BCE" (overdone)

{_ANTI_CLICHE}
{_facts_block()}
{_OUTPUT_SHAPE}
"""


def _reversal_system() -> str:
    return f"""You are a viral-content researcher. Find 3 claims about the
user's TOPIC where the COMMON interpretation is wrong or backwards.

For each: name what most people believe, then state what's actually true,
then point to a specific person or study that established the reversal.
The reversal should be genuine — not a contrarian take, but something
the field actually figured out.

GOOD examples (across topics):
  • "People think humans evolved bigger brains for hunting — actually
    Suzana Herculano-Houzel showed in 2012 it was for COOKING."
  • "Everyone thinks the Wright brothers invented controlled flight —
    Alphonse Pénaud built a working flying model in 1871."

BAD examples:
  • "Actually, free will is an illusion" (not a real reversal, just
    a take)
  • "Most people don't realize X" without naming the source

{_ANTI_CLICHE}
{_facts_block()}
{_OUTPUT_SHAPE}
"""


def _temporal_system() -> str:
    return f"""You are a viral-content researcher. Find 3 claims about the
user's TOPIC tied to a specific YEAR or specific EVENT that reframes how
we see the field.

For each: name the year, name the event, explain why this single moment
matters more than people realize. The event can be a paper, a discovery,
a death, a political turn — anything specific and datable.

GOOD examples (across topics):
  • "1996 — the bioethics committee approved CRISPR cas9 trials in
    primates, three years before the patent fight that shaped modern
    biotech ownership."
  • "1922 — the year Niels Bohr received the Nobel changed what every
    physics PhD studied for the next 40 years, because his model was
    already obsolete."

BAD:
  • "In the 1960s, philosophy changed" (no specific year/event)
  • "In 1969, humans landed on the moon" (overdone)

{_ANTI_CLICHE}
{_facts_block()}
{_OUTPUT_SHAPE}
"""


def _cross_domain_system() -> str:
    return f"""You are a viral-content researcher. Find 3 claims about the
user's TOPIC that connect surprisingly to a DIFFERENT field entirely.
Philosophy ↔ economics. Biology ↔ poetry. Physics ↔ theology. The bridge
should be real and the connection genuinely useful to know.

For each: name the topic field, name the other field, state the
surprising bridge, give a specific person or paper that built it.

GOOD examples (across topics):
  • "Game theory ↔ chimpanzee politics — Frans de Waal's 1982 work
    showed Machiavellian strategies in primates predict human voting
    behaviour."
  • "Topology ↔ ancient Hindu rope tricks — Vaughan Jones's knot
    polynomials trace back to mathematical problems Indian rope-walkers
    solved by intuition 800 years ago."

BAD:
  • "Math is everywhere in nature" (too generic)
  • "Philosophy is connected to all other fields" (vague)

{_ANTI_CLICHE}
{_facts_block()}
{_OUTPUT_SHAPE}
"""


# ───── Per-hunter ai() calls ────────────────────────────────────────


async def _hunt(
    app: Any, topic: str, system: str, angle: str,
) -> list[EssenceCandidate]:
    user = (
        f"TOPIC: {topic}\n\n"
        f"Generate 3 EssenceCandidates that fit your hunter angle. "
        f"Stretch — the goal is candidates the viewer has NEVER seen "
        f"on any reel before. Specificity beats cleverness. If you can "
        f"name a person + a year + a result, the candidate is alive. "
        f"Otherwise it's dead."
    )
    batch: HuntBatch = await app.ai(
        system=system,
        user=user,
        schema=HuntBatch,
        temperature=1.1,
    )
    # Force the angle field — the model sometimes invents wrong values.
    return [c.model_copy(update={"angle": angle}) for c in batch.candidates]


@reel.reasoner("hunt_specific_figure")
async def hunt_specific_figure(app: Any, topic: str) -> list[EssenceCandidate]:
    """3 claims involving a named person most viewers haven't heard of."""
    return await _hunt(app, topic, _specific_figure_system(), "specific_figure")


@reel.reasoner("hunt_reversal")
async def hunt_reversal(app: Any, topic: str) -> list[EssenceCandidate]:
    """3 claims where the common interpretation is backwards."""
    return await _hunt(app, topic, _reversal_system(), "reversal")


@reel.reasoner("hunt_temporal")
async def hunt_temporal(app: Any, topic: str) -> list[EssenceCandidate]:
    """3 claims tied to a specific year/event that reframes the field."""
    return await _hunt(app, topic, _temporal_system(), "temporal")


@reel.reasoner("hunt_cross_domain")
async def hunt_cross_domain(app: Any, topic: str) -> list[EssenceCandidate]:
    """3 claims bridging the topic to an unexpected other field."""
    return await _hunt(app, topic, _cross_domain_system(), "cross_domain")


# ───── Convenience parallel runner (used by orchestrator) ────────────


async def hunt_all_parallel(
    app: Any, topic: str,
) -> list[EssenceCandidate]:
    """Run all 4 hunters in parallel; return the merged 12 candidates."""
    results = await asyncio.gather(
        hunt_specific_figure(app, topic),
        hunt_reversal(app, topic),
        hunt_temporal(app, topic),
        hunt_cross_domain(app, topic),
        return_exceptions=False,
    )
    return [c for batch in results for c in batch]
