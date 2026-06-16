"""Long-form video module — inspirado en ViMax (HKUDS, MIT License).

Pipeline: novel/idea/article/script → chunks → RAG → 3-act script →
          scenes → shots → portraits → shot generation with reference chaining
          → stitch into 5-60min video.

Backends:
- LLM: core.llm_router (mismo que el resto del pipeline)
- Embeddings: sentence-transformers (local, ~80MB modelo)
- RAG store: numpy (default) o FAISS (opt-in via config + extra dep)
- Image gen: ComfyUI workflows existentes (con IPAdapter para reference chain)
- TTS: core.tts (sample-accurate timing)
- Video assembly: core.editor (ffmpeg single-pass)

Sin duplicación con el pipeline base: reusamos llm_router, visual generation,
tts, editor. Solo agregamos los agentes narrativos especializados.

Crédito: prompts y arquitectura conceptual de
https://github.com/HKUDS/ViMax (MIT License).
"""
from __future__ import annotations

from core.long_form.compressor import NovelCompressor
from core.long_form.consistency import BestImageSelector, ReferenceImageSelector
from core.long_form.director import Director, plan_long_form, produce_long_form
from core.long_form.rag import RAGStore, chunk_text
from core.long_form.script_planner import (
    LongFormIntent,
    ScriptPlanner,
    detect_intent,
)
from core.long_form.scenes import SceneExtractor, StoryboardArtist
from core.long_form.types import (
    EmbeddingProvider,
    LongFormError,
    LongFormPlanError,
)

__all__ = [
    "BestImageSelector",
    "Director",
    "EmbeddingProvider",
    "LongFormError",
    "LongFormIntent",
    "LongFormPlanError",
    "NovelCompressor",
    "RAGStore",
    "ReferenceImageSelector",
    "SceneExtractor",
    "ScriptPlanner",
    "StoryboardArtist",
    "chunk_text",
    "detect_intent",
    "plan_long_form",
    "produce_long_form",
]
