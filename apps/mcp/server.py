#!/usr/bin/env python3
"""MCP server para contenido — expone el pipeline de reels a agentes (ADR-021).

Capa FINA sobre `apps/mcp/service.py` (que tiene toda la lógica testeable). Corre
in-process: los tools llaman las funciones del pipeline directamente, sin hop HTTP.

Tools:
  - contenido_analyze_reference : analiza un video (TikTok/Reel/YT) → brief (read-only)
  - contenido_start_reel        : agenda un reel en background → task_id (gasta dinero)
  - contenido_get_task          : estado/progreso/resultado de un job (read-only)
  - contenido_list_tasks        : tasks recientes (read-only)
  - contenido_list_voices       : motores TTS disponibles (read-only)

Gate de costo: el long-form ($16–80) NO se expone — sólo reels. Ejecutar:
    python -m apps.mcp.server      (transport stdio)
Requiere el extra: pip install -e '.[mcp]'.
"""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from apps.mcp import service
from core.reference import ReferenceAnalysisError, analyze_reference
from orchestration.state import get_state_manager
from shared.config import load_config

mcp = FastMCP("contenido_mcp")

# Estado compartido del proceso. `get_state_manager` da Redis si está configurado
# (compartiendo jobs con la API/worker) o memoria en otro caso.
_STATE = get_state_manager(load_config())
_JOBS: set = set()


# =============================================================================
# Input models
# =============================================================================


class AnalyzeReferenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ...,
        description="URL del video de referencia (TikTok/Reel/YouTube/etc.)",
        min_length=4,
    )
    reel_target_s: float = Field(
        25.0,
        description="Duración objetivo de TU reel (para sugerir nº de beats).",
        gt=0.5,
        le=120,
    )


class StartReelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str | None = Field(None, description="Tema del reel (premium DAG). Una sola entrada.")
    url: str | None = Field(None, description="URL de artículo (article path). Una sola entrada.")
    subject: str | None = Field(None, description="Tema simple (express/legacy). Una sola entrada.")
    reference_url: str | None = Field(
        None, description="Opcional: URL de video de referencia para informar pacing/hook."
    )
    mode: str = Field("premium", description="'express' (barato) o 'premium' (DAG). NO long_form.")
    aspect: str = Field("9:16", description="'9:16' | '16:9' | '1:1'.")
    strategy: str = Field("hybrid", description="'stock' | 'ia' | 'hybrid'.")
    voice_name: str = Field("", description="Voz TTS (vacío = default del modo).")
    use_veo: bool = Field(False, description="Usar Veo i2v (más caro, más cinematográfico).")


class TaskIdInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task_id: str = Field(..., description="ID devuelto por contenido_start_reel.", min_length=1)


class ListTasksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(10, description="Máximo de tasks a devolver.", ge=1, le=100)


# =============================================================================
# Tools
# =============================================================================


@mcp.tool(
    name="contenido_analyze_reference",
    annotations={
        "title": "Analizar video de referencia",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def contenido_analyze_reference(params: AnalyzeReferenceInput) -> dict[str, Any]:
    """Analiza un video de referencia y devuelve su brief (pacing, hook, estructura).

    Read-only: descarga + transcribe + detecta cortes, NO genera nada. Úsalo para
    "haz un reel con el ritmo de este video" — luego pasa la misma url como
    `reference_url` a contenido_start_reel.

    Args:
        params: url + reel_target_s.

    Returns:
        dict: ReferenceBrief (url, duration_s, hook_style, wpm, avg_shot_s,
        suggested_beats, target_wpm, transcript, ...) o {"error": "..."}.
    """
    try:
        brief = await analyze_reference(params.url, reel_target_s=params.reel_target_s)
    except ReferenceAnalysisError as e:
        return {"error": str(e)}
    return brief.model_dump()


@mcp.tool(
    name="contenido_start_reel",
    annotations={
        "title": "Generar reel (background job)",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def contenido_start_reel(params: StartReelInput) -> dict[str, Any]:
    """Agenda la generación de un reel en background y devuelve un task_id.

    GASTA dinero (LLM + media). Provee EXACTAMENTE una entrada: topic | url | subject.
    El long-form NO se expone aquí (gate de costo). No bloquea: sondea con
    contenido_get_task(task_id).

    Returns:
        dict: {task_id, state, mode, cost_note, hint} o {"error": "..."}.
    """
    try:
        reel_params = service.build_reel_params(
            topic=params.topic,
            url=params.url,
            subject=params.subject,
            reference_url=params.reference_url,
            mode=params.mode,
            aspect=params.aspect,
            strategy=params.strategy,
            voice_name=params.voice_name,
            use_veo=params.use_veo,
        )
    except ValueError as e:
        return {"error": str(e)}
    return await service.start_reel(_STATE, _JOBS, reel_params)


@mcp.tool(
    name="contenido_get_task",
    annotations={
        "title": "Estado de un job de reel",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def contenido_get_task(params: TaskIdInput) -> dict[str, Any]:
    """Devuelve estado/progreso/resultado de un job.

    Returns:
        dict: {task_id, state, progress, videos, quality_flags, cost_breakdown,
        reference_brief, ...}. state='not_found' si el id no existe.
    """
    return await service.get_task(_STATE, params.task_id)


@mcp.tool(
    name="contenido_list_tasks",
    annotations={
        "title": "Listar reels recientes",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def contenido_list_tasks(params: ListTasksInput) -> dict[str, Any]:
    """Lista las tasks recientes con su estado y progreso."""
    return await service.list_tasks(_STATE, limit=params.limit)


@mcp.tool(
    name="contenido_list_voices",
    annotations={
        "title": "Motores TTS disponibles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def contenido_list_voices() -> dict[str, Any]:
    """Lista los motores TTS soportados (para elegir `voice_name`)."""
    from core.tts import SUPPORTED_ENGINES

    return {"engines": list(SUPPORTED_ENGINES)}


def main() -> None:
    """Entry point — transport stdio (local)."""
    mcp.run()


if __name__ == "__main__":
    main()
