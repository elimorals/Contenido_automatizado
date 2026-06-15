"""DAG de 18 reasoners narrativos (portados de reels-af).

Reasoners disponibles:
  - extract_essence       — URL → Essence (article path)
  - compose_script        — Essence → ScriptDraft (article path)
  - hunt_specific_figure  — Topic → 3 candidates (figura específica)
  - hunt_reversal         — Topic → 3 candidates (reversión)
  - hunt_temporal         — Topic → 3 candidates (temporal)
  - hunt_cross_domain     — Topic → 3 candidates (cross-domain)
  - pick_top_essences     — 12 candidates → top 3 (critic)
  - write_narrations      — 3 essences → 3 ConversationalScripts (paralelo)
  - pick_best_narration   — pairwise judge → 1 winner
  - plan_beat_visuals     — beats → list[BeatVisual] (per-beat image prompts)
  - plan_beat_accents     — beats → list[AccentOverlay|None] (overlays editoriales)

Runtime: usa `core.narrative.runtime.app` (AgentField adapter) que despacha a
`core.llm_router` cuando AgentField no está cableado.
"""
from __future__ import annotations

from core.narrative.accent import accent_for_beat, plan_beat_accents
from core.narrative.compose import compose_script
from core.narrative.critic import pick_top_essences
from core.narrative.extract import extract_essence
from core.narrative.hunters import (
    hunt_cross_domain,
    hunt_reversal,
    hunt_specific_figure,
    hunt_temporal,
)
from core.narrative.judge import pick_best_narration
from core.narrative.narrator import write_narrations
from core.narrative.runtime import app, reel
from core.narrative.visual import plan_beat_visuals

__all__ = [
    "accent_for_beat",
    "app",
    "compose_script",
    "extract_essence",
    "hunt_cross_domain",
    "hunt_reversal",
    "hunt_specific_figure",
    "hunt_temporal",
    "pick_best_narration",
    "pick_top_essences",
    "plan_beat_accents",
    "plan_beat_visuals",
    "reel",
    "write_narrations",
]
