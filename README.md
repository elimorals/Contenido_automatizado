# contenido

Plataforma de generación de reels verticales que fusiona CUATRO linajes:

- **Industrial-horizontal** (de MoneyPrinterTurbo): API REST + colas Redis, WebUI Streamlit, 20+ proveedores LLM, 6 motores TTS, stock footage (Pexels/Pixabay/Coverr), publicación social (Upload-Post), multi-aspect ratio y multi-idioma.
- **Cognitivo-profundo** (de reels-af): DAG de 18 reasoners (hunters → critic → narrators → judge), narrativa delayed-reveal, sample-accurate TTS, word-burst karaoke con libass, per-beat visual grounding, single-pass ffmpeg sin drift.
- **Editorial** (de corredor-content): brand voice como código, anti-alucinación vía `facts.json`, gate humano `plan → approve → produce`, pilares de contenido rotables, specs por plataforma (TikTok/Reels/Shorts/Long/FB/LinkedIn), cost tracking USD por LLM call.
- **Visual ownership** (ComfyUI nativo): workflows custom con LoRAs de marca entrenadas, ControlNet (pose/depth/canny), IPAdapter (style transfer), AnimateDiff. Multi-tenant: cada cliente trae su propia LoRA + workflow. Self-hosted o managed (ViewComfy/RunComfy).

Más: integración profunda con **Higgsfield** — DoP image-to-video con 50+ camera presets cinematográficos nombrados, Soul para character consistency cross-beat, Effects VFX overlay, prompts canónicos extraídos del repo oficial de skills, y CLI fallback vía subprocess.

## Visión

Un mismo backend permite dos modos:

| Modo | Velocidad | Costo | Calidad narrativa | Uso típico |
|---|---|---|---|---|
| **Express** | 3-8 min | ~$0.01-0.05 | Buena | Volumen, canales propios |
| **Premium** | 70-110 s | ~$0.08-1.20 | Cinematográfica | Cuentas high-end, brands |

El usuario elige por reel — o el sistema decide automáticamente según presupuesto.

## Estado

✅ **Sistema completo**: 357/357 tests verde · 3 entry points operativos · DAG de 18 reasoners · 5 patrones editoriales portados · Higgsfield (DoP + Soul + Effects) integrado con CLI fallback · ComfyUI nativo (LoRAs custom + ControlNet + IPAdapter + multi-tenant).

Ver [`PLAN.md`](./PLAN.md) para el roadmap original y [`ARCHITECTURE.md`](./ARCHITECTURE.md) para decisiones técnicas.

## Estructura

```
contenido/
├── apps/                  # Puntos de entrada
│   ├── api/              # FastAPI REST
│   ├── webui/            # Streamlit
│   └── cli/              # Typer CLI (incluye plan/produce-week/brand-check)
├── core/                 # Lógica de dominio
│   ├── narrative/        # 18 reasoners (de reels-af) + facts injection en hunters
│   ├── planning/         # beats, cards, safe_zone (determinístico)
│   ├── llm_router/       # Abstracción multi-LLM + pricing.py (cost tracking)
│   ├── editorial/        # Plan/approve/produce + brand voice + facts (de corredor-content)
│   ├── tts/              # 6 engines + sample-accurate timing
│   ├── visual/           # Stock + IA + selector híbrido
│   │   └── generation/   # Gemini Image, Veo, Higgsfield (DoP/Soul/Effects), ken-burns, ComfyUI
│   ├── comfy/            # Wrapper async sobre comfy-cli (install/launch/lora/node)
│   ├── editor/           # ffmpeg single-pass + multi-aspect + hw encoders
│   ├── subtitles/        # Word-burst libass + SRT fallback
│   └── distribution/     # Upload-Post (TikTok/IG)
├── editorial/            # ✨ FUENTE DE VERDAD editorial (versionada en git)
│   ├── brand-voice.md    #   tono de la marca
│   ├── facts.json        #   hechos verificables (anti-alucinación)
│   ├── pillars/*.md      #   5 pilares de contenido
│   ├── audiences.json    #   perfiles de audiencia
│   ├── platforms.json    #   specs por plataforma (TikTok/Reels/Shorts/Long/FB/LI)
│   └── local-events.json #   eventos del calendario (seed de planes)
├── orchestration/        # Colas, estado, broker AgentField
├── shared/               # Schemas Pydantic, config loader
├── workflows/            # ✨ ComfyUI workflows (formato API) + index.json
├── resource/             # Fuentes, BGM, assets estáticos
├── tests/                # pytest (357 tests)
├── docs/                 # ADRs, EDITORIAL, COMFYUI, pipeline, cost model
├── scripts/              # Batch runners (incluye Higgsfield A/B harness)
└── .claude/skills/       # Submodule oficial higgsfield-ai/skills (dev-only)
```

