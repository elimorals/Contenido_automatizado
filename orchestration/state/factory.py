"""Factory para state manager."""
from __future__ import annotations

from shared.config import Config, load_config

from .base import StateManager
from .memory import MemoryStateManager
from .redis_state import RedisStateManager


def get_state_manager(config: Config | None = None) -> StateManager:
    """Devuelve `MemoryStateManager` o `RedisStateManager` según config."""
    cfg = config or load_config()
    if cfg.app.enable_redis:
        return RedisStateManager(
            redis_host=cfg.app.redis_host,
            redis_port=cfg.app.redis_port,
            redis_db=cfg.app.redis_db,
        )
    return MemoryStateManager()
