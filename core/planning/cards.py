"""Card packer — word timings → subtitle cards. Pure code, no LLM.

Walks ``WordTiming``s in order; emits cards when any of these fire:

  • 5-word cap                                 (MAX_WORDS_PER_CARD)
  • measured width > 1.9 lines of safe stage   (MAX_WIDTH_PER_CARD)
  • next-word gap > 200ms                      (MAX_GAP_S)
  • trailing clause punctuation (≥2 words)     (CLAUSE_PUNCT)

Cards drive ONLY the libass karaoke layout, not video boundaries — so a
card running long never invalidates a Veo bucket.
"""

from __future__ import annotations

from core.planning.font_metrics import (
    MAX_CHARS_PER_LINE,
    MAX_LINES_PER_CARD,
    line_count,
    measured_width,
)
from shared.schemas import Card, WordTiming

MAX_WORDS_PER_CARD: int = 5
MAX_WIDTH_PER_CARD: float = MAX_CHARS_PER_LINE * 1.9
MAX_GAP_S: float = 0.20
CLAUSE_PUNCT: tuple[str, ...] = (",", ".", "!", "?", "—", ";")


def _emit_card(card_words: list[WordTiming]) -> Card:
    """Build a Card from accumulated WordTimings. Assumes non-empty."""
    text = " ".join(w.word for w in card_words)
    lc = line_count(text)
    # Card schema requires line_count Literal[1, 2]; clamp defensively.
    lc_clamped = 1 if lc <= 1 else 2
    return Card(
        text=text,
        words=list(card_words),
        start_s=card_words[0].start_s,
        end_s=card_words[-1].end_s,
        line_count=lc_clamped,  # type: ignore[arg-type]
    )


def _ends_in_clause_punct(word: str) -> bool:
    """True if the word's trailing char is clause-terminating punctuation."""
    if not word:
        return False
    return word[-1] in CLAUSE_PUNCT


def pack_cards(word_timings: list[WordTiming]) -> list[Card]:
    """Pack TTS word timings into subtitle cards.

    Walks words in order. Starts a new card when empty; otherwise appends
    and checks break conditions (priority order). When a break fires the
    card is emitted and the next one starts. Trailing single-word residue
    is folded into the previous card when possible.
    """
    if not word_timings:
        return []

    cards: list[Card] = []
    card_words: list[WordTiming] = []

    for i, w in enumerate(word_timings):
        if card_words:
            prospective = " ".join(cw.word for cw in card_words) + " " + w.word
            if (
                measured_width(prospective) > MAX_WIDTH_PER_CARD
                or line_count(prospective) > MAX_LINES_PER_CARD
            ):
                cards.append(_emit_card(card_words))
                card_words = []

        card_words.append(w)
        card_text = " ".join(cw.word for cw in card_words)
        is_last = i == len(word_timings) - 1

        gap_to_next: float | None = None
        if not is_last:
            gap_to_next = word_timings[i + 1].start_s - w.end_s

        hit_word_cap = len(card_words) >= MAX_WORDS_PER_CARD
        hit_width_cap = measured_width(card_text) > MAX_WIDTH_PER_CARD
        hit_gap = gap_to_next is not None and gap_to_next > MAX_GAP_S
        hit_clause = _ends_in_clause_punct(w.word) and len(card_words) >= 2

        should_break = hit_word_cap or hit_width_cap or hit_gap or hit_clause

        if should_break or is_last:
            cards.append(_emit_card(card_words))
            card_words = []

    # Fold a trailing single-word card into the previous one when safe.
    if len(cards) >= 2 and len(cards[-1].words) == 1:
        prev = cards[-2]
        tail = cards[-1]
        gap_across = tail.start_s - prev.end_s
        merged_words = prev.words + tail.words
        merged_text = " ".join(cw.word for cw in merged_words)
        if (
            gap_across <= MAX_GAP_S
            and len(merged_words) <= MAX_WORDS_PER_CARD
            and measured_width(merged_text) <= MAX_WIDTH_PER_CARD
            and line_count(merged_text) <= MAX_LINES_PER_CARD
        ):
            lc = line_count(merged_text)
            lc_clamped = 1 if lc <= 1 else 2
            cards[-2] = Card(
                text=merged_text,
                words=merged_words,
                start_s=prev.start_s,
                end_s=tail.end_s,
                line_count=lc_clamped,  # type: ignore[arg-type]
            )
            cards.pop()

    return cards
