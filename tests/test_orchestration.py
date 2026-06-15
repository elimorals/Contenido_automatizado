"""Tests para la capa de orchestration (queue + state + AgentField)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from orchestration import (
    AgentFieldAdapter,
    QueueFullError,
    get_state_manager,
    get_task_manager,
)
from orchestration.queue.memory import InMemoryTaskManager
from orchestration.queue.redis_queue import RedisTaskManager
from orchestration.state.memory import MemoryStateManager
from orchestration.state.redis_state import RedisStateManager
from shared.config import AppConfig, Config
from shared.schemas import (
    GenerationMode,
    TaskInfo,
    TaskState,
    VideoAspect,
    VideoParams,
)


# =============================================================================
# Helpers
# =============================================================================


def _mk_params(topic: str = "test topic") -> VideoParams:
    return VideoParams(topic=topic, mode=GenerationMode.PREMIUM, aspect=VideoAspect.PORTRAIT)


def _mk_taskinfo(task_id: str = "t1", state: TaskState = TaskState.QUEUED) -> TaskInfo:
    return TaskInfo(
        task_id=task_id,
        state=state,
        mode=GenerationMode.PREMIUM,
        progress=0,
        params=_mk_params(),
    )


# =============================================================================
# QUEUE — Memory
# =============================================================================


@pytest.mark.asyncio
async def test_memory_enqueue_dequeue_roundtrip():
    q = InMemoryTaskManager(max_concurrent_tasks=3, max_queued_tasks=10)
    params = _mk_params("placebo effect")
    await q.enqueue("task-1", params)
    assert await q.queue_size() == 1

    res = await q.dequeue(timeout=1.0)
    assert res is not None
    task_id, recovered = res
    assert task_id == "task-1"
    assert recovered.topic == "placebo effect"
    assert recovered.mode == GenerationMode.PREMIUM
    assert await q.queue_size() == 0


@pytest.mark.asyncio
async def test_memory_queue_full_raises():
    q = InMemoryTaskManager(max_concurrent_tasks=3, max_queued_tasks=2)
    await q.enqueue("a", _mk_params())
    await q.enqueue("b", _mk_params())
    with pytest.raises(QueueFullError):
        await q.enqueue("c", _mk_params())


@pytest.mark.asyncio
async def test_memory_dequeue_timeout_returns_none():
    q = InMemoryTaskManager(max_concurrent_tasks=3, max_queued_tasks=10)
    # Cola vacía → debe devolver None tras timeout corto.
    res = await q.dequeue(timeout=0.05)
    assert res is None


@pytest.mark.asyncio
async def test_memory_active_count_tracking():
    q = InMemoryTaskManager(max_concurrent_tasks=3, max_queued_tasks=10)
    assert await q.active_count() == 0
    await q.increment_active()
    await q.increment_active()
    assert await q.active_count() == 2
    await q.decrement_active()
    assert await q.active_count() == 1


# =============================================================================
# STATE — Memory
# =============================================================================


@pytest.mark.asyncio
async def test_memory_state_crud():
    state = MemoryStateManager()
    info = _mk_taskinfo("t1", TaskState.QUEUED)

    # CREATE
    await state.set("t1", info)
    fetched = await state.get("t1")
    assert fetched is not None
    assert fetched.task_id == "t1"
    assert fetched.state == TaskState.QUEUED

    # UPDATE
    updated = await state.update("t1", state=TaskState.PROCESSING, progress=42)
    assert updated.state == TaskState.PROCESSING
    assert updated.progress == 42

    # Re-get should reflect update
    again = await state.get("t1")
    assert again is not None and again.progress == 42

    # DELETE
    ok = await state.delete("t1")
    assert ok is True
    assert await state.get("t1") is None
    # Idempotent
    assert await state.delete("t1") is False


@pytest.mark.asyncio
async def test_memory_state_list_paginated_and_count():
    state = MemoryStateManager()
    for i in range(5):
        await state.set(f"t{i}", _mk_taskinfo(f"t{i}", TaskState.QUEUED))
    await state.update("t0", state=TaskState.PROCESSING)
    await state.update("t1", state=TaskState.PROCESSING)
    await state.update("t2", state=TaskState.COMPLETE)

    tasks, total = await state.list(page=1, page_size=3)
    assert total == 5
    assert len(tasks) == 3

    tasks_p2, total2 = await state.list(page=2, page_size=3)
    assert total2 == 5
    assert len(tasks_p2) == 2

    assert await state.count_by_state(TaskState.PROCESSING.value) == 2
    assert await state.count_by_state(TaskState.COMPLETE.value) == 1
    assert await state.count_by_state(TaskState.QUEUED.value) == 2


@pytest.mark.asyncio
async def test_memory_state_update_clamps_progress():
    state = MemoryStateManager()
    await state.set("t1", _mk_taskinfo("t1"))
    res = await state.update("t1", progress=200)
    assert res.progress == 100
    res2 = await state.update("t1", progress=-50)
    assert res2.progress == 0


@pytest.mark.asyncio
async def test_memory_state_update_missing_raises():
    state = MemoryStateManager()
    with pytest.raises(KeyError):
        await state.update("nope", progress=10)


# =============================================================================
# FACTORIES
# =============================================================================


def _cfg(enable_redis: bool) -> Config:
    return Config(app=AppConfig(enable_redis=enable_redis, max_queued_tasks=5))


def test_factories_return_memory_when_redis_disabled():
    cfg = _cfg(enable_redis=False)
    q = get_task_manager(cfg)
    s = get_state_manager(cfg)
    assert isinstance(q, InMemoryTaskManager)
    assert isinstance(s, MemoryStateManager)
    assert q.max_queued_tasks == 5


def test_factories_return_redis_when_redis_enabled():
    cfg = _cfg(enable_redis=True)
    q = get_task_manager(cfg)
    s = get_state_manager(cfg)
    assert isinstance(q, RedisTaskManager)
    assert isinstance(s, RedisStateManager)


# =============================================================================
# REDIS — via AsyncMock (no fakeredis disponible en venv)
# =============================================================================


@pytest.mark.asyncio
async def test_redis_queue_enqueue_calls_lpush(monkeypatch):
    """Test que RedisTaskManager.enqueue llama LPUSH con payload JSON correcto."""
    mock_redis = AsyncMock()
    mock_redis.llen = AsyncMock(return_value=0)
    mock_redis.lpush = AsyncMock(return_value=1)

    q = RedisTaskManager(max_queued_tasks=10)
    q._redis = mock_redis  # type: ignore[assignment]

    params = _mk_params("redis topic")
    await q.enqueue("task-redis-1", params)

    mock_redis.llen.assert_awaited_once()
    mock_redis.lpush.assert_awaited_once()
    args, _ = mock_redis.lpush.call_args
    key, payload = args
    assert key == q.queue_key
    import json
    parsed = json.loads(payload)
    assert parsed["task_id"] == "task-redis-1"
    assert parsed["params"]["topic"] == "redis topic"


@pytest.mark.asyncio
async def test_redis_queue_full_raises():
    mock_redis = AsyncMock()
    mock_redis.llen = AsyncMock(return_value=10)
    q = RedisTaskManager(max_queued_tasks=10)
    q._redis = mock_redis  # type: ignore[assignment]

    with pytest.raises(QueueFullError):
        await q.enqueue("task-x", _mk_params())


@pytest.mark.asyncio
async def test_redis_queue_dequeue_decodes_payload():
    import json
    payload = json.dumps({
        "task_id": "tr1",
        "params": _mk_params("dequeued").model_dump(mode="json"),
    })
    mock_redis = AsyncMock()
    mock_redis.brpop = AsyncMock(return_value=("contenido:task_queue", payload))

    q = RedisTaskManager()
    q._redis = mock_redis  # type: ignore[assignment]

    res = await q.dequeue(timeout=1.0)
    assert res is not None
    task_id, params = res
    assert task_id == "tr1"
    assert params.topic == "dequeued"


@pytest.mark.asyncio
async def test_redis_queue_dequeue_timeout_returns_none():
    mock_redis = AsyncMock()
    mock_redis.brpop = AsyncMock(return_value=None)
    q = RedisTaskManager()
    q._redis = mock_redis  # type: ignore[assignment]

    assert await q.dequeue(timeout=0.5) is None


@pytest.mark.asyncio
async def test_redis_state_get_returns_none_when_missing():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    s = RedisStateManager()
    s._redis = mock_redis  # type: ignore[assignment]
    assert await s.get("nope") is None


# =============================================================================
# AGENTFIELD ADAPTER
# =============================================================================


@pytest.mark.asyncio
async def test_agentfield_local_dispatch_fallback():
    """Sin broker disponible, dispatch_dag ejecuta reasoners locales en paralelo."""
    adapter = AgentFieldAdapter()
    # Forzar modo local sin levantar broker.
    adapter._connected = True
    adapter._mode = "local"

    call_log: list[str] = []

    async def reasoner_a(x: int) -> dict:
        await asyncio.sleep(0.01)
        call_log.append(f"a:{x}")
        return {"a": x * 2}

    async def reasoner_b(y: int) -> dict:
        await asyncio.sleep(0.01)
        call_log.append(f"b:{y}")
        return {"b": y + 1}

    result = await adapter.dispatch_dag(
        reasoners=[
            ("phase_a", reasoner_a, {"x": 5}),
            ("phase_b", reasoner_b, {"y": 10}),
        ],
        timeout_s=5.0,
    )

    assert result.success is True
    assert result.outputs["phase_a"] == {"a": 10}
    assert result.outputs["phase_b"] == {"b": 11}
    assert set(result.timings_s.keys()) == {"phase_a", "phase_b"}
    assert all(v >= 0 for v in result.timings_s.values())
    assert set(call_log) == {"a:5", "b:10"}


@pytest.mark.asyncio
async def test_agentfield_dispatch_captures_per_reasoner_errors():
    """Si un reasoner falla, los demás siguen y el error se reporta en `errors`."""
    adapter = AgentFieldAdapter()
    adapter._connected = True
    adapter._mode = "local"

    async def good() -> dict:
        return {"ok": True}

    async def bad() -> dict:
        raise RuntimeError("boom")

    result = await adapter.dispatch_dag(
        reasoners=[
            ("good_phase", good, {}),
            ("bad_phase", bad, {}),
        ],
        timeout_s=5.0,
    )

    assert result.success is False
    assert result.outputs["good_phase"] == {"ok": True}
    assert "bad_phase" not in result.outputs
    assert "bad_phase" in result.errors
    assert isinstance(result.errors["bad_phase"], RuntimeError)
    # Timings registrados aún para el fallido.
    assert "bad_phase" in result.timings_s


@pytest.mark.asyncio
async def test_agentfield_dispatch_timeout():
    """Timeout en un reasoner produce error pero no rompe el dispatch."""
    adapter = AgentFieldAdapter()
    adapter._connected = True
    adapter._mode = "local"

    async def slow() -> dict:
        await asyncio.sleep(2.0)
        return {}

    result = await adapter.dispatch_dag(
        reasoners=[("slow_phase", slow, {})],
        timeout_s=0.1,
    )
    assert result.success is False
    assert "slow_phase" in result.errors
    assert isinstance(result.errors["slow_phase"], asyncio.TimeoutError)
