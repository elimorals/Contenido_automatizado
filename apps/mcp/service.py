"""Lógica del MCP server — SIN dependencia de `mcp`.

Toda la inteligencia del MCP vive aquí para ser testeable sin el SDK ni el LLM:
construcción/validación de params, gate de costo, ejecución de jobs en background
sobre el `StateManager` existente, y formateo de estado. `apps/mcp/server.py` es
una capa fina FastMCP sobre estas funciones. Ver ADR-021.

Decisión de alcance (gate de costo): el MCP expone SOLO reels (topic/article/subject).
El long-form ($16–80/video) NO se expone como tool — sigue siendo human-gated vía
CLI/plan editorial. No hay parámetro para dispararlo desde aquí.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from loguru import logger

from apps.api.pipeline import run_pipeline
from shared.schemas import (
    GenerationMode,
    TaskInfo,
    TaskState,
    VideoAspect,
    VideoParams,
    VisualStrategy,
)

# Estimación coarse de costo por modo (USD) — para que el agente vea el gasto antes
# de disparar. Valores de docs/COST_MODEL.md (reels).
_COST_NOTE: dict[GenerationMode, str] = {
    GenerationMode.EXPRESS: "~$0.01–0.05 por reel (1 LLM call)",
    GenerationMode.PREMIUM: "~$0.08–1.20 por reel (DAG de 18 reasoners + media)",
}

# Tipo del runner inyectable (run_pipeline real, o un fake en tests).
Runner = Callable[[VideoParams, str, Any], Awaitable[Any]]


def build_reel_params(
    *,
    topic: str | None = None,
    url: str | None = None,
    subject: str | None = None,
    reference_url: str | None = None,
    mode: str = "premium",
    aspect: str = "9:16",
    strategy: str = "hybrid",
    voice_name: str = "",
    use_veo: bool = False,
) -> VideoParams:
    """Construye un `VideoParams` para un reel desde args del MCP.

    Exige EXACTAMENTE una entrada (topic | url | subject). El long-form no se
    expone aquí a propósito (gate de costo). Lanza ValueError si la entrada o
    los enums son inválidos.
    """
    entries = [e for e in (topic, url, subject) if e]
    if len(entries) == 0:
        raise ValueError("Se requiere una entrada: topic, url o subject.")
    if len(entries) > 1:
        raise ValueError(
            "Provee SOLO una entrada (topic, url o subject), no varias."
        )

    try:
        mode_enum = GenerationMode(mode)
    except ValueError as e:
        raise ValueError(
            f"mode inválido '{mode}'. Usa 'express' o 'premium' "
            "(long_form no se expone vía MCP)."
        ) from e

    return VideoParams(
        topic=topic,
        url=url,
        subject=subject,
        reference_url=reference_url or None,
        mode=mode_enum,
        aspect=VideoAspect(aspect),
        visual_strategy=VisualStrategy(strategy),
        voice_name=voice_name,
        use_veo=use_veo,
    )


def task_state_name(state: TaskState | int) -> str:
    """IntEnum TaskState → string legible para el agente."""
    try:
        s = state if isinstance(state, TaskState) else TaskState(state)
    except ValueError:
        return "unknown"
    return {
        TaskState.COMPLETE: "complete",
        TaskState.FAILED: "failed",
        TaskState.PROCESSING: "processing",
        TaskState.QUEUED: "queued",
    }.get(s, "unknown")


def cost_note(mode: GenerationMode) -> str:
    """Nota coarse de costo por modo (para visibilidad del agente)."""
    return _COST_NOTE.get(mode, "costo desconocido")


def format_task(info: TaskInfo) -> dict[str, Any]:
    """TaskInfo → dict compacto y legible para el agente (omite verbosidad)."""
    return {
        "task_id": info.task_id,
        "state": task_state_name(info.state),
        "progress": info.progress,
        "videos": list(info.videos),
        "materials_count": len(info.materials),
        "quality_flags": dict(info.quality_flags),
        "cost_breakdown": dict(info.cost_breakdown),
        "timings_s": dict(info.timings_s),
        "reference_brief": (
            info.reference_brief.model_dump() if info.reference_brief else None
        ),
        "script": info.script,
    }


async def run_job(
    state: Any,
    params: VideoParams,
    task_id: str,
    *,
    runner: Runner = run_pipeline,
) -> None:
    """Corre un job a término sobre el state manager; marca FAILED si revienta.

    Garantiza que un crash del pipeline no deje la task colgada en PROCESSING.
    """
    try:
        await runner(params, task_id, state)
    except Exception as e:  # noqa: BLE001 - frontera del job; se registra el fallo
        logger.error(f"[mcp] job {task_id} falló: {e}")
        try:
            await state.update(task_id, state=TaskState.FAILED)
        except Exception:  # pragma: no cover - state inconsistente
            logger.error(f"[mcp] no se pudo marcar FAILED el job {task_id}")


async def start_reel(
    state: Any,
    jobs: set,
    params: VideoParams,
    *,
    runner: Runner = run_pipeline,
) -> dict[str, Any]:
    """Agenda un reel en background y devuelve task_id + nota de costo de inmediato.

    Setea un TaskInfo QUEUED SÍNCRONO antes de agendar para que un `get_task`
    inmediato no falle. El job se guarda en `jobs` (set provisto por el server)
    para que no lo recoja el GC; se auto-descarta al terminar.
    """
    task_id = uuid4().hex
    await state.set(
        task_id,
        TaskInfo(task_id=task_id, state=TaskState.QUEUED, mode=params.mode, params=params),
    )
    task = asyncio.create_task(run_job(state, params, task_id, runner=runner))
    jobs.add(task)
    task.add_done_callback(jobs.discard)
    return {
        "task_id": task_id,
        "state": "queued",
        "mode": params.mode.value,
        "cost_note": cost_note(params.mode),
        "hint": "Sondea el progreso con contenido_get_task(task_id).",
    }


async def get_task(state: Any, task_id: str) -> dict[str, Any]:
    """Estado actual de un job. `state='not_found'` si no existe."""
    info = await state.get(task_id)
    if info is None:
        return {"task_id": task_id, "state": "not_found"}
    return format_task(info)


async def list_tasks(state: Any, limit: int = 10) -> dict[str, Any]:
    """Lista las tasks recientes (paginado simple)."""
    tasks, total = await state.list(page=1, page_size=limit)
    return {
        "total": total,
        "count": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "state": task_state_name(t.state),
                "progress": t.progress,
            }
            for t in tasks
        ],
    }


__all__ = [
    "Runner",
    "build_reel_params",
    "cost_note",
    "format_task",
    "get_task",
    "list_tasks",
    "run_job",
    "start_reel",
    "task_state_name",
]
