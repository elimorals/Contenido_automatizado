"""Retorno de rendimiento hacia el planner editorial (ADR-031).

`generate_plan()` proponía ideas desde pilares, audiencias y eventos del
calendario — nunca desde qué había funcionado. Un generador que repite, no un
sistema que aprende.

Este módulo cierra el lazo leyendo los planes ya publicados de semanas
anteriores, pidiendo sus métricas con el `upload_request_id` que ADR-030
persiste, y destilando un bloque compacto que entra al system prompt del
planner.

Dos decisiones que sostienen todo lo demás:

- **Se ordena por `engagement_rate`, no por vistas.** Las vistas absolutas
  hablan del tamaño de la cuenta en cada red más que de la pieza; comparar los
  reels de TikTok contra los de LinkedIn por vistas solo dice que TikTok es más
  grande.
- **"Sin datos" nunca se convierte en "cero".** Una idea recién publicada, o una
  cuyo proveedor falló la lectura, se excluye del resumen. Si entrara como cero,
  el planner aprendería a evitar el pilar más reciente en vez del que rinde mal.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from core.distribution.analytics import PostMetrics
from shared.schemas import EditorialPlan, ReelIdea

_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")

# Mínimo de piezas medidas para que un pilar entre al resumen agregado. Con una
# sola pieza, el "promedio" del pilar es esa pieza y el planner sobreajusta.
MIN_SAMPLES_PER_PILLAR = 2


@dataclass
class IdeaPerformance:
    """Una idea publicada + lo que midió, por plataforma."""

    idea_id: str
    week: str
    pillar: str
    audience: str
    title: str
    hook: str
    published_at: str
    cost_usd: float | None = None
    metrics: dict[str, PostMetrics] = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return any(m.measured for m in self.metrics.values())

    @property
    def views(self) -> int:
        return sum(m.views for m in self.metrics.values())

    @property
    def interactions(self) -> int:
        return sum(m.interactions for m in self.metrics.values())

    @property
    def engagement_rate(self) -> float:
        return round(self.interactions / self.views, 4) if self.views else 0.0

    @property
    def cost_per_1k_views(self) -> float | None:
        """Cuánto costó producir mil vistas. `None` si falta costo o vistas."""
        if not self.cost_usd or not self.views:
            return None
        return round(self.cost_usd / (self.views / 1000), 4)


# =============================================================================
# Lectura de planes
# =============================================================================


def recent_weeks(n: int = 4, *, before: dt.datetime | None = None) -> list[str]:
    """Las `n` semanas ISO anteriores a la actual, de la más reciente atrás."""
    now = before or dt.datetime.now(dt.UTC)
    out: list[str] = []
    for i in range(1, n + 1):
        day = now - dt.timedelta(weeks=i)
        iso = day.isocalendar()
        out.append(f"{iso.year}-W{iso.week:02d}")
    return out


def available_weeks(plans_dir: Path | None = None) -> list[str]:
    """Semanas con plan en disco, de la más reciente a la más antigua."""
    base = plans_dir or (Path("out") / "plans")
    if not base.exists():
        return []
    weeks: list[str] = []
    for path in base.glob("plan-*.json"):
        week = path.stem.removeprefix("plan-")
        if _WEEK_RE.match(week):
            weeks.append(week)
    return sorted(weeks, reverse=True)


def _load_plan_quietly(week: str, plans_dir: Path | None = None) -> EditorialPlan | None:
    base = plans_dir or (Path("out") / "plans")
    path = base / f"plan-{week}.json"
    if not path.exists():
        return None
    try:
        return EditorialPlan(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001 — un plan corrupto no tumba el planner
        logger.warning(f"[performance] plan {week} ilegible ({e}); lo salto")
        return None


def published_ideas(
    weeks: list[str], plans_dir: Path | None = None
) -> list[tuple[str, ReelIdea]]:
    """`(week, idea)` de las ideas que llegaron a publicarse."""
    out: list[tuple[str, ReelIdea]] = []
    for week in weeks:
        plan = _load_plan_quietly(week, plans_dir)
        if plan is None:
            continue
        for idea in plan.ideas:
            if idea.published_at and idea.upload_request_id:
                out.append((week, idea))
    return out


# =============================================================================
# Recolección
# =============================================================================


async def collect_performance(
    weeks: list[str] | None = None,
    *,
    service: object | None = None,
    plans_dir: Path | None = None,
    max_ideas: int = 40,
) -> list[IdeaPerformance]:
    """Lee planes publicados y les pide métricas al servicio de distribución.

    `service` es cualquier cosa con `get_post_analytics(request_id)`; si es
    `None` se resuelve del config. Sin servicio configurado devuelve `[]` — el
    planner sigue funcionando exactamente como antes de existir este módulo.
    """
    weeks = weeks or recent_weeks(4)
    if service is None:
        from core.distribution import get_distribution_service
        from shared.config import load_config

        service = get_distribution_service(load_config())
    if service is None:
        logger.info("[performance] sin servicio de distribución; sin retorno de datos")
        return []

    pairs = published_ideas(weeks, plans_dir)[:max_ideas]
    if not pairs:
        logger.info(f"[performance] ninguna idea publicada en {weeks}")
        return []

    out: list[IdeaPerformance] = []
    for week, idea in pairs:
        try:
            metrics = await service.get_post_analytics(idea.upload_request_id or "")
        except Exception as e:  # noqa: BLE001 — analytics es informativo
            logger.warning(f"[performance] {idea.id}: analytics falló ({e})")
            continue
        out.append(
            IdeaPerformance(
                idea_id=idea.id,
                week=week,
                pillar=idea.pillar,
                audience=idea.audience,
                title=idea.title,
                hook=idea.hook,
                published_at=idea.published_at or "",
                cost_usd=idea.cost_usd,
                metrics=metrics,
            )
        )
    measured = sum(1 for p in out if p.measured)
    logger.info(
        f"[performance] {len(out)} ideas publicadas, {measured} con métricas"
    )
    return out


# =============================================================================
# Agregación y resumen
# =============================================================================


def aggregate_by_pillar(perfs: list[IdeaPerformance]) -> dict[str, dict[str, float]]:
    """Promedios por pilar, solo sobre piezas efectivamente medidas."""
    buckets: dict[str, list[IdeaPerformance]] = {}
    for p in perfs:
        if p.measured:
            buckets.setdefault(p.pillar, []).append(p)

    out: dict[str, dict[str, float]] = {}
    for pillar, items in buckets.items():
        if len(items) < MIN_SAMPLES_PER_PILLAR:
            continue
        out[pillar] = {
            "n": float(len(items)),
            "avg_views": round(sum(i.views for i in items) / len(items), 1),
            "avg_engagement": round(
                sum(i.engagement_rate for i in items) / len(items), 4
            ),
        }
    return out


def summarize_for_planner(perfs: list[IdeaPerformance], *, top_n: int = 5) -> str:
    """Bloque compacto de "qué funcionó" para el system prompt del planner.

    Devuelve `""` cuando no hay nada medido — así el caller puede omitir la
    sección entera en vez de inyectar un encabezado vacío que el LLM tomaría
    como señal de que todo rinde mal.
    """
    measured = [p for p in perfs if p.measured]
    if not measured:
        return ""

    ranked = sorted(measured, key=lambda p: p.engagement_rate, reverse=True)
    lines = [
        "\n## RENDIMIENTO REAL DE SEMANAS ANTERIORES",
        f"({len(measured)} piezas con métricas. El engagement es interacciones "
        f"por vista — comparable entre plataformas; las vistas absolutas no lo son.)",
        "\n### Lo que mejor funcionó",
    ]
    for p in ranked[:top_n]:
        lines.append(
            f'- [{p.pillar}] "{p.title[:70]}" — {p.views:,} vistas, '
            f"engagement {p.engagement_rate:.2%} · hook: \"{p.hook[:90]}\""
        )

    if len(ranked) > top_n:
        lines.append("\n### Lo que peor funcionó")
        for p in ranked[-min(top_n, len(ranked) - top_n):]:
            lines.append(
                f'- [{p.pillar}] "{p.title[:70]}" — {p.views:,} vistas, '
                f"engagement {p.engagement_rate:.2%}"
            )

    by_pillar = aggregate_by_pillar(measured)
    if by_pillar:
        lines.append("\n### Promedio por pilar")
        for pillar, stats in sorted(
            by_pillar.items(), key=lambda kv: kv[1]["avg_engagement"], reverse=True
        ):
            lines.append(
                f"- `{pillar}`: {stats['avg_engagement']:.2%} engagement, "
                f"{stats['avg_views']:,.0f} vistas de media (n={int(stats['n'])})"
            )

    lines.append(
        "\nUsa esto como SEÑAL, no como molde: repetir el tema ganador satura. "
        "Fíjate en qué ÁNGULO y qué tipo de hook funcionó, y aplícalo a temas "
        "nuevos. Sigue rotando pilares aunque uno rinda mejor."
    )
    return "\n".join(lines)


__all__ = [
    "MIN_SAMPLES_PER_PILLAR",
    "IdeaPerformance",
    "aggregate_by_pillar",
    "available_weeks",
    "collect_performance",
    "published_ideas",
    "recent_weeks",
    "summarize_for_planner",
]
