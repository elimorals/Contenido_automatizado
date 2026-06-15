# Arquitectura

## Visión de alto nivel

```
                         ┌─────────────────────────────────┐
                         │  CLIENTES (3 entry points)      │
                         │  ├─ Streamlit WebUI             │
                         │  ├─ FastAPI REST                │
                         │  └─ Typer CLI                   │
                         └────────────┬────────────────────┘
                                      │
                         ┌────────────▼────────────────────┐
                         │  ORQUESTACIÓN                   │
                         │  ├─ Redis Queue (concurrency)   │
                         │  ├─ Redis State (persistence)   │
                         │  └─ AgentField Broker (DAG)     │
                         └────────────┬────────────────────┘
                                      │
        ┌─────────────────────────────┴──────────────────────────────┐
        │                                                            │
   ┌────▼─────────┐   ┌─────────────┐   ┌──────────────┐   ┌────────▼────────┐
   │  NARRATIVE   │   │  PLANNING   │   │  TTS         │   │  VISUAL         │
   │  18 reasoners│──▶│ beats/cards │──▶│ 6 engines    │   │  Stock + IA     │
   │  AgentField  │   │ determinist │   │ sample-acc   │   │  Selector       │
   └──────────────┘   └─────────────┘   └──────┬───────┘   └────────┬────────┘
                                               │                    │
                                               └──────────┬─────────┘
                                                          │
                                              ┌───────────▼──────────┐
                                              │  EDITOR (ffmpeg)     │
                                              │  Single-pass concat  │
                                              │  + libass burn       │
                                              │  + AAC mux           │
                                              │  + Multi-encoder HW  │
                                              └───────────┬──────────┘
                                                          │
                                              ┌───────────▼──────────┐
                                              │  DISTRIBUTION        │
                                              │  Upload-Post         │
                                              │  TikTok + Instagram  │
                                              └──────────────────────┘
```

## Pipeline de generación (modo premium, topic-to-reel)

```
Topic
  │
  ├── PHASE 1: HUNT (4 reasoners paralelos, ~8s)
  │   ├── specific_figure  → 3 candidates
  │   ├── reversal         → 3 candidates
  │   ├── temporal         → 3 candidates
  │   └── cross_domain     → 3 candidates
  │
  ├── PHASE 2: CRITIC (1 reasoner, ~4s)
  │   └── 12 candidates → top 3 (angle diversity)
  │
  ├── PHASE 3: NARRATE (3 reasoners paralelos, ~8s)
  │   └── 3 delayed-reveal scripts
  │
  ├── PHASE 4: JUDGE (1 reasoner, ~3s)
  │   └── pairwise → 1 winner + score + why
  │
  ├── PHASE 5: ADAPT (determinístico)
  │   └── ConversationalScript → ScriptDraft
  │
  ├── PHASE 6: AUDIO (1 reasoner, ~12s)
  │   ├── Per-sentence parallel TTS (Gemini Flash o engine elegido)
  │   ├── ffprobe measure → sample-accurate boundaries
  │   ├── atempo=1.35 (preserve pitch)
  │   └── Word timings by syllable distribution
  │
  ├── PHASE 7: PLAN (paralelo, ~1s)
  │   ├── Cards (subtitle layout)
  │   └── Beats (Veo buckets 4/6/8s)
  │
  ├── PHASE 8: VISUAL+ACCENT (paralelo, ~7s)
  │   ├── Per-beat image_prompt (grounded en evidence)
  │   └── Per-beat accent overlay (6 patrones, biased a None)
  │
  ├── PHASE 9: MEDIA (per-beat, ~38s)
  │   ├── Visual selector:
  │   │   ├── Stock (Pexels/Pixabay/Coverr) — express
  │   │   ├── IA (Gemini Image + Veo i2v) — premium
  │   │   └── Mixto — variedad
  │   └── Two-tier fallback (image fail → placeholder; Veo fail → ken-burns)
  │
  └── PHASE 10: STITCH (single ffmpeg, ~5s)
      ├── Concat filter (sample-accurate)
      ├── libass burn (word-burst + accents)
      ├── BGM mix
      └── AAC mux
      
  → MP4 1080×1920 H.264+AAC + result.json
  → ~85-110s total, ~$0.08-0.10 (ken-burns) / ~$1.20 (Veo)
```

## Pipeline express (modo MPT clásico)

```
Subject
  │
  ├── PHASE 1: SCRIPT (1 LLM call, ~3s)
  ├── PHASE 2: TERMS (1 LLM call, ~2s)
  ├── PHASE 3: AUDIO (TTS engine, ~10s)
  ├── PHASE 4: SUBTITLE (Edge SubMaker o Whisper, ~5s)
  ├── PHASE 5: MATERIALS (Pexels paralelo, ~30s)
  └── PHASE 6: STITCH (ffmpeg single-pass, ~60s)
  
  → MP4 cualquier aspect H.264+AAC
  → ~3-5 min total, ~$0.01-0.05
```

## Decisiones técnicas (ADR resumido)

Ver [`docs/DECISIONS.md`](./docs/DECISIONS.md) para ADRs completos.

### ADR-001: ffmpeg directo vs MoviePy
**Elegimos**: ffmpeg directo.
**Razón**: MoviePy 2.2 introduce overhead y casos de drift en sample timing. El single-pass de reels-af (`render/stitch.py`) demostró output más limpio. Trade-off: menos legible que MoviePy.

