"""Sample-accurate timing universal — el upgrade clave del módulo TTS.

Portado de `agentfield/reels-af/src/reel_af/render/tts.py` y generalizado
para que CUALQUIER engine (Edge, Gemini, Azure, MiMo, SiliconFlow, Silent)
obtenga word_timings sample-accurate.

Idea central
============
Los engines TTS conocen cuándo dicen cada palabra pero rara vez exponen
boundaries fiables — Edge SubMaker drift varios ms por minuto, Gemini y
SiliconFlow no devuelven nada, MiMo igual. Recuperar timings vía ASR
introduce 200–500 ms de error y a veces alucina.

Solución universal:
  1. Cortar el script en frases (preservando tags inline pegados).
  2. Sintetizar cada frase en paralelo (asyncio.gather) → WAV per-frase.
  3. Medir cada WAV con ffprobe → duración exacta a la muestra.
  4. (Opcional) atempo=N por frase para comprimir/extender — preserva pitch.
  5. Concat nativo de WAVs (mismo PCM params, sin ffmpeg) → ``full.wav``.
  6. Distribuir palabras dentro de cada frase por syllable count sobre el
     span medido. Boundary entre frases: sample-accurate.

Resultado: sentence boundaries exactos, ±50 ms intra-frase (invisible a
ritmo karaoke). Funciona con cualquier engine que produzca WAV PCM 16-bit
mono — los engines que producen MP3 deben transcodificar antes.
"""
from __future__ import annotations

import asyncio
import io
import re
import subprocess
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path

from shared.schemas import WordTiming

# ─── Constantes públicas ──────────────────────────────────────────────

#: Sample rate canónico. Gemini Flash usa 24kHz; el resto debe transcoder
#: a este rate antes de llegar al concat nativo. Edge devuelve MP3 que se
#: convierte a este rate al hacer atempo.
DEFAULT_SAMPLE_RATE = 24_000

#: Factor atempo default cuando el caller especifica `target_duration_s`
#: pero la duración natural ya está dentro de ±5% del objetivo.
_ATEMPO_TOLERANCE = 0.05

# ─── Regex de split y normalización ──────────────────────────────────

_TAG_RE = re.compile(r"\[[^\]]*\]")
_SENTENCE_END = re.compile(r"[.!?]+(?:[\"'’”])?\s*$")


# =====================================================================
# Sentence splitting
# =====================================================================


def split_into_sentences(text: str) -> list[str]:
    """Corta ``text`` en frases que PRESERVAN los tags inline.

    Los tags `[curious]`, `[emphasis]`, etc. son stage directions para
    engines como Gemini Flash. Deben viajar pegados a la frase que
    preceden, no quedarse huérfanos en un split por puntuación.

    Reglas:
      - Solo splittea por ``.``, ``!``, ``?`` (incluyendo combos `?!`).
      - Em-dashes, comas, dos-puntos NO splittean.
      - Comillas de cierre tras la puntuación se mantienen en la frase
        previa (``She said "hi." Then left.`` → 2 frases).

    Returns:
        Lista de frases con tags preservados. Lista vacía si el texto
        sólo contenía whitespace o tags sin spoken words.
    """
    sentences: list[str] = []
    current: list[str] = []
    for tok in text.split():
        current.append(tok)
        if _SENTENCE_END.search(tok):
            sentences.append(" ".join(current))
            current = []
    if current:
        sentences.append(" ".join(current))
    return sentences


def strip_tts_tags(text: str) -> str:
    """Elimina tags ``[...]`` y colapsa whitespace.

    Útil cuando un engine no soporta tags inline (Edge, Azure, MiMo,
    SiliconFlow). El audio sintetizado NO contiene el texto del tag, así
    que el word_timing tampoco debe incluirlo.
    """
    stripped = _TAG_RE.sub(" ", text)
    return " ".join(stripped.split())


# =====================================================================
# Medición y manipulación de audio (ffprobe / ffmpeg)
# =====================================================================


def measure_audio_duration(path: Path) -> float:
    """Devuelve la duración exacta del archivo en segundos vía ffprobe.

    Se usa ffprobe (no wave.open) para soportar también MP3/AAC y para
    obtener la duración real del container, no la suma de frames PCM.
    """
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


async def measure_audio_duration_async(path: Path) -> float:
    """Versión async de ``measure_audio_duration`` (no bloquea event loop)."""
    return await asyncio.to_thread(measure_audio_duration, path)


