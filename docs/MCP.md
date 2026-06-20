# MCP Server — contenido como herramienta para agentes

Expone el pipeline de reels a agentes (Claude Code, Claude Desktop, Cursor, etc.) vía
**Model Context Protocol**. Un agente puede analizar un video de referencia, disparar la
generación de un reel, y sondear su progreso — sin tocar el CLI. Ver **ADR-021**.

> Diseño: capa fina FastMCP (`apps/mcp/server.py`) sobre la lógica testeable
> (`apps/mcp/service.py`), in-process sobre el pipeline existente (`run_pipeline`).
> Transport **stdio** (local).

## Instalación

```bash
pip install -e ".[mcp]"        # añade el SDK `mcp`
# para que contenido_analyze_reference funcione, además:
pip install -e ".[reference]"  # yt-dlp; ffmpeg debe estar en PATH
```

Verifica que arranca:

```bash
contenido-mcp        # o: python -m apps.mcp.server
```

## Tools expuestos

| Tool | Tipo | Qué hace |
|---|---|---|
| `contenido_analyze_reference` | read-only | Analiza un video (TikTok/Reel/YT) → brief (pacing, hook, wpm, suggested_beats, transcript). |
| `contenido_start_reel` | **escribe / gasta** | Agenda un reel en background → `task_id` + `cost_note`. No bloquea. |
| `contenido_get_task` | read-only | Estado/progreso/resultado (videos, quality_flags, costos, reference_brief). |
| `contenido_list_tasks` | read-only | Tasks recientes. |
| `contenido_list_voices` | read-only | Motores TTS disponibles. |

### Gate de costo

- Solo **reels** (`topic` / `url` / `subject`, ~$0.01–1.20). El **long-form ($16–80) NO se
  expone** — sigue human-gated vía CLI/plan editorial.
- `contenido_start_reel` devuelve `cost_note` para que el agente vea el gasto antes de actuar.
- Provee **exactamente una** entrada (topic | url | subject). `reference_url` es opcional.

## Flujo típico para el agente

```
1. contenido_analyze_reference(url="https://tiktok.com/@x/video/123")
   → { hook_style: "shock_stat", suggested_beats: 5, target_wpm: 180, ... }

2. contenido_start_reel(topic="cómo funciona un agujero negro",
                        reference_url="https://tiktok.com/@x/video/123",
                        mode="premium")
   → { task_id: "ab12…", state: "queued", cost_note: "~$0.08–1.20 …" }

3. contenido_get_task(task_id="ab12…")   # sondear hasta state="complete"
   → { state: "complete", videos: ["output/ab12…/reel.mp4"],
       quality_flags: { slideshow_risk: 0.2 }, cost_breakdown: {…} }
```

## Registro en clientes MCP

### Claude Desktop / Claude Code (`mcpServers`)

```json
{
  "mcpServers": {
    "contenido": {
      "command": "contenido-mcp",
      "env": { "CONTENIDO_CONFIG": "/ruta/a/config.toml" }
    }
  }
}
```

Si `contenido-mcp` no está en PATH, usa el intérprete del venv:

```json
{
  "mcpServers": {
    "contenido": {
      "command": "/ruta/al/repo/.venv/bin/python",
      "args": ["-m", "apps.mcp.server"],
      "cwd": "/ruta/al/repo"
    }
  }
}
```

## Estado compartido

El server usa `get_state_manager(load_config())`: si Redis está configurado, los jobs lanzados
por el agente son visibles también desde la **API REST** y la **WebUI** (mismo `task_id`). En
otro caso, el estado vive en memoria del proceso del MCP.

## Notas de implementación

- Jobs en background con `asyncio.create_task` + set de referencias (no se pierden por GC).
- Un crash del pipeline marca la task `FAILED` (nunca queda colgada en `processing`).
- La lógica está en `apps/mcp/service.py` y se testea sin el SDK `mcp` (14 tests, runner
  inyectable). El server sólo se import-testea cuando el extra `mcp` está instalado.
