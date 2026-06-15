"""Factory que devuelve memory o redis según config."""
from __future__ import annotations

from shared.config import Config, load_config

from .base import TaskManager
from .memory import InMemoryTaskManager
from .redis_queue import RedisTaskManager


def get_task_manager(config: Config | None = None) -> TaskManager:
    """Devuelve `InMemoryTaskManager` o `RedisTaskManager` según `cfg.app.enable_redis`."""
    cfg = config or load_config()
    if cfg.app.enable_redis:
        return RedisTaskManager(
            max_concurrent_tasks=cfg.app.max_concurrent_tasks,
            max_queued_tasks=cfg.app.max_queued_tasks,
            redis_host=cfg.app.redis_host,
            redis_port=cfg.app.redis_port,
            redis_db=cfg.app.redis_db,
        )
    return InMemoryTaskManager(
        max_concurrent_tasks=cfg.app.max_concurrent_tasks,
        max_queued_tasks=cfg.app.max_queued_tasks,
    )
