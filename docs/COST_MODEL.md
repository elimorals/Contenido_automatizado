# Modelo de costos

Documentación viva: actualizar cuando cambien precios de providers.

Última actualización: 2026-06-15.

## Tabla rápida

| Setup | Tiempo | Costo/reel | Brand identity | Use case |
|---|---|---|---|---|
| Express + Edge + Pexels | 3-8 min | ~$0.001 | — | Volumen masivo |
| Premium + ken-burns | 70-110s | ~$0.08 | — | High-end estándar |
| Premium + Higgsfield DoP | 90-120s | ~$1.10 | — | Top tier (50+ presets) |
| Premium + Higgsfield Soul+DoP+FX | 110-150s | ~$1.50 | character (Soul) | Branded narrative |
| **Premium + ComfyUI brand LoRA (self-host)** | 90-180s | **~$0.08-0.15** | **✓ marca completa** | **Multi-tenant, agencias** |
| Premium + ComfyUI managed (ViewComfy) | 90-180s | ~$0.30-0.80 | ✓ marca completa | Multi-tenant sin GPU |
| **Long-form 10 min (book/idea)** | 45-75 min | **~$16-24** | ✓ opcional | Documentales, YouTube long |
| **Long-form 30 min** | 90-150 min | **~$45-50** | ✓ opcional | Libros animados, podcast visual |
| **Long-form 60 min** | 180-300 min | **~$70-80** | ✓ opcional | Audiolibros, documentales largos |

## Modo Premium

### Default (DeepSeek + Gemini Flash + Ken-burns)

| Componente | Calls | Costo unidad | Subtotal |
|---|---|---|---|
| DeepSeek V4 Pro (hunters) | 4 calls × ~800 tokens | $0.27/M input + $1.10/M output | ~$0.003 |
| DeepSeek V4 Pro (critic) | 1 call × ~3000 tokens | idem | ~$0.003 |
| DeepSeek V4 Pro (narrators) | 3 calls × ~1500 tokens | idem | ~$0.005 |
| DeepSeek V4 Pro (judge) | 1 call × ~2000 tokens | idem | ~$0.002 |
| DeepSeek V4 Pro (compose) | 1 call × ~1500 tokens | idem | ~$0.002 |
| DeepSeek V4 Pro (visual+accent) | 2 calls × ~2000 tokens | idem | ~$0.004 |
| Gemini Flash TTS | ~150 words × 4 sentences | $0.003/min | ~$0.015 |
| Gemini 2.5 Flash Image | ~5 first frames | $0.0035/image | ~$0.018 |
| Ken-burns | local | $0 | $0 |
| **TOTAL** | | | **~$0.05-0.08** |

### Con Veo i2v

| Componente | Costo |
|---|---|
| Todo lo anterior | $0.05-0.08 |
| Veo 3.1 Lite i2v (~5 clips de 6s) | ~$1.10 |
| **TOTAL** | **~$1.15-1.20** |

### Con Higgsfield DoP (alternativa a Veo)

| Componente | Costo |
|---|---|
| LLM + TTS + Gemini Image | $0.05-0.08 |
| Higgsfield DoP turbo (~5 clips de 5s × $0.20) | ~$1.00 |
| **TOTAL** | **~$1.05-1.10** |

DoP variants:
| Variant | Costo/clip | Total reel (5 clips) |
|---|---|---|
| `dop-lite` | $0.13 | $0.65 |
| `dop-turbo` (default) | $0.20 | $1.00 |
| `dop-preview` | $0.30 | $1.50 |

### Con Higgsfield Full Stack (DoP + Soul + Effects)

| Componente | Costo |
|---|---|
| LLM + TTS | $0.03 |
| Higgsfield Soul (~5 first frames × $0.05) | ~$0.25 |
| Higgsfield DoP turbo (~5 clips × $0.20) | ~$1.00 |
| Higgsfield Effects (~2 beats × $0.10) | ~$0.20 |
| **TOTAL** | **~$1.48** |

