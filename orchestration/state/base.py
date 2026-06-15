"""ABC del state manager.

Portado y reescrito en async desde
`MoneyPrinterTurbo/app/services/state.py:BaseState`.

Diferencias vs origen:
- Pydantic `TaskInfo` en vez de dict crudo. Tipado fuerte.
- API explícita: `get / set / update / delete / list` (en MPT era
  `get_task / update_task / get_all_tasks / delete_task` con kwargs).
- Async first (RedisState lo era sync con cliente sync).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from shared.schemas import TaskInfo


class StateManager(ABC):
    """Persistencia de `TaskInfo`. Implementaciones: memory, redis."""

    @abstractmethod
    async def get(self, task_id: str) -> TaskInfo | None:
        """Devuelve `TaskInfo` o `None` si no existe."""

    @abstractmethod
    async def set(self, task_id: str, info: TaskInfo) -> None:
        """Crea/reemplaza el TaskInfo."""

    @abstractmethod
    async def update(self, task_id: str, **fields: Any) -> TaskInfo:
        """Patch de campos. Levanta `KeyError` si no existe."""

    @abstractmethod
    async def delete(self, task_id: str) -> bool:
        """Devuelve True si borró, False si no existía."""

    @abstractmethod
    async def list(self, page: int = 1, page_size: int = 10) -> tuple[list[TaskInfo], int]:
        """Lista paginada + total. `page` es 1-indexed."""

    @abstractmethod
    async def count_by_state(self, state: int) -> int:
        """Cuenta de tasks en un estado (para active_count en queue)."""

    @abstractmethod
    async def close(self) -> None:
        """Cierra recursos."""
