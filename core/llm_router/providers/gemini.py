"""Google Gemini via `google.generativeai`.

Soporta structured output con `response_schema=PydanticModel`.
El SDK no es async nativo, pero exponemos métodos async usando
`asyncio.to_thread` para no bloquear el event loop.
"""
from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from core.llm_router.base import LLMProvider, LLMProviderError, strip_think_blocks

T = TypeVar("T", bound=BaseModel)

_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_DEPRECATED_GEMINI_MODELS = {"gemini-pro", "gemini-1.0-pro"}


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        model_name: str = "",
        base_url: str = "",
        **kwargs: Any,
    ) -> None:
        # Auto-migra modelos deprecados (Gemini cambió nombres en 2024).
        if not model_name:
            model_name = _DEFAULT_GEMINI_MODEL
        elif model_name in _DEPRECATED_GEMINI_MODELS:
            model_name = _DEFAULT_GEMINI_MODEL

        super().__init__(
            api_key=api_key, model_name=model_name, base_url=base_url, **kwargs
        )
        self._configured = False

    def _configure(self) -> Any:
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise LLMProviderError(
                "google-generativeai not installed. Run `uv add google-generativeai`."
            ) from e

        if not self._configured:
            kwargs: dict[str, Any] = {"api_key": self.api_key, "transport": "rest"}
            if self.base_url:
                kwargs["client_options"] = {"api_endpoint": self.base_url}
            genai.configure(**kwargs)
            self._configured = True
        return genai

    def _validate(self) -> None:
        if not self.api_key:
            raise LLMProviderError("[gemini] api_key is required")
        if not self.model_name:
            raise LLMProviderError("[gemini] model_name is required")

    @staticmethod
    def _safety_settings() -> list[dict[str, str]]:
        return [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ]

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._validate()
        genai = self._configure()

        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "top_p": kwargs.get("top_p", 1),
            "top_k": kwargs.get("top_k", 1),
        }
        model_kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "generation_config": generation_config,
            "safety_settings": self._safety_settings(),
        }
        if system:
            model_kwargs["system_instruction"] = system

        async def _do() -> str:
            model = genai.GenerativeModel(**model_kwargs)
            # SDK síncrono → ejecutamos en threadpool.
            response = await asyncio.to_thread(model.generate_content, prompt)
            try:
                text = response.candidates[0].content.parts[0].text
            except (AttributeError, IndexError) as e:
                raise LLMProviderError(f"[gemini] invalid response: {e}") from e
            return strip_think_blocks(text, self.name)

        return await self._with_retry(_do, label="generate_content")

    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        """Usa `response_schema` nativo de Gemini con Pydantic."""
        self._validate()
        genai = self._configure()

        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        model_kwargs: dict[str, Any] = {
            "model_name": self.model_name,
            "generation_config": generation_config,
            "safety_settings": self._safety_settings(),
        }
        if system:
            model_kwargs["system_instruction"] = system

        async def _do() -> T:
            model = genai.GenerativeModel(**model_kwargs)
            response = await asyncio.to_thread(model.generate_content, prompt)
            text = response.candidates[0].content.parts[0].text
            return schema.model_validate_json(text)

        try:
            return await self._with_retry(_do, label="structured.response_schema")
        except LLMProviderError:
            return await self._structured_via_text(
                prompt, schema, system=system, temperature=temperature,
                max_tokens=max_tokens, **kwargs,
            )
