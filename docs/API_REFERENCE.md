# API Reference

Referencia completa de los 14 endpoints REST. La versión interactiva (Swagger) está en `http://localhost:8000/docs`.

**Base URL**: `http://localhost:8000` (default)  
**Content-Type**: `application/json` para todos los `POST`  
**Auth**: ninguna por defecto (agregar reverse proxy con auth en producción)

## Health

### `GET /health`

Estado del sistema y subsistemas.

**Response 200**:
```json
{
  "status": "ok",
  "version": "0.1.0",
  "env": "dev",
  "queue": { "connected": true, "size": 0 },
  "state": { "connected": true },
  "distribution": false
}
```

Si algún subsistema falla, status puede ser `"degraded"` (no 503, para que uptime monitors no panickean innecesariamente).

### `GET /`

Root endpoint con links.

---

## Videos (entrypoint principal)

### `POST /videos`

Crea una task de generación. Encola y devuelve `task_id` inmediatamente (no espera).

**Acepta UNO de**: `url` | `topic` | `subject`.

**Request**:
```json
{
  "topic": "the placebo effect",
  "mode": "premium",
  "aspect": "9:16",
  "voice_name": "",
  "visual_strategy": "hybrid",
  "use_veo": false,
  "subtitle_style": "word_burst",
  "auto_upload": false
}
```

**Variantes por entry**:

```json
// Article path (10 reasoners)
{ "url": "https://arxiv.org/abs/2509.25541", "mode": "premium" }

// Topic path (18 reasoners)
{ "topic": "the placebo effect", "mode": "premium" }

// Legacy MPT (1 LLM call)
{ "subject": "Spring flowers", "mode": "express", "video_count": 3 }
```

**Response 202**:
```json
{
  "status": 202,
  "message": "Task queued",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "queue_position": 1
  }
}
```

**Errores**:
- `400` — params inválidos (ej. sin entry point, mode inválido)
- `429` — queue full (`max_queued_tasks` alcanzado)

---

## Tasks (estado y gestión)

### `GET /tasks/{task_id}`

Estado actual de una task.

**Response 200** (task en progreso):
```json
{
  "data": {
    "task_id": "550e8400-...",
    "state": 4,
    "mode": "premium",
    "progress": 60,
    "timings_s": {
      "hunt": 8.2,
      "critic": 4.1,
      "narrate": 7.6,
      "judge": 3.0,
      "tts": 12.4
    }
  }
}
```

**State codes**:
- `0` = QUEUED
- `4` = PROCESSING
- `1` = COMPLETE
- `-1` = FAILED

**Response 200** (complete):
```json
{
  "data": {
    "task_id": "550e8400-...",
    "state": 1,
    "progress": 100,
    "script": "Why are clouds white? Sunlight scatters...",
    "essence": {
      "core_claim": "Clouds are white because of Mie scattering",
      "mechanism": "Water droplets scatter all wavelengths equally...",
      "evidence": ["Mie scattering applies when particle size ≈ wavelength"],
      "content_mode": "scientific"
    },
    "audio_path": "/app/output/550e8400-.../audio.mp3",
    "audio_duration_s": 22.4,
    "subtitle_path": "/app/output/550e8400-.../subs.ass",
    "videos": ["/app/output/550e8400-.../reel.mp4"],
    "timings_s": {
      "hunt": 8.1, "critic": 4.2, "narrate": 7.5, "judge": 3.1,
      "tts": 12.4, "plan": 1.1, "visual_accent": 6.8,
      "media": 38.2, "stitch": 4.6, "total": 86.0
    },
    "cost_breakdown": {},
    "cross_post_results": []
  }
}
```

**Errores**:
- `404` — task_id no existe

### `GET /tasks?page=1&page_size=10`

Lista paginada de todas las tasks.

**Response 200**:
```json
{
  "data": {
    "tasks": [ { "task_id": "...", "state": 1, ... }, ... ],
    "total": 47,
    "page": 1,
    "page_size": 10
  }
}
```

### `DELETE /tasks/{task_id}`

Borra la task y los artifacts del filesystem (output/<task_id>/).

**Response 200**:
```json
{ "message": "Deleted" }
```

### `GET /costs/{task_id}`

Cost breakdown de una task (cuando esté implementado).

**Response 200**:
```json
{
  "data": {
    "llm": 0.020,
    "tts": 0.015,
    "image": 0.018,
    "video": 0.0,
    "total": 0.053
  }
}
```

### `GET /timings/{task_id}`

Timings por phase.

**Response 200**:
```json
{
  "data": {
    "hunt": 8.1,
    "critic": 4.2,
    "narrate": 7.5,
    "judge": 3.1,
    "tts": 12.4,
    "plan": 1.1,
    "visual_accent": 6.8,
    "media": 38.2,
    "stitch": 4.6,
    "total": 86.0
  }
}
```

---

## Endpoints individuales (sin video completo)

Útiles para iteración rápida o A/B testing externo. **Síncronos** — no encolan, devuelven el resultado directamente.

### `POST /scripts`

Genera solo el script (sin TTS ni video).

**Request** (article path):
```json
{
  "url": "https://arxiv.org/abs/2509.25541",
  "mode": "premium",
  "language": "auto"
}
```

**Request** (topic path):
```json
{
  "topic": "the placebo effect",
  "mode": "premium"
}
```

**Request** (subject path):
```json
{
  "subject": "Spring flowers",
  "mode": "express",
  "paragraph_number": 1,
  "video_script_prompt": "Optional custom prompt"
}
```

