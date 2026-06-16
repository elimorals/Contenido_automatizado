"""RAG store híbrido: numpy default, FAISS opt-in.

Backend numpy (default):
- Cosine similarity en vectores densos
- Cache en disk como `.npz` para resume después de crash
- Suficiente hasta ~5k chunks (libros enciclopédicos)

Backend FAISS (opt-in via `cfg.long_form.faiss_enabled=true`):
- Index HNSW (M=32, efConstruction=200) — escala a millones
- Persistencia con `faiss.write_index` / `read_index`

Splitter: `RecursiveCharacterTextSplitter` de langchain-text-splitters
(genuinamente mejor que casero — maneja markdown headers, párrafos,
oraciones en cascade).

Embeddings: sentence-transformers (BAAI/bge-small-en-v1.5 default, multilang
si usuario cambia a `BAAI/bge-m3`).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from core.long_form.types import EmbeddingProvider, LongFormPlanError
from shared.config import LongFormConfig, load_config


# =============================================================================
# Splitter wrapper (recomendado de ViMax/LangChain)
# =============================================================================


def chunk_text(
    text: str,
    *,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[str]:
    """Divide texto largo en chunks con overlap (para preservar contexto cross-chunk).

    Usa `RecursiveCharacterTextSplitter` con separadores en cascade:
    ['\\n\\n', '\\n', '. ', ' ', ''] — corta primero por párrafos, luego oraciones.
    """
    cfg = load_config().long_form
    cs = chunk_size or cfg.chunk_size_chars
    co = chunk_overlap or cfg.chunk_overlap_chars
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cs,
        chunk_overlap=co,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


# =============================================================================
# Sentence-Transformers embedding provider
# =============================================================================


class STEmbeddingProvider:
    """Backend de embeddings vía sentence-transformers (local, sin API).

    Lazy-loads el modelo en primer `embed()`. Cache del modelo via
    HF_HOME standard (~/.cache/huggingface/).
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        device: str = "cpu",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self._model: Any | None = None
        self._dim: int = 0

    def _ensure_model(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info(f"[long_form.rag] cargando embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name, device=self.device)
            # bge-small=384, bge-base=768, bge-large=1024
            self._dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    @property
    def dimension(self) -> int:
        if self._dim == 0:
            self._ensure_model()
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        # bge models necesitan prefix "Represent this sentence for searching relevant passages: "
        # para query; para passages NO. Detectamos heurísticamente: si 1 solo texto y es < 200
        # chars asumimos query, sino passage.
        # Esto es el truco recomendado por BAAI bge.
        normalized = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return normalized.tolist()


def get_default_embedder(cfg: LongFormConfig | None = None) -> EmbeddingProvider:
    cfg = cfg or load_config().long_form
    return STEmbeddingProvider(
        model_name=cfg.embedding_model_name,
        device=cfg.embedding_device,
    )


# =============================================================================
# RAG Store — numpy default, FAISS opt-in
# =============================================================================


@dataclass
class _StoredChunk:
    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class RAGStore:
    """Store híbrido: numpy default, FAISS opt-in via config.

    Uso típico:
        store = RAGStore()
        store.add(chunks=["...chapter 1...", "...chapter 2..."],
                  metadatas=[{"chapter": 1}, {"chapter": 2}])
        results = store.search("what happens to the protagonist?", top_k=3)
        for chunk, score in results:
            ...

    Persistencia (opcional):
        store.save_to_dir(Path("/tmp/rag_store"))
        store2 = RAGStore.load_from_dir(Path("/tmp/rag_store"))
    """

    def __init__(
        self,
        embedder: EmbeddingProvider | None = None,
        *,
        use_faiss: bool | None = None,
    ) -> None:
        cfg = load_config().long_form
        self.embedder = embedder or get_default_embedder(cfg)
        # Resolve backend
        self.use_faiss = cfg.faiss_enabled if use_faiss is None else use_faiss
        self._faiss_index: Any | None = None
        self._embeddings: np.ndarray | None = None  # backend numpy
        self._chunks: list[_StoredChunk] = []

    # === Mutation ===

    def add(
        self,
        chunks: list[str],
        *,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> None:
        if not chunks:
            return
        if metadatas is None:
            metadatas = [{} for _ in chunks]
        if ids is None:
            ids = [_text_hash(c) for c in chunks]
        if not (len(chunks) == len(metadatas) == len(ids)):
            raise LongFormPlanError(
                f"add: lengths mismatch chunks={len(chunks)} meta={len(metadatas)} ids={len(ids)}"
            )

        vectors = np.asarray(self.embedder.embed(chunks), dtype=np.float32)
        # Append a internal state
        for c, m, i in zip(chunks, metadatas, ids):
            self._chunks.append(_StoredChunk(chunk_id=i, text=c, metadata=m))

        if self.use_faiss:
            self._add_to_faiss(vectors)
        else:
            self._embeddings = (
                np.vstack([self._embeddings, vectors])
                if self._embeddings is not None
                else vectors
            )

    def _add_to_faiss(self, vectors: np.ndarray) -> None:
        try:
            import faiss  # type: ignore[import-not-found]
        except ImportError as e:
            raise LongFormPlanError(
                "RAGStore.use_faiss=True pero faiss no instalado. "
                "Corre: uv sync --extra longform-scale"
            ) from e
        if self._faiss_index is None:
            dim = vectors.shape[1]
            # HNSW32 es buen default (M=32 connections, fast + accurate)
            self._faiss_index = faiss.IndexHNSWFlat(dim, 32)
            self._faiss_index.hnsw.efConstruction = 200
        self._faiss_index.add(vectors)

    # === Search ===

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[tuple[_StoredChunk, float]]:
        """Devuelve top-k chunks ranqueados por cosine similarity.

        Score range: 1.0 (idéntico) → -1.0. Para BGE normalized vectors,
        cosine = dot product, así que `np.dot` es lo eficiente.
        """
        if not self._chunks:
            return []
        cfg = load_config().long_form
        k = min(top_k or cfg.top_k_retrieval, len(self._chunks))
        query_vec = np.asarray(self.embedder.embed([query]), dtype=np.float32)[0]

        if self.use_faiss and self._faiss_index is not None:
            # FAISS HNSW devuelve L2 distance; convertimos a cosine (vectors normalizados)
            # Para vectores L2-normalized: cosine = 1 - L2^2/2
            distances, indices = self._faiss_index.search(query_vec.reshape(1, -1), k)
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0 or idx >= len(self._chunks):
                    continue
                cosine = 1.0 - float(dist) / 2.0
                results.append((self._chunks[idx], cosine))
            return results

        # Backend numpy — cosine = dot product (vectors normalized)
        if self._embeddings is None:
            return []
        scores = self._embeddings @ query_vec  # shape (N,)
        # Top-k indices descending
        if len(scores) <= k:
            sorted_idx = np.argsort(-scores)
        else:
            # argpartition es O(N), mucho más rápido que argsort para k pequeño
            sorted_idx = np.argpartition(-scores, k)[:k]
            sorted_idx = sorted_idx[np.argsort(-scores[sorted_idx])]
        return [(self._chunks[i], float(scores[i])) for i in sorted_idx]

    # === Persistence ===

    def save_to_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        chunks_meta = [
            {"id": c.chunk_id, "text": c.text, "metadata": c.metadata}
            for c in self._chunks
        ]
        (path / "chunks.json").write_text(
            json.dumps(chunks_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        config = {
            "use_faiss": self.use_faiss,
            "embedding_model": getattr(self.embedder, "model_name", ""),
            "dimension": self.embedder.dimension,
        }
        (path / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")

        if self.use_faiss and self._faiss_index is not None:
            try:
                import faiss  # type: ignore[import-not-found]
                faiss.write_index(self._faiss_index, str(path / "faiss.index"))
            except ImportError:
                logger.warning("[long_form.rag] save: faiss no disponible para persistir")
        elif self._embeddings is not None:
            np.save(str(path / "embeddings.npy"), self._embeddings)
        logger.info(f"[long_form.rag] saved store {len(self._chunks)} chunks → {path}")

    @classmethod
    def load_from_dir(
        cls,
        path: Path,
        embedder: EmbeddingProvider | None = None,
    ) -> RAGStore:
        if not path.is_dir():
            raise LongFormPlanError(f"load_from_dir: no existe {path}")
        config = json.loads((path / "config.json").read_text(encoding="utf-8"))
        store = cls(embedder=embedder, use_faiss=bool(config.get("use_faiss")))

        chunks_raw = json.loads((path / "chunks.json").read_text(encoding="utf-8"))
        store._chunks = [
            _StoredChunk(chunk_id=c["id"], text=c["text"], metadata=c.get("metadata", {}))
            for c in chunks_raw
        ]

        if store.use_faiss:
            try:
                import faiss  # type: ignore[import-not-found]
                store._faiss_index = faiss.read_index(str(path / "faiss.index"))
            except (ImportError, RuntimeError) as e:
                raise LongFormPlanError(
                    f"load: store guardado con FAISS pero no se pudo cargar ({e})"
                ) from e
        else:
            emb_path = path / "embeddings.npy"
            if emb_path.exists():
                store._embeddings = np.load(str(emb_path))
        return store

    def __len__(self) -> int:
        return len(self._chunks)
