"""De una premisa a una serie producida, sin intervención manual (ADR-039).

El hueco que cierra
-------------------
ADR-038 dejó el pipeline capaz de planificar y producir episodios, pero la
`SeriesBible` había que **escribirla a mano**. Eso convertía "cualquiera escribe
lo que quiere y se genera" en "cualquiera que sepa el schema de Pydantic y los
nombres exactos de las voces de Edge". No es lo mismo.

`create_bible()` genera la biblia entera desde una premisa en una frase, y
`run_series()` encadena todo:

    premisa → biblia → [plan → produce] × N episodios

Dos cosas que el LLM NO decide
------------------------------
1. **Las voces.** Un modelo alucina nombres de voces con total confianza — la
   primera biblia escrita a mano llevaba `es-MX-CarlotaNeural`, que no existe, y
   habría reventado el TTS en producción. El casting sale del catálogo real
   (`core.long_form.voices`).
2. **Los retratos.** Los genera `ensure_portraits` a partir de la apariencia
   escrita, y se persisten. El LLM describe; el pipeline dibuja.
"""
from __future__ import annotations

import re
import unicodedata

from loguru import logger
from pydantic import BaseModel, Field

from core.llm_router import complete_structured
from core.long_form.prompts import BIBLE_ARCHITECT_SYSTEM
from core.long_form.types import resolve_provider
from shared.config import load_config
from shared.schemas import AssetProfile, CharacterProfile, SeriesBible, VideoAspect


class _DraftCharacter(BaseModel):
    """Personaje tal como lo escribe el LLM: sin voz ni retrato.

    `gender` existe solo para el casting de voz — no viaja a
    `CharacterProfile`, donde sería un dato sin uso.
    """

    name: str = Field(..., min_length=2, max_length=80)
    gender: str = Field("female", description="female | male")
    appearance: str = Field(..., min_length=20)
    persona: str = Field("", max_length=600)


