"""Auth, CORS, SSRF en /videos y consumo sin Redis (auditoría 2026-09)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from apps.api.main import app
from orchestration.queue.memory import InMemoryTaskManager
from orchestration.state.memory import MemoryStateManager


@pytest.fixture
def client_app():
    app.state.task_manager = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=4)
    app.state.state_manager = MemoryStateManager()
    cfg = MagicMock()
    cfg.app.max_queued_tasks = 4
    app.state.config = cfg
    app.state.distribution = None
    return app


def _client(a):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://t")


async def test_requires_key_when_configured(client_app, monkeypatch):
    monkeypatch.setenv("CONTENIDO_API_KEY", "s3cret")
    async with _client(client_app) as c:
        assert (await c.get("/tasks")).status_code == 401
        assert (await c.get("/tasks", headers={"X-API-Key": "s3cret"})).status_code == 200
        assert (await c.get("/tasks", headers={"Authorization": "Bearer s3cret"})).status_code == 200
        assert (await c.get("/health")).status_code == 200  # pública


async def test_non_dev_without_key_is_closed(client_app, monkeypatch):
    monkeypatch.delenv("CONTENIDO_API_KEY", raising=False)
    monkeypatch.setenv("CONTENIDO_ENV", "prod")
    async with _client(client_app) as c:
        assert (await c.get("/tasks")).status_code == 503


async def test_cors_not_wildcard(client_app):
    async with _client(client_app) as c:
        r = await c.options("/videos", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") != "*"
    assert "evil.example" not in r.headers.get("access-control-allow-origin", "")


async def test_videos_rejects_private_url(client_app):
    async with _client(client_app) as c:
        r = await c.post("/videos", json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 422


async def test_consume_processes_memory_queue():
    from apps.api.worker import consume
    from shared.schemas import VideoParams

    q = InMemoryTaskManager(max_concurrent_tasks=1, max_queued_tasks=4)
    await q.enqueue("t1", VideoParams(topic="x"))
    stop = asyncio.Event()
    run = AsyncMock(side_effect=lambda *a: stop.set())
    with patch("apps.api.worker.run_pipeline", run):
        await asyncio.wait_for(consume(q, MemoryStateManager(), stop, "w"), 5)
    assert run.await_args.args[1] == "t1"
