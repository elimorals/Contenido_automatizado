"""Drivers OpenAI-compatible (OpenAI oficial + 10 forks que comparten el contrato).

Todos heredan de `OpenAICompatibleProvider` y solo sobreescriben
`name`, `default_base_url` y banderas particulares.
"""
from __future__ import annotations

from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    default_base_url = "https://api.openai.com/v1"


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
