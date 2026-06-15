"""SRT classic subtitle builder — port de MoneyPrinterTurbo/app/services/subtitle.py.

Genera archivos SRT estándar (broadcast / VLC / mpv / Premiere parseable).

Dos entry points:

  • `write_srt(text, audio_path, out_path)`:
        Transcribe el audio con Whisper (fallback) y produce SRT.
        Compatible con el `subtitle.create(audio, srt)` de MPT.

  • `subtitles_from_word_timings(word_timings, out_path)`:
        Convierte una lista `WordTiming[]` (provista por Edge SubMaker u otro
        TTS sample-accurate) a SRT — agrupando palabras en líneas hasta el
        siguiente signo de puntuación o un máximo de palabras.

El formato SRT cumple:

    1
    00:00:00,000 --> 00:00:01,200
    Línea
    [blank]
"""

from __future__ import annotations

from pathlib import Path

from shared.schemas import WordTiming

# Mismo set que MPT/app/models/const.py — incluye CJK y árabe.
_PUNCT_END = {
    "?", ",", ".", "、", ";", ":", "!", "…",
    "？", "，", "。", "；", "：", "！",
    "،", "؛", "؟",
}
# Punctuation that NUNCA debe forzar break aunque sea "puntuación" — separadores
# decimales y miles en números.
_MAX_WORDS_PER_LINE = 14


