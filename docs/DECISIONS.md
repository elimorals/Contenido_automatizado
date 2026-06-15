# Architecture Decision Records

ADRs cronológicas. Cada decisión irreversible o de alto impacto se documenta aquí.

---

## ADR-001: ffmpeg directo en vez de MoviePy

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
MPT usa MoviePy 2.2.1 (~1,189 LOC en `app/services/video.py`). reels-af usa ffmpeg directo (`render/stitch.py`, single-pass). Necesitamos elegir el motor de edición para el monorepo fusionado.

### Decisión
**ffmpeg directo**, portando el single-pass de reels-af.

### Consecuencias
- ✅ Single-pass concat filter → sample-accurate, sin drift sub-frame
- ✅ libass burn (vs MoviePy drawtext que tiene alignment bugs)
- ✅ Hardware encoders más fáciles (nvenc/qsv/videotoolbox)
- ✅ ~3-5× más rápido en reels de 25s
- ❌ Menos legible que MoviePy
- ❌ Requiere mejor manejo de errores (subprocess Popen vs API Python)

### Mitigación de costos
Helper `core/editor/ffmpeg_stitch.py` con builder pattern para que los filter_complex sean legibles.

---

## ADR-002: AgentField como bus de reasoners

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
reels-af está construido sobre AgentField (DAG async de reasoners con validators Pydantic). Alternativas: LangGraph, Temporal, custom asyncio.

### Decisión
Mantener **AgentField** (decisión del usuario en sesión de planning).

### Consecuencias
- ✅ Cero refactor del DAG existente
- ✅ Temperature-per-reasoner ya implementada
- ✅ Structured output con Pydantic ya integrado
- ❌ Requiere control-plane containerizado (docker-compose)
- ❌ Dependencia externa (riesgo si proyecto se abandona)
- ⚠️ Validar en Fase 0 que `docker compose up` funciona

### Plan B
Si AgentField presenta problemas en producción, fork del runtime async + interfaces compatibles (`@reasoner` decorator → Pydantic schema → async call). Estimación: 2 semanas de migración.

---

## ADR-003: Config TOML + env override

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
MPT usa `config.toml` (376 líneas, secciones complejas). reels-af usa `.env` puro (12-factor).

### Decisión
**TOML como fuente de verdad, env vars como override**.

### Razón
- MPT tiene config con estructuras anidadas (`[llm.openai]`, `[tts.azure]`) que se pierden en flat env
- Env vars permiten 12-factor compliance en producción (secrets management)
- Loader (`shared/config.py`) merge sin sorpresas

### Consecuencias
- ✅ Onboarding fácil: editar TOML directo
- ✅ Producción: secrets via env (K8s, Docker secrets, AWS Secrets Manager)
- ✅ Override granular por entorno
- ❌ Dos lugares para mirar config (mitigado: `contenido config-check` muestra resolved)

---

## ADR-004: Pydantic 2 con schemas unificados

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
MPT tiene ~18 modelos Pydantic. reels-af tiene ~22 modelos. Muchos solapan (VideoParams ↔ ScriptDraft).

### Decisión
**`shared/schemas.py` con merge unificado**. Validators de reels-af (loop-back, accent word count) integrados.

### Consecuencias
- ✅ Single source of truth
- ✅ Validators compartidos entre reasoners y endpoints
- ✅ Type safety end-to-end
- ❌ Migration cost si MPT o reels-af cambian upstream

---

## ADR-005: Sample-accurate timing universal para TODOS los TTS

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
reels-af aplica `ffprobe + atempo + word distribution` solo a Gemini Flash. MPT usa Edge SubMaker (drift acumulativo en reels >20s).

### Decisión
Portar el método de reels-af a TODOS los engines en `core/tts/timing.py`. Aplicable post-síntesis (es agnóstico al engine).

### Consecuencias
- ✅ Word-burst karaoke funciona con cualquier voz (incluye CJK)
- ✅ Elimina drift de Edge SubMaker
- ❌ Aumenta complejidad de cada engine driver
- ⚠️ atempo=1.35 puede ser agresivo para voces ya rápidas; hacer configurable por engine

---

## ADR-006: libass word-burst por default, SRT como fallback

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
Word-burst (reels-af) es el estilo viral 2025+. SRT clásico (MPT) es broadcasting tradicional.

### Decisión
**`SubtitleStyle.WORD_BURST` es default**. SRT disponible con `subtitle_style=srt`.

### Razón
- Estilo viral retention rate >2× SRT en TikTok/Reels
- Compatibilidad con usuarios MPT existentes (no breaking)

---

## ADR-007: Soporte multi-aspect (9:16, 16:9, 1:1)

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
reels-af solo soporta 9:16. MPT soporta los 3.

### Decisión
Portar lógica multi-aspect de MPT (`app/services/video.py`) al pipeline ffmpeg directo.

### Cobertura
- 9:16 (1080×1920) → TikTok, Reels, Shorts (default)
- 16:9 (1920×1080) → YouTube, web
- 1:1 (1080×1080) → Instagram Feed

### Consecuencias
- ✅ Mercado direccionable más amplio
- ❌ Necesita ajustar safe_zones, font sizes, accent positions por aspect

---

## ADR-008: Python 3.11 como mínimo

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
MPT requires-python = ">=3.11,<3.13". reels-af requires-python = ">=3.10".

### Decisión
**3.11 como mínimo** (intersección).

### Razón
- `tomllib` en stdlib (no `tomli`)
- Mejor type hints (`Self`, `Required/NotRequired`)
- StrEnum nativo
- Performance: ~25% más rápido vs 3.10

---

## ADR-009: uv como package manager

**Fecha**: 2026-06-15 | **Estado**: Propuesto

### Contexto
Ambos repos usan `uv` (uv.lock presente en ambos).

### Decisión
**uv** como package manager + venv resolver.

### Razón
- Speed: 10-100× más rápido que pip
- Lockfile reproducible
- Compat con pyproject.toml estándar
- Ambos repos ya lo usan

### Riesgo
uv aún es relativamente nuevo (Astral). Plan B: poetry o pip-tools (simple migration).
