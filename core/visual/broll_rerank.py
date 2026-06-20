"""Re-ranking semántico de candidatos de B-roll (stock footage).

Idea reimplementada (NO copiada) de OpenMontage — que indexa un corpus libre
(Archive.org/NASA/Wikimedia) con embeddings CLIP y recupera por similitud semántica.
Aquí no tenemos los bytes de imagen al momento de seleccionar (sólo metadata del
provider), así que adaptamos el concepto: re-ordenamos los candidatos que devuelve
cada stock provider por relevancia entre la **descripción rica del beat**
(image_prompt + visual_anchor + texto) y el **texto de cada candidato** (tags +
description + slug de la url). Ver ADR-018. Código propio bajo Apache-2.0.

Backends:
- Por defecto: scorer léxico determinista (coseno sobre bolsa-de-palabras). Cero deps,
  offline, reproducible — siempre disponible.
- Opcional: cualquier objeto con `.similarity(a: str, b: str) -> float` (p.ej. un
  wrapper de sentence-transformers). Se inyecta vía `embedder=`.
"""
from __future__ import annotations

import math
import re
from typing import Protocol, runtime_checkable

from shared.schemas import MaterialInfo

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Stopwords mínimas EN+ES — evitan que palabras vacías inflen el score.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "and", "or",
        "is", "are", "was", "were", "be", "by", "as", "that", "this", "it", "its",
        "over", "from", "into", "shot", "video", "footage", "clip",
        "el", "la", "los", "las", "un", "una", "de", "del", "y", "o", "en", "con",
        "por", "para", "que", "se", "su", "al", "lo",
    }
)


@runtime_checkable
class Embedder(Protocol):
    """Protocolo opcional para un backend de similitud semántica real."""

    def similarity(self, a: str, b: str) -> float:  # pragma: no cover - protocolo
        ...


def _tokens(text: str) -> set[str]:
    """Tokeniza a minúsculas, descarta stopwords y tokens de 1 char."""
    return {
        t
        for t in _TOKEN_RE.findall(text.lower())
        if len(t) > 1 and t not in _STOPWORDS
    }


def _slug_words(url: str) -> str:
    """Extrae palabras descriptivas del slug de una url de stock.

    p.ej. '.../video/drone-footage-of-a-city-12345/' → 'drone footage of a city'.
    """
    # Quita esquema/host quedándose con el path; reemplaza separadores por espacio;
    # descarta segmentos puramente numéricos (ids) y extensiones.
    path = re.sub(r"^https?://[^/]+", "", url)
    raw = re.split(r"[/\-_.]+", path)
    words = [w for w in raw if w and not w.isdigit() and not w.startswith("mp4")]
    return " ".join(words)


def material_text(material: MaterialInfo) -> str:
    """Texto descriptivo agregado de un candidato: tags + description + slug."""
    parts = [
        " ".join(material.tags),
        material.description,
        _slug_words(material.url),
    ]
    return " ".join(p for p in parts if p).strip()


def _lexical_cosine(query: str, candidate_text: str) -> float:
    """Coseno sobre conjuntos de tokens (bolsa-de-palabras binaria)."""
    q = _tokens(query)
    c = _tokens(candidate_text)
    if not q or not c:
        return 0.0
    inter = len(q & c)
    if inter == 0:
        return 0.0
    return inter / math.sqrt(len(q) * len(c))


def relevance_score(
    query: str,
    material: MaterialInfo,
    embedder: Embedder | None = None,
) -> float:
    """Relevancia ∈ [0,1] entre `query` y el material.

    Usa `embedder.similarity` si se provee; si no, el coseno léxico determinista.
    """
    text = material_text(material)
    if embedder is not None:
        return float(embedder.similarity(query, text))
    return _lexical_cosine(query, text)


def rerank(
    query: str,
    materials: list[MaterialInfo],
    embedder: Embedder | None = None,
) -> list[MaterialInfo]:
    """Devuelve `materials` ordenados por relevancia descendente.

    Sort ESTABLE: empates preservan el orden original del provider (que ya viene
    rankeado por su propia relevancia). Nunca crashea ni filtra candidatos — sólo
    reordena, así que el caller puede seguir tomando `[0]` con confianza.
    """
    if not materials:
        return []
    scored = [
        (relevance_score(query, m, embedder), i, m)
        for i, m in enumerate(materials)
    ]
    # Orden por (-score, índice original) → desc por score, estable en empates.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [m for _, _, m in scored]


__all__ = [
    "Embedder",
    "material_text",
    "relevance_score",
    "rerank",
]
