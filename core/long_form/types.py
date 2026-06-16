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
