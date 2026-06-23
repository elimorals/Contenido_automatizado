"""Tests para el re-ranking VISUAL de B-roll vía CLIP (ADR-025).

A diferencia de `test_broll_rerank` (texto↔texto sobre metadata), aquí el
ranking se basa en frames reales del clip embebidos con CLIP (texto↔imagen).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from core.visual.broll_visual import (
    VisualClipEmbedder,
    extract_frames,
    get_visual_reranker,
    rerank_visual,
)
from shared.schemas import MaterialInfo, VideoSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _png(path: Path) -> Path:
    """Escribe un PNG mínimo real (2x2) para que PIL pueda abrirlo."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), color=(10, 20, 30)).save(path)
    return path


class _FakeClipModel:
    """Modelo CLIP fake: `.encode(list[str])` → text_vec; `.encode(list[Image])`
    → vectores posicionales (en el orden recibido)."""

    def __init__(self, text_vec: list[float], image_vecs: list[list[float]]):
        self._text_vec = text_vec
        self._image_vecs = image_vecs

    def encode(self, inputs: list[Any]) -> list[list[float]]:
        if inputs and isinstance(inputs[0], str):
            return [list(self._text_vec) for _ in inputs]
        return [list(v) for v in self._image_vecs]


class _StubVisualEmbedder:
    """Embedder visual que devuelve un score fijo por el primer frame path."""

    def __init__(self, scores_by_first_frame: dict[str, float]):
        self._scores = scores_by_first_frame

    def score(self, query_text: str, frame_paths: list[Path]) -> float:
        if not frame_paths:
            return 0.0
        return self._scores.get(frame_paths[0].name, 0.0)


def _material(url: str, local_path: str = "") -> MaterialInfo:
    return MaterialInfo(provider=VideoSource.PEXELS, url=url, local_path=local_path)


# ---------------------------------------------------------------------------
# 1. extract_frames
# ---------------------------------------------------------------------------


def test_extract_frames_image_returns_itself(tmp_path: Path) -> None:
    img = _png(tmp_path / "still.jpg")
    frames = extract_frames(img, n=3, out_dir=tmp_path / "frames")
    assert frames == [img]


