"""Validación de ReelIdea + EditorialPlan contra el registry.

Reglas portadas de corredor-content:
- pillar referenciado debe existir
- audience referenciado debe existir
- platforms deben tener spec
- rotación: no más de 2 ideas del mismo pilar por semana
- approved_ideas suma costo estimado
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.editorial.loader import EditorialRegistry, load_editorial
from shared.schemas import EditorialPlan, ReelIdea


@dataclass
class ValidationIssue:
    """Issue encontrado durante validación. severity: 'error' bloquea, 'warning' avisa."""

    severity: str  # "error" | "warning"
    field: str
    message: str
    idea_id: str = ""

    def __str__(self) -> str:
        prefix = f"[{self.severity.upper()}]"
        loc = f" {self.idea_id}.{self.field}" if self.idea_id else f" {self.field}"
        return f"{prefix}{loc}: {self.message}"


@dataclass
class ValidationResult:
    """Resultado agregado. `ok` si no hay errors (warnings sí están permitidos)."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


def validate_idea(
    idea: ReelIdea,
    registry: EditorialRegistry | None = None,
) -> ValidationResult:
    """Valida una idea contra el registry editorial."""
    r = registry or load_editorial()
    result = ValidationResult()

    # Pillar registrado?
    if not r.has_pillar(idea.pillar):
        result.issues.append(
            ValidationIssue(
                severity="error",
                field="pillar",
                message=(
                    f"pillar '{idea.pillar}' no existe en editorial/pillars/. "
                    f"Disponibles: {list(r.pillars.keys())}"
                ),
                idea_id=idea.id,
            )
        )

    # Audience registrado?
    if r.audiences and not r.has_audience(idea.audience):
        result.issues.append(
            ValidationIssue(
                severity="error",
                field="audience",
                message=(
                    f"audience '{idea.audience}' no existe en editorial/audiences.json. "
                    f"Disponibles: {list(r.audiences.keys())}"
                ),
                idea_id=idea.id,
            )
        )

    # Platforms tienen spec?
    if r.platforms:
        for p in idea.platforms:
            if p not in r.platforms:
                result.issues.append(
                    ValidationIssue(
                        severity="warning",
                        field="platforms",
                        message=(
                            f"platform '{p.value}' sin spec en editorial/platforms.json — "
                            "se usará default"
                        ),
                        idea_id=idea.id,
                    )
                )

    return result


def validate_plan(
    plan: EditorialPlan,
    registry: EditorialRegistry | None = None,
    *,
    max_per_pillar: int = 2,
) -> ValidationResult:
    """Valida un plan completo, incluyendo regla de rotación de pilares."""
    r = registry or load_editorial()
    result = ValidationResult()

    # IDs únicos
    seen_ids: set[str] = set()
    for idea in plan.ideas:
        if idea.id in seen_ids:
            result.issues.append(
                ValidationIssue(
                    severity="error",
                    field="id",
                    message=f"id duplicado: '{idea.id}'",
                    idea_id=idea.id,
                )
            )
        seen_ids.add(idea.id)

        # Validación por idea
        idea_result = validate_idea(idea, r)
        result.issues.extend(idea_result.issues)

    # Rotación de pilares
    counts = plan.by_pillar()
    for pillar, count in counts.items():
        if count > max_per_pillar:
            result.issues.append(
                ValidationIssue(
                    severity="warning",
                    field="pillar_rotation",
                    message=(
                        f"pilar '{pillar}' aparece {count} veces "
                        f"(máx recomendado: {max_per_pillar})"
                    ),
                )
            )

    return result
