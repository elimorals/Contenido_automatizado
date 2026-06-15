# Migration desde MoneyPrinterTurbo

Guía para usuarios existentes de [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) que quieren cambiarse a `contenido`.

## TL;DR

`contenido` es **superset** de MoneyPrinterTurbo. Todo lo que hacías en MPT lo puedes seguir haciendo, con dos diferencias clave:

1. **Es más rápido y limpio** (single-pass ffmpeg vs MoviePy multi-step)
2. **Tiene un modo Premium adicional** con DAG cognitivo de 18 reasoners

Si solo usabas MPT para volumen express, sigue usando el modo `express` y obtienes el mismo resultado con mejor calidad.

## Mapeo de conceptos

| MoneyPrinterTurbo | `contenido` equivalente | Notas |
|---|---|---|
| `app/services/llm.py:generate_script()` | `core/narrative/compose.py` + `core/llm_router/` | Refactorizado a interfaz async común |
| `app/services/voice.py:tts()` | `core/tts/get_engine(name).synthesize()` | + sample-accurate timing universal |
| `app/services/video.py:combine_videos()` | `core/editor/stitch_video()` | Single-pass ffmpeg (no MoviePy) |
| `app/services/material.py:search_videos_pexels()` | `core/visual/stock/pexels.py` | Async + ffprobe (no MoviePy) |
| `app/services/subtitle.py:create()` | `core/subtitles/whisper.py` + `srt.py` | + word-burst opcional |
| `app/services/task.py:start()` | `apps/api/pipeline.py:run_subject_pipeline()` | Mismo flujo, modular |
| `app/controllers/v1/video.py` | `apps/api/main.py` | + nuevos endpoints |
| `webui/Main.py` | `apps/webui/Main.py` | Rediseñado con 3 modos |
| `config.toml` | `config.toml` | Compatible, schema más amplio |

## Mapping de parámetros

### MPT request → contenido request

```python
# MPT
{
    "video_subject": "Spring flowers",
    "video_script": "Custom script",
    "video_terms": "flowers,spring",
    "video_aspect": "9:16",
    "video_source": "pexels",
    "voice_name": "en-US-AvaNeural-Female",
    "bgm_type": "random",
    "subtitle_enabled": True,
    "video_count": 1,
    "video_clip_duration": 5,
    "n_threads": 2
}

# contenido (mismos params + alias compat)
{
    "subject": "Spring flowers",          # o "video_subject" (alias)
    "script": "Custom script",            # o "video_script" (alias)
    "terms": ["flowers", "spring"],       # ahora es lista
    "aspect": "9:16",                     # o "video_aspect" (alias)
    "video_source": "pexels",
    "voice_name": "en-US-AvaNeural-Female",
    "bgm_type": "random",
    "subtitle_enabled": True,
    "video_count": 1,
    "video_clip_duration": 5,
    "n_threads": 2,
    "mode": "express"                     # NUEVO: explicit mode
}
```

**Tu código MPT existente sigue funcionando** — los aliases `video_subject`, `video_script`, `video_aspect` están definidos en el schema.

## Migración paso a paso

### 1. Backup tu MPT config

```bash
cd /ruta/a/MoneyPrinterTurbo
cp config.toml /tmp/mpt-config.toml.bak
cp .env /tmp/mpt-env.bak 2>/dev/null || true
```

### 2. Instalar contenido

```bash
git clone <contenido-repo> contenido
cd contenido
uv sync --extra dev
```

### 3. Portar tus API keys

```bash
cp .env.example .env

# Edita .env con tus keys de MPT. Mapeo:
#   pexels_api_keys → PEXELS_API_KEYS
#   openai_api_key → OPENAI_API_KEY
#   azure_api_key → AZURE_OPENAI_API_KEY
#   gemini_api_key → GEMINI_API_KEY
#   etc.
```

Tu config TOML de MPT es **casi 100% compatible** — copia secciones a tu nuevo `config.toml`:

```bash
cp config.example.toml config.toml
# Manualmente: copia [app], [whisper], [ui], [upload_post] de tu MPT config
```

### 4. Verificar que cargó

```bash
uv run contenido config-check
```

Deberías ver tus providers configurados igual que en MPT.

### 5. Replicar tu primer reel de MPT

Si en MPT generabas con:
```python
# MPT
from app.services import task as mpt_task
mpt_task.start(task_id, params)
```

Ahora:
```bash
# contenido CLI
uv run contenido subject "Tu subject habitual" --mode express
```

O via API:
```bash
curl -X POST http://localhost:8000/videos \
    -H 'Content-Type: application/json' \
    -d '{"subject":"Tu subject habitual","mode":"express"}'
```

## Diferencias en comportamiento

### ✅ Sin cambios

- Pexels/Pixabay/Coverr search idéntico
- Edge TTS funciona igual
- Aspect ratios (9:16, 16:9, 1:1) iguales
- BGM library en `resource/songs/` igual
- Whisper para subtítulos igual

### 🔄 Cambios sutiles

| Aspecto | MPT | contenido |
|---|---|---|
| Motor video | MoviePy 2.2 | ffmpeg directo (single-pass) |
| Subtítulos default | SRT | word_burst (cambia con `subtitle_style=srt`) |
| TTS timing | Edge SubMaker | sample-accurate (ffprobe + atempo) |
| Async | mixed sync/async | async-first todo |
| State | dict + Redis opcional | igual, pero schemas unificados |

### ⬆️ Mejoras gratuitas (sin cambiar tu uso)

1. **Sin drift de audio**: sample-accurate timing elimina el problema de SubMaker
2. **Más rápido**: single-pass ffmpeg es 3-5× más rápido que MoviePy
3. **Multi-encoder hardware**: detecta automáticamente nvenc/amf/qsv/videotoolbox
4. **Outputs más limpios**: libass renderiza mejor que drawtext

