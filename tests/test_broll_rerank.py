"""Tests para core/visual/broll_rerank: re-ranking semántico de candidatos stock.

Idea reimplementada (NO copiada) de OpenMontage (corpus CLIP-indexado): en vez de
quedarnos con `results[0]` del provider, re-ordenamos los candidatos por relevancia
semántica contra la descripción RICA del beat (image_prompt + visual_anchor + text),
no sólo el query corto de búsqueda. Ver ADR-018.

Backend por defecto: scorer léxico determinista (sin deps, offline). Backend opcional
de embeddings (sentence-transformers) se inyecta vía protocolo `.similarity()`.

Cobertura:
1. material_text extrae palabras de tags + description + slug de url.
2. relevance_score: mayor para tags que matchean el query.
3. rerank reordena: un candidato posterior con mejores tags supera a results[0].
4. rerank es estable en empates (preserva orden original).
5. lista vacía → [].
6. query vacío / sólo stopwords → no crashea, preserva orden.
7. embedder inyectado controla el orden.
"""
from __future__ import annotations

from core.visual.broll_rerank import material_text, relevance_score, rerank
from shared.schemas import MaterialInfo, VideoSource


def _mat(url: str, *, tags: list[str] | None = None, description: str = "") -> MaterialInfo:
    return MaterialInfo(
        provider=VideoSource.PEXELS,
        url=url,
        duration_s=6.0,
        width=1080,
        height=1920,
        description=description,
        tags=tags or [],
    )


# =============================================================================
# material_text
# =============================================================================


def test_material_text_from_url_slug():
    m = _mat("https://www.pexels.com/video/drone-footage-of-a-city-12345/")
    text = material_text(m).lower()
    assert "drone" in text
    assert "city" in text


def test_material_text_includes_tags_and_description():
    m = _mat("https://x/clip.mp4", tags=["ocean", "waves"], description="calm sea")
    text = material_text(m).lower()
    assert "ocean" in text
    assert "waves" in text
    assert "calm" in text


# =============================================================================
# relevance_score
# =============================================================================


def test_relevance_higher_for_matching_tags():
    query = "a drone shot flying over the ocean waves"
    good = _mat("https://x/a.mp4", tags=["drone", "ocean", "waves"])
    bad = _mat("https://x/b.mp4", tags=["kitchen", "cooking", "food"])
    assert relevance_score(query, good) > relevance_score(query, bad)


def test_relevance_zero_when_no_overlap():
    query = "mountain snow winter"
    m = _mat("https://x/c.mp4", tags=["beach", "summer"])
    assert relevance_score(query, m) == 0.0


# =============================================================================
# rerank
# =============================================================================


def test_rerank_promotes_better_match_over_first():
    query = "neural network brain synapses firing"
    materials = [
        _mat("https://x/generic-office.mp4", tags=["office", "desk"]),
        _mat("https://x/brain.mp4", tags=["brain", "neural", "synapses"]),
    ]
    ranked = rerank(query, materials)
    assert ranked[0].url == "https://x/brain.mp4"


def test_rerank_stable_on_ties():
    query = "completely unrelated xyzzy"
    materials = [
        _mat("https://x/first.mp4", tags=["a"]),
        _mat("https://x/second.mp4", tags=["b"]),
    ]
    ranked = rerank(query, materials)
    # Empate en score 0 → preserva orden original (sort estable).
    assert [m.url for m in ranked] == [
        "https://x/first.mp4",
        "https://x/second.mp4",
    ]


def test_rerank_empty_returns_empty():
    assert rerank("anything", []) == []


def test_rerank_empty_query_preserves_order():
    materials = [_mat("https://x/a.mp4", tags=["x"]), _mat("https://x/b.mp4", tags=["y"])]
    ranked = rerank("", materials)
    assert [m.url for m in ranked] == ["https://x/a.mp4", "https://x/b.mp4"]


def test_rerank_with_injected_embedder():
    class FakeEmbedder:
        # Da score alto sólo al que contiene "winner".
        def similarity(self, a: str, b: str) -> float:
            return 1.0 if "winner" in b else 0.0

    materials = [
        _mat("https://x/loser.mp4", tags=["nope"]),
        _mat("https://x/winner.mp4", tags=["winner"]),
    ]
    ranked = rerank("query", materials, embedder=FakeEmbedder())
    assert ranked[0].url == "https://x/winner.mp4"
