"""Backend semántico OPCIONAL para el re-ranking de B-roll (ADR-018).

Implementa el protocolo `Embedder` de `broll_rerank` con sentence-transformers.
Es opt-in y pesado (torch), así que:
- `sentence-transformers` se importa PEREZOSAMENTE (extra `broll-semantic`).
- `get_broll_embedder(config)` devuelve None si está deshabilitado o la lib falta
  → `rerank` cae automáticamente al scorer léxico determinista.
- El modelo se carga UNA vez (lru_cache por nombre) — no por beat.

El cálculo de coseno es Python puro (sin numpy) para funcionar con listas o arrays.
"""
from __future__ import annotations

from functools import lru_cache
from math import sqrt
from typing import Any

from loguru import logger

from core.visual.broll_rerank import Embedder
from shared.config import Config, load_config


def _cosine(a: Any, b: Any) -> float:
    """Coseno entre dos vectores (listas o arrays). 0.0 si alguno es nulo."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sqrt(sum(x * x for x in a))
    nb = sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SentenceTransformerEmbedder:
    """Embedder semántico vía sentence-transformers. Carga del modelo perezosa.

    Para tests, se puede inyectar un `model` con `.encode(list[str]) -> list[vec]`.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        *,
        model: Any | None = None,
    ) -> None:
        self._model_name = model_name
        self._model = model

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def similarity(self, a: str, b: str) -> float:
        """Relevancia ∈ [0,1]: coseno de los embeddings, clamp de negativos a 0."""
        model = self._ensure_model()
        vec_a, vec_b = model.encode([a, b])
        return max(0.0, _cosine(vec_a, vec_b))


@lru_cache(maxsize=4)
def _load_embedder(model_name: str) -> SentenceTransformerEmbedder:
    """Cachea el wrapper (y por ende el modelo cargado) por nombre."""
    return SentenceTransformerEmbedder(model_name)


def _sentence_transformers_available() -> bool:
    """True si sentence-transformers se puede importar (extra `broll-semantic`)."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def get_broll_embedder(config: Config | None = None) -> Embedder | None:
    """Devuelve el embedder semántico si está habilitado y disponible; si no, None.

    None → `rerank` usa el scorer léxico (comportamiento por defecto, ADR-018).
    """
    cfg = config or load_config()
    if not getattr(cfg.visual, "broll_semantic_rerank", False):
        return None
    if not _sentence_transformers_available():
        logger.warning(
            "[broll] broll_semantic_rerank=true pero sentence-transformers no está "
            "instalado (pip install -e '.[broll-semantic]'); usando scorer léxico."
        )
        return None
    return _load_embedder(getattr(cfg.visual, "broll_embedder_model", "all-MiniLM-L6-v2"))


__all__ = ["SentenceTransformerEmbedder", "get_broll_embedder"]