### ADR-002: AgentField como bus de reasoners
**Elegimos**: AgentField.
**Razón**: reels-af ya está diseñado sobre AgentField; reescribir el DAG en LangGraph implicaría perder validators Pydantic y temperature-per-reasoner. Requiere control-plane containerizado.

### ADR-003: Config TOML + override env
**Elegimos**: `config.toml` como fuente de verdad, `.env` solo para secretos.
**Razón**: MPT tiene 376 líneas de config TOML organizadas en secciones (`[app]`, `[whisper]`, `[ui]`, `[upload_post]`). Mantenerlo como TOML preserva esa organización; env para 12-factor compliance.

### ADR-004: Pydantic 2 unificado
**Elegimos**: schemas en `shared/schemas.py`.
**Razón**: MPT y reels-af tienen 18 + 22 modelos respectivamente; muchos solapan (VideoParams ↔ ScriptDraft). Unificar evita drift y permite validators compartidos.

### ADR-005: Sample-accurate timing para TODOS los TTS
**Elegimos**: portar `ffprobe + atempo` de reels-af a todos los engines.
**Razón**: Edge TTS SubMaker (MPT) tiene drift acumulativo. El timing sample-accurate de reels-af es agnóstico al engine si se aplica post-síntesis.

### ADR-006: libass por defecto, SRT como fallback
**Elegimos**: word-burst libass es default; SRT compatible MPT como opción.
**Razón**: word-burst es el estilo viral 2025+; SRT broadcasting clásico se mantiene para usuarios que vienen de MPT.

### ADR-007: 9:16 como aspect default, 16:9/1:1 disponibles
**Elegimos**: portar lógica multi-aspect de MPT.
**Razón**: reels-af solo soportaba 9:16. Mantener los 3 ratios cubre TikTok/Reels/Shorts (9:16), YouTube/web (16:9), Instagram Feed (1:1).

## Flujo de datos (Pydantic types)

```
Input Request (VideoParams)
  ├── url | topic | subject
  ├── mode: "express" | "premium"
  ├── aspect: "9:16" | "16:9" | "1:1"
  ├── voice_name, voice_volume, voice_rate
  ├── bgm_type, bgm_file, bgm_volume
  ├── subtitle_style: "word_burst" | "srt"
  └── language

Essence (de extract)
  ├── core_claim
  ├── mechanism
  ├── evidence[]
  ├── content_mode: "scientific" | "general"
  └── domain

EssenceCandidate[] (de hunters, 12 items)
  └── + angle, novelty_pitch

CriticOutput (top 3 + rankings)

ConversationalScript[] (de narrators, 3 scripts)
  ├── tease, common_belief, reveal, payoff
  └── narration (con [tags] inline)

PairwiseVerdict (winner + score + why)

ScriptDraft (unificado)
  ├── hook, mechanism_lines[], payoff_line
  ├── narration
  └── target_wpm

WordTiming[] (sample-accurate)
  └── word, start_s, end_s

Beat[] (con Veo buckets)
  ├── idx, role, text
  ├── target_duration_s
  └── veo_duration: 4 | 6 | 8

Card[] (subtitle layout)
  ├── text, words[], start_s, end_s
  └── line_count

BeatVisual[] (per-beat prompts)
  ├── image_prompt (grounded)
  ├── motion_hint
  └── visual_anchor

AccentOverlay | None (per-beat)
  ├── text, pattern, position
  └── biased to None

BeatArtifact[]
  ├── first_frame_path
  └── video_path

Final Output
  ├── reel.mp4 (1080×1920, 20-25s)
  └── result.json
      ├── timings_s (per phase)
      ├── chosen_essence
      ├── winner_composite
      ├── cost_breakdown
      └── narration full
```

## Servicios containerizados

```yaml
# docker-compose.yml (esquema)
services:
  redis:
    image: redis:7-alpine
    
  control-plane:
    image: agentfield/control-plane:latest
    ports: ["8080:8080"]
    
  api:
    build: .
    command: uvicorn apps.api.main:app
    ports: ["8000:8000"]
    depends_on: [redis, control-plane]
    
  webui:
    build: .
    command: streamlit run apps/webui/Main.py
    ports: ["8501:8501"]
    depends_on: [api]
    
  worker:
    build: .
    command: python -m apps.api.worker
    depends_on: [redis, control-plane]
    deploy:
      replicas: 3  # max_concurrent_tasks
```

## Configuración por entorno

| Variable | Local dev | Staging | Prod |
|---|---|---|---|
| `CONTENIDO_ENV` | `dev` | `staging` | `prod` |
| `MAX_CONCURRENT_TASKS` | 1 | 3 | 10 |
| `ENABLE_REDIS` | false | true | true |
| `DEFAULT_MODE` | `express` | `premium` | `premium` |
| `ENABLE_VEO` | false | true | true |
| `ENABLE_UPLOAD_POST` | false | false | true |

## Métricas objetivo

| Métrica | Express | Premium |
|---|---|---|
| Wall time | < 8 min | < 110 s |
| Costo/reel | < $0.05 | < $0.15 (ken-burns) / < $1.30 (Veo) |
| Reels concurrentes | 10 | 3 |
| Disponibilidad | 99% | 99.5% |
| Cobertura tests | > 70% | > 80% |
