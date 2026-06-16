"""SceneExtractor + StoryboardArtist — arc → scenes → shots.

SceneExtractor: 3-act arc + character list → lista de N scenes.
StoryboardArtist: scene → lista de M shots con cinematic language.

LLM backend: `core.llm_router.complete_structured`.

Equivalencia conceptual con ViMax:
- agents/scene_extractor.py → este SceneExtractor
- agents/storyboard_artist.py → este StoryboardArtist
"""
from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from core.llm_router import complete_structured
from core.long_form.prompts import (
    SCENE_EXTRACTOR_SYSTEM,
    STORYBOARD_ARTIST_SYSTEM,
    VISUAL_DECOMPOSE_SYSTEM,
)
from shared.config import load_config
from shared.schemas import (
    CharacterProfile,
    NarrativeArc,
    Scene,
    Shot,
)


class _ScenesResponse(BaseModel):
    scenes: list[Scene] = Field(..., min_length=1, max_length=50)


class _ShotsResponse(BaseModel):
    shots: list[Shot] = Field(..., min_length=1, max_length=30)


class _VisualDecomposeResponse(BaseModel):
    first_frame_desc: str = Field(..., min_length=10)
    last_frame_desc: str = Field(..., min_length=10)
    motion_desc: str = Field(..., min_length=5)


def _format_characters(chars: list[CharacterProfile]) -> str:
    if not chars:
        return "(no characters declared)"
    return "\n".join(
        f"- {c.name}: {c.appearance}" + (f" — persona: {c.persona}" if c.persona else "")
        for c in chars
    )


def _arc_to_text(arc: NarrativeArc) -> str:
    return (
        f"Title: {arc.title}\n"
        f"Logline: {arc.logline}\n\n"
        f"Act 1 — Setup:\n{arc.act1_setup}\n\n"
        f"Act 2 — Confrontation:\n{arc.act2_confrontation}\n\n"
        f"Act 3 — Resolution:\n{arc.act3_resolution}\n\n"
        f"Themes: {', '.join(arc.themes)}"
    )


def _scene_to_text(scene: Scene) -> str:
    return (
        f"Scene #{scene.idx}: {scene.title}\n"
        f"Setting: {scene.setting}\n"
        f"Summary: {scene.summary}\n"
        f"Characters: {', '.join(scene.characters_in_scene) or '(none)'}\n"
        f"Continuation from prev: {scene.continuation_from_prev}"
    )


class SceneExtractor:
    """Descompone un NarrativeArc en lista de Scene."""

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or cfg.chat_model_provider

    async def extract(
        self,
        arc: NarrativeArc,
        characters: list[CharacterProfile],
        *,
        target_scenes: int = 6,
        max_scenes: int = 12,
    ) -> list[Scene]:
        system = SCENE_EXTRACTOR_SYSTEM.format(
            target_minutes=arc.target_minutes,
            target_scenes=target_scenes,
            max_scenes=max_scenes,
        )
        user = (
            f"<SCRIPT>\n{_arc_to_text(arc)}\n</SCRIPT>\n\n"
            f"<CHARACTERS>\n{_format_characters(characters)}\n</CHARACTERS>"
        )
        result = await complete_structured(
            prompt=user,
            schema=_ScenesResponse,
            provider=self.provider,
            system=system,
            temperature=0.6,
            max_tokens=4000,
        )
        # Asegurar idx consecutivo
        for i, sc in enumerate(result.scenes):
            sc.idx = i
        logger.info(
            f"[long_form.scenes] arc '{arc.title}' → {len(result.scenes)} scenes"
        )
        return result.scenes


class StoryboardArtist:
    """Convierte una Scene en lista de Shot con cinematic language."""

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or cfg.chat_model_provider

    async def draw_scene(
        self,
        scene: Scene,
        characters: list[CharacterProfile],
        *,
        user_requirement: str = "",
        min_shots: int = 4,
        max_shots: int = 12,
    ) -> list[Shot]:
        system = STORYBOARD_ARTIST_SYSTEM.format(
            min_shots=min_shots,
            max_shots=max_shots,
        )
        user_req_str = user_requirement or "(none)"
        user = (
            f"<SCRIPT>\n{_scene_to_text(scene)}\n</SCRIPT>\n\n"
            f"<CHARACTERS>\n{_format_characters(characters)}\n</CHARACTERS>\n\n"
            f"<USER_REQUIREMENT>\n{user_req_str}\n</USER_REQUIREMENT>"
        )
        result = await complete_structured(
            prompt=user,
            schema=_ShotsResponse,
            provider=self.provider,
            system=system,
            temperature=0.6,
            max_tokens=4000,
        )
        # Set scene_idx + secuencia
        for i, sh in enumerate(result.shots):
            sh.idx = i
            sh.scene_idx = scene.idx
        logger.info(
            f"[long_form.scenes] scene #{scene.idx} '{scene.title}' → {len(result.shots)} shots"
        )
        return result.shots

    async def decompose_visual(
        self,
        shot: Shot,
        characters: list[CharacterProfile],
    ) -> Shot:
        """Llena first_frame_desc / last_frame_desc / motion_desc del shot.

        Útil cuando vas a generar el shot vía i2v (first frame + motion prompt).
        """
        user = (
            f"<VISUAL_DESC>\n{shot.visual_description}\n</VISUAL_DESC>\n\n"
            f"<CHARACTERS>\n{_format_characters(characters)}\n</CHARACTERS>"
        )
        result = await complete_structured(
            prompt=user,
            schema=_VisualDecomposeResponse,
            provider=self.provider,
            system=VISUAL_DECOMPOSE_SYSTEM,
            temperature=0.4,
            max_tokens=1500,
        )
        return shot.model_copy(update={
            "first_frame_desc": result.first_frame_desc,
            "last_frame_desc": result.last_frame_desc,
            "motion_desc": result.motion_desc,
        })
