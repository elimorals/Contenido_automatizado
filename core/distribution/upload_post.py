"""Cliente async para Upload-Post (cross-post TikTok + Instagram).

Port del original síncrono `MoneyPrinterTurbo/app/services/upload_post.py`.
Cambios clave:
- requests sync -> httpx async
- retries con tenacity (exponential backoff)
- API basada en `Path` + caption + hashtags + metadata
- Cross-post a múltiples plataformas en UNA sola llamada multipart
- Manejo defensivo: nunca crashea, devuelve `UploadResult(success=False, errors=...)`
- API key jamás se loguea (sólo prefix `***`)

Docs: https://docs.upload-post.com
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from loguru import logger

try:
    from tenacity import (
        AsyncRetrying,
        retry_if_exception,
        stop_after_attempt,
        wait_exponential,
    )

    _HAS_TENACITY = True
except ImportError:  # pragma: no cover - tenacity está en uv.lock
    _HAS_TENACITY = False


def _is_retryable(exc: BaseException) -> bool:
    """Determina si una excepción de httpx amerita reintento.

    - Errores de transporte (timeout/connection) -> sí.
    - HTTP 4xx que no sea 429 -> NO (auth, validación, etc).
    - HTTP 5xx o 429 -> sí.
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False

from core.distribution.base import DistributionService
from core.distribution.schemas import SUPPORTED_PLATFORMS, UploadResult, UploadStatus

API_BASE = "https://api.upload-post.com"
UPLOAD_TIMEOUT_S = 300.0  # 5 min para videos grandes
STATUS_TIMEOUT_S = 30.0
MAX_RETRIES = 3
MAX_CAPTION_CHARS = 2200  # Instagram cap
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _mask(api_key: str) -> str:
    """Nunca loguear api_key entero. Sólo prefix `***`."""
    if not api_key:
        return "<empty>"
    return f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"


def _build_caption(caption: str, hashtags: list[str] | None) -> str:
    """Une caption + hashtags, respetando límite de 2200 chars (Instagram)."""
    parts: list[str] = []
    if caption:
        parts.append(caption.strip())
    if hashtags:
        tags = " ".join(
            t if t.startswith("#") else f"#{t}" for t in hashtags if t.strip()
        )
        if tags:
            parts.append(tags)
    full = "\n\n".join(parts)
    if len(full) > MAX_CAPTION_CHARS:
        logger.warning(
            f"Caption truncado: {len(full)} -> {MAX_CAPTION_CHARS} chars"
        )
        full = full[:MAX_CAPTION_CHARS]
    return full


def _validate_platforms(platforms: list[str]) -> tuple[list[str], dict[str, str]]:
    """Filtra plataformas soportadas. Devuelve (válidas, errores_por_plataforma)."""
    valid: list[str] = []
    errors: dict[str, str] = {}
    for p in platforms:
        norm = p.strip().lower()
        if norm in SUPPORTED_PLATFORMS:
            if norm not in valid:
                valid.append(norm)
        else:
            errors[norm] = f"platform not supported (allowed: {SUPPORTED_PLATFORMS})"
    return valid, errors


def _warn_aspect_if_landscape(video_path: Path) -> None:
    """Si el filename sugiere 16:9, advierte que TikTok lo cortará.

    Heurística barata: sólo mira el filename para no añadir dependencia de ffprobe
    aquí. El pipeline real puede pasar el aspect en `metadata={"aspect": "16:9"}`.
    """
    name = video_path.name.lower()
    if "16x9" in name or "16-9" in name or "landscape" in name:
        logger.warning(
            f"Video parece 16:9 ({video_path.name}); TikTok/Instagram pueden cortar a 9:16."
        )


