"""CLI Typer — punto de entrada `contenido <comando>`.

Modo single-shot: el CLI invoca el pipeline orchestrator directamente con
`MemoryStateManager` (sin Redis ni worker). Para deployment con queue, usar la
API HTTP (`apps.api.main`).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import typer
from typing_extensions import Annotated

from apps.api.pipeline import run_pipeline
from core.llm_router import available_providers
from core.tts import SUPPORTED_ENGINES, detect_engine_and_voice
from core.visual.stock import available_providers as stock_providers
from orchestration.state import MemoryStateManager
from shared.config import load_config
from shared.schemas import (
    GenerationMode,
    SubtitleStyle,
    TaskState,
    VideoAspect,
    VideoParams,
    VisualStrategy,
)

app = typer.Typer(
    name="contenido",
    help="Generador de reels: fusión MoneyPrinterTurbo × reels-af",
    no_args_is_help=True,
)


# =============================================================================
# Helpers
# =============================================================================


def _entry_label(params: VideoParams) -> str:
    return params.url or params.topic or params.subject or "?"


def _run(params: VideoParams, output: Path, quiet: bool) -> None:
    """Ejecuta el pipeline sincrónicamente con MemoryStateManager (single-shot)."""
    task_id = str(uuid4())
    state = MemoryStateManager()

    # Asegurar output dir
    try:
        output.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        typer.secho(f"✗ No se pudo crear output dir {output}: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    if not quiet:
        typer.secho(
            f"\n🎬 Generando reel — task {task_id[:8]}",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo(f"   Entry: {_entry_label(params)}")
        typer.echo(f"   Mode: {params.mode.value}")
        typer.echo(f"   Aspect: {params.aspect.value}\n")

    async def _both_with_progress() -> None:
        progress_bar_holder: dict[str, typer.progressbar] = {}

        async def _poll() -> None:
            last = 0
            while True:
                try:
                    info = await state.get(task_id)
                except Exception:
                    info = None
                if info is not None:
                    bar = progress_bar_holder.get("bar")
                    if bar is not None and info.progress > last:
                        bar.update(info.progress - last)
                        last = info.progress
                    if info.state in (TaskState.COMPLETE, TaskState.FAILED):
                        # Cerrar barra al 100 si es complete
                        if bar is not None and info.state == TaskState.COMPLETE and last < 100:
                            bar.update(100 - last)
                        break
                await asyncio.sleep(0.5)

        with typer.progressbar(length=100, label="Pipeline") as bar:
            progress_bar_holder["bar"] = bar
            pipeline_task = asyncio.create_task(run_pipeline(params, task_id, state))
            poll_task = asyncio.create_task(_poll())
            try:
                await pipeline_task
            finally:
                # Dar oportunidad al poll de terminar limpiamente
                try:
                    await asyncio.wait_for(poll_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    poll_task.cancel()

    try:
        if quiet:
            asyncio.run(run_pipeline(params, task_id, state))
        else:
            asyncio.run(_both_with_progress())
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(f"\n✗ Pipeline failed: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    # Recuperar estado final
    final = asyncio.run(state.get(task_id))
    if final is None:
        typer.secho("\n✗ No se encontró estado final de la task", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if final.state == TaskState.COMPLETE:
        total_s = final.timings_s.get("total", 0.0)
        if not quiet:
            typer.secho(f"\n✓ Done in {total_s:.1f}s", fg=typer.colors.GREEN, bold=True)
            video_path = final.videos[0] if final.videos else "N/A"
            typer.echo(f"   Output: {video_path}")
            rounded = {k: round(v, 1) for k, v in final.timings_s.items() if isinstance(v, (int, float))}
            typer.echo(f"   Timings: {json.dumps(rounded, indent=2)}")
        else:
            # En modo quiet: mostrar solo el path del video
            if final.videos:
                typer.echo(final.videos[0])
    else:
        err = final.timings_s.get("error", "unknown")
        typer.secho(f"\n✗ Failed: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)


# =============================================================================
# Commands
# =============================================================================


@app.command()
def topic(
    topic: Annotated[str, typer.Argument(help="Tema del reel (premium DAG completo)")],
    mode: Annotated[GenerationMode, typer.Option(help="express o premium")] = GenerationMode.PREMIUM,
    aspect: Annotated[VideoAspect, typer.Option(help="Aspect ratio")] = VideoAspect.PORTRAIT,
    voice_name: Annotated[str, typer.Option(help="Voz TTS (vacío = default)")] = "",
    strategy: Annotated[
        VisualStrategy, typer.Option(help="stock | ia | hybrid")
    ] = VisualStrategy.HYBRID,
    use_veo: Annotated[bool, typer.Option(help="Usar Veo i2v (más caro)")] = False,
    output: Annotated[Path, typer.Option(help="Directorio de output")] = Path("./output"),
    quiet: Annotated[bool, typer.Option(help="Sin progress bar")] = False,
) -> None:
    """Generar reel desde un topic. Premium = DAG de 18 reasoners; express = 1 LLM call."""
    try:
        params = VideoParams(
            topic=topic,
            mode=mode,
            aspect=aspect,
            voice_name=voice_name,
            visual_strategy=strategy,
            use_veo=use_veo,
        )
    except Exception as e:
        typer.secho(f"✗ Parámetros inválidos: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e
    _run(params, output, quiet)


@app.command()
def article(
    url: Annotated[str, typer.Argument(help="URL del artículo")],
    mode: Annotated[GenerationMode, typer.Option(help="express o premium")] = GenerationMode.PREMIUM,
    aspect: Annotated[VideoAspect, typer.Option(help="Aspect ratio")] = VideoAspect.PORTRAIT,
    voice_name: Annotated[str, typer.Option(help="Voz TTS")] = "",
    output: Annotated[Path, typer.Option(help="Directorio de output")] = Path("./output"),
    quiet: Annotated[bool, typer.Option(help="Sin progress bar")] = False,
) -> None:
    """Generar reel desde una URL de artículo (extract → compose → render)."""
    # Validación básica de URL
    if not (url.startswith("http://") or url.startswith("https://")):
        typer.secho(
            f"✗ URL inválida: '{url}' (debe empezar con http:// o https://)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        params = VideoParams(url=url, mode=mode, aspect=aspect, voice_name=voice_name)
    except Exception as e:
        typer.secho(f"✗ Parámetros inválidos: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e
    _run(params, output, quiet)


@app.command()
def subject(
    subject: Annotated[str, typer.Argument(help="Tema simple (MPT clásico)")],
    voice_name: Annotated[str, typer.Option(help="Voz TTS")] = "en-US-AvaNeural-Female",
    aspect: Annotated[VideoAspect, typer.Option(help="Aspect ratio")] = VideoAspect.PORTRAIT,
    video_count: Annotated[int, typer.Option(min=1, max=5)] = 1,
    paragraph_number: Annotated[int, typer.Option(min=1, max=10)] = 1,
    output: Annotated[Path, typer.Option(help="Directorio de output")] = Path("./output"),
    quiet: Annotated[bool, typer.Option(help="Sin progress bar")] = False,
) -> None:
    """Generar reel rápido (legacy MPT path: 1 LLM call → script → stock → render)."""
    try:
        params = VideoParams(
            subject=subject,
            mode=GenerationMode.EXPRESS,
            voice_name=voice_name,
            aspect=aspect,
            video_count=video_count,
            paragraph_number=paragraph_number,
        )
    except Exception as e:
        typer.secho(f"✗ Parámetros inválidos: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from e
    _run(params, output, quiet)


@app.command("config-check")
def config_check() -> None:
    """Verifica que la configuración carga correctamente y reporta providers disponibles."""
    try:
        cfg = load_config()
    except Exception as e:
        typer.secho(f"✗ Falló load_config: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    typer.secho("=== Configuración cargada ===", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Env: {cfg.app.env}")
    typer.echo(f"  Default mode: {cfg.app.default_mode}")
    typer.echo(f"  Redis: {'✓' if cfg.app.enable_redis else '✗'}")
    typer.echo("")

    typer.secho("=== LLM Providers ===", fg=typer.colors.CYAN, bold=True)
    try:
        providers = list(available_providers())
    except Exception as e:
        providers = []
        typer.secho(f"  (no se pudieron enumerar: {e})", fg=typer.colors.YELLOW)
    for name in providers:
        try:
            provider_cfg = cfg.get_llm_provider(name)
            has_key = "✓" if provider_cfg.api_key else "✗"
        except Exception:
            has_key = "?"
        typer.echo(f"  {has_key} {name}")
    typer.echo("")

    typer.secho("=== TTS Engines ===", fg=typer.colors.CYAN, bold=True)
    for name in SUPPORTED_ENGINES:
        typer.echo(f"  • {name}")
    typer.echo("")

    typer.secho("=== Stock Providers ===", fg=typer.colors.CYAN, bold=True)
    try:
        for name in stock_providers():
            typer.echo(f"  ✓ {name}")
    except Exception as e:
        typer.secho(f"  (no se pudieron enumerar: {e})", fg=typer.colors.YELLOW)
    typer.echo("")

    typer.secho("=== Visual ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Veo enabled: {'✓' if cfg.visual.veo_enabled else '✗'}")
    typer.echo(f"  Default strategy: {cfg.visual.default_strategy}")

    typer.secho("\n=== Subtitles ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Default style: {cfg.subtitles.default_style}")
    typer.echo(f"  Available: {', '.join(s.value for s in SubtitleStyle)}")


@app.command("list-voices")
def list_voices(
    engine: Annotated[
        str,
        typer.Option(help=f"Uno de: {', '.join(SUPPORTED_ENGINES)}"),
    ] = "edge",
) -> None:
    """Lista voces ejemplo para un engine TTS."""
    engine = engine.strip().lower()
    if engine not in SUPPORTED_ENGINES:
        typer.secho(
            f"✗ Engine desconocido: '{engine}'. Soportados: {', '.join(SUPPORTED_ENGINES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    typer.secho(f"=== Voces para engine: {engine} ===", fg=typer.colors.CYAN, bold=True)

    # Ejemplos representativos por engine
    examples: dict[str, list[str]] = {
        "edge": [
            "en-US-AvaNeural-Female",
            "en-US-AndrewNeural-Male",
            "en-GB-SoniaNeural-Female",
            "es-MX-DaliaNeural-Female",
            "es-MX-JorgeNeural-Male",
            "es-ES-ElviraNeural-Female",
        ],
        "gemini_flash": [
            "gemini:Zephyr-Female",
            "gemini:Puck-Male",
            "gemini:Charon-Male",
            "gemini:Kore-Female",
        ],
        "azure": [
            "zh-CN-XiaoxiaoMultilingualNeural-V2-Female",
            "en-US-AvaMultilingualNeural-V2-Female",
            "azure:en-US-JennyNeural-Female",
        ],
        "mimo": [
            "mimo:冰糖-Female",
            "mimo:晨光-Male",
        ],
        "siliconflow": [
            "siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex-Male",
            "siliconflow:FunAudioLLM/CosyVoice2-0.5B:anna-Female",
        ],
        "silent": [
            "no-voice",
        ],
    }

    for voice in examples.get(engine, []):
        # Mostrar parsed engine/voice resultado de detect_engine_and_voice
        try:
            detected_engine, parsed = detect_engine_and_voice(voice)
            tag = " ✓" if detected_engine == engine else f" (detected as {detected_engine})"
        except Exception:
            tag = ""
            parsed = voice
        typer.echo(f"  • {voice}{tag}")
        if parsed and parsed != voice:
            typer.echo(f"      → {parsed}")


@app.command()
def task(
    task_id: Annotated[str, typer.Argument(help="ID de la task a consultar")],
) -> None:
    """Query state de una task. Requiere Redis habilitado (CLI no persiste tasks)."""
    cfg = load_config()
    if not cfg.app.enable_redis:
        typer.secho(
            "✗ Redis no está habilitado. El CLI ejecuta tasks en memoria efímera; "
            "para consultar tasks, habilita Redis y usa la API HTTP.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    # Si Redis está habilitado, intentar consultar
    try:
        from orchestration.state import get_state_manager

        async def _fetch() -> None:
            state = get_state_manager(cfg)
            info = await state.get(task_id)
            if info is None:
                typer.secho(f"✗ Task no encontrada: {task_id}", fg=typer.colors.RED, err=True)
                raise typer.Exit(code=1)
            typer.secho(f"=== Task {task_id} ===", fg=typer.colors.CYAN, bold=True)
            typer.echo(f"  State: {info.state.name}")
            typer.echo(f"  Mode: {info.mode.value if info.mode else '?'}")
            typer.echo(f"  Progress: {info.progress}%")
            if info.videos:
                typer.echo(f"  Videos: {', '.join(info.videos)}")
            if info.timings_s:
                typer.echo(f"  Timings: {json.dumps(info.timings_s, indent=2)}")

        asyncio.run(_fetch())
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(f"✗ Error consultando task: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e


if __name__ == "__main__":
    app()
