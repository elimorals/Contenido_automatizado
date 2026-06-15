"""Essence → ScriptDraft in ONE .ai() call.

Fixed structure: Hook (6-10 words) → Mechanism (2-4 sentences) → Payoff
(1 sentence ending on a loop-back keyword from the hook). The schema's
loop-back validator (in ``shared.schemas.ScriptDraft``) is the safety net;
the prompt teaches the model to satisfy it on the first try.
"""

from __future__ import annotations

from typing import Any

from shared.schemas import Essence, ScriptDraft

from core.narrative.runtime import reel

# ────────────────────────────────────────────────────────────────────
# Scientific-mode writing guide. Applied when essence.content_mode ==
# "scientific" — research papers / preprints / technical writeups read
# by engineers and the technically-literate public.
# ────────────────────────────────────────────────────────────────────
SCIENTIFIC_WRITING_GUIDE = """\
═══════════════════════════════════════════════════════════════════════════
WRITING GUIDE FOR SCIENTIFIC PAPERS — TECHNICAL AUDIENCE
═══════════════════════════════════════════════════════════════════════════

You are writing a vertical reel about a research paper for an audience of
ENGINEERS and the TECHNICALLY-LITERATE PUBLIC. They know what a transformer
is, what gradient descent does, what a benchmark is. They do NOT need
"what is AI" explained. They DO need any acronym or method name introduced
in the paper itself defined inline.

These are principles. Pick the shape that fits the specific paper.

──────────────────────────────────────────────────────────────────────────
PRINCIPLE 1 — LEAD WITH THE RESULT
──────────────────────────────────────────────────────────────────────────
A scientific paper's viral payload is almost always a NUMBER or a CLAIM
demonstrated against a baseline. Open with it.

Bad opener: "Researchers have published a new paper on alignment."
Good opener: "70% jailbreak rate — even after RLHF. Anthropic just showed
              alignment doesn't survive scale."

If the result is qualitative ("we discovered a new failure mode"), still
state the CLAIM up front, not the methodology.

──────────────────────────────────────────────────────────────────────────
PRINCIPLE 2 — USE FIELD JARGON FREELY; DEFINE PAPER-SPECIFIC TERMS
──────────────────────────────────────────────────────────────────────────
The audience already knows: transformer, attention, embedding, gradient,
token, fine-tuning, RL, RLHF, benchmark, parameters, MMLU, HumanEval,
GSM8K. Don't waste a beat defining these.

The audience does NOT know paper-specific things: "we call this BTSP",
"the IM-RoPE rotary scheme", "what we term 'sleeper alignment'". Define
these inline in 5-8 words the FIRST time you use them.

──────────────────────────────────────────────────────────────────────────
PRINCIPLE 3 — SHOW THE INTUITION, NOT THE MATH
──────────────────────────────────────────────────────────────────────────
A paper's actual contribution is usually a clever IDEA dressed in
notation. Translate the idea into one sentence of intuition, then drop
the math name as the label.

──────────────────────────────────────────────────────────────────────────
PRINCIPLE 4 — ACKNOWLEDGE THE BASELINE BRIEFLY
──────────────────────────────────────────────────────────────────────────
Numbers without comparison are noise. "70% accuracy" means nothing
unless you say "vs 40% for the prior SOTA". Always couple a headline
number with what came before. One sentence is enough.

──────────────────────────────────────────────────────────────────────────
PRINCIPLE 5 — CLOSE ON "WHAT DOES THIS ENABLE / BREAK"
──────────────────────────────────────────────────────────────────────────
The close shouldn't be a recap — it should state the consequence. Either
the new thing this unlocks ("this means smaller models can now do X") or
the old assumption this breaks ("this means we were wrong about Y").

══════════════════════════════════════════════════════════════════════════
"""


# ────────────────────────────────────────────────────────────────────
# Tag vocabulary — DELIBERATELY MINIMAL for fast, engaging delivery.
# Gemini honors pause tags with real silence, so more than ~3 tags
# pushes the reel past the 25-second engagement budget. We allow ≤3
# tags total and explicitly ban pause / slow / breath.
# ────────────────────────────────────────────────────────────────────
_TAG_VOCAB = """\
  ALLOWED (use ≤3 total across the whole narration):
    • [curious]   — at the cold open, ONCE
    • [emphasis]  — on the single most surprising word, ONCE
    • [confident] — at the payoff, ONCE (optional)

  BANNED — do NOT use any of these. They insert real silence and
  blow the engagement budget:
    • [pause] [pause short] [pause long] [breath]
    • [slow] [building] [thoughtful] [wonder] [skeptical]
    • [serious] [warm] [whispers] [quiet] [intense] [hopeful]
    • Any other tag that slows delivery

  Trust the punctuation (commas, em-dashes, periods) for rhythm.
  Trust the model's default cadence. Less is more.
"""


