"""Capa de orquestación: queues, state, AgentField adapter."""
from __future__ import annotations

from .agentfield.adapter import AgentFieldAdapter, DispatchResult
from .queue.base import QueueFullError, TaskManager
from .queue.factory import get_task_manager
from .state.base import StateManager
from .state.factory import get_state_manager

__all__ = [
    "AgentFieldAdapter",
    "DispatchResult",
    "QueueFullError",
    "StateManager",
    "TaskManager",
    "get_state_manager",
    "get_task_manager",
]
