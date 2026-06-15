# orchestration

Capa de orquestación: colas, estado, broker AgentField.

## Submódulos (Fase 5)

### `queue/`
Portado de MPT:
- `InMemoryTaskManager` — para dev, sin Redis
- `RedisTaskManager` — para staging/prod
- Control de concurrencia (`max_concurrent_tasks`)
- Backpressure (`max_queued_tasks`, error 429)

### `state/`
Portado de MPT:
- `MemoryState` — dict en RAM
- `RedisState` — persistente
- Estados: QUEUED (0), PROCESSING (4), COMPLETE (1), FAILED (-1)

### `agentfield/`
Adapter del control-plane de AgentField:
- Conexión con broker
- Dispatch de reasoners DAG
- Recolección de timings por phase
- Manejo de timeouts (`AGENTFIELD_LLM_CALL_TIMEOUT`)

## Flujo end-to-end (modo premium)

```
1. POST /videos {topic: "..."}
   └→ apps/api/main.py crea TaskInfo (state=QUEUED)
2. queue/RedisTaskManager.enqueue(task_id, params)
3. apps/api/worker.py:
   └→ BRPOP de queue
   └→ state.update(PROCESSING)
   └→ AgentField dispatch del DAG (18 reasoners)
        ├→ hunt_specific_figure | hunt_reversal | hunt_temporal | hunt_cross_domain
        ├→ pick_top_essences
        ├→ write_narrations (×3)
        ├→ pick_best_narration
        ├→ adapt → ScriptDraft
        ├→ tts.synthesize → AudioArtifact
        ├→ pack_cards | plan_beats (paralelo)
        ├→ plan_beat_visuals | plan_beat_accents (paralelo)
        ├→ generate_beat_videos (per-beat con fallback)
        └→ ffmpeg_stitch → reel.mp4
   └→ state.update(COMPLETE, videos=[...])
   └→ if auto_upload: distribution.upload(...)
4. GET /tasks/{task_id} devuelve TaskInfo completo
```
