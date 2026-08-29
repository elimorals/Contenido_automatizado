"""core.quality — deterministic, $0 quality primitives.

Two clean-room reimplementations of *ideas* (not code) from NousResearch's
unlicensed ``autonovel`` pipeline, adapted to contenido:

  - :mod:`core.quality.slop`    — mechanical (regex, no-LLM) AI-slop detector,
    bilingual EN+ES. A cheap pre-filter BEFORE the LLM Judge (Phase 4).
  - :mod:`core.quality.plateau` — stop-condition for iterative revision loops
    (``long_form``): stop when scores stop improving.

Both modules are pure (no I/O, no deps) and opt-in. See ADR / docs/EDITORIAL.md.
"""

from __future__ import annotations

from core.quality.plateau import PlateauDetector, detect_plateau
from core.quality.slop import (
    DEFAULT_DENSITY_THRESHOLD,
    SlopReport,
    filter_slop,
    rank_by_slop,
    score_slop,
)

__all__ = [
    "DEFAULT_DENSITY_THRESHOLD",
    "PlateauDetector",
    "SlopReport",
    "detect_plateau",
    "filter_slop",
    "rank_by_slop",
    "score_slop",
]
