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

## ADR-010: Higgsfield como provider visual de primera clase

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
Veo 3.1 Lite cubre i2v con buena calidad pero sin presets de motion nombrados. Higgsfield expone DoP con 50+ camera presets cinematográficos (orbit_360, fpv_drone, dolly_zoom_in, super_dolly_out…) + Soul para character consistency cross-beat + Effects VFX. La pregunta era: ¿provider más, o reemplazo de Veo?

### Decisión
**Provider de primera clase, complementario a Veo**. El selector decide por config (`prefer_over_veo: bool`). Cuando ambos enabled, Higgsfield gana por default; cuando solo uno, ese se usa; cuando ninguno, ken-burns sigue siendo el fallback.

### Razón
- Motion presets nombrados = mejor fidelity en hooks cinematográficos (medible vs Veo en A/B)
- Soul abre un caso de uso nuevo (narrativa con personaje recurrente) que Veo no cubre
- Effects son post-step opcional — no compromete el pipeline si falla

### Consecuencias
- ✅ Tres tier visual (Soul→Gemini · DoP→Veo · ken-burns) en vez de dos
- ✅ Catalog de 50+ presets vs los pocos motion hints originales
- ✅ A/B harness mide motion fidelity como métrica diferencial
- ❌ Otro provider más que mantener (auth, pricing, fallback)
- ❌ DoP es fijo 5s (Veo es flexible 4/6/8s) — duración manejada en stitch

### Mitigación
- CLI fallback (`higgsfield generate create --wait --json`) cuando REST falla
- Auth errors NO caen al CLI (mismas credenciales)
- Cost estimates en `_COST_ESTIMATES` para que selector lo considere

---

## ADR-011: Skills oficiales como fuente de prompts (no como runtime)

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
Higgsfield publica un repo `higgsfield-ai/skills` con SKILL.md files que documentan prompts, modelos disponibles, motion presets, photo guides para Soul training. Pregunta: ¿submodule en runtime, o solo dev-time?

### Decisión
**Submodule dev-time only**. Los prompts canónicos se extraen UNA VEZ a `core/visual/generation/higgsfield_prompts.py` (constantes Python). El submodule queda para que Claude Code itere manualmente con doc al lado.

### Razón
- Pipeline runtime es un DAG determinístico, NO un agente que descubre skills en vivo
- Leer markdown en runtime es overhead sin valor
- Las skills se actualizan rápido (v0.3.0 cuando integré) — congelar la versión en código previene drift sin warning

### Consecuencias
- ✅ Prompts canónicos en código (`augment_dop_prompt`, `augment_soul_prompt`)
- ✅ Photo guide validation en `create_soul_id` (de skill v0.3.0)
- ✅ Model catalog correcto (descubrí que `seedance_2_0` es el SOTA, no "dop-turbo" que era WaveSpeed wrapper)
- ❌ Cuando upstream cambia, regenerar manualmente

### Plan de actualización
`scripts/sync_higgsfield_prompts.py` (futuro): regenera `higgsfield_prompts.py` desde el submodule. Por ahora manual.

---

## ADR-012: Capa editorial portada de corredor-content

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
`contenido` era agnóstico a marca: cada `topic` se trata como prompt aislado, sin tono persistente, sin gate humano. corredor-content (proyecto hermano en TypeScript) tiene un patrón limpio: brand-voice.md + facts.json + pillars + audiences + plan→approve→produce. Vale la pena portarlo.

### Decisión
**Portar 5 patrones íntegros**:
1. `editorial/` dir como peer de `core/` con templates editables
2. Brand voice como código (Markdown versionado)
3. Anti-alucinación vía `facts.json` inyectado a hunters
4. Plan → approve → produce con gate humano explícito
5. Platform specs (caption/hashtag/duration ranges por plataforma)

### Razón
- Una idea mala = $1.20 de generación desperdiciada. Gate humano lo previene.
- Brand voice en `.md` se versiona; en chat se pierde
- Facts.json reduce alucinaciones de hunters (year/name/study inventados)
- Platform specs habilitan output multi-canal sin re-pensar specs cada vez

### Consecuencias
- ✅ Nuevo `core/editorial/` module + `editorial/` dir templates
- ✅ 4 comandos CLI nuevos (plan / plan-show / produce-week / brand-check)
- ✅ Hunters inyectan facts automáticamente (transparente — sin facts.json = generic block)
- ✅ 6 plataformas modeladas con specs (TikTok/Reels/Shorts/Long/FB/LinkedIn)
- ❌ Si usas `contenido` como librería (sin editorial/), el registry queda vacío — sin crash pero sin layer

