"""Editorial layer — portado de corredor-content.

Provee:
- Loader cacheado para brand-voice.md / pillars/ / facts.json / audiences.json / platforms.json
- Generador de plan semanal con gate humano
- Validación de ReelIdea contra registry + platform specs
- Anti-hallucination via facts.json (inyectable a hunters)
"""
from __future__ import annotations

from core.editorial.loader import (
    EditorialRegistry,
    facts_anti_hallucination_block,
    load_editorial,
    reload_editorial,
)
from core.editorial.plan import generate_plan, load_plan, save_plan
from core.editorial.validation import (
    ValidationIssue,
    validate_idea,
    validate_plan,
)

__all__ = [
    "EditorialRegistry",
    "ValidationIssue",
    "facts_anti_hallucination_block",
    "generate_plan",
    "load_editorial",
    "load_plan",
    "reload_editorial",
    "save_plan",
    "validate_idea",
    "validate_plan",
]
