# Guía de API Keys

Cada provider externo que `contenido` puede usar, cómo obtener su key, costos típicos y alternativas.

## TL;DR — Setup mínimo

| Tier | Keys necesarias | Costo |
|---|---|---|
| 🟢 **Solo probar** | `OPENROUTER_API_KEY` + `PEXELS_API_KEYS` | $5 (250+ reels) |
| 🔵 **Producción Express** | + `OPENAI_API_KEY` o `DEEPSEEK_API_KEY` (fallback) | + $5 |
| 🟣 **Premium con Veo** | + `REEL_AF_USE_VEO=true` | +$1.10/reel |
| 🟡 **Distribución social** | + `UPLOAD_POST_API_KEY` | $29/mes plan básico |

## Categoría 1 — LLM Providers (al menos UNO requerido)

El sistema usa LLMs para generar scripts, hunters, narrators, judge, visual prompts, accents. **Necesitas mínimo uno**.

### 🥇 OpenRouter (recomendado para empezar)

**Por qué**: acceso unificado a 100+ modelos con UNA sola key. Default de reels-af.

- **URL**: https://openrouter.ai/keys
- **Signup**: email o Google
- **Formato**: `sk-or-v1-xxxxxxxxxxxxxxxx`
- **Pricing**: pay-as-you-go, mínimo $5 USD
- **Modelos default**:
  - Reasoning: `deepseek/deepseek-v4-pro` (~$0.27/M input, $1.10/M output)
  - TTS: `google/gemini-3.1-flash-tts-preview` (~$0.003/min)
  - Image: `google/gemini-2.5-flash-image` (~$0.035/image)
- **Costo típico**: ~$0.08/reel premium, ~$0.005/reel express
- **Variable**: `OPENROUTER_API_KEY=sk-or-v1-...`

**Cómo cambiar el modelo default:**
```bash
# .env
REEL_AF_MODEL=openrouter/anthropic/claude-sonnet-4-6
REEL_AF_MODEL=openrouter/openai/gpt-5-mini
REEL_AF_MODEL=openrouter/qwen/qwen3-235b-a22b-instruct
```

### 🥈 OpenAI (alternativa popular)

- **URL**: https://platform.openai.com/api-keys
- **Signup**: email + verificación de teléfono
- **Formato**: `sk-proj-...` (nuevo) o `sk-...` (clásico)
- **Pricing**: pay-as-you-go, mínimo $5
- **Modelos sugeridos**:
  - Express: `gpt-4o-mini` ($0.15/M input, $0.60/M output)
  - Premium: `gpt-5` o `gpt-5-mini`
- **Costo típico**: ~$0.001/reel express
- **Variables**:
  ```bash
  OPENAI_API_KEY=sk-proj-...
  OPENAI_MODEL_NAME=gpt-4o-mini
  ```

### 🥉 Anthropic Claude (premium quality)

- **URL**: https://console.anthropic.com/settings/keys
- **Signup**: email + verificación
- **Formato**: `sk-ant-api03-...`
- **Pricing**: pay-as-you-go, mínimo $5
- **Modelos sugeridos**:
  - `claude-haiku-4-5-20251001` (rápido, barato)
  - `claude-sonnet-4-6` (balanced)
  - `claude-opus-4-7` (premium)
- **Especialmente bueno para**: critic, judge (consistencia)
- **Variable**: `ANTHROPIC_API_KEY=sk-ant-api03-...`

### Otras opciones de LLM

| Provider | URL | Variable | Costo |
|---|---|---|---|
| **DeepSeek** | https://platform.deepseek.com/api_keys | `DEEPSEEK_API_KEY` | ~$0.14/M input |
| **Gemini directo** | https://aistudio.google.com/apikey | `GEMINI_API_KEY` | Free tier disponible |
| **Groq** (rápido) | https://console.groq.com/keys | `GROQ_API_KEY` | Gratis con rate limits |
| **Moonshot Kimi** | https://platform.moonshot.cn/console/api-keys | `MOONSHOT_API_KEY` | China-focused |
| **Qwen (Alibaba)** | https://dashscope.console.aliyun.com | `QWEN_API_KEY` | China-focused |
| **Ollama** (local, gratis) | https://ollama.com/download | `OLLAMA_BASE_URL=http://localhost:11434/v1` | $0 |

## Categoría 2 — Stock Footage (recomendado al menos UNO)

Para el modo Express y como fallback en Premium híbrido.

### 🥇 Pexels (gratis, recomendado)

- **URL**: https://www.pexels.com/api/new/
- **Signup**: email/Google
- **Formato**: 56-character API key
- **Pricing**: **GRATIS** (200 req/hr, 20k req/mes)
- **Calidad**: HD/4K disponible, 9:16 nativo
- **Variable**: `PEXELS_API_KEYS=key1,key2,key3` (puedes rotar varias keys)

### 🥈 Pixabay (gratis)

- **URL**: https://pixabay.com/api/docs/
- **Signup**: email
- **Pricing**: **GRATIS** (100 req/min)
- **Variable**: `PIXABAY_API_KEYS=key1,key2`

### 🥉 Coverr (gratis con registro)

- **URL**: https://coverr.co/api
- **Signup**: email
- **Pricing**: **GRATIS**
- **Mejor para**: 16:9 broadcast quality
- **Variable**: `COVERR_API_KEYS=key1`

### Rotación de keys

Si tienes varias accounts para evitar rate limits:
```bash
# Separar con comas, el sistema rotará automáticamente
PEXELS_API_KEYS=key_a,key_b,key_c
```

## Categoría 3 — TTS (opcional, Edge es gratis por default)

