"""Per-beat visual planner — one `.ai()` call per beat, fanned out in parallel."""

from __future__ import annotations

import asyncio
from typing import Any

from shared.schemas import Beat, BeatVisual, Essence

from core.narrative.runtime import reel


def _role_block(role: str) -> str:
    if role == "hook":
        return (
            "ROLE: HOOK — this is the FIRST beat. It must be the most arresting "
            "visual in the reel — biggest stop-the-scroll energy. Strong subject, "
            "high contrast, one unexpected element. Lean toward slow_zoom_in so "
            "the frame keeps revealing as the hook lands."
        )
    if role == "payoff":
        return (
            "ROLE: PAYOFF — this is the LAST beat. Visually CALLBACK the hook "
            "when possible: same subject reframed, same location after a moment, "
            "or the same object completed. Lean toward `static` so the final "
            "image holds and the viewer loops back to the top of the reel."
        )
    return (
        "ROLE: MECHANISM — this is a BODY beat. Illustrate concretely WHAT "
        "THIS NARRATIVE LINE SAYS. Not mood — the actual thing. Vary motion "
        "across body beats; pick the move that reveals what the line claims."
    )


def _mode_block(content_mode: str, domain: str) -> str:
    if content_mode == "scientific":
        return (
            f"MODE: SCIENTIFIC ({domain}). Use REAL FIELD ARTIFACTS — charts, "
            f"data viz, microscopy, oscilloscope traces, equations on a "
            f"whiteboard, a terminal with actual code, a labelled diagram, an "
            f"instrument in a lab. Audience is technical: they recognise the "
            f"artifacts. AVOID mood imagery, abstract 'glowing brain' renders, "
            f"or generic cinematic establishing shots. If the visual could "
            f"illustrate ANY paper in this field, it's wrong — make it "
            f"specific to THIS claim and THIS evidence."
        )
    return (
        f"MODE: GENERAL ({domain}). Editorial mood imagery, cinematic "
        f"composition. The frame must convey ONE specific thing this article "
        f"is about — a named person, place, object, or moment — not a "
        f"generic mood for the topic. No stock-photo handshakes. No 'AI "
        f"research' wallpaper. Pick a concrete scene grounded in the "
        f"evidence list."
    )


def _system_prompt(beat: Beat, essence: Essence) -> str:
    return f"""You are planning the visual for ONE beat of a ~25-second vertical reel.

This is beat {beat.idx} (~{beat.target_duration_s:.1f}s of audio, on a {beat.veo_duration}s Veo clip).

{_role_block(beat.role)}

{_mode_block(essence.content_mode, essence.domain)}

COMPOSITION (non-negotiable):
- 9:16 vertical frame. Subject centered or in lower third.
- Leave NEGATIVE SPACE in the upper-center of the frame — burned subtitles
  will sit there. Do NOT compose anything important upper-center.
- One unexpected element (unusual angle / object out of place / surprising
  color / dramatic single-source light) that catches the eye on frame one.
- No text, letters, captions, or watermarks IN the image — subtitles are
  added later in post.

GROUNDING:
- visual_anchor MUST be one of the evidence items listed below, copied
  verbatim. This is the concrete piece of the article the beat stands on.
- image_prompt must REFERENCE the chosen anchor — name the specific
  number, entity, or example. Generic = wrong.

MOTION HINT — pick what serves THIS beat:
- `static`         — locked frame. Best for payoff / final beat.
- `slow_zoom_in`   — push into subject. Best for hook / revealing detail.
- `slow_zoom_out`  — pull back to reveal context. Good for body beats that
                     widen scope.
- `pan_left`       — horizontal reveal leftward.
- `pan_right`      — horizontal reveal rightward.
- `ken_burns`      — combined slow pan + zoom on a still subject.

Default policy: hook → slow_zoom_in; payoff → static; mechanism → vary
(zoom_out / pan / ken_burns) to keep the body of the reel kinetic. Pick
the move that REVEALS what the line says, never a decorative camera move.

REEL ESSENCE:
  core claim : {essence.core_claim}
  evidence   :
{chr(10).join(f"    {i + 1}. {ev}" for i, ev in enumerate(essence.evidence))}
"""


def _user_prompt(
    beat: Beat,
    essence: Essence,
    full_narration: str,
) -> str:
    return f"""FULL REEL NARRATION (for global context — pick a visual that fits
the arc, distinct from other beats):
{full_narration}

THIS IS THE NARRATIVE LINE FOR THIS BEAT — the visual MUST match:
  {beat.text!r}

Beat meta:
  idx               : {beat.idx}
  role              : {beat.role}
  target_duration_s : {beat.target_duration_s:.2f}
  veo_duration      : {beat.veo_duration}

Return a BeatVisual:
  image_prompt   — 9:16 vertical, centered subject, negative space upper-center
                   for subtitles, grounded in the chosen evidence item, with
                   one unexpected compositional element. Specific subject,
                   framing, lighting, palette.
  motion_hint    — one of: static, slow_zoom_in, slow_zoom_out, pan_left,
                   pan_right, ken_burns. Follow the default policy unless
                   the line clearly demands otherwise.
  visual_anchor  — the evidence item this beat grounds on, VERBATIM from
                   the list above.
"""


@reel.reasoner("visual_for_beat")
async def visual_for_beat(
    app: Any,
    beat: Beat,
    essence: Essence,
    full_narration: str,
) -> BeatVisual:
    """One `.ai()` call. Image prompt + motion hint + visual anchor."""
    return await app.ai(
        system=_system_prompt(beat, essence),
        user=_user_prompt(beat, essence, full_narration),
        schema=BeatVisual,
    )


async def plan_beat_visuals(
    app: Any,
    beats: list[Beat],
    essence: Essence,
    full_narration: str,
) -> list[BeatVisual]:
    """Fan out `visual_for_beat` across all beats in parallel via `asyncio.gather`.

    `return_exceptions=False` — if any beat fails, the whole pipeline fails.
    The orchestrator catches and surfaces.
    """
    if not beats:
        return []
    return list(
        await asyncio.gather(
            *(visual_for_beat(app, beat, essence, full_narration) for beat in beats),
            return_exceptions=False,
        )
    )
