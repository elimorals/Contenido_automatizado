# Estado actual — ecosistema completo

Fecha última actualización: 2026-06-15

## 🎉 Sistema operativo

`contenido` es ahora un **ecosistema de creación de contenido con IA** que cubre:

1. **Pipeline industrial** (de MoneyPrinterTurbo): API REST + colas + WebUI + 20 LLM providers + 6 TTS engines
2. **DAG cognitivo** (de reels-af): 18 reasoners con delayed-reveal narrative
3. **Capa editorial** (de corredor-content): brand voice as code + facts.json + gate humano
4. **Higgsfield**: DoP (i2v) + Soul (character) + Effects (VFX) + CLI fallback
5. **ComfyUI nativo**: 7 workflows pre-armados + LoRA training wizard + multi-tenant + observability

Los 3 entry points (article URL, topic, subject) + workflow editorial (plan→approve→produce) están operacionales con el pipeline completo conectado.

## 📊 Métricas actuales

| Capa | Archivos | LOC aprox | Tests |
|---|---:|---:|---:|
| `shared/` (schemas + config) | 2 | ~1,300 | 10 |
| `apps/api/` (FastAPI + worker + pipeline) | 4 | ~1,700 | — |
| `apps/cli/` (Typer + comfy subapp) | 2 | ~900 | 3 |
| `apps/webui/` (Streamlit) | 2 | ~600 | — |
| `core/narrative/` (18 reasoners + facts injection) | 10 | ~1,450 | (in DAG) |
| `core/planning/` (beats, cards, font, safe_zone) | 5 | ~380 | (in DAG) |
| `core/llm_router/` (10 providers + pricing) | 13 | ~1,400 | 25 |
| `core/editorial/` (plan/validate/loader + brand-visual) | 4 | ~700 | 29 |
| `core/tts/` (6 engines + sample-accurate timing) | 12 | ~1,680 | 46 |
| `core/visual/generation/` (Gemini, Veo, Higgsfield x4, ComfyUI x3, ken-burns) | 14 | ~3,200 | 75 |
| `core/comfy/` (wrapper + training wizard) | 3 | ~600 | (in test_comfyui) |
| `core/editor/` (ffmpeg + multi-aspect + hw encoders) | 5 | ~760 | 12 |
| `core/subtitles/` (word-burst + SRT + Whisper) | 5 | ~980 | 8 |
| `core/distribution/` (Upload-Post) | 5 | ~510 | 7 |
| `orchestration/` (queue + state + AgentField) | 13 | ~720 | 18 |
| `editorial/` (brand-voice/facts/pillars/audiences/platforms/brand-visual) | 13 | ~700 (yaml/md) | — |
| `workflows/` (7 JSONs + index.json + README) | 9 | ~600 (JSON) | — |
| `scripts/` (Higgsfield AB + ComfyUI AB) | 3 | ~800 | — |
| `tests/` | ~24 | ~6,500 | **383** |
| `docs/` (12 archivos) | 12 | ~3,500 | — |

**Compile check global**: ✅ EXIT 0
**Tests verdes**: **383 / 383** (100%)

## 🛣️ Pipeline E2E funcional

```
Request → apps/api/main.py:POST /videos
       → orchestration/queue (Redis o memory)
       → apps/api/worker.py
       → apps/api/pipeline.py:run_pipeline
            ├─ article: extract → compose → shared downstream (10 reasoners)
            ├─ topic:   hunters × 4 → critic → narrators × 3 → judge → adapt → shared downstream (18 reasoners)
            └─ subject: 1 LLM call → terms → shared downstream (legacy MPT)

       Shared downstream:
         A. core/tts.synthesize             → AudioArtifact (sample-accurate)
         B. core/planning.pack_cards | plan_beats  (paralelo)
         C. core/narrative.plan_beat_visuals | plan_beat_accents  (paralelo)
         D. core/visual.generate_all_beat_artifacts (selector híbrido):
              Tier 1 first frame: ComfyUI (brand LoRA) → Higgsfield Soul → Gemini Image → placeholder
              Tier 2 motion:      Higgsfield DoP → Veo i2v → ken-burns
              Tier 3 effects:     Higgsfield Effects (post-step opcional)
         E. core/subtitles.write_reel_ass_with_accents
         F. core/editor.stitch_video (single-pass ffmpeg)
         G. core/distribution.upload_video (opcional)

       → TaskInfo.state = COMPLETE
       → response: reel.mp4 + result.json (timings, costs, narration)

Workflow editorial paralelo:
   plan (LLM gen 7 ideas) → humano marca approved → produce-week itera → invoca pipeline anterior
```

