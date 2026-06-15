"""Azure OpenAI: contrato OpenAI pero URL/auth distintos.

Azure usa:
  POST {endpoint}/openai/deployments/{deployment}/chat/completions?api-version={ver}
  Header: api-key: {key}  (NO Bearer)
"""
from __future__ import annotations

from typing import Any

from core.llm_router.base import LLMProviderError
from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider


class AzureProvider(OpenAICompatibleProvider):
    name = "azure"
    requires_api_key = True
    supports_json_mode = True
    default_api_version = "2024-02-15-preview"

    def __init__(self, *args: Any, api_version: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_version = api_version or self.extra.get("api_version") or self.default_api_version

    def _build_url(self) -> str:
        if not self.base_url:
            raise LLMProviderError("[azure] base_url (azure_endpoint) is required")
        endpoint = self.base_url.rstrip("/")
        # `model_name` aquí se interpreta como deployment name.
        return (
            f"{endpoint}/openai/deployments/{self.model_name}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _build_headers(self) -> dict[str, str]:
        # Azure usa header `api-key` en vez de `Authorization: Bearer`.
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }
