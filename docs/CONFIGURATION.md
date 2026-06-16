# Configuration Reference

Referencia completa de `config.toml` y variables de entorno (`.env`).

## Precedencia

```
Variables de entorno (.env)   ← Mayor prioridad
        ↓
config.toml                    ← Default editable
        ↓
config.example.toml            ← Fallback si no hay config.toml
        ↓
Defaults hardcoded en shared/config.py   ← Último recurso
```

**Regla**: las variables de entorno SIEMPRE sobrescriben `config.toml`. Usa `.env` para secrets, `config.toml` para comportamiento.

## Estructura de config.toml

### `[app]` — Configuración general

```toml
[app]
env = "dev"                            # dev | staging | prod
default_mode = "premium"               # express | premium
log_level = "INFO"                     # DEBUG | INFO | WARNING | ERROR
tls_verify = true                      # SSL cert verification
```

| Key | Tipo | Default | Override env | Descripción |
|---|---|---|---|---|
| `env` | str | `"dev"` | `CONTENIDO_ENV` | Identifica el entorno (afecta logging y defaults) |
| `default_mode` | str | `"premium"` | `DEFAULT_MODE` | Modo default cuando no se especifica en request |
| `log_level` | str | `"INFO"` | `LOG_LEVEL` | Loguru level |
| `tls_verify` | bool | `true` | `TLS_VERIFY` | Verificar certificados SSL en requests externos |

### `[app]` — Concurrencia y queue

```toml
max_concurrent_tasks = 3               # Workers simultáneos
max_queued_tasks = 50                  # Capacidad de la queue
```

| Key | Default | Override env | Descripción |
|---|---|---|---|
| `max_concurrent_tasks` | `3` | `MAX_CONCURRENT_TASKS` | Workers paralelos. Subir si tienes recursos |
| `max_queued_tasks` | `50` | `MAX_QUEUED_TASKS` | Cuando llena, API devuelve 429 |

### `[app]` — Redis (opcional)

```toml
enable_redis = false                   # true para producción
redis_host = "localhost"
redis_port = 6379
redis_db = 0
```

Cuando `enable_redis = false`, se usa `InMemoryTaskManager` y `MemoryStateManager` (datos se pierden al reiniciar).

### `[app]` — AgentField (broker de reasoners)

```toml
agentfield_server = "http://localhost:8080"
agentfield_node_id = "contenido"
agentfield_llm_call_timeout = 120
```

Si AgentField broker no está disponible, el adapter usa fallback a `core.llm_router` directo (sin DAG distribuido).

---

## `[llm]` — Proveedores de LLM

### Defaults

```toml
[llm]
default_provider_premium = "openrouter"     # Mejor calidad
default_provider_express = "edge_tts_companion"  # Más barato
```

### Per-provider config

Cada provider tiene su sección `[llm.<nombre>]`:

```toml
[llm.openrouter]
api_key = ""                           # Override env: OPENROUTER_API_KEY
default_model = "openrouter/deepseek/deepseek-v4-pro"
base_url = "https://openrouter.ai/api/v1"

[llm.openai]
api_key = ""                           # OPENAI_API_KEY
model_name = "gpt-4o-mini"
base_url = ""                          # Vacío = OpenAI oficial

[llm.azure]
api_key = ""                           # AZURE_OPENAI_API_KEY
model_name = "gpt-35-turbo"
api_version = "2024-02-15-preview"
base_url = ""                          # Tu endpoint Azure custom

[llm.gemini]
api_key = ""                           # GEMINI_API_KEY
model_name = "gemini-2.5-flash"

[llm.moonshot]
api_key = ""
model_name = "moonshot-v1-8k"

[llm.ollama]
model_name = ""                        # ej. "llama3.2"
base_url = "http://localhost:11434/v1"

[llm.qwen]
api_key = ""                           # QWEN_API_KEY
model_name = "qwen-max"

[llm.deepseek]
api_key = ""                           # DEEPSEEK_API_KEY
model_name = "deepseek-chat"

[llm.groq]
api_key = ""                           # GROQ_API_KEY
model_name = "llama-3.3-70b-versatile"
```