### Edge TTS (Microsoft) — DEFAULT, GRATIS

- **No requiere key** — usa el endpoint público de Azure
- **Variable**: ninguna necesaria
- **Calidad**: buena, ~50 voces multilenguaje (CJK first-class)
- **Limitación**: sin tags inline (no soporta `[curious]`)

### Gemini Flash TTS (premium, recomendado para Premium mode)

Es lo que reels-af usa por defecto. **Ya está incluido en `OPENROUTER_API_KEY`** — no necesitas key adicional.

- **Modelo**: `google/gemini-3.1-flash-tts-preview`
- **Costo**: ~$0.015/reel
- **Único feature**: tags inline `[curious]`, `[emphasis]`, `[confident]`
- **5 voces**: Charon, Kore (default), Schedar, Aoede, Puck

### Azure Cognitive Services Speech (opcional)

Para voces premium en idiomas específicos.

- **URL**: https://portal.azure.com → Crear recurso "Speech"
- **Pricing**: 500k chars/mes gratis, después ~$16/M chars
- **Variables**:
  ```bash
  AZURE_TTS_API_KEY=...
  AZURE_TTS_REGION=eastus
  ```

### SiliconFlow CosyVoice2 (opcional, voice cloning futuro)

- **URL**: https://siliconflow.cn
- **Variable**: `SILICONFLOW_API_KEY=...`

### Xiaomi MiMo (opcional, chino)

- **URL**: https://api.mimo.xiaomi.com
- **Variable**: `MIMO_API_KEY=...`

## Categoría 4 — Generación visual IA (incluido en OpenRouter)

### Gemini Image (default)

- Ya incluido en `OPENROUTER_API_KEY`
- **Modelo**: `google/gemini-2.5-flash-image`
- **Costo**: ~$0.035/imagen, ~$0.02/reel (5 frames)

### Veo i2v (opcional, premium)

Video generation desde first frame (Image-to-Video). **Costoso pero cinematográfico**.

- Ya incluido en `OPENROUTER_API_KEY`
- **Modelo**: `google/veo-3.1-lite`
- **Costo**: ~$1.10/reel (5 clips de 6s c/u)
- **Activar**:
  ```bash
  REEL_AF_USE_VEO=true
  ```

## Categoría 5 — Distribución social (opcional)

### Upload-Post (TikTok + Instagram)

- **URL**: https://upload-post.com
- **Pricing**: $29/mes (10 posts/día), $79/mes (40 posts/día)
- **Variable**:
  ```bash
  UPLOAD_POST_API_KEY=...
  UPLOAD_POST_AUTO_UPLOAD=true
  UPLOAD_POST_PLATFORMS=tiktok,instagram
  ```

## Categoría 6 — Whisper ASR (opcional, local)

Para subtítulos desde audio personalizado. **No requiere key** — corre local con `faster-whisper`.

Primera ejecución descarga el modelo (~2 GB para `large-v3`).

```bash
# .env (opcional)
WHISPER_MODEL_SIZE=large-v3       # tiny | base | small | medium | large | large-v3
WHISPER_DEVICE=cpu                # cuda si tienes GPU
WHISPER_COMPUTE_TYPE=int8         # int8 | float16
```

## Setup recomendado por caso de uso

### "Solo quiero probar"

```bash
OPENROUTER_API_KEY=sk-or-v1-...
PEXELS_API_KEYS=...
DEFAULT_MODE=express
```

**Costo total para 100 reels**: ~$1

### "Canal de creadores, 50 reels/día"

```bash
OPENROUTER_API_KEY=sk-or-v1-...          # backup
DEEPSEEK_API_KEY=...                      # primary, más barato
PEXELS_API_KEYS=key1,key2,key3            # rotación
PIXABAY_API_KEYS=key1,key2
DEFAULT_MODE=express
```

**Costo mensual**: ~$30 (1,500 reels)

### "Brand premium, 5 reels/semana, calidad cinematográfica"

```bash
OPENROUTER_API_KEY=sk-or-v1-...
ANTHROPIC_API_KEY=sk-ant-...              # para critic/judge
REEL_AF_USE_VEO=true                      # cinematografía Veo
PEXELS_API_KEYS=...                       # fallback
UPLOAD_POST_API_KEY=...                   # auto-publish
DEFAULT_MODE=premium
```

**Costo mensual**: ~$130 (20 reels × $1.20 Veo + $29 Upload-Post + $75 LLMs)

### "Volumen masivo con calidad, 100 reels/día"

```bash
OPENROUTER_API_KEY=...
DEEPSEEK_API_KEY=...                      # primary
PEXELS_API_KEYS=key1,key2,key3,key4,key5  # 5 keys para rate limits
PIXABAY_API_KEYS=key1,key2,key3
COVERR_API_KEYS=key1
ENABLE_REDIS=true                         # queue para concurrency
MAX_CONCURRENT_TASKS=10
DEFAULT_MODE=premium
```

**Costo mensual**: ~$250 (3,000 reels Premium ken-burns)

## Seguridad

- **NUNCA** commitees `.env` (ya está en `.gitignore`)
- En producción usa secrets management: AWS Secrets Manager, Vault, K8s secrets
- Rota keys cada 90 días
- Usa keys con scope mínimo (read-only donde aplique)
- Monitorea uso desde dashboards de cada provider

## Validar tus keys

```bash
uv run contenido config-check
```

Te muestra qué providers están configurados y cuáles faltan. Las keys con `✓` están listas.

## Troubleshooting

¿Una key no funciona? Ver [TROUBLESHOOTING.md](./TROUBLESHOOTING.md#api-keys).
