"""Drivers OpenAI-compatible (OpenAI oficial + 10 forks que comparten el contrato).

Todos heredan de `OpenAICompatibleProvider` y solo sobreescriben
`name`, `default_base_url` y banderas particulares.
"""
from __future__ import annotations

from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    default_base_url = "https://api.openai.com/v1"

    #: Familias que exigen `max_completion_tokens` en vez de `max_tokens`.
    #: Se compara por prefijo del `model_name` porque la misma key sirve
    #: modelos viejos y nuevos a la vez.
    MODERN_TOKEN_PARAM_PREFIXES = ("gpt-5", "o1", "o3", "o4")

    def _token_limit_key(self) -> str:
        model = (self.model_name or "").lower()
        if model.startswith(self.MODERN_TOKEN_PARAM_PREFIXES):
            return "max_completion_tokens"
        return "max_tokens"

    def _supports_temperature(self) -> bool:
        return not (self.model_name or "").lower().startswith(
            self.MODERN_TOKEN_PARAM_PREFIXES
        )

    #: Piso de tokens para modelos de razonamiento. Medido: con 2000 los
    #: prompts narrativos largos agotan el presupuesto razonando y devuelven
    #: content vacío con finish_reason="length". No encarece las llamadas que
    #: no lo necesitan — solo se factura lo que el modelo realmente genera.
    REASONING_MIN_TOKENS = 8000

    def _min_token_budget(self) -> int:
        if (self.model_name or "").lower().startswith(self.MODERN_TOKEN_PARAM_PREFIXES):
            return self.REASONING_MIN_TOKENS
        return 0


class MoonshotProvider(OpenAICompatibleProvider):
    name = "moonshot"
    default_base_url = "https://api.moonshot.cn/v1"


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"
    default_base_url = "http://localhost:11434/v1"
    requires_api_key = False
    # Ollama implementa response_format desde 0.5; lo dejamos activo.
    supports_json_mode = True


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    default_base_url = "https://api.deepseek.com"
    # DeepSeek R1 inyecta <think>; el cleaner del base se encarga.


class MiMoProvider(OpenAICompatibleProvider):
    name = "mimo"
    default_base_url = "https://api.xiaomimimo.com/v1"


class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    default_base_url = "https://api.groq.com/openai/v1"


class GrokProvider(OpenAICompatibleProvider):
    name = "grok"
    default_base_url = "https://api.x.ai/v1"


class OneAPIProvider(OpenAICompatibleProvider):
    name = "oneapi"
    # OneAPI requiere base_url custom siempre — no hay default razonable.
    default_base_url = ""


class AIHubMixProvider(OpenAICompatibleProvider):
    name = "aihubmix"
    default_base_url = "https://aihubmix.com/v1"


class AIMLProvider(OpenAICompatibleProvider):
    name = "aimlapi"
    default_base_url = "https://api.aimlapi.com/v1"


class MiniMaxProvider(OpenAICompatibleProvider):
    name = "minimax"
    default_base_url = "https://api.minimax.io/v1"
    # MiniMax M3 también inyecta <think>; cleaner se encarga.


class ModelScopeProvider(OpenAICompatibleProvider):
    name = "modelscope"
    default_base_url = "https://api-inference.modelscope.cn/v1/"
    # ModelScope a veces no respeta response_format; preferimos fallback texto.
    supports_json_mode = False
