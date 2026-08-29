"""Mechanical slop detection — deterministic, $0, pre-LLM quality gate.

Clean-room reimplementation of the *idea* behind autonovel's `evaluate.py`
mechanical slop detection (wordlist-based, no LLM). The algorithm — score a
text by counting known "AI-ism" words and filler phrases — is not
copyrightable; this is an independent implementation adapted to contenido:

  - **Bilingual (EN + ES)**: contenido defaults to ``es-MX``. "sumérgete en el
    fascinante mundo" is as much of a tell as "let's dive into".
  - **Accent-insensitive** matching via ``unicodedata`` so "plétora" == "pletora".
  - Returns a structured :class:`SlopReport` with per-tier hits and a weighted
    penalty, so it can act as a cheap pre-filter BEFORE the LLM Judge (Phase 4)
    instead of spending Judge tokens ranking AI-slop narrations.

No third-party deps — pure ``re`` + stdlib. Pure functions, side-effect free.

Why deterministic *before* the LLM: the Judge is good at "A or B?" but it costs
a token round-trip and it can be charmed by fluent slop. A regex gate is free,
instant, and never rationalises "delve" into acceptability.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── Tier weights ────────────────────────────────────────────────────────────
# tier1 = hard AI-isms (almost never written by a human on purpose)
# tier2 = suspicious corporate/marketing filler (context-dependent)
# tier3 = filler *phrases* (regex) — the connective tissue of generated prose
_W_TIER1 = 3.0
_W_TIER2 = 1.0
_W_TIER3 = 2.0

# ── Tier 1: banned single words (EN + ES) ───────────────────────────────────
_TIER1_BANNED: tuple[str, ...] = (
    # English
    "delve", "utilize", "leverage", "facilitate", "elucidate", "embark",
    "endeavor", "encompass", "multifaceted", "tapestry", "paradigm",
    "synergy", "synergize", "holistic", "catalyze", "catalyst", "juxtapose",
    "myriad", "plethora", "interplay", "underscore",
    # Spanish
    "sumergete", "sumergirnos", "sumergirse", "adentrarnos", "adentrate",
    "adentrarse", "tapiz", "pletora", "multifacetico", "multifacetica",
    "paradigma", "sinergia", "holistico", "holistica", "abanico",
    "vasto", "vasta", "crisol", "amalgama", "epitome",
)

# ── Tier 2: suspicious words (EN + ES) ──────────────────────────────────────
_TIER2_SUSPICIOUS: tuple[str, ...] = (
    # English
    "robust", "comprehensive", "seamless", "seamlessly", "innovative",
    "streamline", "empower", "foster", "enhance", "elevate", "optimize",
    "pivotal", "intricate", "profound", "resonate", "harness", "cultivate",
    "bolster", "cornerstone", "scalable",
    # Spanish
    "robusto", "robusta", "integral", "innovador", "innovadora", "potenciar",
    "fomentar", "optimizar", "fundamental", "intrincado", "intrincada",
    "profundo", "resonar", "piedra angular", "vanguardia", "revolucionar",
    "revolucionario", "primordial",
)

# ── Tier 3: filler phrases (regex, EN + ES) ─────────────────────────────────
# Patterns are matched against accent-stripped, lowercased text. ``^`` anchors
# are applied per-line (re.MULTILINE) so leading connectives are caught.
_TIER3_FILLER: tuple[str, ...] = (
    # English
    r"it'?s worth noting that",
    r"it'?s important to (note|remember|understand) that",
    r"^\s*importantly,?\s",
    r"^\s*notably,?\s",
    r"^\s*interestingly,?\s",
    r"let'?s (dive|delve) into",
    r"let'?s explore",
    r"as we can see",
    r"in conclusion,?\s",
    r"when it comes to",
    # Spanish
    r"cabe (destacar|mencionar|resaltar|recalcar|senalar) que",
    r"es importante (destacar|mencionar|notar|senalar|recordar) que",
    r"vale la pena (mencionar|destacar)",
    r"como podemos ver",
    r"en este (articulo|video|reel)",
    r"sin lugar a dudas",
    r"hoy en dia",
    r"en resumen,?\s",
    r"a lo largo de la historia",
    r"el fascinante mundo de",
)


def strip_accents(text: str) -> str:
    """Lowercase + remove diacritics so 'Plétora' matches 'pletora'."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _compile_words(words: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation regex with word boundaries, over accent-stripped words."""
    normalized = sorted({strip_accents(w) for w in words}, key=len, reverse=True)
    alt = "|".join(re.escape(w) for w in normalized)
    return re.compile(rf"\b(?:{alt})\b")


