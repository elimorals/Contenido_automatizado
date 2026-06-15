"""Deterministic beat planner — carves a ScriptDraft into narrative Beats.

One Beat per spoken section (hook + mechanism_lines + payoff). Each beat
gets a fixed Veo bucket (4 / 6 / 8 s) picked from the proportional audio
share with role-aware bias (hook floors at 6s; payoff caps at 4s for the
snappy loop-back close).
"""

from __future__ import annotations

import math

from shared.schemas import Beat, BeatRole, ScriptDraft

# Veo i2v fixed clip-length buckets.
VEO_BUCKETS: tuple[int, int, int] = (4, 6, 8)

# Safety margin (seconds) added before bucket selection.
SAFETY_S: float = 0.3


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _bucket_for(seconds: float) -> int:
    """Smallest Veo bucket ≥ ceil(seconds + safety)."""
    target = math.ceil(seconds + SAFETY_S)
    for b in VEO_BUCKETS:
        if b >= target:
            return b
    return VEO_BUCKETS[-1]


def plan_beats(script: ScriptDraft, audio_duration_s: float) -> list[Beat]:
    """Carve a ScriptDraft into Beats aligned to the audio timeline.

    One Beat per:
      - ``script.hook``                 → role=BeatRole.HOOK,      idx=0
      - ``script.mechanism_lines[i]``   → role=BeatRole.MECHANISM, idx=1..N
      - ``script.payoff_line``          → role=BeatRole.PAYOFF,    idx=N+1

    ``target_duration_s`` is proportional to the beat's share of total
    script words. ``veo_duration`` is the smallest bucket from {4,6,8}
    that covers the estimated audio share + a small safety margin, with
    role-aware bias:
      - Hook      : floor at 6s  (short hook clips can't stop a scroll)
      - Payoff    : cap at 4s    (snappy close engineers the loop-back)
      - Mechanism : pure bucket lookup
    """
    beat_specs: list[tuple[BeatRole, str]] = []
    beat_specs.append((BeatRole.HOOK, script.hook))
    for line in script.mechanism_lines:
        beat_specs.append((BeatRole.MECHANISM, line))
    if script.payoff_line and script.payoff_line.strip():
        beat_specs.append((BeatRole.PAYOFF, script.payoff_line))

    word_counts = [_word_count(text) for _, text in beat_specs]
    total_words = sum(word_counts) or 1

    beats: list[Beat] = []
    for idx, ((role, text), wc) in enumerate(zip(beat_specs, word_counts)):
        target = (wc / total_words) * audio_duration_s

        if role == BeatRole.HOOK:
            bucket = max(6, _bucket_for(target))
        elif role == BeatRole.PAYOFF:
            bucket = 4
        else:
            bucket = _bucket_for(target)

        # Pydantic Literal[4, 6, 8] guard — bucket is by construction one of
        # {4,6,8} so the ignore is safe.
        beats.append(
            Beat(
                idx=idx,
                role=role,
                text=text,
                target_duration_s=max(target, 1e-6),
                veo_duration=bucket,  # type: ignore[arg-type]
            )
        )

    return beats
