"""Re-ranking VISUAL de B-roll vía CLIP texto↔imagen (ADR-025).

A diferencia de `broll_rerank` (texto↔texto sobre la metadata del provider),
este módulo rankea por lo que el clip **realmente muestra**: descarga los
candidatos, extrae frames y los embebe con un modelo CLIP que vive en el mismo
espacio vectorial que el texto del query.

Idea inspirada (NO copiada) en el corpus CLIP de OpenMontage (AGPLv3). Aquí se
reimplementa sobre `sentence-transformers` — que YA está en las deps base y
soporta modelos CLIP (`clip-ViT-B-32`) — en vez de `transformers` crudo, y se
adapta al flujo de selección beat-a-beat de `contenido`. Código propio
Apache-2.0. Ver ADR-025.

Diseño:
- `VisualEmbedder` (protocolo): `score(query_text, frame_paths) -> float`.
  Es texto↔imagen, distinto del `Embedder` (texto↔texto) de `broll_rerank`.
- `VisualClipEmbedder`: implementación real. Carga perezosa del modelo (lru_cache).
- `extract_frames`: muestrea N frames equiespaciados de un video (o devuelve la
  imagen tal cual si el media ya es una imagen). Inyectable (`runner`, `prober`).
- `rerank_visual`: ordena `MaterialInfo` ya descargados por score visual desc.
- `get_visual_reranker`: opt-in vía `config.visual.broll_visual_rerank`. Devuelve
  `None` si está deshabilitado o la lib falta → el caller mantiene el flujo léxico.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from loguru import logger

from core.visual.broll_embedder import _cosine
from core.visual.stock.cache import ProbeResult, probe_video
from shared.config import Config, load_config
from shared.schemas import MaterialInfo

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})

FrameExtractor = Callable[..., list[Path]]


@runtime_checkable
class VisualEmbedder(Protocol):
    """Backend de similitud texto↔imagen. `score` ∈ [0,1] (mayor = más relevante)."""

    def score(self, query_text: str, frame_paths: list[Path]) -> float:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def extract_frames(
    media_path: Path,
    n: int = 3,
    out_dir: Path | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    prober: Callable[[Path], ProbeResult] = probe_video,
) -> list[Path]:
    """Extrae hasta `n` frames JPEG equiespaciados de `media_path`.

    - Si el media ya es una imagen → lo devuelve tal cual (`[media_path]`).
    - Si es video → muestrea `n` timestamps que evitan los extremos
      (`dur * k/(n+1)` para k=1..n). Duración 0/desconocida → un único frame en t=0.
    - Frames que ffmpeg no logre escribir se omiten silenciosamente.
    """
    media_path = Path(media_path)
    if media_path.suffix.lower() in _IMAGE_SUFFIXES:
        return [media_path]

    out = Path(out_dir) if out_dir else media_path.parent
    out.mkdir(parents=True, exist_ok=True)

    duration = prober(media_path).duration_s
    if duration and duration > 0:
        timestamps = [duration * (k + 1) / (n + 1) for k in range(n)]
    else:
        timestamps = [0.0]

    frames: list[Path] = []
    for i, ts in enumerate(timestamps):
        dest = out / f"{media_path.stem}_f{i}.jpg"
        # `-ss` antes de `-i` = seek rápido por keyframe; suficiente para embedding.
        cmd = [
            "ffmpeg", "-y", "-ss", _fmt_ts(ts), "-i", str(media_path),
            "-frames:v", "1", "-q:v", "3", str(dest),
        ]
        try:
            proc = runner(cmd, capture_output=True, text=True, timeout=60, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning(f"[broll-visual] ffmpeg no disponible/timeout: {e}")
            break
        if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            frames.append(dest)
        else:
            logger.debug(f"[broll-visual] frame {i} falló para {media_path.name}")
    return frames


def _fmt_ts(ts: float) -> str:
    """Formatea un timestamp sin ceros/punto sobrantes (2.25, 4.5, 0)."""
    return f"{ts:g}"


# ---------------------------------------------------------------------------
# CLIP embedder
# ---------------------------------------------------------------------------


class VisualClipEmbedder:
    """Embedder visual CLIP (texto↔imagen). Carga del modelo perezosa.

    Para tests se inyecta un `model` con `.encode(list[str] | list[Image]) -> vecs`.
    El score de un clip es el **máximo** coseno texto↔frame (un solo frame muy
    on-topic basta para que el clip sea relevante).
    """

    def __init__(self, model_name: str = "clip-ViT-B-32", *, model: Any | None = None):
        self._model_name = model_name
        self._model = model

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def _embed_texts(self, texts: list[str]) -> list[Any]:
        return list(self._ensure_model().encode(texts))

    def _embed_images(self, frame_paths: list[Path]) -> list[Any]:
        from PIL import Image

        imgs = [Image.open(p).convert("RGB") for p in frame_paths]
        try:
            return list(self._ensure_model().encode(imgs))
        finally:
            for im in imgs:
                im.close()

    def score(self, query_text: str, frame_paths: list[Path]) -> float:
        if not frame_paths:
            return 0.0
        text_vec = self._embed_texts([query_text])[0]
        img_vecs = self._embed_images(frame_paths)
        sims = [max(0.0, _cosine(text_vec, iv)) for iv in img_vecs]
        return max(sims) if sims else 0.0


@lru_cache(maxsize=4)
def _load_visual_embedder(model_name: str) -> VisualClipEmbedder:
    """Cachea el wrapper (y el modelo cargado) por nombre."""
    return VisualClipEmbedder(model_name)


def _sentence_transformers_available() -> bool:
    """True si sentence-transformers se puede importar (deps base / extra)."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def get_visual_reranker(config: Config | None = None) -> VisualClipEmbedder | None:
    """Devuelve el reranker visual si está habilitado y disponible; si no, `None`.

    `None` → el caller mantiene el rerank léxico/semántico textual (ADR-018).
    """
    cfg = config or load_config()
    if not getattr(cfg.visual, "broll_visual_rerank", False):
        return None
    if not _sentence_transformers_available():
        logger.warning(
            "[broll-visual] broll_visual_rerank=true pero sentence-transformers no "
            "está instalado (pip install -e '.[broll-visual]'); usando rerank textual."
        )
        return None
    return _load_visual_embedder(getattr(cfg.visual, "broll_visual_model", "clip-ViT-B-32"))


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------


def rerank_visual(
    query: str,
    materials: list[MaterialInfo],
    embedder: VisualEmbedder,
    *,
    out_dir: Path,
    frames: int = 3,
    frame_extractor: FrameExtractor = extract_frames,
) -> list[MaterialInfo]:
    """Reordena `materials` (ya descargados) por relevancia VISUAL descendente.

    Cada material debe traer `local_path` poblado (la descarga ocurre antes, en
    el caller). Materiales sin `local_path` o sin frames extraíbles puntúan 0 y
    quedan al final, preservando su orden relativo (sort estable).
    """
    if not materials:
        return []

    scored: list[tuple[float, int, MaterialInfo]] = []
    for i, m in enumerate(materials):
        score = 0.0
        if m.local_path:
            try:
                frame_paths = frame_extractor(Path(m.local_path), n=frames, out_dir=out_dir)
                score = embedder.score(query, frame_paths)
            except Exception as e:  # nunca crashea el ranking por un clip malo
                logger.warning(f"[broll-visual] score falló para {m.url}: {e}")
                score = 0.0
        scored.append((score, i, m))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [m for _, _, m in scored]


__all__ = [
    "VisualClipEmbedder",
    "VisualEmbedder",
    "extract_frames",
    "get_visual_reranker",
    "rerank_visual",
]
