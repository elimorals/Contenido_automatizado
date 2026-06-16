"""NovelCompressor — chunk + compress + aggregate (de ViMax/agents/novel_compressor.py).

Pipeline:
1. `split()` — RecursiveCharacterTextSplitter divide el libro en chunks
2. `compress()` — N chunks en paralelo (semáforo 5 default) → compressed chunks
3. `aggregate()` — opcional, fusiona los compressed chunks resolviendo overlap

Después del compress, el output cabe en el contexto del Script Planner
sin perder eventos clave.

LLM backend: reusamos `core.llm_router.complete` (NO LangChain init_chat_model).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger

from core.llm_router import complete
from core.long_form.prompts import (
    COMPRESS_NOVEL_CHUNK_HUMAN,
    COMPRESS_NOVEL_CHUNK_SYSTEM,
)
from core.long_form.rag import chunk_text
from shared.config import load_config


@dataclass
class CompressionResult:
    """Resultado de comprimir un solo chunk."""

    index: int
    original_chars: int
    compressed_text: str

    @property
    def ratio(self) -> float:
        return len(self.compressed_text) / max(self.original_chars, 1)


class NovelCompressor:
    """Comprime chunks largos de novela preservando plot + characters + dialogue.

    Uso:
        compressor = NovelCompressor()
        chunks = compressor.split(book_text)
        results = await compressor.compress_all(chunks)
        # results es lista de CompressionResult ordenada por index
    """

    def __init__(
        self,
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        provider: str | None = None,
        max_concurrent: int = 5,
    ) -> None:
        cfg = load_config().long_form
        self.chunk_size = chunk_size or cfg.chunk_size_chars
        self.chunk_overlap = chunk_overlap or cfg.chunk_overlap_chars
        self.provider = provider or cfg.chat_model_provider
        self.max_concurrent = max_concurrent

    def split(self, novel_text: str) -> list[str]:
        return chunk_text(
            novel_text,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

    async def _compress_one(
        self,
        sem: asyncio.Semaphore,
        index: int,
        chunk: str,
    ) -> CompressionResult:
        async with sem:
            logger.debug(f"[long_form.compressor] compressing chunk {index} ({len(chunk)} chars)")
            user = COMPRESS_NOVEL_CHUNK_HUMAN.format(novel_chunk=chunk)
            try:
                compressed = await complete(
                    prompt=user,
                    system=COMPRESS_NOVEL_CHUNK_SYSTEM,
                    provider=self.provider,
                    temperature=0.3,
                    max_tokens=4000,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[long_form.compressor] chunk {index} fallo ({e}); usando original"
                )
                compressed = chunk
            return CompressionResult(
                index=index,
                original_chars=len(chunk),
                compressed_text=compressed,
            )

    async def compress_all(self, chunks: list[str]) -> list[CompressionResult]:
        """Comprime N chunks en paralelo con semáforo."""
        if not chunks:
            return []
        sem = asyncio.Semaphore(self.max_concurrent)
        tasks = [self._compress_one(sem, i, c) for i, c in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        avg_ratio = sum(r.ratio for r in results) / max(len(results), 1)
        logger.info(
            f"[long_form.compressor] compressed {len(chunks)} chunks; "
            f"avg ratio: {avg_ratio:.2%}"
        )
        # Ordenar por index (gather puede reordenar)
        return sorted(results, key=lambda r: r.index)

    def aggregate_compressed(self, results: list[CompressionResult]) -> str:
        """Concatena chunks comprimidos en un solo texto.

        Para uso simple: concat con doble newline. Si hay overlap real
        entre chunks consecutivos, el LLM downstream lo absorbe sin problema.
        Si necesitaras dedup serio, usa `aggregate_with_llm()` (más caro).
        """
        return "\n\n".join(r.compressed_text for r in results)
