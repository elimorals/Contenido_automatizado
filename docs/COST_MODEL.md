# Modelo de costos

Documentación viva: actualizar cuando cambien precios de providers.

Última actualización: 2026-06-15.

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

## Optimizaciones futuras

1. **Cache de hunters** — mismo topic en 24h reusa los 12 candidates (~$0.013 saving/call)
2. **Batch de TTS** — Azure Batch synthesis (50% descuento)
3. **Stock pre-fetched** — pool de videos por categoría (~$0.005 saving/reel)
4. **Modo "balanced"** — DeepSeek para hunters/narrators + Claude Haiku para critic/judge (~$0.04 vs $0.05)
