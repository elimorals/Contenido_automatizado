"""Cola in-memory (asyncio.Queue).

Portado de `MoneyPrinterTurbo/app/controllers/manager/memory_manager.py`
y reescrito en async. Apto para dev/test/single-process; en prod usar Redis.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from shared.schemas import VideoParams

from .base import QueueFullError, TaskManager


class InMemoryTaskManager(TaskManager):
    """Cola en RAM con asyncio.Queue. NO sobrevive a reinicios."""

    def __init__(self, max_concurrent_tasks: int = 3, max_queued_tasks: int = 50) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks
        self.max_queued_tasks = max_queued_tasks
        # Bound estricto para evitar OOM por backlogs anónimos.
        self._queue: asyncio.Queue[tuple[str, VideoParams]] = asyncio.Queue(
            maxsize=max_queued_tasks
        )
        # Contador de tasks activas (PROCESSING). Tracking opcional — el
        # source de verdad es el state manager, pero exponerlo aquí permite
        # backpressure rápida sin round-trip a Redis.
        self._active = 0
        self._active_lock = asyncio.Lock()

    async def enqueue(self, task_id: str, params: VideoParams) -> None:
        # `asyncio.Queue` ya implementa el `maxsize` check; usamos put_nowait
        # para fallar rápido en lugar de bloquear al caller.
        if self._queue.full():
            logger.warning(
                f"reject task {task_id}: queue full (max={self.max_queued_tasks})"
            )
            raise QueueFullError(
                f"task queue is full (max={self.max_queued_tasks}), try again later"
            )
        await self._queue.put((task_id, params))
        logger.info(f"enqueue task {task_id} (size={self._queue.qsize()})")

    async def dequeue(self, timeout: float = 5.0) -> tuple[str, VideoParams] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def queue_size(self) -> int:
        return self._queue.qsize()

    async def active_count(self) -> int:
        return self._active

    async def increment_active(self) -> None:
        async with self._active_lock:
            self._active += 1

    async def decrement_active(self) -> None:
        async with self._active_lock:
            self._active = max(0, self._active - 1)

    async def close(self) -> None:
        # Nada que cerrar para memory.
        return
