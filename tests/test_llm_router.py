"""Tests del LLM router. Sin llamadas de red — todo mock de httpx/SDKs."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from core.llm_router import (
    LLMProvider,
    LLMProviderError,
    available_providers,
    clear_cache,
    detect_provider_from_model,
    get_provider,
    strip_think_blocks,
)
from core.llm_router.providers.anthropic import AnthropicProvider
from core.llm_router.providers.azure import AzureProvider
from core.llm_router.providers.gemini import GeminiProvider
from core.llm_router.providers.openai import (
    DeepSeekProvider,
    GroqProvider,
    OpenAIProvider,
)
from core.llm_router.providers.openai_compatible import OpenAICompatibleProvider
from core.llm_router.providers.openrouter import OpenRouterProvider


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# 1. La clase base abstracta no se puede instanciar
# ---------------------------------------------------------------------------
def test_llm_provider_abstract_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        LLMProvider(api_key="x", model_name="m")  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 2. strip_think_blocks limpia DeepSeek/MiniMax <think> y casos borde
# ---------------------------------------------------------------------------
class TestThinkBlocks:
    def test_strips_closed_think_block(self) -> None:
        raw = "<think>razonando...</think>Respuesta final"
        assert strip_think_blocks(raw, "test") == "Respuesta final"

    def test_strips_unclosed_think_block(self) -> None:
        raw = "Respuesta final<think>razonando truncado"
        assert strip_think_blocks(raw, "test") == "Respuesta final"

    def test_multiline_think_block(self) -> None:
        raw = "<think>\nlínea1\nlínea2\n</think>\n\nResultado"
        assert strip_think_blocks(raw, "test") == "Resultado"

    def test_no_think_block_passthrough(self) -> None:
        assert strip_think_blocks("Hola mundo", "test") == "Hola mundo"

    def test_none_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            strip_think_blocks(None, "test")

    def test_non_string_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            strip_think_blocks(123, "test")  # type: ignore[arg-type]

    def test_only_think_block_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            strip_think_blocks("<think>solo pensamiento</think>", "test")


# ---------------------------------------------------------------------------
# 3. get_provider devuelve la clase correcta
# ---------------------------------------------------------------------------
class TestGetProvider:
    def test_returns_openai_for_openai_name(self) -> None:
        p = get_provider("openai", api_key="sk-test", model_name="gpt-4o-mini")
        assert isinstance(p, OpenAIProvider)
        assert p.name == "openai"
        assert p.model_name == "gpt-4o-mini"

    def test_returns_anthropic(self) -> None:
        p = get_provider("anthropic", api_key="sk-ant", model_name="claude-sonnet-4-6")
        assert isinstance(p, AnthropicProvider)

    def test_returns_azure(self) -> None:
        p = get_provider("azure", api_key="k", model_name="my-deploy", base_url="https://x.openai.azure.com")
        assert isinstance(p, AzureProvider)
        assert p.api_version  # default aplicado

    def test_returns_gemini(self) -> None:
        p = get_provider("gemini", api_key="k", model_name="gemini-2.5-flash")
        assert isinstance(p, GeminiProvider)

    def test_returns_openrouter(self) -> None:
        p = get_provider("openrouter", api_key="k", model_name="anthropic/claude-3.5-sonnet")
        assert isinstance(p, OpenRouterProvider)

    def test_unknown_name_falls_back_to_litellm(self) -> None:
        p = get_provider("cohere-foo-bar")
        # Cae a LiteLLM como universal proxy
        assert p.name == "litellm"
        assert p.model_name == "cohere-foo-bar"

    def test_instances_are_cached(self) -> None:
        p1 = get_provider("openai", api_key="sk", model_name="gpt-4o-mini")
        p2 = get_provider("openai", api_key="sk", model_name="gpt-4o-mini")
        assert p1 is p2  # mismo objeto desde cache

    def test_different_keys_yield_different_instances(self) -> None:
        p1 = get_provider("openai", api_key="a", model_name="gpt-4o-mini")
        p2 = get_provider("openai", api_key="b", model_name="gpt-4o-mini")
        assert p1 is not p2

    def test_available_providers_includes_required(self) -> None:
        provs = available_providers()
        for required in ("openai", "anthropic", "azure", "gemini", "openrouter", "litellm", "qwen"):
            assert required in provs


# ---------------------------------------------------------------------------
# 4. detect_provider_from_model heurística
# ---------------------------------------------------------------------------
class TestDetectProvider:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-4o-mini", "openai"),
            ("o3-mini", "openai"),
            ("claude-sonnet-4-6", "anthropic"),
            ("anthropic/claude-3.5-sonnet", "anthropic"),
            ("gemini-2.5-flash", "gemini"),
            ("google/gemini-1.5-pro", "gemini"),
            ("openrouter/anthropic/claude-3-haiku", "openrouter"),
            ("qwen-max", "qwen"),
            ("deepseek-chat", "deepseek"),
            ("grok-3-beta", "grok"),
            ("", None),
            ("totally-unknown-model", None),
        ],
    )
    def test_detection(self, model: str, expected: str | None) -> None:
        assert detect_provider_from_model(model) == expected


# ---------------------------------------------------------------------------
# 5. OpenAI-compatible: URL building, headers, parsing
# ---------------------------------------------------------------------------
class TestOpenAICompatible:
    def test_url_appended_when_base_lacks_path(self) -> None:
        p = OpenAIProvider(api_key="k", model_name="gpt-4o-mini")
        assert p._build_url() == "https://api.openai.com/v1/chat/completions"

    def test_url_preserved_when_already_full(self) -> None:
        p = OpenAICompatibleProvider(
            api_key="k",
            model_name="x",
            base_url="https://text.pollinations.ai/openai/chat/completions",
        )
        assert p._build_url().endswith("/chat/completions")
        assert p._build_url().count("/chat/completions") == 1

    def test_headers_authorization_bearer(self) -> None:
        p = OpenAIProvider(api_key="sk-abc", model_name="gpt-4o-mini")
        h = p._build_headers()
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["Content-Type"] == "application/json"

    def test_validate_requires_api_key(self) -> None:
        p = OpenAIProvider(api_key="", model_name="gpt-4o-mini")
        with pytest.raises(LLMProviderError, match="api_key"):
            p._validate()

    def test_validate_requires_model(self) -> None:
        p = OpenAIProvider(api_key="sk", model_name="")
        with pytest.raises(LLMProviderError, match="model_name"):
            p._validate()

    def test_extract_text_happy_path(self) -> None:
        data = {"choices": [{"message": {"content": "hola"}}]}
        assert OpenAICompatibleProvider._extract_text(data) == "hola"

    def test_extract_text_missing_choices(self) -> None:
        with pytest.raises(LLMProviderError, match="choices"):
            OpenAICompatibleProvider._extract_text({})

    def test_extract_text_missing_content(self) -> None:
        with pytest.raises(LLMProviderError, match="content"):
            OpenAICompatibleProvider._extract_text({"choices": [{"message": {}}]})


# ---------------------------------------------------------------------------
# 6. complete() end-to-end con httpx mockeado
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Doble de httpx.AsyncClient como async context manager."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_call: dict[str, Any] | None = None

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self.last_call = {"url": url, "headers": headers, "json": json}
        return self._response


@pytest.mark.asyncio
async def test_openai_complete_happy_path() -> None:
    response = _FakeResponse(
        200,
        {
            "choices": [
                {"message": {"content": "<think>razonando</think>respuesta limpia"}}
            ]
        },
    )
    fake_client = _FakeAsyncClient(response)

    with patch("core.llm_router.providers.openai_compatible.httpx.AsyncClient", return_value=fake_client):
        p = OpenAIProvider(api_key="sk-test", model_name="gpt-4o-mini", max_retries=1)
        out = await p.complete("hola", temperature=0.5, max_tokens=100)

    # think block fue removido
    assert out == "respuesta limpia"
    # request body fue armado bien
    assert fake_client.last_call is not None
    body = fake_client.last_call["json"]
    assert body["model"] == "gpt-4o-mini"
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 100
    assert body["messages"][-1] == {"role": "user", "content": "hola"}
    # bearer token
    assert fake_client.last_call["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_deepseek_strips_think_blocks() -> None:
    """DeepSeek R1 incrusta <think>; el provider debe limpiarlo."""
    response = _FakeResponse(
        200,
        {"choices": [{"message": {"content": "<think>cálculo interno...</think>42"}}]},
    )
    with patch(
        "core.llm_router.providers.openai_compatible.httpx.AsyncClient",
        return_value=_FakeAsyncClient(response),
    ):
        p = DeepSeekProvider(api_key="k", model_name="deepseek-chat", max_retries=1)
        assert await p.complete("¿2+2?") == "42"


@pytest.mark.asyncio
async def test_groq_uses_default_base_url() -> None:
    response = _FakeResponse(200, {"choices": [{"message": {"content": "ok"}}]})
    fake = _FakeAsyncClient(response)
    with patch(
        "core.llm_router.providers.openai_compatible.httpx.AsyncClient",
        return_value=fake,
    ):
        p = GroqProvider(api_key="k", model_name="llama-3.3-70b", max_retries=1)
        await p.complete("hola")
    assert fake.last_call is not None
    assert fake.last_call["url"].startswith("https://api.groq.com/openai/v1/")


@pytest.mark.asyncio
async def test_http_error_raises_after_retries() -> None:
    response = _FakeResponse(500, text="server boom")
    with patch(
        "core.llm_router.providers.openai_compatible.httpx.AsyncClient",
        return_value=_FakeAsyncClient(response),
    ):
        p = OpenAIProvider(api_key="k", model_name="gpt-4o-mini", max_retries=2)
        with pytest.raises(LLMProviderError, match="failed after"):
            await p.complete("hola")


# ---------------------------------------------------------------------------
# 7. Azure URL y headers (api-key, no Bearer)
# ---------------------------------------------------------------------------
def test_azure_url_includes_deployment_and_api_version() -> None:
    p = AzureProvider(
        api_key="k",
        model_name="my-gpt4-deploy",
        base_url="https://my-resource.openai.azure.com",
    )
    url = p._build_url()
    assert "openai/deployments/my-gpt4-deploy/chat/completions" in url
    assert "api-version=" in url


def test_azure_uses_api_key_header_not_bearer() -> None:
    p = AzureProvider(
        api_key="secret",
        model_name="dep",
        base_url="https://x.openai.azure.com",
    )
    h = p._build_headers()
    assert h["api-key"] == "secret"
    assert "Authorization" not in h


def test_azure_requires_base_url() -> None:
    p = AzureProvider(api_key="k", model_name="dep", base_url="")
    with pytest.raises(LLMProviderError, match="base_url"):
        p._build_url()


# ---------------------------------------------------------------------------
# 8. Structured output via text fallback
# ---------------------------------------------------------------------------
class _DummySchema(BaseModel):
    name: str
    score: int


@pytest.mark.asyncio
async def test_structured_via_text_parses_json() -> None:
    response = _FakeResponse(
        200,
        {"choices": [{"message": {"content": '{"name": "ada", "score": 99}'}}]},
    )
    with patch(
        "core.llm_router.providers.openai_compatible.httpx.AsyncClient",
        return_value=_FakeAsyncClient(response),
    ):
        p = OpenAIProvider(api_key="k", model_name="gpt-4o-mini", max_retries=1)
        result = await p.complete_structured("dame algo", schema=_DummySchema)
    assert isinstance(result, _DummySchema)
    assert result.name == "ada"
    assert result.score == 99


@pytest.mark.asyncio
async def test_structured_strips_markdown_fences() -> None:
    """Algunos modelos envuelven el JSON en ```json ... ```."""
    fenced = "```json\n{\"name\": \"ada\", \"score\": 1}\n```"
    response = _FakeResponse(200, {"choices": [{"message": {"content": fenced}}]})
    # Forzamos fallback texto desactivando JSON-mode nativo.
    with patch(
        "core.llm_router.providers.openai_compatible.httpx.AsyncClient",
        return_value=_FakeAsyncClient(response),
    ):
        p = OpenAIProvider(api_key="k", model_name="gpt-4o-mini", max_retries=1)
        # Llamamos directamente al fallback texto.
        result = await p._structured_via_text("x", _DummySchema)
    assert result.score == 1


# ---------------------------------------------------------------------------
# 9. OpenRouter inyecta headers de attribution
# ---------------------------------------------------------------------------
def test_openrouter_attribution_headers() -> None:
    p = OpenRouterProvider(api_key="sk-or", model_name="anthropic/claude-3.5-sonnet")
    h = p._build_headers()
    assert "HTTP-Referer" in h
    assert "X-Title" in h
    assert h["Authorization"] == "Bearer sk-or"
