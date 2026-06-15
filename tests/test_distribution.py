"""Tests del subsistema de distribución (Upload-Post).

Sin llamadas de red — todo httpx mockeado vía `MockTransport`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from core.distribution import (
    SUPPORTED_PLATFORMS,
    DistributionService,
    UploadPostService,
    UploadResult,
    UploadStatus,
    get_distribution_service,
)
from shared.config import Config, UploadPostConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _CallRecorder:
    """Captura cada request hecho por httpx para inspección post-hoc."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []


def _make_handler(
    recorder: _CallRecorder,
    *,
    responses: list[httpx.Response] | None = None,
    response: httpx.Response | None = None,
) -> Any:
    """Devuelve un handler para httpx.MockTransport.

    Si pasas `responses`, devuelve la siguiente cada vez (para simular retries).
    Si pasas `response`, devuelve esa siempre.
    """
    seq = list(responses) if responses else None

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "content": request.content,
            }
        )
        if seq is not None:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        assert response is not None
        return response

    return handler


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    """Crea un archivo de video fake (sólo bytes) para el upload."""
    p = tmp_path / "reel.mp4"
    p.write_bytes(b"FAKE_MP4_BYTES" * 100)
    return p


def _patch_async_client(handler: Any) -> Any:
    """Patch para que `httpx.AsyncClient(...)` use nuestro MockTransport."""
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    return patch(
        "core.distribution.upload_post.httpx.AsyncClient",
        side_effect=fake_async_client,
    )


# ===========================================================================
# Test 1: Upload exitoso multi-platform
# ===========================================================================

@pytest.mark.asyncio
async def test_upload_video_success_multi_platform(fake_video: Path) -> None:
    recorder = _CallRecorder()
    body = {
        "success": True,
        "request_id": "req-abc-123",
        "results": {
            "tiktok": {"url": "https://tiktok.com/@u/video/1"},
            "instagram": {"url": "https://instagram.com/reel/xyz"},
        },
    }
    handler = _make_handler(recorder, response=httpx.Response(200, json=body))

    with _patch_async_client(handler):
        svc = UploadPostService(api_key="sk_live_xxxxYYYY", max_retries=1)
        result = await svc.upload_video(
            video_path=fake_video,
            platforms=["tiktok", "instagram"],
            caption="Probando reel",
            hashtags=["ai", "reels"],
            metadata={"privacy_level": "PUBLIC_TO_EVERYONE"},
        )

    assert isinstance(result, UploadResult)
    assert result.success is True
    assert result.request_id == "req-abc-123"
    assert result.urls == {
        "tiktok": "https://tiktok.com/@u/video/1",
        "instagram": "https://instagram.com/reel/xyz",
    }
    assert result.errors == {}
    assert result.platforms == ["tiktok", "instagram"]

    # 1 sola llamada multipart con ambas plataformas
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/api/upload_video")
    # API key se envía como Apikey, NO loguead (lo verificamos a nivel header)
    assert call["headers"]["authorization"] == "Apikey sk_live_xxxxYYYY"
    # Caption + hashtags se concatenan, ambas plataformas en data multipart
    body_bytes = call["content"]
    assert b"#ai" in body_bytes and b"#reels" in body_bytes
    assert b"tiktok" in body_bytes and b"instagram" in body_bytes

    # model_dump() debe ser un dict serializable (para TaskInfo.cross_post_results)
    dumped = result.model_dump()
    assert isinstance(dumped, dict)
    assert json.dumps(dumped)  # serializable


# ===========================================================================
# Test 2: Manejo de error 401 (auth fallida; no retry, devuelve UploadResult)
# ===========================================================================

@pytest.mark.asyncio
async def test_upload_video_handles_401(fake_video: Path) -> None:
    recorder = _CallRecorder()
    body = {"success": False, "message": "Invalid API key"}
    # 401 NO está en RETRYABLE_STATUS -> se levanta HTTPStatusError directo
    handler = _make_handler(recorder, response=httpx.Response(401, json=body))

    with _patch_async_client(handler):
        svc = UploadPostService(api_key="bad_key", max_retries=3)
        result = await svc.upload_video(
            video_path=fake_video,
            platforms=["tiktok"],
            caption="x",
        )

    assert result.success is False
    assert result.request_id is None
    # El error 401 quedó capturado, NO crasheó
    assert "_http" in result.errors
    assert "401" in result.errors["_http"]
    # Una sola llamada (no es retryable)
    assert len(recorder.calls) == 1


# ===========================================================================
# Test 3: Retry exponencial en 503 -> finalmente éxito
# ===========================================================================