### Anthropic Claude

```toml
[llm.anthropic]
api_key = ""                           # ANTHROPIC_API_KEY
model_name = "claude-sonnet-4-6"       # claude-haiku-4-5 | claude-opus-4-7
```

---

## `[tts]` — Engines de TTS

```toml
[tts]
default_premium = "gemini_flash"       # Único con tags inline
default_express = "edge"               # Gratis
sample_accurate_timing = true          # Aplicar ffprobe+atempo a TODOS
```

### Per-engine config

```toml
[tts.edge]
timeout = 30                           # EDGE_TTS_TIMEOUT
default_voice = "en-US-AvaNeural-Female"

[tts.gemini_flash]
model = "google/gemini-3.1-flash-tts-preview"   # REEL_AF_TTS_MODEL
voice_tone = "wonder"                  # urgent | wonder | deadpan | earnest | playful

[tts.azure]
api_key = ""                           # AZURE_TTS_API_KEY
region = "eastus"                      # AZURE_TTS_REGION

[tts.siliconflow]
api_key = ""                           # SILICONFLOW_API_KEY
model = "FunAudioLLM/CosyVoice2-0.5B"

[tts.mimo]
api_key = ""                           # MIMO_API_KEY
```

### Voice tone (solo Gemini Flash)

| Tone | Voz Gemini | Cuándo usar |
|---|---|---|
| `urgent` | Charon (deep, serious) | News, crisis |
| `wonder` | Kore (warm, curious) | Educational, science (DEFAULT) |
| `deadpan` | Schedar (neutral) | Comedia seca |
| `earnest` | Aoede (friendly) | Lifestyle, tutoriales |
| `playful` | Puck (bright) | Kids, fun content |

---

## `[visual]` — Generación visual

```toml
[visual]
default_strategy = "hybrid"            # stock | ia | hybrid

[visual.gemini_image]
model = "openrouter/google/gemini-2.5-flash-image"
canvas_w = 720
canvas_h = 1280

[visual.veo]
enabled = false                        # REEL_AF_USE_VEO=true
model = "openrouter/google/veo-3.1-lite"
fallback_to_ken_burns = true

[visual.ken_burns]
zoom_factor = 1.15                     # 15% zoom durante el beat
direction = "auto"                     # auto | in | out | left | right
```

### Strategy explained

| Strategy | Cuándo elegir | Costo |
|---|---|---|
| `stock` | Volumen masivo, contenido genérico | $0 |
| `ia` | Cada beat necesita visual específico | ~$0.05/reel |
| `hybrid` | **Default**, mezcla según role + evidence | ~$0.02/reel |

### Veo trade-offs

| `enabled = false` (default) | `enabled = true` |
|---|---|
| Costo: $0 motion | Costo: +$1.10/reel |
| Tiempo: 70-90s | Tiempo: 85-110s |
| Calidad: ken-burns (zoom) | Calidad: motion cinematográfico real |
| Recomendado: 90% de casos | Recomendado: brand premium |

### Higgsfield (DoP + Soul + Effects)

Provider alternativo (o complementario) a Veo con motion presets cinematográficos nombrados (50+: orbit_360, fpv_drone, dolly_zoom_in, super_dolly_out, etc.) y soporte para character consistency vía SoulId.

