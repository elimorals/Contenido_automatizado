"""Integración del rerank visual CLIP en el selector de stock (ADR-025).

Verifica que `_resolve_stock_clip`:
- sin reranker visual: descarga el top léxico ([0]) — comportamiento ADR-018.
- con reranker visual: descarga top-K, los re-rankea por frames y elige el mejor.
- es robusto a descargas fallidas.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.visual.selector import _resolve_stock_clip
from shared.schemas import MaterialInfo, VideoSource


def _mat(url: str) -> MaterialInfo:
    return MaterialInfo(provider=VideoSource.PEXELS, url=url)


class _StubVisualEmbedder:
    def __init__(self, scores_by_url_token: dict[str, float]):
        self._scores = scores_by_url_token

    def score(self, query_text: str, frame_paths: list[Path]) -> float:
        # el frame se nombra por el clip → el token está en su nombre
        if not frame_paths:
            return 0.0
        for token, val in self._scores.items():
            if token in frame_paths[0].name:
                return val
        return 0.0


@pytest.mark.asyncio
async def test_resolve_without_visual_reranker_picks_first(tmp_path: Path) -> None:
    cands = [_mat("https://ex/first.mp4"), _mat("https://ex/second.mp4")]

    async def _fetch(url: str, **_kw: Any) -> Path:
        p = tmp_path / (url.rsplit("/", 1)[-1])
        p.write_bytes(b"v")
        return p

    result = await _resolve_stock_clip(
        "query", cands, out_dir=tmp_path, fetcher=_fetch, visual_reranker=None
    )
    assert result is not None
    material, path = result
    assert material.url == "https://ex/first.mp4"
    assert path.name == "first.mp4"


@pytest.mark.asyncio
async def test_resolve_with_visual_reranker_picks_best_frame(tmp_path: Path) -> None:
    # léxico puso 'first' arriba, pero visualmente 'second' es mucho mejor
    cands = [_mat("https://ex/first.mp4"), _mat("https://ex/second.mp4")]

    async def _fetch(url: str, **_kw: Any) -> Path:
        p = tmp_path / (url.rsplit("/", 1)[-1])
        p.write_bytes(b"v")
        return p

    def _extractor(media: Path, **_kw: Any) -> list[Path]:
        f = tmp_path / f"{media.stem}_f0.jpg"
        f.write_bytes(b"x")
        return [f]

    stub = _StubVisualEmbedder({"first": 0.10, "second": 0.95})
    result = await _resolve_stock_clip(
        "query",
        cands,
        out_dir=tmp_path,
        fetcher=_fetch,
        visual_reranker=stub,
        top_k=5,
        frame_extractor=_extractor,
    )
    assert result is not None
    material, _path = result
    assert material.url == "https://ex/second.mp4"


@pytest.mark.asyncio
async def test_resolve_with_visual_reranker_skips_failed_downloads(tmp_path: Path) -> None:
    cands = [_mat("https://ex/broken.mp4"), _mat("https://ex/ok.mp4")]

    async def _fetch(url: str, **_kw: Any) -> Path | None:
        if "broken" in url:
            return None  # descarga falló
        p = tmp_path / "ok.mp4"
        p.write_bytes(b"v")
        return p

    def _extractor(media: Path, **_kw: Any) -> list[Path]:
        f = tmp_path / f"{media.stem}_f0.jpg"
        f.write_bytes(b"x")
        return [f]

    stub = _StubVisualEmbedder({"ok": 0.5})
    result = await _resolve_stock_clip(
        "query",
        cands,
        out_dir=tmp_path,
        fetcher=_fetch,
        visual_reranker=stub,
        frame_extractor=_extractor,
    )
    assert result is not None
    material, _ = result
    assert material.url == "https://ex/ok.mp4"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_all_downloads_fail(tmp_path: Path) -> None:
    cands = [_mat("https://ex/a.mp4")]

    async def _fetch(url: str, **_kw: Any) -> None:
        return None

    result = await _resolve_stock_clip(
        "query", cands, out_dir=tmp_path, fetcher=_fetch, visual_reranker=None
    )
    assert result is None
