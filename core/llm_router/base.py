"""Interfaz abstracta para todos los providers LLM.

Toda la app habla con la abstracción `LLMProvider`. Los drivers concretos
(en `providers/`) implementan `complete()` y `complete_structured()` —
todos async, con retry/backoff y limpieza de respuestas.
"""
from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Bloques <think>...</think> que algunos reasoning models (DeepSeek R1,
# MiniMax M3, Qwen QwQ) embeben en la respuesta. Si no los limpiamos, el
# script, los subtítulos y la voz incluyen la "cadena de pensamiento" como
# si fuese contenido. Se conserva esta lógica del MPT original.
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_UNCLOSED_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*$", re.IGNORECASE | re.DOTALL)


def strip_think_blocks(content: str | None, provider_name: str = "") -> str:
    """Quita bloques <think>...</think> y valida que quede texto.

    Levanta `ValueError` si la respuesta es None, vacía, o solo contiene
    "pensamiento" sin contenido final.
    """
    if content is None:
        raise ValueError(f"[{provider_name}] returned empty text content")

    if not isinstance(content, str):
        raise TypeError(
            f"[{provider_name}] returned non-text content: {type(content).__name__}"
        )

    cleaned = _THINK_BLOCK_RE.sub("", content)
    cleaned = _UNCLOSED_THINK_BLOCK_RE.sub("", cleaned).strip()
    if not cleaned:
        raise ValueError(f"[{provider_name}] returned empty text content after cleaning")

    return cleaned


class LLMProviderError(Exception):
    """Error base para fallos de un provider LLM (red, auth, formato)."""


class LLMProvider(ABC):
    """Interfaz unificada para cualquier driver LLM.

    Implementaciones concretas viven en `core.llm_router.providers.*`.
    Cada provider expone:
      - `complete()` → texto libre
      - `complete_structured()` → instancia de un Pydantic model
    """

    name: str = "abstract"

    # Defaults razonables (override en subclases si el proveedor cambia).
    default_timeout: float = 60.0
    default_max_retries: int = 3
    default_retry_backoff: float = 1.5  # multiplicador exponencial

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "",
        base_url: str = "",
        timeout: float | None = None,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url
        self.timeout = timeout if timeout is not None else self.default_timeout
        self.max_retries = max_retries if max_retries is not None else self.default_max_retries
        self.extra: dict[str, Any] = kwargs

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        """Genera texto libre. Debe ser implementado por cada driver."""

    @abstractmethod
    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        """Genera salida estructurada validada contra un Pydantic model.

        Por defecto cae a `_structured_via_text()`, que pide JSON al modelo
        y parsea con retry. Los providers que soporten JSON-mode nativo
        (OpenAI, Gemini) deben sobreescribir para usar la feature nativa.
        """

    async def _structured_via_text(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        """Fallback: pedir JSON al modelo en texto plano y parsear."""
        json_schema = schema.model_json_schema()
        sys_msg = (
            (system + "\n\n" if system else "")
            + "Respond ONLY with a JSON object that matches this schema. "
            + "No prose, no markdown fences, no <think> blocks.\n\n"
            + f"Schema: {json_schema}"
        )
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            raw = await self.complete(
                prompt=prompt,
                system=sys_msg,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            try:
                # Quita posibles fences ```json ... ```
                trimmed = raw.strip()
                if trimmed.startswith("```"):
                    trimmed = re.sub(r"^```(?:json)?\s*|\s*```$", "", trimmed, flags=re.DOTALL)
                return schema.model_validate_json(trimmed)
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(
                    f"[{self.name}] structured parse attempt {attempt + 1}/{self.max_retries} "
                    f"failed: {e}"
                )
        raise LLMProviderError(
            f"[{self.name}] could not produce valid structured output after "
            f"{self.max_retries} attempts: {last_err}"
        )

    async def _with_retry(self, coro_factory, *, label: str = "request") -> Any:
        """Ejecuta `coro_factory()` con backoff exponencial.

        `coro_factory` es un callable sin args que devuelve una coroutine
        nueva en cada intento (las coroutines no pueden reusarse).
        """
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return await coro_factory()
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = self.default_retry_backoff ** attempt
                logger.warning(
                    f"[{self.name}] {label} attempt {attempt + 1}/{self.max_retries} "
                    f"failed: {e}. Retrying in {wait:.1f}s"
                )
                await asyncio.sleep(wait)
        raise LLMProviderError(
            f"[{self.name}] {label} failed after {self.max_retries} attempts: {last_err}"
        )

    def __repr__(self) -> str:  # debug-friendly
        return f"<{self.__class__.__name__} name={self.name} model={self.model_name!r}>"