def test_extract_frames_video_extracts_n_evenly_spaced(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    out_dir = tmp_path / "frames"

    calls: list[list[str]] = []

    def _fake_runner(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        calls.append(cmd)
        # ffmpeg "escribe" el frame de salida (último arg)
        Path(cmd[-1]).write_bytes(b"jpgbytes")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _fake_prober(_p: Path) -> Any:
        from core.visual.stock.cache import ProbeResult

        return ProbeResult(duration_s=9.0, fps=30.0)

    frames = extract_frames(
        video, n=3, out_dir=out_dir, runner=_fake_runner, prober=_fake_prober
    )

    assert len(frames) == 3
    assert all(f.exists() for f in frames)
    # timestamps equiespaciados evitando extremos: 9 * 1/4, 2/4, 3/4 = 2.25, 4.5, 6.75
    seek_values = [cmd[cmd.index("-ss") + 1] for cmd in calls]
    assert seek_values == ["2.25", "4.5", "6.75"]


def test_extract_frames_zero_duration_falls_back_to_single_frame(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    def _fake_runner(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        Path(cmd[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def _fake_prober(_p: Path) -> Any:
        from core.visual.stock.cache import ProbeResult

        return ProbeResult(duration_s=0.0, fps=0.0)

    frames = extract_frames(
        video, n=3, out_dir=tmp_path / "f", runner=_fake_runner, prober=_fake_prober
    )
    assert len(frames) == 1


def test_extract_frames_skips_failed_ffmpeg(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    def _fake_runner(cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(cmd, 1, "", "boom")  # no escribe archivo

    def _fake_prober(_p: Path) -> Any:
        from core.visual.stock.cache import ProbeResult

        return ProbeResult(duration_s=6.0, fps=30.0)

    frames = extract_frames(
        video, n=2, out_dir=tmp_path / "f", runner=_fake_runner, prober=_fake_prober
    )
    assert frames == []


# ---------------------------------------------------------------------------
# 2. VisualClipEmbedder.score
# ---------------------------------------------------------------------------


def test_visual_clip_embedder_score_is_max_over_frames(tmp_path: Path) -> None:
    f1 = _png(tmp_path / "f1.jpg")
    f2 = _png(tmp_path / "f2.jpg")
    # text=[1,0]; img1=[1,0] (sim 1.0), img2=[0,1] (sim 0.0) → max = 1.0
    model = _FakeClipModel(text_vec=[1.0, 0.0], image_vecs=[[1.0, 0.0], [0.0, 1.0]])
    emb = VisualClipEmbedder(model=model)
    assert emb.score("a blue ocean", [f1, f2]) == pytest.approx(1.0)


def test_visual_clip_embedder_no_frames_returns_zero() -> None:
    model = _FakeClipModel(text_vec=[1.0, 0.0], image_vecs=[])
    emb = VisualClipEmbedder(model=model)
    assert emb.score("anything", []) == 0.0


def test_visual_clip_embedder_clamps_negative_to_zero(tmp_path: Path) -> None:
    f1 = _png(tmp_path / "f1.jpg")
    # text=[1,0], img=[-1,0] → cosine -1 → clamp 0
    model = _FakeClipModel(text_vec=[1.0, 0.0], image_vecs=[[-1.0, 0.0]])
    emb = VisualClipEmbedder(model=model)
    assert emb.score("x", [f1]) == 0.0


# ---------------------------------------------------------------------------
# 3. rerank_visual
# ---------------------------------------------------------------------------


def test_rerank_visual_orders_by_visual_score(tmp_path: Path) -> None:
    m_low = _material("https://ex/low.mp4", local_path=str(tmp_path / "low.mp4"))
    m_high = _material("https://ex/high.mp4", local_path=str(tmp_path / "high.mp4"))
    for m in (m_low, m_high):
        Path(m.local_path).write_bytes(b"v")

    def _extractor(media: Path, **_kw: Any) -> list[Path]:
        # un frame nombrado como el clip → el stub puntúa por ese nombre
        frame = tmp_path / f"{media.stem}_f0.jpg"
        frame.write_bytes(b"x")
        return [frame]

    stub = _StubVisualEmbedder({"low_f0.jpg": 0.10, "high_f0.jpg": 0.90})
    ordered = rerank_visual(
        "query", [m_low, m_high], embedder=stub, out_dir=tmp_path, frame_extractor=_extractor
    )
    assert [m.url for m in ordered] == ["https://ex/high.mp4", "https://ex/low.mp4"]


def test_rerank_visual_stable_on_ties(tmp_path: Path) -> None:
    a = _material("https://ex/a.mp4", local_path=str(tmp_path / "a.mp4"))
    b = _material("https://ex/b.mp4", local_path=str(tmp_path / "b.mp4"))
    for m in (a, b):
        Path(m.local_path).write_bytes(b"v")

    def _extractor(media: Path, **_kw: Any) -> list[Path]:
        frame = tmp_path / f"{media.stem}_f0.jpg"
        frame.write_bytes(b"x")
        return [frame]

    stub = _StubVisualEmbedder({"a_f0.jpg": 0.5, "b_f0.jpg": 0.5})
    ordered = rerank_visual(
        "q", [a, b], embedder=stub, out_dir=tmp_path, frame_extractor=_extractor
    )
    assert [m.url for m in ordered] == ["https://ex/a.mp4", "https://ex/b.mp4"]


def test_rerank_visual_material_without_local_path_scored_last(tmp_path: Path) -> None:
    have = _material("https://ex/have.mp4", local_path=str(tmp_path / "have.mp4"))
    Path(have.local_path).write_bytes(b"v")
    missing = _material("https://ex/missing.mp4", local_path="")  # sin descarga

    def _extractor(media: Path, **_kw: Any) -> list[Path]:
        frame = tmp_path / f"{media.stem}_f0.jpg"
        frame.write_bytes(b"x")
        return [frame]

    stub = _StubVisualEmbedder({"have_f0.jpg": 0.3})
    ordered = rerank_visual(
        "q", [missing, have], embedder=stub, out_dir=tmp_path, frame_extractor=_extractor
    )
    assert ordered[0].url == "https://ex/have.mp4"
    assert ordered[-1].url == "https://ex/missing.mp4"


def test_rerank_visual_empty_returns_empty(tmp_path: Path) -> None:
    assert rerank_visual("q", [], embedder=_StubVisualEmbedder({}), out_dir=tmp_path) == []


# ---------------------------------------------------------------------------
# 4. get_visual_reranker (opt-in)
# ---------------------------------------------------------------------------


def test_get_visual_reranker_disabled_returns_none() -> None:
    from shared.config import load_config

    cfg = load_config()
    cfg.visual.broll_visual_rerank = False
    assert get_visual_reranker(cfg) is None


def test_get_visual_reranker_enabled_but_lib_missing_returns_none(monkeypatch) -> None:
    from shared.config import load_config

    cfg = load_config()
    cfg.visual.broll_visual_rerank = True
    monkeypatch.setattr(
        "core.visual.broll_visual._sentence_transformers_available", lambda: False
    )
    assert get_visual_reranker(cfg) is None


def test_get_visual_reranker_enabled_and_available_returns_embedder(monkeypatch) -> None:
    from shared.config import load_config

    cfg = load_config()
    cfg.visual.broll_visual_rerank = True
    cfg.visual.broll_visual_model = "clip-ViT-B-32"
    monkeypatch.setattr(
        "core.visual.broll_visual._sentence_transformers_available", lambda: True
    )
    reranker = get_visual_reranker(cfg)
    assert isinstance(reranker, VisualClipEmbedder)
