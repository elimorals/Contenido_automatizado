# core/llm_router

Capa de abstracción de proveedores LLM. Combina los 20+ providers de MPT con la interfaz de reasoner de reels-af.

## Providers soportados (Fase 1)

| Provider | Config key | Default model | Compat |
|---|---|---|---|
| OpenAI | `openai` | gpt-4o-mini | Chat Completions |
| Azure | `azure` | gpt-35-turbo | Custom deployment |
| Moonshot | `moonshot` | moonshot-v1-8k | OpenAI-compatible |
| Ollama | `ollama` | (local) | OpenAI-compatible |
| Qwen | `qwen` | qwen-max | DashScope SDK |
| Gemini | `gemini` | gemini-2.5-flash | Google SDK |
| Groq | `groq` | llama-3.3-70b | OpenAI-compatible |
| DeepSeek | `deepseek` | deepseek-chat | OpenAI-compatible |
| OpenRouter | `openrouter` | deepseek-v4-pro | 100+ proxy |
| LiteLLM | `litellm` | configurable | 100+ unified |
| Pollinations | `pollinations` | openai-fast | Public API |
| Xiaomi MiMo | `mimo` | mimo-v2.5-pro | OpenAI-compatible |
| MiniMax | `minimax` | MiniMax-M3 | OpenAI-compatible |
| Cloudflare | `cloudflare` | Workers AI | OpenAI-compatible |
| AIHubMix | `aihubmix` | gpt-5.4-mini | OpenAI-compatible |
| AIML API | `aimlapi` | openai/gpt-4o-mini | OpenAI-compatible |
| OneAPI | `oneapi` | Custom | OpenAI-compatible |
| Grok (xAI) | `grok` | grok-4.3 | OpenAI-compatible |
| ModelScope | `modelscope` | Qwen3-32B | Alibaba |
| Anthropic | `anthropic` | claude-sonnet-4.6 | Anthropic SDK |

## Interfaz

```python
from core.llm_router import LLMRouter, get_provider

provider = get_provider("openrouter")  # auto-config desde shared.config
response = await provider.complete(
    prompt="...",
    system="...",
    temperature=0.7,
    max_tokens=2000,
)
```

## Por qué necesitamos esto

- **reels-af original** solo soportaba OpenRouter → vendor lock-in
- **MPT original** tenía 20+ providers pero sin interfaz unificada (`if/elif` chain de 100+ líneas en `llm.py`)
- Esta capa permite **mezclar providers por reasoner**: DeepSeek para hunters (creativo, barato), Anthropic para critic (consistency), Gemini Flash para visual prompts (rápido)

## Cost optimization automático

Modo `premium`: usa provider de máxima calidad.  
Modo `express`: usa provider más barato (Edge TTS companion, Pollinations).  
Modo `balanced` (futuro): DeepSeek para volumen + Anthropic para tareas críticas.
