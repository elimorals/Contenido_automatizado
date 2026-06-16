# contenido

Plataforma de generación de video con IA que fusiona CINCO linajes:

- **Industrial-horizontal** (de MoneyPrinterTurbo): API REST + colas Redis, WebUI Streamlit, 20+ proveedores LLM, 6 motores TTS, stock footage (Pexels/Pixabay/Coverr), publicación social (Upload-Post), multi-aspect ratio y multi-idioma.
- **Cognitivo-profundo** (de reels-af): DAG de 18 reasoners (hunters → critic → narrators → judge), narrativa delayed-reveal, sample-accurate TTS, word-burst karaoke con libass, per-beat visual grounding, single-pass ffmpeg sin drift.
- **Editorial** (de corredor-content): brand voice como código, anti-alucinación vía `facts.json`, gate humano `plan → approve → produce`, pilares de contenido rotables, specs por plataforma (TikTok/Reels/Shorts/Long/FB/LinkedIn), cost tracking USD por LLM call.
- **Visual ownership** (ComfyUI nativo): 7 workflows pre-armados (Flux LoRA, ControlNet, IPAdapter, AnimateDiff, Inpaint, Upscale), wizard de training de LoRAs (Replicate cloud o kohya local), auto-retry OOM, workflow versioning con SHA256, multi-tenant. Self-hosted o managed (ViewComfy/RunComfy).
- **Long-form video** (inspirado en ViMax HKUDS): pipeline novel/script/idea → 3 actos → escenas → shots → consistency cross-shot vía VLM, para video de 5-60 min (documentales, libros animados, YouTube long-form, audiolibros visuales).

Más: integración profunda con **Higgsfield** — DoP image-to-video con 50+ camera presets cinematográficos nombrados, Soul para character consistency cross-beat, Effects VFX overlay, prompts canónicos extraídos del repo oficial de skills, y CLI fallback vía subprocess.

## Visión

Un mismo backend permite TRES modos de generación + uno para video largo:

| Modo | Velocidad | Costo | Brand identity | Uso típico |
|---|---|---|---|---|
| **Express** (reel 25s) | 3-8 min | ~$0.01-0.05 | — | Volumen, canales propios |
| **Premium** (reel 25s) | 70-110 s | ~$0.08-1.20 | — | Cuentas high-end, brands |
| **Brand-owned** (reel 25s, ComfyUI LoRA) | 90-180 s | ~$0.04-0.40 | ✓ marca completa | Multi-tenant, agencias |
| **Long-form** (video 5-60 min) | 45-75 min | ~$16-80 | ✓ opcional | Documentales, libros animados, YouTube |

El usuario elige por reel — o el sistema decide automáticamente según presupuesto y disponibilidad de LoRA del tenant.

## Estado

✅ **Sistema completo y operativo**:
- **413 tests verde** (30 long-form, 75 ComfyUI, 56 Higgsfield, 29 editorial, +223 pipeline base)
- 4 entry points operativos (topic / article / subject / **long_form_input**) + workflow editorial (plan / approve / produce-week)
- DAG de 18 reasoners (hunters → critic → narrators → judge → adapt → visual → accent)
- 5 patrones editoriales portados (brand voice as code, facts.json, pilares, audiencias, plataformas)
- Higgsfield integrado: DoP + Soul + Effects + CLI fallback + A/B harness
- ComfyUI nativo: **7 workflows registrados**, 3 tenants demo (`default`/`ruteo`/`ciencia`), wizard de training, OOM auto-retry, observability
- **Long-form**: NovelCompressor + RAGStore híbrido (numpy/FAISS) + ScriptPlanner (3 actos con intent routing) + SceneExtractor + StoryboardArtist + VLM consistency selectors + Director con 2 fases (plan barato + produce caro)
- Cost tracking USD por LLM call (30+ modelos tabulados)

Ver [`PLAN.md`](./PLAN.md) para el roadmap original y [`ARCHITECTURE.md`](./ARCHITECTURE.md) para decisiones técnicas.

## Estructura

