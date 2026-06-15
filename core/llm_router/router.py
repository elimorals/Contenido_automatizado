"""Router: nombre → instancia configurada.

Lee `shared.config.load_config().llm[name]` y devuelve un `LLMProvider`
ya configurado. Cachea por (provider_name, api_key, model_name, base_url)
para no recrear clientes httpx en cada llamada.
"""
from __future__ import annotations

from threading import Lock
from typing import Any

from loguru import logger

from core.llm_router.base import LLMProvider, LLMProviderError
from core.llm_router.providers.anthropic import AnthropicProvider
from core.llm_router.providers.azure import AzureProvider
from core.llm_router.providers.gemini import GeminiProvider
from core.llm_router.providers.litellm import LiteLLMProvider
from core.llm_router.providers.openai import (
    AIHubMixProvider,
    AIMLProvider,
    DeepSeekProvider,
    GrokProvider,
    GroqProvider,
    MiMoProvider,
    MiniMaxProvider,
    ModelScopeProvider,
    MoonshotProvider,
    OllamaProvider,
    OneAPIProvider,
    OpenAIProvider,
)
from core.llm_router.providers.openrouter import OpenRouterProvider
from core.llm_router.providers.qwen import QwenProvider

# Registry: nombre canónico → clase. Si el usuario configura un provider
# fuera de esta lista, cae a LiteLLM como universal fallback.
PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "openai": OpenAIProvider,
    "azure": AzureProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "qwen": QwenProvider,
    "moonshot": MoonshotProvider,
    "ollama": OllamaProvider,
    "deepseek": DeepSeekProvider,
    "mimo": MiMoProvider,
    "groq": GroqProvider,
    "grok": GrokProvider,
    "oneapi": OneAPIProvider,
    "aihubmix": AIHubMixProvider,
    "aimlapi": AIMLProvider,
    "minimax": MiniMaxProvider,
    "modelscope": ModelScopeProvider,
    "openrouter": OpenRouterProvider,
    "litellm": LiteLLMProvider,
}

_instance_cache: dict[tuple, LLMProvider] = {}
_cache_lock = Lock()


def detect_provider_from_model(model: str) -> str | None:
    """Heurística: deduce el provider del nombre del modelo.

    - `openrouter/...` → openrouter
    - `claude-*`, `anthropic/...` → anthropic
    - `gemini-*`, `google/...` → gemini
    - `gpt-*`, `o1-*`, `o3-*` → openai
    - `qwen-*` → qwen
    - `deepseek-*` → deepseek
    - `claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-7` → anthropic
    """
    if not model:
        return None
    m = model.lower()
    if m.startswith("openrouter/"):
        return "openrouter"
    if m.startswith("anthropic/") or m.startswith("claude-") or "claude-" in m:
        return "anthropic"
    if m.startswith("google/") or m.startswith("gemini-"):
        return "gemini"
    if m.startswith(("gpt-", "o1-", "o3-", "o4-", "chatgpt-")):
        return "openai"
    if m.startswith("qwen-") or m.startswith("qwen2") or m.startswith("qwen3"):
        return "qwen"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith("llama-") or m.startswith("mixtral"):
        return "groq"
    if m.startswith("grok-"):
        return "grok"
    return None


def _load_config_safe() -> Any:
    """Carga config; si shared.config falla, devuelve None (test-friendly)."""
    try:
        from shared.config import load_config
        return load_config()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"shared.config unavailable: {e}")
        return None


def get_provider(
    name: str,
    *,
    api_key: str | None = None,
    model_name: str | None = None,
    base_url: str | None = None,
    overrides: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> LLMProvider:
    """Devuelve un `LLMProvider` configurado para `name`.

    Resolución:
      1. `overrides` (kwargs explícitos del caller, máxima prioridad)
      2. `shared.config.load_config().llm[name]`
      3. Defaults de la clase del provider

    Si `name` no está en `PROVIDER_REGISTRY`, intenta deducir por modelo;
    si tampoco, cae a `LiteLLMProvider` (universal proxy).
    """
    if not name:
        raise LLMProviderError("get_provider: name is required")

    # 1. Resolver clase
    provider_cls = PROVIDER_REGISTRY.get(name)
    if provider_cls is None:
        # Tal vez `name` es realmente un modelo (ej. "claude-sonnet-4-6").
        detected = detect_provider_from_model(name)
        if detected and detected in PROVIDER_REGISTRY:
            logger.info(f"resolved model name '{name}' → provider '{detected}'")
            provider_cls = PROVIDER_REGISTRY[detected]
            # Usamos el name original como model_name si no se pasó otro.
            if model_name is None:
                model_name = name
            name = detected
        else:
            logger.info(f"unknown provider '{name}', falling back to litellm")
            provider_cls = LiteLLMProvider
            if model_name is None:
                model_name = name
            name = "litellm"

    # 2. Cargar config (puede ser None en tests sin pyproject)
    cfg = _load_config_safe()
    provider_cfg = None
    if cfg is not None:
        provider_cfg = cfg.llm.get(name)

    resolved_api_key = api_key
    resolved_model = model_name
    resolved_base_url = base_url
    extra_kwargs: dict[str, Any] = {}

    if provider_cfg is not None:
        if resolved_api_key is None:
            resolved_api_key = provider_cfg.api_key
        if resolved_model is None:
            resolved_model = provider_cfg.model_name or provider_cfg.default_model
        if resolved_base_url is None:
            resolved_base_url = provider_cfg.base_url
        if provider_cfg.api_version:
            extra_kwargs["api_version"] = provider_cfg.api_version

    if overrides:
        extra_kwargs.update(overrides)

    # 3. Cache key
    cache_key = (
        name,
        resolved_api_key or "",
        resolved_model or "",
        resolved_base_url or "",
        tuple(sorted(extra_kwargs.items())),
    )
    if use_cache:
        with _cache_lock:
            cached = _instance_cache.get(cache_key)
            if cached is not None:
                return cached

    instance = provider_cls(
        api_key=resolved_api_key or "",
        model_name=resolved_model or "",
        base_url=resolved_base_url or "",
        **extra_kwargs,
    )

    if use_cache:
        with _cache_lock:
            _instance_cache[cache_key] = instance

    return instance


def clear_cache() -> None:
    """Vacía cache de instancias (útil en tests o tras reload de config)."""
    with _cache_lock:
        _instance_cache.clear()


def available_providers() -> list[str]:
    """Lista de provider names registrados."""
    return sorted(PROVIDER_REGISTRY.keys())
