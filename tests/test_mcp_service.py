"""Tests para apps/mcp/service.py — lógica del MCP server (sin importar `mcp`).

El server FastMCP (apps/mcp/server.py) es una capa fina; toda la lógica testeable
vive aquí: construcción de params, gate de costo, ejecución de jobs en background
sobre el state manager existente, y formateo de estado. Ver ADR-021.

Cobertura:
1. build_reel_params: topic/url/subject → VideoParams; reference_url se propaga.
2. build_reel_params: 0 entradas → ValueError; >1 entrada → ValueError.
3. build_reel_params: long-form NO se expone (no hay parámetro para ello).
4. task_state_name: mapea el IntEnum a string legible.
5. cost_note: refleja el modo.
6. run_job: runner OK → COMPLETE; runner que lanza → FAILED (no se queda colgado).
7. start_reel: setea estado inicial síncrono + devuelve task_id + cost_note.
8. get_task / list_tasks: formatean TaskInfo (quality_flags, reference_brief).
"""
from __future__ import annotations

import asyncio

import pytest

from apps.mcp.service import (
    build_reel_params,
    cost_note,
    format_task,
    get_task,
    list_tasks,
    run_job,
    start_reel,
    task_state_name,
)
from orchestration.state import MemoryStateManager
from shared.schemas import GenerationMode, TaskState, VideoAspect, VideoParams, VisualStrategy

# =============================================================================
# build_reel_params
# =============================================================================


def test_build_params_topic():
    p = build_reel_params(topic="black holes")
    assert isinstance(p, VideoParams)
    assert p.topic == "black holes"
    assert p.mode == GenerationMode.PREMIUM  # default


def test_build_params_threads_reference_url():
    p = build_reel_params(topic="x", reference_url="https://tiktok.com/v")
    assert p.reference_url == "https://tiktok.com/v"


def test_build_params_maps_enums():
    p = build_reel_params(
        subject="y", mode="express", aspect="16:9", strategy="stock"
    )
    assert p.mode == GenerationMode.EXPRESS
    assert p.aspect == VideoAspect.LANDSCAPE
    assert p.visual_strategy == VisualStrategy.STOCK


def test_build_params_no_entry_raises():
    with pytest.raises(ValueError):
        build_reel_params()


def test_build_params_multiple_entries_raises():
    with pytest.raises(ValueError):
        build_reel_params(topic="a", url="https://x/y")


def test_build_params_invalid_mode_raises():
    with pytest.raises(ValueError):
        build_reel_params(topic="x", mode="long_form")


# =============================================================================
# task_state_name + cost_note
# =============================================================================


def test_task_state_name():
    assert task_state_name(TaskState.COMPLETE) == "complete"
    assert task_state_name(TaskState.FAILED) == "failed"
    assert task_state_name(TaskState.PROCESSING) == "processing"
    assert task_state_name(TaskState.QUEUED) == "queued"


def test_cost_note_reflects_mode():
    assert "$" in cost_note(GenerationMode.PREMIUM)
    assert "$" in cost_note(GenerationMode.EXPRESS)
    assert cost_note(GenerationMode.PREMIUM) != cost_note(GenerationMode.EXPRESS)


# =============================================================================
# format_task
# =============================================================================


@pytest.mark.asyncio
async def test_format_task_includes_quality_and_state():
    p = build_reel_params(topic="x")
    from shared.schemas import TaskInfo

    info = TaskInfo(
        task_id="t1",
        state=TaskState.COMPLETE,
        mode=p.mode,
        progress=100,
        videos=["/out/reel.mp4"],
        quality_flags={"slideshow_risk": 0.2},
    )
    out = format_task(info)
    assert out["state"] == "complete"
    assert out["quality_flags"]["slideshow_risk"] == 0.2
    assert out["videos"] == ["/out/reel.mp4"]
    assert out["reference_brief"] is None


# =============================================================================
# run_job (con runner inyectado — sin LLM real)
# =============================================================================


@pytest.mark.asyncio
async def test_run_job_success_sets_complete():
    state = MemoryStateManager()
    p = build_reel_params(topic="x")
    from shared.schemas import TaskInfo

    await state.set("j1", TaskInfo(task_id="j1", state=TaskState.QUEUED, mode=p.mode))

    async def fake_runner(params, task_id, sm):
        await sm.update(task_id, state=TaskState.COMPLETE, progress=100, videos=["/r.mp4"])

    await run_job(state, p, "j1", runner=fake_runner)
    out = await get_task(state, "j1")
    assert out["state"] == "complete"
    assert out["progress"] == 100


@pytest.mark.asyncio
async def test_run_job_failure_sets_failed_not_stuck():
    state = MemoryStateManager()
    p = build_reel_params(topic="x")
    from shared.schemas import TaskInfo

    await state.set("j2", TaskInfo(task_id="j2", state=TaskState.PROCESSING, mode=p.mode))

    async def broken_runner(params, task_id, sm):
        raise RuntimeError("boom")

    await run_job(state, p, "j2", runner=broken_runner)
    out = await get_task(state, "j2")
    assert out["state"] == "failed"


# =============================================================================
# start_reel + list_tasks
# =============================================================================


@pytest.mark.asyncio
async def test_start_reel_initial_state_and_costnote():
    state = MemoryStateManager()
    jobs: set = set()
    p = build_reel_params(topic="x", mode="express")

    async def fake_runner(params, task_id, sm):
        await sm.update(task_id, state=TaskState.COMPLETE, progress=100)

    out = await start_reel(state, jobs, p, runner=fake_runner)
    assert "task_id" in out
    assert "$" in out["cost_note"]
    # Estado inicial consultable de inmediato (síncrono antes de agendar el job).
    immediate = await get_task(state, out["task_id"])
    assert immediate["state"] in ("queued", "complete")
    # Dejar terminar el job de fondo.
    await asyncio.gather(*jobs)
    final = await get_task(state, out["task_id"])
    assert final["state"] == "complete"


@pytest.mark.asyncio
async def test_get_task_not_found():
    state = MemoryStateManager()
    out = await get_task(state, "nope")
    assert out["state"] == "not_found"


@pytest.mark.asyncio
async def test_list_tasks_returns_recent():
    state = MemoryStateManager()
    jobs: set = set()

    async def fake_runner(params, task_id, sm):
        await sm.update(task_id, state=TaskState.COMPLETE)

    await start_reel(state, jobs, build_reel_params(topic="a"), runner=fake_runner)
    await start_reel(state, jobs, build_reel_params(topic="b"), runner=fake_runner)
    await asyncio.gather(*jobs)
    out = await list_tasks(state, limit=10)
    assert out["count"] == 2
    assert len(out["tasks"]) == 2
