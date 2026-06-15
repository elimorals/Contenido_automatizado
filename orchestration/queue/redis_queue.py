"""Cola Redis con LPUSH/BRPOP.

Portado de `MoneyPrinterTurbo/app/controllers/manager/redis_manager.py`,
reescrito sobre `redis.asyncio` (cliente async) en vez del cliente sync.

Patrón LIST + LLEN:
- LPUSH al head, BRPOP al tail (FIFO).
- LLEN para queue_size (O(1)).
- Backpressure: LLEN check antes de LPUSH (no atómico — race posible bajo
  carga extrema, pero el daño es a lo sumo overshoot mínimo del bound;
  para garantía estricta habría que usar Lua script).
- Reconnect: el cliente de redis.asyncio reconecta automáticamente al usar
  `decode_responses=False` y connection pool.

Para tracking de `active_count` consultamos el state manager (no exposed
aquí — el factory inyecta la cuenta vía un getter externo opcional). En
esta implementación devolvemos 0 si no se setea — el control real lo lleva
el worker leyendo state.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from loguru import logger
from redis import asyncio as aioredis

from shared.schemas import VideoParams

from .base import QueueFullError, TaskManager

_QUEUE_KEY = "contenido:task_queue"


class RedisTaskManager(TaskManager):
    """Cola Redis (LIST). Sobrevive reinicios; multi-worker safe."""

    def __init__(
        self,
        max_concurrent_tasks: int = 3,
        max_queued_tasks: int = 50,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_db: int = 0,
        redis_password: str | None = None,
        queue_key: str = _QUEUE_KEY,
        active_count_provider: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks
        self.max_queued_tasks = max_queued_tasks
        self.queue_key = queue_key
        # `redis.asyncio.Redis` con pool: reconnect automático en errores
        # transitorios. `decode_responses=True` simplifica el manejo de JSON.
        self._redis: aioredis.Redis = aioredis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True,
        )
        self._active_count_provider = active_count_provider

    async def enqueue(self, task_id: str, params: VideoParams) -> None:
        size = await self._redis.llen(self.queue_key)
        if size >= self.max_queued_tasks:
            logger.warning(
                f"reject task {task_id}: redis queue full ({size}/{self.max_queued_tasks})"
            )
            raise QueueFullError(
                f"task queue is full (max={self.max_queued_tasks}), try again later"
            )
        # Serializamos params usando model_dump_json para handle de Path,
        # Enum, etc. (lo mismo que TaskInfo).
        payload = json.dumps(
            {"task_id": task_id, "params": params.model_dump(mode="json")}
        )
        await self._redis.lpush(self.queue_key, payload)
        logger.info(f"enqueue task {task_id} (size={size + 1})")

    async def dequeue(self, timeout: float = 5.0) -> tuple[str, VideoParams] | None:
        # BRPOP bloquea hasta `timeout` segundos. Devuelve None si vence.
        # `timeout` en BRPOP es int; redondeamos hacia arriba para garantizar
        # que esperamos al menos lo pedido (mejor "un poco más" que no esperar).
        timeout_int = max(1, int(timeout + 0.999))
        res = await self._redis.brpop([self.queue_key], timeout=timeout_int)
        if not res:
            return None
        # res = (queue_key, payload)
        _, payload = res
        data = json.loads(payload)
        # Reconstruir VideoParams desde dict
        params = VideoParams.model_validate(data["params"])
        return data["task_id"], params

    async def queue_size(self) -> int:
        return await self._redis.llen(self.queue_key)

    async def active_count(self) -> int:
        if self._active_count_provider is not None:
            return await self._active_count_provider()
        # Sin provider → 0 (el control real lo hace el worker via state).
        return 0

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception as e:  # pragma: no cover
            logger.warning(f"error closing redis: {e}")
