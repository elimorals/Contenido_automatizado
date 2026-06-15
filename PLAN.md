# Plan de integración — 6 fases

Ventana objetivo: **4-6 semanas** | Modelo: **Fusión real (monorepo)** | Caso primario: **Calidad cinematográfica premium** | Stack base: **ffmpeg directo + AgentField**.

---

## Fase 0 — Cimientos (semana 1)

**Objetivo**: estructura de proyecto, dependencias, CI verde, sin código portado todavía.

### Entregables
- [x] Estructura de directorios (`apps/`, `core/`, `orchestration/`, `shared/`)
- [x] `pyproject.toml` con dependencias combinadas (sin MoviePy)
- [x] `.env.example` y `config.example.toml` unificados
- [x] `Dockerfile` y `docker-compose.yml` (API + Redis + control-plane AgentField)
- [x] `shared/schemas.py` — Pydantic 2 con todos los modelos (MPT + reels-af mergeados)
- [x] `shared/config.py` — loader unificado (TOML como fuente de verdad, env override)
- [x] CI (lint con ruff, tests con pytest, build de imagen Docker)
- [ ] Pre-commit hooks
- [ ] `Makefile` con comandos comunes
- [ ] Docs: README, PLAN, ARCHITECTURE, DECISIONS

### Decisiones técnicas tomadas
| Decisión | Elección | Razón |
|---|---|---|
| Motor de video | ffmpeg directo | Single-pass sin drift, mejor performance |
| Bus de reasoners | AgentField | Mantiene compatibilidad con DAG de reels-af |
| Config | `config.toml` + `.env` override | TOML para complejo, env para secretos/12-factor |
| Default LLM | DeepSeek V4 Pro (premium) / Edge TTS gratis (express) | Costo bajo, calidad alta |
| Default TTS | Gemini Flash (tags inline) | Único con audio tags inline |
| Subtítulos | libass word-burst | Estilo viral, sin bugs de drawtext |
| Python | 3.11 (rango 3.11-3.12) | Intersección de ambos repos |

---

## Fase 1 — Core unificado (semana 2)

**Objetivo**: LLM router y TTS engines portados con interfaz común. Sample-accurate timing para TODOS los engines.

### 1.1 LLM Router universal (`core/llm_router/`)
- Portar los 20+ providers de MPT (`app/services/llm.py`) a una interfaz que AgentField pueda consumir como driver.
- Mantener el `_generate_response()` style de routing por config.
- Capa de adaptación: cualquier reasoner de reels-af puede ahora correr con DeepSeek/Qwen/MiMo (costo bajo) en vez de solo OpenRouter.
- Tests: cada provider con mock, fallback automático.

### 1.2 TTS unificado (`core/tts/`)
- Interfaz común `TTSEngine` (abstracta), drivers concretos:
  - `engines/edge.py` — Edge TTS (gratis, default express)
  - `engines/gemini_flash.py` — Gemini Flash (tags inline, default premium)
  - `engines/azure.py` — Azure SDK
  - `engines/mimo.py` — Xiaomi MiMo
  - `engines/siliconflow.py` — SiliconFlow CosyVoice2
  - `engines/silent.py` — No-voice (para videos sin narración)
- **`core/tts/timing.py`** — portar el sample-accurate timing de reels-af (`render/tts.py`):
  - Split por sentence + paralelo
  - ffprobe measure por WAV
  - atempo=1.35 con preserve pitch
  - Word distribution por syllable count sobre measured span
- **Esto es el upgrade clave**: TTS de MPT (Edge SubMaker) tiene drift; ahora todos los engines tendrán timing sample-accurate.

### Entregables
- [ ] `core/llm_router/router.py` con 20+ providers funcionando
- [ ] `core/tts/timing.py` portado y testeado
- [ ] `core/tts/engines/*.py` (5 engines + silent)
- [ ] Tests unitarios por provider/engine
- [ ] Benchmark de costo por provider (incluido en `docs/COST_MODEL.md`)

---

## Fase 2 — Pipeline narrativo (semana 3)

**Objetivo**: portar los 18 reasoners de reels-af. Tres entry points en la API.

### 2.1 Reasoners (`core/narrative/`)
Portar de `src/reel_af/agents/`:
- `extract.py` — URL → Essence (article path)
- `compose.py` — Essence → ScriptDraft (Hook→Mechanism→Payoff)
- `hunters.py` — 4 hunters paralelos (specific_figure, reversal, temporal, cross_domain)
- `critic.py` — 12 candidates → top 3
- `narrator.py` — 3 delayed-reveal narrations paralelas
- `judge.py` — pairwise → 1 winner
- `visual.py` — per-beat image prompts
- `accent.py` — per-beat editorial overlays (6 patrones)