def _format_ts(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _is_break_punct(word: str) -> bool:
    """Termina una sentencia? (heurística MPT). No rompe en decimales/miles."""
    if not word:
        return False
    last = word[-1]
    if last not in _PUNCT_END:
        return False
    # 2.5% / 1,000 → no romper. Verificamos que no sea separador decimal
    # tipo "2.5" donde la palabra entera es numérico+punto+más-dígitos…
    # En la práctica word boundary del TTS no separa esos casos, así que el
    # check último-char ya es suficiente.
    return True


def _emit_block(
    out: list[str],
    idx: int,
    start_s: float,
    end_s: float,
    text: str,
) -> None:
    text = text.strip()
    if not text:
        return
    out.append(str(idx))
    out.append(f"{_format_ts(start_s)} --> {_format_ts(end_s)}")
    out.append(text)
    out.append("")  # blank line separator


def subtitles_from_word_timings(
    word_timings: list[WordTiming],
    out_path: Path,
    max_words_per_line: int = _MAX_WORDS_PER_LINE,
) -> Path:
    """Convierte WordTiming[] → SRT, agrupando hasta puntuación o N palabras.

    Es lo que MPT llama "Edge SubMaker path": Edge devuelve word boundaries
    sample-accurate; aquí los reagrupamos en líneas legibles.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    idx = 1

    cur_words: list[str] = []
    cur_start: float | None = None
    cur_end: float = 0.0

    for w in word_timings:
        if cur_start is None:
            cur_start = w.start_s
        cur_words.append(w.word)
        cur_end = w.end_s

        ends_sentence = _is_break_punct(w.word)
        too_long = len(cur_words) >= max_words_per_line
        if ends_sentence or too_long:
            text = " ".join(cur_words).strip()
            # Quitar puntuación final SOLO si quedamos con texto coherente
            _emit_block(blocks, idx, cur_start, cur_end, text)
            idx += 1
            cur_words = []
            cur_start = None

    if cur_words and cur_start is not None:
        _emit_block(blocks, idx, cur_start, cur_end, " ".join(cur_words).strip())

    out_path.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    return out_path


def write_srt(
    text: str,
    audio_path: Path,
    out_path: Path,
    model_size: str | None = None,
) -> Path:
    """Genera SRT desde audio con Whisper (fallback de MPT).

    `text` es el script original — se usa para corrección post-Whisper
    (alineación por similitud). Si está vacío saltamos correction.

    NOTA: este path solo se usa cuando NO tenemos WordTiming[] del TTS.
    En el happy path (Edge / Gemini Flash) preferimos
    `subtitles_from_word_timings`.
    """
    from core.subtitles.whisper import transcribe  # lazy

    out_path.parent.mkdir(parents=True, exist_ok=True)
    word_timings = transcribe(audio_path, model_size=model_size)
    if text and word_timings:
        # Best-effort correction — non-fatal.
        try:
            word_timings = correct(word_timings, text)
        except Exception:  # noqa: BLE001 — correction es opcional
            pass
    subtitles_from_word_timings(word_timings, out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# Correction — alinea la transcripción Whisper con el script original.
# Port simplificado de MPT/subtitle.correct() usando difflib (no agregamos
# python-Levenshtein como dependencia).
# ─────────────────────────────────────────────────────────────────────────────


def _similarity(a: str, b: str) -> float:
    """Razón de similitud [0..1] usando difflib (stdlib)."""
    from difflib import SequenceMatcher

    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _split_script_into_lines(script: str) -> list[str]:
    """Particiona el script por puntuación + saltos de línea, igual que MPT."""
    if not script:
        return []
    result: list[str] = []
    buf = ""
    n = len(script)
    for i, ch in enumerate(script):
        prev_ch = script[i - 1] if i > 0 else ""
        next_ch = script[i + 1] if i < n - 1 else ""
        if ch == "\n":
            result.append(buf.strip())
            buf = ""
            continue
        # No romper decimales / miles dentro de números
        if ch in (".", ",") and prev_ch.isdigit() and next_ch.isdigit():
            buf += ch
            continue
        if ch in _PUNCT_END:
            result.append(buf.strip())
            buf = ""
            continue
        buf += ch
    result.append(buf.strip())
    return [r for r in result if r]


def correct(
    word_timings: list[WordTiming],
    script: str,
    min_similarity: float = 0.8,
) -> list[WordTiming]:
    """Alinea Whisper output con el script.

    Estrategia:
      1. Particionar el script en oraciones por puntuación.
      2. Tomar la secuencia de palabras Whisper y ver qué prefijo de palabras
         hace mejor match (por similarity) con cada oración del script.
      3. Reemplazar el texto de esas palabras por la oración del script,
         preservando los timestamps originales (start del primer match,
         end del último).

    Si el script y la transcripción difieren demasiado (< min_similarity),
    devolvemos los word_timings sin tocar (mejor original que invención).
    """
    if not word_timings or not script:
        return word_timings

    lines = _split_script_into_lines(script)
    if not lines:
        return word_timings

    out: list[WordTiming] = []
    wi = 0
    total_words = len(word_timings)

    for line in lines:
        if wi >= total_words:
            break
        # Greedy: empezamos con 1 palabra, crecemos mientras similarity sube.
        best_end = wi + 1
        best_sim = _similarity(line, word_timings[wi].word)
        cap = min(total_words, wi + len(line.split()) * 2 + 4)
        for j in range(wi + 2, cap + 1):
            candidate = " ".join(w.word for w in word_timings[wi:j])
            sim = _similarity(line, candidate)
            if sim >= best_sim:
                best_sim = sim
                best_end = j
            else:
                break

        if best_sim >= min_similarity:
            start_s = word_timings[wi].start_s
            end_s = word_timings[best_end - 1].end_s
            # Re-emit COMO UNA sola "palabra lógica" con el texto del script.
            # Esto rompe la garantía 1-palabra-1-WordTiming, pero solo se usa
            # para downstream SRT (no para word_burst).
            out.append(WordTiming(word=line, start_s=start_s, end_s=end_s))
            wi = best_end
        else:
            # Below threshold: passthrough sin modificar
            out.append(word_timings[wi])
            wi += 1

    # Trailing words que el script no cubrió → passthrough
    while wi < total_words:
        out.append(word_timings[wi])
        wi += 1

    return out


def file_to_subtitles(path: Path) -> list[tuple[int, str, str]]:
    """Parsea un .srt → list[(idx, "HH:MM:SS,mmm --> HH:MM:SS,mmm", text)].

    Compatibilidad inversa con MPT (algunos callers leen el SRT generado).
    """
    if not path.exists():
        return []
    import re

    times_pat = re.compile(r"\d{2}:\d{2}:\d{2},\d{3}")
    out: list[tuple[int, str, str]] = []
    current_times: str | None = None
    current_text = ""
    idx = 0
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        if times_pat.findall(line):
            current_times = line.strip()
        elif line.strip() == "" and current_times:
            idx += 1
            out.append((idx, current_times, current_text.strip()))
            current_times, current_text = None, ""
        elif current_times:
            current_text += line
    # Flush final
    if current_times:
        idx += 1
        out.append((idx, current_times, current_text.strip()))
    return out


__all__ = [
    "correct",
    "file_to_subtitles",
    "subtitles_from_word_timings",
    "write_srt",
]
