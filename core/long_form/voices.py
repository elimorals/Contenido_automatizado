"""Casting automático de voces TTS por personaje (ADR-039).

Un microdrama mexicano con cuatro personajes necesita cuatro voces distintas, y
el operador no tiene por qué saberse el catálogo de Edge. Este módulo reparte
voces reales por género, con un orden de preferencia geográfico.

Por qué el orden importa
------------------------
Edge tiene exactamente **dos** voces `es-MX` (Dalia ♀, Jorge ♂). Una serie con
más de dos personajes hablando obliga a salir de México, y ahí la elección no es
neutra: el acento peninsular (`es-ES`) rompe la ilusión de un melodrama
chilango de inmediato, mientras que un acento colombiano o argentino pasa como
"alguien de fuera" sin sacar al espectador. Por eso `es-ES` va al final del
orden, nunca antes que un latinoamericano.

El catálogo se consulta en vivo y se cachea: escribir a mano nombres de voces
fue exactamente el bug que tuvo la primera biblia — `es-MX-CarlotaNeural` no
existe y el TTS habría fallado en producción.
"""
from __future__ import annotations

from functools import lru_cache

from loguru import logger

#: Orden de preferencia para castellano latino. México primero; España al final
#: porque el acento peninsular desentona en un melodrama mexicano.
LATAM_PRIORITY: tuple[str, ...] = (
    "es-MX", "es-CO", "es-AR", "es-CL", "es-PE", "es-VE",
    "es-EC", "es-GT", "es-CR", "es-UY", "es-BO", "es-DO",
    "es-PY", "es-SV", "es-HN", "es-NI", "es-PA", "es-PR",
    "es-CU", "es-US", "es-ES",
)

#: Fallback si el catálogo no se puede consultar. Son las dos voces que
#: siempre existen en Edge para México.
_FALLBACK: dict[str, list[str]] = {
    "female": ["es-MX-DaliaNeural"],
    "male": ["es-MX-JorgeNeural"],
}


def _fetch_voices() -> list[dict]:
    """Catálogo de Edge, funcione o no dentro de un event loop.

    `asyncio.run()` lanza `RuntimeError` si ya hay un loop corriendo — y
    `cast_voices` se llama justo desde ahí, dentro de `create_bible`. El
    síntoma era silencioso y caro: el fallback de una sola voz por género hacía
    que todos los personajes del mismo sexo sonaran igual, que es exactamente
    lo que este módulo existe para evitar. Cuando hay loop, la consulta va a un
    thread aparte con su propio loop.
    """
    import asyncio

    import edge_tts

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(edge_tts.list_voices())  # sin loop: camino directo

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(edge_tts.list_voices())).result(timeout=30)


@lru_cache(maxsize=4)
def _catalog(prefix: str = "es-") -> dict[str, list[str]]:
    """`{"female": [...], "male": [...]}` ordenado por `LATAM_PRIORITY`.

    Cacheado: consultar el catálogo es una llamada de red y el resultado no
    cambia dentro de una corrida.
    """
    try:
        voices = _fetch_voices()
    except Exception as e:  # noqa: BLE001 — sin red seguimos con el fallback
        logger.warning(f"[voices] catálogo no disponible ({e}); uso voces por defecto")
        return {k: list(v) for k, v in _FALLBACK.items()}

    def rank(locale: str) -> int:
        return LATAM_PRIORITY.index(locale) if locale in LATAM_PRIORITY else len(LATAM_PRIORITY)

    out: dict[str, list[str]] = {"female": [], "male": []}
    for voice in sorted(
        (v for v in voices if v.get("Locale", "").startswith(prefix)),
        key=lambda v: rank(v.get("Locale", "")),
    ):
        gender = str(voice.get("Gender", "")).lower()
        if gender in out:
            out[gender].append(voice["ShortName"])

    for gender, names in out.items():
        if not names:
            out[gender] = list(_FALLBACK[gender])
    return out


def available_voices(gender: str = "female", prefix: str = "es-") -> list[str]:
    """Voces disponibles para un género, mejor primero."""
    return list(_catalog(prefix).get(gender.lower().strip(), []))


def canonical(short_name: str, gender: str) -> str:
    """`es-MX-DaliaNeural` → `es-MX-DaliaNeural-Female`.

    El sufijo de género es lo que `detect_engine_and_voice` espera; sin él, la
    voz se resuelve igual pero el formato queda inconsistente con el resto del
    repo.
    """
    suffix = "Female" if gender.lower().startswith("f") else "Male"
    return f"{short_name}-{suffix}"


def cast_voices(
    people: list[tuple[str, str]],
    *,
    prefix: str = "es-",
) -> dict[str, str]:
    """Asigna una voz distinta a cada personaje. `[(nombre, género)] → {nombre: voz}`.

    Reglas:

    - **Sin repetir mientras haya voces libres.** Dos personajes con la misma
      voz en la misma escena es exactamente lo que hace que un melodrama suene
      a audiolibro.
    - Cuando se agotan, se reciclan por orden — con aviso, porque a partir de
      ahí la serie tiene un problema real de casting que el operador debe saber.
    - Género desconocido cae a `female`: es arbitrario, pero es preferible una
      voz asignada a una vacía que rompa el TTS en producción.
    """
    pools = {g: available_voices(g, prefix) for g in ("female", "male")}
    used: dict[str, int] = {"female": 0, "male": 0}
    out: dict[str, str] = {}

    for name, raw_gender in people:
        gender = "male" if str(raw_gender).lower().startswith("m") else "female"
        pool = pools[gender] or _FALLBACK[gender]
        idx = used[gender]
        if idx >= len(pool):
            logger.warning(
                f"[voices] se agotaron las voces '{gender}' ({len(pool)}); "
                f"'{name}' reutiliza una — revisa el casting"
            )
        out[name] = canonical(pool[idx % len(pool)], gender)
        used[gender] += 1

    return out


__all__ = ["LATAM_PRIORITY", "available_voices", "canonical", "cast_voices"]
