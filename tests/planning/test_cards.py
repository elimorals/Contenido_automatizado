"""Tests for the deterministic card packer."""
from __future__ import annotations

from core.planning.cards import (
    MAX_GAP_S,
    MAX_WORDS_PER_CARD,
    pack_cards,
)
from shared.schemas import WordTiming


def _wt(word: str, start: float, end: float) -> WordTiming:
    return WordTiming(word=word, start_s=start, end_s=end)


def _flatten_words(cards) -> list[str]:
    return [w.word for c in cards for w in c.words]


class TestPackCards:
    def test_empty_input_returns_empty(self) -> None:
        assert pack_cards([]) == []

    def test_word_cap_breaks_card(self) -> None:
        # 7 tight words with no punctuation, no big gap, short enough text.
        # Should break at the 5-word cap.
        words = [
            _wt("one", 0.0, 0.15),
            _wt("two", 0.18, 0.30),
            _wt("hi", 0.33, 0.45),
            _wt("go", 0.48, 0.60),
            _wt("up", 0.63, 0.75),  # 5th word — break here
            _wt("now", 0.78, 0.90),
            _wt("yes", 0.93, 1.05),
        ]
        cards = pack_cards(words)
        # First card must have exactly MAX_WORDS_PER_CARD words.
        assert len(cards[0].words) == MAX_WORDS_PER_CARD
        # All input words present in some card, in order.
        assert _flatten_words(cards) == [w.word for w in words]

    def test_large_gap_breaks_card(self) -> None:
        # Two words, small gap → would fit one card.
        # But a gap larger than MAX_GAP_S between word-2 and word-3 must break.
        words = [
            _wt("hello", 0.0, 0.3),
            _wt("world", 0.35, 0.7),         # gap from prev=0.05, gap-to-next is BIG
            _wt("after", 1.5, 1.9),          # gap=0.8 → > MAX_GAP_S
            _wt("pause", 1.95, 2.3),
        ]
        cards = pack_cards(words)
        # Need at least 2 cards (the gap forces a break).
        assert len(cards) >= 2
        # First card ends with "world" (the word right before the big gap).
        assert cards[0].words[-1].word == "world"
        # The gap-to-next from end of card 0 to start of card 1 must exceed cap.
        gap_across = cards[1].start_s - cards[0].end_s
        assert gap_across > MAX_GAP_S

    def test_clause_punct_breaks_card_when_two_or_more_words(self) -> None:
        # Two words ending in comma should break the card right there.
        words = [
            _wt("ok", 0.0, 0.2),
            _wt("yes,", 0.22, 0.45),     # comma after 2 words → break
            _wt("more", 0.48, 0.70),
            _wt("here", 0.73, 0.95),
        ]
        cards = pack_cards(words)
        # First card ends at the comma-terminating word.
        assert cards[0].words[-1].word == "yes,"
        # Second card begins at "more".
        assert cards[1].words[0].word == "more"

    def test_single_word_lone_should_not_appear_as_solo_card_when_mergeable(self) -> None:
        # 6 words with no punctuation, small gaps → after 5-word break the
        # trailing single word should be folded back into the previous card.
        words = [
            _wt("one", 0.0, 0.15),
            _wt("two", 0.18, 0.30),
            _wt("hi", 0.33, 0.45),
            _wt("go", 0.48, 0.60),
            _wt("up", 0.63, 0.75),
            _wt("now", 0.78, 0.90),  # trailing single-word — should merge back
        ]
        cards = pack_cards(words)
        # After merge we expect one card containing all 6 words OR the trailing
        # word folded in. Either way no card may be a single isolated word
        # when the merged variant fits the caps.
        # MAX_WORDS_PER_CARD=5 so the merged card would be 6 words — over cap.
        # That means merge MUST be refused; we should see a 5+1 split.
        assert [len(c.words) for c in cards] == [5, 1]

    def test_card_timestamps_match_first_and_last_words(self) -> None:
        words = [
            _wt("alpha", 0.0, 0.4),
            _wt("beta", 0.45, 0.9),
        ]
        cards = pack_cards(words)
        assert cards[0].start_s == 0.0
        assert cards[0].end_s == 0.9
        assert cards[0].text == "alpha beta"
