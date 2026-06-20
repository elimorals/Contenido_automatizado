"""Tests para core/visual/broll_embedder: backend semántico opcional (ADR-018).

sentence-transformers es pesado (torch) y opt-in. La lógica se testea con un modelo
INYECTADO (sin la lib): coseno → [0,1], protocolo Embedder, y el factory que cae a
None cuando está deshabilitado o la lib falta (→ rerank usa el léxico).
"""
from __future__ import annotations

import core.visual.broll_embedder as emb_mod
from core.visual.broll_embedder import (
    SentenceTransformerEmbedder,
    _cosine,
    get_broll_embedder,
)
from core.visual.broll_rerank import Embedder
from shared.config import Config


class _FakeModel:
    """Modelo fake con .encode() determinista para tests sin torch."""

    def __init__(self, mapping: dict[str, list[float]]):
        self.mapping = mapping

    def encode(self, texts):
        return [self.mapping[t] for t in texts]


# =============================================================================
# _cosine
# =============================================================================


def test_cosine_identical_is_one():
    assert abs(_cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9


def test_cosine_orthogonal_is_zero():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_zero_vector_is_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# =============================================================================
# SentenceTransformerEmbedder (modelo inyectado)
# =============================================================================


def test_embedder_similarity_identical():
    emb = SentenceTransformerEmbedder(model=_FakeModel({"a": [1.0, 0.0], "b": [1.0, 0.0]}))
    assert abs(emb.similarity("a", "b") - 1.0) < 1e-9


def test_embedder_similarity_orthogonal():
    emb = SentenceTransformerEmbedder(model=_FakeModel({"a": [1.0, 0.0], "b": [0.0, 1.0]}))
    assert emb.similarity("a", "b") == 0.0


def test_embedder_clamps_negative_cosine_to_zero():
    emb = SentenceTransformerEmbedder(model=_FakeModel({"a": [1.0, 0.0], "b": [-1.0, 0.0]}))
    assert emb.similarity("a", "b") == 0.0


def test_embedder_satisfies_protocol():
    emb = SentenceTransformerEmbedder(model=_FakeModel({}))
    assert isinstance(emb, Embedder)


# =============================================================================
# get_broll_embedder (factory + fallback)
# =============================================================================


def test_get_embedder_none_when_disabled():
    cfg = Config()  # broll_semantic_rerank=False por default
    assert get_broll_embedder(cfg) is None


def test_get_embedder_none_when_lib_missing(monkeypatch):
    # Habilitado pero la lib no disponible → None (cae a léxico). Forzamos vía monkeypatch
    # para no depender de si sentence-transformers está o no instalado en el entorno.
    monkeypatch.setattr(emb_mod, "_sentence_transformers_available", lambda: False)
    cfg = Config()
    cfg.visual.broll_semantic_rerank = True
    assert get_broll_embedder(cfg) is None


def test_get_embedder_returns_embedder_when_available(monkeypatch):
    monkeypatch.setattr(emb_mod, "_sentence_transformers_available", lambda: True)
    cfg = Config()
    cfg.visual.broll_semantic_rerank = True
    result = get_broll_embedder(cfg)
    # Construcción es perezosa (no carga el modelo); sólo verificamos el contrato.
    assert isinstance(result, Embedder)