### 2.2 Planning determinístico (`core/planning/`)
Portar de `src/reel_af/planning/`:
- `beats.py` — ScriptDraft → Beats con Veo buckets (4/6/8s)
- `cards.py` — WordTiming[] → Cards (layout subtítulos)
- `font_metrics.py` — Montserrat Bold metrics
- `safe_zone.py` — Canvas 1080×1920 safe zones

### 2.3 Tres entry points en `apps/api/`
```python
POST /videos
  {"url": "..."}              # article path (10 reasoners)
  {"topic": "..."}            # topic path (18 reasoners)
  {"subject": "..."}          # legacy MPT path (1 LLM call, rápido)
  {"mode": "express|premium"} # override del modo
```

### Entregables
- [ ] 18 reasoners portados a `core/narrative/`
- [ ] 4 módulos de planning portados
- [ ] Endpoint `/videos` unificado con tres modos
- [ ] WebUI Streamlit con selector de modo
- [ ] Tests E2E de los tres pipelines

---

## Fase 3 — Pipeline visual híbrido (semana 4)

**Objetivo**: selector híbrido stock-vs-IA por beat. Multi-aspect ratio. Multi-encoder hardware.

### 3.1 Stock (`core/visual/stock/`)
Portar de MPT (`app/services/material.py`):
- `pexels.py`, `pixabay.py`, `coverr.py`
- Rotación de API keys thread-safe
- Cache por URL hash
- Validación con ffprobe (no MoviePy)

### 3.2 Generación IA (`core/visual/generation/`)
Portar de reels-af:
- `gemini_image.py` — first frames 720×1280
- `veo.py` — i2v opcional (Veo 3.1 Lite)

### 3.3 Selector híbrido (`core/visual/selector.py`)
Por beat decide entre:
- **Stock** (Pexels/Pixabay/Coverr) — barato, rápido
- **IA** (Gemini Image + Veo) — específico, premium
- **Mixto** — primer plano IA + cortes de stock para variedad

Heurísticas:
- Si `beat.role == "hook"` y modo premium → IA
- Si `beat.role == "mechanism"` y evidence tiene número/nombre → IA
- Si `beat.role == "payoff"` y modo express → stock
- Override manual desde request

### 3.4 Aspect ratios (`core/editor/aspect.py`)
reels-af solo hace 9:16. Portar lógica de canvas de MPT:
- 9:16 (1080×1920) — TikTok, Reels, Shorts
- 16:9 (1920×1080) — YouTube, web
- 1:1 (1080×1080) — Instagram Feed

### 3.5 Hardware encoders (`core/editor/encoders.py`)
Portar fallback automático de MPT:
- libx264 → h264_nvenc (NVIDIA) → h264_amf (AMD) → h264_qsv (Intel) → h264_mf (Windows) → h264_videotoolbox (macOS)

### Entregables
- [ ] 3 providers de stock funcionando
- [ ] Gemini Image + Veo i2v portados
- [ ] Selector híbrido con heurísticas configurables
- [ ] Multi-aspect funcionando (3 ratios)
- [ ] Multi-encoder con detección de hardware

---

## Fase 4 — Subtítulos y editor final (semana 5)

**Objetivo**: word-burst libass como default + SRT tradicional como fallback. BGM. i18n.

### 4.1 Subtítulos (`core/subtitles/`)
- `word_burst.py` — portar de reels-af (`render/subtitles.py`), libass con pysubs2
- `srt.py` — fallback compatible con MPT (Whisper o Edge SubMaker)
- `whisper.py` — faster-whisper para audio personalizado (de MPT)
- 6 patrones de accents integrados (number, named_entity, jargon, hook_title, reaction, list)

### 4.2 Editor final (`core/editor/`)
- `ffmpeg_stitch.py` — single-pass de reels-af (`render/stitch.py`)
- `bgm.py` — biblioteca de songs de MPT integrada al single-pass
- Subtítulos quemados con libass en el mismo pass

### 4.3 i18n
Portar de MPT (`webui/i18n/`):
- 10 idiomas (zh-CN, zh-HK, zh-TW, en-US, fr-FR, de-DE, ru-RU, vi-VN, th-TH, tr-TR)
- CJK fonts en `resource/fonts/`
- Selector de idioma en WebUI

