"""ABC del task manager (queue).

Portado y reescrito en async desde
`MoneyPrinterTurbo/app/controllers/manager/base_manager.py`.

Diferencias clave vs origen:
- 100% async (asyncio en vez de threading).
- `enqueue` recibe `(task_id, params)` en lugar de `func/args/kwargs` — la
  capa de orquestación ya no acopla función a ejecutar; el worker decide
  qué hacer con `(task_id, VideoParams)`.
- `dequeue` con timeout (BRPOP-like). Devuelve None si timeout vence.
- Backpressure: `enqueue` levanta `QueueFullError` (→ HTTP 429 en la API).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from shared.schemas import VideoParams


class QueueFullError(Exception):
    """Cola llena — backpressure. La API debe traducirlo a HTTP 429."""


class TaskManager(ABC):
    """Cola de tasks abstracta. Implementaciones: memory, redis."""

    max_concurrent_tasks: int
    max_queued_tasks: int

    @abstractmethod
    async def enqueue(self, task_id: str, params: VideoParams) -> None:
        """Encola task. Levanta `QueueFullError` si la cola está llena."""

    @abstractmethod
    async def dequeue(self, timeout: float = 5.0) -> tuple[str, VideoParams] | None:
        """Desencola con timeout (segundos). Devuelve `None` si vence."""

    @abstractmethod
    async def queue_size(self) -> int:
        """Tamaño actual de la cola."""

    @abstractmethod
    async def active_count(self) -> int:
        """Tasks en ejecución (estado PROCESSING en state manager)."""

    @abstractmethod
    async def close(self) -> None:
        """Cierra recursos (conexión Redis, etc)."""
