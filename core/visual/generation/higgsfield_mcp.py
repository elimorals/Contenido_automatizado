"""Higgsfield vía su servidor MCP, con el token OAuth de Claude Code (ADR-037).

Por qué existe
--------------
Higgsfield tiene **dos** productos con facturación separada:

- **Platform API** (`platform.higgsfield.ai`, `Key ID:SECRET`) — la que usa
  `higgsfield_client.py`. Credenciales válidas pero **sin créditos**:
  `{"detail": "Not enough credits"}`.
- **MCP** (`mcp.higgsfield.ai`, OAuth) — la cuenta web, con los créditos del
  plan. Es la que sí tiene saldo.

Este módulo deja al pipeline usar la segunda. El protocolo MCP es JSON-RPC sobre
HTTP, así que no hace falta un SDK: se habla directo.

⚠️ Es un **puente de desarrollo, no una ruta de producción.** El token lo emite
y renueva Claude Code, y se lee del keychain de macOS — un servidor sin esa
sesión no puede usarlo. Para producción va la Platform API con créditos. Por eso
está off por default y aislado en su propio tier.

Flujo (verificado contra el servidor real, 2026-08-29):

    initialize → notifications/initialized → tools/call
    media_upload (presigned URL) → PUT bytes → generate_video(medias=[uuid])
    → job_status(sync=true) hasta terminal → descargar el MP4
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.veo import _motion_clause
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, VideoSource

KEYCHAIN_SERVICE = "Claude Code-credentials"
PROTOCOL_VERSION = "2024-11-05"


# =============================================================================
# Token del keychain
# =============================================================================


def _read_oauth_entry(server: str = "higgsfield") -> dict[str, Any]:
    """Entrada OAuth de `server` en el keychain de macOS.

    Claude Code guarda los tokens MCP ahí, NO en `~/.claude/.credentials.json`
    (ese archivo existe pero está desactualizado). La clave lleva un sufijo
    hash, así que se busca por prefijo.
    """
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        raise VisualGenerationError(f"keychain no accesible: {e}") from e

    try:
        entries = json.loads(raw).get("mcpOAuth") or {}
    except json.JSONDecodeError as e:
        raise VisualGenerationError(f"keychain con JSON inválido: {e}") from e

    for key, value in entries.items():
        if key.split("|")[0] == server:
            return value
    raise VisualGenerationError(
        f"sin token OAuth para '{server}'. Conéctalo con `/mcp` en Claude Code."
    )


# =============================================================================
# Cliente MCP mínimo
# =============================================================================


class HiggsfieldMCPClient:
    """JSON-RPC sobre HTTP contra el MCP de Higgsfield.

    Mínimo a propósito: `initialize` + `tools/call` es todo lo que hace falta.
    Un SDK de MCP añadiría una dependencia para envolver dos POSTs.
    """

    def __init__(self, timeout_s: float = 300.0) -> None:
        entry = _read_oauth_entry()
        self.url: str = entry["serverUrl"]
        self._token: str = entry["accessToken"]
        self.timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None
        self._ready = False

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            # El servidor responde en SSE aunque se pida JSON; hay que aceptarlo.
            "Accept": "application/json, text/event-stream",
        }

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        """Decodifica la respuesta SSE.

        Las llamadas largas (`job_status` con `sync`) intercalan comentarios de
        keepalive (`: keepalive`) antes del `data:` real, así que no vale con
        cortar por el primer `data: ` — hay que recorrer las líneas y quedarse
        con el último payload JSON válido.
        """
        text = resp.text
        if "data:" not in text:
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                raise VisualGenerationError(f"MCP: respuesta ilegible ({text[:200]})") from e

        last: dict[str, Any] | None = None
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                last = json.loads(chunk)
            except json.JSONDecodeError:
                continue
        if last is None:
            raise VisualGenerationError(f"MCP: SSE sin payload JSON ({text[:200]})")
        return last

    async def __aenter__(self) -> HiggsfieldMCPClient:
        self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._ready = False

    async def _rpc(self, method: str, params: dict | None = None, rid: int = 1) -> dict:
        if self._client is None:
            raise VisualGenerationError("HiggsfieldMCPClient: usar con `async with`")
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if rid:
            body["id"] = rid
        if params is not None:
            body["params"] = params
        resp = await self._client.post(self.url, headers=self._headers(), json=body)
        if resp.status_code >= 400:
            raise VisualGenerationError(f"MCP {method} HTTP {resp.status_code}: {resp.text[:200]}")
        if not rid:
            return {}
        data = self._parse(resp)
        if "error" in data:
            raise VisualGenerationError(f"MCP {method}: {str(data['error'])[:300]}")
        return data.get("result") or {}

    async def _handshake(self) -> None:
        if self._ready:
            return
        await self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "contenido", "version": "1"},
        })
        await self._rpc("notifications/initialized", rid=0)
        self._ready = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Llama una herramienta y devuelve su payload ya deserializado.

        El resultado viene como `content[].text` con JSON dentro — una capa más
        de envoltura que hay que abrir.
        """
        await self._handshake()
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments}, rid=2)
        for item in result.get("content") or []:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    return {"text": item["text"]}
        return result

    # --- helpers de alto nivel -------------------------------------------

    async def upload_media(self, path: Path) -> str:
        """Sube un archivo y devuelve su `media_id`. Tres pasos.

        `media_upload` NO devuelve JSON: contesta en prosa, con el UUID y la URL
        prefirmada embebidos dentro de un comando `curl` de ejemplo. Está
        pensado para que un asistente ejecute ese curl, así que hay que extraer
        ambos con regex — parsear texto es feo, pero es el contrato real.

        Los bytes van por PUT directo a S3 (el servidor MCP nunca los ve) y
        luego `media_confirm` registra el media como utilizable.
        """
        content_type = (
            "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        )
        info = await self.call_tool("media_upload", {
            "filename": path.name,
            "content_type": content_type,
        })

        text = info.get("text") if isinstance(info, dict) else ""
        if not text:
            raise VisualGenerationError(f"media_upload sin texto: {str(info)[:200]}")

        media_id_m = re.search(
            r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b", text
        )
        url_m = re.search(r"'(https://[^']+)'", text) or re.search(r"(https://\S+)", text)
        if not media_id_m or not url_m:
            raise VisualGenerationError(f"media_upload ilegible: {text[:220]}")
        media_id, put_url = media_id_m.group(1), url_m.group(1)

        assert self._client is not None
        put = await self._client.put(
            put_url, content=path.read_bytes(), headers={"Content-Type": content_type}
        )
        if put.status_code >= 400:
            raise VisualGenerationError(
                f"PUT del media falló: HTTP {put.status_code} {put.text[:150]}"
            )

        # Sin confirmar, el media existe en S3 pero la generación no lo acepta.
        await self.call_tool("media_confirm", {"media_ids": [media_id]})
        return media_id

    async def await_job(self, job_id: str, max_wait_s: float = 600.0) -> dict[str, Any]:
        """Sondea hasta estado terminal. `sync=true` bloquea ~25 s por llamada."""
        waited = 0.0
        while waited < max_wait_s:
            status = await self.call_tool("job_status", {"jobId": job_id, "sync": True})
            state = _job_state(status)
            if state in ("completed", "succeeded", "success", "done"):
                return status
            if state in ("failed", "error", "canceled", "cancelled", "nsfw"):
                raise VisualGenerationError(
                    f"job {job_id} terminó en '{state}': {str(status)[:200]}"
                )
            delay = float(status.get("poll_after_seconds") or 5.0)
            await asyncio.sleep(delay)
            waited += delay + 25.0  # el `sync` ya consumió su propia espera
        raise VisualGenerationError(f"job {job_id} no terminó en {max_wait_s:.0f}s")


def _job_state(payload: dict[str, Any]) -> str:
    """Estado de un job, venga estructurado o en prosa.

    `job_status` responde con texto tipo `Job <uuid> - completed\n<url>`, no con
    un campo `status`. Se busca primero la clave y se cae al texto — asumir la
    clave hacía que un job COMPLETADO se sondeara hasta agotar el timeout.
    """
    for key in ("status", "state"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val.lower()
    text = payload.get("text")
    if not isinstance(text, str):
        return ""
    m = re.search(
        r"\b(completed|succeeded|success|done|failed|error|canceled|cancelled|nsfw|"
        r"in_progress|queued|pending|processing)\b",
        text, re.I,
    )
    return m.group(1).lower() if m else ""


def _declined_preset_id(payload: dict[str, Any]) -> str | None:
    """`preset_id` cuando el MCP pide elegir entre preset y generación literal.

    Devuelve `None` si la respuesta no es ese aviso — la forma normal de
    detectarlo es que no haya job id y sí un texto con "Preset id:".
    """
    text = payload.get("text") if isinstance(payload, dict) else None
    if not text or "declined_preset_id" not in text:
        return None
    m = re.search(r'[Pp]reset id:\s*"?([0-9a-f-]{36})"?', text)
    return m.group(1) if m else None


def _job_id_from(payload: dict[str, Any]) -> str | None:
    """Job id de una respuesta de `generate_*`.

    El MCP no devuelve JSON estructurado: contesta en prosa
    ("Submitted 1 job.\n- <uuid>  \"prompt\"") con los ajustes que aplicó por
    defecto. Se intenta primero por clave y se cae al texto.
    """
    for key in ("job_id", "jobId", "id"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    text = payload.get("text")
    if not isinstance(text, str):
        return None
    m = re.search(
        r"^-\s*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        text, re.M,
    )
    return m.group(1) if m else None


def _extract_video_url(payload: dict[str, Any]) -> str:
    """Primera URL de vídeo del resultado, sea cual sea su forma.

    La respuesta anida distinto según el modelo, así que se busca en anchura en
    vez de asumir una ruta concreta.
    """
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key in ("video_url", "url", "output_url", "result_url"):
                val = node.get(key)
                if isinstance(val, str) and val.startswith("http") and ".mp4" in val:
                    return val
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and "http" in node and ".mp4" in node:
            # `job_status` entrega la URL dentro de su texto en prosa, no en un
            # campo — el mismo motivo por el que existe `_job_state`.
            m = re.search(r"https?://\S+\.mp4[^\s\"']*", node)
            if m:
                return m.group(0)
    raise VisualGenerationError(f"resultado sin URL de vídeo: {str(payload)[:250]}")


# =============================================================================
# Generador
# =============================================================================


class HiggsfieldMCPGenerator(VisualGenerator):
    """Motion i2v vía el MCP de Higgsfield (créditos de la cuenta web)."""

    name = "higgsfield_mcp"

    def __init__(self, model: str | None = None, aspect_ratio: str | None = None) -> None:
        cfg = load_config()
        self.model = model or cfg.visual.higgsfield_mcp_model
        self.aspect_ratio = aspect_ratio or "9:16"
        self.max_wait_s = cfg.visual.higgsfield_mcp_max_wait_s

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        if first_frame_path is None or not first_frame_path.exists():
            raise VisualGenerationError(
                f"HiggsfieldMCP: beat {beat.idx} sin first_frame_path (es i2v)"
            )

        prompt = (
            f"{visual.image_prompt}. {_motion_clause(visual.motion_hint)}. "
            "Subtle, restrained motion. No text or watermarks."
        )
        dest = out_dir / f"clip-{beat.idx:02d}.mp4"

        async with HiggsfieldMCPClient(timeout_s=self.max_wait_s) as mcp:
            media_id = await mcp.upload_media(first_frame_path)
            logger.debug(f"[hf_mcp] beat {beat.idx}: media {media_id}")

            params: dict[str, Any] = {
                "model": self.model,
                "prompt": prompt,
                "aspect_ratio": self.aspect_ratio,
                #  es el rol i2v: con  a secas los modelos
                # de video lo interpretan como t2v y rechazan la referencia
                # ("mode 't2v' does not accept reference media").
                "medias": [{"value": media_id, "role": "start_image"}],
                "count": 1,
            }
            started = await mcp.call_tool("generate_video", {"params": params})

            # El MCP está pensado para un asistente interactivo: si el prompt se
            # parece a un preset del catálogo, en vez de generar devuelve un
            # aviso y espera que alguien elija. Un pipeline no puede responder
            # preguntas, así que se declina el preset y se reintenta — el propio
            # mensaje trae el `declined_preset_id` para hacerlo.
            preset_id = _declined_preset_id(started)
            if preset_id:
                logger.debug(
                    f"[hf_mcp] beat {beat.idx}: preset '{preset_id}' declinado; "
                    "genero literal"
                )
                params["declined_preset_id"] = preset_id
                started = await mcp.call_tool("generate_video", {"params": params})

            job_id = _job_id_from(started)
            if not job_id:
                raise VisualGenerationError(f"generate_video sin job id: {str(started)[:200]}")
            logger.info(f"[hf_mcp] beat {beat.idx}: job {job_id} ({self.model})")

            result = await mcp.await_job(str(job_id), max_wait_s=self.max_wait_s)
            video_url = _extract_video_url(result)

            assert mcp._client is not None
            resp = await mcp._client.get(video_url)
            if resp.status_code >= 400 or not resp.content:
                raise VisualGenerationError(f"descarga del clip falló: HTTP {resp.status_code}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        logger.info(f"[hf_mcp] beat {beat.idx} ✓ {dest.name} ({dest.stat().st_size // 1024} KB)")
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=dest,
            source=VideoSource.HIGGSFIELD_MCP,
            duration_s=float(beat.veo_duration),
        )


__all__ = ["HiggsfieldMCPClient", "HiggsfieldMCPGenerator"]
