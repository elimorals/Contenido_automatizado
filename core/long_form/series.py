"""Series de microdrama: biblia persistente + planificación episódica (ADR-038).

Por qué existe
--------------
`LongFormJob` modela **un video suelto**. Un microdrama es 60-100 episodios de
90 s que comparten personajes, locaciones y continuidad — y esa continuidad no
puede vivir en el prompt de cada episodio: tiene que estar escrita en un sitio y
leerse igual la semana 1 que la semana 20.

`SeriesBible` es ese sitio. Es el equivalente narrativo de
`editorial/brand-voice.md`: se versiona en git, es la fuente de verdad, y un
humano la edita a mano cuando quiere.

Lo episódico NO es lo mismo que lo largo
----------------------------------------
`NarrativeArc` (tres actos) es estructura de película: planteamiento, nudo,
desenlace. Un microdrama es lo contrario — cada unidad **abre** más de lo que
cierra. De ahí `EpisodeBeat`: `cold_open` (el gancho de los primeros 8 s),
`escalation`, `turn`, `cliffhanger`. Un episodio que resuelve su conflicto es un
episodio que no tiene siguiente.

Layout en disco (junto a `editorial/`, versionado):

    editorial/series/<slug>/bible.json     ← la biblia
    editorial/series/<slug>/portraits/     ← retratos fijos por personaje
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from core.llm_router import complete_structured
from core.long_form.prompts import EPISODE_PLANNER_SYSTEM
from core.long_form.types import LongFormPlanError, resolve_provider
from shared.config import load_config
from shared.schemas import CharacterProfile, Episode, SeriesBible

SERIES_DIRNAME = "series"
BIBLE_FILENAME = "bible.json"


# =============================================================================
# Persistencia
# =============================================================================


def series_root(base: Path | str | None = None) -> Path:
    """`editorial/series/` — junto a la capa editorial, no en `storage/`.

    Va versionado a propósito: la biblia es material editorial, igual que
    `brand-voice.md` o `facts.json`. `storage/` es para artefactos regenerables.
    """
    root = Path(base) if base else Path("editorial")
    return root / SERIES_DIRNAME


def bible_path(slug: str, base: Path | str | None = None) -> Path:
    return series_root(base) / slug / BIBLE_FILENAME


def load_bible(slug: str, base: Path | str | None = None) -> SeriesBible:
    path = bible_path(slug, base)
    if not path.exists():
        raise LongFormPlanError(
            f"serie '{slug}' no encontrada en {path}. "
            f"Créala con `contenido series new {slug} ...`"
        )
    return SeriesBible(**json.loads(path.read_text(encoding="utf-8")))


def save_bible(bible: SeriesBible, base: Path | str | None = None) -> Path:
    """Guarda la biblia. Escritura atómica: es la fuente de verdad de la serie."""
    path = bible_path(bible.slug, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(bible.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    tmp.replace(path)
    return path


def list_series(base: Path | str | None = None) -> list[str]:
    root = series_root(base)
    if not root.exists():
        return []
    return sorted(d.name for d in root.iterdir() if (d / BIBLE_FILENAME).exists())


def portraits_dir(slug: str, base: Path | str | None = None) -> Path:
    """Dónde viven los retratos fijos. Son el ancla de consistencia de la serie."""
    d = series_root(base) / slug / "portraits"
    d.mkdir(parents=True, exist_ok=True)
    return d


# =============================================================================
# Planificación episódica
# =============================================================================


class EpisodePlanner:
    """Planifica UN episodio en el contexto de la serie completa."""

    def __init__(self, *, provider: str | None = None) -> None:
        cfg = load_config().long_form
        self.provider = provider or resolve_provider(cfg.chat_model_provider)

    async def plan(
        self,
        bible: SeriesBible,
        *,
        number: int | None = None,
        direction: str = "",
    ) -> Episode:
        """Genera el episodio `number` (o el siguiente).

        `direction` es la nota del showrunner: "que Renata descubra lo de la
        hija", "meter a la abuela en flashback". Es el punto de control humano,
        equivalente al gate `approve` de la capa editorial.
        """
        n = number or bible.next_episode_number()
        prev = bible.story_so_far(before=n)

        system = EPISODE_PLANNER_SYSTEM.format(
            title=bible.title,
            genre=bible.genre,
            tone=bible.tone or "(sin especificar)",
            setting=bible.setting or "(sin especificar)",
            logline=bible.logline,
            seconds=int(bible.target_episode_seconds),
        )
        user = (
            f"<PERSONAJES>\n{_format_cast(bible.characters)}\n</PERSONAJES>\n\n"
            f"<REGLAS_DEL_MUNDO>\n"
            + ("\n".join(f"- {r}" for r in bible.rules) or "(ninguna)")
            + "\n</REGLAS_DEL_MUNDO>\n\n"
            f"<LO_QUE_YA_PASO>\n{prev or '(este es el primer episodio)'}\n"
            f"</LO_QUE_YA_PASO>\n\n"
            f"<INDICACION_DEL_SHOWRUNNER>\n{direction or '(ninguna)'}\n"
            f"</INDICACION_DEL_SHOWRUNNER>\n\n"
            f"Escribe el episodio {n}."
        )

        episode = await complete_structured(
            prompt=user,
            schema=Episode,
            provider=self.provider,
            system=system,
            temperature=0.85,   # melodrama: se premia el exceso, no la prudencia
            max_tokens=3000,
        )
        episode.number = n
        episode.target_seconds = bible.target_episode_seconds
        logger.info(
            f"[series] E{n} '{episode.title}' — corta con: {episode.cliffhanger[:60]}"
        )
        return episode


def _format_cast(characters: list[CharacterProfile]) -> str:
    if not characters:
        return "(sin personajes definidos)"
    return "\n".join(
        f"- {c.name}: {c.appearance}"
        + (f" | {c.persona}" if c.persona else "")
        + (f" | voz: {c.voice}" if c.voice else "")
        for c in characters
    )


__all__ = [
    "BIBLE_FILENAME",
    "EpisodePlanner",
    "bible_path",
    "list_series",
    "load_bible",
    "portraits_dir",
    "save_bible",
    "series_root",
]