### Entregables
- [ ] Word-burst karaoke funcionando en 3 aspect ratios
- [ ] SRT fallback compatible con MPT existente
- [ ] BGM integrada al single-pass ffmpeg
- [ ] 10 idiomas portados a WebUI

---

## Fase 5 — Orchestration y distribución (semana 6)

**Objetivo**: colas Redis, WebUI rediseñada, Upload-Post, endpoints avanzados.

### 5.1 Colas y estado (`orchestration/`)
Portar de MPT:
- `queue/` — `InMemoryTaskManager` + `RedisTaskManager`
- `state/` — `MemoryState` + `RedisState`
- `agentfield/` — broker AgentField (control-plane)
- `max_concurrent_tasks` y `max_queued_tasks` aplicando al DAG completo

### 5.2 WebUI (`apps/webui/`)
Rediseño en dos vistas:
- **Express** — clásico MPT (subject + voice + materials)
- **Premium** — slider de calidad, preview de reasoners en vivo, A/B test de hunters
- Visualización de timings (de `result.json`)
- Cost tracker integrado

### 5.3 Distribución (`core/distribution/`)
Portar de MPT (`app/services/upload_post.py`):
- TikTok + Instagram via Upload-Post
- Cross-post automático opcional
- Status check

### 5.4 Endpoints avanzados
```python
POST /narratives    # solo script delayed-reveal (sin video)
POST /hunters       # devuelve 12 candidates (para A/B testing externo)
POST /judge         # compara 2 narraciones (juez externo)
GET  /costs/{task}  # breakdown de costo por proveedor
GET  /timings/{task} # breakdown de tiempo por reasoner
```

### Entregables
- [ ] Redis queue envolviendo reasoners
- [ ] WebUI con vista Express + Premium
- [ ] Upload-Post integrado
- [ ] 4 endpoints nuevos documentados en OpenAPI

---

## Fase 6 — Testing y hardening (semana 6+)

**Objetivo**: producción-ready. Tests E2E, observabilidad, Docker optimizado.

### 6.1 Tests E2E (`tests/integration/`)
- Article path completo (URL → MP4)
- Topic path completo (topic → MP4)
- Legacy MPT path (subject → MP4)
- Fallbacks (Veo fail → ken-burns, image fail → placeholder)
- Multi-aspect (9:16, 16:9, 1:1)
- Multi-idioma (CJK + latín)

### 6.2 Observabilidad
- Loguru (de MPT) con timings de reels-af
- Métricas Prometheus opcionales
- Cost tracker persistente
- `result.json` sidecar por task (de reels-af)

### 6.3 Docker optimizado
- Multi-stage: imagen ligera para API, imagen GPU para encoding
- Cache de modelos Whisper en volume
- Healthcheck endpoints

### 6.4 Documentación
- OpenAPI completo
- Tutorial WebUI
- Cookbook (10 casos de uso)
- Migration guide desde MPT existente

### Entregables
- [ ] Cobertura tests >75% en core/
- [ ] Pipeline E2E < 110s en modo premium (matching reels-af baseline)
- [ ] Pipeline E2E < 8min en modo express
- [ ] Docker imagen API < 500MB
- [ ] Cost por reel reportado en cada response

---

## Hitos críticos

| Semana | Hito | Criterio "done" |
|---|---|---|
| 1 | Scaffolding | `docker compose up` levanta sin código portado |
| 2 | Core | Generar audio con timings sample-accurate desde 6 engines |
| 3 | Narrativa | `/videos {topic: "..."}` devuelve ScriptDraft válido |
| 4 | Visual | Reel completo en 9:16 con stock + IA mixto |
| 5 | Orchestration | 5 reels concurrentes en WebUI sin colisiones |
| 6 | Producción | Reel premium de demo publicado a TikTok |

## Riesgos en seguimiento

| Riesgo | Mitigación |
|---|---|
| AgentField broker requiere control-plane separado | Validar en Fase 0 con docker-compose |
| Sample-accurate timing solo testeado con Gemini Flash | Adaptar atempo por engine en Fase 1 |
| Veo i2v puede tener cuotas/throttling | Fallback ken-burns siempre activo |
| Costo de DeepSeek puede subir | Monitor + selector dinámico en Fase 5 |
| MPT y reels-af pueden divergir upstream | Documentar SHAs base + cherry-pick selectivo |
