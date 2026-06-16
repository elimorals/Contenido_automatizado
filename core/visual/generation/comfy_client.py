"""Cliente async para ComfyUI server (REST + WebSocket).

ComfyUI usa un protocolo único:
- REST POST /prompt con el grafo completo (no presets)
- WebSocket /ws?clientId=<UUID> para eventos en tiempo real
- La "señal de done" es un mensaje `executing` con `node: null`
- GET /history/{prompt_id} devuelve outputs cuando done
- GET /view?filename=&subfolder=&type= descarga el archivo binario

Multi-tenant: cada session usa un `client_id` UUID distinto; el server
filtra WS events por client_id automáticamente. Un mismo server atiende
N tenants concurrentes.

Soporta dos modos:
- Self-hosted local: `server_url=http://127.0.0.1:8188`
- Managed remoto: `server_url=https://...` + `auth_header="Bearer XYZ"`
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import websockets
from loguru import logger

from shared.config import ComfyUIConfig, load_config
from shared.schemas import ComfyJob, ComfyJobStatus


# =============================================================================
# Errores tipados
# =============================================================================


class ComfyError(RuntimeError):
    """Base de errores ComfyUI."""


class ComfyConnectionError(ComfyError):
    """No se pudo conectar al server (down, network)."""


class ComfyAuthError(ComfyError):
    """401/403 — auth_header inválido o ausente."""


class ComfyValidationError(ComfyError):
    """400 con node_errors — workflow JSON inválido (faltan inputs, modelos)."""

    def __init__(self, message: str, node_errors: dict | None = None) -> None:
        super().__init__(message)
        self.node_errors = node_errors or {}


class ComfyTimeoutError(ComfyError):
    """Workflow excedió poll_timeout_s sin completar."""


class ComfyExecutionError(ComfyError):
    """Workflow ejecutó pero algún nodo falló."""

    def __init__(self, message: str, node_id: str | None = None) -> None:
        super().__init__(message)
        self.node_id = node_id


# =============================================================================
# Cliente
# =============================================================================


def _ws_url_from_http(http_url: str) -> str:
    """`http://x:8188` → `ws://x:8188/ws`. Soporta http(s)."""
    base = http_url.rstrip("/")
    if base.startswith("https://"):
        return f"wss://{base[len('https://'):]}/ws"
    if base.startswith("http://"):
        return f"ws://{base[len('http://'):]}/ws"
    # asumimos sin scheme = http
    return f"ws://{base}/ws"


class ComfyClient:
    """Cliente async para una instancia ComfyUI.

    Uso típico:
        async with ComfyClient() as cli:
            job = await cli.submit_workflow(workflow_json)
            async for event in cli.stream_events(job.prompt_id):
                ...  # progress, executing, executed
            outputs = await cli.get_history(job.prompt_id)
            data = await cli.download_view(filename, subfolder, type)
    """

    def __init__(
        self,
        cfg: ComfyUIConfig | None = None,
        *,
        client_id: str | None = None,
    ) -> None:
        self.cfg = cfg or load_config().visual.comfyui
        # Multi-tenant: el client_id PUEDE ser tenant_id si quieres correlación
        self.client_id = client_id or str(uuid.uuid4())
        self._http: httpx.AsyncClient | None = None

    # === Auth + URLs ===

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self.cfg.auth_header:
            # Soporta "Bearer XYZ" o "Basic abc" como header completo
            parts = self.cfg.auth_header.split(" ", 1)
            if len(parts) == 2:
                h["Authorization"] = self.cfg.auth_header
            else:
                # Si solo es el token sin scheme, asumimos Bearer
                h["Authorization"] = f"Bearer {self.cfg.auth_header}"
        return h

    def _http_url(self, path: str) -> str:
        return self.cfg.server_url.rstrip("/") + path

    def _ws_url(self) -> str:
        return f"{_ws_url_from_http(self.cfg.server_url)}?clientId={self.client_id}"

    # === Context manager ===

    async def __aenter__(self) -> ComfyClient:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(self.cfg.submit_timeout_s, connect=10.0),
            headers=self._headers(),
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise ComfyError(
                "ComfyClient: usar dentro de `async with` (HTTP client no inicializado)"
            )
        return self._http

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, ctx: str) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:500]
        sc = resp.status_code
        if sc in (401, 403):
            raise ComfyAuthError(f"{ctx}: HTTP {sc} {body}")
        if sc == 400:
            # Tratar de extraer node_errors si vienen
            try:
                data = resp.json()
                if isinstance(data, dict) and "node_errors" in data:
                    raise ComfyValidationError(
                        f"{ctx}: validation failed ({body})",
                        node_errors=data.get("node_errors", {}),
                    )
            except json.JSONDecodeError:
                pass
            raise ComfyValidationError(f"{ctx}: HTTP 400 {body}")
        raise ComfyError(f"{ctx}: HTTP {sc} {body}")

    # === Health ===

    async def is_alive(self) -> bool:
        """True si el server responde a GET /system_stats."""
        try:
            r = await self._ensure_http().get(self._http_url("/system_stats"))
            return r.status_code < 400
        except (httpx.HTTPError, ComfyError):
            return False

    async def system_stats(self) -> dict[str, Any]:
        """Devuelve `/system_stats`: OS, python version, devices, vram."""
        r = await self._ensure_http().get(self._http_url("/system_stats"))
        self._raise_for_status(r, ctx="system_stats")
        return r.json()

    # === Introspección ===

    async def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """Catalogo completo de nodos disponibles (o uno específico)."""
        path = f"/object_info/{node_class}" if node_class else "/object_info"
        r = await self._ensure_http().get(self._http_url(path))
        if r.status_code == 404 and node_class:
            return {}
        self._raise_for_status(r, ctx=f"object_info({node_class})")
        return r.json()

    async def list_models(self, model_type: str) -> list[str]:
        """GET /models/{type}. type: checkpoints|loras|vae|controlnet|...
        Devuelve [] si el tipo no existe.
        """
        r = await self._ensure_http().get(self._http_url(f"/models/{model_type}"))
        if r.status_code >= 400:
            return []
        data = r.json()
        return data if isinstance(data, list) else []

    async def list_embeddings(self) -> list[str]:
        r = await self._ensure_http().get(self._http_url("/embeddings"))
        if r.status_code >= 400:
            return []
        return r.json()

    # === Queue control ===

    async def queue_state(self) -> dict[str, Any]:
        """GET /queue — listado completo de running + pending."""
        r = await self._ensure_http().get(self._http_url("/queue"))
        self._raise_for_status(r, ctx="queue_state")
        return r.json()

    async def queue_remaining(self) -> int:
        """GET /prompt — cuántos jobs faltan."""
        r = await self._ensure_http().get(self._http_url("/prompt"))
        self._raise_for_status(r, ctx="queue_remaining")
        data = r.json()
        return int(data.get("exec_info", {}).get("queue_remaining", 0))

    async def interrupt(self) -> None:
        """POST /interrupt — cancela el job actual."""
        r = await self._ensure_http().post(self._http_url("/interrupt"))
        self._raise_for_status(r, ctx="interrupt")

    async def free_memory(self, *, unload_models: bool = True, free_memory: bool = True) -> None:
        """POST /free — libera VRAM (útil ante OOM antes de retry)."""
        r = await self._ensure_http().post(
            self._http_url("/free"),
            json={"unload_models": unload_models, "free_memory": free_memory},
        )
        self._raise_for_status(r, ctx="free")

    # === Submit + history ===

    async def submit_prompt(
        self,
        workflow: dict[str, Any],
        *,
        extra_data: dict[str, Any] | None = None,
        front: bool = False,
    ) -> str:
        """POST /prompt. Devuelve `prompt_id` para tracking posterior.

        El workflow es el grafo completo en formato API (no GUI).
        """
        payload: dict[str, Any] = {
            "prompt": workflow,
            "client_id": self.client_id,
        }
        if extra_data:
            payload["extra_data"] = extra_data
        if front:
            payload["front"] = True
        try:
            r = await self._ensure_http().post(
                self._http_url("/prompt"), json=payload,
            )
        except httpx.ConnectError as e:
            raise ComfyConnectionError(
                f"ComfyUI server no responde en {self.cfg.server_url}: {e}"
            ) from e
        self._raise_for_status(r, ctx="submit_prompt")
        data = r.json()
        pid = data.get("prompt_id")
        if not pid:
            raise ComfyError(f"submit_prompt: respuesta sin prompt_id ({data})")
        logger.debug(f"[comfy] submitted prompt_id={pid} client_id={self.client_id}")
        return pid

    async def get_history(self, prompt_id: str) -> dict[str, Any]:
        """GET /history/{prompt_id}. Devuelve `{}` si pending o desconocido."""
        r = await self._ensure_http().get(self._http_url(f"/history/{prompt_id}"))
        if r.status_code == 404:
            return {}
        self._raise_for_status(r, ctx="get_history")
        data = r.json()
        # ComfyUI devuelve {prompt_id: {prompt, outputs, status}}
        return data.get(prompt_id, {}) if isinstance(data, dict) else {}

    # === View / download ===

    async def download_view(
        self,
        filename: str,
        *,
        subfolder: str = "",
        type: str = "output",
    ) -> bytes:
        """GET /view?filename=&subfolder=&type=. Devuelve bytes del archivo."""
        params = {"filename": filename, "type": type}
        if subfolder:
            params["subfolder"] = subfolder
        r = await self._ensure_http().get(self._http_url("/view"), params=params)
        self._raise_for_status(r, ctx=f"download_view({filename})")
        return r.content

    # === Upload ===

    async def upload_image(
        self,
        path: Path,
        *,
        subfolder: str = "",
        type: str = "input",
        overwrite: bool = True,
    ) -> dict[str, str]:
        """POST /upload/image multipart. Devuelve `{name, subfolder, type}`."""
        if not path.exists():
            raise FileNotFoundError(path)
        files = {
            "image": (path.name, path.read_bytes(), "application/octet-stream"),
        }
        data: dict[str, str] = {"type": type, "overwrite": "1" if overwrite else "0"}
        if subfolder:
            data["subfolder"] = subfolder
        # No usamos JSON Content-Type para multipart
        headers = {k: v for k, v in self._headers().items() if k.lower() != "content-type"}
        r = await self._ensure_http().post(
            self._http_url("/upload/image"),
            data=data,
            files=files,
            headers=headers,
        )
        self._raise_for_status(r, ctx="upload_image")
        return r.json()

    # === WebSocket eventos ===

    @asynccontextmanager
    async def open_ws(self) -> AsyncIterator[Any]:
        """Context manager para una conexión WS limpia."""
        url = self._ws_url()
        extra_headers: list[tuple[str, str]] = []
        if self.cfg.auth_header:
            extra_headers.append(("Authorization", self.cfg.auth_header))
        try:
            ws = await websockets.connect(
                url,
                additional_headers=extra_headers or None,
                max_size=64 * 1024 * 1024,  # 64 MB para preview frames binarios
            )
        except (OSError, websockets.WebSocketException) as e:
            raise ComfyConnectionError(
                f"WS connect a {url} falló: {e}"
            ) from e
        try:
            yield ws
        finally:
            await ws.close()

    async def stream_events(
        self,
        prompt_id: str,
        *,
        timeout_s: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Async-itera eventos del WS filtrados por `prompt_id`.

        Yields dicts con `{type, data}`. Termina cuando recibe
        `executing` con `node: null` y `prompt_id` matching (= done)
        o `execution_error` (raises ComfyExecutionError).

        Si `timeout_s` se excede, raises ComfyTimeoutError.
        """
        deadline = time.monotonic() + (timeout_s or self.cfg.poll_timeout_s)
        async with self.open_ws() as ws:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ComfyTimeoutError(
                        f"WS stream excedió {timeout_s or self.cfg.poll_timeout_s}s"
                    )
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 5.0))
                except asyncio.TimeoutError:
                    # No msg en 5s — sigamos esperando hasta deadline
                    continue
                except websockets.ConnectionClosed:
                    raise ComfyConnectionError("WS cerrado prematuramente")

                # Frames binarios (preview images) → skip
                if isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                mdata = msg.get("data") or {}
                # Filtrar por prompt_id si está presente
                msg_pid = mdata.get("prompt_id")
                if msg_pid and msg_pid != prompt_id:
                    continue

                yield msg

                # Done signal: executing con node=null y prompt_id matching
                if (
                    mtype == "executing"
                    and mdata.get("node") is None
                    and msg_pid == prompt_id
                ):
                    return
                if mtype == "execution_error" and msg_pid == prompt_id:
                    raise ComfyExecutionError(
                        f"Workflow falló: {mdata.get('exception_message', 'unknown')}",
                        node_id=mdata.get("node_id"),
                    )
                if mtype == "execution_interrupted" and msg_pid == prompt_id:
                    raise ComfyExecutionError(
                        "Workflow interrumpido por usuario",
                        node_id=mdata.get("node_id"),
                    )

    async def poll_until_done(self, prompt_id: str) -> dict[str, Any]:
        """Fallback sin WS: polea GET /history hasta completar.

        Útil cuando WS no está disponible (proxy lo bloquea, restricciones de red).
        """
        deadline = time.monotonic() + self.cfg.poll_timeout_s
        while True:
            hist = await self.get_history(prompt_id)
            if hist:
                status = hist.get("status", {})
                if status.get("completed") is True:
                    return hist
                # Algunos servers usan status_str
                if status.get("status_str") in ("error", "interrupted"):
                    raise ComfyExecutionError(
                        f"Workflow {prompt_id} falló: {status}"
                    )
            if time.monotonic() > deadline:
                raise ComfyTimeoutError(
                    f"poll_until_done excedió {self.cfg.poll_timeout_s}s"
                )
            await asyncio.sleep(self.cfg.poll_interval_s)

    # === End-to-end helper ===

    async def execute_workflow(
        self,
        workflow: dict[str, Any],
        *,
        use_ws: bool = True,
    ) -> ComfyJob:
        """Submit + tracking + retrieval en un solo call.

        Devuelve un `ComfyJob` con status=COMPLETED y `images`/`videos`/`gifs`
        poblados con los outputs descargables vía `download_view()`.
        """
        job = ComfyJob(
            prompt_id="",
            client_id=self.client_id,
            workflow_id="",
            submitted_at_unix=time.time(),
        )
        try:
            job.prompt_id = await self.submit_prompt(workflow)
            job.status = ComfyJobStatus.EXECUTING

            if use_ws:
                try:
                    async for event in self.stream_events(job.prompt_id):
                        etype = event.get("type")
                        edata = event.get("data") or {}
                        if etype == "executing":
                            job.current_node = edata.get("node")
                        elif etype == "progress":
                            job.progress_value = int(edata.get("value", 0))
                            job.progress_max = int(edata.get("max", 0))
                except ComfyConnectionError:
                    logger.warning("[comfy] WS falló — fallback a polling")
                    use_ws = False

            if not use_ws:
                await self.poll_until_done(job.prompt_id)

            # Recuperar outputs desde history
            hist = await self.get_history(job.prompt_id)
            outputs = hist.get("outputs", {})
            for _node_id, node_out in outputs.items():
                if not isinstance(node_out, dict):
                    continue
                for img in node_out.get("images") or []:
                    if isinstance(img, dict):
                        job.images.append(img)
                for vid in node_out.get("videos") or []:
                    if isinstance(vid, dict):
                        job.videos.append(vid)
                for gif in node_out.get("gifs") or []:
                    if isinstance(gif, dict):
                        job.gifs.append(gif)

            job.status = ComfyJobStatus.COMPLETED
            job.completed_at_unix = time.time()
            return job
        except ComfyTimeoutError as e:
            job.status = ComfyJobStatus.TIMEOUT
            job.error_message = str(e)
            raise
        except ComfyExecutionError as e:
            job.status = ComfyJobStatus.FAILED
            job.error_message = str(e)
            job.error_node = e.node_id
            raise
        except ComfyError as e:
            job.status = ComfyJobStatus.FAILED
            job.error_message = str(e)
            raise
