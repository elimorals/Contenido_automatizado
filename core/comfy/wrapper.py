"""Subprocess wrapper sobre el binario `comfy` (comfy-cli).

Cada función es async + non-blocking. Captura stdout/stderr. Lanza
`ComfyCLINotInstalled` si el binario no está en PATH (instalación previa).

No conoce nada del protocolo del server — solo orquesta el binario.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from loguru import logger

from shared.config import load_config


class ComfyCLIError(RuntimeError):
    """Error genérico del comfy-cli (exit code != 0)."""


class ComfyCLINotInstalled(ComfyCLIError):
    """El binario `comfy` no está en PATH y no se pudo encontrar."""


# =============================================================================
# Resolución del binario
# =============================================================================


def check_binary(binary_path: str | None = None) -> str:
    """Devuelve el path absoluto del binario `comfy`.

    Resolución:
    1. `binary_path` argumento explícito
    2. `cfg.visual.comfyui.cli_binary_path` del config
    3. `which comfy`

    Lanza `ComfyCLINotInstalled` si no se encuentra.
    """
    candidate = binary_path or load_config().visual.comfyui.cli_binary_path or "comfy"
    if "/" in candidate or "\\" in candidate:
        # Path absoluto/relativo
        if Path(candidate).is_file():
            return candidate
        raise ComfyCLINotInstalled(f"comfy binary no existe: {candidate}")
    found = shutil.which(candidate)
    if not found:
        raise ComfyCLINotInstalled(
            f"`{candidate}` no encontrado en PATH. Instala: pip install comfy-cli"
        )
    return found


# =============================================================================
# Runner
# =============================================================================


async def _run(
    args: list[str],
    *,
    timeout_s: float = 600.0,
    cwd: Path | None = None,
) -> tuple[int, str, str]:
    """Ejecuta `args` y devuelve (returncode, stdout, stderr)."""
    logger.debug(f"[comfy-cli] {' '.join(args)}")
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
        raise ComfyCLIError(f"comfy-cli timeout tras {timeout_s}s")
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def _run_or_raise(args: list[str], *, timeout_s: float = 600.0) -> str:
    code, stdout, stderr = await _run(args, timeout_s=timeout_s)
    if code != 0:
        raise ComfyCLIError(
            f"comfy-cli exit {code} ({' '.join(args[1:3])}...): {(stderr or stdout)[:400]}"
        )
    return stdout


# =============================================================================
# Comandos de alto nivel
# =============================================================================


async def cli_status() -> dict:
    """Devuelve `{installed, running, workspace, version}`.

    No usa `comfy status` (que está bugged en algunas versiones); compone con
    `comfy which` + check del binario.
    """
    info: dict[str, str | bool | None] = {
        "installed": False,
        "binary": None,
        "workspace": None,
        "version": None,
    }
    try:
        binary = check_binary()
        info["installed"] = True
        info["binary"] = binary
    except ComfyCLINotInstalled:
        return info

    # `comfy which` → workspace path
    try:
        out = await _run_or_raise([binary, "which"], timeout_s=10.0)
        info["workspace"] = out.strip().splitlines()[-1] if out.strip() else None
    except ComfyCLIError as e:
        logger.debug(f"[comfy-cli] which falló: {e}")

    # `comfy --version`
    try:
        out = await _run_or_raise([binary, "--version"], timeout_s=10.0)
        info["version"] = out.strip()
    except ComfyCLIError:
        pass

    return info


async def cli_install(
    *,
    workspace: str | None = None,
    cuda: bool = True,
    timeout_s: float = 1800.0,
) -> None:
    """Instala ComfyUI vía `comfy install`. Tardísimo (15-30 min).

    Si `workspace` no se pasa, comfy-cli usa su default (`~/comfy/`).
    """
    binary = check_binary()
    args = [binary, "install"]
    if workspace:
        args += ["--workspace", workspace]
    if not cuda:
        args.append("--cpu")
    await _run_or_raise(args, timeout_s=timeout_s)


async def cli_launch(
    *,
    workspace: str | None = None,
    port: int = 8188,
    background: bool = True,
    timeout_s: float = 60.0,
) -> int | None:
    """Lanza el server ComfyUI. Si `background=True`, retorna PID y desvincula.

    Para foreground: bloquea hasta que el server muera.
    """
    binary = check_binary()
    args = [binary, "launch"]
    if workspace:
        args += ["--workspace", workspace]
    args += ["--", "--port", str(port)]
    if background:
        args.append("--background")

    if not background:
        # Foreground: bloquea
        await _run_or_raise(args, timeout_s=timeout_s)
        return None

    # Background: spawn y devuelve PID
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Esperar 2s para verificar que no murió inmediatamente
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        # Murió rápido → es error
        stdout = (await proc.stdout.read()).decode() if proc.stdout else ""
        stderr = (await proc.stderr.read()).decode() if proc.stderr else ""
        raise ComfyCLIError(f"comfy launch murió: {(stderr or stdout)[:400]}")
    except asyncio.TimeoutError:
        # Sigue corriendo → el server arrancó OK
        return proc.pid


# =============================================================================
# Modelos / LoRAs
# =============================================================================


async def list_models(model_type: str = "checkpoints") -> list[str]:
    """`comfy model list --type <type>`. Devuelve filenames."""
    binary = check_binary()
    args = [binary, "model", "list", "--type", model_type]
    try:
        out = await _run_or_raise(args, timeout_s=30.0)
        # Output es plain text (uno por línea típicamente)
        return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.startswith("─")]
    except ComfyCLIError:
        return []


async def list_loras() -> list[str]:
    return await list_models("loras")


async def download_model(
    url: str,
    *,
    model_type: str = "loras",
    filename: str | None = None,
    timeout_s: float = 1800.0,
) -> None:
    """`comfy model download --url <url> --type <type> [--filename <name>]`.

    Soporta CivitAI URLs, Hugging Face URLs y URLs directas (auto-detecta).
    """
    binary = check_binary()
    args = [binary, "model", "download", "--url", url, "--relative-path", f"models/{model_type}"]
    if filename:
        args += ["--filename", filename]
    await _run_or_raise(args, timeout_s=timeout_s)


async def download_lora(url: str, filename: str | None = None) -> None:
    """Shortcut: download a una LoRA."""
    await download_model(url, model_type="loras", filename=filename)


# =============================================================================
# Custom nodes
# =============================================================================


async def install_custom_node(name_or_url: str, *, timeout_s: float = 300.0) -> None:
    """`comfy node install <name_or_url>`. Acepta nombre del manager o git URL."""
    binary = check_binary()
    args = [binary, "node", "install", name_or_url]
    await _run_or_raise(args, timeout_s=timeout_s)


async def list_installed_custom_nodes() -> list[str]:
    """`comfy node show installed`."""
    binary = check_binary()
    try:
        out = await _run_or_raise(
            [binary, "node", "show", "installed"], timeout_s=30.0
        )
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except ComfyCLIError:
        return []


# =============================================================================
# Workflows registrados
# =============================================================================


async def list_workflows() -> list[str]:
    """Lista workflows registrados localmente en `workflows/index.json`.

    No usa comfy-cli (no maneja workflows propios) — lee nuestro registry.
    """
    from core.visual.generation.comfy_workflows import load_registry

    return list(load_registry().keys())
