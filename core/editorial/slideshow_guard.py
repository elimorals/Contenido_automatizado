"""Guard anti-slideshow: detecta "PowerPoint animado" antes de aceptar un render.

Idea reimplementada (NO copiada) de OpenMontage `lib/slideshow_risk.py` +
`lib/delivery_promise.py`, adaptada al modelo de datos de contenido (`BeatArtifact`).
OpenMontage es AGPLv3; aquí sólo se replica el *concepto* (scoring de stills vs
movimiento) con código propio bajo Apache-2.0 — ver ADR-019.

Problema que resuelve: el pipeline visual NUNCA crashea (cae a ken-burns sobre
still o a placeholder). Eso es bueno para robustez, pero significa que un reel que
prometió "video cinético" puede terminar siendo 80% imágenes estáticas con zoom —
exactamente el fracaso de "slideshow" que mata el engagement. Este módulo mide ese
riesgo y emite issues (warning/error) reusando el patrón `ValidationResult` de la
capa editorial, para enchufarse al gate humano / pipeline sin fricción.

Clasificación de movimiento real vs still (semántica del pipeline):
- Movimiento real  → stock (Pexels/Pixabay/Coverr), Veo i2v, Higgsfield DoP/Effect,
  LiveAvatar. `MOTION_SOURCES`.
- Still / slideshow → generadores de IMAGEN (Gemini Image, Higgsfield Soul, ComfyUI)
  materializados como clip = still + ken-burns; `LOCAL` = ken-burns sobre placeholder.
- Placeholder roto  → `video_path is None`.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.editorial.validation import ValidationIssue, ValidationResult
from shared.schemas import BeatArtifact, VideoSource

# Fuentes que producen MOVIMIENTO real (no still-derived). Todo lo demás se trata
# como estático para efectos de detección de slideshow.
MOTION_SOURCES: frozenset[VideoSource] = frozenset(
    {
        VideoSource.PEXELS,
        VideoSource.PIXABAY,
        VideoSource.COVERR,
        VideoSource.VEO,
        VideoSource.HIGGSFIELD_DOP,
        VideoSource.HIGGSFIELD_EFFECT,
        VideoSource.LIVE_AVATAR,
        # Corpus libres de VIDEO real (ADR-022). Unsplash NO va aquí: es imagen
        # convertida a clip vía ken-burns → cuenta como still (riesgo slideshow).
        VideoSource.ARCHIVE_ORG,
        VideoSource.WIKIMEDIA,
        VideoSource.NASA,
        VideoSource.FAL,
    }
)


def is_static_artifact(
    artifact: BeatArtifact,
    motion_sources: frozenset[VideoSource] = MOTION_SOURCES,
) -> bool:
    """True si el beat es un still/placeholder (riesgo slideshow), False si es movimiento real."""
    if artifact.video_path is None:
        return True
    return artifact.source not in motion_sources


@dataclass
class SlideshowReport:
    """Resultado del scoring anti-slideshow.

    `risk_score` ∈ [0,1]: 0 = todo movimiento real, 1 = todo still/placeholder.
    """

    n_beats: int
    static_beats: int
    placeholder_beats: int
    distinct_sources: int
    static_ratio: float
    risk_score: float
    result: ValidationResult

    @property
    def ok(self) -> bool:
        """No hay errores (warnings sí están permitidos)."""
        return self.result.ok

    @property
    def is_slideshow(self) -> bool:
        """True si se promitió movimiento y el render quedó dominado por stills."""
        return any(i.field == "slideshow" for i in self.result.errors)


def assess_slideshow_risk(
    artifacts: list[BeatArtifact],
    *,
    promised_motion: bool = True,
    max_static_ratio: float = 0.6,
    motion_sources: frozenset[VideoSource] = MOTION_SOURCES,
) -> SlideshowReport:
    """Evalúa cuántos beats son estáticos y decide si el reel es un "slideshow".

    Args:
        artifacts: artefactos por beat (post Phase D del pipeline).
        promised_motion: si el reel prometió movimiento/cinemato (PREMIUM, use_veo,
            higgsfield, etc.). Si False, los stills son aceptables y nunca se marca
            slideshow (sólo se avisa de placeholders rotos).
        max_static_ratio: fracción máxima de stills tolerada antes de marcar error.
        motion_sources: override del conjunto de fuentes consideradas "movimiento real".

    Returns:
        SlideshowReport con métricas + ValidationResult (issues warning/error).
    """
    result = ValidationResult()
    n = len(artifacts)

    if n == 0:
        return SlideshowReport(
            n_beats=0,
            static_beats=0,
            placeholder_beats=0,
            distinct_sources=0,
            static_ratio=0.0,
            risk_score=0.0,
            result=result,
        )

    static = [a for a in artifacts if is_static_artifact(a, motion_sources)]
    placeholders = [a for a in artifacts if a.video_path is None]
    static_ratio = len(static) / n
    placeholder_ratio = len(placeholders) / n
    distinct_sources = len({a.source for a in artifacts if a.video_path is not None})
    risk_score = min(1.0, 0.7 * static_ratio + 0.3 * placeholder_ratio)

    # Placeholders rotos: siempre se avisa (render incompleto), sin importar promesa.
    if placeholders:
        result.issues.append(
            ValidationIssue(
                severity="warning",
                field="placeholder",
                message=(
                    f"{len(placeholders)}/{n} beats sin video real (placeholder). "
                    "Render incompleto — revisar fallbacks de assets."
                ),
            )
        )

    if promised_motion and static_ratio > max_static_ratio:
        result.issues.append(
            ValidationIssue(
                severity="error",
                field="slideshow",
                message=(
                    f"{len(static)}/{n} beats son estáticos "
                    f"(ratio {static_ratio:.0%} > umbral {max_static_ratio:.0%}). "
                    "El reel prometió movimiento pero quedó como slideshow "
                    "(ken-burns sobre stills). Revisar Veo/Higgsfield/stock."
                ),
            )
        )
        # Baja diversidad agrava: todo viene de una sola fuente de imagen.
        if distinct_sources <= 1 and n > 1:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="source_diversity",
                    message=(
                        f"Baja diversidad de fuentes ({distinct_sources}): "
                        "todos los visuales provienen del mismo generador. "
                        "Sube el contraste visual mezclando stock + i2v."
                    ),
                )
            )

    return SlideshowReport(
        n_beats=n,
        static_beats=len(static),
        placeholder_beats=len(placeholders),
        distinct_sources=distinct_sources,
        static_ratio=static_ratio,
        risk_score=risk_score,
        result=result,
    )


__all__ = [
    "MOTION_SOURCES",
    "SlideshowReport",
    "assess_slideshow_risk",
    "is_static_artifact",
]
