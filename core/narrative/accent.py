"""Per-beat editorial accent overlay — one `.ai()` call, biased to None.

Cada beat puede recibir un overlay editorial (UPPERCASE, 2-6 palabras) en el
third opuesto al subtítulo word-burst. La defaults es None — sobre-uso es
peor que falta de overlays.

6 patrones canónicos:
  - number          → "$47,000", "85%", "3 STEPS"
  - named_entity    → "DR. CHEN, STANFORD"
  - jargon_translation → "ENTANGLEMENT = SPOOKY LINK"
  - hook_title_card → ONLY beat.role == "hook"
  - reaction        → "WAIT WHAT", "BIG IF TRUE"
  - list_marker     → "STEP 2 OF 3" (solo listicles estructurados)
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import AccentOverlay, AccentPattern, AccentPosition, Beat, Essence


# La defaults DEBE ser None. Cada parte del prompt y schema fuerza al modelo
# a comprometerse yes/no ANTES de construir el overlay, para que no se deje
# llevar por momentum de overlay-construction.


class _AccentDecision(BaseModel):
    """Wrapper interno: fuerza al modelo a comprometerse yes/no antes de diseñar."""

    model_config = ConfigDict(extra="forbid")

    emit_overlay: bool = Field(
        ...,
        description=(
            "Decide FIRST. Default is false. Only set true if one of the 6 "
            "canonical patterns unambiguously applies to this beat."
        ),
    )
    overlay: Optional[AccentOverlay] = Field(
        default=None,
        description=(
            "ONLY set when emit_overlay is true. Must be null otherwise. "
            "When set, text is 2-6 words; the renderer uppercases it."
        ),
    )
    reasoning: str = Field(
        ...,
        description=(
            "1-2 sentences: which of the 6 patterns this matches and why — "
            "OR why no pattern applies and the beat stays clean. Audit trail."
        ),
    )


_SYSTEM_PROMPT = (
    "You decide whether ONE beat of a vertical reel gets an editorial accent "
    "overlay on top of the verbatim word-by-word subtitles already burned at "
    "the upper-center of the frame.\n\n"
    "THE DEFAULT IS emit_overlay=false. Most beats will not emit. Repeat: "
    "most beats will not emit. Over-cluttering the frame is worse than not "
    "adding accent. If no pattern unambiguously applies, emit_overlay MUST "
    "be false. Do not manufacture overlays to fill a quota.\n\n"
    "Only six canonical patterns are allowed. Concrete examples:\n"
    "  • number — narration mentions a specific number worth locking in for a "
    "muted viewer (\"$47,000\", \"85%\", \"3 STEPS\"). The number must actually "
    "appear in this beat's narration.\n"
    "  • named_entity — a person, place, organization, or product whose "
    "spelling matters (\"DR. CHEN, STANFORD\", \"THE HIPPOCAMPUS\"). Not "
    "generic nouns.\n"
    "  • jargon_translation — the narration uses a domain term and a plain-"
    "English gloss would help muted viewers (\"ENTANGLEMENT = SPOOKY LINK\").\n"
    "  • hook_title_card — ONLY allowed when beat.role == 'hook'. A bold "
    "question or claim, 5-8 words, that becomes the title card.\n"
    "  • reaction — comedic punctuation on a spoken beat (\"WAIT WHAT\", "
    "\"NO WAY\", \"BIG IF TRUE\"). Only when the script clearly has that beat.\n"
    "  • list_marker — only when the script is structured as a numbered "
    "listicle (\"STEP 2 OF 3\", \"1 OF 3\"). Not for generic enumeration.\n\n"
    "Hard rules:\n"
    "  1. If this beat's narration doesn't contain a number to call out, a "
    "named entity that needs spelling, a jargon term being defined, or a "
    "clear emotional / structural beat — emit_overlay MUST be false.\n"
    "  2. hook_title_card is ONLY valid when beat.role == 'hook'. For any "
    "non-hook beat, never use this pattern.\n"
    "  3. Overlay text must be 2-6 words. The renderer uppercases it; you can "
    "write any case.\n"
    "  4. position: hook_title_card → 'upper_third'. Everything else → "
    "'lower_third' (opposite of the subtitles).\n"
    "  5. The overlay must surface information the voiceover does not "
    "explicitly say but the viewer needs to lock in (the number's exact "
    "value, the name's spelling, the comedic beat the audio can't show).\n\n"
    "Output a structured decision: pick emit_overlay first, then either set "
    "overlay to a valid AccentOverlay or leave it null. reasoning explains "
    "which pattern matched, or why none did."
)


def _build_user_prompt(beat: Beat, essence: Essence) -> str:
    evidence = "\n".join(f"  - {e}" for e in essence.evidence)
    return (
        f"BEAT INDEX: {beat.idx}\n"
        f"BEAT ROLE: {beat.role.value}\n"
        f"BEAT TARGET DURATION: {beat.target_duration_s:.2f}s "
        f"(Veo clip: {beat.veo_duration}s)\n"
        f"BEAT NARRATION (verbatim of what's spoken):\n"
        f"  {beat.text!r}\n\n"
        f"CONTENT MODE: {essence.content_mode}\n"
        f"CORE CLAIM: {essence.core_claim}\n"
        f"EVIDENCE:\n{evidence}\n\n"
        f"Decide: does this beat warrant a Layer 2 editorial accent overlay?\n"
        f"Reminder: most beats should return emit_overlay=false."
    )


async def accent_for_beat(
    app: Any,
    beat: Beat,
    essence: Essence,
) -> AccentOverlay | None:
    """Single `.ai()` call. Returns None by default — only emits an overlay
    when one of the 6 canonical patterns is genuinely warranted."""

    decision = await app.ai(
        system=_SYSTEM_PROMPT,
        user=_build_user_prompt(beat, essence),
        schema=_AccentDecision,
    )

    if not decision.emit_overlay or decision.overlay is None:
        return None

    overlay = decision.overlay

    # Enforcement defensivo: hook_title_card SOLO en beat.role == "hook"
    if overlay.pattern == AccentPattern.HOOK_TITLE_CARD and beat.role.value != "hook":
        return None

    # Position rule: hook_title_card → upper_third; resto → lower_third
    if overlay.pattern == AccentPattern.HOOK_TITLE_CARD:
        overlay = overlay.model_copy(update={"position": AccentPosition.UPPER_THIRD})
    elif overlay.position == AccentPosition.UPPER_THIRD:
        overlay = overlay.model_copy(update={"position": AccentPosition.LOWER_THIRD})

    return overlay


async def plan_beat_accents(
    app: Any,
    beats: list[Beat],
    essence: Essence,
) -> list[AccentOverlay | None]:
    """Fan-out via `asyncio.gather`. Returns list aligned to `beats` by index."""
    if not beats:
        return []
    return list(
        await asyncio.gather(
            *(accent_for_beat(app, beat, essence) for beat in beats)
        )
    )
