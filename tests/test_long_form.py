"""Tests para core/long_form/ — schemas, chunker, RAG, mocked LLM agents.

Cubre:
1. Schemas (NarrativeArc, Scene, Shot, LongFormScript, LongFormJob, ConsistencyAnchor)
2. Chunker (RecursiveCharacterTextSplitter wiring)
3. RAGStore numpy backend (add + search + persist)
4. NovelCompressor splitting + result dataclass
5. ScriptPlanner intent routing (mocked LLM)
6. SceneExtractor + StoryboardArtist (mocked LLM)
7. Reference + Best image selector edge cases (no candidates, fallback)
8. Director.load_job round-trip
9. VideoParams accepts long_form_input

NO ejecuta LLM calls reales (todo mocked). No descarga el modelo de
sentence-transformers (que es ~80MB) salvo el test marked slow.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from core.long_form.compressor import CompressionResult, NovelCompressor
from core.long_form.consistency import BestImageSelector, ReferenceImageSelector
from core.long_form.rag import RAGStore, chunk_text
from core.long_form.scenes import SceneExtractor, StoryboardArtist
from core.long_form.script_planner import ScriptPlanner, detect_intent
from core.long_form.types import LongFormError, LongFormPlanError
from shared.schemas import (
    CharacterProfile,
    ConsistencyAnchor,
    LongFormIntent,
    LongFormJob,
    LongFormScript,
    NarrativeArc,
    Scene,
    Shot,
    VideoParams,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_arc() -> NarrativeArc:
    return NarrativeArc(
        title="The Memory Cost",
        logline="A time traveler loses memories with every change he makes",
        act1_setup="Lives a normal life as a librarian",
        act2_confrontation="Discovers time travel and starts changing small things, losing pieces of his past",
        act3_resolution="Decides to accept the present as it is",
        themes=["memory", "identity", "regret"],
        target_minutes=10.0,
    )


@pytest.fixture
def sample_character() -> CharacterProfile:
    return CharacterProfile(
        name="Aldo",
        appearance="Hombre 35 años, pelo castaño, lentes redondos, suéter beige",
        persona="Introspectivo, melancólico, lector empedernido",
    )


@pytest.fixture
def sample_shot() -> Shot:
    return Shot(
        idx=0,
        scene_idx=0,
        visual_description="Wide shot of a quiet library at dusk",
        shot_type="wide",
        camera_angle="eye_level",
        camera_movement="static",
        target_duration_s=4.0,
        characters_present=["Aldo"],
    )


@pytest.fixture
def sample_scene(sample_shot: Shot) -> Scene:
    return Scene(
        idx=0,
        title="Library at dusk",
        setting="Old library, autumn evening, golden light through window",
        summary="Aldo discovers an old book about time travel",
        characters_in_scene=["Aldo"],
        shots=[sample_shot],
    )


@pytest.fixture
def sample_script(sample_arc: NarrativeArc, sample_character: CharacterProfile, sample_scene: Scene) -> LongFormScript:
    return LongFormScript(
        arc=sample_arc,
        intent=LongFormIntent.NARRATIVE,
        characters=[sample_character],
        scenes=[sample_scene],
        source_kind="idea",
    )


# =============================================================================
# Schema tests
# =============================================================================


class TestSchemas:
    def test_narrative_arc_min_max(self) -> None:
        with pytest.raises(Exception):
            NarrativeArc(
                title="x",
                logline="l",
                act1_setup="a",
                act2_confrontation="b",
                act3_resolution="c",
                target_minutes=200,  # > 120 max
            )

    def test_script_total_shots(self, sample_script: LongFormScript) -> None:
        assert sample_script.total_shots == 1
        assert sample_script.estimated_duration_s == 4.0

    def test_script_serializes_roundtrip(self, sample_script: LongFormScript) -> None:
        json_str = sample_script.model_dump_json(indent=2)
        recovered = LongFormScript.model_validate_json(json_str)
        assert recovered.arc.title == sample_script.arc.title
        assert recovered.total_shots == sample_script.total_shots

    def test_long_form_job_default_status(self) -> None:
        job = LongFormJob(job_id="x", target_minutes=10.0)
        assert job.status == "pending"

    def test_consistency_anchor_creation(self) -> None:
        anchor = ConsistencyAnchor(
            shot_idx=3, scene_idx=1,
            frame_path="/tmp/x.jpg",
            description="Aldo (suéter beige) en biblioteca",
            character_names=["Aldo"],
            camera_id="cam2",
        )
        assert anchor.shot_idx == 3
        assert anchor.character_names == ["Aldo"]

    def test_video_params_accepts_long_form(self) -> None:
        params = VideoParams(
            long_form_input="A time traveler loses memories",
            long_form_target_minutes=12,
        )
        assert params.long_form_input is not None
        assert params.long_form_source_kind == "idea"
        assert params.long_form_target_minutes == 12.0

    def test_video_params_requires_entry(self) -> None:
        with pytest.raises(Exception, match="Se requiere uno de"):
            VideoParams()


# =============================================================================
# Chunker
# =============================================================================


class TestChunker:
    def test_chunk_small_text(self) -> None:
        text = "hola " * 100  # 500 chars
        chunks = chunk_text(text, chunk_size=200, chunk_overlap=20)
        assert len(chunks) >= 2
        assert all(len(c) <= 250 for c in chunks)  # algunos un poco mayores por la cascade

    def test_chunk_with_paragraphs_cascades(self) -> None:
        # Texto con párrafos — debería cortar por \n\n primero
        text = "\n\n".join([f"Párrafo {i}: " + "lorem ipsum " * 20 for i in range(5)])
        chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
        assert len(chunks) > 1
        # No corta en mitad de palabra
        for c in chunks:
            assert not c.endswith(" lor")

    def test_chunk_empty(self) -> None:
        assert chunk_text("") == []


# =============================================================================
# RAGStore con embedder mockeado (no descarga modelo)
# =============================================================================


class _MockEmbedder:
    """Embedder fake — devuelve vectores deterministas basados en hash del texto."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        results = []
        for t in texts:
            # Hash determinista a vector normalizado
            h = hash(t) & 0xFFFFFFFF
            np.random.seed(h)
            v = np.random.randn(self._dim).astype(np.float32)
            v = v / np.linalg.norm(v)
            results.append(v.tolist())
        return results

    @property
    def dimension(self) -> int:
        return self._dim


