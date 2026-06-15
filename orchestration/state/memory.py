"""State in-memory: dict + asyncio.Lock.

Portado de `MoneyPrinterTurbo/app/services/state.py:MemoryState`.
Diferencias: TaskInfo tipado, async + lock para writes, pagination consistente.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from shared.schemas import TaskInfo, TaskState

from .base import StateManager


class MemoryStateManager(StateManager):
    """State en RAM. Apto para dev/test/single-process."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskInfo] = {}
        # Lock para evitar TOCTOU en `update` (read-modify-write).
        self._lock = asyncio.Lock()

    async def get(self, task_id: str) -> TaskInfo | None:
        return self._tasks.get(task_id)

    async def set(self, task_id: str, info: TaskInfo) -> None:
        async with self._lock:
            self._tasks[task_id] = info
        logger.debug(f"state set {task_id} -> {info.state.name}")

    async def update(self, task_id: str, **fields: Any) -> TaskInfo:
        async with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                raise KeyError(task_id)
            # Clamp de progress a [0, 100] como hacía MPT.
            if "progress" in fields:
                fields["progress"] = max(0, min(100, int(fields["progress"])))
            updated = current.model_copy(update=fields)
            self._tasks[task_id] = updated
            return updated

    async def delete(self, task_id: str) -> bool:
        async with self._lock:
            return self._tasks.pop(task_id, None) is not None

    async def list(self, page: int = 1, page_size: int = 10) -> tuple[list[TaskInfo], int]:
        all_tasks = list(self._tasks.values())
        total = len(all_tasks)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        return all_tasks[start:end], total

    async def count_by_state(self, state: int) -> int:
        target = TaskState(state) if not isinstance(state, TaskState) else state
        return sum(1 for t in self._tasks.values() if t.state == target)

    async def close(self) -> None:
        return
