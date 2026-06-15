"""Planning determinístico (sin LLM): beats, cards, font_metrics, safe_zone.

  beats        — ScriptDraft → list[Beat] (video planning con buckets Veo)
  cards        — list[WordTiming] → list[Card] (layout subtítulos word-burst)
  font_metrics — Montserrat Bold width tables used by cards
  safe_zone    — 9:16 canvas + subtitle/accent positioning constants
"""

from __future__ import annotations

from core.planning.beats import SAFETY_S, VEO_BUCKETS, plan_beats
from core.planning.cards import (
    CLAUSE_PUNCT,
    MAX_GAP_S,
    MAX_WIDTH_PER_CARD,
    MAX_WORDS_PER_CARD,
    pack_cards,
)
from core.planning.font_metrics import (
    MAX_CHARS_PER_LINE,
    MAX_LINES_PER_CARD,
    char_width,
    fits_one_line,
    line_count,
    measured_width,
)

__all__ = [
    # beats
    "plan_beats",
    "VEO_BUCKETS",
    "SAFETY_S",
    # cards
    "pack_cards",
    "MAX_WORDS_PER_CARD",
    "MAX_WIDTH_PER_CARD",
    "MAX_GAP_S",
    "CLAUSE_PUNCT",
    # font_metrics
    "char_width",
    "measured_width",
    "fits_one_line",
    "line_count",
    "MAX_CHARS_PER_LINE",
    "MAX_LINES_PER_CARD",
]
