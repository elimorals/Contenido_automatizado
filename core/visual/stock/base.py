"""Interfaz común para proveedores de stock footage.

Cada provider (Pexels, Pixabay, Coverr) implementa `StockProvider` y se
registra en `registry.py`. Todos son async (httpx) — sin requests sync.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from shared.schemas import MaterialInfo, VideoAspect


class NoAPIKeyError(RuntimeError):
    """Raise cuando el provider no tiene ninguna API key configurada."""


class AllKeysExhaustedError(RuntimeError):
    """Raise cuando todas las API keys del provider fueron rechazadas (401/429)."""


class APIKeyRotator:
    """Rotador thread-safe + async-safe de API keys.

    Equivalente al pattern de MoneyPrinterTurbo (`_api_key_counter` +
    `threading.Lock`), pero usando `asyncio.Lock` para no bloquear el
    event loop. Mantiene también un `threading.Lock` para escenarios
    mixtos sync/async (raro pero seguro).
    """

    def __init__(self, keys: list[str], provider_name: str) -> None:
        self._keys = [k for k in keys if k]
        self._provider_name = provider_name
        self._idx = 0
        self._lock = asyncio.Lock()
        self._exhausted: set[str] = set()

    @property
    def has_keys(self) -> bool:
        return bool(self._keys)

    @property
    def total(self) -> int:
        return len(self._keys)

    async def acquire(self) -> str:
        """Devuelve la siguiente key (round-robin) excluyendo las exhaustas."""
        async with self._lock:
            if not self._keys:
                raise NoAPIKeyError(
                    f"No hay API keys configuradas para provider '{self._provider_name}'"
                )
            alive = [k for k in self._keys if k not in self._exhausted]
            if not alive:
                raise AllKeysExhaustedError(
                    f"Todas las API keys de '{self._provider_name}' fueron rechazadas"
                )
            # round-robin sobre la lista original — mantenemos paridad con MPT
            self._idx = (self._idx + 1) % len(self._keys)
            # buscar siguiente key viva a partir del índice actual
            for offset in range(len(self._keys)):
                candidate = self._keys[(self._idx + offset) % len(self._keys)]
                if candidate not in self._exhausted:
                    return candidate
            raise AllKeysExhaustedError(
                f"Todas las API keys de '{self._provider_name}' fueron rechazadas"
            )

    async def mark_exhausted(self, key: str) -> None:
        async with self._lock:
            self._exhausted.add(key)

    async def reset(self) -> None:
        async with self._lock:
            self._exhausted.clear()


class StockProvider(ABC):
    """Contrato común para los 3 proveedores de stock footage."""

    name: str = ""

    @abstractmethod
    async def search(
        self,
        query: str,
        aspect: VideoAspect,
        min_duration_s: float = 3.0,
    ) -> list[MaterialInfo]:
        """Busca videos relevantes. Devuelve lista vacía si nada matchea.

        Implementaciones deben:
        - Rotar API keys si la actual responde 401/429.
        - Filtrar por `min_duration_s`.
        - Devolver `MaterialInfo` con provider, url, duration_s, width, height.
        """

    @abstractmethod
    async def download(self, url: str, dest: Path) -> Path:
        """Descarga `url` a `dest`. Sobrescribe destino.

        Implementaciones deben usar httpx async con timeout (60, 240)s
        y User-Agent de browser.
        """
