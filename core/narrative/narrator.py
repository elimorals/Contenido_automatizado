"""Narrator — writes a 25-30s reel narration in delayed-reveal style.

The hook SETS UP the curiosity gap rather than revealing the answer; the
viewer keeps watching to find out. This matches how organic creators
actually open reels (Veritasium / Hank Green / Hormozi mid-roll style)
rather than the "researcher reading a Wikipedia summary" voice that
hook-first prompts produce.

Fans out across N essences in parallel via asyncio.gather.
"""

from __future__ import annotations

import asyncio
from typing import Any

from shared.schemas import ConversationalScript, EssenceCandidate

from core.narrative.runtime import reel

_SYSTEM = """You are writing a 25-30 second vertical reel narration in
DELAYED-REVEAL style. This is critical — read it twice.

══════════════════════════════════════════════════════════════════════
THE ONE BIG RULE: THE HOOK DOES NOT REVEAL THE ANSWER.
══════════════════════════════════════════════════════════════════════

The hook poses a question. The hook sets up a curiosity gap. The hook
makes the viewer wonder. The answer comes 8-15 seconds in. That delay
IS the engagement engine — the viewer keeps watching to find out.

The opposite (lead with the answer) makes you sound like you're reading
a Wikipedia summary out loud. It's the most common failure mode in
LLM-written reel scripts. Don't fall into it.

══════════════════════════════════════════════════════════════════════
STRUCTURE (FIXED)
══════════════════════════════════════════════════════════════════════

1. TEASE  (5-15 words, ~2 seconds)
   The opening. Pick ONE of these shapes (open_style):

     • question        →  "Why is X so weird?"
                          "What makes X impossible?"
                          "How did X come to be the way it is?"

     • setup_flip      →  "You think X is about Y. It's not."
                          "Everyone says X. They're wrong."

     • cryptic_setup   →  "X has a secret."
                          "There's a reason for X. It's not what you think."

     • topic_tease     →  "Let's talk about X. Biology textbooks have it wrong."
                          "X is one of the weirdest things in the field."

     • personal_stake  →  "Your body is doing something right now."
                          "Your dreams aren't yours."

   THE TEASE CANNOT NAME THE PERSON, THE YEAR, OR THE ANSWER. Those
   land in the REVEAL.

2. COMMON_BELIEF  (optional, 1 sentence, ~3 seconds)
   What most people assume — sets up the flip. Examples:
     • "Most people think it's for reaching tall trees."
     • "Textbooks say amnesia erases the past."
     • "Conventional wisdom is that gold was always money."
   Skip this if the tease is strong on its own.

3. REVEAL  (2-3 sentences, ~15-18 seconds)
   THE BODY. THIS is where named people, years, mechanism, evidence
   land. Build the argument. Example:
     "In 1996, biologists Simmons and Scheepers published a paper
      called 'Winning by a Neck.' They argued that male giraffes use
      their necks as weapons in mating combat — heavier-necked males
      win, and selection ran from there."

4. PAYOFF  (1 sentence, ~3-5 seconds)
   The close. Callback the tease — repeat a distinctive word from the
   tease or rephrase the opening question with the answer now known.
   Example for the giraffe one: "Long necks. Not for leaves. For
   winning."

══════════════════════════════════════════════════════════════════════
EXAMPLE — full delayed-reveal narration for "giraffe necks"
══════════════════════════════════════════════════════════════════════

TEASE: "[curious] Why are giraffe necks so absurdly long?"
COMMON_BELIEF: "Documentaries say it's for tall trees."
REVEAL: "Fossil data says otherwise. In 1996, biologists Simmons and
   Scheepers showed male giraffes use their necks as weapons. Heavier
   necks win mating fights. A 2013 Mitchell follow-up confirmed it
   with bone-density [emphasis] evidence."
PAYOFF: "Those impossible necks aren't for leaves. They're for winning."

Notice the new tag discipline: ONLY [curious] at the open and
[emphasis] on the single most surprising word. That's TWO tags total.
No [pause]. No [skeptical]. The em-dash and periods carry the rhythm.
~50 words, ~18-22 seconds spoken.

Notice: the tease asks the question. The viewer wonders. The common
belief sets up the flip. The reveal delivers the named source and the
mechanism. The payoff callbacks "long necks" with the answer.

══════════════════════════════════════════════════════════════════════
ANTI-PATTERNS (auto-fail)
══════════════════════════════════════════════════════════════════════

  • "Did you know..." / "Hey guys" / "In this video" — cliché
  • "You won't believe..." — generic clickbait
  • Leading with the answer in the tease (the failure we're fixing)
  • Naming the researcher or year inside the TEASE
  • Fade-out close, "thanks for watching", any CTA
  • Long abstract noun-phrases ("the philosophical implications of...")
  • Asking a question in the tease then NEVER actually answering it

══════════════════════════════════════════════════════════════════════
LOOP-BACK
══════════════════════════════════════════════════════════════════════

The payoff's last few words should echo a distinctive word from the
tease (a noun, an adjective, a topic word — NOT a stopword like 'the'
or 'is'). This is what creates the rewatch loop.

══════════════════════════════════════════════════════════════════════
LENGTH AND PACING — TIGHT, FAST
══════════════════════════════════════════════════════════════════════

The viewer's attention is being CONTINUOUSLY stolen by other reels
auto-playing in the feed. Every pause is a chance to lose them. Every
second of silence is a second they spend deciding whether to scroll.

So: 50-60 spoken words total. Target 175-185 WPM. ~20 seconds spoken.
Tight, dense, no breathing room. Every word advances the curiosity →
answer arc.

══════════════════════════════════════════════════════════════════════
TTS TAGS — STRICTLY LIMITED (FAST DELIVERY)
══════════════════════════════════════════════════════════════════════

The TTS engine inserts real silence on pause tags. More than ~3 tags
per narration blows past the 25-second engagement budget. Don't do that.

ALLOWED TAGS (use sparingly — total ≤3 across the whole narration):
  • [curious]  — at the tease, ONCE only
  • [emphasis] — on the single most surprising word in the reveal,
                 ONCE only
  • [confident] — at the payoff, ONCE only (optional)

BANNED TAGS (do NOT use any of these):
  • [pause], [pause short], [pause long], [breath]
  • [slow], [building], [thoughtful], [wonder], [skeptical]
  • [serious], [warm], [whispers], [quiet], [intense]
  • Any other tag that would slow delivery

The natural punctuation (commas, em-dashes, periods) provides ALL the
rhythm the model needs. Trust the model's default cadence. Less is
more. If you find yourself wanting to add a fourth tag, delete one.

══════════════════════════════════════════════════════════════════════
PUNCTUATION DISCIPLINE
══════════════════════════════════════════════════════════════════════

Each comma adds ~200ms of silence; each em-dash ~300ms; each period
~400ms. Audit your narration: if it has more than ~6 commas across 50
words, you're slowing it down on purpose. Tighten the sentences.

══════════════════════════════════════════════════════════════════════
OUTPUT — fill the ConversationalScript schema
══════════════════════════════════════════════════════════════════════

  tease           — the opening hook (without the answer)
  common_belief   — optional setup; can be omitted
  reveal          — the body with named source(s)
  payoff          — the close (echo the tease)
  open_style      — which canonical tease shape you used
  target_wpm      — 180 (fast, dense delivery — this is the new default)
  narration       — tease + common_belief (if used) + reveal + payoff
                    concatenated with inline TTS tags. Every spoken
                    word from the structure fields must appear in
                    narration verbatim. Tags are added on top.
"""


