"""Silent engine — modo "no-voice".

Genera un track de silencio con duración estimada por syllable/char count
y devuelve word_timings distribuidos sobre ese span. Útil cuando el
usuario sólo quiere video con BGM y subtítulos, o cuando no hay API key
de TTS disponible y queremos un placeholder con el time axis correcto.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path

from shared.schemas import AudioArtifact

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    distribute_word_timings,
    measure_audio_duration_async,
    split_into_sentences,
    strip_tts_tags,
)


def estimate_speech_duration(text: str) -> float:
    """Estima duración de "lectura" del texto.

    Portado de MPT `estimate_no_voice_duration`. Combina:
      - CJK chars: 4.2 char/s
      - Palabras ASCII: 2.7 word/s
      - Otros alfabetos (cyrillic, arabic, etc.): 4.0 char/s
      - Pausa de 0.35 s por separador de frase
      - Mínimo 3.0 s

    Returns:
        Duración estimada en segundos (≥3.0).
    """
    norm = (text or "").strip()
    if not norm:
        return 3.0

    cjk_chars = len(re.findall(r"[一-鿿]", norm))
    words = len(re.findall(r"[A-Za-z0-9]+", norm))
    ascii_word_chars = sum(len(w) for w in re.findall(r"[A-Za-z0-9]+", norm))

    other_text_chars = 0
    for ch in norm:
        category = unicodedata.category(ch)
        if category.startswith(("L", "N")):
            other_text_chars += 1
    other_text_chars = max(other_text_chars - cjk_chars - ascii_word_chars, 0)

    sentence_count = max(len(split_into_sentences(norm)), 1)

    cjk_dur = cjk_chars / 4.2
    word_dur = words / 2.7
    other_dur = other_text_chars / 4.0
    pause_dur = max(sentence_count - 1, 0) * 0.35

    return max(3.0, cjk_dur + word_dur + other_dur + pause_dur)


async def _generate_silent_wav(
    duration_s: float, out_path: Path, sample_rate: int = DEFAULT_SAMPLE_RATE
) -> None:
    """Genera un WAV de silencio canónico (mono, 16-bit, sample_rate)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{max(duration_s, 0.1):.3f}",
        "-c:a", "pcm_s16le",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"silent: ffmpeg anullsrc failed ({proc.returncode}): "
            f"{stderr.decode(errors='replace')[-400:]}"
        )


class SilentEngine(TTSEngine):
    """Engine que genera silencio + word_timings sintéticos."""

    name = "silent"
    supports_inline_tags = False

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        out_dir.mkdir(parents=True, exist_ok=True)
        duration = target_duration_s or estimate_speech_duration(text)

        full_path = out_dir / "full.wav"
        await _generate_silent_wav(duration, full_path)
        measured = await measure_audio_duration_async(full_path)

        # Word timings distribuidos uniformemente por syllable count.
        # Para silent NO importan los tags inline (no se sintetizan), pero
        # sí queremos respetar los sentence boundaries para que los
        # subtítulos funcionen.
        sentences = split_into_sentences(text) or [text.strip()]
        # Pesar cada frase por su longitud de char (proxy más estable que
        # syllable count cuando hay CJK).
        char_counts = [max(len(strip_tts_tags(s)), 1) for s in sentences]
        total_chars = sum(char_counts)

        word_timings = []
        cursor = 0.0
        for sent, count in zip(sentences, char_counts, strict=True):
            sent_dur = measured * count / total_chars
            sent_start = cursor
            sent_end = cursor + sent_dur
            spoken = strip_tts_tags(sent).split()
            if spoken:
                word_timings.extend(
                    distribute_word_timings(spoken, sent_start, sent_end)
                )
            cursor = sent_end

        return AudioArtifact(
            path=full_path,
            duration_s=measured,
            word_timings=word_timings,
            engine=self.name,
        )


__all__ = ["SilentEngine", "estimate_speech_duration"]
