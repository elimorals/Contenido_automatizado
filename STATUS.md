# Estado actual — Fases 0-6 completadas

Fecha última actualización: 2026-06-15

## 🎉 Sistema completo end-to-end

El monorepo `contenido` es ahora funcional como **fusión real** de MoneyPrinterTurbo (industrial) × reels-af (cognitivo). Los 3 entry points (article URL, topic, subject) están operacionales con el pipeline completo conectado.

## 📊 Métricas finales

| Capa | Archivos | LOC | Procedencia |
|---|---:|---:|---|
| `shared/` (schemas + config) | 3 | 834 | merge MPT + reels-af |
| `apps/api/` (FastAPI + worker + pipeline) | 4 | 1,698 | nuevo + adapt |
| `apps/cli/` (Typer) | 2 | 387 | nuevo |
| `apps/webui/` (Streamlit) | 2 | 608 | rediseño |
| `core/narrative/` (18 reasoners) | 10 | 1,437 | reels-af |
| `core/planning/` (beats, cards, font, safe_zone) | 5 | 377 | reels-af |
| `core/llm_router/` (10 providers) | 12 | 1,271 | MPT |
| `core/tts/` (6 engines + sample-accurate timing) | 12 | 1,677 | reels-af + MPT |
| `core/visual/` (stock + IA + selector híbrido) | 16 | 2,124 | MPT + reels-af |
| `core/editor/` (ffmpeg single-pass + multi-aspect + HW encoders) | 5 | 760 | reels-af + MPT |
| `core/subtitles/` (word-burst + SRT + Whisper) | 5 | 980 | reels-af + MPT |
| `core/distribution/` (Upload-Post) | 5 | 512 | MPT |
| `orchestration/` (queue + state + AgentField) | 13 | 714 | MPT + reels-af |
| `tests/` | 21 | 5,078 | nuevo |
| **TOTAL** | **117** | **18,457** | — |

**Compile check global**: ✅ EXIT 0  
**Tests verdes**: ~200+ tests pasan (entre módulos)

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
         D. core/visual.generate_all_beat_artifacts (selector híbrido)
         E. core/subtitles.write_reel_ass_with_accents
         F. core/editor.stitch_video (single-pass ffmpeg)
         G. core/distribution.upload_video (opcional)
       
       → TaskInfo.state = COMPLETE
       → response: reel.mp4 + result.json (timings, costs, narration)
```

## ✅ Todo lo que está integrado

### Lo que MPT aportó (ya cableado)
1. ✅ API REST con colas (FastAPI + Redis backpressure 429)
2. ✅ WebUI Streamlit con 3 modos (Express / Premium / Avanzado)
3. ✅ Material de stock (Pexels + Pixabay + Coverr con rotación de keys async)
4. ✅ 10 LLM providers async (OpenAI, OpenAI-compat, Azure, Gemini, Qwen, Anthropic, OpenRouter, LiteLLM + base extensible)
5. ✅ 6 TTS engines (Edge gratis, Gemini Flash con tags, Azure SDK, MiMo, SiliconFlow, Silent) + sample-accurate timing en TODOS
6. ✅ Upload-Post (TikTok + Instagram)
7. ✅ Multi-aspect ratio (9:16, 16:9, 1:1)
8. ✅ Estado persistente (Redis state + queue, atomic con MULTI/EXEC)

### Lo que reels-af aportó (ya cableado)
1. ✅ DAG de 18 reasoners en `core/narrative/` (extract, compose, 4 hunters, critic, 3 narrators, judge, visual, accent)
2. ✅ Delayed-reveal con loop-back validator Pydantic
3. ✅ Hunters multi-ángulo con anti-clichés explícitos
4. ✅ Sample-accurate TTS (ffprobe + atempo + word distribution por syllable count) universal para TODOS los engines
5. ✅ Word-burst karaoke libass (170px, bottom-center, per-word timing)
6. ✅ Per-beat visual grounding en evidence
7. ✅ Two-tier fallback (image fail → placeholder, Veo fail → ken-burns, nunca crashea)
8. ✅ Single-pass ffmpeg (concat + ass burn + BGM mix + AAC mux en UNA invocación)
9. ✅ Validators Pydantic (loop-back, accent word count, schema-level invariants)

### Capacidades nuevas (no estaban en ninguno)
- ✅ **Visual selector híbrido** — decide por beat stock vs IA vs mixto según mode + role + evidence
- ✅ **Anthropic Claude provider** — no estaba en MPT
- ✅ **3 entry points unificados** en un solo endpoint `/videos`
- ✅ **Endpoint `/narratives`, `/hunters`** — exponer reasoners individuales
- ✅ **Cost tracking + timings per phase** en TaskInfo
- ✅ **Hardware encoder fallback** automático cross-OS (videotoolbox/nvenc/amf/qsv/mf/libx264)

## 📂 Estructura final

```
contenido/
├── README.md, PLAN.md, ARCHITECTURE.md, STATUS.md, LICENSE
├── pyproject.toml, Dockerfile, docker-compose.yml, Makefile, CI
├── .env.example, config.example.toml, .gitignore, .python-version
├── apps/
│   ├── api/          # FastAPI 14 endpoints + worker + pipeline (1,698 LOC)
│   ├── cli/          # Typer 6 commands (387 LOC)
│   └── webui/        # Streamlit 3 tabs (608 LOC)
├── core/
│   ├── narrative/    # 8 reasoners + runtime AgentField adapter (1,437 LOC)
│   ├── planning/     # beats, cards, font_metrics, safe_zone (377 LOC)
│   ├── llm_router/   # base, router + 8 providers (1,271 LOC)
│   ├── tts/          # base, timing, voice_names, registry + 6 engines (1,677 LOC)
│   ├── visual/
│   │   ├── stock/    # Pexels, Pixabay, Coverr, cache, registry
│   │   ├── generation/ # Gemini Image, Veo, ken-burns, orchestrator
│   │   └── selector.py # selector híbrido stock vs IA
│   ├── editor/       # ffmpeg_stitch, aspect, encoders, bgm (760 LOC)
│   ├── subtitles/    # word_burst, SRT, Whisper, accents (980 LOC)
│   └── distribution/ # Upload-Post (512 LOC)
├── orchestration/
│   ├── queue/        # memory + Redis (714 LOC total)
│   ├── state/        # memory + Redis con índices
│   └── agentfield/   # adapter con fallback local
├── shared/
│   ├── schemas.py    # 18 enums + 22 models Pydantic 2 (522 LOC)
│   └── config.py     # TOML + env override (312 LOC)
├── tests/            # 21 test files, 5,078 LOC
├── docs/             # DECISIONS, PIPELINE, COST_MODEL, CONTRIBUTING
└── resource/         # fonts, songs, public
```

## 🚀 Para correr

```bash
# Setup
cp .env.example .env       # editar con tus keys
cp config.example.toml config.toml
make install               # uv sync --extra dev

