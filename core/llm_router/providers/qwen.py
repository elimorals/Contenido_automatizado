"""Qwen / DashScope provider.

DashScope SDK no es async, así que envolvemos en `asyncio.to_thread`.
DashScope tiene su propio endpoint (no OpenAI-compatible al 100%).
"""
from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from core.llm_router.base import LLMProvider, LLMProviderError, strip_think_blocks

T = TypeVar("T", bound=BaseModel)


def _get(value: Any, key: str) -> Any:
    """Acceso tolerante: dict o objeto SDK."""
    if isinstance(value, dict):
        return value.get(key)
    try:
        return value[key]
    except (KeyError, TypeError, AttributeError):
        return getattr(value, key, None)


class QwenProvider(LLMProvider):
    name = "qwen"

    def _validate(self) -> None:
        if not self.api_key:
            raise LLMProviderError("[qwen] api_key is required")
        if not self.model_name:
            raise LLMProviderError("[qwen] model_name is required")

    def _get_dashscope(self) -> Any:
        try:
            import dashscope
        except ImportError as e:
            raise LLMProviderError("dashscope not installed. Run `uv add dashscope`.") from e
        dashscope.api_key = self.api_key
        return dashscope

    @staticmethod
    def _extract_text(response: Any) -> str:
        output = _get(response, "output")
        if output is None:
            raise LLMProviderError("[qwen] response has no `output`")

        # Nueva forma (messages-based): output.choices[0].message.content
        choices = _get(output, "choices")
        if choices:
            first = choices[0]
            message = _get(first, "message")
            content = _get(message, "content") if message is not None else None
            if content is not None:
                return content

        # Vieja forma (completion-based): output.text
        text = _get(output, "text")
        if text is None:
            raise LLMProviderError("[qwen] response has no text content")
        return text

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._validate()
        dashscope = self._get_dashscope()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        call_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "result_format": "message",
        }
        for k, v in kwargs.items():
            if k not in call_kwargs and v is not None:
                call_kwargs[k] = v

        async def _do() -> str:
            response = await asyncio.to_thread(dashscope.Generation.call, **call_kwargs)
            status_code = getattr(response, "status_code", 200)
            if status_code != 200:
                raise LLMProviderError(f"[qwen] status {status_code}: {response}")
            text = self._extract_text(response)
            return strip_think_blocks(text, self.name)

        return await self._with_retry(_do, label="dashscope.Generation.call")

    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        # DashScope no tiene JSON-mode universal; usamos fallback texto.
        return await self._structured_via_text(
            prompt, schema, system=system, temperature=temperature,
            max_tokens=max_tokens, **kwargs,
        )
