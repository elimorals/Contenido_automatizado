"""Cliente async de bajo nivel para la Higgsfield Platform API.

Wrapper httpx que abstrae:
- Auth header `Authorization: Key KEY_ID:KEY_SECRET`
- Patrón submit + poll (`/v1/<endpoint>` → `/v1/requests/{id}/status`)
- Descarga de assets (image/video) finales
- Catálogo de motion presets cacheado en disco
- Taxonomía de errores que espeja el SDK oficial

NO sabe nada de schemas del pipeline (Beat/BeatVisual). Los providers
(higgsfield.py, higgsfield_soul.py, higgsfield_effects.py) hacen el bridge.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from shared.config import HiggsfieldConfig, load_config


# =============================================================================
# Errores (espejo del SDK oficial)
# =============================================================================


class HiggsfieldError(RuntimeError):
    """Base de errores Higgsfield."""


class HiggsfieldAuthError(HiggsfieldError):
    """Credenciales inválidas o ausentes."""


class HiggsfieldBadInputError(HiggsfieldError):
    """Body rechazado por validación del servidor (400)."""


class HiggsfieldNotEnoughCreditsError(HiggsfieldError):
    """402 — créditos insuficientes."""


class HiggsfieldNSFWError(HiggsfieldError):
    """Generación bloqueada por filtro NSFW."""


class HiggsfieldTimeoutError(HiggsfieldError):
    """Polling excedió max_poll_time_s sin completar."""


class HiggsfieldAPIError(HiggsfieldError):
    """5xx u otro error genérico de la API."""


# =============================================================================
# Respuestas
# =============================================================================


@dataclass
class JobResult:
    """Resultado de una generación completa."""

    request_id: str
    status: str  # completed | failed | nsfw
    # Para video jobs:
    video_url: str | None = None
    # Para image jobs (Soul, T2I):
    image_urls: list[str] | None = None
    # Raw response por si el provider necesita más detalle:
    raw: dict[str, Any] | None = None


# =============================================================================
# Cliente
# =============================================================================


def _resolve_credentials(cfg: HiggsfieldConfig) -> str:
    """Resuelve la credencial 'KEY_ID:KEY_SECRET' desde varios fallbacks."""
    if cfg.credentials:
        return cfg.credentials
    if cfg.key_id and cfg.key_secret:
        return f"{cfg.key_id}:{cfg.key_secret}"
    raise HiggsfieldAuthError(
        "Higgsfield: credenciales ausentes. Setea HIGGSFIELD_CREDENTIALS "
        "(KEY_ID:KEY_SECRET) o HIGGSFIELD_KEY_ID + HIGGSFIELD_KEY_SECRET."
    )


class HiggsfieldClient:
    """Cliente async para la Higgsfield Platform API.

    Uso típico:
        async with HiggsfieldClient() as cli:
            result = await cli.submit_and_wait(
                endpoint="/v1/image2video/dop",
                payload={"model": "dop-turbo", "prompt": "...", "input_images": [...]},
            )
            video_bytes = await cli.download(result.video_url)
    """

    def __init__(self, cfg: HiggsfieldConfig | None = None) -> None:
        self.cfg = cfg or load_config().visual.higgsfield
        self._auth = _resolve_credentials(self.cfg) if self._credentials_present() else ""
        self._client: httpx.AsyncClient | None = None

    def _credentials_present(self) -> bool:
        return bool(self.cfg.credentials or (self.cfg.key_id and self.cfg.key_secret))

    # === Context manager ===

    async def __aenter__(self) -> HiggsfieldClient:
        self._client = httpx.AsyncClient(
            base_url=self.cfg.base_url,
            timeout=httpx.Timeout(self.cfg.timeout_s, connect=10.0),
            headers={
                "Authorization": f"Key {self._auth}",
                "Content-Type": "application/json",
                "User-Agent": "contenido/0.1 (Higgsfield-Python)",
            },
        )
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HiggsfieldError(
                "HiggsfieldClient: usar dentro de `async with` (cliente no inicializado)"
            )
        return self._client

    # === Mapping de errores HTTP ===

    @staticmethod
    def _raise_for_status(resp: httpx.Response, *, ctx: str) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:500]
        sc = resp.status_code
        if sc == 401 or sc == 403:
            raise HiggsfieldAuthError(f"{ctx}: HTTP {sc} {body}")
        if sc == 400 or sc == 422:
            raise HiggsfieldBadInputError(f"{ctx}: HTTP {sc} {body}")
        if sc == 402:
            raise HiggsfieldNotEnoughCreditsError(f"{ctx}: créditos insuficientes ({body})")
        raise HiggsfieldAPIError(f"{ctx}: HTTP {sc} {body}")

    # === Submit job ===

    async def submit(self, endpoint: str, payload: dict[str, Any]) -> str:
        """Envía un job y devuelve el request_id para polling.

        El endpoint debe ser path-only ('/v1/image2video/dop'); base_url ya
        está en el client.
        """
        client = self._ensure_client()
        resp = await client.post(endpoint, json=payload)
        self._raise_for_status(resp, ctx=f"submit {endpoint}")
        data = resp.json()
        # La API responde con request_id (o id) en formato diverso.
        rid = data.get("request_id") or data.get("id") or data.get("requestId")
        if not rid:
            raise HiggsfieldAPIError(
                f"submit {endpoint}: respuesta sin request_id ({str(data)[:200]})"
            )
        logger.debug(f"[higgsfield] submitted {endpoint} → {rid}")
        return rid

    # === Polling ===

    async def poll(self, request_id: str) -> JobResult:
        """Polea estado hasta `completed`/`failed`/`nsfw` o timeout.

        Status posibles: queued | in_progress | completed | failed | nsfw.
        """
        client = self._ensure_client()
        deadline = time.monotonic() + self.cfg.max_poll_time_s
        while True:
            resp = await client.get(f"/v1/requests/{request_id}/status")
            self._raise_for_status(resp, ctx=f"poll {request_id}")
            data = resp.json()
            status = (data.get("status") or "").lower()

            if status == "completed":
                video_url = None
                image_urls: list[str] | None = None
                video_obj = data.get("video")
                if isinstance(video_obj, dict):
                    video_url = video_obj.get("url")
                elif isinstance(video_obj, str):
                    video_url = video_obj
                # Algunos jobs (Soul, T2I) devuelven `images: [{url}, ...]`
                imgs = data.get("images") or []
                if isinstance(imgs, list) and imgs:
                    image_urls = [
                        (im.get("url") if isinstance(im, dict) else str(im))
                        for im in imgs
                        if im
                    ]
                # Si la respuesta usa JobSet (`jobs[].results`)
                jobs = data.get("jobs") or []
                if isinstance(jobs, list) and jobs and not (video_url or image_urls):
                    for j in jobs:
                        results = (j or {}).get("results") or {}
                        raw = results.get("raw") or results.get("min") or {}
                        if isinstance(raw, dict) and raw.get("url"):
                            url = raw["url"]
                            if url.endswith((".mp4", ".webm", ".mov")):
                                video_url = url
                            else:
                                image_urls = (image_urls or []) + [url]
                return JobResult(
                    request_id=request_id,
                    status="completed",
                    video_url=video_url,
                    image_urls=image_urls,
                    raw=data,
                )
            if status == "failed":
                err = data.get("error") or data.get("message") or "unknown"
                raise HiggsfieldAPIError(f"job {request_id} failed: {err}")
            if status == "nsfw":
                raise HiggsfieldNSFWError(
                    f"job {request_id}: contenido bloqueado por filtro NSFW"
                )
            # queued | in_progress → seguir poleando
            if time.monotonic() > deadline:
                raise HiggsfieldTimeoutError(
                    f"job {request_id}: excedió {self.cfg.max_poll_time_s}s"
                )
            await asyncio.sleep(self.cfg.poll_interval_s)

    async def submit_and_wait(self, endpoint: str, payload: dict[str, Any]) -> JobResult:
        rid = await self.submit(endpoint, payload)
        return await self.poll(rid)

    # === Asset upload + download ===

    async def upload_image(self, path: Path) -> str:
        """Sube una imagen local al CDN de Higgsfield y devuelve la URL.

        Algunos endpoints aceptan data URLs base64; otros requieren CDN URL.
        Por defecto usamos POST /v1/uploads con multipart.
        """
        client = self._ensure_client()
        with path.open("rb") as f:
            files = {"file": (path.name, f, "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png")}
            # Auth header se mantiene del client; quitamos Content-Type para que httpx setee multipart
            headers = {"Authorization": f"Key {self._auth}"}
            resp = await client.post(
                "/v1/uploads",
                files=files,
                headers=headers,
            )
        self._raise_for_status(resp, ctx="upload_image")
        data = resp.json()
        url = data.get("url") or data.get("cdn_url") or data.get("file_url")
        if not url:
            raise HiggsfieldAPIError(
                f"upload_image: respuesta sin URL ({str(data)[:200]})"
            )
        return url

    @staticmethod
    def _image_to_data_url(path: Path) -> str:
        """Fallback: data URL base64 cuando upload no aplica."""
        suffix = path.suffix.lower().lstrip(".") or "jpeg"
        if suffix == "jpg":
            suffix = "jpeg"
        return (
            f"data:image/{suffix};base64,"
            f"{base64.b64encode(path.read_bytes()).decode()}"
        )

    async def download(self, url: str) -> bytes:
        """Descarga bytes desde una URL (CDN del job result)."""
        if url.startswith("data:"):
            try:
                _, b64 = url.split(",", 1)
            except ValueError as e:
                raise HiggsfieldAPIError(f"data URL malformed: {e}") from e
            return base64.b64decode(b64)
        # URLs externas: usamos un client fresco (no necesita auth) con timeout amplio
        async with httpx.AsyncClient(timeout=300.0) as cli:
            r = await cli.get(url)
            if r.status_code >= 400:
                raise HiggsfieldAPIError(f"download {url}: HTTP {r.status_code}")
            return r.content

    # === Motion catalog ===

    async def list_motions(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Lista los presets de motion disponibles (GET /v1/motions).

        Cachea en disco según `motion_catalog_cache_path`. Llamar `force_refresh`
        para invalidar el cache (ej. CLI: `contenido higgsfield refresh-motions`).
        """
        cache_path = Path(self.cfg.motion_catalog_cache_path)
        if not force_refresh and cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # cache corrupto → re-fetch

        client = self._ensure_client()
        resp = await client.get("/v1/motions")
        self._raise_for_status(resp, ctx="list_motions")
        data = resp.json()
        motions = data if isinstance(data, list) else data.get("motions") or []

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(motions, indent=2))
        return motions

    async def resolve_motion_id(self, preset_name: str) -> str | None:
        """Resuelve un preset semántico (ej 'dolly_in') a motion_id (UUID).

        Devuelve None si no hay match. El caller decide fallback (texto en prompt).
        """
        motions = await self.list_motions()
        target = preset_name.replace("_", " ").lower().strip()
        for m in motions:
            name = (m.get("name") or m.get("title") or "").lower().strip()
            if name == target or name.replace(" ", "_") == preset_name.lower():
                return m.get("id") or m.get("motion_id")
        return None

    # === Soul helpers ===

    async def list_soul_ids(self, page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
        """GET /v1/soul-ids?page=...&page_size=..."""
        client = self._ensure_client()
        resp = await client.get(
            "/v1/soul-ids", params={"page": page, "page_size": page_size}
        )
        self._raise_for_status(resp, ctx="list_soul_ids")
        data = resp.json()
        return data if isinstance(data, list) else (data.get("items") or [])

    async def create_soul_id(
        self,
        *,
        name: str,
        image_urls: list[str],
        wait: bool = True,
    ) -> str:
        """Crea un SoulId entrenado con N imágenes de referencia.

        Devuelve el `reference_id` (SoulId) usable en payloads downstream.
        """
        payload = {"name": name, "input_images": [{"type": "image_url", "image_url": u} for u in image_urls]}
        rid = await self.submit("/v1/soul-ids", payload)
        if not wait:
            return rid
        result = await self.poll(rid)
        # Soul ID viene en raw.reference_id según SDK
        raw = result.raw or {}
        sid = raw.get("reference_id") or raw.get("soul_id") or rid
        return str(sid)