async def apply_atempo(
    in_path: Path,
    out_path: Path,
    factor: float,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> None:
    """Aplica `atempo=factor` con ffmpeg, preservando pitch.

    Si ``factor`` está dentro de ±_ATEMPO_TOLERANCE (5%) de 1.0, copia el
    archivo tal cual (atempo a 1.0 introduce reencode innecesario).

    El output siempre se normaliza a ``pcm_s16le`` mono al sample_rate
    indicado, para que un concat nativo posterior funcione (los WAV deben
    compartir nchannels/sampwidth/framerate).

    `atempo` válido en ffmpeg solo entre 0.5 y 100.0. Para factores
    fuera del rango [0.5, 2.0] se cascadean filtros: `atempo=2,atempo=N`.
    """
    if abs(factor - 1.0) < _ATEMPO_TOLERANCE:
        # Aún transcodificamos para garantizar PCM canónico (necesario para
        # concat nativo). Pero sin filtro atempo.
        filter_arg = "anull"
    else:
        filter_arg = _build_atempo_filter(factor)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(in_path),
        "-filter:a", filter_arg,
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"apply_atempo: ffmpeg failed ({proc.returncode}): "
            f"{stderr.decode(errors='replace')[-400:]}"
        )


def _build_atempo_filter(factor: float) -> str:
    """Construye una cadena de filtros atempo válida para ffmpeg.

    ffmpeg restringe ``atempo`` a [0.5, 100.0]. Para factor < 0.5
    cascadeamos `atempo=0.5,atempo=...`; para > 2.0 igual con 2.0.
    """
    if factor <= 0:
        raise ValueError(f"atempo factor debe ser positivo, got {factor}")

    filters: list[str] = []
    remaining = factor
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def concat_wavs(paths: list[Path], out_path: Path) -> None:
    """Concatena WAVs reescribiendo PCM raw a un único container.

    Funciona cuando todos los inputs comparten params (nchannels,
    sampwidth, framerate). Evita ffmpeg para evitar reencode y posibles
    desfases por blank frames. Si los params difieren, lanza
    RuntimeError — el caller debe normalizar antes con ``apply_atempo``.
    """
    if not paths:
        raise ValueError("concat_wavs: lista de inputs vacía")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()

    with wave.open(str(out_path), "wb") as out_wav:
        out_wav.setparams(params)
        for wav_path in paths:
            with wave.open(str(wav_path), "rb") as in_wav:
                if in_wav.getparams()[:3] != params[:3]:
                    raise RuntimeError(
                        f"concat_wavs: PCM param mismatch en {wav_path.name} "
                        f"({in_wav.getparams()[:3]} vs {params[:3]})"
                    )
                out_wav.writeframes(in_wav.readframes(in_wav.getnframes()))


# =====================================================================
# Word distribution por syllable count
# =====================================================================


def _syllables(word: str) -> int:
    """Estimación barata de sílabas para distribuir tiempos.

    - Acrónimos (todo mayúsculas ≥2 letras): cuentan como N "sílabas"
      porque se deletrean (NASA = 4, IBM = 3).
    - Resto: cuenta grupos de vocales en el normalizado a-z.
    - Mínimo 1.

    Es aproximado pero suficiente para tiling: el sentence boundary es
    sample-accurate, y el error intra-frase queda ±50 ms — invisible
    a ritmo karaoke estándar.
    """
    if word.isupper() and len(re.sub(r"[^A-Z]", "", word)) >= 2:
        return max(1, len(re.sub(r"[^A-Z]", "", word)))
    norm = re.sub(r"[^a-z]", "", word.lower())
    if not norm:
        return 1
    groups = re.findall(r"[aeiouyáéíóú]+", norm)
    return max(1, len(groups))


def distribute_word_timings(
    words: list[str],
    start_s: float,
    end_s: float,
) -> list[WordTiming]:
    """Distribuye ``words`` en el span ``[start_s, end_s]`` por sílabas.

    Cada palabra recibe una fracción del span proporcional a su syllable
    count. La última palabra termina exactamente en ``end_s`` (cierre
    sample-accurate del sentence boundary).

    Si ``words`` está vacío devuelve lista vacía. Si ``start_s == end_s``
    todas las palabras colapsan en ese instante (caso degenerado).
    """
    if not words:
        return []

    span = max(0.0, end_s - start_s)
    weights = [_syllables(w) for w in words]
    total = sum(weights)
    if total <= 0:
        # Defensive — _syllables siempre devuelve ≥1, pero por si acaso.
        total = len(words)
        weights = [1] * len(words)

    timings: list[WordTiming] = []
    cursor = start_s
    for i, (word, weight) in enumerate(zip(words, weights, strict=True)):
        if i == len(words) - 1:
            # Última palabra: cierra en end_s exacto para preservar
            # sample-accuracy del sentence boundary.
            word_end = end_s
        else:
            word_end = cursor + span * weight / total
        timings.append(WordTiming(word=word, start_s=cursor, end_s=word_end))
        cursor = word_end
    return timings


# =====================================================================
# Orquestador universal
# =====================================================================


SynthSentenceFn = Callable[[int, str, Path], Awaitable[Path]]
"""Callback que sintetiza UNA frase a un WAV.

Recibe ``(idx, sentence_text_with_tags, sub_dir)`` y debe devolver el
path al WAV generado en ``sub_dir`` (con el sample rate canónico, mono,
PCM 16-bit). El orquestador se encarga del atempo, concat y word_timings.
"""