```
contenido/
├── apps/                  # Puntos de entrada
│   ├── api/              # FastAPI REST (14 endpoints)
│   ├── webui/            # Streamlit (3 vistas)
│   └── cli/              # Typer CLI (plan/produce-week/brand-check/comfy/book)
├── core/                 # Lógica de dominio
│   ├── narrative/        # 18 reasoners (de reels-af) + facts injection en hunters
│   ├── planning/         # beats, cards, safe_zone (determinístico)
│   ├── llm_router/       # Abstracción multi-LLM + pricing.py (cost tracking)
│   ├── editorial/        # Plan/approve/produce + brand voice + facts (de corredor-content)
│   ├── tts/              # 6 engines + sample-accurate timing
│   ├── visual/           # Stock + IA + selector híbrido
│   │   └── generation/   # Gemini Image, Veo, Higgsfield (DoP/Soul/Effects), ken-burns, ComfyUI
│   ├── comfy/            # Wrapper async sobre comfy-cli + LoRA training wizard
│   ├── long_form/        # ✨ NUEVO: ViMax-inspired (5-60 min video)
│   │   ├── compressor.py     # NovelCompressor (chunk + parallel compress)
│   │   ├── rag.py            # RAGStore híbrido numpy/FAISS + sentence-transformers
│   │   ├── script_planner.py # 3-act NarrativeArc con intent routing
│   │   ├── scenes.py         # SceneExtractor + StoryboardArtist
│   │   ├── consistency.py    # ReferenceImageSelector + BestImageSelector (VLM)
│   │   ├── director.py       # plan_long_form + produce_long_form
│   │   └── prompts.py        # 9 prompts canónicos (atribución MIT a ViMax)
│   ├── editor/           # ffmpeg single-pass + multi-aspect + hw encoders
│   ├── subtitles/        # Word-burst libass + SRT fallback
│   └── distribution/     # Upload-Post (TikTok/IG)
├── editorial/            # ✨ FUENTE DE VERDAD editorial (versionada en git)
│   ├── brand-voice.md    #   tono de la marca
│   ├── facts.json        #   hechos verificables (anti-alucinación)
│   ├── pillars/*.md      #   5 pilares de contenido
│   ├── audiences.json    #   perfiles de audiencia
│   ├── platforms.json    #   specs por plataforma (TikTok/Reels/Shorts/Long/FB/LI)
│   ├── brand-visual.json #   LoRA + workflow + style por tenant (multi-tenant)
│   └── local-events.json #   eventos del calendario (seed de planes)
├── workflows/            # ComfyUI workflows (formato API) + index.json
│   ├── flux_basic_9x16.json
│   ├── flux_lora_brand.json
│   ├── flux_controlnet_pose.json     # layout strict (logo, sujeto)
│   ├── sdxl_ipadapter_style.json     # style transfer desde reference
│   ├── animatediff_lora.json         # video t2v con LoRA
│   ├── inpaint_brand.json            # producto cambia, fondo persiste
│   ├── upscale_face_restore.json     # post-process chain
│   └── index.json                    # registry con ComfyParameterMap
├── orchestration/        # Colas, estado, broker AgentField
├── shared/               # Schemas Pydantic, config loader
├── storage/              # Runtime: caches, RAG stores, embed_cache (gitignored)
│   └── long_form/<job>/  # Per-job: chunks/, compressed/, rag/, script.json, job.json
├── resource/             # Fuentes, BGM, assets estáticos
├── tests/                # pytest (413 tests)
├── scripts/              # A/B harnesses (Higgsfield + ComfyUI)
├── docs/                 # ADRs, EDITORIAL, COMFYUI, LONG_FORM, pipeline, cost model
└── .claude/skills/       # Submodule oficial higgsfield-ai/skills (dev-only)
```

## Quick start

