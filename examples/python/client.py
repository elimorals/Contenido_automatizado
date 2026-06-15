"""Cliente Python reutilizable para la API de contenido.

Uso:

    from client import ContenidoClient

    client = ContenidoClient("http://localhost:8000")
    task_id = client.create_video(topic="placebo effect", mode="premium")
    result = client.wait_for_task(task_id)
    print(result["videos"])
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class TaskResult:
    task_id: str
    state: int
    progress: int
    videos: list[str]
    script: str | None
    timings_s: dict[str, float]
    cost_breakdown: dict[str, float]
    error: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.state == 1

    @property
    def is_failed(self) -> bool:
        return self.state == -1


class ContenidoClient:
    """Cliente HTTP para la API de contenido. Síncrono (usa httpx.Client)."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (
            base_url
            or os.getenv("CONTENIDO_API_URL")
            or "http://localhost:8000"
        ).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._client.close()

    # ---------- Health ----------

    def health(self) -> dict[str, Any]:
        r = self._client.get("/health")
        r.raise_for_status()
        return r.json()

    # ---------- Videos ----------

    def create_video(
        self,
        *,
        topic: str | None = None,
        url: str | None = None,
        subject: str | None = None,
        mode: str = "premium",
        aspect: str = "9:16",
        voice_name: str = "",
        visual_strategy: str = "hybrid",
        use_veo: bool = False,
        subtitle_style: str = "word_burst",
        auto_upload: bool = False,
        **extra,
    ) -> str:
        """POST /videos. Devuelve task_id."""
        body: dict[str, Any] = {
            "mode": mode,
            "aspect": aspect,
            "voice_name": voice_name,
            "visual_strategy": visual_strategy,
            "use_veo": use_veo,
            "subtitle_style": subtitle_style,
            "auto_upload": auto_upload,
            **extra,
        }
        if topic:
            body["topic"] = topic
        elif url:
            body["url"] = url
        elif subject:
            body["subject"] = subject
        else:
            raise ValueError("Debe especificar uno de: topic, url, subject")

        r = self._client.post("/videos", json=body)
        r.raise_for_status()
        return r.json()["data"]["task_id"]

    def get_task(self, task_id: str) -> TaskResult:
        r = self._client.get(f"/tasks/{task_id}")
        r.raise_for_status()
        data = r.json()["data"]
        return TaskResult(
            task_id=data["task_id"],
            state=data["state"],
            progress=data.get("progress", 0),
            videos=data.get("videos", []),
            script=data.get("script"),
            timings_s=data.get("timings_s", {}),
            cost_breakdown=data.get("cost_breakdown", {}),
            error=(data.get("timings_s") or {}).get("error"),
        )

    def delete_task(self, task_id: str) -> None:
        r = self._client.delete(f"/tasks/{task_id}")
        r.raise_for_status()

    def list_tasks(self, page: int = 1, page_size: int = 10) -> dict[str, Any]:
        r = self._client.get(f"/tasks?page={page}&page_size={page_size}")
        r.raise_for_status()
        return r.json()["data"]

    def wait_for_task(
        self,
        task_id: str,
        *,
        poll_interval_s: float = 2.0,
        timeout_s: float = 600.0,
        on_progress=None,
    ) -> TaskResult:
        """Polling hasta complete/failed/timeout. Llama `on_progress(result)` cada poll."""
        start = time.time()
        while True:
            result = self.get_task(task_id)
            if on_progress:
                on_progress(result)
            if result.is_complete or result.is_failed:
                return result
            if time.time() - start > timeout_s:
                raise TimeoutError(f"Task {task_id} timeout after {timeout_s}s")
            time.sleep(poll_interval_s)

    # ---------- Endpoints individuales ----------

    def generate_script(self, **params) -> dict[str, Any]:
        """POST /scripts → ScriptDraft."""
        r = self._client.post("/scripts", json=params)
        r.raise_for_status()
        return r.json()["data"]

    def generate_audio(self, **params) -> dict[str, Any]:
        """POST /audio → audio_path + word_timings."""
        r = self._client.post("/audio", json=params)
        r.raise_for_status()
        return r.json()["data"]

    def generate_narrative(self, **params) -> dict[str, Any]:
        """POST /narratives → ScriptDraft delayed-reveal."""
        r = self._client.post("/narratives", json=params)
        r.raise_for_status()
        return r.json()["data"]

    def run_hunters(self, topic: str, mode: str = "premium") -> list[dict[str, Any]]:
        """POST /hunters → 12 candidates."""
        r = self._client.post("/hunters", json={"topic": topic, "mode": mode})
        r.raise_for_status()
        return r.json()["data"]["candidates"]

    def get_costs(self, task_id: str) -> dict[str, float]:
        r = self._client.get(f"/costs/{task_id}")
        r.raise_for_status()
        return r.json()["data"]

    def get_timings(self, task_id: str) -> dict[str, float]:
        r = self._client.get(f"/timings/{task_id}")
        r.raise_for_status()
        return r.json()["data"]