### Compat
- Comandos `topic / article / subject` siguen funcionando igual (single-shot, sin gate)
- Hunters con `facts.json` vacío = comportamiento previo (bloque GENERIC)
- `produce-week` itera ideas y delega al pipeline existente (entry_value → entry_type)

---

## ADR-013: Cost tracking via priceOf pattern

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
`TaskInfo.cost_breakdown` existía como dict vacío. No había forma de saber cuánto gastaba un reel. corredor-content tiene `priceOf(model)` con tabla en TypeScript; el patrón es trivial de portar.

### Decisión
**`core/llm_router/pricing.py`** con tabla `PRICING: dict[str, Price]` de 30+ modelos + `calculate_cost(model, in_tok, out_tok)` + `_stamp_cost()` en el base `LLMProvider`. `OpenAICompatibleProvider` extrae `usage` del response y stampa.

### Razón
- Trivialmente portable (~150 LOC)
- 9 providers heredan de `OpenAICompatibleProvider` → todos reciben tracking gratis
- Modelos no-tabulados caen a fallback conservador `Price(1.0, 3.0)` (sobre-estima > sub-reporta)

### Consecuencias
- ✅ `provider.last_cost_usd`, `total_cost_usd`, `total_calls` stamped
- ✅ `provider.get_cost_record(phase=...)` devuelve `LLMCostRecord` agregable
- ✅ Schema `LLMCostRecord` en `shared/schemas.py` para que el pipeline lo acumule
- ❌ Anthropic + Gemini providers todavía no overrideearon (heredan stamping = 0 hasta que lo hagan)

---

## ADR-014: ComfyUI como provider visual nativo (no solo wrapper)

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
Higgsfield Soul resuelve character consistency. Veo/DoP resuelven motion. Pero ninguno resuelve **identidad visual de marca**: el estilo único que hace que todos los reels parezcan tuyos. La única vía es LoRAs entrenadas + workflows compuestos, y la herramienta canónica para eso es ComfyUI.

Pregunta clave: ¿integrar como wrapper de comfy-cli (passthrough a partner nodes managed) o como provider nativo de la API REST/WS del server?

### Decisión
**Provider nativo de la API server** (HTTP + WebSocket). `comfy-cli` se mantiene como herramienta auxiliar de instalación/gestión (install, launch, lora download, node install), no como ruta de generación.

### Razón
- Los "partner nodes" de comfy-cli son los mismos providers que ya tenemos (Flux, Luma, Kling, Seedance) vía Higgsfield + OpenRouter — sería duplicación
- El valor REAL de ComfyUI es lo que no hace ningún provider managed: workflows custom con LoRAs propias, ControlNet, IPAdapter, AnimateDiff
- Self-host vs managed se resuelve con un solo flag (`server_url`) sin código diferente
- Multi-tenant trivial: `client_id` UUID en WS por sesión, mismo server atiende N tenants

### Implementación
- `core/visual/generation/comfy_client.py` — REST + WS async con polling fallback
- `core/visual/generation/comfy_workflows.py` — registry + parameterizer `{node_id}-inputs-{param}`
- `core/visual/generation/comfy.py` — `ComfyUIGenerator` (extiende `VisualGenerator`)
- `core/comfy/wrapper.py` — subprocess wrapper sobre comfy-cli (install/launch/lora/node)
- `workflows/` dir con 3 templates iniciales (flux_basic, flux_lora_brand, sdxl_ipadapter_style)
- `editorial/brand-visual.json` para mapping multi-tenant

### Consecuencias
- ✅ Brand identity vía LoRA entrenada (moat real vs APIs templated)
- ✅ Multi-tenant nativo (cada cliente con su LoRA + workflow)
- ✅ Self-host (RTX 4090) o managed (ViewComfy) — mismo código
- ✅ Soft fail: server down → orchestrator cae a Soul/Gemini sin abortar
- ✅ Selector decide automáticamente: ComfyUI gana cuando tenant tiene LoRA
- ❌ Tier extra en el orchestrator (4 tiers vs 3) — testing de fallback más complejo
- ❌ Dependencia de websockets lib + comfy-cli (opcional)

### Trade-off explícito
ComfyUI es OPT-IN: `COMFYUI_ENABLED=false` por default. El pipeline funciona idéntico sin ComfyUI configurado. Habilitar solo cuando tienes:
1. LoRA entrenada (30-50 referencias) o
2. Caso específico de ControlNet/IPAdapter/AnimateDiff o
3. Multi-tenant con LoRAs distintas por cliente

