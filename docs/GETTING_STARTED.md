# Getting Started

Tutorial paso a paso: desde `git clone` hasta tu primer reel en ~15 minutos.

## Prerequisitos

| Requisito | Versión mínima | Cómo verificar |
|---|---|---|
| Python | 3.11 | `python --version` |
| ffmpeg | 6.0+ | `ffmpeg -version` |
| uv (opcional) | 0.5+ | `uv --version` |
| Git | 2.0+ | `git --version` |
| Docker (opcional) | 24+ | `docker --version` |

### Instalar dependencias del sistema

**macOS:**
```bash
brew install python@3.11 ffmpeg uv
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**
- Python: https://www.python.org/downloads/
- ffmpeg: `winget install ffmpeg` o https://www.gyan.dev/ffmpeg/builds/
- uv: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

## Paso 1 — Clonar e instalar

```bash
git clone <url-del-repo> contenido
cd contenido

# Con uv (recomendado, 10× más rápido):
uv sync --extra dev

# O con pip tradicional:
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Paso 2 — Configurar API keys mínimas

Copia los archivos de ejemplo:

```bash
cp .env.example .env
cp config.example.toml config.toml
```

Para tu **primer reel** solo necesitas DOS keys (~5 minutos de signup):

### 1. OpenRouter (LLM unificado) — $5 mínimo

1. Ve a https://openrouter.ai/keys
2. Sign up con email o Google
3. Genera key → cópiala (formato `sk-or-v1-...`)
4. Top-up de $5 USD (suficiente para ~250 reels express o ~50 premium)

### 2. Pexels (stock footage) — GRATIS

1. Ve a https://www.pexels.com/api/new/
2. Login con email/Google
3. Solicita key → la verás inmediatamente

### Editar `.env`

Abre `.env` con tu editor y pega las keys:

```bash
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
PEXELS_API_KEYS=tu_pexels_key_aqui

# Opcional pero recomendado:
DEFAULT_MODE=express              # más barato para empezar
ENABLE_REDIS=false                # no necesitas Redis para el primer reel
```

Más detalles en [API_KEYS.md](./API_KEYS.md).

## Paso 3 — Verificar que todo está bien

```bash
uv run contenido config-check
```

Deberías ver:
```
=== Configuración cargada ===
  Env: dev
  Default mode: express
  Redis: ✗

=== LLM Providers ===
  ✓ openrouter
  ✗ openai
  ...

=== TTS Engines ===
  • edge
  • gemini_flash
  ...

=== Stock Providers ===
  ✓ pexels
```

Si ves `✓ openrouter` y `✓ pexels`, estás listo.

## Paso 4 — Tu primer reel (modo Express)

El modo **Express** es el más rápido y barato (~3-5 min, ~$0.001/reel). Perfecto para validar que todo funciona.

```bash
uv run contenido subject "The science of why coffee makes you alert" \
    --mode express \
    --voice-name "en-US-AvaNeural-Female"
```

Deberías ver:
```
🎬 Generando reel — task abc12345
   Entry: The science of why coffee makes you alert
   Mode: express
   Aspect: 9:16

Pipeline  ████████████████████  100%

✓ Done in 142.3s
   Output: ./output/abc12345-.../final-1.mp4
   Timings: {
     "script": 2.3,
     "tts": 12.4,
     "materials": 28.1,
     "stitch": 78.5,
     "total": 142.3
   }
```

¡Listo! Abre `./output/abc12345-.../final-1.mp4` para ver tu reel.

## Paso 5 — Tu primer reel Premium (DAG profundo)

Modo **Premium** activa los 18 reasoners (delayed-reveal, hunters, judge…). Más caro (~$0.08-0.10) pero **mucho** mejor calidad narrativa.

```bash
uv run contenido topic "the placebo effect" \
    --mode premium \
    --strategy hybrid
```

Esto correrá:
1. 4 hunters paralelos → 12 candidates
2. Critic → top 3
3. 3 narrators paralelos → 3 scripts delayed-reveal
4. Judge → winner
5. TTS sample-accurate
6. Per-beat visual planning con grounding en evidence
7. Per-beat accents editoriales
8. Media generation (stock + Gemini Image híbrido)
9. Single-pass ffmpeg con word-burst karaoke

Total: ~70-110s. Output en `./output/<task_id>/reel.mp4`.

## Paso 6 — Levantar la WebUI

Para usuarios no-técnicos o para iterar visualmente:

### Opción A: Sin Docker (solo API + WebUI locales)

```bash
# Terminal 1
uv run uvicorn apps.api.main:app --reload --port 8000

# Terminal 2
uv run streamlit run apps/webui/Main.py --server.port 8501
```

Abre:
- WebUI: http://localhost:8501
- API docs (Swagger): http://localhost:8000/docs

### Opción B: Stack completo con Docker (Redis + workers)

```bash
make docker-up
# WebUI: http://localhost:8501
# API:   http://localhost:8000/docs
# Stop:  make docker-down
```

## Paso 7 — Generar tu primer reel desde un artículo

```bash
uv run contenido article "https://en.wikipedia.org/wiki/Placebo" \
    --mode premium
```

El reasoner `extract_essence` leerá el artículo y producirá un Essence (core_claim + mechanism + evidence). Luego `compose_script` genera el ScriptDraft con loop-back.

## Próximos pasos

| Quieres… | Lee… |
|---|---|
| Entender cada parámetro de config | [CONFIGURATION.md](./CONFIGURATION.md) |
| Ver todos los endpoints REST | [API_REFERENCE.md](./API_REFERENCE.md) |
| Optimizar costos | [COST_MODEL.md](./COST_MODEL.md) |
| Diagnosticar un error | [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) |
| Conocer el pipeline en detalle | [PIPELINE.md](./PIPELINE.md) |
| Ver decisiones arquitectónicas | [DECISIONS.md](./DECISIONS.md) |
| Migrar desde MoneyPrinterTurbo | [MIGRATION_FROM_MPT.md](./MIGRATION_FROM_MPT.md) |
| Ejemplos curl/Python | [`../examples/`](../examples/) |

## Trucos útiles

```bash
# Generar sin progress bar (para scripts)
uv run contenido subject "X" --quiet

# Generar en modo verbose (logs detallados)
LOG_LEVEL=DEBUG uv run contenido topic "X"

# Generar 5 variaciones del mismo tema
for i in 1 2 3 4 5; do
  uv run contenido subject "Beautiful sunsets" --output "./output/sunset_$i"
done

# Listar voces disponibles
uv run contenido list-voices --engine edge

# Verificar estado de una task (requiere Redis)
uv run contenido task <task_id>
```

## ¿Algo no funciona?

→ [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) tiene errores comunes con fixes.