Ventaja vs Veo: motion presets cinematográficos nombrados + character consistency cross-beat (Soul). Trade-off: ~30% más caro que Veo solo, ~5% más caro que Veo+ken-burns mix.

### Con ComfyUI brand LoRA (self-hosted)

| Componente | Costo |
|---|---|
| LLM (DAG 18 reasoners) | $0.020 |
| TTS (Gemini Flash o Edge) | $0-0.015 |
| ComfyUI Flux + LoRA (compute propio) | $0 (post hardware ~$2k) |
| Ken-burns (motion) | $0 |
| **TOTAL** | **~$0.02-0.04** |

El moat real: tras pagar la GPU, **cada reel cuesta solo los LLMs**. A 100 reels/día → ~$60/mes (LLM solamente) y la identidad de marca queda baked en el output sin prompts gigantes.

### Con ComfyUI managed (ViewComfy / RunComfy / Modal serverless)

| Componente | Costo |
|---|---|
| LLM + TTS | $0.03 |
| Hosting baseline ViewComfy | ~$50/mes / por reels = depende del volumen |
| Por reel (Flux + LoRA, 22s GPU H100) | ~$0.04 + baseline |
| **TOTAL típico (50 reels/mes)** | **~$1.03 + $1.00 baseline = ~$2/reel** |
| **TOTAL volumen (500 reels/mes)** | **~$0.13/reel** |

ViewComfy/RunComfy escalan inversamente: el baseline se diluye con volumen.

### Training inicial de LoRA (one-time)

| Backend | Costo | Tiempo | Pre-req |
|---|---|---|---|
| Replicate ai-toolkit (Flux LoRA) | ~$2-3 | ~25 min | Sin GPU local |
| Kohya local (Flux LoRA) | $0 | 4-8 horas | GPU NVIDIA 16GB+ |
| CivitAI online trainer | ~$10+ | 30-60 min | Sin GPU local, UI simple |

Una LoRA buena dura 6-12 meses sin re-entrenar (si tu identidad visual no cambia).

### LoRA training via `contenido comfy lora train`

El wizard incluido (`core/comfy/training.py`) emite el comando bash listo. Para Replicate:
- ~$2-3 por entrenamiento
- ~25 minutos
- Output: URL al `.safetensors` listo para descargar con `comfy lora download`

## Modo Long-form (video 5-60 min)

Pipeline `contenido book plan/produce` (inspirado en ViMax). 2 fases:

### Fase 1 — PLAN (cheap, sin GPU, gate humano)

| Componente | Costo aprox | Tiempo |
|---|---|---|
| Plan idea → 3-act arc | $0.02-0.10 | 30s-2min |
| Plan novel (50k words) → arc (compress N chunks paralelo) | $0.50-2.00 | 3-8min |
| SceneExtractor (6-12 scenes) | $0.10-0.30 | 30s-1min |
| StoryboardArtist (30-100 shots) | $0.50-1.50 | 2-5min |
| **TOTAL Phase 1 (book → script)** | **$1-4** | **5-15min** |

### Fase 2 — PRODUCE (expensive, requiere GPU + ComfyUI)

Por shot:

| Componente | Costo aprox |
|---|---|
| ReferenceImageSelector (VLM 2-stage) | $0.02 |
| BestImageSelector (VLM best-of-3) | $0.04 |
| ComfyUI image gen × 3 candidates (self-host) | $0 |
| Higgsfield DoP (5s motion clip) | $0.20 |
| TTS narration | $0.005 |
| **Por shot** | **~$0.27** |

Por video:

| Duración | Shots aprox | Costo total Phase 2 |
|---|---|---|
| 10 min | 60 | ~$15-20 |
| 30 min | 150 | ~$40-45 |
| 60 min | 250 | ~$67-75 |

