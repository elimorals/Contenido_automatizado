# Capa Editorial

Patrón portado de [`corredor-content`](https://github.com/elimorals/corredor-content). Le da a `contenido` lo que antes le faltaba: una **fuente de verdad** del programa de contenido (marca, hechos, pilares) versionada en git + un **gate humano** entre ideación y producción.

## Filosofía

> *"Una idea mala genera 28 piezas a la basura."* — README de corredor-content

```
plan      →   humano revisa  →   produce-week   →   inspecciona   →   publica
(LLM)         (approved: true)    (DAG completo)     (output/)        (Upload-Post)
```

Si nadie aprueba ideas, el costo es **$0**. El gate previene tasks de $1.20 desperdiciadas en hooks malos.

## Estructura

Todo vive en `editorial/` (peer de `core/`, `apps/`, etc.):

```
editorial/
├── README.md               # cómo usar esta carpeta
├── brand-voice.md          # ✨ FUENTE DE VERDAD del tono
├── facts.json              # 🛡️ anti-alucinación: people, studies, facts
├── audiences.json          # perfiles de a quién hablas
├── platforms.json          # specs por plataforma (TikTok, Reels, Shorts, …)
├── local-events.json       # eventos del calendario para seed de planes
└── pillars/                # 5 pilares default (editables)
    ├── educacion.md
    ├── historia.md
    ├── ciencia.md
    ├── cultura.md
    └── utilidad.md
```

## Los 5 patrones portados (qué resuelve cada uno)

### 1. Gate humano entre plan y producción

| Comando | Qué hace |
|---|---|
| `uv run python -m apps.cli.main plan --ideas 7` | LLM genera 7 ideas semana en `out/plans/plan-YYYY-Www.json` con `approved: false` |
| `$EDITOR out/plans/plan-YYYY-Www.json` | Tú marcas `"approved": true` en las que quieras producir |
| `uv run python -m apps.cli.main plan-show` | Muestra el plan con ✓ por idea aprobada |
| `uv run python -m apps.cli.main produce-week` | Ejecuta el DAG completo SOLO para `approved: true` |

### 2. Brand voice como código

`editorial/brand-voice.md` es Markdown versionado. Los reasoners lo cargan en su system prompt. Si cambias el tono, cualquier corrida futura lo respeta sin re-explicarle a ChatGPT cada vez.

### 3. Facts.json anti-alucinación

`editorial/facts.json` define `verified_people`, `verified_studies`, `verified_facts`. El módulo `core.editorial.facts_anti_hallucination_block()` los serializa en un bloque que se inyecta automáticamente al system prompt de TODOS los 4 hunters (`specific_figure`, `reversal`, `temporal`, `cross_domain`).

Si `facts.json` está vacío, se inyecta un bloque GENERIC con reglas de Wikipedia-tier. Si tiene contenido, las hunters reciben los datos exactos y se les instruye: **"cita SOLO desde estos — nunca inventes"**.

Ver `core/narrative/hunters.py:_facts_block()`.

### 4. Cost transparency end-to-end

`core/llm_router/pricing.py` tiene la tabla `PRICING` con 30+ modelos (OpenAI, Anthropic, Gemini, DeepSeek, Groq, Qwen, MiMo, …) en USD por millón de tokens.

Cada `LLMProvider` ahora stamp el costo después de cada call:

```python
provider.last_input_tokens     # 2031
provider.last_output_tokens    # 487
provider.last_cost_usd         # 0.0058
provider.total_cost_usd        # acumulado
provider.total_calls           # 12
provider.get_cost_record(phase="hunt")  # → LLMCostRecord
```

Modelos no tabulados → fallback conservador (`Price(1.0, 3.0)`). Mejor sobre-estimar que sub-reportar.

### 5. Platform-aware output

`editorial/platforms.json` modela 6 plataformas (TikTok, Instagram Reels, YouTube Shorts, YouTube Long, Facebook Reels, LinkedIn Video) con:

- `aspect_ratio` (9:16, 16:9, 1:1)
- `video_duration_s` {min, max, recommended}
- `caption_max_chars` + `caption_recommended_chars`
- `hashtags_min` / `hashtags_max`
- `notes` ("Hook visual en 3s. CTA en bio.")

`ReelIdea.platforms` valida contra estos specs y `validate_idea()` emite warning si la plataforma no tiene spec.

## Comandos CLI nuevos

```bash
# Cargar capa editorial e inspeccionar lo que ve el código
uv run python -m apps.cli.main brand-check

# Plan semanal
uv run python -m apps.cli.main plan --ideas 7

# Ver plan con estado de aprobación
uv run python -m apps.cli.main plan-show --week 2026-W24

# Producir todo lo aprobado de una semana
uv run python -m apps.cli.main produce-week --mode premium --aspect 9:16
```

## Workflow end-to-end típico

```bash
# 1. Edita editorial/brand-voice.md con tu tono real
# 2. Llena editorial/facts.json con tus datos verificables
# 3. (Opcional) edita editorial/pillars/*.md para tus pilares

# Cada lunes:
uv run python -m apps.cli.main plan --ideas 7
# → out/plans/plan-2026-W24.json (7 ideas, approved: false)

$EDITOR out/plans/plan-2026-W24.json
# → marca approved: true en lo que quieres producir

uv run python -m apps.cli.main plan-show
# → ✓ 3 aprobadas, ○ 4 rechazadas

uv run python -m apps.cli.main produce-week --mode premium
# → DAG completo para las 3 aprobadas

# Inspecciona output/, publica con upload-post (futuro)
```

## API Python

```python
from core.editorial import (
    load_editorial, EditorialRegistry,
    generate_plan, load_plan, save_plan,
    validate_idea, validate_plan,
    facts_anti_hallucination_block,
)

# Cargar registro
r: EditorialRegistry = load_editorial()
print(r.pillars.keys())              # dict_keys(['ciencia', 'historia', ...])
print(r.facts.verified_people[0].name)

# Generar plan programáticamente
plan, path, cost = await generate_plan(n_ideas=7, registry=r)

# Validar
result = validate_plan(plan, r, max_per_pillar=2)
if not result.ok:
    for err in result.errors:
        print(err)

# Bloque anti-alucinación (lo que reciben los hunters)
block = facts_anti_hallucination_block(r)
```

## Cuándo NO usar la capa editorial

- Si `contenido` se usa como **librería** dentro de otro pipeline que ya tiene su propia capa editorial (no hagas dos).
- Si la carpeta `editorial/` no existe, los hunters siguen funcionando con el bloque genérico de anti-alucinación. El registry queda vacío sin crash.

## Compatibilidad con el flujo existente

`contenido topic "..."`, `contenido article "..."`, `contenido subject "..."` siguen funcionando igual. La capa editorial es **opt-in via los nuevos comandos** (`plan`, `produce-week`). El cambio en hunters es transparente: si `facts.json` está vacío usan el bloque genérico (= comportamiento anterior).
