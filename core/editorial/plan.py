"""Generación + carga + guardado de planes editoriales semanales.

Patrón portado de corredor-content/src/pipeline/plan.ts:
- 1 LLM call con system prompt que incluye brand-voice + pillars + facts + events
- Output: EditorialPlan validado con Zod/Pydantic
- Guardar a out/plans/plan-YYYY-Www.json con approved=False
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from uuid import uuid4

from loguru import logger
from pydantic import BaseModel, Field

from core.editorial.loader import EditorialRegistry, load_editorial
from core.llm_router import complete_structured
from shared.config import load_config
from shared.schemas import (
    DistributionPlatform,
    EditorialPlan,
    EntryType,
    ReelIdea,
)


def iso_week(when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.now(dt.timezone.utc)
    iso = when.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _plans_dir() -> Path:
    """out/plans/ — gitignored en .gitignore."""
    p = Path("out") / "plans"
    p.mkdir(parents=True, exist_ok=True)
    return p


def plan_path(week: str) -> Path:
    return _plans_dir() / f"plan-{week}.json"


def load_plan(week: str) -> EditorialPlan:
    """Carga un plan desde disk. Lanza FileNotFoundError si no existe."""
    p = plan_path(week)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return EditorialPlan(**raw)


def save_plan(plan: EditorialPlan) -> Path:
    """Guarda un plan a disk en out/plans/plan-YYYY-Www.json."""
    p = plan_path(plan.week)
    p.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return p


# =============================================================================
# Schema interno para la respuesta del LLM
# =============================================================================


class _PlanLLMResponse(BaseModel):
    """Lo que el LLM devuelve (envoltura con `ideas`)."""

    ideas: list[ReelIdea] = Field(..., min_length=1, max_length=20)


# =============================================================================
# Generador
# =============================================================================


def _events_for_month(registry: EditorialRegistry, month: int) -> list[str]:
    """Eventos del calendario activos en el mes dado."""
    out: list[str] = []
    for e in registry.local_events:
        if e.start_month <= month <= e.end_month:
            angles = "; ".join(e.angles[:3]) if e.angles else ""
            label = f"{e.name} ({e.location})" if e.location else e.name
            out.append(f"{label}" + (f" — ángulos: {angles}" if angles else ""))
    return out


def _build_system_prompt(
    registry: EditorialRegistry,
    n_ideas: int,
) -> str:
    """System prompt con brand voice + pillars + facts compactados."""
    parts: list[str] = []

    parts.append(
        f"Eres editor jefe de contenido. Tu trabajo es proponer {n_ideas} ideas "
        "de reels para esta semana, rotando pilares y aprovechando eventos del calendario."
    )

    if registry.brand_voice_md:
        parts.append("\n## VOZ DE MARCA\n" + registry.brand_voice_md)

    if registry.pillars:
        parts.append("\n## PILARES (id → descripción)")
        for pid, p in registry.pillars.items():
            parts.append(f"- `{pid}`: {p.label} — {p.description[:200]}")

    if registry.audiences:
        parts.append("\n## AUDIENCIAS (id → registro)")
        for aid, a in registry.audiences.items():
            parts.append(
                f"- `{aid}`: {a.label} — {a.voice_register} — {', '.join(a.interests[:3])}"
            )

    if registry.platforms:
        parts.append("\n## PLATAFORMAS DISPONIBLES")
        for pid in registry.platforms:
            parts.append(f"- `{pid.value}`")

    # Facts: solo nombres + claims breves (no toda la base) para no inflar tokens
    f = registry.facts
    if f.verified_people or f.verified_facts:
        parts.append("\n## HECHOS VERIFICADOS (usar SOLO estos para claims con número/nombre/año)")
        for p in f.verified_people[:30]:
            parts.append(f"- PERSONA: {p.name} — {p.field} — {p.relevance[:120]}")
        for fact in f.verified_facts[:30]:
            parts.append(f"- FACT: [{fact.id}] {fact.claim[:160]}")

    parts.append(
        f"\n## REGLAS\n"
        f"- Rota pilares: no más de 2 ideas del mismo pilar.\n"
        f"- Aprovecha eventos locales solo si encajan.\n"
        f"- Cada idea es concreta y accionable, no temas genéricos.\n"
        f"- `id` en kebab-case, único en el plan.\n"
        f"- `audience`: una sola por idea.\n"
        f"- `platforms`: elige 1-3 según formato natural.\n"
        f"- `entry_type`: 'topic' por default (entry al DAG /videos).\n"
        f"- `entry_value`: el topic concreto que el DAG ingestará.\n"
        f"- `rationale`: por qué ESTA semana, no por qué es buena en general.\n"
        f"- `approved` siempre false."
    )

    return "\n".join(parts)


def _build_user_prompt(registry: EditorialRegistry, n_ideas: int, week: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    upcoming = _events_for_month(registry, now.month)
    events_block = "\n".join(f"- {e}" for e in upcoming) if upcoming else "Ninguno relevante."

    pillar_ids = list(registry.pillars.keys()) or ["educacion", "historia", "ciencia"]
    audience_ids = list(registry.audiences.keys()) or ["general"]
    platform_vals = (
        [p.value for p in registry.platforms]
        if registry.platforms
        else ["tiktok", "instagram_reels", "youtube_shorts"]
    )

    return f"""Hoy es {now.strftime('%Y-%m-%d')} (semana ISO {week}).

EVENTOS DEL MES:
{events_block}

Genera EXACTAMENTE {n_ideas} ideas de reels, devuelve JSON con esta forma:

{{
  "ideas": [
    {{
      "id": "kebab-case",
      "title": "Título descriptivo 8-120 chars",
      "pillar": "<uno de: {', '.join(pillar_ids)}>",
      "audience": "<uno de: {', '.join(audience_ids)}>",
      "hook": "Frase de gancho 10-280 chars (curiosity gap, sin spoiler)",
      "rationale": "Por qué esta semana (10-400 chars)",
      "platforms": ["<uno o más de: {', '.join(platform_vals)}>"],
      "entry_type": "topic",
      "entry_value": "el topic concreto que el DAG va a producir (3+ chars)",
      "approved": false
    }}
  ]
}}"""


async def generate_plan(
    n_ideas: int = 7,
    *,
    registry: EditorialRegistry | None = None,
    provider_name: str | None = None,
) -> tuple[EditorialPlan, Path, float]:
    """Genera un plan editorial semanal.

    Returns: (plan, path_donde_se_guardó, costo_estimado_usd).
    """
    reg = registry or load_editorial()
    week = iso_week()

    cfg = load_config()
    provider_name = provider_name or cfg.llm_default_provider_premium

    system = _build_system_prompt(reg, n_ideas)
    user = _build_user_prompt(reg, n_ideas, week)

    logger.info(f"[editorial.plan] generando {n_ideas} ideas para {week} con {provider_name}")
    response: _PlanLLMResponse = await complete_structured(
        prompt=user,
        schema=_PlanLLMResponse,
        provider=provider_name,
        system=system,
        temperature=0.8,
        max_tokens=4000,
    )

    plan = EditorialPlan(
        week=week,
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        ideas=response.ideas,
    )

    out = save_plan(plan)
    logger.info(f"[editorial.plan] {len(plan.ideas)} ideas → {out}")
    return plan, out, 0.0  # cost se llena cuando providers reporten cost real
