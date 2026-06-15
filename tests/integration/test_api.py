"""Integration tests para `apps.api.main` (FastAPI).

Usa `httpx.ASGITransport` para hablar con el app sin abrir socket.
Mocks pesados están aplicados sobre los reasoners (extract/hunters/etc) — los
tests verifican el wiring HTTP, no la calidad de los reasoners.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.api.main import app
from orchestration.queue import InMemoryTaskManager
from orchestration.state import MemoryStateManager
from shared.schemas import (
    AudioArtifact,
    ConversationalScript,
    CriticOutput,
    CriticRanking,
    Essence,
    EssenceCandidate,
    HookVariant,
    OpenStyle,
    PairwiseVerdict,
    ScriptDraft,
    TaskInfo,
    TaskState,
    VideoParams,
    WordTiming,
)


# =============================================================================
# Helpers / fixtures
# =============================================================================


@pytest.fixture
def fake_state() -> MemoryStateManager:
    return MemoryStateManager()


@pytest.fixture
def fake_queue() -> InMemoryTaskManager:
    return InMemoryTaskManager(max_concurrent_tasks=2, max_queued_tasks=4)


@pytest.fixture
def configured_app(
    fake_state: MemoryStateManager, fake_queue: InMemoryTaskManager
) -> Any:
    """App con queue+state inyectados; saltea el lifespan real."""
    app.state.task_manager = fake_queue
    app.state.state_manager = fake_state
    # Config mínimo (max_queued_tasks coincide con fake_queue para que /videos
    # respete el bound).
    cfg = MagicMock()
    cfg.app.max_queued_tasks = fake_queue.max_queued_tasks
    cfg.llm_default_provider_express = "openrouter"
    app.state.config = cfg
    app.state.distribution = None
    return app


def _make_client(application: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=application)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# =============================================================================
# /videos — encolado
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_videos_enqueues_and_returns_task_id(
    configured_app: Any, fake_state: MemoryStateManager, fake_queue: InMemoryTaskManager
) -> None:
    async with _make_client(configured_app) as client:
        resp = await client.post(
            "/videos",
            json={"topic": "the placebo effect", "mode": "premium"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    task_id = body["data"]["task_id"]
    assert task_id
    assert body["data"]["state"] == TaskState.QUEUED.value
    # State debe tener el TaskInfo en QUEUED.
    info = await fake_state.get(task_id)
    assert info is not None
    assert info.state == TaskState.QUEUED
    assert info.params is not None
    assert info.params.topic == "the placebo effect"
    # Queue debe tener el item.
    assert await fake_queue.queue_size() == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_videos_returns_429_when_queue_full(
    configured_app: Any, fake_queue: InMemoryTaskManager
) -> None:
    # Llenar la cola al límite.
    params = VideoParams(topic="x", mode="premium")
    for i in range(fake_queue.max_queued_tasks):
        await fake_queue.enqueue(f"pre-{i}", params)

    async with _make_client(configured_app) as client:
        resp = await client.post(
            "/videos",
            json={"topic": "another topic", "mode": "premium"},
        )

    assert resp.status_code == 429
    assert "full" in resp.json()["detail"].lower()


# =============================================================================
# /tasks/{id}
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_task_404_when_missing(configured_app: Any) -> None:
    async with _make_client(configured_app) as client:
        resp = await client.get("/tasks/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_task_returns_taskinfo(
    configured_app: Any, fake_state: MemoryStateManager
) -> None:
    info = TaskInfo(
        task_id="abc-123",
        state=TaskState.PROCESSING,
        mode="premium",
        progress=42,
        params=VideoParams(topic="placebos", mode="premium"),
    )
    await fake_state.set("abc-123", info)

    async with _make_client(configured_app) as client:
        resp = await client.get("/tasks/abc-123")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["task_id"] == "abc-123"
    assert body["data"]["progress"] == 42
    assert body["data"]["state"] == TaskState.PROCESSING.value


# =============================================================================
# /tasks (listado paginado)
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_tasks_pagination(
    configured_app: Any, fake_state: MemoryStateManager
) -> None:
    for i in range(5):
        await fake_state.set(
            f"t-{i}",
            TaskInfo(
                task_id=f"t-{i}",
                state=TaskState.QUEUED,
                mode="premium",
                params=VideoParams(topic=f"topic-{i}", mode="premium"),
            ),
        )
    async with _make_client(configured_app) as client:
        resp = await client.get("/tasks?page=1&page_size=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["total"] == 5
    assert len(body["data"]["tasks"]) == 2
    assert body["data"]["page"] == 1
    assert body["data"]["page_size"] == 2


# =============================================================================
# DELETE /tasks/{id}
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_task(
    configured_app: Any, fake_state: MemoryStateManager
) -> None:
    info = TaskInfo(
        task_id="del-me",
        state=TaskState.COMPLETE,
        mode="premium",
        params=VideoParams(topic="x", mode="premium"),
    )
    await fake_state.set("del-me", info)
    async with _make_client(configured_app) as client:
        resp = await client.delete("/tasks/del-me")
    assert resp.status_code == 200
    assert await fake_state.get("del-me") is None
    # 404 después de borrado
    async with _make_client(configured_app) as client:
        resp = await client.delete("/tasks/del-me")
    assert resp.status_code == 404


# =============================================================================
# /scripts — topic path (llama hunters/critic/narrators/judge)
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_scripts_topic_invokes_hunters_critic_judge(
    configured_app: Any,
) -> None:
    fake_candidates = [
        EssenceCandidate(
            core_claim=f"Claim {i}",
            mechanism="Some mechanism",
            evidence=["evidence-1"],
            content_mode="general",
            domain="health",
            angle="specific_figure",
            novelty_pitch=f"pitch {i}",
        )
        for i in range(3)
    ]
    critic_out = CriticOutput(
        top_3_indices=[0, 1, 2],
        rankings=[
            CriticRanking(
                candidate_idx=i,
                novelty=8,
                specificity=8,
                hookability=8,
                narratability=8,
                composite=8.0,
                why="ok",
            )
            for i in range(3)
        ],
    )
    fake_conv = ConversationalScript(
        tease="Why do placebos work better than aspirin?",
        common_belief=None,
        reveal=(
            "Henry Beecher published the canonical 1955 study. "
            "Belief activates opioid pathways. The effect size is reproducible."
        ),
        payoff="Aspirin loses to placebos.",
        open_style=OpenStyle.QUESTION,
        target_wpm=180,
        narration=(
            "Why do placebos work better than aspirin? "
            "Beecher 1955. Belief activates opioids. Aspirin loses to placebos."
        ),
    )
    verdict = PairwiseVerdict(winner_idx=0, composite_score=8.5, why="best hook")

    with patch(
        "apps.api.main.hunt_specific_figure",
        new_callable=AsyncMock,
        return_value=fake_candidates,
    ) as h1, patch(
        "apps.api.main.hunt_reversal",
        new_callable=AsyncMock,
        return_value=fake_candidates,
    ) as h2, patch(
        "apps.api.main.hunt_temporal",
        new_callable=AsyncMock,
        return_value=fake_candidates,
    ) as h3, patch(
        "apps.api.main.hunt_cross_domain",
        new_callable=AsyncMock,
        return_value=fake_candidates,
    ) as h4, patch(
        "apps.api.main.pick_top_essences",
        new_callable=AsyncMock,
        return_value=critic_out,
    ) as critic, patch(
        "apps.api.main.write_narrations",
        new_callable=AsyncMock,
        return_value=[fake_conv, fake_conv, fake_conv],
    ) as narrators, patch(
        "apps.api.main.pick_best_narration",
        new_callable=AsyncMock,
        return_value=verdict,
    ) as judge:
        async with _make_client(configured_app) as client:
            resp = await client.post(
                "/scripts",
                json={"topic": "placebos", "mode": "premium"},
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["entry"] == "topic"
    assert body["data"]["winner_idx"] == 0
    assert body["data"]["composite_score"] == 8.5
    h1.assert_awaited_once()
    h2.assert_awaited_once()
    h3.assert_awaited_once()
    h4.assert_awaited_once()
    critic.assert_awaited_once()
    narrators.assert_awaited_once()
    judge.assert_awaited_once()


# =============================================================================
# /scripts — url path (llama extract + compose)
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_scripts_url_invokes_extract_and_compose(
    configured_app: Any,
) -> None:
    fake_essence = Essence(
        core_claim="Placebos beat aspirin in trials.",
        mechanism="Belief activates endogenous opioids.",
        evidence=["Beecher 1955", "Wager 2004", "30% effect size"],
        content_mode="general",
        domain="health",
    )
    fake_draft = ScriptDraft(
        hook="Placebos beat aspirin in real trials.",
        hook_variant=HookVariant.SHOCK_STAT,
        mechanism_lines=[
            "Beecher proved this in 1955.",
            "Beliefs activate opioid pathways.",
        ],
        payoff_line="Aspirin loses to placebos — that's the placebo story.",
        target_wpm=180,
        narration=(
            "Placebos beat aspirin in real trials. "
            "Beecher proved this in 1955. "
            "Beliefs activate opioid pathways. "
            "Aspirin loses to placebos — that's the placebo story."
        ),
    )

    with patch(
        "apps.api.main.extract_essence",
        new_callable=AsyncMock,
        return_value=fake_essence,
    ) as extract, patch(
        "apps.api.main.compose_script",
        new_callable=AsyncMock,
        return_value=fake_draft,
    ) as compose:
        async with _make_client(configured_app) as client:
            resp = await client.post(
                "/scripts",
                json={"url": "https://example.com/article", "mode": "premium"},
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["entry"] == "article"
    assert body["data"]["essence"]["core_claim"].startswith("Placebos")
    extract.assert_awaited_once()
    compose.assert_awaited_once()


# =============================================================================
# /audio — devuelve word_timings
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_audio_returns_word_timings(
    configured_app: Any, tmp_path: Any
) -> None:
    fake_wav = tmp_path / "fake.wav"
    fake_wav.write_bytes(b"RIFF0000WAVEfmt ")
    fake_artifact = AudioArtifact(
        path=fake_wav,
        duration_s=12.5,
        word_timings=[
            WordTiming(word="Hello", start_s=0.0, end_s=0.4),
            WordTiming(word="world", start_s=0.4, end_s=0.9),
        ],
        engine="silent",
    )

    fake_engine = MagicMock()
    fake_engine.synthesize = AsyncMock(return_value=fake_artifact)

    with patch(
        "apps.api.main.detect_engine_and_voice", return_value=("silent", "default")
    ), patch("apps.api.main.get_engine", return_value=fake_engine):
        async with _make_client(configured_app) as client:
            resp = await client.post(
                "/audio",
                json={
                    "topic": "n/a",
                    "mode": "express",
                    "video_script": "Hello world",
                },
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["duration_s"] == 12.5
    assert body["data"]["engine"] == "silent"
    assert len(body["data"]["word_timings"]) == 2
    assert body["data"]["word_timings"][0]["word"] == "Hello"
    assert "audio_path" in body["data"]


# =============================================================================
# /audio — sin script → 400
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_audio_without_script_returns_400(configured_app: Any) -> None:
    async with _make_client(configured_app) as client:
        resp = await client.post(
            "/audio",
            json={"topic": "no-script", "mode": "express"},
        )
    assert resp.status_code == 400


# =============================================================================
# Health
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_ok_when_queue_and_state_responsive(
    configured_app: Any,
) -> None:
    async with _make_client(configured_app) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["queue"]["connected"] is True
    assert body["state"]["connected"] is True