### ⬆️ Capacidades nuevas

| Nuevo | Cómo activar |
|---|---|
| Modo Premium con DAG cognitivo | `mode: "premium"` |
| Entry desde URL de artículo | `url: "https://..."` |
| Entry desde topic con hunters | `topic: "..."` |
| Word-burst karaoke libass | `subtitle_style: "word_burst"` |
| Anthropic Claude provider | `ANTHROPIC_API_KEY=...` |
| Visual selector híbrido | `visual_strategy: "hybrid"` |
| Veo i2v cinematográfico | `REEL_AF_USE_VEO=true` |

## Workflow recomendado de migración

### Semana 1 — Paralelo
Mantén MPT corriendo. Levanta `contenido` en puertos diferentes (8000/8501 vs MPT 8080/8501):

```bash
# Terminal 1: MPT
cd /path/to/MoneyPrinterTurbo
python main.py

# Terminal 2: contenido
cd /path/to/contenido
uv run uvicorn apps.api.main:app --port 8000
```

Compara outputs con los mismos inputs.

### Semana 2 — Migrar uso real

Cambia tus scripts/automatizaciones a apuntar a `contenido`:

```diff
- POST http://localhost:8080/api/v1/videos
+ POST http://localhost:8000/videos
```

Body es compatible (alias `video_subject` etc. funcionan).

### Semana 3 — Adoptar Premium

Para tus 5-10% de reels más importantes, prueba `mode: "premium"`:

```diff
{
    "subject": "...",
-   "mode": "express"
+   "topic": "...",
+   "mode": "premium",
+   "visual_strategy": "hybrid"
}
```

### Semana 4 — Optimizar

Una vez cómodo:
- Activa Redis para concurrency
- Configura Upload-Post para auto-publish
- Activa Veo en reels premium selectos

## ¿Qué pasa con mi config.toml de MPT?

**Las secciones idénticas funcionan as-is**:
- `[app]` (con algunas keys adicionales nuevas)
- `[whisper]`
- `[ui]`
- `[upload_post]`

**Las secciones que cambiaron de forma**:

MPT tenía LLM providers planos:
```toml
[app]
openai_api_key = "..."
openai_model_name = "gpt-4o-mini"
azure_api_key = "..."
```

`contenido` los anida:
```toml
[llm.openai]
api_key = "..."
model_name = "gpt-4o-mini"

[llm.azure]
api_key = "..."
```

Los env vars siguen siendo los mismos (`OPENAI_API_KEY` etc).

## Endpoints REST: mapeo

| MPT endpoint | contenido equivalente | Cambios |
|---|---|---|
| `POST /api/v1/videos` | `POST /videos` | Sin `/api/v1/` prefix |
| `GET /api/v1/tasks/{id}` | `GET /tasks/{id}` | Idem |
| `GET /api/v1/tasks` | `GET /tasks` | Paginación con `?page&page_size` |
| `DELETE /api/v1/tasks/{id}` | `DELETE /tasks/{id}` | Idem |
| `POST /api/v1/scripts` | `POST /scripts` | Acepta también topic/url |
| `POST /api/v1/terms` | `POST /terms` | Misma firma |
| `POST /api/v1/audio` | `POST /audio` | + word_timings en response |
| `POST /api/v1/subtitle` | `POST /subtitle` | + soporte word_burst |
| `GET /api/v1/musics` | (TODO) | No migrado aún |
| `POST /api/v1/musics` | (TODO) | No migrado aún |
| `GET /api/v1/materials` | (TODO) | No migrado aún |

**Endpoints nuevos**:
- `POST /narratives` — solo ScriptDraft
- `POST /hunters` — los 12 candidates
- `GET /costs/{task_id}` — breakdown de costos
- `GET /timings/{task_id}` — timings por phase

## FAQ migración

**Q: ¿Mis tasks viejas de MPT en Redis siguen visibles?**  
A: No. El schema de TaskInfo cambió (es Pydantic 2 con campos nuevos). Migrate scripts mediante export → re-import.

**Q: ¿Mis BGM files siguen funcionando?**  
A: Sí. Copia `MoneyPrinterTurbo/resource/songs/*.mp3` a `contenido/resource/songs/`.

**Q: ¿Mis fonts custom?**  
A: Sí. Copia TTF/TTC a `contenido/resource/fonts/`.

**Q: ¿Mi setup Docker funciona?**  
A: No directamente. `contenido` tiene su propio `docker-compose.yml` con control-plane AgentField + Redis. Adáptalo a tu network.

**Q: ¿Qué pasa con mis customizations en MPT (forks, plugins)?**  
A: Si modificaste `app/services/llm.py` para agregar providers custom, esos cambios van ahora a `core/llm_router/providers/`. La interfaz es diferente (async + Pydantic structured output).

**Q: ¿Puedo correr ambos en paralelo indefinidamente?**  
A: Sí. Diferentes puertos, diferentes outputs dirs. Buena estrategia mientras te acomodas.

**Q: Mi automation script asume sync (no async).**  
A: `contenido` expone REST API HTTP que es agnostic. Tu cliente sigue siendo síncrono.

## Recursos

- [GETTING_STARTED.md](./GETTING_STARTED.md) — tutorial inicial
- [API_REFERENCE.md](./API_REFERENCE.md) — endpoints detallados
- [CONFIGURATION.md](./CONFIGURATION.md) — todas las config keys
- [DECISIONS.md](./DECISIONS.md) — por qué los cambios arquitectónicos
- [`../examples/`](../examples/) — ejemplos curl/Python equivalentes
