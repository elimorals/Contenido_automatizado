"""Fallback vía CLI oficial `higgsfield` (subprocess).

La REST API directa es la ruta principal (rápida, sin shell overhead).
Si falla por rate-limit, 5xx, o timeout, ESTE módulo intenta el mismo
job vía la CLI oficial — que usa su propia auth (device-flow persistente
en `~/.config/higgsfield/credentials.json`) y maneja retries/polling
internamente.

Pattern (de las skills oficiales):
    higgsfield generate create seedance_2_0 \\
        --prompt "..." \\
        --start-image ./first_frame.jpg \\
        --duration 5 \\
        --wait --json

`--json` produce salida parseable en stdout. `--wait` bloquea hasta que
el job termine (success o error terminal).

Requisitos:
- Binario `higgsfield` instalado y en PATH (o config.cli_binary_path absoluto)
- Auth previamente hecha: `higgsfield auth login` (device-flow)

Política:
- Opt-in vía config.cli_fallback_enabled
- NUNCA reemplaza la ruta REST; siempre es post-falla
- Si CLI también falla, lanza CLIFallbackError → orchestrator cae a Veo/ken-burns
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from loguru import logger

from shared.config import HiggsfieldConfig, load_config


class CLIFallbackError(RuntimeError):
    """Fallo en la ruta CLI (binario ausente, auth fallida, job rechazado)."""


class CLINotInstalledError(CLIFallbackError):
    """El binario `higgsfield` no está en PATH ni en cli_binary_path."""


def _check_binary(cfg: HiggsfieldConfig) -> str:
    """Resuelve el path absoluto del binario `higgsfield`.

    Lanza `CLINotInstalledError` si no se encuentra.
    """
    candidate = cfg.cli_binary_path or "higgsfield"
    if "/" in candidate or "\\" in candidate:
        if Path(candidate).is_file():
            return candidate
        raise CLINotInstalledError(
            f"CLI binary no existe en {candidate}"
        )
    found = shutil.which(candidate)
    if not found:
        raise CLINotInstalledError(
            f"`{candidate}` no encontrado en PATH. "
            "Install: curl -fsSL https://raw.githubusercontent.com/higgsfield-ai/cli/main/install.sh | sh"
        )
    return found


async def _run_cli(
    args: list[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Ejecuta el binario y captura stdout/stderr.

    Returns: (returncode, stdout_text, stderr_text).
    Lanza `asyncio.TimeoutError` si excede `timeout_s`.
    """
    logger.debug(f"[higgsfield-cli] {' '.join(args)}")
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def generate_video_via_cli(
    *,
    prompt: str,
    first_frame_path: Path,
    duration_s: int,
    out_path: Path,
    model: str | None = None,
    cfg: HiggsfieldConfig | None = None,
) -> Path:
    """Genera un video usando la CLI oficial y descarga al `out_path`.

    Args:
        prompt: Prompt enriquecido (ya pasado por `augment_dop_prompt`).
        first_frame_path: Imagen local — la CLI la sube automáticamente.
        duration_s: 2-15 según el modelo (validar antes de llamar).
        out_path: Path destino del MP4.
        model: Override del modelo. Si None, usa `cfg.cli_default_video_model`.
        cfg: HiggsfieldConfig. Si None, carga el global.

    Returns:
        Path al MP4 descargado.

    Raises:
        CLINotInstalledError: binario ausente.
        CLIFallbackError: cualquier otro fallo (auth, job rejected, network).
    """
    cfg = cfg or load_config().visual.higgsfield
    binary = _check_binary(cfg)
    chosen_model = model or cfg.cli_default_video_model
    if not first_frame_path.exists():
        raise CLIFallbackError(f"first_frame_path no existe: {first_frame_path}")

    # Las skills documentan estos flags como los canónicos (--wait + --json).
    args = [
        binary, "generate", "create", chosen_model,
        "--prompt", prompt,
        "--start-image", str(first_frame_path),
        "--duration", str(int(duration_s)),
        "--wait", "--json",
    ]

    try:
        code, stdout, stderr = await _run_cli(args, timeout_s=cfg.cli_timeout_s)
    except asyncio.TimeoutError as e:
        raise CLIFallbackError(
            f"CLI timeout tras {cfg.cli_timeout_s}s — modelo: {chosen_model}"
        ) from e

    if code != 0:
        raise CLIFallbackError(
            f"CLI exit {code}: {(stderr or stdout)[:400]}"
        )

    # Parsear stdout JSON — debería contener el URL del video resultante.
    try:
        payload = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        raise CLIFallbackError(
            f"CLI stdout no es JSON: {stdout[:400]}"
        ) from e

    url = _extract_video_url(payload)
    if not url:
        raise CLIFallbackError(
            f"CLI response sin video URL: {str(payload)[:400]}"
        )

    # Descarga (httpx async)
    import httpx  # import local — solo aquí lo necesitamos
    try:
        async with httpx.AsyncClient(timeout=300.0) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            out_path.write_bytes(r.content)
    except httpx.HTTPError as e:
        raise CLIFallbackError(f"download {url} falló: {e}") from e

    return out_path


def _extract_video_url(payload: dict) -> str | None:
    """Busca el URL del video en respuestas CLI con varias formas conocidas."""
    # Forma 1: payload directo con `result_url` o `media_url`
    for key in ("result_url", "media_url", "url", "video_url"):
        v = payload.get(key)
        if isinstance(v, str) and v.startswith(("http", "data:")):
            return v
    # Forma 2: payload.results[0].url (JobSet shape)
    results = payload.get("results") or payload.get("jobs") or []
    if isinstance(results, list) and results:
        first = results[0] or {}
        for key in ("url", "media_url", "result_url"):
            v = (first or {}).get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        # Anidado: results.raw.url
        raw = (first or {}).get("results", {}).get("raw", {})
        if isinstance(raw, dict) and raw.get("url"):
            return raw["url"]
    # Forma 3: payload.video.url
    video = payload.get("video")
    if isinstance(video, dict) and video.get("url"):
        return video["url"]
    return None


async def is_cli_available(cfg: HiggsfieldConfig | None = None) -> bool:
    """Quick check: ¿está el binario disponible Y el usuario autenticado?

    Útil al inicio para decidir si registrar el fallback o no.
    """
    cfg = cfg or load_config().visual.higgsfield
    try:
        binary = _check_binary(cfg)
    except CLINotInstalledError:
        return False
    try:
        code, _, _ = await _run_cli(
            [binary, "account", "status"], timeout_s=10.0,
        )
        return code == 0
    except (asyncio.TimeoutError, FileNotFoundError):
        return False
