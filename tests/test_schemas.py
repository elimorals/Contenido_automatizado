"""Tests de schemas unificados."""
from __future__ import annotations

import pytest

from shared.schemas import (
    AccentOverlay,
    AccentPattern,
    AccentPosition,
    BeatRole,
    GenerationMode,
    HookVariant,
    ScriptDraft,
    VideoAspect,
    VideoParams,
)


class TestVideoParams:
    def test_topic_entry(self) -> None:
        params = VideoParams(topic="placebo effect")
        assert params.topic == "placebo effect"
        assert params.mode == GenerationMode.PREMIUM

    def test_url_entry(self) -> None:
        params = VideoParams(url="https://example.com")
        assert params.url == "https://example.com"

    def test_subject_entry_with_alias(self) -> None:
        params = VideoParams(**{"video_subject": "flowers"})
        assert params.subject == "flowers"

    def test_requires_one_entry_point(self) -> None:
        with pytest.raises(ValueError, match="url, topic, subject"):
            VideoParams()

    def test_aspect_dimensions(self) -> None:
        assert VideoAspect.PORTRAIT.dimensions() == (1080, 1920)
        assert VideoAspect.LANDSCAPE.dimensions() == (1920, 1080)
        assert VideoAspect.SQUARE.dimensions() == (1080, 1080)


class TestScriptDraft:
    def test_valid_loop_back(self) -> None:
        # "fingerprints" (keyword del hook) aparece en payoff_line
        script = ScriptDraft(
            hook="Why do we have fingerprints?",
            hook_variant=HookVariant.CURIOSITY_GAP,
            mechanism_lines=[
                "Scientists at MIT studied identical twins for years.",
                "They found unique ridges form in the womb due to random fluid pressure.",
            ],
            payoff_line="Yes, even twins have unique fingerprints.",
            narration=(
                "Why do we have fingerprints? "
                "Scientists at MIT studied identical twins for years. "
                "They found unique ridges form in the womb due to random fluid pressure. "
                "Yes, even twins have unique fingerprints."
            ),
        )
        assert script.hook_variant == HookVariant.CURIOSITY_GAP

    def test_loop_back_violation(self) -> None:
        # payoff_line NO menciona "fingerprints" → debe fallar
        with pytest.raises(ValueError, match="Loop-back validator"):
            ScriptDraft(
                hook="Why do we have fingerprints?",
                hook_variant=HookVariant.CURIOSITY_GAP,
                mechanism_lines=[
                    "Something about ridges and patterns.",
                    "More about biology and development.",
                ],
                payoff_line="And that's how it works.",
                narration=(
                    "Why do we have fingerprints? "
                    "Something about ridges and patterns. "
                    "More about biology and development. "
                    "And that's how it works."
                ),
            )


class TestAccentOverlay:
    def test_valid_word_count(self) -> None:
        overlay = AccentOverlay(
            text="DR CHEN STANFORD",
            pattern=AccentPattern.NAMED_ENTITY,
            position=AccentPosition.LOWER_THIRD,
        )
        assert overlay.text == "DR CHEN STANFORD"

    def test_too_few_words(self) -> None:
        with pytest.raises(ValueError, match="2-6 palabras"):
            AccentOverlay(text="WOW", pattern=AccentPattern.REACTION)

    def test_too_many_words(self) -> None:
        with pytest.raises(ValueError, match="2-6 palabras"):
            AccentOverlay(
                text="ONE TWO THREE FOUR FIVE SIX SEVEN",
                pattern=AccentPattern.REACTION,
            )