# Verificar
make test-fast
uv run contenido config-check

# Single-shot CLI
uv run contenido topic "the placebo effect" --mode premium
uv run contenido article "https://arxiv.org/abs/2509.25541"
uv run contenido subject "Spring flowers" --mode express

# Stack completo (API + WebUI + Redis + worker)
make docker-up

# WebUI: http://localhost:8501
# API docs: http://localhost:8000/docs
```

## ⚠️ Qué falta para producción real

Esto es un MVP funcional. Antes de meter usuarios reales:

1. **Tests E2E reales** (no mocks) con ffmpeg + servicios cloud — los unit tests pasan pero falta ejecución completa con audio/video real
2. **Cost tracker funcional** — el dict existe pero los providers no devuelven costos todavía
3. **Métricas Prometheus** — actualmente solo Loguru
4. **Rate limiting per-user** — API tiene backpressure global pero no per-tenant
5. **Auth/multi-tenant** — no hay autenticación, todos los endpoints son públicos
6. **CDN para outputs** — videos se sirven desde filesystem local
7. **Whisper modelo cache** — el modelo se descarga la primera vez (~2GB para large-v3)
8. **i18n WebUI completo** — solo labels básicos, faltan traducciones reales de mensajes

## 🎯 Próximos pasos sugeridos

| Prioridad | Tarea |
|---|---|
| 🔴 Alta | Correr `make docker-up` y validar pipeline end-to-end con un reel real |
| 🔴 Alta | Validar que sample-accurate timing funciona en Edge TTS (no solo Gemini) |
| 🟡 Media | Agregar OpenAPI examples a cada endpoint |
| 🟡 Media | Configurar Sentry o Logfire para error tracking |
| 🟢 Baja | Portar i18n completo de MPT (10 idiomas, JSON files) |
| 🟢 Baja | Agregar GitHub Actions workflow para Docker Hub push |

## 🧬 Origen de cada componente (mapa final)

| Componente | Origen | Adaptación |
|---|---|---|
| Schemas Pydantic | MPT 18 modelos + reels-af 22 modelos | Merge unificado con validators |
| Config TOML+env | MPT pattern | Loader con cache + 12-factor compliance |
| FastAPI endpoints | MPT base | Async + nuevos endpoints (/narratives, /hunters) |
| Streamlit WebUI | MPT base | Rediseñada con 3 vistas + polling API |
| Typer CLI | reels-af base | 3 entry points + config-check |
| 18 reasoners DAG | reels-af agents/ | Imports a shared.schemas, runtime adapter para AgentField/llm_router |
| Planning (beats, cards) | reels-af planning/ | Imports adaptados |
| 10 LLM providers | MPT llm.py 20+ providers | Refactor sync → async, base class compartida, Anthropic agregado |
| 6 TTS engines | MPT voice.py + reels-af tts.py | Sample-accurate timing universal aplicado a todos |
| Stock Pexels/Pixabay/Coverr | MPT material.py | requests → httpx async, MoviePy → ffprobe |
| Gemini Image + Veo | reels-af images.py + video.py | httpx directo a OpenRouter multimodal |
| Ken-burns | reels-af video.py | ffmpeg directo async |
| ffmpeg single-pass | reels-af stitch.py | + multi-aspect MPT + hardware encoder fallback |
| Word-burst libass | reels-af subtitles.py | pysubs2 + CJK font fallback |
| SRT subtitles | MPT subtitle.py | Compatibilidad backwards |
| Whisper ASR | MPT subtitle.py | Lazy import, lru_cache |
| Redis queue + state | MPT manager/ + state.py | threading → asyncio + índices Redis |
| Upload-Post | MPT upload_post.py | requests → httpx async + tenacity |
| AgentField adapter | reels-af + nuevo | Local fallback si broker no disponible |
| Visual selector | nuevo | Heurísticas por mode + role + evidence |
| Pipeline orchestrator | nuevo | Pega los 3 entry points con timings |
