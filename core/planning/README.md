# core/planning

Módulos determinísticos (puro Python, sin LLM) portados desde `reels-af/src/reel_af/planning/`.

## Módulos (Fase 2)

- **`beats.py`** — `plan_beats(script, audio_duration_s) → list[Beat]`. Asigna buckets Veo (4/6/8s) por rol: hook≥6s, payoff≤4s, mechanism = bucket más pequeño ≥ target+0.3s.
- **`cards.py`** — `pack_cards(word_timings) → list[Card]`. Layout de subtítulos con 4 condiciones de break (word cap, width, gap, clause punct).
- **`font_metrics.py`** — char widths de Montserrat Bold para `pack_cards`.
- **`safe_zone.py`** — canvas 1080×1920, safe zones para subtítulos + accents (no se pisan).

## Por qué determinístico
- Reproducibilidad: mismo input → mismo output siempre.
- Velocidad: <100ms para reels de 25s.
- Testeable sin mocks de LLM.
- Permite iteración rápida en layout sin coste de tokens.