@pytest.mark.asyncio
async def test_upload_video_retries_then_succeeds(
    fake_video: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _CallRecorder()
    success_body = {
        "success": True,
        "request_id": "req-after-retry",
        "results": {"tiktok": {"url": "https://tiktok.com/x"}},
    }
    handler = _make_handler(
        recorder,
        responses=[
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(200, json=success_body),
        ],
    )

    # Acelerar tenacity wait_exponential a 0 para que el test no demore
    import tenacity

    monkeypatch.setattr(
        tenacity, "wait_exponential", lambda *a, **kw: tenacity.wait_none()
    )

    with _patch_async_client(handler):
        svc = UploadPostService(api_key="k", max_retries=3)
        result = await svc.upload_video(
            video_path=fake_video,
            platforms=["tiktok"],
            caption="retry test",
        )

    assert result.success is True
    assert result.request_id == "req-after-retry"
    # 3 intentos = 3 calls
    assert len(recorder.calls) == 3


# ===========================================================================
# Test 4: Factory devuelve None sin api_key
# ===========================================================================

def test_factory_returns_none_without_api_key() -> None:
    cfg = Config(upload_post=UploadPostConfig(api_key=""))
    assert get_distribution_service(cfg) is None

    # api_key con solo espacios también -> None
    cfg2 = Config(upload_post=UploadPostConfig(api_key="   "))
    assert get_distribution_service(cfg2) is None


def test_factory_returns_service_with_api_key() -> None:
    cfg = Config(
        upload_post=UploadPostConfig(
            api_key="sk_test_key",
            auto_upload=True,
            platforms=["tiktok", "instagram"],
        )
    )
    svc = get_distribution_service(cfg)
    assert svc is not None
    assert isinstance(svc, DistributionService)
    assert isinstance(svc, UploadPostService)


# ===========================================================================
# Test 5: check_status parsea el estado correctamente
# ===========================================================================

@pytest.mark.asyncio
async def test_check_status_parses_states(fake_video: Path) -> None:
    cases = [
        ({"state": "completed", "urls": {"tiktok": "https://t.co/x"}}, "complete"),
        ({"state": "processing"}, "uploading"),
        ({"state": "failed", "error": "bad"}, "failed"),
        ({"status": "pending"}, "pending"),
        ({"state": "unknown_value"}, "pending"),
    ]

    for payload, expected in cases:
        recorder = _CallRecorder()
        handler = _make_handler(recorder, response=httpx.Response(200, json=payload))
        with _patch_async_client(handler):
            svc = UploadPostService(api_key="k")
            status = await svc.check_status("req-XYZ")

        assert isinstance(status, UploadStatus)
        assert status.request_id == "req-XYZ"
        assert status.state == expected
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["method"] == "GET"
        assert recorder.calls[0]["url"].endswith("/api/status/req-XYZ")

    # Caso especial: urls se preservan en "complete"
    recorder = _CallRecorder()
    handler = _make_handler(
        recorder,
        response=httpx.Response(
            200,
            json={"state": "completed", "urls": {"tiktok": "https://tiktok/y"}},
        ),
    )
    with _patch_async_client(handler):
        svc = UploadPostService(api_key="k")
        status = await svc.check_status("rid")
    assert status.state == "complete"
    assert status.urls == {"tiktok": "https://tiktok/y"}


# ===========================================================================
# Tests extra de robustez
# ===========================================================================

@pytest.mark.asyncio
async def test_upload_video_missing_file(tmp_path: Path) -> None:
    """Si el video no existe, devuelve UploadResult(success=False) sin llamar API."""
    recorder = _CallRecorder()
    handler = _make_handler(recorder, response=httpx.Response(200, json={"success": True}))
    with _patch_async_client(handler):
        svc = UploadPostService(api_key="k")
        result = await svc.upload_video(
            video_path=tmp_path / "nope.mp4",
            platforms=["tiktok"],
        )
    assert result.success is False
    assert "_validation" in result.errors
    assert "not found" in result.errors["_validation"].lower()
    assert recorder.calls == []  # ninguna llamada de red


@pytest.mark.asyncio
async def test_upload_video_invalid_platform(fake_video: Path) -> None:
    """Plataforma no soportada -> error sin llamar API."""
    recorder = _CallRecorder()
    handler = _make_handler(recorder, response=httpx.Response(200, json={"success": True}))
    with _patch_async_client(handler):
        svc = UploadPostService(api_key="k")
        result = await svc.upload_video(
            video_path=fake_video,
            platforms=["youtube_shorts"],  # roadmap, no soportada hoy
        )
    assert result.success is False
    assert "youtube_shorts" in result.errors
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_upload_post_init_requires_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        UploadPostService(api_key="")


def test_supported_platforms_constant() -> None:
    """Sanity: tiktok e instagram están soportados; youtube_shorts no."""
    assert "tiktok" in SUPPORTED_PLATFORMS
    assert "instagram" in SUPPORTED_PLATFORMS
    assert "youtube_shorts" not in SUPPORTED_PLATFORMS


def test_upload_result_dump_compatible_with_task_info() -> None:
    """UploadResult.model_dump() debe ser un dict válido para TaskInfo.cross_post_results."""
    from shared.schemas import GenerationMode, TaskInfo, TaskState

    r = UploadResult(
        success=True,
        request_id="rid",
        urls={"tiktok": "https://t.co/x"},
        platforms=["tiktok"],
    )
    info = TaskInfo(
        task_id="t",
        state=TaskState.COMPLETE,
        mode=GenerationMode.PREMIUM,
        cross_post_results=[r.model_dump()],
    )
    assert info.cross_post_results[0]["success"] is True
    assert info.cross_post_results[0]["urls"]["tiktok"] == "https://t.co/x"