```toml
[visual.higgsfield]
enabled = false                        # true → DoP reemplaza/complementa Veo
credentials = ""                       # "KEY_ID:KEY_SECRET" (auth V2)
key_id = ""
key_secret = ""
base_url = "https://platform.higgsfield.ai"
timeout_s = 300.0
poll_interval_s = 2.0
max_poll_time_s = 600.0

# DoP (image-to-video, 5s fijo)
dop_model = "dop-turbo"                # dop-lite | dop-turbo | dop-preview
dop_endpoint = "/v1/image2video/dop"
dop_clip_duration_s = 5
dop_motion_strength = 0.85

# Soul (character consistency)
soul_enabled = false
soul_model = "soul_cinematic"          # text2image_soul_v2 | soul_cinematic
soul_endpoint = "/v1/text2image/soul"
soul_default_style_id = ""
soul_default_reference_id = ""         # SoulId global; BeatVisual.soul_id la sobrescribe
soul_reference_strength = 0.75
soul_width = 720
soul_height = 1280

# Effects (action VFX overlay post-step)
effects_enabled = false
effects_endpoint = "/v1/effects/apply"

# Selector
prefer_over_veo = true                 # cuando ambos enabled, Higgsfield gana
motion_catalog_cache_path = "./storage/higgsfield_motions.json"
```

### Higgsfield model variants (DoP)

| Variant | Velocidad | Calidad | Costo/run |
|---|---|---|---|
| `dop-lite` | Más rápido | Preview-grade | ~$0.13 |
| `dop-turbo` (default) | 2× lite | Producción | ~$0.20 |
| `dop-preview` | Más lento | Premium (mejor lighting) | ~$0.30 |

### Higgsfield motion presets (50+ via `HiggsfieldPreset` enum)

Camera moves: `static`, `dolly_in/out/left/right`, `super_dolly_in/out`, `dolly_zoom_in/out`, `zoom_in/out`, `rapid_zoom_in/out`, `crash_zoom_out`, `yoyo_zoom`, `pan_left/right`, `whip_pan`, `tilt_up/down`, `dutch_angle`, `overhead`, `jib_up/down`, `hero_cam`, `lazy_susan`, `360_orbit`, `arc_right`, `robo_arm`, `snorricam`, `fpv_drone`, `flying_cam_transition`, `handheld`, `head_tracking`, `object_pov`, `road_rush`, `fisheye`, `focus_change`, `low_shutter`, `incline`, `wiggle`, `glam`, `timelapse_glam/human/landscape`, `hyperlapse`, `through_object_in/out`, `3d_rotation`.

Mapping default desde `MotionHint`:

| MotionHint | → HiggsfieldPreset |
|---|---|
| `static` | `static` |
| `slow_zoom_in` | `zoom_in` |
| `slow_zoom_out` | `zoom_out` |
| `pan_left` | `pan_left` |
| `pan_right` | `pan_right` |
| `ken_burns` | `dolly_in` |

Para override por beat: `BeatVisual.higgsfield_preset=HiggsfieldPreset.FPV_DRONE`. Para arbitrary motion_id (UUID): `BeatVisual.higgsfield_motion_id="..."`.

### Higgsfield Soul (character consistency)

Generación de first frames con un personaje consistente cross-beat (rostro/cuerpo idénticos en cada scene). Requiere un `SoulId` entrenado previamente con N fotos de referencia:

```python
from core.visual.generation import create_soul_id
soul_id = await create_soul_id(
    name="protagonista_principal",
    reference_images=[Path("ref1.jpg"), Path("ref2.jpg"), Path("ref3.jpg")],
)
# Después: setea soul_default_reference_id = "<soul_id>" en TOML
# o por beat: BeatVisual.soul_id="<soul_id>"
```

### Env vars Higgsfield

