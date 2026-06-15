"""OpenRouter: OpenAI-compatible con headers de attribution opcionales."""
from __future__ import annotations

from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    # OpenRouter respeta response_format para los modelos que lo soportan;
    # si el modelo no, hace fallback automático.
    supports_json_mode = True

    def _build_headers(self) -> dict[str, str]:
        headers = super()._build_headers()
        # Headers de attribution opcionales (OpenRouter los recomienda).
        referer = self.extra.get("http_referer") or "https://github.com/contenido"
        title = self.extra.get("x_title") or "contenido"
        headers["HTTP-Referer"] = referer
        headers["X-Title"] = title
        return headers
