"""Subtítulos: word-burst libass (default) + SRT clásico + Whisper fallback.

Public API:

    write_reel_ass / write_reel_ass_with_accents — word-burst .ass output
    subtitles_from_word_timings / write_srt       — SRT classic output
    transcribe / is_available                     — Whisper fallback (lazy)
    normalize_overlay / canonical_position        — accent helpers
"""

from core.subtitles.accents import (
    canonical_position,
    count_words,
    is_valid_for_role,
    normalize_overlay,
    render_text,
)
from core.subtitles.srt import (
    correct,
    file_to_subtitles,
    subtitles_from_word_timings,
    write_srt,
)
from core.subtitles.whisper import is_available as whisper_available
from core.subtitles.whisper import transcribe
from core.subtitles.word_burst import (
    CANVAS_H,
    CANVAS_W,
    build_reel_ass,
    build_reel_ass_with_accents,
    write_reel_ass,
    write_reel_ass_with_accents,
)

__all__ = [
    # word_burst
    "CANVAS_H",
    "CANVAS_W",
    "build_reel_ass",
    "build_reel_ass_with_accents",
    "write_reel_ass",
    "write_reel_ass_with_accents",
    # srt
    "correct",
    "file_to_subtitles",
    "subtitles_from_word_timings",
    "write_srt",
    # whisper
    "transcribe",
    "whisper_available",
    # accents
    "canonical_position",
    "count_words",
    "is_valid_for_role",
    "normalize_overlay",
    "render_text",
]