_RE_TIER1 = _compile_words(_TIER1_BANNED)
_RE_TIER2 = _compile_words(_TIER2_SUSPICIOUS)
_RE_TIER3 = [re.compile(strip_accents(p), re.MULTILINE) for p in _TIER3_FILLER]

# Default: flag when weighted penalty per 100 words ≥ this, OR any tier-1 hit.
DEFAULT_DENSITY_THRESHOLD = 2.0


@dataclass(frozen=True)
class SlopReport:
    """Result of scoring one text. Immutable; cheap to build and compare."""

    word_count: int
    tier1_hits: list[str] = field(default_factory=list)
    tier2_hits: list[str] = field(default_factory=list)
    tier3_hits: list[str] = field(default_factory=list)
    penalty: float = 0.0
    density: float = 0.0  # penalty per 100 words
    flagged: bool = False

    @property
    def total_hits(self) -> int:
        return len(self.tier1_hits) + len(self.tier2_hits) + len(self.tier3_hits)

    def note(self) -> str:
        """Compact one-liner for logs or Judge injection."""
        if self.total_hits == 0:
            return "slop: clean"
        parts: list[str] = []
        if self.tier1_hits:
            parts.append(f"banned={sorted(set(self.tier1_hits))}")
        if self.tier2_hits:
            parts.append(f"suspicious={sorted(set(self.tier2_hits))}")
        if self.tier3_hits:
            parts.append(f"filler={len(self.tier3_hits)}")
        flag = " ⚑" if self.flagged else ""
        return f"slop: density={self.density:.1f} {' '.join(parts)}{flag}"


def score_slop(
    text: str,
    *,
    extra_banned: list[str] | None = None,
    density_threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> SlopReport:
    """Score ``text`` for mechanical slop. Deterministic, no I/O.

    Args:
        text: the narration / script to inspect.
        extra_banned: project-specific tier-1 words to add (treated as banned).
        density_threshold: penalty-per-100-words at which ``flagged`` flips True.
            Any tier-1 hit also flips ``flagged`` regardless of density.

    Returns:
        A :class:`SlopReport`.
    """
    if not text or not text.strip():
        return SlopReport(word_count=0)

    norm = strip_accents(text)
    word_count = len(re.findall(r"\b\w+\b", norm)) or 1

    tier1 = _RE_TIER1.findall(norm)
    if extra_banned:
        extra_re = _compile_words(tuple(extra_banned))
        tier1 = tier1 + extra_re.findall(norm)
    tier2 = _RE_TIER2.findall(norm)
    tier3 = [p.pattern for p in _RE_TIER3 if p.search(norm)]

    penalty = _W_TIER1 * len(tier1) + _W_TIER2 * len(tier2) + _W_TIER3 * len(tier3)
    density = penalty / word_count * 100.0
    flagged = bool(tier1) or density >= density_threshold

    return SlopReport(
        word_count=word_count,
        tier1_hits=tier1,
        tier2_hits=tier2,
        tier3_hits=tier3,
        penalty=penalty,
        density=round(density, 3),
        flagged=flagged,
    )


def rank_by_slop(
    texts: list[str],
    *,
    extra_banned: list[str] | None = None,
    density_threshold: float = DEFAULT_DENSITY_THRESHOLD,
) -> list[tuple[int, SlopReport]]:
    """Score every text and return ``(original_index, report)`` cleanest-first.

    Stable: ties preserve input order, so the Judge still breaks genuine ties.
    """
    scored = [
        (i, score_slop(t, extra_banned=extra_banned, density_threshold=density_threshold))
        for i, t in enumerate(texts)
    ]
    return sorted(scored, key=lambda pair: (pair[1].penalty, pair[0]))


def filter_slop(
    texts: list[str],
    *,
    extra_banned: list[str] | None = None,
    density_threshold: float = DEFAULT_DENSITY_THRESHOLD,
    keep_min: int = 1,
) -> tuple[list[int], list[SlopReport]]:
    """Pre-judge gate: drop flagged texts, but never return fewer than ``keep_min``.

    Returns ``(kept_indices, all_reports)`` where ``all_reports`` is aligned to
    the original ``texts`` order (so callers can log every candidate's score).

    Guarantee: if *all* candidates are flagged, the ``keep_min`` cleanest survive
    (by penalty) — the gate degrades to "least-slop wins", never to "empty".
    """
    reports = [
        score_slop(t, extra_banned=extra_banned, density_threshold=density_threshold)
        for t in texts
    ]
    kept = [i for i, r in enumerate(reports) if not r.flagged]
    if len(kept) < keep_min:
        ranked = sorted(range(len(texts)), key=lambda i: (reports[i].penalty, i))
        kept = sorted(ranked[:keep_min])
    return kept, reports