class TestRAGStore:
    def test_empty_search_returns_empty(self) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        assert store.search("anything") == []

    def test_add_and_search_returns_self(self) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        store.add(chunks=["query target"])
        results = store.search("query target", top_k=1)
        assert len(results) == 1
        chunk, score = results[0]
        assert chunk.text == "query target"
        assert score == pytest.approx(1.0, abs=1e-4)

    def test_add_metadata_persists(self) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        store.add(
            chunks=["a", "b"],
            metadatas=[{"chapter": 1}, {"chapter": 2}],
        )
        results = store.search("a", top_k=2)
        # El metadata debería estar en el chunk
        for chunk, _ in results:
            assert "chapter" in chunk.metadata

    def test_top_k_caps_correctly(self) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        store.add(chunks=[f"chunk {i}" for i in range(10)])
        results = store.search("query", top_k=3)
        assert len(results) == 3

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        store.add(
            chunks=["aaa", "bbb"],
            metadatas=[{"i": 0}, {"i": 1}],
        )
        store.save_to_dir(tmp_path)
        # Reconstruir con mismo embedder
        loaded = RAGStore.load_from_dir(tmp_path, embedder=_MockEmbedder())
        assert len(loaded) == 2

    def test_add_mismatched_lengths_raises(self) -> None:
        store = RAGStore(embedder=_MockEmbedder())
        with pytest.raises(LongFormPlanError, match="lengths mismatch"):
            store.add(chunks=["a", "b"], metadatas=[{}])

    def test_faiss_disabled_raises_on_request_without_dep(self) -> None:
        # Si faiss no está instalado y use_faiss=True, debe romper al add()
        # (no podemos asumir si lo tiene o no — el test es resistente)
        store = RAGStore(embedder=_MockEmbedder(), use_faiss=True)
        try:
            import faiss  # noqa: F401  # type: ignore[import-not-found]
            # Sí está instalado: el add no debe explotar
            store.add(chunks=["x"])
            assert len(store) == 1
        except ImportError:
            with pytest.raises(LongFormPlanError, match="faiss"):
                store.add(chunks=["x"])


# =============================================================================
# NovelCompressor
# =============================================================================


class TestNovelCompressor:
    def test_split_returns_chunks(self) -> None:
        c = NovelCompressor(chunk_size=200, chunk_overlap=20)
        text = "frase. " * 100
        chunks = c.split(text)
        assert len(chunks) > 1

    def test_compression_result_ratio(self) -> None:
        r = CompressionResult(index=0, original_chars=1000, compressed_text="hello")
        assert r.ratio == 0.005

    def test_compression_result_ratio_zero_original(self) -> None:
        r = CompressionResult(index=0, original_chars=0, compressed_text="abc")
        # Should not divide by zero
        assert r.ratio == 3.0  # 3 / max(0, 1) = 3

    @pytest.mark.asyncio
    async def test_compress_all_mocks_llm(self) -> None:
        c = NovelCompressor(chunk_size=200, chunk_overlap=20)
        with patch("core.long_form.compressor.complete", new=AsyncMock(return_value="SHORT")):
            results = await c.compress_all(["long " * 100, "another long " * 80])
        assert len(results) == 2
        assert all(r.compressed_text == "SHORT" for r in results)