class UploadPostService(DistributionService):
    """Cliente async para Upload-Post API."""

    def __init__(
        self,
        api_key: str,
        username: str = "",
        *,
        api_base: str = API_BASE,
        upload_timeout_s: float = UPLOAD_TIMEOUT_S,
        status_timeout_s: float = STATUS_TIMEOUT_S,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if not api_key:
            raise ValueError("UploadPostService requiere api_key no vacía")
        self.api_key = api_key
        self.username = username
        self.api_base = api_base.rstrip("/")
        self.upload_timeout_s = upload_timeout_s
        self.status_timeout_s = status_timeout_s
        self.max_retries = max_retries
        logger.debug(
            f"UploadPostService initialized api_key={_mask(api_key)} "
            f"user={username or '<none>'} retries={max_retries}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upload_video(
        self,
        video_path: Path,
        platforms: list[str],
        caption: str = "",
        hashtags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> UploadResult:
        video_path = Path(video_path)
        metadata = metadata or {}

        # Validaciones tempranas
        if not video_path.exists():
            msg = f"Video file not found: {video_path}"
            logger.error(msg)
            return UploadResult(success=False, errors={"_validation": msg}, platforms=platforms)

        valid_platforms, platform_errors = _validate_platforms(platforms)
        if not valid_platforms:
            return UploadResult(
                success=False,
                errors=platform_errors or {"_validation": "no valid platforms"},
                platforms=platforms,
            )

        # Caption + warnings
        full_caption = _build_caption(caption, hashtags)
        if metadata.get("aspect") == "16:9":
            logger.warning(
                f"aspect=16:9 declarado en metadata; las plataformas verticales recortarán."
            )
        else:
            _warn_aspect_if_landscape(video_path)

        logger.info(
            f"Upload-Post: subiendo {video_path.name} a {valid_platforms} "
            f"(caption={len(full_caption)} chars)"
        )

        # Ejecutar con retries
        try:
            payload = await self._post_with_retries(
                video_path, valid_platforms, full_caption, metadata
            )
        except httpx.HTTPStatusError as exc:
            err = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.error(f"Upload-Post failed: {err}")
            return UploadResult(
                success=False,
                errors={"_http": err, **platform_errors},
                platforms=valid_platforms,
            )
        except httpx.HTTPError as exc:
            logger.error(f"Upload-Post transport error: {exc}")
            return UploadResult(
                success=False,
                errors={"_transport": str(exc), **platform_errors},
                platforms=valid_platforms,
            )
        except Exception as exc:  # noqa: BLE001 - safety net, nunca crashear
            logger.exception(f"Upload-Post unexpected error: {exc}")
            return UploadResult(
                success=False,
                errors={"_unexpected": str(exc), **platform_errors},
                platforms=valid_platforms,
            )

        # Parseo de respuesta
        return self._parse_upload_response(payload, valid_platforms, platform_errors)

    async def check_status(self, request_id: str) -> UploadStatus:
        headers = self._auth_headers()
        url = f"{self.api_base}/api/status/{request_id}"
        try:
            async with httpx.AsyncClient(timeout=self.status_timeout_s) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
        except httpx.HTTPError as exc:
            logger.error(f"check_status({request_id}) failed: {exc}")
            return UploadStatus(request_id=request_id, state="failed", raw={"error": str(exc)})

        return self._parse_status_response(request_id, payload)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Apikey {self.api_key}"}

    def _build_form_data(
        self,
        platforms: list[str],
        caption: str,
        metadata: dict,
    ) -> dict[str, str]:
        data: dict[str, str] = {
            "title": caption,
            "privacy_level": str(metadata.get("privacy_level", "PUBLIC_TO_EVERYONE")),
        }
        if self.username:
            data["user"] = self.username
        # Plataformas como índices (formato esperado por Upload-Post)
        for i, plat in enumerate(platforms):
            data[f"platform[{i}]"] = plat
        # Metadata extra opcional (whitelist conservadora)
        for k in ("schedule_at", "cover_url"):
            if k in metadata:
                data[k] = str(metadata[k])
        return data

    async def _post_with_retries(
        self,
        video_path: Path,
        platforms: list[str],
        caption: str,
        metadata: dict,
    ) -> dict[str, Any]:
        """POST multipart con reintentos exponenciales."""
        url = f"{self.api_base}/api/upload_video"
        headers = self._auth_headers()
        data = self._build_form_data(platforms, caption, metadata)

        async def _do_post() -> dict[str, Any]:
            # IMPORTANTE: reabrir el handle en cada intento (los streams se consumen).
            with video_path.open("rb") as fh:
                files = {"video": (video_path.name, fh, "video/mp4")}
                async with httpx.AsyncClient(timeout=self.upload_timeout_s) as client:
                    resp = await client.post(url, headers=headers, data=data, files=files)
            if resp.status_code in RETRYABLE_STATUS:
                # Force retry via raise_for_status() (HTTPStatusError es retryable)
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()

        if _HAS_TENACITY:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    return await _do_post()
            # Unreachable: AsyncRetrying con reraise=True levanta o devuelve dentro del with.
            raise RuntimeError("tenacity loop exited without return")  # pragma: no cover

        # Fallback manual exponential backoff
        import asyncio

        last_exc: Exception | None = None
        for attempt_idx in range(self.max_retries):
            try:
                return await _do_post()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt_idx == self.max_retries - 1:
                    raise
                delay = min(2**attempt_idx, 10)
                logger.warning(
                    f"Upload-Post intento {attempt_idx + 1} falló ({exc}); "
                    f"reintentando en {delay}s"
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    def _parse_upload_response(
        self,
        payload: dict[str, Any],
        platforms: list[str],
        platform_errors: dict[str, str],
    ) -> UploadResult:
        success = bool(payload.get("success", False))
        request_id = payload.get("request_id")
        urls: dict[str, str] = {}
        errors: dict[str, str] = dict(platform_errors)

        # Upload-Post puede devolver `results: {platform: {url, error}}` o similar.
        # Hacemos parseo defensivo para varios formatos comunes.
        results = payload.get("results") or {}
        if isinstance(results, dict):
            for plat, info in results.items():
                if isinstance(info, dict):
                    if info.get("url"):
                        urls[plat] = str(info["url"])
                    if info.get("error"):
                        errors[plat] = str(info["error"])
                elif isinstance(info, str):
                    urls[plat] = info

        # Formato alternativo: `urls: {platform: url}` plano
        flat_urls = payload.get("urls")
        if isinstance(flat_urls, dict):
            for plat, url in flat_urls.items():
                if isinstance(url, str):
                    urls.setdefault(plat, url)

        if not success and payload.get("message"):
            errors.setdefault("_message", str(payload["message"]))

        if success:
            logger.info(f"Upload-Post OK request_id={request_id} urls={list(urls)}")
        else:
            logger.warning(f"Upload-Post failed payload={payload}")

        return UploadResult(
            success=success,
            request_id=str(request_id) if request_id else None,
            urls=urls,
            errors=errors,
            platforms=platforms,
            raw=payload,
        )

    def _parse_status_response(
        self, request_id: str, payload: dict[str, Any]
    ) -> UploadStatus:
        raw_state = str(payload.get("state") or payload.get("status") or "").lower()
        state: str
        if raw_state in {"complete", "completed", "done", "success"}:
            state = "complete"
        elif raw_state in {"fail", "failed", "error"}:
            state = "failed"
        elif raw_state in {"uploading", "in_progress", "processing"}:
            state = "uploading"
        else:
            state = "pending"

        urls: dict[str, str] = {}
        raw_urls = payload.get("urls") or {}
        if isinstance(raw_urls, dict):
            urls = {k: str(v) for k, v in raw_urls.items() if isinstance(v, str)}

        return UploadStatus(request_id=request_id, state=state, urls=urls, raw=payload)  # type: ignore[arg-type]