## ✅ Lo que está integrado

### Industrial (MPT)
- API REST con colas + backpressure 429
- WebUI Streamlit (3 modos)
- 10 LLM providers async + pricing tabulado (30+ modelos)
- 6 TTS engines (Edge gratis, Gemini Flash con tags, Azure, MiMo, SiliconFlow, Silent)
- Sample-accurate timing universal (ffprobe + atempo + word distribution)
- Stock Pexels/Pixabay/Coverr con rotación de keys
- Upload-Post TikTok + Instagram
- Multi-aspect (9:16, 16:9, 1:1)
- Estado Redis con índices

### Cognitivo (reels-af)
- 18 reasoners DAG (4 hunters + critic + 3 narrators + judge + adapt + extract + compose + visual + accent)
- Delayed-reveal con loop-back validator Pydantic
- Hunters multi-ángulo con anti-clichés + **facts injection** desde editorial
- Word-burst karaoke libass (170px, bottom-center, per-word timing)
- Per-beat visual grounding en evidence
- Single-pass ffmpeg + hardware encoder fallback

### Editorial (corredor-content)
- Brand voice as code (markdown versionado)
- `facts.json` anti-alucinación inyectado a 4 hunters
- Plan → approve → produce-week gate humano
- 5 pilares de contenido con rotación
- 6 platform specs (TikTok/IG Reels/YT Shorts/YT Long/FB Reels/LinkedIn)
- Cost transparency (LLMCostRecord agregable a TaskInfo)

### Higgsfield
- DoP image-to-video con 50+ camera presets
- Soul para character consistency cross-beat
- Effects VFX overlay
- Prompts canónicos del repo oficial (submodule)
- CLI fallback vía subprocess cuando REST falla
- A/B harness Veo vs Higgsfield DoP

### ComfyUI nativo
- HTTP + WebSocket client async con polling fallback
- 7 workflows registrados:
  - `flux_basic_9x16`, `flux_lora_brand`, `flux_controlnet_pose`
  - `sdxl_ipadapter_style`, `animatediff_lora` (video)
  - `inpaint_brand`, `upscale_face_restore`
- 3 tenants demo (`default`/`ruteo`/`ciencia`)
- LoRA training wizard (Replicate cloud + kohya local) con validation
- OOM auto-retry (POST /free + retry 1x)
- Workflow versioning (SHA256 corto)
- Reference image auto-upload
- Observability (last_job + cost_record)
- A/B harness ComfyUI brand LoRA vs Gemini Image
- comfy-cli wrapper para install/launch/lora/node management

## 📂 Estructura final

```
contenido/
├── apps/                  # FastAPI + WebUI + CLI (incluye `comfy` subapp)
├── core/
│   ├── narrative/         # 18 reasoners DAG + facts injection
│   ├── planning/          # beats, cards, safe_zone
│   ├── llm_router/        # 10 providers + pricing.py
│   ├── editorial/         # plan/validate/loader + brand-visual
│   ├── tts/               # 6 engines + sample-accurate
│   ├── visual/generation/ # Gemini + Veo + Higgsfield + ComfyUI + ken-burns
│   ├── comfy/             # comfy-cli wrapper + training wizard
│   ├── editor/            # ffmpeg single-pass + hw encoders
│   ├── subtitles/         # word-burst + SRT + Whisper
│   └── distribution/      # Upload-Post
├── editorial/             # brand-voice + facts + pillars + audiences + platforms + brand-visual
├── workflows/             # 7 ComfyUI JSONs + index.json
├── orchestration/         # queue + state + AgentField
├── shared/                # schemas + config
├── tests/                 # 383 tests
├── scripts/               # higgsfield_ab_test + comfyui_ab_test
├── docs/                  # 12 archivos (incl. EDITORIAL, COMFYUI, DECISIONS)
└── .claude/skills/        # Submodule higgsfield-ai/skills (dev-only)
```

## 🚀 Para correr

```bash
# Setup mínimo
cp .env.example .env       # editar con tus keys (mín: OPENROUTER_API_KEY + PEXELS_API_KEYS)
cp config.example.toml config.toml
uv sync --extra dev

# Verificar
uv run pytest -q --ignore=tests/integration   # 383 tests
uv run python -m apps.cli.main config-check
uv run python -m apps.cli.main brand-check

# Single-shot reels
uv run python -m apps.cli.main topic "the placebo effect" --mode premium
uv run python -m apps.cli.main article "https://arxiv.org/abs/2509.25541"
uv run python -m apps.cli.main subject "Spring flowers" --mode express

# Workflow editorial
uv run python -m apps.cli.main plan --ideas 7
$EDITOR out/plans/plan-2026-W24.json
uv run python -m apps.cli.main produce-week --mode premium

# ComfyUI (opcional, para brand identity vía LoRA)
uv run python -m apps.cli.main comfy install
uv run python -m apps.cli.main comfy launch --background
uv run python -m apps.cli.main comfy lora train --name miMarca --image-dir ./fotos --backend replicate
uv run python -m apps.cli.main comfy workflow list

# Stack completo con WebUI + Redis
make docker-up
```