# =============================================================================
# ScriptPlanner (mocked)
# =============================================================================


class TestScriptPlanner:
    @pytest.mark.asyncio
    async def test_detect_intent_narrative(self) -> None:
        from core.long_form.script_planner import _IntentRouterResponse
        mock_response = _IntentRouterResponse(intent=LongFormIntent.NARRATIVE, rationale="char-driven")
        with patch(
            "core.long_form.script_planner.complete_structured",
            new=AsyncMock(return_value=mock_response),
        ):
            result = await detect_intent("a man rediscovers love")
        assert result == LongFormIntent.NARRATIVE

    @pytest.mark.asyncio
    async def test_detect_intent_fallback_on_error(self) -> None:
        with patch(
            "core.long_form.script_planner.complete_structured",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await detect_intent("x")
        # Fallback es narrative
        assert result == LongFormIntent.NARRATIVE

    @pytest.mark.asyncio
    async def test_plan_uses_intent_template(self, sample_arc: NarrativeArc) -> None:
        planner = ScriptPlanner()
        with patch(
            "core.long_form.script_planner.complete_structured",
            new=AsyncMock(return_value=sample_arc),
        ):
            result = await planner.plan(
                basic_idea="x", target_minutes=10.0, intent=LongFormIntent.MOTION,
            )
        assert result.title == sample_arc.title


# =============================================================================
# SceneExtractor + StoryboardArtist
# =============================================================================


class TestSceneExtractor:
    @pytest.mark.asyncio
    async def test_extract_assigns_consecutive_idx(self, sample_arc, sample_character) -> None:
        from core.long_form.scenes import _ScenesResponse
        fake_scenes = [
            Scene(
                idx=99,  # idx incorrecto del LLM
                title="Library at dusk",
                setting="Old library, autumn evening",
                summary="Aldo discovers an old book about time",
            ),
            Scene(
                idx=100,
                title="Park bench afternoon",
                setting="City park, mid-afternoon",
                summary="Aldo reads the book on a bench",
            ),
        ]
        with patch(
            "core.long_form.scenes.complete_structured",
            new=AsyncMock(return_value=_ScenesResponse(scenes=fake_scenes)),
        ):
            extractor = SceneExtractor()
            result = await extractor.extract(sample_arc, [sample_character])
        # idx debe ser secuencial 0, 1 independientemente de lo que devolvió el LLM
        assert [s.idx for s in result] == [0, 1]


class TestStoryboardArtist:
    @pytest.mark.asyncio
    async def test_draw_scene_assigns_idx(self, sample_scene, sample_character) -> None:
        from core.long_form.scenes import _ShotsResponse
        fake_shots = [
            Shot(idx=99, scene_idx=99, visual_description="wide shot of library", target_duration_s=4.0),
            Shot(idx=100, scene_idx=99, visual_description="close up on book cover", target_duration_s=4.0),
        ]
        with patch(
            "core.long_form.scenes.complete_structured",
            new=AsyncMock(return_value=_ShotsResponse(shots=fake_shots)),
        ):
            artist = StoryboardArtist()
            result = await artist.draw_scene(sample_scene, [sample_character])
        assert [s.idx for s in result] == [0, 1]
        assert all(s.scene_idx == sample_scene.idx for s in result)


# =============================================================================
# Consistency selectors (mocked VLM)
# =============================================================================


class TestReferenceImageSelector:
    @pytest.mark.asyncio
    async def test_empty_anchors_returns_empty(self) -> None:
        selector = ReferenceImageSelector()
        result = await selector.select([], "target")
        assert result == ([], "target")


class TestBestImageSelector:
    @pytest.mark.asyncio
    async def test_zero_candidates_raises(self) -> None:
        selector = BestImageSelector()
        with pytest.raises(LongFormError, match="0 candidatos"):
            await selector.select_best([], [], "target")

    @pytest.mark.asyncio
    async def test_single_candidate_returns_self(self, tmp_path: Path) -> None:
        # Create a dummy image file
        from PIL import Image
        p = tmp_path / "candidate.jpg"
        Image.new("RGB", (10, 10), color=(0, 0, 0)).save(p)
        selector = BestImageSelector()
        path, reason = await selector.select_best([p], [], "target")
        assert path == p
        assert "single" in reason.lower()


# =============================================================================
# Director.load_job
# =============================================================================


class TestDirectorPersistence:
    def test_load_missing_job_raises(self, tmp_path: Path, monkeypatch) -> None:
        from core.long_form import Director
        from shared.config import load_config
        cfg = load_config()
        monkeypatch.setattr(cfg.long_form, "working_dir", str(tmp_path))
        with pytest.raises(LongFormPlanError, match="no encontrado"):
            Director.load_job("nonexistent_id")
