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


# =============================================================================
# Editorial commands (portados de corredor-content)
# =============================================================================


@app.command()
def plan(
    ideas: Annotated[int, typer.Option(min=1, max=20, help="Número de ideas a generar")] = 7,
    provider: Annotated[
        str, typer.Option(help="LLM provider (override del default)")
    ] = "",
) -> None:
    """Genera un plan editorial semanal con N ideas y lo guarda en out/plans/.

    Después: edita out/plans/plan-YYYY-Www.json y marca `approved: true` en
    las ideas que quieras producir, luego corre `contenido produce-week`.
    """
    try:
        from core.editorial import generate_plan, load_editorial, validate_plan
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ No se pudo importar core.editorial: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    async def _run_plan() -> None:
        registry = load_editorial()
        provider_name = provider or None
        the_plan, out_path, _cost = await generate_plan(
            n_ideas=ideas, registry=registry, provider_name=provider_name,
        )
        vr = validate_plan(the_plan, registry)
        typer.secho(
            f"\n✓ Plan {the_plan.week} con {len(the_plan.ideas)} ideas → {out_path}",
            fg=typer.colors.GREEN, bold=True,
        )
        # Pillar breakdown
        typer.echo("  Distribución por pilar:")
        for pillar_id, count in sorted(the_plan.by_pillar().items()):
            typer.echo(f"    • {pillar_id}: {count}")
        if vr.errors:
            typer.secho(f"  ✗ {len(vr.errors)} errores:", fg=typer.colors.RED)
            for e in vr.errors[:5]:
                typer.echo(f"    {e}")
        if vr.warnings:
            typer.secho(f"  ⚠ {len(vr.warnings)} warnings:", fg=typer.colors.YELLOW)
            for w in vr.warnings[:5]:
                typer.echo(f"    {w}")
        typer.echo(
            f"\nSiguiente paso: edita {out_path} y marca `\"approved\": true` "
            "en las ideas que quieras producir.\nLuego: contenido produce-week"
        )

    try:
        asyncio.run(_run_plan())
    except typer.Exit:
        raise
    except Exception as e:
        typer.secho(f"\n✗ plan falló: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e


@app.command("plan-show")
def plan_show(
    week: Annotated[str, typer.Option(help="Semana ISO (default: actual)")] = "",
) -> None:
    """Muestra el plan editorial de una semana con su estado de aprobación."""
    try:
        from core.editorial import load_plan
        from core.editorial.plan import iso_week
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ Editorial no disponible: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    target_week = week or iso_week()
    try:
        the_plan = load_plan(target_week)
    except FileNotFoundError:
        typer.secho(
            f"✗ No hay plan para {target_week}. Crealo con: contenido plan",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1) from None

    typer.secho(f"=== Plan {target_week} ({len(the_plan.ideas)} ideas) ===",
                fg=typer.colors.CYAN, bold=True)
    for i, idea in enumerate(the_plan.ideas, 1):
        mark = "✓" if idea.approved else "○"
        color = typer.colors.GREEN if idea.approved else typer.colors.WHITE
        typer.secho(f"  {mark} [{i}] {idea.id} ({idea.pillar}, {idea.audience})",
                    fg=color)
        typer.echo(f"      {idea.title}")
        typer.echo(f"      hook: {idea.hook[:80]}{'...' if len(idea.hook) > 80 else ''}")
        typer.echo(f"      platforms: {', '.join(p.value for p in idea.platforms)}")
    approved_n = len(the_plan.approved_ideas())
    typer.echo(f"\n  Aprobadas: {approved_n}/{len(the_plan.ideas)}")
    if approved_n > 0:
        typer.echo("  Siguiente paso: contenido produce-week")


@app.command("produce-week")
def produce_week(
    week: Annotated[str, typer.Option(help="Semana ISO (default: actual)")] = "",
    mode: Annotated[GenerationMode, typer.Option(help="Modo por idea")] = GenerationMode.PREMIUM,
    aspect: Annotated[VideoAspect, typer.Option(help="Aspect ratio")] = VideoAspect.PORTRAIT,
    use_veo: Annotated[bool, typer.Option(help="Usar Veo i2v")] = False,
    output: Annotated[Path, typer.Option(help="Output dir")] = Path("./output"),
    quiet: Annotated[bool, typer.Option(help="Sin progress")] = False,
) -> None:
    """Produce TODAS las ideas aprobadas del plan editorial de la semana.

    Por cada idea:
      - Construye VideoParams según entry_type (topic/url/subject)
      - Ejecuta el pipeline DAG completo
      - Persiste task_id + output_path + cost_usd de vuelta en el plan
    """
    try:
        from core.editorial import load_plan, save_plan
        from core.editorial.plan import iso_week
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ Editorial no disponible: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    target_week = week or iso_week()
    try:
        the_plan = load_plan(target_week)
    except FileNotFoundError:
        typer.secho(f"✗ No hay plan para {target_week}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    approved = the_plan.approved_ideas()
    if not approved:
        typer.secho(
            "✗ Ninguna idea aprobada. Edita el plan y marca `approved: true`.",
            fg=typer.colors.YELLOW, err=True,
        )
        raise typer.Exit(code=1)

    typer.secho(
        f"\n🎬 Produciendo {len(approved)} ideas aprobadas de {target_week}",
        fg=typer.colors.GREEN, bold=True,
    )

    for i, idea in enumerate(approved, 1):
        typer.echo(f"\n[{i}/{len(approved)}] {idea.id} ({idea.pillar})")
        # Mapear entry_type → VideoParams
        params_kwargs: dict = {
            "mode": mode,
            "aspect": aspect,
            "use_veo": use_veo,
        }
        if idea.entry_type.value == "url":
            params_kwargs["url"] = idea.entry_value
        elif idea.entry_type.value == "topic":
            params_kwargs["topic"] = idea.entry_value
        else:
            params_kwargs["subject"] = idea.entry_value
        try:
            params = VideoParams(**params_kwargs)
        except Exception as e:  # noqa: BLE001
            typer.secho(f"  ✗ params inválidos: {e}", fg=typer.colors.RED, err=True)
            continue
        try:
            _run(params, output, quiet)
        except typer.Exit:
            typer.secho(f"  ✗ idea {idea.id} falló — continuando con siguiente", fg=typer.colors.RED)
            continue
        # NOTE: persistir task_id/output/cost en el plan requiere capturar el TaskInfo
        # final; lo haremos en una iteración futura cuando _run devuelva el state.

    typer.secho(
        f"\n✓ Semana {target_week} procesada ({len(approved)} ideas)",
        fg=typer.colors.GREEN, bold=True,
    )


@app.command("brand-check")
def brand_check() -> None:
    """Inspecciona la capa editorial cargada (brand-voice, pillars, facts, etc)."""
    try:
        from core.editorial import facts_anti_hallucination_block, load_editorial
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ Editorial no disponible: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    r = load_editorial()
    typer.secho("=== Editorial Layer ===", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"  Root: {r.root}")
    typer.echo(f"  Brand voice: {'✓' if r.brand_voice_md else '✗'} ({len(r.brand_voice_md)} chars)")
    typer.echo(f"  Pillars ({len(r.pillars)}): {', '.join(r.pillars.keys()) or '—'}")
    typer.echo(f"  Audiences ({len(r.audiences)}): {', '.join(r.audiences.keys()) or '—'}")
    typer.echo(
        f"  Platforms ({len(r.platforms)}): "
        f"{', '.join(p.value for p in r.platforms.keys()) or '—'}"
    )
    f = r.facts
    typer.echo(
        f"  Facts: {len(f.verified_facts)} facts, "
        f"{len(f.verified_people)} people, {len(f.verified_studies)} studies"
    )
    typer.echo(f"  Local events: {len(r.local_events)}")
    typer.echo("\n=== Anti-hallucination block (first 400 chars) ===")
    typer.echo(facts_anti_hallucination_block(r)[:400])


# =============================================================================
# ComfyUI subcommands — `contenido comfy ...`
# =============================================================================

comfy_app = typer.Typer(
    name="comfy",
    help="ComfyUI: install, launch, status, lora, workflow, test",
    no_args_is_help=True,
)
app.add_typer(comfy_app, name="comfy")


@comfy_app.command("status")
def comfy_status() -> None:
    """Estado del binario comfy-cli + server ComfyUI."""
    try:
        from core.comfy import cli_status
        from core.visual.generation.comfy import is_comfyui_available
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ Imports fallaron: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    async def _run() -> None:
        cli_info = await cli_status()
        cfg = load_config().visual.comfyui
        typer.secho("=== comfy-cli ===", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  Installed: {'✓' if cli_info.get('installed') else '✗'}")
        if cli_info.get("binary"):
            typer.echo(f"  Binary:    {cli_info['binary']}")
        if cli_info.get("workspace"):
            typer.echo(f"  Workspace: {cli_info['workspace']}")
        if cli_info.get("version"):
            typer.echo(f"  Version:   {cli_info['version']}")
        typer.secho("\n=== ComfyUI Server ===", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  URL:       {cfg.server_url}")
        typer.echo(f"  Enabled:   {'✓' if cfg.enabled else '✗'}")
        alive = await is_comfyui_available()
        typer.echo(f"  Alive:     {'✓' if alive else '✗'}")
        if cfg.tenants:
            typer.secho("\n=== Tenants registrados ===", fg=typer.colors.CYAN, bold=True)
            for tid, entry in cfg.tenants.items():
                typer.echo(
                    f"  • {tid}: workflow={entry.primary_workflow_id or '—'}, "
                    f"lora={entry.lora_name or '—'}@{entry.lora_strength:.2f}"
                )

    asyncio.run(_run())


@comfy_app.command("install")
def comfy_install_cmd(
    workspace: Annotated[str, typer.Option(help="Path al workspace destino")] = "",
    cpu_only: Annotated[bool, typer.Option(help="Sin CUDA (lento, solo dev)")] = False,
) -> None:
    """Descarga e instala ComfyUI vía comfy-cli (tarda 15-30 min)."""
    try:
        from core.comfy import cli_install
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    typer.secho(
        "⏳ Instalando ComfyUI... (15-30 min, depende del ancho de banda)",
        fg=typer.colors.YELLOW,
    )
    try:
        asyncio.run(cli_install(workspace=workspace or None, cuda=not cpu_only))
        typer.secho("✓ ComfyUI instalado", fg=typer.colors.GREEN, bold=True)
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ install falló: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e


@comfy_app.command("launch")
def comfy_launch_cmd(
    workspace: Annotated[str, typer.Option(help="Workspace")] = "",
    port: Annotated[int, typer.Option(help="Puerto del server")] = 8188,
    background: Annotated[bool, typer.Option(help="Spawn en background")] = True,
) -> None:
    """Lanza el server ComfyUI."""
    try:
        from core.comfy import cli_launch
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    try:
        pid = asyncio.run(
            cli_launch(workspace=workspace or None, port=port, background=background)
        )
        if pid is not None:
            typer.secho(
                f"✓ ComfyUI arrancando en background (PID {pid}) → http://127.0.0.1:{port}",
                fg=typer.colors.GREEN, bold=True,
            )
        else:
            typer.echo("ComfyUI cerrado (foreground)")
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ launch falló: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e


@comfy_app.command("lora")
def comfy_lora_cmd(
    action: Annotated[str, typer.Argument(help="list | download")],
    url: Annotated[str, typer.Option(help="URL del LoRA (download)")] = "",
    filename: Annotated[str, typer.Option(help="Nombre destino (download)")] = "",
) -> None:
    """Gestiona LoRAs: list (instalados) o download <url>."""
    try:
        from core.comfy import download_lora, list_loras
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    async def _run() -> None:
        if action == "list":
            loras = await list_loras()
            if not loras:
                typer.echo("(sin LoRAs instaladas)")
                return
            typer.secho(f"=== LoRAs ({len(loras)}) ===", fg=typer.colors.CYAN, bold=True)
            for name in loras:
                typer.echo(f"  • {name}")
        elif action == "download":
            if not url:
                typer.secho("✗ --url requerido para download", fg=typer.colors.RED)
                raise typer.Exit(code=2)
            typer.echo(f"⏳ Descargando LoRA desde {url}...")
            await download_lora(url, filename=filename or None)
            typer.secho("✓ LoRA descargada", fg=typer.colors.GREEN, bold=True)
        else:
            typer.secho(
                f"✗ acción desconocida '{action}'. Usa: list | download",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)

    asyncio.run(_run())


@comfy_app.command("workflow")
def comfy_workflow_cmd(
    action: Annotated[str, typer.Argument(help="list | show <id>")] = "list",
    workflow_id: Annotated[str, typer.Argument(help="ID del workflow (show)")] = "",
) -> None:
    """Inspecciona workflows registrados en workflows/index.json."""
    try:
        from core.visual.generation.comfy_workflows import get_workflow_spec, load_registry
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    if action == "list":
        reg = load_registry()
        if not reg:
            typer.echo("(sin workflows registrados — agrega entries en workflows/index.json)")
            return
        typer.secho(
            f"=== Workflows registrados ({len(reg)}) ===",
            fg=typer.colors.CYAN, bold=True,
        )
        for wid, spec in reg.items():
            typer.echo(f"  • {wid}: {spec.name}")
            typer.echo(
                f"      kind={spec.kind.value}, output={spec.output_type.value}, "
                f"~{spec.estimated_seconds}s, {spec.estimated_vram_gb}GB VRAM"
            )
    elif action == "show":
        if not workflow_id:
            typer.secho("✗ workflow_id requerido", fg=typer.colors.RED)
            raise typer.Exit(code=2)
        try:
            spec = get_workflow_spec(workflow_id)
        except KeyError as e:
            typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from e
        typer.secho(f"=== {spec.name} ===", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  ID:         {spec.id}")
        typer.echo(f"  Kind:       {spec.kind.value}")
        typer.echo(f"  Output:     {spec.output_type.value}")
        typer.echo(f"  JSON:       {spec.json_path}")
        typer.echo(f"  Output nodes: {spec.output_nodes}")
        typer.echo(f"  Checkpoints: {spec.required_checkpoints}")
        typer.echo(f"  LoRAs:       {spec.required_loras}")
        typer.echo(f"  Custom nodes: {spec.required_custom_nodes}")
        typer.echo(f"  Estimated:  {spec.estimated_seconds}s, {spec.estimated_vram_gb}GB VRAM")
        typer.echo("\n  Parámetros mapeados:")
        pmap = spec.parameters
        for field_name in ("prompt", "seed", "width", "height", "lora_name", "lora_strength"):
            v = getattr(pmap, field_name, None)
            if v:
                typer.echo(f"    {field_name:18s} → {v}")
    else:
        typer.secho(f"✗ acción desconocida '{action}'", fg=typer.colors.RED)
        raise typer.Exit(code=2)


@comfy_app.command("test")
def comfy_test_cmd(
    workflow_id: Annotated[str, typer.Argument(help="ID del workflow a probar")],
    prompt: Annotated[
        str, typer.Option(help="Prompt de prueba")
    ] = "a beautiful mountain landscape at golden hour, cinematic",
    output: Annotated[Path, typer.Option(help="Output dir")] = Path("./output"),
) -> None:
    """Ejecuta UN workflow end-to-end con un prompt de prueba.

    Útil para validar que el workflow está bien parametrizado y el server responde.
    """
    try:
        from core.visual.generation.comfy import ComfyUIGenerator
        from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    async def _run() -> None:
        beat = Beat(
            idx=0, role=BeatRole.HOOK,
            text="test", target_duration_s=4.0, veo_duration=4,
        )
        visual = BeatVisual(
            image_prompt=prompt, motion_hint=MotionHint.STATIC, visual_anchor="test",
        )
        gen = ComfyUIGenerator(workflow_id=workflow_id)
        # Forzar enabled para el test
        gen.cfg = gen.cfg.model_copy(update={"enabled": True})
        typer.echo(f"⏳ Ejecutando workflow '{workflow_id}'...")
        try:
            artifact = await gen.generate(
                beat=beat, visual=visual, content_mode="general",
                out_dir=output,
            )
            typer.secho(
                f"\n✓ Generado: {artifact.first_frame_path or artifact.video_path}",
                fg=typer.colors.GREEN, bold=True,
            )
            typer.echo(f"  Source: {artifact.source.value}")
        except Exception as e:  # noqa: BLE001
            typer.secho(f"✗ test falló: {e}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1) from e

    asyncio.run(_run())


@comfy_app.command("models")
def comfy_models_cmd(
    model_type: Annotated[
        str, typer.Argument(help="checkpoints|loras|vae|controlnet|embeddings|...")
    ] = "checkpoints",
) -> None:
    """Lista modelos instalados (consulta al server via HTTP, no comfy-cli)."""
    try:
        from core.visual.generation.comfy_client import ComfyClient
    except Exception as e:  # noqa: BLE001
        typer.secho(f"✗ {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from e

    async def _run() -> None:
        async with ComfyClient() as cli:
            models = await cli.list_models(model_type)
        if not models:
            typer.echo(f"(sin {model_type} instalados, o server no responde)")
            return
        typer.secho(
            f"=== {model_type} ({len(models)}) ===",
            fg=typer.colors.CYAN, bold=True,
        )
        for m in models:
            typer.echo(f"  • {m}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