async def synthesize_with_sample_accurate_timing(
    text: str,
    out_dir: Path,
    synth_sentence_fn: SynthSentenceFn,
    *,
    target_duration_s: float | None = None,
    strip_tags_for_words: bool = True,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[Path, float, list[WordTiming]]:
    """Pipeline universal de síntesis sample-accurate.

    Args:
        text: Script crudo (puede incluir tags inline).
        out_dir: Directorio destino para ``full.wav`` y
            ``sentences/sentence-NN.wav``.
        synth_sentence_fn: Coroutine que genera UNA frase a WAV
            canónico. La aplica el orquestador en paralelo.
        target_duration_s: Si se especifica, se calcula un factor atempo
            global tras medir la suma de durations raw para acercarse al
            objetivo. ``None`` deja a tempo natural.
        strip_tags_for_words: Si True, los tags ``[...]`` se eliminan
            antes de calcular las palabras del word_timing (Edge, Azure,
            MiMo, SiliconFlow, Silent). False solo cuando el engine
            verbaliza tags (nunca, en la práctica).
        sample_rate: Sample rate canónico para concat (debe match el WAV
            devuelto por ``synth_sentence_fn``).

    Returns:
        ``(full_audio_path, full_duration_s, word_timings)``
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sent_dir = out_dir / "sentences"
    sent_dir.mkdir(parents=True, exist_ok=True)

    sentences = split_into_sentences(text)
    if not sentences:
        # Caso degenerado: una sola "frase" sin punctuation final.
        sentences = [text.strip()] if text.strip() else []

    if not sentences:
        raise ValueError("synthesize_with_sample_accurate_timing: text vacío")

    # Fase 1: síntesis paralela por frase → WAVs raw canónicos.
    async def _synth_one(idx: int, sent: str) -> tuple[Path, float]:
        sub_dir = sent_dir / f"s{idx:02d}"
        sub_dir.mkdir(parents=True, exist_ok=True)
        raw_path = await synth_sentence_fn(idx, sent, sub_dir)
        dur = await measure_audio_duration_async(raw_path)
        return raw_path, dur

    raw_results = await asyncio.gather(
        *(_synth_one(i, sent) for i, sent in enumerate(sentences))
    )
    raw_paths = [r[0] for r in raw_results]
    raw_durations = [r[1] for r in raw_results]
    total_raw = sum(raw_durations)

    # Fase 2: calcular factor atempo global (si aplica).
    if target_duration_s and total_raw > 0:
        atempo_factor = total_raw / target_duration_s
    else:
        atempo_factor = 1.0

    # Fase 3: aplicar atempo (paralelo) y medir nueva duración.
    async def _atempo_one(idx: int, raw: Path) -> tuple[Path, float]:
        final = sent_dir / f"sentence-{idx:02d}.wav"
        await apply_atempo(raw, final, atempo_factor, sample_rate=sample_rate)
        dur = await measure_audio_duration_async(final)
        return final, dur

    final_results = await asyncio.gather(
        *(_atempo_one(i, p) for i, p in enumerate(raw_paths))
    )
    final_paths = [r[0] for r in final_results]
    final_durations = [r[1] for r in final_results]

    # Fase 4: concat nativo.
    full_path = out_dir / "full.wav"
    concat_wavs(final_paths, full_path)
    full_duration = sum(final_durations)

    # Fase 5: word_timings sample-accurate.
    word_timings: list[WordTiming] = []
    cursor = 0.0
    for sent_text, sent_dur in zip(sentences, final_durations, strict=True):
        sent_start = cursor
        sent_end = cursor + sent_dur

        spoken = strip_tts_tags(sent_text) if strip_tags_for_words else sent_text
        words = spoken.split()
        if not words:
            cursor = sent_end
            continue
        word_timings.extend(distribute_word_timings(words, sent_start, sent_end))
        cursor = sent_end

    return full_path, full_duration, word_timings


# =====================================================================
# Helpers de bajo nivel reutilizables
# =====================================================================


def wrap_pcm16_as_wav(
    pcm: bytes,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    channels: int = 1,
) -> bytes:
    """Envuelve PCM s16le crudo en un container WAV (sin escribir disco).

    Útil para engines que devuelven raw PCM (Gemini, Azure SDK con
    `Raw16Khz...`) y necesitan persistir el WAV antes de medir/atempo.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def transcode_to_canonical_wav(
    in_path: Path,
    out_path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> None:
    """Convierte cualquier audio a WAV PCM 16-bit mono al sample_rate.

    Usado por engines que devuelven MP3 (Edge, SiliconFlow, MiMo cuando
    voice_file termina en .mp3) antes de pasar por el pipeline de timing.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(in_path),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"transcode_to_canonical_wav: ffmpeg failed ({proc.returncode}): "
            f"{stderr.decode(errors='replace')[-400:]}"
        )


__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "apply_atempo",
    "concat_wavs",
    "distribute_word_timings",
    "measure_audio_duration",
    "measure_audio_duration_async",
    "split_into_sentences",
    "strip_tts_tags",
    "synthesize_with_sample_accurate_timing",
    "transcode_to_canonical_wav",
    "wrap_pcm16_as_wav",
]
