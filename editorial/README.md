# editorial/

Capa **editorial** de `contenido`. Aquí vive la **fuente de verdad** del programa de contenido como código:

- `brand-voice.md` — tono, estilo, audiencias, frases ancla. Versionado en git.
- `facts.json` — hechos verificables (números, nombres, fechas). Anti-alucinación de los hunters.
- `pillars/` — 5 (o más) pilares temáticos. Una idea por reel debe pertenecer a uno.
- `audiences.json` — perfiles de a quién le hablas (persona, KPIs, registro).
- `platforms.json` — overrides de specs por plataforma (caption, hashtags, duración, ratio).
- `local-events.json` — eventos del calendario que disparan ideas (opcional).
- `plans/` — planes semanales generados (gitignored por default).

## Filosofía

Inspirada en `corredor-content`: **"una idea mala genera 28 piezas a la basura"**.

```
plan      →   tú revisas  →   produce   →   inspecciona  →   publica
(LLM)         (humano)         (DAG completo)  (output/)        (Upload-Post)
```

El gate humano entre `plan` y `produce` es a propósito. Si nadie aprueba ideas, el costo es $0.

## Por qué versionar todo esto

- **Brand voice como código**: si quieres cambiar el tono, editas el `.md` y cualquier corrida futura lo respeta. No re-explicas el tono a ChatGPT cada vez.
- **Facts.json**: si decimos "98% asistencia" es porque está en `facts.json`. Los hunters están instruidos para NUNCA inventar números/nombres/años; solo pueden citar lo que está aquí.
- **Pillars**: rotación obligatoria (no más de 2 ideas del mismo pilar por semana). Sin esto, el LLM converge a los mismos 3 ángulos.

## Uso rápido

```bash
# 1. Genera plan editorial (default 7 ideas)
uv run contenido plan --ideas 7

# 2. Edita out/plans/plan-2026-W24.json y marca "approved": true en lo que quieras producir

# 3. Produce reels para todas las ideas aprobadas
uv run contenido produce-week
```

## Estructura por defecto

Esta carpeta viene con **plantillas genéricas**. Reemplázalas con tu marca real.

Si quieres un ejemplo concreto (Ruteo / corredor-content), copia desde:
`/Users/elias/Documents/Trabajo/corredor-content/{src/prompts/, data/}`