def _system_prompt(content_mode: str) -> str:
    if content_mode == "scientific":
        word_target = "45-52 words"
        wpm = 175
        register = (
            "TECHNICAL register. Audience is engineers and the technically-"
            "literate public. Use field jargon freely. Define only paper-"
            "specific terms inline in 5-8 words on first use."
        )
        hook_menu = (
            "For scientific content prefer `authority` or `shock_stat`. "
            "`curiosity_gap` works if the result is genuinely surprising."
        )
        mode_block = (
            "\n──── SCIENTIFIC WRITING GUIDE (applies to the WHOLE script) ────\n"
            f"{SCIENTIFIC_WRITING_GUIDE}\n"
        )
    else:  # general
        word_target = "55-62 words"
        wpm = 180
        register = (
            "CONVERSATIONAL register. Audience is general scrolling viewers. "
            "Plain language; second person where natural. No jargon without "
            "a one-clause translation. TIGHT sentences — no padding."
        )
        hook_menu = (
            "For general content prefer `shock_stat`, `contrarian`, or "
            "`curiosity_gap`. `listicle` only if the article is structurally "
            "a list. `authority` only if the source's expert is the story."
        )
        mode_block = ""

    return f"""You are writing a 25-second vertical reel narration.

The structure is FIXED. Do not deviate.

  1. HOOK            — 6-10 spoken words. Picks ONE variant from:
                       shock_stat | contrarian | authority | curiosity_gap | listicle
                       Declare which variant you chose in `hook_variant`.
                       {hook_menu}

  2. MECHANISM       — 2-4 sentences that explain the WHY behind the hook.
                       Each sentence is a coherent visual beat downstream
                       (one shot per sentence), so each must stand alone.
                       Names, numbers, specific things — not vibes.

  3. PAYOFF + LOOP   — 1 closing sentence. The last 4-8 words MUST echo a
                       distinctive word from your HOOK: a noun, a number,
                       or a named entity — NOT a stopword (not "the", "and",
                       "that", "this", "you"). This is how the viewer loops
                       back to the start. The schema validator checks this
                       literally; if you skip it the call fails.

REGISTER: {register}

TOTAL LENGTH: {word_target}. Set `target_wpm` to {wpm}. The reel lands
at ~20-22s. FAST delivery — every pause is attention you've lost.

──── INLINE TTS TAGS — STRICTLY LIMITED ────
The `narration` field is passed VERBATIM to Gemini 3.1 Flash TTS. Tags
go in [square brackets] BEFORE the clause they modify. Gemini interprets
pause tags as REAL SILENCE — so we ban them.

{_TAG_VOCAB}

PUNCTUATION DISCIPLINE: each comma is ~200ms of silence, em-dash ~300ms,
period ~400ms. If your narration has more than ~5 commas across the
{word_target}, you're slowing it down on purpose. Tighten.

──── ANTI-PATTERNS — instant rejection ────
  • "Hey guys", "Did you know", "In this video", "Today we…"
  • "Thanks for watching", "Don't forget to like", "Smash that subscribe"
  • Generic CTAs ("Follow for more", "Comment below").
  • Fade-out closes that trail into nothing.
  • Hedges in the close ("kind of", "sort of", "might be", "maybe").
  • Padding the word count with filler — tight is better than long.
  • Inventing facts, names, or numbers not in the essence below.

──── OUTPUT FIELDS ────
  hook              : the literal first 6-10 spoken words, punctuated.
  hook_variant      : which canonical shape — for the audit trail.
  mechanism_lines   : list of 2-4 sentences (no leading bullets).
  payoff_line       : the closing sentence, with the loop-back keyword.
  target_wpm        : {wpm}.
  narration         : hook + mechanism + payoff concatenated as ONE string,
                      with inline [tags] inserted. Same words, same order,
                      same punctuation as the structured fields — only tags
                      added. This string is what TTS speaks.
{mode_block}"""


def _user_prompt(essence: Essence) -> str:
    """User payload sourced from Essence."""
    evidence_block = "\n".join(
        f"    {i + 1}. {e}" for i, e in enumerate(essence.evidence)
    )
    return (
        f"ESSENCE (from the source article — use these facts, invent nothing)\n"
        f"  content_mode : {essence.content_mode}\n"
        f"  domain       : {essence.domain}\n"
        f"  core_claim   : {essence.core_claim}\n"
        f"  mechanism    : {essence.mechanism}\n"
        f"  evidence:\n"
        f"{evidence_block}\n\n"
        f"Write the ScriptDraft now. The hook draws from `core_claim`; the "
        f"mechanism_lines unpack `mechanism` using the evidence above; the "
        f"payoff_line lands on a word that callbacks the hook."
    )


@reel.reasoner("compose_script")
async def compose_script(app: Any, essence: Essence) -> ScriptDraft:
    """One .ai() call. Fixed Hook -> Mechanism -> Payoff -> Loop structure.
    Parameterized by content_mode. Inline TTS tags in the narration."""
    return await app.ai(
        system=_system_prompt(essence.content_mode),
        user=_user_prompt(essence),
        schema=ScriptDraft,
    )
