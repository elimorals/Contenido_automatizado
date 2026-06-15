"""State Redis: hash por task_id + set para listing.

Portado de `MoneyPrinterTurbo/app/services/state.py:RedisState`.

Diferencias clave vs origen:
- `redis.asyncio` (no sync).
- Hash key `task:{task_id}` (en MPT era directamente `{task_id}` — el
  prefijo evita colisiones con otras keys del namespace).
- Set `tasks:all` para listing paginado (en MPT usaba SCAN, que tiene
  pagination complicada; un sorted set por timestamp simplifica).
- Serialización JSON completa de TaskInfo (`model_dump_json(mode='json')`
  maneja Path, Enum, datetime).
- Stat indexes adicionales: `tasks:by_state:{N}` (set) para `count_by_state`
  en O(1).
"""
from __future__ import annotations

from typing import Any

from loguru import logger
from redis import asyncio as aioredis

from shared.schemas import TaskInfo, TaskState

from .base import StateManager

_TASK_PREFIX = "contenido:task"
_TASKS_INDEX = "contenido:tasks:all"
_TASKS_BY_STATE_PREFIX = "contenido:tasks:by_state"


class RedisStateManager(StateManager):
    """State persistido en Redis. Multi-worker safe."""

    def __init__(
        self,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: str | None = None,
    ) -> None:
        self._redis: aioredis.Redis = aioredis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True,
        )

    @staticmethod
    def _key(task_id: str) -> str:
        return f"{_TASK_PREFIX}:{task_id}"

    @staticmethod
    def _state_set_key(state: int | TaskState) -> str:
        v = state.value if isinstance(state, TaskState) else int(state)
        return f"{_TASKS_BY_STATE_PREFIX}:{v}"

    async def get(self, task_id: str) -> TaskInfo | None:
        payload = await self._redis.get(self._key(task_id))
        if payload is None:
            return None
        return TaskInfo.model_validate_json(payload)

    async def set(self, task_id: str, info: TaskInfo) -> None:
        # Serializamos en JSON usando mode="json" para manejar Path/Enum.
        payload = info.model_dump_json()
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self._key(task_id), payload)
            pipe.sadd(_TASKS_INDEX, task_id)
            # Index por state. Antes de set, sacar de otros buckets para no
            # quedar en estados viejos. Sin transacción esto sería racey;
            # con MULTI/EXEC es atómico.
            for s in TaskState:
                if s != info.state:
                    pipe.srem(self._state_set_key(s), task_id)
            pipe.sadd(self._state_set_key(info.state), task_id)
            await pipe.execute()
        logger.debug(f"state set {task_id} -> {info.state.name}")

    async def update(self, task_id: str, **fields: Any) -> TaskInfo:
        current = await self.get(task_id)
        if current is None:
            raise KeyError(task_id)
        if "progress" in fields:
            fields["progress"] = max(0, min(100, int(fields["progress"])))
        updated = current.model_copy(update=fields)
        # Re-set (también arregla los state indexes).
        await self.set(task_id, updated)
        return updated

    async def delete(self, task_id: str) -> bool:
        # Limpiar todos los índices.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(self._key(task_id))
            pipe.srem(_TASKS_INDEX, task_id)
            for s in TaskState:
                pipe.srem(self._state_set_key(s), task_id)
            results = await pipe.execute()
        # results[0] = 0 si no existía, 1 si se borró
        return bool(results[0])

    async def list(self, page: int = 1, page_size: int = 10) -> tuple[list[TaskInfo], int]:
        # SMEMBERS no garantiza orden — para pagination estable habría que
        # usar un sorted set por created_at. Para el MVP, ordenamos por
        # task_id (lexicográfico) y sliceamos.
        all_ids = sorted(await self._redis.smembers(_TASKS_INDEX))
        total = len(all_ids)
        start = max(0, (page - 1) * page_size)
        end = start + page_size
        page_ids = all_ids[start:end]
        if not page_ids:
            return [], total
        # MGET bulk fetch.
        payloads = await self._redis.mget([self._key(tid) for tid in page_ids])
        tasks = [
            TaskInfo.model_validate_json(p) for p in payloads if p is not None
        ]
        return tasks, total

    async def count_by_state(self, state: int) -> int:
        return await self._redis.scard(self._state_set_key(state))

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception as e:  # pragma: no cover
            logger.warning(f"error closing redis: {e}")