## ⚠️ Qué falta para producción real

Esto es un MVP completo. Antes de meter usuarios reales:

1. **Tests E2E reales con servicios cloud** — los unit tests pasan con mocks; falta ejecución completa con APIs vivas
2. **LoRA real entrenada** — los tenants `ruteo` y `ciencia` apuntan a `.safetensors` que no existen (placeholders)
3. **ComfyUI server real corriendo** — el código asume `127.0.0.1:8188` o managed; falta validar E2E con GPU
4. **Métricas Prometheus** — actualmente solo Loguru
5. **Rate limiting per-user** — backpressure global pero no per-tenant
6. **Auth/multi-tenant API** — endpoints son públicos
7. **CDN para outputs** — videos desde filesystem local
8. **Whisper model cache** — descarga la primera vez (~2GB para large-v3)
9. **Anthropic + Gemini providers cost stamping** — heredan la base pero no overridean `_extract_usage`

## 🎯 Próximos pasos sugeridos

| Prioridad | Tarea |
|---|---|
| 🔴 Alta | Conseguir GPU (RTX 4090 local o RunPod) y validar pipeline ComfyUI E2E |
| 🔴 Alta | Entrenar primera LoRA real con `comfy lora train --backend replicate` |
| 🔴 Alta | Correr `make docker-up` y validar pipeline editorial end-to-end con un reel real |
| 🟡 Media | Anthropic + Gemini providers overridear `_extract_usage` para cost tracking |
| 🟡 Media | A/B harness real (ComfyUI vs Gemini) con votos humanos |
| 🟡 Media | Configurar Sentry o Logfire para error tracking |
| 🟢 Baja | Portar i18n completo de MPT (10 idiomas) |
| 🟢 Baja | Workflows ComfyUI adicionales (flux_canny, sdxl_lcm_speed, sdxl_lora_layered) |

## 🧬 Origen de cada componente

| Componente | Origen | Adaptación |
|---|---|---|
| Schemas Pydantic | MPT 18 + reels-af 22 modelos | Merge unificado + Editorial schemas (15 nuevos) + ComfyUI schemas (8) |
| Config TOML+env | MPT pattern | Loader con cache + 12-factor + ComfyUI tenants |
| FastAPI endpoints | MPT base | Async + /narratives, /hunters + workflow editorial |
| Streamlit WebUI | MPT base | Rediseñada con 3 vistas + polling API |
| Typer CLI | reels-af base | 3 entry points + plan/produce-week/brand-check + comfy subapp |
| 18 reasoners DAG | reels-af agents/ | Imports a shared.schemas + facts injection en hunters |
| Planning (beats, cards) | reels-af planning/ | Sin cambios |
| 10 LLM providers | MPT llm.py | Refactor sync → async + pricing.py + cost stamping |
| 6 TTS engines | MPT voice.py + reels-af tts.py | Sample-accurate timing universal |
| Stock Pexels/Pixabay/Coverr | MPT material.py | requests → httpx async + ffprobe |
| Gemini Image + Veo | reels-af images.py + video.py | httpx directo a OpenRouter multimodal |
| Higgsfield (DoP/Soul/Effects) | nuevo (este proyecto) | REST + CLI fallback + 50+ camera presets |
| ComfyUI nativo | nuevo (este proyecto) | REST+WS client + 7 workflows + training wizard + multi-tenant |
| Editorial (brand-voice/facts/plan) | corredor-content | Portado a Python + integración con DAG |
| Ken-burns | reels-af video.py | ffmpeg directo async |
| ffmpeg single-pass | reels-af stitch.py | + multi-aspect MPT + hardware encoder fallback |
| Word-burst libass | reels-af subtitles.py | pysubs2 + CJK font fallback |
| Whisper ASR | MPT subtitle.py | Lazy import + lru_cache |
| Redis queue + state | MPT manager/ + state.py | threading → asyncio + índices Redis |
| Upload-Post | MPT upload_post.py | requests → httpx async + tenacity |
| Pipeline orchestrator | nuevo | Pega los 3 entry points + workflow editorial |
| A/B harnesses | nuevo | Higgsfield vs Veo + ComfyUI vs Gemini |
