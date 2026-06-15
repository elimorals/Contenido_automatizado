# contenido

Plataforma de generación de reels verticales que fusiona dos linajes:

- **Industrial-horizontal** (de MoneyPrinterTurbo): API REST + colas Redis, WebUI Streamlit, 20+ proveedores LLM, 6 motores TTS, stock footage (Pexels/Pixabay/Coverr), publicación social (Upload-Post), multi-aspect ratio y multi-idioma.
- **Cognitivo-profundo** (de reels-af): DAG de 18 reasoners (hunters → critic → narrators → judge), narrativa delayed-reveal, sample-accurate TTS, word-burst karaoke con libass, per-beat visual grounding, single-pass ffmpeg sin drift.

## Visión

Un mismo backend permite dos modos:

| Modo | Velocidad | Costo | Calidad narrativa | Uso típico |
|---|---|---|---|---|
| **Express** | 3-8 min | ~$0.01-0.05 | Buena | Volumen, canales propios |
| **Premium** | 70-110 s | ~$0.08-1.20 | Cinematográfica | Cuentas high-end, brands |

El usuario elige por reel — o el sistema decide automáticamente según presupuesto.

## Estado

🚧 **Fase 0 — Cimientos** (scaffolding inicial).

Ver [`PLAN.md`](./PLAN.md) para el roadmap de 6 fases y [`ARCHITECTURE.md`](./ARCHITECTURE.md) para decisiones técnicas.

## Estructura

```
contenido/
├── apps/                  # Puntos de entrada
│   ├── api/              # FastAPI REST
│   ├── webui/            # Streamlit
│   └── cli/              # Typer CLI
├── core/                 # Lógica de dominio
│   ├── narrative/        # 18 reasoners (de reels-af)
│   ├── planning/         # beats, cards, safe_zone (determinístico)
│   ├── llm_router/       # Abstracción multi-LLM (de MPT)
│   ├── tts/              # 6 engines + sample-accurate timing
│   ├── visual/           # Stock + generación IA (selector híbrido)
│   ├── editor/           # ffmpeg single-pass + multi-aspect + hw encoders
│   ├── subtitles/        # Word-burst libass + SRT fallback
│   └── distribution/     # Upload-Post (TikTok/IG)
├── orchestration/        # Colas, estado, broker AgentField
├── shared/               # Schemas Pydantic, config loader
├── resource/             # Fuentes, BGM, assets estáticos
├── tests/                # pytest
├── docs/                 # ADRs, pipeline, cost model
└── scripts/              # Batch runners, utilidades
```

## Quick start

```bash
# 1. Setup
cp .env.example .env       # editar con tus keys (mínimo: OPENROUTER_API_KEY + PEXELS_API_KEYS)
cp config.example.toml config.toml
uv sync --extra dev

# 2. Verificar
uv run contenido config-check

# 3. Generar tu primer reel
uv run contenido subject "Spring flowers" --mode express
uv run contenido topic "the placebo effect" --mode premium
uv run contenido article "https://arxiv.org/abs/2509.25541"

# 4. (Opcional) Stack completo con WebUI + Redis
make docker-up
# WebUI: http://localhost:8501
# API:   http://localhost:8000/docs
```

📘 **Lee primero**: [`docs/GETTING_STARTED.md`](./docs/GETTING_STARTED.md) — tutorial paso a paso desde cero  
🔑 **API keys**: [`docs/API_KEYS.md`](./docs/API_KEYS.md) — qué keys, dónde, costos  
🛠️ **Errores**: [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md) — errores comunes y fixes  
💻 **Ejemplos**: [`examples/`](./examples/) — curl, Python, batch listos para copiar

## Origen y créditos

Este proyecto integra y refactoriza código de:

- [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) — MIT License
- [reels-af (agentfield)](https://github.com/agentfield/reels-af) — Apache 2.0 License

Ver [`docs/DECISIONS.md`](./docs/DECISIONS.md) para detalles sobre qué se conservó de cada proyecto y por qué.

## Licencia

Apache 2.0 (compatible con ambos orígenes).