| Variable | Default | Descripción |
|---|---|---|
| `HIGGSFIELD_CREDENTIALS` | — | "KEY_ID:KEY_SECRET" (forma combinada) |
| `HIGGSFIELD_KEY_ID` | — | Key id (forma separada) |
| `HIGGSFIELD_KEY_SECRET` | — | Key secret (forma separada) |
| `HIGGSFIELD_ENABLED` | `false` | Habilita DoP |
| `HIGGSFIELD_SOUL_ENABLED` | `false` | Habilita Soul para first frames |
| `HIGGSFIELD_EFFECTS_ENABLED` | `false` | Habilita VFX effects post-step |
| `HIGGSFIELD_DOP_MODEL` | `dop-turbo` | Variant del modelo DoP |
| `HIGGSFIELD_SOUL_MODEL` | `soul_cinematic` | Variant del modelo Soul |
| `HIGGSFIELD_SOUL_REFERENCE_ID` | — | SoulId default cross-beat |
| `HIGGSFIELD_BASE_URL` | `https://platform.higgsfield.ai` | Override del API base |
| `HF_CREDENTIALS` / `HF_API_KEY` / `HF_API_SECRET` | — | Alias del SDK oficial (también soportados) |

---

## `[stock]` — Stock footage

```toml
[stock]
pexels_api_keys = []                   # PEXELS_API_KEYS=key1,key2
pixabay_api_keys = []                  # PIXABAY_API_KEYS=key1,key2
coverr_api_keys = []                   # COVERR_API_KEYS=key1
provider_order = ["pexels", "pixabay", "coverr"]  # Fallback order

cache_dir = "./storage/materials_cache"
min_duration_s = 3.0                   # Filtrar videos muy cortos
max_duration_s = 30.0                  # Filtrar videos muy largos
```

**Rotación de keys**: si tienes varias, separar con comas en env vars:
```bash
PEXELS_API_KEYS=key1,key2,key3
```

El sistema rota automáticamente. Si una key da 429/401, marca como exhausted hasta el siguiente reinicio.

---

## `[whisper]` — ASR para subtítulos

```toml
[whisper]
model_size = "large-v3"                # tiny | base | small | medium | large | large-v3
device = "cpu"                         # cpu | cuda
compute_type = "int8"                  # int8 | int8_float16 | float16
beam_size = 5
vad_threshold_ms = 500
```

### Trade-offs por modelo

| Modelo | Tamaño | Velocidad CPU | Calidad | Uso |
|---|---|---|---|---|
| `tiny` | 150 MB | 32× realtime | Baja | Drafts |
| `base` | 290 MB | 16× realtime | Aceptable | Quick checks |
| `small` | 967 MB | 6× realtime | Buena | Default español |
| `medium` | 3 GB | 2× realtime | Muy buena | Producción media |
| `large` | 6 GB | 1× realtime | Excelente | Producción alta |
| `large-v3` | 6 GB | 1× realtime | Estado-del-arte | **DEFAULT recomendado** |

### GPU acceleration

Si tienes CUDA:
```toml
device = "cuda"
compute_type = "float16"
```

---

## `[subtitles]` — Subtítulos

```toml
[subtitles]
default_style = "word_burst"           # word_burst | srt
font_name = "Montserrat-Bold.ttf"
font_size_burst = 170                  # px para word-burst
font_size_srt = 60                     # px para SRT clásico
fore_color = "#FFFFFF"
stroke_color = "#000000"
stroke_width = 1.5
background = false                     # Solo aplica a SRT
position = "bottom"                    # bottom | top | center | custom
custom_position = 70.0                 # % de altura (solo si position=custom)

cjk_font = "STHeitiMedium.ttc"         # Auto-swap para texto CJK
```

### Word-burst vs SRT

| Word-burst (default) | SRT clásico |
|---|---|
| 1 palabra a la vez | Múltiples palabras |
| 170px, bottom-center | 60px, ajustable |
| Estilo viral 2025+ | Broadcasting tradicional |
| Solo libass | libass o ffmpeg drawtext |

---

## `[editor]` — Editor ffmpeg

