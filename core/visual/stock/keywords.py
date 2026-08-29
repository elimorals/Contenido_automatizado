"""Keywords de búsqueda para stock, derivadas del plan visual (ADR-034).

El selector usaba `BeatVisual.visual_anchor` como query de stock. Pero ese campo
**no es un keyword**: es un campo de *procedencia*. El prompt del planner lo
exige verbatim ("MUST be one of the evidence items, copied verbatim") justo para
que se pueda auditar sobre qué evidencia se apoya cada beat — es la pieza
anti-alucinación del sistema. Y una cita académica es lo contrario de lo que
quiere un buscador de stock:

    query = "Dement and Kleitman, 1957: systematic awakenings established that
             dream reports could be obtained from both REM and non-REM sleep."
    → Archive.org, Wikimedia, NASA, LoC: 0 resultados, siempre.

De ahí el `source_diversity: 1` del guard: no es que el stock fuera malo, es que
nunca llegó a buscarse nada buscable, y todos los beats caían a IA.

La solución NO es relajar el anchor —seguiría siendo verbatim, es su trabajo—
sino extraer términos del `image_prompt`, que ya describe una escena concreta
("sleep laboratory at night, EEG machine, electrodes"): exactamente el registro
que un buscador de stock entiende.

Determinista y $0: regex y wordlists, sin una llamada extra al LLM. Mismo
espíritu que `core.quality` (ADR-028) y `core.distribution.captions` (ADR-030).
"""
from __future__ import annotations

import re

from core.quality import strip_accents

#: Máximo de términos por query. Los buscadores de stock degradan rápido con
#: queries largas: cada palabra extra reduce el recall en vez de afinarlo.
MAX_TERMS = 3

# Ruido de cita académica: años, "et al.", numeración, referencias. Es lo que
# domina un `visual_anchor` y lo que jamás debe llegar a un buscador de stock.
_CITATION_NOISE = re.compile(
    r"\b(?:\d{4}|et\s+al\.?|ibid|op\.\s?cit|vol\.?|pp?\.|fig\.?|"
    r"study|studies|research|researchers|estudio|estudios|investigacion)\b",
    re.IGNORECASE,
)

_NON_WORD = re.compile(r"[^a-z0-9\s]+")

#: Palabras sin valor visual. Además de las gramaticales van las abstractas
#: ("evidence", "effect", "concepto"): describen ideas, y un buscador de stock
#: indexa cosas que se ven.
_STOPWORDS_RAW = """
a al algo algunas algunos ante antes aunque cada como con contra cual cuando de
del desde donde dos el ella ellas ellos en entre era eran es esa esas ese eso
esos esta estas este esto estos ha hasta hay la las le les lo los mas mientras
mucho muy no nos o otra otras otro otros para pero poco por porque que quien se
segun sin sobre su sus tambien tanto te tras tu tus un una uno unos y ya
a an and are as at be been but by can could did do does for from had has have
how in into is it its may might must of on or should than that the their them
then there these they this those to was were what when where which while who
will with would you your
concept concepts evidence effect effects example examples fact facts finding
findings idea ideas number percent point points process rate reason result
results system systems thing things time way ways
concepto dato datos ejemplo efecto hecho hechos idea manera numero porcentaje
proceso razon resultado resultados sistema
""".strip()
_STOPWORDS = frozenset(_STOPWORDS_RAW.split())

#: Vocabulario de dirección de fotografía. Describe CÓMO se filma, no QUÉ se ve,
#: así que satura la query sin acotarla: "cinematic" trae de todo.
_STYLE_WORDS = frozenset(
    ["cinematic", "cinematography", "shot", "frame", "framing", "composition", "composed", "centered", "closeup", "close", "macro", "wide", "angle", "aerial", "overhead", "lens", "depth", "field", "bokeh", "lighting", "light", "lit", "backlit", "dramatic", "moody", "dim", "bright", "dark", "contrast", "palette", "tone", "toned", "color", "colour", "colors", "grade", "graded", "film", "grain", "texture", "vertical", "portrait", "landscape", "background", "foreground", "negative", "space", "subject", "camera", "static", "zoom", "pan", "tilt", "push", "pull", "slow", "motion", "photo", "photograph", "photography", "image", "render", "rendered", "style", "styled", "realistic", "photorealistic", "detailed", "high", "quality", "resolution", "4k", "8k", "hd", "plano", "encuadre", "iluminacion", "camara", "camara", "fondo", "primer", "luz", "sombra", "estilo"]
)


def _tokens(text: str) -> list[str]:
    """Palabras normalizadas y sin ruido, preservando el orden del texto.

    El orden importa: se quitan los acentos ANTES de aplicar el filtro de ruido.
    Al revés, `investigación` no casaba con el patrón `investigacion` y el ruido
    acentuado se colaba entero — la misma lección que ya aprendió
    `core.quality.slop`.
    """
    cleaned = _CITATION_NOISE.sub(" ", strip_accents(str(text or "")))
    cleaned = _NON_WORD.sub(" ", cleaned)
    out: list[str] = []
    for word in cleaned.split():
        if len(word) < 3 or word.isdigit():
            continue
        if word in _STOPWORDS or word in _STYLE_WORDS:
            continue
        out.append(word)
    return out


def extract_terms(*sources: str | None, max_terms: int = MAX_TERMS) -> list[str]:
    """Términos de búsqueda a partir de una o varias fuentes de texto.

    El ORDEN de `sources` es la prioridad: el caller pasa primero lo más
    descriptivo (el `image_prompt`) y deja el anchor como último recurso. Se
    preserva el orden de aparición dentro de cada fuente porque los prompts
    visuales nombran el sujeto primero y el ambiente después.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for token in _tokens(source or ""):
            if token not in seen:
                seen.add(token)
                terms.append(token)
            if len(terms) >= max_terms:
                return terms
    return terms


def build_stock_query(
    image_prompt: str = "",
    visual_anchor: str = "",
    fallback: str = "",
    max_terms: int = MAX_TERMS,
) -> str:
    """Query de stock para un beat. `""` si no queda nada buscable.

    Prioridad deliberada:

    1. `image_prompt` — describe una escena concreta; es lo más parecido a
       cómo está etiquetado el material de stock.
    2. `visual_anchor` — la evidencia. Suele ser prosa académica, pero a veces
       contiene la única entidad concreta del beat.
    3. `fallback` — normalmente `beat.text`; último recurso.

    Devolver `""` es un resultado válido y explícito: significa "este beat no
    tiene nada que buscar", y el selector debe ir directo a IA en vez de gastar
    una ronda de peticiones a seis proveedores para recibir cero resultados.
    """
    terms = extract_terms(image_prompt, visual_anchor, fallback, max_terms=max_terms)
    return " ".join(terms)


__all__ = ["MAX_TERMS", "build_stock_query", "extract_terms"]
