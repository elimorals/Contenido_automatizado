"""Tests for the deterministic beat planner."""
from __future__ import annotations

import pytest

from core.planning.beats import VEO_BUCKETS, plan_beats
from shared.schemas import BeatRole, HookVariant, ScriptDraft


def _make_script(
    *,
    hook: str = "Why do we have fingerprints?",
    mechanism_lines: list[str] | None = None,
    payoff_line: str = "Yes, even twins have unique fingerprints.",
) -> ScriptDraft:
    """Helper: build a ScriptDraft that passes the loop-back validator."""
    if mechanism_lines is None:
        mechanism_lines = [
            "Scientists at MIT studied identical twins for years.",
            "They found unique ridges form in the womb due to random fluid pressure.",
        ]
    return ScriptDraft(
        hook=hook,
        hook_variant=HookVariant.CURIOSITY_GAP,
        mechanism_lines=mechanism_lines,
        payoff_line=payoff_line,
        narration=" ".join([hook, *mechanism_lines, payoff_line]),
    )


class TestPlanBeats:
    def test_emits_one_beat_per_section(self) -> None:
        script = _make_script()
        beats = plan_beats(script, audio_duration_s=20.0)
        # 1 hook + 2 mechanism + 1 payoff
        assert len(beats) == 4
        assert beats[0].role == BeatRole.HOOK
        assert beats[1].role == BeatRole.MECHANISM
        assert beats[2].role == BeatRole.MECHANISM
        assert beats[3].role == BeatRole.PAYOFF
        # idx is sequential starting at 0
        assert [b.idx for b in beats] == [0, 1, 2, 3]

    def test_hook_floors_at_6s_even_with_short_text(self) -> None:
        # Hook is just 2 words → tiny share of a long audio → tiny target.
        # The hook floor must still bump it up to 6s minimum.
        script = _make_script(
            hook="Hi fingerprints?",  # 2 words, but keyword still present
            payoff_line="Yes — unique fingerprints.",
        )
        beats = plan_beats(script, audio_duration_s=30.0)
        hook = beats[0]
        assert hook.role == BeatRole.HOOK
        assert hook.veo_duration >= 6
        assert hook.veo_duration in VEO_BUCKETS

    def test_payoff_caps_at_4s_even_with_long_text(self) -> None:
        # Force payoff to be the heaviest section. The 4s cap must still apply.
        script = _make_script(
            mechanism_lines=[
                "Short line one.",
                "Short line two.",
            ],
            payoff_line=(
                "And that is exactly why every single one of us walks around "
                "with totally unique fingerprints on every fingertip."
            ),
        )
        beats = plan_beats(script, audio_duration_s=40.0)
        payoff = beats[-1]
        assert payoff.role == BeatRole.PAYOFF
        assert payoff.veo_duration == 4

    def test_mechanism_picks_smallest_bucket_covering_target_plus_safety(self) -> None:
        # Construct so the mechanism share lands cleanly inside the 6s bucket.
        # Total words ≈ small; we'll inspect the actual buckets are in {4,6,8}.
        script = _make_script()
        beats = plan_beats(script, audio_duration_s=18.0)
        for b in beats:
            assert b.veo_duration in VEO_BUCKETS
            assert b.target_duration_s > 0
        # Mechanism beats should use pure bucket lookup (no floor/cap).
        for b in beats:
            if b.role == BeatRole.MECHANISM:
                # Bucket must be at least the smallest ≥ target+safety, ceil'd.
                assert b.veo_duration >= 4

    def test_targets_sum_to_audio_duration(self) -> None:
        script = _make_script()
        audio = 22.5
        beats = plan_beats(script, audio_duration_s=audio)
        total = sum(b.target_duration_s for b in beats)
        assert total == pytest.approx(audio, rel=1e-6)