```toml
[editor]
single_pass = true                     # NO cambiar (single-pass = mejor calidad)
codec = "libx264"
crf = 23                               # 18 (alta) - 28 (baja)
preset = "fast"                        # ultrafast | superfast | veryfast | faster | fast | medium | slow | slower | veryslow
audio_codec = "aac"
audio_bitrate = "128k"
fps = 30

hw_encoder_fallback = [
    "h264_videotoolbox",               # macOS
    "h264_nvenc",                      # NVIDIA
    "h264_amf",                        # AMD
    "h264_qsv",                        # Intel
    "h264_mf",                         # Windows Media Foundation
    "libx264",                         # SOFTWARE fallback (siempre)
]
```

### Aspect ratios

```toml
[editor.aspect]
default = "9:16"
"9:16" = { width = 1080, height = 1920 }   # TikTok, Reels, Shorts
"16:9" = { width = 1920, height = 1080 }   # YouTube, web
"1:1" = { width = 1080, height = 1080 }    # Instagram Feed
```

### CRF (Constant Rate Factor)

| CRF | Calidad | Bitrate aprox | Uso |
|---|---|---|---|
| 18 | Visualmente lossless | 10-15 Mbps | Master quality |
| 20-22 | Excelente | 6-10 Mbps | Distribución premium |
| **23** | **Muy buena (DEFAULT)** | 4-6 Mbps | Web/social |
| 26-28 | Aceptable | 2-3 Mbps | Móvil/data-limited |

### Preset

`fast` (default) balancea velocidad y calidad. `ultrafast` para iteración rápida, `slow` para calidad máxima.

---

## `[bgm]` — Música de fondo

```toml
[bgm]
default_type = "random"                # random | none | custom
default_file = ""                      # Solo si type = custom
default_volume = 0.2                   # 0.0 - 1.0
library_dir = "./resource/songs"
```

Coloca archivos MP3 en `resource/songs/` para que `random` los encuentre.

Override por request: `params.bgm_type`, `params.bgm_file`, `params.bgm_volume`.

---

## `[ui]` — WebUI (Streamlit)

```toml
[ui]
hide_log = false
show_cost_tracker = true
show_timings = true
default_language = "es-MX"
supported_languages = [
    "en-US", "es-MX", "zh-CN", "zh-HK", "zh-TW",
    "fr-FR", "de-DE", "ru-RU", "vi-VN", "th-TH", "tr-TR",
]
```

---

## `[upload_post]` — Distribución social

```toml
[upload_post]
api_key = ""                           # UPLOAD_POST_API_KEY
auto_upload = false                    # UPLOAD_POST_AUTO_UPLOAD
platforms = ["tiktok", "instagram"]   # UPLOAD_POST_PLATFORMS=tiktok,instagram
```

Cuando `auto_upload = true`, cada reel completado se publica automáticamente.

---

## Variables de entorno completas (.env)

Lista completa de todas las env vars soportadas. Ver `.env.example` para template.

### Sistema
| Variable | Default | Descripción |
|---|---|---|
| `CONTENIDO_ENV` | `dev` | Entorno |
| `DEFAULT_MODE` | `premium` | Modo default |
| `LOG_LEVEL` | `INFO` | Loguru level |
| `TLS_VERIFY` | `true` | SSL verify |

### Orquestación
| Variable | Default | Descripción |
|---|---|---|
| `ENABLE_REDIS` | `false` | Habilitar Redis |
| `REDIS_HOST` | `localhost` | Host Redis |
| `REDIS_PORT` | `6379` | Puerto Redis |
| `REDIS_DB` | `0` | DB index |
| `MAX_CONCURRENT_TASKS` | `3` | Workers paralelos |
| `MAX_QUEUED_TASKS` | `50` | Capacidad queue |

### AgentField
| Variable | Default | Descripción |
|---|---|---|
| `AGENT_NODE_ID` | `contenido` | ID del nodo |
| `AGENTFIELD_SERVER` | `http://localhost:8080` | Control-plane URL |
| `AGENTFIELD_LLM_CALL_TIMEOUT` | `120` | Timeout per call (s) |

