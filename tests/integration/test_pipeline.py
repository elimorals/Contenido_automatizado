"""Integration tests para `apps.api.pipeline` con TODOS los mocks.

Estos tests NO golpean LLMs, ffmpeg, ni redes — solo verifican el wiring
de las funciones públicas (dispatch + adapt helper). El happy-path real
con APIs reales vive en suites e2e fuera de este archivo.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from apps.api.pipeline import (
    _adapt_conversational_to_draft,
    run_pipeline,
)
from orchestration.state.memory import MemoryStateManager
from shared.schemas import (
    AudioArtifact,
    Beat,
    BeatArtifact,
    BeatRole,
    BeatVisual,
    Card,
    ConversationalScript,
    Essence,
    GenerationMode,
    HookVariant,
    MotionHint,
    OpenStyle,
    ScriptDraft,
    SubtitleStyle,
    TaskState,
    VideoAspect,
    VideoParams,
    VideoSource,
    WordTiming,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def state_manager() -> MemoryStateManager:
    return MemoryStateManager()


@pytest.fixture
def fake_essence() -> Essence:
    return Essence(
        core_claim="Most placebos are stronger than aspirin in trials.",
        mechanism="Belief activates endogenous opioids.",
        evidence=["Beecher 1955 placebo study", "Wager 2004 fMRI", "30% effect size"],
        content_mode="general",
        domain="health",
    )


@pytest.fixture
def fake_script_draft() -> ScriptDraft:
    return ScriptDraft(
        hook="Placebos beat aspirin in real trials.",
        hook_variant=HookVariant.SHOCK_STAT,
        mechanism_lines=[
            "Beecher proved this in 1955 with combat wounded.",
            "Beliefs activate the same opioid pathways as morphine.",
        ],
        payoff_line="Aspirin loses to a sugar pill — that's the real placebo story.",
        target_wpm=180,
        narration=(
            "[curious] Placebos beat aspirin in real trials. "
            "Beecher proved this in 1955 with combat wounded. "
            "Beliefs activate the same opioid pathways as morphine. "
            "Aspirin loses to a sugar pill — that's the real placebo story."
        ),
    )


@pytest.fixture
def fake_audio_artifact(tmp_path: Path) -> AudioArtifact:
    fake_wav = tmp_path / "full.wav"
    fake_wav.write_bytes(b"RIFF0000WAVEfmt ")
    return AudioArtifact(
        path=fake_wav,
        duration_s=22.0,
        word_timings=[
            WordTiming(word="Placebos", start_s=0.0, end_s=0.5),
            WordTiming(word="beat", start_s=0.5, end_s=0.8),
            WordTiming(word="aspirin.", start_s=0.8, end_s=1.4),
        ],
        engine="silent",
    )


@pytest.fixture
def fake_visuals() -> list[BeatVisual]:
    return [
        BeatVisual(
            image_prompt="9:16 macro of a glass jar of white sugar pills",
            motion_hint=MotionHint.SLOW_ZOOM_IN,
            visual_anchor="Beecher 1955 placebo study",
        )
        for _ in range(3)
    ]


@pytest.fixture
def fake_beats() -> list[Beat]:
    return [
        Beat(idx=0, role=BeatRole.HOOK, text="Hook text", target_duration_s=4.0, veo_duration=6),
        Beat(idx=1, role=BeatRole.MECHANISM, text="Mech text", target_duration_s=8.0, veo_duration=8),
        Beat(idx=2, role=BeatRole.PAYOFF, text="Payoff text", target_duration_s=3.5, veo_duration=4),
    ]


@pytest.fixture
def fake_artifacts(tmp_path: Path) -> list[BeatArtifact]:
    arts = []
    for i in range(3):
        clip = tmp_path / f"beat_{i}.mp4"
        clip.write_bytes(b"\x00" * 1024)
        arts.append(
            BeatArtifact(
                idx=i,
                video_path=clip,
                source=VideoSource.PEXELS,
                duration_s=6.0,
            )
        )
    return arts


# =============================================================================
# Adapter helper test
# =============================================================================


class TestAdaptConversationalToDraft:
    @pytest.mark.integration
    def test_produces_valid_scriptdraft(self) -> None:
        conv = ConversationalScript(
            tease="Why do placebos work better than aspirin?",
            common_belief="Most people think sugar pills do nothing.",
            reveal=(
                "Henry Beecher proved this in 1955. He showed combat soldiers "
                "treated with saline reported less pain than morphine controls. "
                "Beliefs activate endogenous opioids."
            ),
            payoff="So aspirin loses to belief — placebos are real medicine.",
            open_style=OpenStyle.QUESTION,
            target_wpm=180,
            narration=(
                "[curious] Why do placebos work better than aspirin? "
                "Most people think sugar pills do nothing. "
                "Henry Beecher proved this in 1955. "
                "He showed combat soldiers treated with saline reported less pain "
                "than morphine controls. Beliefs activate endogenous opioids. "
                "So aspirin loses to belief — placebos are real medicine."
            ),
        )
        draft = _adapt_conversational_to_draft(conv)

        assert isinstance(draft, ScriptDraft)
        assert draft.hook == conv.tease
        assert 2 <= len(draft.mechanism_lines) <= 4
        assert draft.target_wpm == conv.target_wpm
        # Loop-back validator pasó (de lo contrario, model_validator habría rezongado).
        assert draft.payoff_line.strip()
        assert draft.narration.strip()

    @pytest.mark.integration
    def test_repair_loop_back_when_missing(self) -> None:
        # Tease con palabra distintiva "placebos" pero payoff sin ella.
        conv = ConversationalScript(
            tease="Why do placebos defy medical logic?",
            common_belief=None,
            reveal=(
                "Beecher published the canonical 1955 paper. The effect size is "
                "real and reproducible. Subsequent meta-analyses confirm."
            ),
            payoff="The body heals itself.",  # ← NO contiene 'placebos'
            open_style=OpenStyle.QUESTION,
            target_wpm=180,
            narration=(
                "Why do placebos defy medical logic? "
                "Beecher published the canonical 1955 paper. "
                "The effect size is real and reproducible. "
                "Subsequent meta-analyses confirm. "
                "The body heals itself."
            ),
        )
        draft = _adapt_conversational_to_draft(conv)
        # El helper debe haber parchado el payoff_line para contener la keyword.
        assert "placebos" in draft.payoff_line.lower() or "logic" in draft.payoff_line.lower()


# =============================================================================
# Dispatch tests — el corazón
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_pipeline_with_topic_dispatches_topic(
    state_manager: MemoryStateManager,
) -> None:
    params = VideoParams(
        topic="placebos",
        mode=GenerationMode.PREMIUM,
        aspect=VideoAspect.PORTRAIT,
    )

    with patch(
        "apps.api.pipeline.run_topic_pipeline",
        new_callable=AsyncMock,
    ) as mock_topic, patch(
        "apps.api.pipeline.run_article_pipeline", new_callable=AsyncMock
    ) as mock_article, patch(
        "apps.api.pipeline.run_subject_pipeline", new_callable=AsyncMock
    ) as mock_subject:
        async def _return_info(p: VideoParams, tid: str, sm: Any) -> Any:
            current = await sm.get(tid)
            assert current is not None
            return current

        mock_topic.side_effect = _return_info

        info = await run_pipeline(params, "topic-task-id", state_manager)

        mock_topic.assert_awaited_once()
        mock_article.assert_not_awaited()
        mock_subject.assert_not_awaited()
        assert info.state == TaskState.COMPLETE
        assert info.task_id == "topic-task-id"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_pipeline_with_url_dispatches_article(
    state_manager: MemoryStateManager,
) -> None:
    params = VideoParams(
        url="https://example.com/article",
        mode=GenerationMode.PREMIUM,
    )

    with patch(
        "apps.api.pipeline.run_article_pipeline", new_callable=AsyncMock
    ) as mock_article, patch(
        "apps.api.pipeline.run_topic_pipeline", new_callable=AsyncMock
    ) as mock_topic, patch(
        "apps.api.pipeline.run_subject_pipeline", new_callable=AsyncMock
    ) as mock_subject:

        async def _return_info(p: VideoParams, tid: str, sm: Any) -> Any:
            return await sm.get(tid)

        mock_article.side_effect = _return_info

        info = await run_pipeline(params, "article-task-id", state_manager)

        mock_article.assert_awaited_once()
        mock_topic.assert_not_awaited()
        mock_subject.assert_not_awaited()
        assert info.state == TaskState.COMPLETE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_pipeline_with_subject_dispatches_subject(
    state_manager: MemoryStateManager,
) -> None:
    params = VideoParams(
        subject="spring flowers in Tokyo",
        mode=GenerationMode.EXPRESS,
        voice_name="en-US-AvaNeural-Female",
    )

    with patch(
        "apps.api.pipeline.run_subject_pipeline", new_callable=AsyncMock
    ) as mock_subject, patch(
        "apps.api.pipeline.run_article_pipeline", new_callable=AsyncMock
    ) as mock_article, patch(
        "apps.api.pipeline.run_topic_pipeline", new_callable=AsyncMock
    ) as mock_topic:

        async def _return_info(p: VideoParams, tid: str, sm: Any) -> Any:
            return await sm.get(tid)

        mock_subject.side_effect = _return_info

        info = await run_pipeline(params, "subject-task-id", state_manager)

        mock_subject.assert_awaited_once()
        mock_article.assert_not_awaited()
        mock_topic.assert_not_awaited()
        assert info.state == TaskState.COMPLETE


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_pipeline_catches_exception_marks_failed(
    state_manager: MemoryStateManager,
) -> None:
    params = VideoParams(topic="anything", mode=GenerationMode.PREMIUM)

    with patch(
        "apps.api.pipeline.run_topic_pipeline",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await run_pipeline(params, "fail-task-id", state_manager)

    # El state debe haber registrado FAILED + error en timings.
    stored = await state_manager.get("fail-task-id")
    assert stored is not None
    assert stored.state == TaskState.FAILED
    assert "error" in stored.timings_s or any(
        "boom" in str(v) for v in stored.timings_s.values()
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_pipeline_initializes_taskinfo_in_state(
    state_manager: MemoryStateManager,
) -> None:
    """Antes de despachar, run_pipeline DEBE crear el TaskInfo en state.

    Esto es invariante del contrato — el worker / API confía en que el TaskInfo
    está presente desde el instante en que se llama, no recién después de
    fases largas.
    """
    params = VideoParams(topic="placebos", mode=GenerationMode.PREMIUM)

    seen_in_callee: dict[str, Any] = {}

    async def _capture_state(p: VideoParams, tid: str, sm: Any) -> Any:
        info = await sm.get(tid)
        seen_in_callee["info"] = info
        return info

    with patch(
        "apps.api.pipeline.run_topic_pipeline",
        new_callable=AsyncMock,
        side_effect=_capture_state,
    ):
        await run_pipeline(params, "init-test", state_manager)

    callee_info = seen_in_callee["info"]
    assert callee_info is not None, "TaskInfo no estaba en state al despachar"
    assert callee_info.task_id == "init-test"
    assert callee_info.state == TaskState.PROCESSING  # antes de completarse
    assert callee_info.params is not None
    assert callee_info.params.topic == "placebos"
