"""Canvas del job según el aspect pedido (ADR-007).

Antes todo generador tenía 720×1280 fijo y un job 16:9 recibía frames
verticales. El aspect se fija una vez por beat (`use_aspect`) y cada
generador lee `current_canvas()`; un ContextVar sobrevive a `asyncio.gather`.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from shared.config import load_config

_aspect: ContextVar[str] = ContextVar("canvas_aspect", default="9:16")


def canvas_for(aspect: str) -> tuple[int, int]:
    """(w, h) para el aspect. Lado corto = `canvas_w` de config (720 default)."""
    cfg = load_config().visual
    short, long_ = min(cfg.canvas_w, cfg.canvas_h), max(cfg.canvas_w, cfg.canvas_h)
    if aspect == "16:9":
        return long_, short
    if aspect == "1:1":
        return short, short
    return short, long_


def current_aspect() -> str:
    return _aspect.get()


def current_canvas() -> tuple[int, int]:
    return canvas_for(_aspect.get())


@contextmanager
def use_aspect(aspect: str) -> Iterator[None]:
    token = _aspect.set(aspect if aspect in ("9:16", "16:9", "1:1") else "9:16")
    try:
        yield
    finally:
        _aspect.reset(token)