**End-to-end (Phase 1 + Phase 2)**:
- 10 min: ~$16-24, ~45-75 min wall-clock
- 30 min: ~$45-50, ~90-150 min
- 60 min: ~$70-80, ~180-300 min

Optimizaciones futuras (no implementadas):
- Compress + RAG share cross-jobs (libros del mismo dominio)
- Generate 2 candidates en vez de 3 (-33% costo)
- Reusar portraits cross-scene (-$0.10/scene)

## Modo Express (MPT clásico)

| Componente | Calls | Costo unidad | Subtotal |
|---|---|---|---|
| OpenAI GPT-4o-mini (script) | 1 call × ~500 tokens | $0.15/M input + $0.60/M output | ~$0.0005 |
| OpenAI GPT-4o-mini (terms) | 1 call × ~800 tokens | idem | ~$0.0008 |
| Edge TTS | local proxy | $0 (gratis) | $0 |
| Pexels stock | ~5 videos | $0 (free tier) | $0 |
| Whisper local (subtitle) | local | $0 | $0 |
| **TOTAL** | | | **~$0.001-0.005** |

### Express con providers premium

Cambiar Edge TTS por Azure TTS:
- Azure TTS: ~$16/M chars → reels de 25s = ~500 chars = ~$0.008

Cambiar OpenAI por Claude Haiku:
- Anthropic Claude Haiku: ~$0.0001/script call

## Comparativa

| Modo | Tiempo | Costo | Quality | Use case |
|---|---|---|---|---|
| Express + Edge + Pexels | 3-8 min | ~$0.001 | Buena | Volumen masivo |
| Express + Azure + Pexels | 3-8 min | ~$0.015 | Muy buena | Volumen premium |
| Premium + ken-burns | 70-110s | ~$0.08 | Excelente | High-end estándar |
| Premium + Veo i2v | 85-110s | ~$1.20 | Cinematográfica | Top tier |
| Premium + Higgsfield DoP | 90-120s | ~$1.10 | Cinematográfica + presets nombrados | Top tier diferenciado |
| Premium + Higgsfield Soul+DoP | 110-150s | ~$1.50 | Cinematográfica + personaje consistente | Branded narrative |
| **Premium + ComfyUI self-host** | 90-180s | **~$0.04** | **Brand identity baked-in** | **Marca propia, volumen** |
| Premium + ComfyUI managed | 90-180s | ~$0.13-2.00 | Brand identity baked-in | Multi-tenant, agencias |

## Throughput por presupuesto mensual

Asumiendo $500/mes:
- Express barato: ~500,000 reels
- Express premium: ~33,000 reels
- Premium ken-burns: ~6,250 reels
- Premium Veo: ~415 reels

## Recomendación por caso

| Caso | Modo + config |
|---|---|
| Canal con 50 reels/día | Express + Edge + Pexels (~$0.05/día) |
| Brand premium (5 reels/sem) | Premium + Veo (~$24/sem) |
| Creador high-end (1 reel/día) | Premium + ken-burns (~$2.40/mes) |
| Volumen masivo con calidad | Premium + ken-burns ($240/mes para 100 reels/día) |
| **Marca con identidad visual única** | **Premium + ComfyUI self-host LoRA (~$0.04/reel post hardware)** |
| **Agencia multi-tenant** | Premium + ComfyUI managed ViewComfy (~$0.13/reel a 500/mes) |
| **Brand narrative continua** (mismo personaje cross-reels) | Premium + Higgsfield Soul + ComfyUI ControlNet (~$1.60/reel) |

## Optimizaciones futuras

1. **Cache de hunters** — mismo topic en 24h reusa los 12 candidates (~$0.013 saving/call)
2. **Batch de TTS** — Azure Batch synthesis (50% descuento)
3. **Stock pre-fetched** — pool de videos por categoría (~$0.005 saving/reel)
4. **Modo "balanced"** — DeepSeek para hunters/narrators + Claude Haiku para critic/judge (~$0.04 vs $0.05)