```bash
# 1. Setup
cp .env.example .env       # editar con tus keys (mínimo: OPENROUTER_API_KEY + PEXELS_API_KEYS)
cp config.example.toml config.toml
uv sync --extra dev

# 2. Verificar configuración + capas
uv run python -m apps.cli.main config-check
uv run python -m apps.cli.main brand-check

# 3. Generar un reel single-shot (sin gate editorial)
uv run python -m apps.cli.main subject "Spring flowers" --mode express
uv run python -m apps.cli.main topic "the placebo effect" --mode premium
uv run python -m apps.cli.main article "https://arxiv.org/abs/2509.25541"

# 4. Workflow editorial (plan → approve → produce)
uv run python -m apps.cli.main plan --ideas 7
$EDITOR out/plans/plan-2026-W24.json   # marca "approved": true en lo que quieras
uv run python -m apps.cli.main produce-week --mode premium

# 5. ComfyUI con tu propia LoRA (brand identity)
uv run python -m apps.cli.main comfy install                  # instala ComfyUI
uv run python -m apps.cli.main comfy launch --background      # arranca server
uv run python -m apps.cli.main comfy lora train \             # entrena LoRA
    --name miMarca --image-dir ./fotos_brand --backend replicate
uv run python -m apps.cli.main comfy workflow list            # 7 workflows pre-armados

# 6. Long-form video (5-60 min)
echo "LONG_FORM_ENABLED=true" >> .env
echo "A time traveler loses memories with each change" > idea.txt
uv run python -m apps.cli.main book plan ./idea.txt --target-minutes 10
uv run python -m apps.cli.main book show <job_id>
uv run python -m apps.cli.main book produce <job_id>           # requiere GPU

# 7. A/B test ComfyUI vs Gemini para validar el moat de tu marca
uv run python scripts/comfyui_ab_test.py --quick --tenant miMarca

# 8. (Opcional) Stack completo con WebUI + Redis
make docker-up
# WebUI: http://localhost:8501
# API:   http://localhost:8000/docs
```

## Documentación

