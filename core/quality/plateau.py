"""Plateau detection for iterative quality-improvement loops.

autonovel stops its revision loop "when scores stabilize" (plateau detection);
contenido today only does retry-on-fail. This is the missing primitive: a pure,
stateful detector that answers *"have we stopped improving enough to stop
spending money?"* for any score series.

Designed for the ``long_form`` revision loop (rewrite a chapter/scene, re-score,
repeat) but it's generic — any monotone-ish quality signal works, including a
future reel re-judge loop.

No deps, no I/O. The detector is the state; the functional :func:`detect_plateau`
is for one-shot analysis of an existing series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["max", "min"]


@dataclass
class PlateauDetector:
    """Stateful stop-condition for revision loops.

    Feed one score per round via :meth:`update`; it returns ``True`` the moment
    you should stop. Two independent stop reasons:

    * ``"target"``  — score crossed ``target`` (early success, optional).
    * ``"plateau"`` — ``patience`` consecutive rounds failed to improve the best
      score by at least ``min_delta`` (diminishing returns).

    Example (long_form revision)::

        det = PlateauDetector(patience=2, min_delta=0.1, target=8.5)
        for _ in range(max_passes):
            score = await rewrite_and_score(chapter)
            if det.update(score):
                break  # det.reason tells you why
    """

    patience: int = 2
    min_delta: float = 0.1
    mode: Mode = "max"
    target: float | None = None

    best: float | None = field(default=None, init=False)
    best_round: int = field(default=-1, init=False)
    rounds_without_improvement: int = field(default=0, init=False)
    history: list[float] = field(default_factory=list, init=False)
    reason: Literal["target", "plateau"] | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.patience < 1:
            raise ValueError("patience must be >= 1")
        if self.min_delta < 0:
            raise ValueError("min_delta must be >= 0")

    def _is_better(self, candidate: float, reference: float) -> bool:
        """True if ``candidate`` beats ``reference`` by at least ``min_delta``."""
        if self.mode == "max":
            return candidate >= reference + self.min_delta
        return candidate <= reference - self.min_delta

    def _meets_target(self, score: float) -> bool:
        if self.target is None:
            return False
        return score >= self.target if self.mode == "max" else score <= self.target

    def update(self, score: float) -> bool:
        """Record one round's score; return True if the loop should stop."""
        self.history.append(score)
        idx = len(self.history) - 1

        if self.best is None or self._is_better(score, self.best):
            self.best = score
            self.best_round = idx
            self.rounds_without_improvement = 0
        else:
            self.rounds_without_improvement += 1

        # Target check uses the round's own score, not just the best.
        if self._meets_target(score):
            self.reason = "target"
            return True

        if self.rounds_without_improvement >= self.patience:
            self.reason = "plateau"
            return True

        return False

    @property
    def should_stop(self) -> bool:
        return self.reason is not None


def detect_plateau(
    scores: list[float],
    *,
    patience: int = 2,
    min_delta: float = 0.1,
    mode: Mode = "max",
    target: float | None = None,
) -> int | None:
    """One-shot: index of the round at which the loop *would have* stopped.

    Returns the 0-based round index where the stop condition first triggers, or
    ``None`` if the series never plateaus (i.e. it kept improving / never hit
    target within the given data).
    """
    det = PlateauDetector(patience=patience, min_delta=min_delta, mode=mode, target=target)
    for i, s in enumerate(scores):
        if det.update(s):
            return i
    return None
