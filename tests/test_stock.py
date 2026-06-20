"""Tests para los providers de stock footage (Pexels, Pixabay, Coverr)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.visual.stock.base import (
    AllKeysExhaustedError,
    APIKeyRotator,
    NoAPIKeyError,
)
from core.visual.stock.cache import fetch_and_cache, probe_video
from core.visual.stock.coverr import CoverrProvider
from core.visual.stock.pexels import PexelsProvider
from core.visual.stock.pixabay import PixabayProvider
from core.visual.stock.registry import (
    available_providers,
    get_all_providers,
    get_provider,
)
from shared.schemas import VideoAspect, VideoSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAsyncClient:
    """AsyncClient mockeable que devuelve respuestas pre-canned por URL."""

    def __init__(self, responses: dict[str, Any] | list[Any]):
        # responses puede ser dict por host o lista secuencial
        self._responses = responses
        self._calls = 0

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        if isinstance(self._responses, list):
            r = self._responses[min(self._calls, len(self._responses) - 1)]
        else:
            # dict[host_substring] = Response
            r = next(
                (v for k, v in self._responses.items() if k in url),
                next(iter(self._responses.values())),
            )
        self._calls += 1
        return r


def _resp(status: int, payload: dict[str, Any]) -> httpx.Response:
    req = httpx.Request("GET", "https://example.com")
    return httpx.Response(status_code=status, json=payload, request=req)


# ---------------------------------------------------------------------------
# 1. search() devuelve lista válida (Pexels, Pixabay, Coverr)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pexels_search_returns_valid_materials() -> None:
    payload = {
        "videos": [
            {
                "duration": 10,
                "video_files": [
                    {"width": 1080, "height": 1920, "link": "https://pexels.example/vid1.mp4"},
                    {"width": 640, "height": 360, "link": "https://pexels.example/vid1-low.mp4"},
                ],
            },
            {
                # demasiado corto → debe filtrarse
                "duration": 1,
                "video_files": [
                    {"width": 1080, "height": 1920, "link": "https://pexels.example/short.mp4"},
                ],
            },
        ]
    }
    fake = _FakeAsyncClient([_resp(200, payload)])
    provider = PexelsProvider(api_keys=["KEY-1"])
    with patch("core.visual.stock.pexels.httpx.AsyncClient", return_value=fake):
        items = await provider.search("ocean", VideoAspect.PORTRAIT, min_duration_s=3.0)
    assert len(items) == 1
    assert items[0].provider is VideoSource.PEXELS
    assert items[0].url == "https://pexels.example/vid1.mp4"
    assert items[0].width == 1080 and items[0].height == 1920


@pytest.mark.asyncio
async def test_pixabay_search_flexible_width() -> None:
    payload = {
        "hits": [
            {
                "duration": 8,
                "videos": {
                    "large": {"width": 1920, "height": 1080, "url": "https://px.example/large.mp4"},
                    "medium": {"width": 1280, "height": 720, "url": "https://px.example/medium.mp4"},
                },
            }
        ]
    }
    fake = _FakeAsyncClient([_resp(200, payload)])
    provider = PixabayProvider(api_keys=["KEY-A"])
    with patch("core.visual.stock.pixabay.httpx.AsyncClient", return_value=fake):
        items = await provider.search("forest", VideoAspect.LANDSCAPE, min_duration_s=3.0)
    assert len(items) == 1
    assert items[0].provider is VideoSource.PIXABAY
    # large es el primer formato cuyo width >= 1920
    assert items[0].url == "https://px.example/large.mp4"


@pytest.mark.asyncio
async def test_coverr_search_accepts_string_duration() -> None:
    payload = {
        "hits": [
            {
                "id": "abc123",
                "duration": "10.500000",
                "urls": {"mp4_download": "https://coverr.example/abc.mp4?token=jwt"},
                "width": 1920,
                "height": 1080,
            },
            {
                # sin id → debe descartarse
                "duration": 12,
                "urls": {"mp4_download": "https://coverr.example/nope.mp4"},
            },
        ]
    }
    fake = _FakeAsyncClient([_resp(200, payload)])
    provider = CoverrProvider(api_keys=["BEARER"])
    with patch("core.visual.stock.coverr.httpx.AsyncClient", return_value=fake):
        items = await provider.search("city", VideoAspect.LANDSCAPE, min_duration_s=3.0)
    assert len(items) == 1
    assert items[0].provider is VideoSource.COVERR
    assert "jwt" in items[0].url


# ---------------------------------------------------------------------------
# 2. Rotación de API keys (401 → siguiente; todas fallan → exception)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pexels_rotates_on_401() -> None:
    # primera key → 401, segunda key → éxito
    success_payload = {
        "videos": [
            {
                "duration": 6,
                "video_files": [
                    {"width": 1080, "height": 1920, "link": "https://ok.example/v.mp4"}
                ],
            }
        ]
    }
    fake = _FakeAsyncClient([_resp(401, {"error": "unauthorized"}), _resp(200, success_payload)])
    provider = PexelsProvider(api_keys=["BAD", "GOOD"])
    with patch("core.visual.stock.pexels.httpx.AsyncClient", return_value=fake):
        items = await provider.search("ocean", VideoAspect.PORTRAIT)
    assert len(items) == 1
    assert items[0].url == "https://ok.example/v.mp4"


@pytest.mark.asyncio
async def test_pexels_all_keys_exhausted_raises() -> None:
    fake = _FakeAsyncClient([_resp(401, {}), _resp(429, {})])
    provider = PexelsProvider(api_keys=["BAD-1", "BAD-2"])
    with patch("core.visual.stock.pexels.httpx.AsyncClient", return_value=fake):
        with pytest.raises(AllKeysExhaustedError):
            await provider.search("x", VideoAspect.PORTRAIT)


@pytest.mark.asyncio
async def test_provider_without_keys_raises() -> None:
    provider = PexelsProvider(api_keys=[])
    with pytest.raises(NoAPIKeyError):
        await provider.search("x", VideoAspect.PORTRAIT)


@pytest.mark.asyncio
async def test_api_key_rotator_round_robin() -> None:
    rot = APIKeyRotator(["a", "b", "c"], "test")
    seen = [await rot.acquire() for _ in range(6)]
    # round-robin debe ciclar — al menos los 3 valores aparecen
    assert set(seen) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 3. Cache hit / miss + validación ffprobe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_does_not_redownload(tmp_path: Path) -> None:
    url = "https://example.com/video.mp4?sig=xxx"
    # Pre-popular cache con contenido no vacío
    from core.visual.stock.cache import _cache_path

    cached = _cache_path(url, tmp_path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"fake-video-bytes")

    download_mock = AsyncMock()
    with patch("core.visual.stock.cache.download_to_file", download_mock):
        result = await fetch_and_cache(url, cache_dir=tmp_path)

    assert result == cached
    download_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_miss_downloads_and_validates(tmp_path: Path) -> None:
    url = "https://example.com/fresh.mp4"

    async def _fake_download(u: str, dest: Path, extra_headers: Any = None) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"some-bytes")
        return dest

    valid_probe = MagicMock()
    valid_probe.valid = True
    valid_probe.duration_s = 5.0
    valid_probe.fps = 30.0

    with (
        patch("core.visual.stock.cache.download_to_file", _fake_download),
        patch("core.visual.stock.cache.probe_video", return_value=valid_probe),
    ):
        result = await fetch_and_cache(url, cache_dir=tmp_path)

    assert result is not None
    assert result.exists()
    assert result.read_bytes() == b"some-bytes"


@pytest.mark.asyncio
async def test_cache_deletes_invalid_video(tmp_path: Path) -> None:
    """Si ffprobe reporta duration=0 o fps=0, el archivo debe borrarse."""
    url = "https://example.com/broken.mp4"

    async def _fake_download(u: str, dest: Path, extra_headers: Any = None) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"corrupt")
        return dest

    invalid_probe = MagicMock()
    invalid_probe.valid = False
    invalid_probe.duration_s = 0.0
    invalid_probe.fps = 0.0

    with (
        patch("core.visual.stock.cache.download_to_file", _fake_download),
        patch("core.visual.stock.cache.probe_video", return_value=invalid_probe),
    ):
        result = await fetch_and_cache(url, cache_dir=tmp_path)

    assert result is None
    # archivo debe estar borrado
    from core.visual.stock.cache import _cache_path

    assert not _cache_path(url, tmp_path).exists()


def test_probe_video_parses_ffprobe_json(tmp_path: Path) -> None:
    """Mock de subprocess.run que devuelve JSON de ffprobe válido."""
    fake_file = tmp_path / "v.mp4"
    fake_file.write_bytes(b"x")

    completed = subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(
            {"streams": [{"duration": "12.5", "r_frame_rate": "30000/1001"}]}
        ),
        stderr="",
    )
    with patch("core.visual.stock.cache.subprocess.run", return_value=completed):
        result = probe_video(fake_file)

    assert result.valid is True
    assert result.duration_s == pytest.approx(12.5)
    assert result.fps == pytest.approx(30000 / 1001, rel=1e-3)


def test_probe_video_handles_ffprobe_failure(tmp_path: Path) -> None:
    fake_file = tmp_path / "v.mp4"
    fake_file.write_bytes(b"x")
    completed = subprocess.CompletedProcess(
        args=["ffprobe"], returncode=1, stdout="", stderr="moov atom not found"
    )
    with patch("core.visual.stock.cache.subprocess.run", return_value=completed):
        result = probe_video(fake_file)
    assert result.valid is False


# ---------------------------------------------------------------------------
# 4. Registry
# ---------------------------------------------------------------------------


def test_registry_available_providers() -> None:
    names = available_providers()
    # Paid/keyed + corpus libres (ADR-022).
    assert {"pexels", "pixabay", "coverr"}.issubset(set(names))
    assert {"archive_org", "wikimedia", "nasa", "unsplash"}.issubset(set(names))


def test_registry_get_provider_known() -> None:
    p = get_provider("pexels")
    assert p.name == "pexels"


def test_registry_get_provider_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_provider("youtube")


def test_registry_get_all_providers_filters_unconfigured(monkeypatch) -> None:
    """Sin keys configuradas, `get_all_providers()` debe devolver lista vacía."""
    from shared import config as cfg_module

    fake_cfg = cfg_module.load_config()
    # forzar listas vacías
    fake_cfg.stock.pexels_api_keys = []
    fake_cfg.stock.pixabay_api_keys = []
    fake_cfg.stock.coverr_api_keys = []
    with patch("core.visual.stock.registry.load_config", return_value=fake_cfg):
        providers = get_all_providers()
    assert providers == []
