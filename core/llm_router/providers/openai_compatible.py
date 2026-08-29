"""Driver base compatible con OpenAI Chat Completions.

Cualquier proveedor que exponga `/chat/completions` con el contrato OpenAI
puede usar este driver — solo cambia `base_url`, `api_key` y `model_name`.
Cubre: OpenAI, Moonshot, Ollama, DeepSeek, MiMo, Groq, Grok, OneAPI,
AIHubMix, AIML, MiniMax, OpenRouter, ModelScope, Pollinations.
"""
from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from loguru import logger
from pydantic import BaseModel

from core.llm_router.base import LLMProvider, LLMProviderError, strip_think_blocks

T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleProvider(LLMProvider):
    """Cliente async vía httpx para endpoints estilo OpenAI."""

    name = "openai_compatible"
    default_base_url = "https://api.openai.com/v1"
    # Algunos providers (Ollama, Pollinations) no exigen API key.
    requires_api_key: bool = True
    # ¿Soporta `response_format={"type": "json_object"}`?
    supports_json_mode: bool = True
    # Headers adicionales (override en subclases).
    extra_headers: dict[str, str] = {}

    def _build_url(self) -> str:
        base = (self.base_url or self.default_base_url).rstrip("/")
        # Si ya termina en /chat/completions úsala tal cual (Pollinations).
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.extra_headers)
        return headers

    def _build_messages(self, prompt: str, system: str) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _validate(self) -> None:
        if self.requires_api_key and not self.api_key:
            raise LLMProviderError(f"[{self.name}] api_key is required")
        if not self.model_name:
            raise LLMProviderError(f"[{self.name}] model_name is required")

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self._build_url()
        headers = self._build_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise LLMProviderError(
                    f"[{self.name}] HTTP {resp.status_code}: {resp.text[:500]}"
                )
            return resp.json()

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not choices:
            raise LLMProviderError("response has no `choices`")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not content:
            # `finish_reason` y los tokens de razonamiento son la diferencia
            # entre "el modelo no contestó" y "se le acabó el presupuesto
            # razonando". Sin ellos el error de arriba no dice nada útil.
            finish = choices[0].get("finish_reason")
            reasoning = (
                (data.get("usage") or {}).get("completion_tokens_details") or {}
            ).get("reasoning_tokens")
            detail = f"finish_reason={finish!r}"
            if reasoning is not None:
                detail += f", reasoning_tokens={reasoning}"
            if finish == "length":
                detail += " — sube el límite de tokens: en modelos de "
                detail += "razonamiento cubre razonamiento + respuesta"
            raise LLMProviderError(f"response message has no `content` ({detail})")
        return content

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int]:
        """(input_tokens, output_tokens) del usage del response.

        OpenAI estilo: data['usage'] = {prompt_tokens, completion_tokens, total_tokens}
        Si el provider no lo devuelve, retorna (0, 0).
        """
        usage = data.get("usage") or {}
        return (
            int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        )

    def _token_limit_key(self) -> str:
        """Nombre del parámetro de límite de tokens para este provider/modelo.

        La mayoría del ecosistema OpenAI-compatible sigue usando `max_tokens`.
        OpenAI lo renombró a `max_completion_tokens` en sus modelos nuevos y
        **rechaza el viejo con HTTP 400**, así que `OpenAIProvider` lo
        sobreescribe. Se resuelve por modelo, no por provider, porque una misma
        cuenta sirve gpt-4o (viejo) y gpt-5 (nuevo).
        """
        return "max_tokens"

    def _supports_temperature(self) -> bool:
        """¿El modelo acepta un `temperature` distinto del default?

        Los modelos de razonamiento de OpenAI (gpt-5+, serie o) solo admiten
        el default y devuelven HTTP 400 ante cualquier otro valor. Cuando esto
        es False el parámetro se omite del payload.

        OJO al efecto de segundo orden: el DAG usa la temperatura como palanca
        (0.8 en los hunters para diversidad, 0.0 en los selectores para
        determinismo). Con estos modelos esa palanca deja de existir.
        """
        return True

    def _apply_sampling(self, payload: dict[str, Any], temperature: float) -> None:
        if self._supports_temperature():
            payload["temperature"] = temperature

    def _min_token_budget(self) -> int:
        """Piso del límite de tokens. 0 = respetar lo que pida el caller.

        En los modelos de razonamiento el parámetro cambió de significado: ya no
        acota la RESPUESTA sino razonamiento **más** respuesta. Un caller que
        pidió 2000 pensando en la respuesta puede quedarse sin nada — la API
        devuelve `content: ""` con `finish_reason: "length"`, que aguas abajo
        aparece como el críptico "empty text content after cleaning".
        """
        return 0

    def _effective_token_limit(self, max_tokens: int) -> int:
        return max(max_tokens, self._min_token_budget())

    async def complete(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> str:
        self._validate()
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(prompt, system),
            self._token_limit_key(): self._effective_token_limit(max_tokens),
        }
        self._apply_sampling(payload, temperature)
        # Permite a llamadores pasar extras como `top_p`, `seed`, etc.
        for k, v in kwargs.items():
            if k not in payload and v is not None:
                payload[k] = v

        async def _do() -> str:
            data = await self._post(payload)
            text = self._extract_text(data)
            # Stamp cost (sin error si usage no viene)
            in_tok, out_tok = self._extract_usage(data)
            self._stamp_cost(in_tok, out_tok)
            return strip_think_blocks(text, self.name)

        return await self._with_retry(_do, label="chat.completions")

    async def complete_structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = "",
        temperature: float = 0.3,
        max_tokens: int = 2000,
        **kwargs: Any,
    ) -> T:
        self._validate()
        if not self.supports_json_mode:
            return await self._structured_via_text(
                prompt, schema, system=system, temperature=temperature,
                max_tokens=max_tokens, **kwargs,
            )

        sys_msg = (system + "\n\n" if system else "") + (
            "Respond ONLY with a JSON object matching the requested schema."
        )
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": self._build_messages(prompt, sys_msg),
            self._token_limit_key(): self._effective_token_limit(max_tokens),
            "response_format": {"type": "json_object"},
        }
        self._apply_sampling(payload, temperature)
        for k, v in kwargs.items():
            if k not in payload and v is not None:
                payload[k] = v

        async def _do() -> T:
            data = await self._post(payload)
            text = self._extract_text(data)
            in_tok, out_tok = self._extract_usage(data)
            self._stamp_cost(in_tok, out_tok)
            cleaned = strip_think_blocks(text, self.name)
            try:
                return schema.model_validate_json(cleaned)
            except Exception:
                # último intento: parsear como dict y volver a validar
                obj = json.loads(cleaned)
                return schema.model_validate(obj)

        try:
            return await self._with_retry(_do, label="structured")
        except LLMProviderError as e:
            logger.warning(f"[{self.name}] native JSON mode failed ({e}); falling back to text mode")
            return await self._structured_via_text(
                prompt, schema, system=system, temperature=temperature,
                max_tokens=max_tokens, **kwargs,
            )
