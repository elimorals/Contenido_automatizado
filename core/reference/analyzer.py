"""Analizador de video de referencia — lógica pura + orquestación.

Reimplementado (NO copiado) del concepto de OpenMontage (video_analyzer +
scene_detect + transcript_fetcher), bajo Apache-2.0. Ver ADR-017.

Separación clave:
- `build_brief(url, raw)`: lógica PURA y determinista (pacing, hook, sugerencias).
  Testeable offline.
- `analyze_reference(url, fetcher=...)`: orquesta fetch (descarga/transcribe/scene-detect)
  + build_brief. La parte de I/O vive detrás del protocolo `ReferenceFetcher` y se
  inyecta; el default real (yt-dlp + whisper + ffmpeg) se carga perezosamente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from loguru import logger

from shared.schemas import ReferenceBrief, ReferenceSegment

# Duración objetivo de NUESTRO reel — para mapear el pacing de la referencia
# a un número de beats sugerido.
DEFAULT_REEL_TARGET_S: float = 25.0
MIN_BEATS: int = 3
MAX_BEATS: int = 8

_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
}
_LIST_NOUNS = {
    "ways", "things", "reasons", "tips", "steps", "rules", "signs", "mistakes",
    "habits", "lessons", "facts", "secrets", "tricks", "myths",
}
_QUESTION_STARTS = {
    "what", "why", "how", "when", "where", "who", "did", "do", "does", "is", "are",
    "can", "should", "would", "could",
}
_CONTRARIAN_MARKERS = {
    "not", "wrong", "myth", "stop", "never", "actually", "lie", "nobody", "myths",
}


class ReferenceAnalysisError(RuntimeError):
    """Falló la obtención/análisis de la referencia."""


@dataclass
class RawReference:
    """Datos crudos extraídos del video de referencia, antes de derivar métricas."""

    title: str
    duration_s: float
    segments: list[tuple[float, float, str]] = field(default_factory=list)
    shot_cuts: list[float] = field(default_factory=list)  # timestamps de cortes interiores


@runtime_checkable
class ReferenceFetcher(Protocol):
    """Obtiene los datos crudos de una url (descarga + transcript + scene-detect)."""

    async def fetch(self, url: str) -> RawReference:  # pragma: no cover - protocolo
        ...


def _classify_hook(hook_text: str) -> str:
    """Clasifica el estilo del hook por heurística léxica."""
    t = hook_text.strip().lower()
    if not t:
        return "statement"
    words = set(re.findall(r"[a-z]+", t))
    has_digit = bool(re.search(r"\d", t)) or "percent" in words

    if has_digit:
        return "shock_stat"
    if t.endswith("?") or (re.findall(r"[a-z]+", t)[:1] or [""])[0] in _QUESTION_STARTS:
        return "question"
    if (words & _NUMBER_WORDS) and (words & _LIST_NOUNS):
        return "listicle"
    if words & _CONTRARIAN_MARKERS:
        return "contrarian"
    return "statement"


def _suggested_beats(avg_shot_s: float, reel_target_s: float) -> int:
    """Mapea el pacing de la referencia a un nº de beats para nuestro reel."""
    if avg_shot_s <= 0:
        return 0
    raw = round(reel_target_s / avg_shot_s)
    return max(MIN_BEATS, min(MAX_BEATS, raw))


def build_brief(
    url: str,
    raw: RawReference,
    *,
    reel_target_s: float = DEFAULT_REEL_TARGET_S,
) -> ReferenceBrief:
    """Deriva un `ReferenceBrief` (pacing/estructura/hook/sugerencias) de datos crudos.

    Pura y determinista — sin I/O.
    """
    transcript = " ".join(text for _, _, text in raw.segments).strip()
    word_count = len([w for w in transcript.split() if w.strip()])

    wpm = round(word_count / (raw.duration_s / 60.0)) if raw.duration_s > 0 else 0

    shot_count = (len(raw.shot_cuts) + 1) if raw.duration_s > 0 else 0
    avg_shot_s = (raw.duration_s / shot_count) if shot_count > 0 else 0.0

    hook_text = " ".join(transcript.split()[:12])
    hook_style = _classify_hook(hook_text)

    target_wpm = max(120, min(200, wpm)) if wpm > 0 else 0

    return ReferenceBrief(
        url=url,
        title=raw.title,
        duration_s=raw.duration_s,
        transcript=transcript,
        segments=[
            ReferenceSegment(start_s=s, end_s=e, text=txt) for s, e, txt in raw.segments
        ],
        segment_count=len(raw.segments),
        shot_count=shot_count,
        avg_shot_s=avg_shot_s,
        wpm=wpm,
        hook_text=hook_text,
        hook_style=hook_style,
        suggested_beats=_suggested_beats(avg_shot_s, reel_target_s),
        target_wpm=target_wpm,
    )


_HOOK_STYLE_GUIDANCE = {
    "question": "abre con una PREGUNTA que cree curiosidad (no reveles la respuesta)",
    "shock_stat": "abre con un NÚMERO/estadística impactante",
    "contrarian": "abre CONTRADICIENDO la creencia común",
    "listicle": "abre anunciando una lista ('N maneras de…')",
    "statement": "abre con una afirmación tajante y concreta",
}


# Cota dura de ScriptDraft.mechanism_lines (min_length=2, max_length=4).
MECHANISM_MIN: int = 2
MECHANISM_MAX: int = 4


def mechanism_target(brief: ReferenceBrief) -> int:
    """Nº de `mechanism_lines` sugerido por el pacing de la referencia.

    `suggested_beats` cuenta hook + mechanism + payoff, así que el mechanism es
    `suggested_beats - 2`. Se acota a [2,4] para respetar el schema `ScriptDraft`.
    """
    return max(MECHANISM_MIN, min(MECHANISM_MAX, brief.suggested_beats - 2))


def reference_style_hint(brief: ReferenceBrief) -> str:
    """Bloque de guía de estilo (soft) derivado del brief, para el prompt de composición.

    Pensado para INYECTARSE al user-prompt de los reasoners de guion como complemento
    OPCIONAL: orienta energía/pacing/estilo de hook sin tocar la estructura fija ni el
    contenido (que sigue saliendo de la essence/evidence). Ver ADR-017.
    """
    hook_line = _HOOK_STYLE_GUIDANCE.get(brief.hook_style, brief.hook_style or "n/a")
    pace = f"~{brief.target_wpm} wpm" if brief.target_wpm else "ritmo del default"
    beats = brief.suggested_beats or "el default"
    cuts = f"~{brief.avg_shot_s:.1f}s/corte" if brief.avg_shot_s else "n/a"
    return (
        "\n──── STYLE REFERENCE (guía SUAVE — respeta la estructura fija de arriba) ────\n"
        "Se analizó un video de referencia. Imita su ENERGÍA y PACING, NO sus palabras:\n"
        f"  • estilo de hook : {brief.hook_style} → {hook_line}\n"
        f"  • ritmo de voz   : {pace} (apunta cerca; no rellenes)\n"
        f"  • ritmo visual   : ~{beats} beats distintos ({cuts})\n"
        "Usa esto SOLO para ajustar velocidad de entrega y sabor del hook. NO copies su "
        "contenido; no inventes nada fuera de la essence/evidence.\n"
    )


async def analyze_reference(
    url: str,
    fetcher: ReferenceFetcher | None = None,
    *,
    reel_target_s: float = DEFAULT_REEL_TARGET_S,
) -> ReferenceBrief:
    """Analiza un video de referencia y devuelve su brief.

    Args:
        url: enlace al video (TikTok/Reel/YouTube/etc.).
        fetcher: estrategia de obtención. Si None, usa el default real
            (yt-dlp + whisper + ffmpeg), cargado perezosamente.
        reel_target_s: duración objetivo de nuestro reel (para `suggested_beats`).

    Raises:
        ReferenceAnalysisError: si la obtención o el análisis falla.
    """
    if fetcher is None:
        from core.reference.fetcher import YtDlpFetcher

        fetcher = YtDlpFetcher()

    try:
        raw = await fetcher.fetch(url)
    except Exception as e:  # noqa: BLE001 - se re-empaqueta con contexto
        raise ReferenceAnalysisError(f"No se pudo obtener la referencia '{url}': {e}") from e

    logger.info(
        f"[reference] {url}: {raw.duration_s:.1f}s, {len(raw.segments)} segmentos, "
        f"{len(raw.shot_cuts)} cortes"
    )
    return build_brief(url, raw, reel_target_s=reel_target_s)


__all__ = [
    "DEFAULT_REEL_TARGET_S",
    "RawReference",
    "ReferenceAnalysisError",
    "ReferenceFetcher",
    "analyze_reference",
    "build_brief",
    "mechanism_target",
    "reference_style_hint",
]
