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

---

## ADR-016: LiveAvatar (Alibaba-Quark) como talking-head opt-in para long-form

**Fecha**: 2026-06-18 | **Estado**: Aceptado

### Contexto

Hasta ahora el pipeline visual cubre:

- **Reels cortos (25s)**: stock + Gemini/ComfyUI imagen + Veo/Higgsfield i2v + ken-burns
- **Long-form (5–60 min)**: ComfyUI portrait + reference chaining + i2v shot-by-shot

Ningún backend produce **avatares parlantes con lip-sync sincronizado al audio**. Soul (Higgsfield) genera retratos consistentes pero estáticos; DoP/Veo añaden motion cinematográfica pero no sincronización labial. El comentario en `core/subtitles/safe_zone.py` ("subtitles sit above the face/center of any talking-head") era prospectivo.

[LiveAvatar](https://github.com/Alibaba-Quark/LiveAvatar) es un LoRA Distillation Matching Distillation (DMD) sobre Wan2.2-S2V-14B (14B params, diffusion). Input: reference image + audio WAV + prompt textual. Output: MP4 con boca sincronizada al audio. ECCV 2026 accepted, Apache 2.0.

### Decisión

Integrar LiveAvatar como **nuevo VisualGenerator opt-in** específico para el caso de uso "presentador on-screen":

1. **Nuevo intent `LongFormIntent.TALKING_HEAD`** en el script planner (junto a narrative/motion/montage). Routing automático cuando la idea menciona explainer/curso/anchor/lecture.
2. **Nuevo módulo `core/visual/generation/live_avatar*.py`** siguiendo el contrato `VisualGenerator` (mismo patrón que `higgsfield.py`). Dos backends:
   - `local_cli`: subprocess `torchrun minimal_inference/s2v_streaming_interact.py` (mismo patrón que `higgsfield_cli.py`).
   - `remote_http`: POST multipart a worker HTTP propio (RunPod/Lambda Labs/self-host).
3. **Short-circuit en `core/visual/generation/orchestrator.py`**: cuando `BeatVisual.audio_path is not None` y LiveAvatar habilitado, salta DoP/Veo en Tier 2.
4. **Nuevo director `core/long_form/talking_head_director.py`** (concreto, NO stub). Path: Shot → TTS (audio) → LiveAvatar(portrait, audio, prompt) → ffmpeg single-pass concat.
5. **Cost tracking USD/segundo** en `core/llm_router/pricing.py:VIDEO_PRICING_USD_PER_SECOND` con keys `live_avatar_local` / `live_avatar_remote`.

NO se integra para:
- Reels cortos (25s) — overkill, costo GPU mata el unit economics.
- Cinemato sin presentador — Higgsfield DoP y Veo cubren ese caso.
- Brand-owned LoRA custom — el training code de LiveAvatar aún no está liberado (en TODO list del repo upstream); todos usan el LoRA Quark base por ahora.

### Razón

- **TTS ya resuelto**: el módulo `core/tts/` produce WAV sample-accurate compatible. Match perfecto con el input que pide LiveAvatar.
- **Patrón arquitectónico clonable**: contrato `VisualGenerator` ya existente; tier-2 short-circuit es 1 if-statement.
- **Multi-character + 10 000s** de video continuo → consistente con long-form 60 min.
- **Apache 2.0**: sin conflictos de licencia con el resto del repo.
- **Modo offline single-GPU (80GB VRAM)** → factible en H100/A100 rentado por hora (~$2-3.50/hr).
- **Demanda editorial**: explainer videos / cursos / news-anchor son un formato editorial concreto. Soul (estático) no resuelve ese caso; LiveAvatar sí.

### Costo

| Modo | GPU | Costo aprox |
|---|---|---|
| `remote_http` (RunPod serverless H100) | 1×H100 | ~$0.05/s video output → 10 min ≈ $30 |
| `local_cli` (server propio, single-GPU) | 1×H100/A100 80GB | ~$0.005/s amortizado eléctrico |
| `local_cli` (multi-GPU TPP) | 5×H800 | real-time streaming (45 FPS), uso interactivo |

El `cost_per_video_second_usd` es **overrideable por config** según el deal del proveedor.

### Riesgos

1. **Sin training code** (upstream TODO): no podemos diferenciar visualmente por brand mientras eso siga abierto.
2. **TTS integration upstream pendiente**: nuestro pipeline NO depende del TTS interno del repo Quark — pasamos WAV externo. Pero si liberan un TTS integrado, podríamos simplificar.
3. **Latencia batch**: con `enable_compile=true` el primer run es lento (~5-10 min compilación). Runs subsiguientes 2-3× más rápidos. `local_cli` debe correr long-lived, no spin-up por job.
4. **48GB VRAM mínimo (FP8)**: server más barato es A6000 Ada / RTX 6000 Ada. H100 sigue siendo deseable para velocidad.
5. **Aspect ratio fijado por input**: el modelo respeta el AR de la imagen. Para reels 9:16 hay que pasar imagen 9:16.

### Migration / Plan B

- Si LiveAvatar v1.2 cambia la CLI: ajustar `LocalCliBackend._build_cmd` (un solo punto de extensión).
- Si el proveedor remoto sube precios: cambiar `cfg.visual.live_avatar.backend = "local_cli"` y rentar GPU directo.
- Si surge un competidor mejor (D-ID, HeyGen API, Hedra Character-3): el contrato `LiveAvatarBackend` (ABC) permite añadir backends sin tocar el generator.

### Archivos creados/modificados

**Nuevos**:
- `core/visual/generation/live_avatar.py` — generator
- `core/visual/generation/live_avatar_client.py` — backends + errores
- `core/long_form/talking_head_director.py` — `produce_talking_head()` end-to-end
- `tests/test_live_avatar.py` — 24 tests unitarios
- `docs/LIVE_AVATAR.md` — setup local/remote, hardware reqs, ejemplos

**Modificados**:
- `shared/config.py` — `LiveAvatarConfig` añadido a `VisualConfig`
- `shared/schemas.py` — `VideoSource.LIVE_AVATAR`, `LongFormIntent.TALKING_HEAD`, `BeatVisual.audio_path`, `BeatVisual.reference_image_path`
- `core/visual/generation/orchestrator.py` — short-circuit `_should_use_live_avatar` + nuevo param `live_avatar_gen`
- `core/visual/generation/__init__.py` — exports
- `core/long_form/prompts.py` — `INTENT_ROUTER_SYSTEM` extendido + `TALKING_HEAD_SCRIPT_SYSTEM`
- `core/long_form/script_planner.py` — `_system_template_for(TALKING_HEAD)`
- `core/long_form/director.py` — branching en `produce_long_form` por intent
- `core/llm_router/pricing.py` — `VIDEO_PRICING_USD_PER_SECOND` + helpers
- `config.example.toml` — sección `[visual.live_avatar]`

### Crédito

- LiveAvatar — Yubo Huang et al. (Alibaba Group / USTC / BUPT / ZJU / Monash), ECCV 2026
- Repo: https://github.com/Alibaba-Quark/LiveAvatar — Apache 2.0
- Paper: arXiv:2512.04677
- Modelo base: [Wan-AI/Wan2.2-S2V-14B](https://huggingface.co/Wan-AI/Wan2.2-S2V-14B)
- LoRA distilled: [Quark-Vision/Live-Avatar](https://huggingface.co/Quark-Vision/Live-Avatar)

---

## ADR-017: Analizador de video de referencia (input-by-reference)

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

Evaluamos integrar [OpenMontage](https://github.com/calesthio/OpenMontage) (AGPLv3) y
[automate-faceless-content](https://github.com/cporter202/automate-faceless-content).
Conclusión (ver "Evaluación de integración"): **no integrar código** de ninguno —
automate-faceless-content es un curso en Markdown (0% código) que promociona un SaaS
cerrado; OpenMontage es código real pero divergente arquitectónicamente y, sobre todo,
**AGPLv3**, incompatible con nuestro despliegue como servicio bajo Apache-2.0 (la cláusula
Affero contaminaría toda la plataforma).

De OpenMontage sí valía la pena **reimplementar** (no copiar) 3 ideas que llenan gaps
reales. Esta es la primera: hoy no existe forma de decir "haz un reel con el ritmo/estructura
de *este* TikTok". Las 4 entradas (url/topic/subject/long_form) parten de texto, nunca de un
video ejemplar.

### Decisión

Nuevo paquete `core/reference/` con código propio bajo Apache-2.0:

1. **Lógica pura `build_brief(url, raw)`** — deriva pacing (`avg_shot_s`, `shot_count`),
   ritmo de voz (`wpm`), clasificación de hook (question/shock_stat/contrarian/listicle/
   statement) y sugerencias (`suggested_beats`, `target_wpm`) de datos crudos. Determinista,
   testeable offline.
2. **I/O detrás del protocolo `ReferenceFetcher`** — inyectable. Default real `YtDlpFetcher`
   (yt-dlp + faster-whisper + ffmpeg scene-detect), con imports perezosos.
3. **Schema `ReferenceBrief`** en `shared/schemas.py`.
4. **CLI `contenido reference <url>`** standalone + opción `topic --reference <url>`.
5. **`VideoParams.reference_url`** → el pipeline analiza la referencia en una **Phase 0
   no-fatal**, expone el brief en `TaskInfo.reference_brief` y loguea la forma sugerida.

### Razón

- **Patrón ya existente**: el protocolo + fetcher inyectable es el mismo estilo que el resto
  de providers; la lógica pura separada de I/O se testea sin red (12 tests).
- **faster-whisper ya está** en deps base — sólo añadimos `yt-dlp` como extra `[reference]`.
- **Apache-2.0 limpio**: cero líneas de OpenMontage; sólo el concepto.

### Consumo creativo del brief (complemento OPCIONAL)

El brief además se **inyecta al prompt de composición** como complemento opcional que NO
altera el comportamiento previo:

- `reference_style_hint(brief)` — helper PURO que produce un bloque de guía SUAVE
  (pacing/hook/ritmo visual). Determinista, testeado.
- `compose_script(app, essence, reference_brief=None)` y
  `write_narration(s)(..., reference_brief=None)` aceptan el brief opcional. Con `None`, el
  user-prompt es **idéntico byte-a-byte** al anterior (test lo verifica); con brief, se anexa
  el bloque de estilo SIN tocar la estructura fija ni el contenido (sigue saliendo de la
  essence/evidence).
- **Modulación del conteo de beats**: `mechanism_target(brief) = clamp(suggested_beats - 2, 2, 4)`
  (descuenta hook+payoff, respeta la cota del schema). Cuando hay brief, `_system_prompt` pide
  un nº **exacto** de `mechanism_lines` ("exactly N sentences") en vez del rango "2-4"; sin
  brief, el prompt es idéntico al previo. Así el pacing de la referencia (cortes rápidos →
  más beats) se traduce en estructura real, no sólo en texto de guía.
- `run_topic_pipeline`/`run_article_pipeline` computan el brief UNA vez
  (`_compute_reference_brief`) antes de componer, lo inyectan, y lo reutilizan downstream sin
  re-fetch. El subject path lo deja a `_run_shared_downstream` (Phase 0).

La guía es **soft** a propósito: orienta velocidad de entrega y sabor del hook, no reescribe el
guion. El valor standalone (CLI `contenido reference <url>`) sigue intacto.

### Archivos

**Nuevos**: `core/reference/{__init__,analyzer,fetcher}.py`, `tests/test_reference.py`.
**Modificados**: `shared/schemas.py` (`ReferenceBrief`, `ReferenceSegment`,
`VideoParams.reference_url`, `TaskInfo.reference_brief`), `apps/api/pipeline.py`
(`_compute_reference_brief` + Phase 0 + inyección en topic/article),
`core/narrative/{compose,narrator}.py` (kwarg `reference_brief` opcional),
`apps/cli/main.py` (comando `reference` + `topic --reference`), `pyproject.toml` (extra `reference`).
Surface en las 3 superficies: `apps/api/main.py` (POST `/reference`), `apps/webui/Main.py`
(input URL de referencia en tabs Premium + badge slideshow + expander del brief),
`apps/mcp/server.py` (tool `contenido_analyze_reference`, ADR-021).

### Crédito

Concepto inspirado en OpenMontage (`tools/analysis/video_analyzer.py` + `scene_detect` +
`transcript_fetcher`). Reimplementación independiente — sin reutilización de código AGPLv3.

---

## ADR-018: Re-ranking semántico de B-roll (stock)

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

Segunda idea reimplementada de OpenMontage (que indexa un corpus libre Archive.org/NASA/
Wikimedia con embeddings CLIP). El selector tomaba `results[0]` del provider de stock: el
primer resultado del query corto de búsqueda, sin reordenar por la intención RICA del beat.

No tenemos los bytes de imagen al seleccionar (sólo metadata), así que adaptamos el concepto
a los datos disponibles: re-ranking por **texto**, no por embedding de imagen.

### Decisión

1. **`core/visual/broll_rerank.py`** — `rerank(query, materials, embedder=None)` ordena los
   candidatos por relevancia entre la descripción rica del beat (`image_prompt + visual_anchor
   + text`) y el texto de cada candidato. Backend default: coseno léxico determinista (cero
   deps, offline). Backend opcional vía protocolo `.similarity()` (p.ej. sentence-transformers).
2. **`MaterialInfo.description` + `.tags`** — Pexels rellena `description` con el slug de la
   page-url; Pixabay rellena `tags`. Best-effort, retro-compatible (defaults vacíos).
3. **Hook en `selector._try_stock`** — reordena el top-N del provider antes de tomar `[0]`.

### Razón

- **Sort estable**: empates preservan el orden del provider (que ya viene rankeado) → nunca
  empeora, sólo mejora cuando hay señal.
- **Determinista y offline por defecto** → 9 tests sin red; el provider ya filtra por keyword,
  esto sólo afina la elección entre los candidatos.
- **Apache-2.0**: código propio; no usamos CLIP ni el corpus de OpenMontage.

### Backend semántico (opcional, implementado)

El protocolo `Embedder` ahora tiene un backend real opt-in:
`core/visual/broll_embedder.py:SentenceTransformerEmbedder` (sentence-transformers, coseno
clamp a [0,1]). `get_broll_embedder(config)` devuelve el embedder si
`config.visual.broll_semantic_rerank=true` **y** la lib está disponible; si no, **None →
el rerank cae al léxico** (default). El modelo se carga perezosamente y se cachea
(`lru_cache`) por nombre — no por beat. Extra `broll-semantic` (pesado, torch). El selector
pasa `embedder=get_broll_embedder()` a `rerank`; con la config por defecto es None, así que
el comportamiento no cambia.

### Archivos

**Nuevos**: `core/visual/broll_rerank.py`, `core/visual/broll_embedder.py`,
`tests/test_broll_rerank.py`, `tests/test_broll_embedder.py`.
**Modificados**: `shared/schemas.py` (`MaterialInfo.description/tags`),
`shared/config.py` (`VisualConfig.broll_semantic_rerank/broll_embedder_model`),
`core/visual/selector.py`, `core/visual/stock/{pexels,pixabay}.py`,
`pyproject.toml` (extra `broll-semantic`).

---

## ADR-019: Guard anti-slideshow (delivery promise)

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

Tercera idea reimplementada de OpenMontage (`lib/slideshow_risk.py` + `lib/delivery_promise.py`).
El pipeline visual NUNCA crashea: cae a ken-burns sobre still o a placeholder. Eso es robusto,
pero un reel que prometió "video cinético" (PREMIUM/Veo/Higgsfield) puede terminar siendo 80%
imágenes estáticas con zoom — el fracaso "PowerPoint animado" que mata el engagement. No había
ninguna señal que lo detectara.

### Decisión

1. **`core/editorial/slideshow_guard.py`** — `assess_slideshow_risk(artifacts, promised_motion,
   max_static_ratio)` clasifica cada beat como movimiento real (stock/Veo/DoP/LiveAvatar) vs
   still (generadores de imagen + ken-burns + placeholder), calcula `static_ratio`/`risk_score`
   y emite `ValidationResult` (reusa el patrón de la capa editorial).
2. **Cableo no-fatal en el pipeline** (tras Phase D): loguea warnings/errors y expone
   `slideshow_risk`/`static_ratio`/`is_slideshow` en `TaskInfo.quality_flags`.

### Razón

- **Advisory, no bloqueante en runtime**: respeta la filosofía "nunca crashear por assets". El
  gate duro sigue siendo el humano (plan → approve). Esto es una señal observable que podría
  elevarse a gate si se quisiera.
- **Reusa `ValidationResult`/`ValidationIssue`** → integración natural con el gate editorial
  existente. 15 tests, cero deps.

### Archivos

**Nuevos**: `core/editorial/slideshow_guard.py`, `tests/test_slideshow_guard.py`.
**Modificados**: `core/editorial/__init__.py`, `apps/api/pipeline.py`,
`shared/schemas.py` (`TaskInfo.quality_flags`).

---

## ADR-020: Composición Remotion — NO integrar (entorno opcional / observación)

**Fecha**: 2026-06-19 | **Estado**: Rechazado (documentado como entorno opcional futuro)

### Contexto

OpenMontage compone con **Remotion (React/Node) + HyperFrames**, que permite cosas que nuestro
`core/editor/ffmpeg_stitch.py` (single-pass, ADR-001) no hace hoy: transiciones custom,
color grading, motion graphics multi-capa, animaciones spring. Nuestro propio análisis lista
esto como el gap más claro del montaje ("funcional y sample-accurate pero **básico**").

### Decisión

**No integrar Remotion.** Se deja registrado como *entorno opcional* a evaluar sólo si el
"look cinematográfico" se vuelve un objetivo de producto explícito.

### Razón

Adoptar Remotion implica: meter Node.js 18+ y un build de React en un stack Python, **perder
el timing sample-accurate** (nuestra ventaja, ADR-001/ADR-005), y ~2 semanas de refactor del
editor. El ROI no lo justifica para reels sociales de 25s. Además, el composer de OpenMontage
es **AGPLv3** — no se podría importar; habría que reimplementarlo.

### Si algún día se hace

La vía sería un **editor alternativo opcional** detrás de un switch de config
(`config.editor.runtime = "remotion"` junto al `ffmpeg` default), **reimplementado** bajo
Apache-2.0 — nunca importando el de OpenMontage. El `ffmpeg single-pass` seguiría siendo el
default por timing y simplicidad.

---

## ADR-021: MCP server (agente-driven) sobre el pipeline existente

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

El proyecto tiene 3 superficies (CLI, REST API, Streamlit). La conclusión del análisis de
OpenMontage fue que su idea valiosa era **"el agente como director"**. Un MCP server es esa
idea, nativa de nuestra arquitectura Apache-2.0 — sin adoptar su código AGPL ni su orquestación.
Le da a Claude/cualquier agente las herramientas para dirigir el producto.

### Decisión

Nuevo paquete `apps/mcp/` con FastMCP (transport **stdio**, local), in-process sobre el
pipeline existente:

1. **`apps/mcp/service.py`** — TODA la lógica, SIN importar `mcp` → testeable sin el SDK ni LLM:
   `build_reel_params`, `cost_note`, `run_job`/`start_reel` (jobs en background sobre el
   `StateManager`), `get_task`/`list_tasks`, `format_task`. 14 tests con runner inyectado.
2. **`apps/mcp/server.py`** — capa FINA FastMCP. 5 tools:
   `contenido_analyze_reference` (read-only) · `contenido_start_reel` (job, gasta dinero) ·
   `contenido_get_task` · `contenido_list_tasks` · `contenido_list_voices` (read-only).
   Cada tool con Pydantic input + annotations (readOnly/destructive/idempotent/openWorld).
3. **Jobs no-bloqueantes**: `start_reel` setea un `TaskInfo` QUEUED síncrono, agenda el pipeline
   con `asyncio.create_task` (guardado en un set para no ser GC'd) y devuelve `task_id` +
   `cost_note`. El agente sondea con `get_task`. `run_job` marca FAILED si el pipeline revienta
   (nunca se queda colgado en PROCESSING).
4. **Estado compartido**: `get_state_manager(load_config())` → Redis si está configurado
   (jobs visibles también desde la API/UI) o memoria en otro caso.

### Gate de costo (decisión de alcance)

El MCP expone **solo reels** (topic/article/subject, ~$0.01–1.20). El **long-form ($16–80)
NO se expone** como tool — no hay parámetro para dispararlo; sigue human-gated vía CLI/plan
editorial. `start_reel` devuelve `cost_note` para que el agente vea el gasto antes de actuar.

### Razón

- **Wrapper fino, cero dominio nuevo**: reusa `run_pipeline`, `StateManager`, `VideoParams`,
  `analyze_reference`. El MCP es orquestación, no lógica.
- **Lógica separada del SDK** → 14 tests corren sin `mcp` instalado; el server sólo se
  import-testea cuando el extra está presente.
- **Apache-2.0**: sin relación con el código de OpenMontage.

### Archivos

**Nuevos**: `apps/mcp/{__init__,service,server}.py`, `tests/test_mcp_service.py`, `docs/MCP.md`.
**Modificados**: `pyproject.toml` (extra `mcp` + script `contenido-mcp`).

### Riesgos / extensión

- Un agente podría encadenar muchos `start_reel` → gasto. Mitigación actual: `cost_note` visible
  + sólo reels. Futuro: quota/confirmación por tool, o `estimate_cost` dedicado.
- `generate_plan` (ideación editorial) podría exponerse como tool read-mostly más adelante.

---

## ADR-022: Providers de corpus libre (Archive.org, Wikimedia, NASA, Unsplash)

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

Paridad con el B-roll de OpenMontage: además del re-ranking semántico (ADR-018) sobre stock
de pago (Pexels/Pixabay/Coverr), OpenMontage **recuperaba** de archivos libres
(Archive.org/NASA/Wikimedia) indexados con CLIP. Faltaba la *recuperación* desde esas fuentes.

### Decisión

Cuatro nuevos `StockProvider` (código propio Apache-2.0, concepto inspirado en OpenMontage):

- **Archive.org** (`archive_org.py`) — video dominio público, keyless, 2 pasos
  (advancedsearch → metadata → archivo mp4).
- **Wikimedia Commons** (`wikimedia.py`) — video CC, keyless, 1 paso (query+imageinfo,
  filtrado a mediatype=VIDEO).
- **NASA** (`nasa.py`) — images-api keyless, 2 pasos (search → asset manifest → mp4).
- **Unsplash** (`unsplash.py`) — FOTOS (no video), requiere Access Key.

Como Unsplash es imagen, se añadió `MaterialInfo.media_kind` (`"video"` default | `"image"`):
el selector convierte `image` a clip vía **ken-burns** (`render_ken_burns`), respetando el
contrato de la rama stock (devuelve video). Cada parser es **puro y testeado** (15 tests sobre
payloads representativos); la parte HTTP es wrapper fino (no testeada offline, como `YtDlpFetcher`).

Activación: Archive.org/Wikimedia/NASA por flag `stock.*_enabled` (keyless); Unsplash por
`stock.unsplash_api_keys`. El registry los incluye y los anexa tras los de pago en
`provider_order`. Costo 0.0 en el selector. `slideshow_guard.MOTION_SOURCES` incluye los 3 de
video real; **Unsplash NO** (imagen→ken-burns cuenta como still).

### Razón

- **Encaja en la ABC `StockProvider`** sin tocar el editor — la rama stock ya descarga+usa clips.
- **Gratis**: amplía cobertura de B-roll sin costo por clip.
- **Parsers puros separados de I/O** → testeables sin red.

### Riesgos

- Las formas de JSON de cada API son best-effort según su doc; verificación en vivo pendiente
  (igual que `reference/fetcher.py`). Si una API cambia, sólo afecta su parser.
- Archive.org/NASA son 2 pasos → más latencia; por eso van como fallback tras el stock de pago.

### Archivos

**Nuevos**: `core/visual/stock/{archive_org,wikimedia,nasa,unsplash}.py`, `tests/test_stock_free.py`.
**Modificados**: `shared/schemas.py` (`VideoSource` + `MaterialInfo.media_kind`),
`shared/config.py` (`StockConfig` flags+keys), `core/visual/stock/registry.py`,
`core/visual/selector.py` (puente imagen→ken-burns + costos),
`core/editorial/slideshow_guard.py` (MOTION_SOURCES), `config.example.toml`.

---

## ADR-023: Video-gen extra vía fal.ai (Kling / Runway / MiniMax)

**Fecha**: 2026-06-19 | **Estado**: Aceptado

### Contexto

OpenMontage ofrecía más providers de video-gen (Kling, Runway, MiniMax, Seedance vía fal.ai).
contenido ya cubre i2v con Veo + Higgsfield DoP. Estos eran "extras menores", pero añadir un
gateway unificado da diversidad de motion sin un provider nuevo por modelo.

### Decisión

Un único `VisualGenerator` sobre **fal.ai** (`core/visual/generation/fal.py`) con variantes de
modelo seleccionables (`kling` | `runway` | `minimax` | id `fal-ai/...`). Es un **fallback de
motion adicional en tier 2**, entre Veo y ken-burns:

  DoP → Veo → **fal.ai** → ken-burns

Opt-in por `config.visual.fal.enabled` + `api_key`. Helpers puros (mapeo de modelo, request,
parseo) testeados offline (7 tests); la llamada HTTP es wrapper fino (httpx, sin SDK).
`VideoSource.FAL` cuenta como **motion real** en `slideshow_guard`; costo ~$0.25 en el selector.

### Razón

- **Un archivo, no uno por modelo**: fal.ai unifica la API → variante por config.
- **Encaja en la cascada existente**: mismo contrato `VisualGenerator` que Veo; 1 if en el
  orchestrator (lazy import del provider sólo si enabled).
- Diversidad de motion para A/B sin tocar el resto del pipeline.

### Archivos

**Nuevos**: `core/visual/generation/fal.py`, `tests/test_fal.py`.
**Modificados**: `shared/schemas.py` (`VideoSource.FAL`), `shared/config.py` (`FalConfig`),
`core/visual/generation/orchestrator.py` (tier 2b.5 + construcción lazy),
`core/visual/selector.py` (costo), `core/editorial/slideshow_guard.py` (MOTION_SOURCES),
`config.example.toml`.

---

## ADR-024: Checkpoint reanudable (resume mid-pipeline)

**Fecha**: 2026-06-19 | **Estado**: Aceptado (marginal para reels, útil para long-form)

### Contexto

OpenMontage tenía `checkpoint.py` para reanudar a mitad de ejecución. contenido ya tiene
estado de task (`StateManager`), pero ese estado es de *progreso*, no de *reanudación
granular*: si un long-form (45-90 min, $15-80) muere en el shot 40/120, no hay forma de
saltar los 39 ya generados.

### Decisión

`core/checkpoint.py` con `Checkpoint` (fases + beats completados, con artefactos) y
`CheckpointStore` (JSON atómico por task, tmp+replace). Agnóstico al pipeline: el caller
consulta `is_phase_done`/`pending_beats` antes de cada fase y registra con
`mark_phase`/`mark_beat`+`save`. Carga tolerante a corrupción (→ checkpoint vacío, no crash).
`get_checkpoint_store()` deriva el dir de `long_form.working_dir/checkpoints` por default.

**Alcance**: módulo + 10 tests, NO cableado al pipeline de reels (donde es marginal: 25s,
rápido, barato — reanudar no compensa). Pensado para que `core/long_form/director.py` lo
adopte en su loop de shots, donde el ahorro es real. Se deja como infraestructura lista.

### Razón

- **Reels no lo necesitan**: cablearlo ahí añadiría I/O por beat sin payoff.
- **Long-form sí**: un solo módulo reutilizable, sin acoplar; el director lo usa cuando quiera.
- **Atómico + tolerante a corrupción** → un checkpoint a medio escribir nunca rompe un re-run.

### Archivos

**Nuevos**: `core/checkpoint.py`, `tests/test_checkpoint.py`.

---

## ADR-025: Re-ranking VISUAL de B-roll vía CLIP (texto↔imagen)

**Fecha**: 2026-06-23 | **Estado**: Aceptado

### Contexto

El re-ranking de B-roll (ADR-018) reordena candidatos por similitud **textual** entre la
descripción del beat y la metadata del provider (tags + título + slug de la url). Es barato y
offline, pero ciego al contenido real del clip: dos clips con tags idénticos pueden mostrar
cosas muy distintas. OpenMontage resuelve esto con un corpus CLIP **visual** (descarga el clip →
extrae frames → embeddings CLIP → retrieval por lo que la imagen realmente muestra). Su
`broll_rerank.py` ya documentaba el gap: *"no tenemos los bytes de imagen al momento de
seleccionar… adaptamos el concepto"*.

### Decisión

`core/visual/broll_visual.py`: re-ranking **texto↔imagen** vía CLIP, **opt-in** y aditivo.

- `VisualEmbedder` (protocolo nuevo `score(query, frame_paths) -> float`), distinto del
  `Embedder` texto↔texto de ADR-018.
- `VisualClipEmbedder` sobre **sentence-transformers** (`clip-ViT-B-32`) — ya en deps base, sin
  añadir torch/transformers crudos. Score del clip = **máximo** coseno texto↔frame.
- `extract_frames()`: muestrea N frames equiespaciados vía ffmpeg (reusa `probe_video`);
  imágenes pasan tal cual. `runner`/`prober` inyectables para test offline.
- `rerank_visual()`: reordena `MaterialInfo` ya descargados; nunca crashea (clip malo → score 0).
- Integración en `selector._resolve_stock_clip`: con visual ON baja el top-K léxico, los
  re-rankea por frames y elige el mejor; con OFF, flujo ADR-018 intacto (`candidates[0]`).
- `get_visual_reranker()`: `None` salvo `config.visual.broll_visual_rerank=true` y lib presente.

Idea inspirada (NO copiada) en OpenMontage (AGPLv3). Reimplementación propia Apache-2.0.

### Razón

- **Salto de relevancia real**: rankea por lo que el clip muestra, no por sus tags — clave para
  montaje documental y para los corpus libres (Archive.org/NASA) con metadata pobre.
- **Cero coste por defecto**: off → ni un import pesado; el flujo léxico (ADR-018) no cambia.
- **Reusa infra existente**: CLIP de sentence-transformers (deps base) + ffmpeg (ya en
  `cache.py`) → el extra `broll-visual` es nominal.
- **Aditivo y testeable**: 18 tests offline; suite total verde sin regresión.

### Archivos

**Nuevos**: `core/visual/broll_visual.py`, `tests/test_broll_visual.py`,
`tests/test_selector_visual.py`.
**Modificados**: `shared/config.py` (`VisualConfig.broll_visual_*`),
`core/visual/selector.py` (`_resolve_stock_clip` + wiring en `_try_stock`),
`pyproject.toml` (extra `broll-visual`), `config.example.toml`.

---

## ADR-026: Corpus libre histórico — LoC, NARA, Pond5 PD (no scrapers frágiles)

**Fecha**: 2026-06-23 | **Estado**: Aceptado

### Contexto

OpenMontage expone 16 fuentes de stock; contenido ya integró 7 (ADR-022). De las 9 restantes,
8 fueron candidatas a portar: ESA, JAXA, NOAA, LoC, NARA, Mixkit, Videvo, Pond5-PD. Auditadas,
se parten en dos grupos: **API JSON oficial** (LoC, NARA, Videvo, Pond5-PD) y **scraping HTML
frágil** (ESA, JAXA, NOAA, Mixkit) — estos últimos rompen sin aviso al cambiar el HTML del sitio
y no son testeables de forma fiable.

### Decisión

Portar solo los **keyless de dominio público con API estable**: **LoC**, **NARA**, **Pond5-PD**
(`core/visual/stock/{loc,nara,pond5_pd}.py`). Mismo patrón que ADR-022: parser puro +
`StockProvider` async; activación por flag keyless (`loc_enabled`, `nara_enabled`,
`pond5_pd_enabled`); NARA/Pond5 aceptan key opcional (rate limit) vía env. Cuentan como **motion
real** en `slideshow_guard` y costo 0 en el selector.

**Descartados**:
- **ESA, JAXA, NOAA, Mixkit** — scraping HTML; deuda de mantenimiento en un repo con suite verde.
  Se omiten (no se portan) por la misma lógica selectiva de ADR-022.
- **Videvo** — API JSON robusta pero **key obligatoria** → no es "free corpus" keyless. Se deja
  como keyed opcional futuro (trivial de añadir con el patrón pexels si se requiere).

Idea inspirada (NO copiada) en OpenMontage (AGPLv3). Reimplementación propia Apache-2.0.

### Razón

- **Valor documental directo**: cine temprano (LoC), WWII/Apollo/gobierno (NARA), histórico
  (Pond5-PD) → exactamente lo que pide el tenant `ciencia`/documental y el long-form.
- **Robustez sobre cantidad**: 3 APIs estables y testeables > 8 fuentes con 4 scrapers que
  romperán. Parsers puros cubiertos offline (15 tests); HTTP es wrapper fino.
- **Coherente con ADR-022**: keyless por flag, sin lock-in, mismo contrato `StockProvider`.

### Archivos

**Nuevos**: `core/visual/stock/loc.py`, `core/visual/stock/nara.py`,
`core/visual/stock/pond5_pd.py`, `tests/test_stock_historical.py`.
**Modificados**: `shared/schemas.py` (`VideoSource.{LOC,NARA,POND5_PD}`),
`shared/config.py` (`StockConfig` flags + env keys), `core/visual/stock/registry.py`,
`core/editorial/slideshow_guard.py` (MOTION_SOURCES), `core/visual/selector.py` (costos),
`config.example.toml`.

---

## ADR-027: Scoring de proveedor auditable (7 dimensiones + audit trail)

**Fecha**: 2026-06-23 | **Estado**: Aceptado

### Contexto

El selector decide stock vs IA por beat con una heurística probada
(`selector._decide_source`): rápida y testeada, pero **no deja rastro** de por qué se eligió una
fuente ni qué alternativas se descartaron. Para A/B testing y auditoría por tenant hace falta
trazabilidad. OpenMontage puntúa proveedores en 7 dimensiones con log de alternativas.

### Decisión

`core/visual/provider_scoring.py`: capa de **scoring auditable** que NO reemplaza la heurística.

- 7 dimensiones ponderadas (`ScoreWeights`): task_fit 0.30, quality 0.20, control 0.15,
  reliability 0.15, cost 0.10, latency 0.05, continuity 0.05.
- Perfil estático de capacidad por `VideoSource` (`_PROFILES`) + `task_fit` **dinámico** según
  el contexto del beat (motion_required, role hook/payoff, evidencia).
- `score_providers()` / `decide_provider()` → `ProviderDecision` con `chosen`, `score` y
  `alternatives` (todas, orden desc) — cada una con dimensiones y razones.
- `DecisionLog`: acumula decisiones por beat, serializa a JSON (`to_dict`/`save`) para auditoría.

**Cableo (estilo `cost_tracker`)**: el selector acepta `decision_log` opcional en
`select_and_generate_beat_artifact`/`generate_all_beat_artifacts` y, tras elegir la fuente real,
registra `decide_provider(candidatos_plausibles, contexto_del_beat)` con la fuente REAL en el
contexto (`_audit_candidates` + `_record_decision`, no-fatal). El pipeline crea un `DecisionLog`,
lo pasa a la fase D y lo persiste a `output/<task>/visual_decisions.json` — permitiendo comparar
heurística vs scoring offline para A/B y auditoría por tenant. Off por defecto (sin `decision_log`
= no-op): los callers que no lo pasan no pagan nada.

Idea inspirada (NO copiada) en `lib/scoring.py` de OpenMontage (AGPLv3). Propio Apache-2.0.

### Razón

- **Trazabilidad sin riesgo**: la heurística probada sigue decidiendo; el scoring solo audita.
- **Datos para A/B**: ¿el scoring habría elegido distinto? → señal para afinar la heurística.
- **Multi-tenant**: el audit trail por tenant explica el gasto y la mezcla de fuentes.

### Archivos

**Nuevos**: `core/visual/provider_scoring.py`, `tests/test_provider_scoring.py`,
`tests/test_selector_decision_log.py`.
**Modificados**: `core/visual/selector.py` (`_audit_candidates`, `_record_decision`, param
`decision_log`), `apps/api/pipeline.py` (crea/pasa/persiste el `DecisionLog`).

---

## ADR-029: `produce` real para MONTAGE — checkpoint reanudable, veredicto por shot y assets anclados

**Fecha**: 2026-08-29 | **Estado**: Aceptado

### Contexto

`plan_long_form()` era sólido (arco → escenas → shots con descomposición
first/last frame + motion) pero `produce_long_form()` era un **stub** para
NARRATIVE, MOTION y MONTAGE: sólo TALKING_HEAD tenía director concreto
(ADR-016). Un job planificaba bien y no renderizaba nada.

En paralelo, `core/checkpoint.py` (ADR-024) llevaba escrito y testeado desde su
commit **sin un solo caller**. La primitiva de reanudación existía en el vacío.

El disparador fue analizar [HyCreator](https://hycreator.tencent.com) (Tencent
Hunyuan), un *agent harness* para video largo cuyas tres funciones diferenciales
resultaron ser justo las tres piezas que faltaban aquí:

1. *"divide-and-conquer checking"* — el fallo de un fragmento no tira el
   proyecto; sólo se re-hace el fragmento defectuoso.
2. Inspección por capas que distingue "el mejor del lote" de "bueno".
3. Assets (personajes, entornos, props) como entidades reutilizables con
   *unified style consistency*, no strings sueltos dentro del guion.

Nada de esto es novedad arquitectónica: es control de flujo. Las tres se
implementan con lo que el repo ya tenía.

### Decisión

**1. `core/long_form/montage_director.py` — el primer `produce` genérico.**

Se estrena con MONTAGE, no con NARRATIVE, porque es el intent más barato de
validar: sin diálogo obligatorio, sin consistencia de personaje cross-shot, sin
ComfyUI ni GPU. El módulo es **pegamento, no motor** — cada etapa delega:

```
Shot → Beat + BeatVisual → core.visual.selector (stock vs IA + DecisionLog)
     → core.tts (sample-accurate; engine `silent` si el shot no tiene diálogo)
     → core.editor.stitch_video (single-pass ffmpeg + BGM, ADR-001)
```

La duración **medida** del audio pisa a la planificada shot a shot, para no
acumular drift (mismo principio que ADR-001). Los shots sin diálogo pasan por
el engine `silent` en vez de por una rama aparte: un solo camino, no dos.

**2. Checkpoint cableado (cierra ADR-024).**

Cada shot renderizado se persiste al `CheckpointStore` en el acto, bajo un lock
porque el fan-out es concurrente. Un re-run del mismo `job_id` arranca desde
`pending_beats()`. Morir en el shot 40 de 120 pasa a costar 80 generaciones en
vez de 120 — para un job de $16-80, la diferencia entre reintentar y no. El
checkpoint se borra al completar, para que un re-run futuro con el mismo id no
herede basura.

**3. Veredicto por shot (`ShotVerdict`), opt-in.**

`BestImageSelector` elegía el mejor de N y lo aceptaba siempre; un lote entero
malo entregaba el menos malo sin dejar rastro. Se añade `judge()`, que devuelve
`ShotVerdict(path, reason, acceptable, score)`. `select_best()` queda como
wrapper delgado — la firma histórica no cambia y sus tests siguen pasando.

- El prompt pide ahora un `score` **absoluto** (0-10, no relativo al lote) con
  anclas explícitas, más un booleano `acceptable`.
- Ambos campos tienen default permisivo (`acceptable=True`, `score=-1`): un VLM
  que ignore las claves nuevas se comporta exactamente como antes.
- Con `shot_accept_threshold > 0`, el montage re-genera hasta `shot_max_rework`
  veces; si sigue rechazado **entrega igual** y anota `needs_rework: true` en el
  checkpoint. Se deja rastro sin bloquear la entrega.
- Un fallo del VLM (timeout, JSON inválido, índice fuera de rango) nunca produce
  `acceptable=False`. Un gate que se cae no debe convertirse en una fuente nueva
  de trabajo perdido.
- Con un solo candidato y umbral configurado se juzga igual: no hay nada que
  *comparar*, pero sí que juzgar.

**4. `AssetProfile` — assets como entidades de primera clase.**

`CharacterProfile` ya anclaba personas vía `portrait_path`. Los entornos y props
vivían como texto libre en `Scene.setting`, así que "el café" de la escena 2 y el
de la 9 eran dos strings y el generador los dibujaba distintos.

`AssetProfile(name, kind, description, reference_path)` es el **hermano** de
`CharacterProfile`, no una jerarquía con base común: comparten función (ancla de
consistencia) pero no ciclo de vida — un personaje tiene vistas alternativas y
arco de persona, un prop no. Una base común obligaría a ambos a cargar campos
que uno nunca usa.

Se añaden `Scene.environment` / `Scene.props`, `Shot.assets_present` y
`LongFormScript.assets`. `_enrich_prompt()` inyecta las descripciones de los
assets del shot **más el entorno de su escena** en el prompt visual, que es donde
se gana la consistencia. `Scene.setting` sigue siendo la verdad narrativa; esto
es la capa de anclaje visual encima, y puede quedar vacía.

### Razón

- **La pieza cara es la generación, no la orquestación**: el checkpoint protege
  exactamente el gasto, y ya estaba escrito.
- **Estrenar por lo barato**: MONTAGE valida el camino completo (mapeo, bucle,
  stitch) sin GPU. NARRATIVE reusará el mismo esqueleto añadiendo portraits y
  reference chaining.
- **Todo opt-in y permisivo por default**: `shot_accept_threshold=0.0` deja el
  comportamiento idéntico al de antes y no cuesta un token.
- **Sin motor nuevo**: el selector, el TTS y el stitcher son los del pipeline de
  reels. Lo único añadido es el pegamento.

### Consecuencias

- NARRATIVE y MOTION **siguen en stub**. Este ADR no los cierra.
- El veredicto sobre un solo frame juzga la *imagen*, no el movimiento del clip:
  un first frame correcto con un i2v malo pasa el gate.
- `shot_max_rework` reintentos son generaciones pagadas. Con umbral alto y
  proveedor flojo, el coste sube — de ahí que el default sea 0 (desactivado).
- El checkpoint se indexa por `job_id`: reusar un `job_id` con un script
  distinto reanudaría contra shots que ya no corresponden. Cada `plan_long_form`
  genera un id nuevo, así que sólo pasa si se fuerza a mano.

### Archivos

**Nuevos**: `core/long_form/montage_director.py`, `tests/test_long_form_montage.py`.
**Modificados**: `shared/schemas.py` (`AssetProfile`, `Scene.environment/props`,
`Shot.assets_present`, `LongFormScript.assets` + `assets_by_name()`),
`core/long_form/consistency.py` (`ShotVerdict`, `judge()`), `core/long_form/prompts.py`
(rúbrica de scoring), `core/long_form/director.py` (routing MONTAGE),
`core/long_form/__init__.py`, `shared/config.py` (`shot_accept_threshold`,
`shot_max_rework`, `resume_enabled` + env overrides).