### LLM providers
| Variable | Provider |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter |
| `OPENAI_API_KEY` | OpenAI |
| `OPENAI_MODEL_NAME` | OpenAI model override |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI |
| `GEMINI_API_KEY` | Google Gemini |
| `MOONSHOT_API_KEY` | Moonshot Kimi |
| `OLLAMA_BASE_URL` | Ollama local |
| `QWEN_API_KEY` | Alibaba Qwen |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MIMO_API_KEY` | Xiaomi MiMo |
| `GROQ_API_KEY` | Groq |
| `REEL_AF_MODEL` | Modelo default override |

### TTS
| Variable | Descripción |
|---|---|
| `EDGE_TTS_TIMEOUT` | Timeout Edge TTS (s) |
| `REEL_AF_TTS_MODEL` | Modelo TTS Gemini Flash |
| `AZURE_TTS_API_KEY` | Key Azure TTS |
| `AZURE_TTS_REGION` | Region Azure |
| `SILICONFLOW_API_KEY` | Key SiliconFlow |

### Visual
| Variable | Descripción |
|---|---|
| `REEL_AF_IMAGE_MODEL` | Modelo Gemini Image |
| `REEL_AF_VIDEO_MODEL` | Modelo Veo |
| `REEL_AF_USE_VEO` | Habilitar Veo (default: `false`) |

### Stock
| Variable | Descripción |
|---|---|
| `PEXELS_API_KEYS` | Keys separadas con coma |
| `PIXABAY_API_KEYS` | Keys separadas con coma |
| `COVERR_API_KEYS` | Keys separadas con coma |

### Whisper
| Variable | Default | Valores |
|---|---|---|
| `WHISPER_MODEL_SIZE` | `large-v3` | tiny/base/small/medium/large/large-v3 |
| `WHISPER_DEVICE` | `cpu` | cpu/cuda |
| `WHISPER_COMPUTE_TYPE` | `int8` | int8/int8_float16/float16 |

### Distribución
| Variable | Descripción |
|---|---|
| `UPLOAD_POST_API_KEY` | Key Upload-Post |
| `UPLOAD_POST_AUTO_UPLOAD` | true/false |
| `UPLOAD_POST_PLATFORMS` | `tiktok,instagram` |

---

---

## Capa Editorial (`editorial/`)

Patrón portado de `corredor-content`. Provee brand voice, pilares, facts verificables, audiencias y specs por plataforma — TODO versionado en git. Ver `docs/EDITORIAL.md` para detalle completo.

```
editorial/
├── brand-voice.md          # fuente de verdad del tono (cargado por reasoners)
├── facts.json              # anti-alucinación inyectada a hunters
├── audiences.json          # perfiles de audiencia
├── platforms.json          # specs por plataforma (TikTok/Reels/Shorts/…)
├── local-events.json       # eventos del calendario para seed de planes
└── pillars/*.md            # 5 pilares default editables
```

### Comandos relacionados

| Comando | Propósito |
|---|---|
| `contenido brand-check` | Inspecciona la capa editorial cargada |
| `contenido plan --ideas 7` | Genera plan semanal con N ideas en `out/plans/` |
| `contenido plan-show [--week ...]` | Muestra plan + estado de aprobación |
| `contenido produce-week` | Ejecuta DAG para todas las ideas con `approved: true` |

### Cost tracking

`core/llm_router/pricing.py` tabula 30+ modelos en USD/M tokens. Cada provider stampa `last_cost_usd`, `total_cost_usd`, `total_calls` tras cada call. Llamar `provider.get_cost_record(phase="hunt")` devuelve un `LLMCostRecord` agregable.

---

## Validar tu configuración

```bash
uv run contenido config-check
```

Output esperado:
```
=== Configuración cargada ===
  Env: dev
  Default mode: premium
  Redis: ✓

=== LLM Providers ===
  ✓ openrouter
  ✗ openai
  ...
```

`✓` = api_key configurada. `✗` = vacía (no usable).
