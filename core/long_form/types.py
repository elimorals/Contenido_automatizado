"""Errores y protocolos del módulo long-form."""
from __future__ import annotations

from typing import Protocol


class LongFormError(RuntimeError):
    """Base de errores del módulo long-form."""


class LongFormPlanError(LongFormError):
    """Falla durante planning (compress / RAG / script / scenes / storyboard)."""


class LongFormShootError(LongFormError):
    """Falla durante shooting (portraits / shots / stitch)."""


class EmbeddingProvider(Protocol):
    """Contrato mínimo del backend de embeddings.

    Permite swap entre sentence-transformers local y APIs remotas (OpenAI,
    Cohere, Voyage) sin cambiar el RAGStore.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Devuelve embeddings densos (lista paralela a `texts`)."""
        ...

    @property
    def dimension(self) -> int:
        ...


def resolve_provider(configured: str = "") -> str:
    """Provider LLM efectivo para el módulo long-form.

    `long_form.chat_model_provider` traía `"openrouter"` hardcodeado, así que un
    repo configurado con `llm.default_provider_premium = "openai"` fallaba con
    `[openrouter] api_key is required` — apuntando a un provider que el usuario
    nunca eligió. Mismo bug que tenía el stub del DAG narrativo.

    Vacío = hereda el premium global. Un valor explícito gana.
    """
    if configured:
        return configured
    try:
        from shared.config import load_config

        return load_config().llm_default_provider_premium or "openrouter"
    except Exception:  # noqa: BLE001 — sin config utilizable, default histórico
        return "openrouter"
