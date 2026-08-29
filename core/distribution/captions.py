"""Captions y hashtags POR PLATAFORMA, derivados de `PlatformSpec` (ADR-030).

Antes, el pipeline mandaba `script_draft.hook` idéntico a todas las plataformas
y cero hashtags — mientras `editorial/platforms.json` declaraba, sin que nadie
lo leyera, cuántos caracteres y cuántos hashtags quiere cada una. Un caption de
TikTok publicado tal cual en LinkedIn es contenido muerto.

Todo aquí es **determinista y $0**: los hashtags salen del material que el
pipeline ya produjo (pilar editorial, dominio de la `Essence`, search terms del
stock), no de una llamada extra al LLM. Mismo espíritu que `core.quality`.

Salida: un `PlatformPost` por plataforma, con `title` y `description` separados
porque Upload-Post los acepta por separado (`youtube_title` es *obligatorio*
para publicar en YouTube, y `youtube_description` cae al title si falta).
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from loguru import logger

from core.distribution.schemas import to_provider
from core.quality import strip_accents
from shared.schemas import DistributionPlatform, PlatformSpec

# Límite de título de YouTube; el más restrictivo de los que aceptan `*_title`.
TITLE_MAX_CHARS = 100

# Palabras que no aportan nada como hashtag.
_STOPWORDS_RAW = """
    a al algo alguna algunas alguno algunos ante antes como con contra cual cuando de del
    desde donde dos el ella ellas ellos en entre era eran es esa esas ese eso esos esta
    estas este esto estos ha hasta hay la las le les lo los mas más me mi mis mucho muy no
    nos o os otra otras otro otros para pero poco por porque que quien se sin sobre su sus
    también tanto te tu tus un una uno unos y ya
    a an and are as at be but by for from has have how in into is it its of on or that the
    their they this to was were what when where which who will with you your
"""
_STOPWORDS = frozenset(_STOPWORDS_RAW.split())

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


# =============================================================================
# Hashtags
# =============================================================================


def normalize_hashtag(raw: str) -> str | None:
    """`"El efecto placebo"` → `"efectoplacebo"`. `None` si no queda nada útil.

    Quita acentos (un hashtag con tilde se fragmenta distinto según plataforma),
    colapsa a alfanumérico y descarta stopwords y tokens de una sola letra.
    """
    text = strip_accents(str(raw or "")).strip().lstrip("#")
    if not text:
        return None

    words = [w for w in _NON_ALNUM.split(text) if w]
    words = [w for w in words if w not in _STOPWORDS]
    tag = "".join(words)
    if len(tag) < 2 or tag.isdigit():
        return None
    # Un hashtag larguísimo no lo lee nadie y algunas plataformas lo cortan.
    return tag[:30]


def build_hashtag_pool(*sources: Iterable[str] | str | None) -> list[str]:
    """Pool ordenado y sin duplicados a partir de cualquier mezcla de fuentes.

    El ORDEN importa: lo primero que se pasa es lo primero que se usa cuando la
    plataforma solo acepta 3 hashtags. Los callers pasan de más específico
    (pilar, dominio) a más genérico (search terms).
    """
    pool: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if source is None:
            continue
        items = [source] if isinstance(source, str) else list(source)
        for item in items:
            tag = normalize_hashtag(item)
            if tag and tag not in seen:
                seen.add(tag)
                pool.append(tag)
    return pool


# =============================================================================
# Truncado
# =============================================================================


def truncate_words(text: str, limit: int) -> str:
    """Corta en el último espacio antes de `limit` y añade `…`.

    Cortar a media palabra se ve a un metro de distancia; es la diferencia entre
    "un caption largo" y "un caption roto".
    """
    text = " ".join(str(text or "").split())
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]

    head = text[: limit - 1]
    cut = head.rfind(" ")
    # Solo respetamos el corte por palabra si no nos deja con un muñón.
    if cut > limit * 0.6:
        head = head[:cut]
    return head.rstrip(" ,;:.—-") + "…"


# =============================================================================
# PlatformPost
# =============================================================================


@dataclass(frozen=True)
class PlatformPost:
    """Lo que se publica en UNA plataforma."""

    platform: DistributionPlatform
    provider: str
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)

    @property
    def hashtag_block(self) -> str:
        return " ".join(f"#{t}" for t in self.hashtags)

    @property
    def caption(self) -> str:
        """Description + bloque de hashtags, tal como se publica."""
        parts = [p for p in (self.description.strip(), self.hashtag_block) if p]
        return "\n\n".join(parts)


def build_post(
    spec: PlatformSpec,
    *,
    hook: str,
    body: str = "",
    hashtag_pool: Sequence[str] = (),
) -> PlatformPost:
    """Compone el post de UNA plataforma respetando su `PlatformSpec`.

    - `title`: el hook, recortado al menor de (límite de la plataforma, 100).
    - `description`: hook + body, con presupuesto = extremo alto del rango
      *recomendado* (no el máximo: 63 206 chars es lo que Facebook *permite*,
      no lo que conviene), menos lo que ocupen los hashtags.
    - `hashtags`: hasta `hashtags_max` del pool. Si el pool no llega a
      `hashtags_min` se usa lo que haya — inventar hashtags de relleno es peor
      que publicar con menos.
    """
    hook = " ".join(str(hook or "").split())
    body = " ".join(str(body or "").split())

    tags = list(hashtag_pool[: spec.hashtags_max]) if spec.hashtags_max else []
    if len(tags) < spec.hashtags_min:
        logger.debug(
            f"[captions] {spec.id.value}: pool de {len(tags)} hashtags < mínimo "
            f"{spec.hashtags_min}; publico con los que hay"
        )
    tag_len = len(" ".join(f"#{t}" for t in tags))

    recommended_hi = spec.caption_recommended_chars[1]
    budget = min(spec.caption_max_chars, recommended_hi)
    # Los hashtags van dentro del caption, así que compiten por el presupuesto.
    budget -= tag_len + 2 if tag_len else 0

    full = f"{hook} {body}".strip() if body else hook
    description = truncate_words(full, max(budget, 0))

    return PlatformPost(
        platform=spec.id,
        provider=to_provider(spec.id),
        title=truncate_words(hook, min(spec.caption_max_chars, TITLE_MAX_CHARS)),
        description=description,
        hashtags=tags,
    )


def build_posts(
    platforms: Sequence[DistributionPlatform],
    specs: dict[DistributionPlatform, PlatformSpec],
    *,
    hook: str,
    body: str = "",
    hashtag_pool: Sequence[str] = (),
) -> list[PlatformPost]:
    """`build_post` para varias plataformas, saltando las que no tienen spec.

    Una plataforma sin spec en `editorial/platforms.json` se omite con warning
    en vez de publicarse con defaults inventados: si no está declarada, nadie
    decidió cómo debe leerse ahí.
    """
    posts: list[PlatformPost] = []
    for platform in platforms:
        spec = specs.get(platform)
        if spec is None:
            logger.warning(
                f"[captions] {platform.value} sin PlatformSpec en editorial/"
                f"platforms.json; se omite"
            )
            continue
        posts.append(
            build_post(spec, hook=hook, body=body, hashtag_pool=hashtag_pool)
        )
    return posts


__all__ = [
    "TITLE_MAX_CHARS",
    "PlatformPost",
    "build_hashtag_pool",
    "build_post",
    "build_posts",
    "normalize_hashtag",
    "truncate_words",
]
