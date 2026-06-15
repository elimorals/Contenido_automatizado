"""core.llm_router — abstracción async multi-provider sobre LLMs.

Ejemplo de uso:

    from core.llm_router import get_provider, complete

    # Provider explícito
    provider = get_provider("openrouter")
    text = await provider.complete("Hola, mundo", system="Eres conciso")

    # Helper a nivel módulo (usa el provider default de config)
    text = await complete("Hola", provider="anthropic")

    # Structured output
    from pydantic import BaseModel
    class Story(BaseModel):
        title: str
        beats: list[str]
    story = await provider.complete_structured("Un cuento corto", schema=Story)
"""
from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from core.llm_router.base import (
    LLMProvider,
    LLMProviderError,
    strip_think_blocks,
)
from core.llm_router.router import (
    PROVIDER_REGISTRY,
    available_providers,
    clear_cache,
    detect_provider_from_model,
    get_provider,
)

T = TypeVar("T", bound=BaseModel)

__all__ = [
    "LLMProvider",
    "LLMProviderError",
    "PROVIDER_REGISTRY",
    "available_providers",
    "clear_cache",
    "complete",
    "complete_structured",
    "detect_provider_from_model",
    "get_provider",
    "strip_think_blocks",
]


async def complete(
    prompt: str,
    *,
    provider: str = "openrouter",
    system: str = "",
    temperature: float = 0.7,
    max_tokens: int = 2000,
    model_name: str | None = None,
    **kwargs,
) -> str:
    """Helper a nivel módulo. Resuelve provider y delega `complete()`."""
    p = get_provider(provider, model_name=model_name)
    return await p.complete(
        prompt=prompt,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )


async def complete_structured(
    prompt: str,
    schema: type[T],
    *,
    provider: str = "openrouter",
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 2000,
    model_name: str | None = None,
    **kwargs,
) -> T:
    """Helper a nivel módulo para structured output."""
    p = get_provider(provider, model_name=model_name)
    return await p.complete_structured(
        prompt=prompt,
        schema=schema,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
