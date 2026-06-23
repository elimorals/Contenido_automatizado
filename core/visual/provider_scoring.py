"""Scoring de proveedor visual auditable — 7 dimensiones + audit trail (ADR-027).

`contenido` ya decide stock vs IA por beat con una heurística probada
(`selector._decide_source`). Este módulo NO la reemplaza: añade una capa de
**trazabilidad** que puntúa cada proveedor candidato en 7 dimensiones ponderadas
y registra POR QUÉ se eligió uno y qué alternativas se consideraron —
valioso para A/B testing y para auditar decisiones por tenant.

Idea inspirada (NO copiada) en el `lib/scoring.py` de OpenMontage (AGPLv3).
Reimplementación propia Apache-2.0, adaptada a `VideoSource` de contenido.

Las 7 dimensiones (∈[0,1]) y sus pesos por default:
- task_fit (0.30)    — qué tan apto es el proveedor para ESTE beat (dinámico).
- quality (0.20)     — fidelidad visual del output.
- control (0.15)     — parámetros / customización disponible.
- reliability (0.15) — estabilidad / uptime.
- cost (0.10)        — eficiencia de costo (1.0 = gratis).
- latency (0.05)     — velocidad (1.0 = rápido).
- continuity (0.05)  — consistencia de personaje/estilo entre clips.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shared.schemas import VideoSource


@dataclass(frozen=True)
class ScoreWeights:
    task_fit: float = 0.30
    quality: float = 0.20
    control: float = 0.15
    reliability: float = 0.15
    cost: float = 0.10
    latency: float = 0.05
    continuity: float = 0.05


@dataclass(frozen=True)
class DimensionScores:
    task_fit: float
    quality: float
    control: float
    reliability: float
    cost: float
    latency: float
    continuity: float

    def weighted(self, w: ScoreWeights) -> float:
        return (
            self.task_fit * w.task_fit
            + self.quality * w.quality
            + self.control * w.control
            + self.reliability * w.reliability
            + self.cost * w.cost
            + self.latency * w.latency
            + self.continuity * w.continuity
        )


@dataclass(frozen=True)
class ProviderScore:
    provider: str  # VideoSource.value
    score: float
    dimensions: DimensionScores
    reasons: dict[str, str]


@dataclass
class ProviderDecision:
    chosen: str
    score: float
    alternatives: list[ProviderScore]  # todas, orden desc por score
    context: dict
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "chosen": self.chosen,
            "score": round(self.score, 4),
            "context": self.context,
            "alternatives": [
                {
                    "provider": a.provider,
                    "score": round(a.score, 4),
                    "dimensions": {k: round(v, 4) for k, v in asdict(a.dimensions).items()},
                    "reasons": a.reasons,
                }
                for a in self.alternatives
            ],
        }


# Perfil estático de capacidad por fuente (todo salvo task_fit, que es dinámico).
# (quality, control, reliability, cost, latency, continuity, is_motion)
@dataclass(frozen=True)
class _Profile:
    quality: float
    control: float
    reliability: float
    cost: float
    latency: float
    continuity: float
    is_motion: bool


_PROFILES: dict[VideoSource, _Profile] = {
    # Stock de video: barato, estable, rápido; poco control y continuidad.
    VideoSource.PEXELS: _Profile(0.65, 0.20, 0.90, 1.0, 0.90, 0.20, True),
    VideoSource.PIXABAY: _Profile(0.55, 0.20, 0.85, 1.0, 0.90, 0.20, True),
    VideoSource.COVERR: _Profile(0.60, 0.20, 0.80, 1.0, 0.88, 0.20, True),
    VideoSource.ARCHIVE_ORG: _Profile(0.50, 0.15, 0.75, 1.0, 0.70, 0.20, True),
    VideoSource.WIKIMEDIA: _Profile(0.50, 0.15, 0.75, 1.0, 0.70, 0.20, True),
    VideoSource.NASA: _Profile(0.70, 0.15, 0.80, 1.0, 0.70, 0.20, True),
    VideoSource.LOC: _Profile(0.55, 0.15, 0.80, 1.0, 0.68, 0.20, True),
    VideoSource.NARA: _Profile(0.55, 0.15, 0.80, 1.0, 0.68, 0.20, True),
    VideoSource.POND5_PD: _Profile(0.55, 0.15, 0.78, 1.0, 0.68, 0.20, True),
    VideoSource.UNSPLASH: _Profile(0.70, 0.15, 0.85, 1.0, 0.85, 0.20, False),
    # Generadores de imagen (still → motion vía ken-burns).
    VideoSource.GEMINI_IMAGE: _Profile(0.75, 0.55, 0.80, 0.85, 0.70, 0.30, False),
    VideoSource.HIGGSFIELD_SOUL: _Profile(0.80, 0.65, 0.70, 0.80, 0.55, 0.85, False),
    VideoSource.COMFYUI: _Profile(0.85, 0.95, 0.65, 0.90, 0.45, 0.85, False),
    # Generadores de motion (i2v).
    VideoSource.VEO: _Profile(0.88, 0.60, 0.75, 0.40, 0.40, 0.45, True),
    VideoSource.HIGGSFIELD_DOP: _Profile(0.85, 0.80, 0.70, 0.50, 0.45, 0.50, True),
    VideoSource.HIGGSFIELD_EFFECT: _Profile(0.75, 0.70, 0.70, 0.60, 0.50, 0.40, True),
    VideoSource.FAL: _Profile(0.82, 0.60, 0.70, 0.45, 0.45, 0.45, True),
    VideoSource.LIVE_AVATAR: _Profile(0.80, 0.55, 0.65, 0.70, 0.35, 0.80, True),
}

_DEFAULT_PROFILE = _Profile(0.60, 0.40, 0.70, 0.70, 0.60, 0.40, False)


def _task_fit(source: VideoSource, profile: _Profile, context: dict) -> tuple[float, str]:
    """task_fit dinámico según el contexto del beat. Devuelve (score, razón)."""
    fit = 0.60
    reason_bits: list[str] = []

    if context.get("motion_required"):
        if profile.is_motion:
            fit += 0.30
            reason_bits.append("motion nativo")
        else:
            fit -= 0.20
            reason_bits.append("still→ken-burns")

    role = str(context.get("beat_role", "")).lower()
    if role == "hook":
        # El hook es stop-the-scroll: premia calidad/motion.
        fit += 0.10 * profile.quality
        reason_bits.append("hook premia calidad")
    elif role == "payoff" and profile.is_motion is not False:
        reason_bits.append("payoff callback genérico")

    if context.get("has_evidence") and profile.control >= 0.55:
        # Evidencia específica → premia control (prompts precisos).
        fit += 0.10
        reason_bits.append("evidencia→control")

    fit = max(0.0, min(1.0, fit))
    return fit, ", ".join(reason_bits) or "base"


def _profile_for(source: VideoSource) -> _Profile:
    return _PROFILES.get(source, _DEFAULT_PROFILE)


def score_providers(
    candidates: list[VideoSource],
    context: dict,
    *,
    weights: ScoreWeights | None = None,
) -> list[ProviderScore]:
    """Puntúa cada candidato en las 7 dimensiones. Orden = el de entrada."""
    w = weights or ScoreWeights()
    out: list[ProviderScore] = []
    for src in candidates:
        prof = _profile_for(src)
        task_fit, fit_reason = _task_fit(src, prof, context)
        dims = DimensionScores(
            task_fit=task_fit,
            quality=prof.quality,
            control=prof.control,
            reliability=prof.reliability,
            cost=prof.cost,
            latency=prof.latency,
            continuity=prof.continuity,
        )
        out.append(
            ProviderScore(
                provider=src.value,
                score=dims.weighted(w),
                dimensions=dims,
                reasons={
                    "task_fit": fit_reason,
                    "cost": "gratis" if prof.cost >= 0.99 else "de pago",
                    "motion": "motion nativo" if prof.is_motion else "still",
                },
            )
        )
    return out


def decide_provider(
    candidates: list[VideoSource],
    context: dict,
    *,
    weights: ScoreWeights | None = None,
    label: str = "",
) -> ProviderDecision:
    """Puntúa, ordena desc y elige el mejor — con trail de alternativas."""
    if not candidates:
        raise ValueError("decide_provider: candidates vacío")
    scores = score_providers(candidates, context, weights=weights)
    ranked = sorted(scores, key=lambda s: -s.score)  # estable en empates
    best = ranked[0]
    return ProviderDecision(
        chosen=best.provider,
        score=best.score,
        alternatives=ranked,
        context=dict(context),
        label=label,
    )


@dataclass
class DecisionLog:
    """Acumula `ProviderDecision` por beat; serializable a JSON para auditoría."""

    decisions: list[ProviderDecision] = field(default_factory=list)

    def record(self, decision: ProviderDecision) -> None:
        self.decisions.append(decision)

    def to_dict(self) -> dict:
        return {"decisions": [d.to_dict() for d in self.decisions]}

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
        return p


__all__ = [
    "DecisionLog",
    "DimensionScores",
    "ProviderDecision",
    "ProviderScore",
    "ScoreWeights",
    "decide_provider",
    "score_providers",
]