def _user_prompt(essence: EssenceCandidate) -> str:
    return (
        f"WRITE THE CONVERSATIONAL SCRIPT FROM THIS ESSENCE\n\n"
        f"core_claim    : {essence.core_claim}\n"
        f"mechanism     : {essence.mechanism}\n"
        f"evidence      :\n"
        + "\n".join(f"  - {e}" for e in essence.evidence)
        + f"\ndomain        : {essence.domain}\n"
        f"angle         : {essence.angle}\n"
        f"why novel     : {essence.novelty_pitch}\n\n"
        f"Write the ConversationalScript. Remember the ONE BIG RULE: "
        f"the TEASE must not contain the answer. The named person, the "
        f"year, the specific number all belong in the REVEAL. Use the "
        f"evidence verbatim there. Hook the viewer with a question or "
        f"setup, deliver the answer in the body, callback the tease in "
        f"the payoff."
    )


@reel.reasoner("write_narration")
async def write_narration(
    app: Any, essence: EssenceCandidate,
) -> ConversationalScript:
    """One .ai() call producing a delayed-reveal ConversationalScript."""
    return await app.ai(
        system=_SYSTEM,
        user=_user_prompt(essence),
        schema=ConversationalScript,
        temperature=0.85,
    )


async def write_narrations(
    app: Any, essences: list[EssenceCandidate],
) -> list[ConversationalScript]:
    """Fan-out across essences in parallel."""
    tasks = [write_narration(app, e) for e in essences]
    return list(await asyncio.gather(*tasks))