---

## ADR-015: Long-form video — extract from ViMax (no integration wholesale)

**Fecha**: 2026-06-15 | **Estado**: Aceptado

### Contexto
[HKUDS/ViMax](https://github.com/HKUDS/ViMax) es un framework agentic para video largo (5-60 min) con 4 roles: Director, Screenwriter, Producer, Video Generator. 10.2k stars, MIT, paper en arXiv 2606.07649. Use case: novelas → video, documentales, audiolibros animados — exactamente lo que el pipeline base (`topic/article/subject` para reels 25s) NO cubre.

Pregunta: ¿integrar wholesale (clone+wrap), portar selectivamente, o no integrar?

### Decisión
**Portar algoritmos + prompts canónicos**, NO el código completo. Crear `core/long_form/` como módulo nuevo paralelo al pipeline base, con 9 archivos: schemas, prompts, RAG, compressor, script planner, scenes/storyboard, consistency (ref + best image selectors), director, types.

### Razón

**Lo que ViMax aporta valioso**:
- Prompts canónicos (intent router narrative/motion/montage, storyboard artist con cinematic language rules, best image selector con character+spatial+description rubric, reference image selector con 2-stage filter)
- Arquitectura conceptual (compress chunks → RAG → arc → scenes → shots → reference chaining)
- ~370 commits + 1.5k forks = código probado en producción académica

**Lo que ViMax NO aporta**:
- Backends de generación (Veo + Gemini + Nanobanana — todo ya está en `contenido`)
- `novel2movie_pipeline.py` está marcado `# TODO: NOT IMPLEMENTED YET` — el pipeline más prometedor está incompleto
- Es API-only (sin LoRA, sin ControlNet, sin IPAdapter — exactamente lo que ComfyUI multi-tenant nos da)
- Sin cost tracking, sin numerical benchmarks publicados en readme

**Riesgo de integración wholesale**: LangChain wholesale (~500MB), breaking changes anuales, duplicación con `llm_router`, dos sistemas paralelos de prompt+parse, deuda técnica desde día 1.

**Solución elegida (Opción B híbrida)**:
- `langchain-text-splitters` solo (~200KB aislado, RecursiveCharacterTextSplitter es genuinamente mejor que casero)
- `sentence-transformers` para embeddings locales (sin API recurring cost)
- `faiss-cpu` opt-in via `--extra longform-scale` (numpy default para ≤5k chunks)
- Reemplazo `LangChain init_chat_model + ChatPromptTemplate + PydanticOutputParser` por `core.llm_router.complete_structured`
- Prompts portados como string constants en `core/long_form/prompts.py` con atribución MIT
- 2-fase: `plan_long_form()` (cheap, ~$1-4) + `produce_long_form()` (expensive, ~$15-20) — gate humano editorial natural

### Consecuencias
- ✅ Nuevo entry point `VideoParams.long_form_input` + CLI `contenido book plan/show/produce`
- ✅ Reusa todo el pipeline existente (TTS, ComfyUI, Higgsfield, editor, distribution)
- ✅ Multi-tenant funciona: cada tenant con su LoRA renderiza el mismo script con identidad propia
- ✅ Cost transparency: el provider stamping del `llm_router` aplica también acá
- ✅ ~200MB overhead (vs 500MB+ de LangChain wholesale)
- ✅ 30 tests verde, 8 schemas nuevos, 9 archivos en `core/long_form/`
- ❌ `produce_long_form()` queda como stub hasta validar E2E con GPU
- ❌ Una dep más (langchain-text-splitters), pero aislada y estable

### Estructura del módulo

```
core/long_form/
├── __init__.py       # exports públicos
├── types.py          # LongFormError, EmbeddingProvider Protocol
├── prompts.py        # 9 prompts canónicos (atribución MIT a ViMax)
├── rag.py            # RAGStore híbrido numpy/FAISS + chunk_text + STEmbeddingProvider
├── compressor.py     # NovelCompressor (split + parallel compress + aggregate)
├── script_planner.py # detect_intent + ScriptPlanner (3-act arc)
├── scenes.py         # SceneExtractor + StoryboardArtist + visual decomposition
├── consistency.py    # ReferenceImageSelector (2-stage) + BestImageSelector
└── director.py       # plan_long_form + produce_long_form + Director.load_job
```

### Crédito
- Algoritmos y prompts: HKUDS/ViMax (MIT License)
- arXiv: 2606.07649 — *ViMax: Agentic Video Generation*

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
