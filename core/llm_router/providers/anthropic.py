"""Anthropic Claude provider — NUEVO (no estaba en MoneyPrinterTurbo).

Usa el SDK `anthropic` async oficial. Soporta tool_choice + JSON schema
para structured output sin tener que parsear texto.
"""
from __future__ import annotations

from typing import Any, TypeVar

from loguru import logger
from pydantic import BaseModel

from core.llm_router.base import LLMProvider, LLMProviderError, strip_think_blocks

T = TypeVar("T", bound=BaseModel)


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-6"

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "",
        base_url: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model_name=model_name or self.default_model,
            base_url=base_url,
            **kwargs,
        )
        # SDK importado perezosamente para evitar fallo si no está instalado.
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as e:
                raise LLMProviderError(
                    "anthropic SDK not installed. Run `uv add anthropic`."
                ) from e

            kwargs: dict[str, Any] = {"api_key": self.api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _validate(self) -> None:
        if not self.api_key:
            raise LLMProviderError("[anthropic] api_key is required")
        if not self.model_name:
            raise LLMProviderError("[anthropic] model_name is required")

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._validate()
        client = self._get_client()

        msg_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            msg_kwargs["system"] = system
        for k, v in kwargs.items():
            if k not in msg_kwargs and v is not None:
                msg_kwargs[k] = v

        async def _do() -> str:
            response = await client.messages.create(**msg_kwargs)
            # `response.content` es lista de bloques; tomamos el text.
            blocks = getattr(response, "content", None) or []
            for block in blocks:
                if getattr(block, "type", None) == "text":
                    return strip_think_blocks(getattr(block, "text", ""), self.name)
            raise LLMProviderError("[anthropic] response had no text block")

        return await self._with_retry(_do, label="messages.create")

    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        """Usa tool_use forzado con el JSON schema del modelo."""
        self._validate()
        client = self._get_client()

        json_schema = schema.model_json_schema()
        tool_name = f"emit_{schema.__name__.lower()}"
        tools = [
            {
                "name": tool_name,
                "description": f"Emit a structured {schema.__name__} object.",
                "input_schema": json_schema,
            }
        ]
        msg_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
            "tools": tools,
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if system:
            msg_kwargs["system"] = system

        async def _do() -> T:
            response = await client.messages.create(**msg_kwargs)
            for block in getattr(response, "content", None) or []:
                if getattr(block, "type", None) == "tool_use":
                    return schema.model_validate(block.input)
            raise LLMProviderError("[anthropic] response had no tool_use block")

        try:
            return await self._with_retry(_do, label="structured.tool_use")
        except LLMProviderError as e:
            logger.warning(f"[anthropic] tool_use failed ({e}); falling back to text JSON")
            return await self._structured_via_text(
                prompt, schema, system=system, temperature=temperature,
                max_tokens=max_tokens, **kwargs,
            )
