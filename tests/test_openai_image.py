"""Tests de `core/visual/generation/openai_image.py` (ADR-032).

El tier existe porque `GeminiImageGenerator` no habla con Google: llama al
modelo de imagen de Gemini **a través de OpenRouter** y exige
`OPENROUTER_API_KEY`. Un repo con solo key de OpenAI se quedaba sin rama de IA
visual y todos los beats caían a placeholder sólido.

Cero red: el transporte httpx va contra `MockTransport`.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from core.visual.generation.base import VisualGenerationError
from core.visual.generation.gemini_image import GeminiImageGenerator
from core.visual.generation.openai_image import (
    PORTRAIT_SIZE,
    OpenAIImageGenerator,
    _call_openai_image,
)
from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint, VideoSource

# =============================================================================
# Fixtures
# =============================================================================


def _png_bytes(w: int = 64, h: int = 96) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 40, 60)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def beat() -> Beat:
    return Beat(
        idx=3, role=BeatRole.MECHANISM, text="mechanism",
        target_duration_s=4.0, veo_duration=4,
    )


@pytest.fixture
def visual() -> BeatVisual:
    return BeatVisual(
        image_prompt="A surgeon in a 1950s operating theatre",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor="surgeon",
    )


class _Rec:
    def __init__(self) -> None:
        self.payloads: list[dict] = []
        self.headers: list[dict] = []


def _patched(rec: _Rec, response: httpx.Response) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        rec.payloads.append(_json.loads(request.content))
        rec.headers.append(dict(request.headers))
        return response

    real = httpx.AsyncClient

    def fake(*a: Any, **k: Any) -> httpx.AsyncClient:
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    return patch("core.visual.generation.openai_image.httpx.AsyncClient", side_effect=fake)


# =============================================================================
# Llamada HTTP
# =============================================================================


class TestCallOpenAIImage:
    @pytest.mark.asyncio
    async def test_decodes_b64_json(self) -> None:
        raw = _png_bytes()
        rec = _Rec()
        resp = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]})
        with _patched(rec, resp):
            out = await _call_openai_image(prompt="p", model="gpt-image-1", api_key="k")
        assert out == raw

    @pytest.mark.asyncio
    async def test_requests_portrait_size(self) -> None:
        """Pedir 2:3 y recortar pierde ~10%; pedir cuadrado perdería ~44%."""
        rec = _Rec()
        resp = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]})
        with _patched(rec, resp):
            await _call_openai_image(prompt="p", model="gpt-image-1", api_key="k")
        assert rec.payloads[0]["size"] == PORTRAIT_SIZE == "1024x1536"
        assert rec.payloads[0]["n"] == 1
        assert rec.payloads[0]["model"] == "gpt-image-1"

    @pytest.mark.asyncio
    async def test_sends_bearer_auth(self) -> None:
        rec = _Rec()
        resp = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_png_bytes()).decode()}]})
        with _patched(rec, resp):
            await _call_openai_image(prompt="p", model="gpt-image-1", api_key="sk-test")
        assert rec.headers[0]["authorization"] == "Bearer sk-test"

    @pytest.mark.asyncio
    async def test_http_error_raises_visual_error(self) -> None:
        rec = _Rec()
        with _patched(rec, httpx.Response(429, text="rate limited")):
            with pytest.raises(VisualGenerationError, match="429"):
                await _call_openai_image(prompt="p", model="gpt-image-1", api_key="k")

    @pytest.mark.asyncio
    async def test_empty_data_raises(self) -> None:
        rec = _Rec()
        with _patched(rec, httpx.Response(200, json={"data": []})):
            with pytest.raises(VisualGenerationError, match="sin `data`"):
                await _call_openai_image(prompt="p", model="gpt-image-1", api_key="k")

    @pytest.mark.asyncio
    async def test_neither_b64_nor_url_raises(self) -> None:
        rec = _Rec()
        with _patched(rec, httpx.Response(200, json={"data": [{"revised_prompt": "x"}]})):
            with pytest.raises(VisualGenerationError):
                await _call_openai_image(prompt="p", model="gpt-image-1", api_key="k")


# =============================================================================
# Generator
# =============================================================================


class TestOpenAIImageGenerator:
    def test_takes_key_from_the_openai_provider(self, monkeypatch) -> None:
        """Una sola credencial: la del provider que ya mueve el DAG narrativo."""
        from shared.config import load_config

        cfg = load_config()
        monkeypatch.setattr(cfg.get_llm_provider("openai"), "api_key", "sk-from-provider")
        assert OpenAIImageGenerator().api_key == "sk-from-provider"

    def test_explicit_args_win(self) -> None:
        g = OpenAIImageGenerator(model="gpt-image-2", api_key="sk-x", canvas_w=1080)
        assert (g.model, g.api_key, g.canvas_w) == ("gpt-image-2", "sk-x", 1080)

    @pytest.mark.asyncio
    async def test_without_key_raises_before_any_request(self, beat, visual, tmp_path) -> None:
        rec = _Rec()
        g = OpenAIImageGenerator(api_key="")
        with _patched(rec, httpx.Response(200, json={"data": []})):
            with pytest.raises(VisualGenerationError, match="OPENAI_API_KEY"):
                await g.generate(beat, visual, "general", tmp_path)
        assert rec.payloads == []  # no se gastó una llamada

    @pytest.mark.asyncio
    async def test_writes_cropped_9x16_frame(self, beat, visual, tmp_path) -> None:
        rec = _Rec()
        raw = _png_bytes(1024, 1536)
        resp = httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(raw).decode()}]})
        g = OpenAIImageGenerator(api_key="k", canvas_w=720)
        with _patched(rec, resp):
            art = await g.generate(beat, visual, "scientific", tmp_path)

        assert art.source == VideoSource.OPENAI_IMAGE
        assert art.idx == 3
        assert art.video_path is None  # este tier solo produce el first frame
        assert art.first_frame_path is not None
        assert Image.open(art.first_frame_path).size == (720, 1280)

    @pytest.mark.asyncio
    async def test_prompt_carries_the_style_block(self, beat, visual, tmp_path) -> None:
        """Reusa `_augment` de gemini_image: el style block no debe divergir."""
        rec = _Rec()
        resp = httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(_png_bytes(1024, 1536)).decode()}]}
        )
        g = OpenAIImageGenerator(api_key="k")
        with _patched(rec, resp):
            await g.generate(beat, visual, "scientific", tmp_path)
        sent = rec.payloads[0]["prompt"]
        assert visual.image_prompt.rstrip(".") in sent
        assert len(sent) > len(visual.image_prompt)  # se le añadió el style


# =============================================================================
# Cadena de fallback del orchestrator
# =============================================================================


class TestOrchestratorFallback:
    @pytest.mark.asyncio
    async def test_openai_takes_over_when_gemini_fails(self, beat, visual, tmp_path) -> None:
        """El caso que motivó el ADR: sin OpenRouter, antes caía a placeholder."""
        from core.visual.generation.orchestrator import _generate_first_frame_with_fallback
        from shared.schemas import BeatArtifact

        failing_gemini = MagicMock(spec=GeminiImageGenerator)
        failing_gemini.generate = AsyncMock(
            side_effect=VisualGenerationError("OPENROUTER_API_KEY no configurada")
        )

        frame = tmp_path / "oai.jpg"
        Image.new("RGB", (720, 1280), (10, 10, 10)).save(frame)
        oai = MagicMock(spec=OpenAIImageGenerator)
        oai.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=beat.idx, first_frame_path=frame, source=VideoSource.OPENAI_IMAGE
            )
        )

        path, is_real, source = await _generate_first_frame_with_fallback(
            None, None, failing_gemini, oai, beat, visual, "general", tmp_path
        )
        assert is_real is True
        assert source == VideoSource.OPENAI_IMAGE
        assert path == frame

    @pytest.mark.asyncio
    async def test_placeholder_when_both_fail(self, beat, visual, tmp_path) -> None:
        from core.visual.generation.orchestrator import _generate_first_frame_with_fallback

        failing = MagicMock(spec=GeminiImageGenerator)
        failing.generate = AsyncMock(side_effect=RuntimeError("down"))
        oai = MagicMock(spec=OpenAIImageGenerator)
        oai.generate = AsyncMock(side_effect=RuntimeError("also down"))

        path, is_real, source = await _generate_first_frame_with_fallback(
            None, None, failing, oai, beat, visual, "general", tmp_path
        )
        assert is_real is False
        assert source == VideoSource.LOCAL
        assert Path(path).exists()

    @pytest.mark.asyncio
    async def test_no_openai_tier_still_degrades_cleanly(self, beat, visual, tmp_path) -> None:
        """`None` = no había key; debe caer a placeholder sin romper."""
        from core.visual.generation.orchestrator import _generate_first_frame_with_fallback

        failing = MagicMock(spec=GeminiImageGenerator)
        failing.generate = AsyncMock(side_effect=RuntimeError("down"))

        path, is_real, source = await _generate_first_frame_with_fallback(
            None, None, failing, None, beat, visual, "general", tmp_path
        )
        assert is_real is False
        assert source == VideoSource.LOCAL

    @pytest.mark.asyncio
    async def test_gemini_still_wins_when_it_works(self, beat, visual, tmp_path) -> None:
        """El tier nuevo es fallback, no reemplazo: no cambia el orden."""
        from core.visual.generation.orchestrator import _generate_first_frame_with_fallback
        from shared.schemas import BeatArtifact

        frame = tmp_path / "gem.jpg"
        Image.new("RGB", (720, 1280), (5, 5, 5)).save(frame)
        gem = MagicMock(spec=GeminiImageGenerator)
        gem.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=beat.idx, first_frame_path=frame, source=VideoSource.GEMINI_IMAGE
            )
        )
        oai = MagicMock(spec=OpenAIImageGenerator)
        oai.generate = AsyncMock(side_effect=AssertionError("no debió llamarse"))

        _, _, source = await _generate_first_frame_with_fallback(
            None, None, gem, oai, beat, visual, "general", tmp_path
        )
        assert source == VideoSource.GEMINI_IMAGE
        oai.generate.assert_not_awaited()