class _DraftAsset(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    kind: str = Field("prop", description="environment | prop | vehicle | creature | other")
    description: str = Field(..., min_length=15)


class _DraftBible(BaseModel):
    """Lo que el arquitecto devuelve. Se completa con voces y defaults."""

    slug: str = Field(..., min_length=2, max_length=80)
    title: str = Field(..., min_length=3, max_length=200)
    logline: str = Field(..., min_length=20, max_length=400)
    genre: str = Field("melodrama", max_length=60)
    tone: str = Field("", max_length=1200)
    setting: str = Field("", max_length=800)
    characters: list[_DraftCharacter] = Field(..., min_length=1, max_length=8)
    assets: list[_DraftAsset] = Field(default_factory=list, max_length=10)
    rules: list[str] = Field(default_factory=list, max_length=12)


def slugify(text: str) -> str:
    """`"La Herencia de los Vargas"` → `"la-herencia-de-los-vargas"`.

    Sin acentos ni signos: el slug es un nombre de directorio y una clave de
    checkpoint, así que tiene que sobrevivir a un filesystem y a un shell.
    """
    plain = unicodedata.normalize("NFKD", str(text or "").lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    plain = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")
    return (plain or "serie")[:60]


async def create_bible(
    premise: str,
    *,
    slug: str = "",
    episode_seconds: float = 90.0,
    aspect: VideoAspect = VideoAspect.PORTRAIT,
    provider: str | None = None,
) -> SeriesBible:
    """Construye la biblia completa desde una premisa en lenguaje natural.

    El LLM escribe personajes, locaciones, reglas y tono. El pipeline asigna las
    voces desde el catálogo real — nunca el modelo, que las alucina.
    """
    if not premise.strip():
        raise ValueError("create_bible: premisa vacía")

    cfg = load_config().long_form
    resolved = provider or resolve_provider(cfg.chat_model_provider)

    draft: _DraftBible = await complete_structured(
        prompt=f"<PREMISA>\n{premise.strip()}\n</PREMISA>\n\nConstruye la biblia.",
        schema=_DraftBible,
        provider=resolved,
        system=BIBLE_ARCHITECT_SYSTEM,
        temperature=0.9,   # la biblia se escribe una vez: conviene que arriesgue
        max_tokens=6000,
    )

    from core.long_form.voices import cast_voices

    cast = cast_voices([(c.name, c.gender) for c in draft.characters])

    bible = SeriesBible(
        slug=slugify(slug or draft.slug or draft.title),
        title=draft.title,
        logline=draft.logline,
        genre=draft.genre,
        tone=draft.tone,
        setting=draft.setting,
        characters=[
            CharacterProfile(
                name=c.name,
                appearance=c.appearance,
                persona=c.persona,
                voice=cast.get(c.name, ""),
            )
            for c in draft.characters
        ],
        assets=[
            AssetProfile(
                name=a.name,
                kind=a.kind if a.kind in
                {"environment", "prop", "vehicle", "creature", "other"} else "prop",
                description=a.description,
            )
            for a in draft.assets
        ],
        rules=list(draft.rules),
        target_episode_seconds=episode_seconds,
        aspect_ratio=aspect,
    )
    logger.info(
        f"[architect] '{bible.title}' — {len(bible.characters)} personajes, "
        f"{len(bible.assets)} locaciones, {len(bible.rules)} reglas"
    )
    return bible


# =============================================================================
# Harness: premisa → serie producida
# =============================================================================


async def run_series(
    premise: str = "",
    *,
    slug: str = "",
    episodes: int = 1,
    episode_seconds: float = 90.0,
    produce: bool = True,
    direction: str = "",
    on_progress=None,
) -> SeriesBible:
    """Pipeline completo y desatendido: premisa → biblia → N episodios.

    Idempotente por diseño. Si el slug ya tiene biblia, la **reusa** en vez de
    regenerarla: los personajes y sus retratos son el activo de la serie, y
    volver a inventarlos rompería la consistencia que ADR-038 existe para
    proteger. Añadir episodios a una serie viva es el caso normal, no la
    excepción.

    `produce=False` planifica sin gastar en generación visual — el gate barato
    para revisar los guiones antes de pagar.

    `on_progress(fase, detalle)` permite a un CLI o a una API informar sin que
    este módulo sepa nada de terminales.
    """
    from core.long_form.narrative_director import produce_episode
    from core.long_form.series import EpisodePlanner, bible_path, load_bible, save_bible

    def report(phase: str, detail: str = "") -> None:
        if on_progress:
            on_progress(phase, detail)
        logger.info(f"[harness] {phase}: {detail}")

    # --- Biblia: crear o reusar ---
    existing = slug and bible_path(slug).exists()
    if existing:
        bible = load_bible(slug)
        report("biblia", f"reusando '{bible.title}' ({len(bible.episodes)} eps)")
    else:
        if not premise.strip():
            raise ValueError("run_series: hace falta una premisa para una serie nueva")
        report("biblia", "escribiendo desde la premisa…")
        bible = await create_bible(premise, slug=slug, episode_seconds=episode_seconds)
        save_bible(bible)
        report("biblia", f"'{bible.title}' → {bible_path(bible.slug)}")

    planner = EpisodePlanner()

    for i in range(episodes):
        number = bible.next_episode_number()
        report("plan", f"E{number} ({i + 1}/{episodes})")
        episode = await planner.plan(
            bible,
            number=number,
            # La indicación del showrunner aplica solo al primero del lote: los
            # siguientes deben seguir de lo que pasó, no repetir la nota.
            direction=direction if i == 0 else "",
        )
        bible.episodes.append(episode)
        bible.episodes.sort(key=lambda e: e.number)
        save_bible(bible)
        report("plan", f"E{number} '{episode.title}' — corta: {episode.cliffhanger[:60]}")

        if not produce:
            continue

        report("produce", f"E{number}…")
        try:
            produced = await produce_episode(bible, episode)
        except Exception as e:  # noqa: BLE001 — un episodio caído no tumba el lote
            report("error", f"E{number}: {e}")
            continue
        bible.episodes = [x if x.number != produced.number else produced for x in bible.episodes]
        save_bible(bible)
        report("produce", f"E{number} ✓ {produced.video_path}")

    return bible


__all__ = ["create_bible", "run_series", "slugify"]
