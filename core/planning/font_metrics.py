"""Char-width metrics for the subtitle font (Montserrat Bold ~80px).

The card packer needs to know how wide a line of text will be so it
doesn't pack a card that overflows the safe zone. Real font-metrics
would require Pillow / freetype at runtime; this table is an empirical
per-glyph average that's accurate enough for line-breaking decisions.
The actual rendering happens en libass (que hace layout real).
"""

from __future__ import annotations

# Approximate per-char width in "char units" where 1.0 = average.
# Tuned so the sum across an average English sentence ≈ char count.
_WIDTHS: dict[str, float] = {
    # very narrow
    "i": 0.40, "l": 0.42, "I": 0.45, "j": 0.45, "t": 0.55, "f": 0.55,
    # narrow
    "r": 0.62, "1": 0.65, "!": 0.42, ".": 0.40, ",": 0.40, "'": 0.35,
    ":": 0.40, ";": 0.40, " ": 0.55,
    # wide
    "m": 1.45, "w": 1.40, "M": 1.55, "W": 1.55, "@": 1.55,
    # extra-wide
    "&": 1.30, "%": 1.30, "$": 1.10, "—": 1.40, "-": 0.60,
}

DEFAULT_WIDTH: float = 1.0

# Max chars-of-average-width per line on the 9:16 safe stage at ~80px font.
# Tightened from 25 to 18 so long cards don't bleed off the edges.
MAX_CHARS_PER_LINE: float = 18.0

# Max lines per card — anything over 2 lines is too tall to read on scroll.
MAX_LINES_PER_CARD: int = 2


def char_width(c: str) -> float:
    """Approximate width of one character in 'avg char' units."""
    return _WIDTHS.get(c, DEFAULT_WIDTH)


def measured_width(text: str) -> float:
    """Sum of per-char widths — comparable to MAX_CHARS_PER_LINE."""
    return sum(char_width(c) for c in text)


def fits_one_line(text: str) -> bool:
    """True if `text` fits in a single line on the safe stage."""
    return measured_width(text) <= MAX_CHARS_PER_LINE


def line_count(text: str) -> int:
    """How many lines `text` will wrap to under naive greedy wrap.

    For card-packing decisions only — libass does real layout.
    """
    if measured_width(text) <= MAX_CHARS_PER_LINE:
        return 1
    words = text.split()
    lines = 0
    current = 0.0
    for w in words:
        ww = measured_width(w + " ")
        if current + ww > MAX_CHARS_PER_LINE and current > 0:
            lines += 1
            current = ww
        else:
            current += ww
    if current > 0:
        lines += 1
    return max(lines, 1)