## Quick start

```bash
# 1. Setup
cp .env.example .env       # editar con tus keys (mínimo: OPENROUTER_API_KEY + PEXELS_API_KEYS)
cp config.example.toml config.toml
uv sync --extra dev

# 2. Verificar configuración + capa editorial
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

# 5. (Opcional) Stack completo con WebUI + Redis
make docker-up
# WebUI: http://localhost:8501
# API:   http://localhost:8000/docs
```

📘 **Lee primero**: [`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md) — tutorial paso a paso desde cero  
✏️ **Editorial**: [`docs/EDITORIAL.md`](./docs/EDITORIAL.md) — brand voice, facts.json, gate humano, pilares  
🎨 **ComfyUI**: [`docs/COMFYUI.md`](./docs/COMFYUI.md) — LoRAs custom, ControlNet, IPAdapter, multi-tenant  
🔑 **API keys**: [`docs/API_KEYS.md`](./docs/API_KEYS.md) — qué keys, dónde, costos (incluye Higgsfield)  
⚙️ **Configuración**: [`docs/CONFIGURATION.md`](./docs/CONFIGURATION.md) — TOML + env (incluye Higgsfield + ComfyUI)  
🛠️ **Errores**: [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md) — errores comunes y fixes  
🎬 **Decisiones**: [`docs/DECISIONS.md`](./docs/DECISIONS.md) — ADRs (incluye ADR-010..014)  
💻 **Ejemplos**: [`examples/`](./examples/) — curl, Python, batch listos para copiar  
🆚 **A/B harness**: [`scripts/higgsfield_ab_test.py`](./scripts/higgsfield_ab_test.py) — Veo vs Higgsfield DoP

## Comandos CLI principales

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
| `comfy status` | Health check del binario comfy-cli + server ComfyUI |
| `comfy install` | Instala ComfyUI vía comfy-cli (15-30 min) |
| `comfy launch --background` | Arranca el server ComfyUI |
| `comfy workflow list / show <id>` | Inspecciona workflows registrados |
| `comfy lora list / download --url ...` | Gestiona LoRAs en el server |
| `comfy test <workflow_id>` | E2E test de un workflow con prompt de prueba |
| `comfy models <type>` | Lista modelos en el server (checkpoints, loras, vae, ...) |

## Origen y créditos

Este proyecto integra, refactoriza y extiende código de:

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) — MIT License (capa industrial: API/WebUI/providers/distribución)
- [reels-af (agentfield)](https://github.com/agentfield/reels-af) — Apache 2.0 License (capa cognitiva: 18 reasoners DAG)
- [corredor-content](https://github.com/elimorals/corredor-content) — (capa editorial: brand voice + facts + plan/approve)
- [higgsfield-ai/skills](https://github.com/higgsfield-ai/skills) — submodule oficial (prompt engineering + model catalog)

Ver [`docs/DECISIONS.md`](./docs/DECISIONS.md) para detalles sobre qué se conservó de cada proyecto y por qué (ADRs 1-13).

## Licencia

Apache 2.0 (compatible con ambos orígenes).
