"""Análisis de video de referencia (input-by-reference) — ADR-017.

"Haz un reel con el ritmo/estructura de *este* video": dado un enlace
(TikTok/Reel/YouTube), extrae pacing, hook, estructura y transcript en un
`ReferenceBrief` que puede informar la composición del guion/beats.

Reimplementado bajo Apache-2.0 (concepto inspirado en OpenMontage, sin reutilizar
su código AGPLv3).
"""
from __future__ import annotations

from core.reference.analyzer import (
    DEFAULT_REEL_TARGET_S,
    RawReference,
    ReferenceAnalysisError,
    ReferenceFetcher,
    analyze_reference,
    build_brief,
    mechanism_target,
    reference_style_hint,
)

__all__ = [
    "DEFAULT_REEL_TARGET_S",
    "RawReference",
    "ReferenceAnalysisError",
    "ReferenceFetcher",
    "analyze_reference",
    "build_brief",
    "mechanism_target",
    "reference_style_hint",
]