**Response 200**:
```json
{
  "data": {
    "script_draft": {
      "hook": "Why do we have fingerprints?",
      "hook_variant": "curiosity_gap",
      "mechanism_lines": [...],
      "payoff_line": "...",
      "narration": "Full narration with [tags]",
      "target_wpm": 180
    }
  }
}
```

### `POST /terms`

Genera términos de búsqueda (solo subject path).

**Request**:
```json
{
  "subject": "Spring flowers",
  "video_script": "..."  // opcional
}
```

**Response 200**:
```json
{ "data": { "terms": ["flowers", "spring", "garden", "bloom", "nature"] } }
```

### `POST /audio`

Genera solo audio TTS + word_timings sample-accurate.

**Request**:
```json
{
  "subject": "Hello world",
  "voice_name": "en-US-AvaNeural-Female"
}
```

(Cualquier entry point + voice_name)

**Response 200**:
```json
{
  "data": {
    "audio_path": "/app/output/audio_xyz.mp3",
    "audio_duration_s": 1.2,
    "word_timings": [
      { "word": "Hello", "start_s": 0.0, "end_s": 0.5 },
      { "word": "world", "start_s": 0.5, "end_s": 1.0 }
    ],
    "engine": "edge"
  }
}
```

### `POST /subtitle`

Genera solo subtítulos (necesita audio + script).

**Request**:
```json
{
  "subject": "...",
  "voice_name": "...",
  "subtitle_style": "word_burst"  // o "srt"
}
```

**Response 200**:
```json
{
  "data": {
    "subtitle_path": "/app/output/subs.ass",
    "style": "word_burst"
  }
}
```

---

## Premium-only (DAG profundo)

### `POST /narratives`

Devuelve solo el ScriptDraft delayed-reveal (sin generar video). Útil para A/B testing de narrativas.

**Request** (topic):
```json
{
  "topic": "the placebo effect",
  "mode": "premium"
}
```

**Response 200** (article):
```json
{
  "data": {
    "script_draft": { ... }
  }
}
```

**Response 200** (topic):
```json
{
  "data": {
    "winner": {
      "tease": "Why does pretending you took medicine work?",
      "common_belief": null,
      "reveal": "In 1957, Henry Beecher showed that...",
      "payoff": "Your brain literally medicates itself.",
      "open_style": "question",
      "target_wpm": 180,
      "narration": "..."
    },
    "winner_idx": 2,
    "composite_score": 8.4,
    "why": "Strongest scroll-stop hook + specific named entity..."
  }
}
```

### `POST /hunters`

Devuelve los 12 candidates de los 4 hunters paralelos. Para A/B testing externo o reranking custom.

**Request**:
```json
{
  "topic": "the placebo effect",
  "mode": "premium"
}
```

**Response 200**:
```json
{
  "data": {
    "candidates": [
      {
        "core_claim": "...",
        "mechanism": "...",
        "evidence": ["..."],
        "content_mode": "scientific",
        "domain": "neuroscience",
        "angle": "specific_figure",
        "novelty_pitch": "..."
      },
      ...11 more
    ]
  }
}
```

---

## Patrones de uso

### Polling de una task hasta completar

```bash
TASK_ID=$(curl -s -X POST http://localhost:8000/videos \
    -H 'Content-Type: application/json' \
    -d '{"topic":"placebo","mode":"premium"}' | jq -r .data.task_id)

while true; do
    STATE=$(curl -s http://localhost:8000/tasks/$TASK_ID | jq -r .data.state)
    case $STATE in
        1)  echo "✓ Complete"; break;;
        -1) echo "✗ Failed"; break;;
        *)  echo "Still processing (state=$STATE)..."; sleep 2;;
    esac
done

curl http://localhost:8000/tasks/$TASK_ID | jq .data.videos
```

### Generar 3 narrativas alternativas (A/B testing)

```bash
for i in 1 2 3; do
    curl -s -X POST http://localhost:8000/narratives \
        -H 'Content-Type: application/json' \
        -d '{"topic":"climate change","mode":"premium"}' \
        | jq -r '.data.winner.tease'
done
```

### Batch desde CSV

```bash
while IFS= read -r topic; do
    curl -s -X POST http://localhost:8000/videos \
        -H 'Content-Type: application/json' \
        -d "{\"topic\":\"$topic\",\"mode\":\"express\"}" \
        | jq -r '.data.task_id'
done < topics.csv
```

---

## Códigos de error

| Status | Significado | Causa común |
|---|---|---|
| `400` | Bad Request | Params inválidos, sin entry point |
| `404` | Not Found | task_id no existe |
| `422` | Validation Error | Pydantic schema falló |
| `429` | Too Many Requests | Queue full |
| `500` | Internal Server Error | Bug en el pipeline |
| `502` | Bad Gateway | LLM provider caído |
| `503` | Service Unavailable | Redis/AgentField caído |

Body de error estándar:
```json
{
  "detail": "Descripción del error"
}
```

---

## OpenAPI / Swagger

- **Swagger UI**: http://localhost:8000/docs (interactivo)
- **ReDoc**: http://localhost:8000/redoc (lectura)
- **JSON schema**: http://localhost:8000/openapi.json

## Rate limiting

Por defecto **no hay rate limiting per-user**. El sistema tiene backpressure global (`max_queued_tasks`).

Para producción multi-tenant, agregar reverse proxy (nginx, Caddy, Cloudflare) con rate limiting per IP/user.

## Ejemplos ejecutables

Ver [`examples/`](../examples/) para curl + Python clients listos para copiar.
