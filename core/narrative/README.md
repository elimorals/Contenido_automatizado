# core/narrative

DAG de 18 reasoners portados desde `reels-af/src/reel_af/agents/`.

## Reasoners (Fase 2)

| Archivo | Reasoners | Función | Trigger |
|---|---|---|---|
| `extract.py` | `extract_essence` | URL → Essence (single harness call) | article path |
| `compose.py` | `compose_script` | Essence → ScriptDraft (Hook→Mech→Payoff) | article path |
| `hunters.py` | `hunt_specific_figure`, `hunt_reversal`, `hunt_temporal`, `hunt_cross_domain` | Topic → 12 candidates (paralelo) | topic path |
| `critic.py` | `pick_top_essences` | 12 → top 3 con angle diversity | topic path |
| `narrator.py` | `write_narrations` | 3 essences → 3 delayed-reveal scripts | topic path |
| `judge.py` | `pick_best_narration` | 3 scripts → 1 winner (pairwise) | topic path |
| `visual.py` | `plan_beat_visuals` | beats → image_prompts (grounded en evidence) | shared |
| `accent.py` | `plan_beat_accents` | beats → editorial overlays (6 patrones) | shared |

## Decisiones de portado

1. **AgentField como bus**: cada reasoner se registra con `@reel.reasoner()` (mantenemos namespace original).
2. **Pydantic schemas**: en `shared/schemas.py` (no duplicar models).
3. **Temperatures**: hunters=1.1 (diversity), critic=0.5 (consistency), default=0.7.
4. **Anti-clichés**: mantener lista explícita de `hunters.py` (líneas 26-40 del original).
5. **Loop-back validator**: ya integrado en `ScriptDraft` (ver `shared/schemas.py`).

## Adaptación a multi-LLM

reels-af original solo usaba OpenRouter. Aquí cada reasoner puede usar cualquier provider de `core/llm_router/` (DeepSeek para narrative, Gemini Flash para visual prompts, etc.). El cambio se hace por config, no por código.
