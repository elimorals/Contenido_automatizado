"""LiteLLM unified — fallback para 100+ providers no listados explícitamente.

LiteLLM tiene `acompletion()` async nativo. Usalo cuando quieras llamar
a un modelo que no tiene driver dedicado (ej. cohere, bedrock, vertex).
"""
from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from core.llm_router.base import LLMProvider, LLMProviderError, strip_think_blocks

T = TypeVar("T", bound=BaseModel)


class LiteLLMProvider(LLMProvider):
    name = "litellm"

    def _validate(self) -> None:
        if not self.model_name:
            raise LLMProviderError("[litellm] model_name is required")

    def _get_litellm(self) -> Any:
        try:
            import litellm
        except ImportError as e:
            raise LLMProviderError("litellm not installed. Run `uv add litellm`.") from e
        return litellm

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._validate()
        litellm = self._get_litellm()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        call_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "drop_params": True,
            "timeout": self.timeout,
        }
        if self.api_key:
            call_kwargs["api_key"] = self.api_key
        if self.base_url:
            call_kwargs["api_base"] = self.base_url
        for k, v in kwargs.items():
            if k not in call_kwargs and v is not None:
                call_kwargs[k] = v

        async def _do() -> str:
            response = await litellm.acompletion(**call_kwargs)
            choices = getattr(response, "choices", None)
            if not choices:
                raise LLMProviderError("[litellm] response has no choices")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message is not None else None
            return strip_think_blocks(content, self.name)

        return await self._with_retry(_do, label="acompletion")

    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        return await self._structured_via_text(
            prompt, schema, system=system, temperature=temperature,
            max_tokens=max_tokens, **kwargs,
        )