📘 **Lee primero**: [`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md) — tutorial paso a paso desde cero
✏️ **Editorial**: [`docs/EDITORIAL.md`](./docs/EDITORIAL.md) — brand voice, facts.json, gate humano, pilares
🎨 **ComfyUI**: [`docs/COMFYUI.md`](./docs/COMFYUI.md) — 7 workflows, LoRA training, multi-tenant, OOM retry, observability
📚 **Long-form**: [`docs/LONG_FORM.md`](./docs/LONG_FORM.md) — video 5-60 min, novelas, RAG, VLM consistency, 2-fase plan/produce
🔑 **API keys**: [`docs/API_KEYS.md`](./docs/API_KEYS.md) — qué keys, dónde, costos (incluye Higgsfield)
⚙️ **Configuración**: [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) — TOML + env (incluye Higgsfield + ComfyUI + long_form)
🛠️ **Errores**: [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md) — errores comunes y fixes
🎬 **Decisiones**: [`docs/DECISIONS.md`](./docs/DECISIONS.md) — ADRs 1-15 (incluye ADR-010..015 sobre Higgsfield + Editorial + ComfyUI + Long-form)
💻 **Ejemplos**: [`examples/`](./examples/) — curl, Python, batch listos para copiar
🆚 **A/B harnesses**:
  - [`scripts/higgsfield_ab_test.py`](./scripts/higgsfield_ab_test.py) — Veo vs Higgsfield DoP
  - [`scripts/comfyui_ab_test.py`](./scripts/comfyui_ab_test.py) — ComfyUI (brand LoRA) vs Gemini Image

## Comandos CLI principales

### Pipeline + editorial

| Comando | Propósito |
|---|---|
| `config-check` | Valida config + lista providers disponibles |
| `brand-check` | Inspecciona la capa editorial cargada (brand voice, pillars, facts) |
| `topic <topic>` | Reel single-shot desde un tema (DAG completo si `--mode premium`) |
| `article <url>` | Reel desde URL de artículo (extract → compose → pipeline) |
| `subject <subject>` | Reel rápido (legacy MPT, 1 LLM call) |
| `plan --ideas N` | Genera plan editorial semanal con N ideas (gate humano) |
| `plan-show [--week ...]` | Muestra plan con estado de aprobación |
| `produce-week [--mode ...]` | Ejecuta DAG para todas las ideas con `approved: true` |
| `list-voices --engine <e>` | Lista voces de un TTS engine |
| `task <task_id>` | Query state de una task (requiere Redis) |

### ComfyUI

| Comando | Propósito |
|---|---|
| `comfy status` | Health check del binario comfy-cli + server ComfyUI + tenants registrados |
| `comfy install` | Instala ComfyUI vía comfy-cli (15-30 min) |
| `comfy launch --background` | Arranca el server ComfyUI |
| `comfy workflow list` | 7 workflows registrados con timings + VRAM estimada |
| `comfy workflow show <id>` | Detalle + parámetros mapeados del workflow |
| `comfy lora list` | LoRAs instaladas en el server |
| `comfy lora download --url ...` | Descarga LoRA desde URL (CivitAI/HF/directa) |
| `comfy lora train --name ... --image-dir ... --backend replicate\|kohya` | Wizard de training |
| `comfy test <workflow_id>` | E2E test de un workflow con prompt de prueba |
| `comfy models <type>` | Lista modelos en el server (checkpoints/loras/vae/controlnet/...) |

### Long-form (video 5-60 min)

| Comando | Propósito |
|---|---|
| `book plan <input.txt> --target-minutes N --source-kind idea\|script\|novel` | Plan barato (~$1-4): NovelCompressor → RAG → 3-act arc → scenes → shots |
| `book show <job_id>` | Inspecciona script generado (gate humano: review/edit antes de producir) |
| `book produce <job_id>` | Renderiza shots + stitch final (~$15-20, requiere GPU + ComfyUI) |

Detalle completo en [`docs/LONG_FORM.md`](./docs/LONG_FORM.md).

## Capabilities matrix por workflow ComfyUI

| Workflow | Output | Brand LoRA | Layout strict | Style ref | Video | Custom nodes |
|---|---|---|---|---|---|---|
| `flux_basic_9x16` | image | — | — | — | — | (none) |
| `flux_lora_brand` | image | ✓ | — | — | — | (none) |
| `flux_controlnet_pose` | image | ✓ | ✓ (pose/depth/canny) | — | — | (none) |
| `sdxl_ipadapter_style` | image | — | — | ✓ (reference image) | — | IPAdapter_plus |
| `animatediff_lora` | **video** | ✓ | — | — | ✓ (16 frames) | AnimateDiff-Evolved + VideoHelperSuite |
| `inpaint_brand` | image | ✓ | — | ✓ (mask + reference) | — | (none, requiere SDXL inpainting checkpoint) |
| `upscale_face_restore` | image | — | — | — | — | (none, requiere upscale model) |

## Tenants editoriales pre-cargados

`editorial/brand-visual.json` viene con 3 tenants demo:

| Tenant | LoRA | Workflow | Style |
|---|---|---|---|
| `default` | (sin LoRA) | `flux_basic_9x16` | genérico |
| `ruteo` | `ruteo_brand_v1.safetensors` | `flux_lora_brand` | cinematic, central Veracruz, 35mm film, golden hour |
| `ciencia` | `ciencia_brand_v1.safetensors` | `flux_lora_brand` | documentary research lab, sharp focus, neutral lighting |

Los `.safetensors` no se incluyen — los entrenas con `comfy lora train` y los pones en `~/comfy/models/loras/`.

## Origen y créditos

Este proyecto integra, refactoriza y extiende código de:

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) — MIT License (capa industrial: API/WebUI/providers/distribución)
- [reels-af (agentfield)](https://github.com/agentfield/reels-af) — Apache 2.0 License (capa cognitiva: 18 reasoners DAG)
- [corredor-content](https://github.com/elimorals/corredor-content) — (capa editorial: brand voice + facts + plan/approve)
- [higgsfield-ai/skills](https://github.com/higgsfield-ai/skills) — submodule oficial (prompt engineering + model catalog)
- [Comfy-Org/comfy-cli](https://github.com/Comfy-Org/comfy-cli) + [ComfyUI](https://github.com/comfyanonymous/ComfyUI) — Apache 2.0 (visual ownership)
- [Replicate ai-toolkit](https://replicate.com/ostris/flux-dev-lora-trainer) + [kohya_ss](https://github.com/kohya-ss/sd-scripts) — backends del LoRA training wizard
- [HKUDS/ViMax](https://github.com/HKUDS/ViMax) — MIT License (algoritmos y prompts canónicos para long-form video; arXiv 2606.07649)

Ver [`docs/DECISIONS.md`](./docs/DECISIONS.md) para detalles sobre qué se conservó de cada proyecto y por qué (ADRs 1-15).

## Licencia

Apache 2.0 (compatible con todos los orígenes).
