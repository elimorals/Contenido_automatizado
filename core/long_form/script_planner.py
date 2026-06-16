"""ScriptPlanner — basic idea / compressed novel → 3-act NarrativeArc.

2 pasos (de ViMax):
1. Intent Router: detecta narrative | motion | montage
2. Script Plan: usa el template específico del intent para generar el arc

LLM backend: `core.llm_router.complete_structured` con Pydantic schemas
nativos del project.
"""
from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from core.llm_router import complete_structured
from core.long_form.prompts import (
    INTENT_ROUTER_HUMAN,
    INTENT_ROUTER_SYSTEM,
    MONTAGE_SCRIPT_SYSTEM,
    MOTION_SCRIPT_SYSTEM,
    NARRATIVE_SCRIPT_SYSTEM,
)
from shared.config import load_config
from shared.schemas import LongFormIntent, NarrativeArc


class _IntentRouterResponse(BaseModel):
    intent: LongFormIntent = Field(...)
    rationale: str = Field("")


async def detect_intent(
    basic_idea: str,
    *,
    provider: str | None = None,
) -> LongFormIntent:
    """Llama al intent router LLM y devuelve el enum."""
    cfg = load_config().long_form
    p = provider or cfg.chat_model_provider
    try:
        result = await complete_structured(
            prompt=INTENT_ROUTER_HUMAN.format(basic_idea=basic_idea),
            schema=_IntentRouterResponse,
            provider=p,
            system=INTENT_ROUTER_SYSTEM,
            temperature=0.0,
            max_tokens=200,
        )
        logger.info(f"[long_form.script_planner] intent routed: {result.intent.value} ({result.rationale})")
        return result.intent
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[long_form.script_planner] intent router falló ({e}); usando narrative")
        return LongFormIntent.NARRATIVE


def _system_template_for(intent: LongFormIntent) -> str:
    if intent == LongFormIntent.MOTION:
        return MOTION_SCRIPT_SYSTEM
    if intent == LongFormIntent.MONTAGE:
        return MONTAGE_SCRIPT_SYSTEM
    return NARRATIVE_SCRIPT_SYSTEM


class ScriptPlanner:
    """Expande una idea/text comprimido en un 3-act NarrativeArc.

    Uso:
        planner = ScriptPlanner()
        arc = await planner.plan(
            basic_idea="A time traveler loses memories each time he changes history",
            target_minutes=10.0,
        )
        # arc es NarrativeArc con act1/act2/act3 llenos
    """

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or cfg.chat_model_provider

    async def plan(
        self,
        basic_idea: str,
        *,
        target_minutes: float = 10.0,
        intent: LongFormIntent | None = None,
    ) -> NarrativeArc:
        """Genera el arc de 3 actos. Si `intent` es None, lo detecta."""
        actual_intent = intent or await detect_intent(basic_idea, provider=self.provider)
        system = _system_template_for(actual_intent).format(target_minutes=target_minutes)
        user = INTENT_ROUTER_HUMAN.format(basic_idea=basic_idea)

        arc = await complete_structured(
            prompt=user,
            schema=NarrativeArc,
            provider=self.provider,
            system=system,
            temperature=0.7,
            max_tokens=3000,
        )
        logger.info(
            f"[long_form.script_planner] arc generated: '{arc.title}' "
            f"({actual_intent.value}, {arc.target_minutes:.1f}min, {len(arc.themes)} themes)"
        )
        return arc
