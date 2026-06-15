"""Edge TTS — Microsoft Edge consumer TTS (gratis).

Portado de MoneyPrinterTurbo `app/services/voice.py` + envuelto con el
pipeline de sample-accurate timing universal de `core.tts.timing`.

Diferencias vs MPT:
  - 100% async (usa `edge_tts.Communicate.stream()` async).
  - Sin `SubMaker` ni `subs/offset` — los word_timings vienen del pipeline
    de timing (sentence boundary sample-accurate + syllable distribution).
  - Tags inline `[curious]` se stripean antes de la síntesis (Edge no los
    interpreta y los lee literalmente como texto).
  - Output MP3 → transcodifica a WAV PCM canónico para concat nativo.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts

from shared.config import load_config
from shared.schemas import AudioArtifact

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
    transcode_to_canonical_wav,
)

_DEFAULT_RATE_STR = "+0%"
_DEFAULT_TIMEOUT_S = 30.0


def _rate_to_percent(rate: float) -> str:
    """Convierte un voice_rate float (1.0 = normal) a string `+N%` / `-N%`.

    Edge TTS rechaza el `0%` sin signo, por eso siempre prefijamos signo.
    """
    pct = round((rate - 1.0) * 100)
    return f"+{pct}%" if pct >= 0 else f"{pct}%"


async def _synth_sentence_to_mp3(
    sentence: str,
    voice: str,
    out_mp3: Path,
    rate_str: str,
    timeout_s: float,
) -> None:
    """Stream Edge TTS chunks → MP3 file.

    Strip tags inline antes de mandar el texto al servicio. Edge no soporta
    `[curious]` y los lee como "curious" (poluciona el audio).
    """
    clean = strip_tts_tags(sentence)
    if not clean:
        raise ValueError("edge: empty sentence after stripping tags")

    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(clean, voice, rate=rate_str)

    async def _consume() -> None:
        with open(out_mp3, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])

    await asyncio.wait_for(_consume(), timeout=timeout_s)


class EdgeEngine(TTSEngine):
    """Engine basado en `edge-tts` (servicio gratis de Microsoft Edge)."""

    name = "edge"
    supports_inline_tags = False

    def __init__(self, voice_rate: float = 1.0, timeout_s: float | None = None):
        self._rate_str = _rate_to_percent(voice_rate)
        if timeout_s is None:
            try:
                cfg = load_config().get_tts_engine(self.name)
                self._timeout_s = float(cfg.timeout or _DEFAULT_TIMEOUT_S)
            except Exception:
                self._timeout_s = _DEFAULT_TIMEOUT_S
        else:
            self._timeout_s = timeout_s

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        out_dir.mkdir(parents=True, exist_ok=True)

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            mp3_path = sub_dir / "raw.mp3"
            await _synth_sentence_to_mp3(
                sentence=sentence,
                voice=voice,
                out_mp3=mp3_path,
                rate_str=self._rate_str,
                timeout_s=self._timeout_s,
            )
            # El pipeline necesita WAV canónico para concat nativo.
            wav_path = sub_dir / "raw.wav"
            await transcode_to_canonical_wav(
                mp3_path, wav_path, sample_rate=DEFAULT_SAMPLE_RATE
            )
            return wav_path

        full_path, duration, words = await synthesize_with_sample_accurate_timing(
            text=text,
            out_dir=out_dir,
            synth_sentence_fn=_synth_sentence_fn,
            target_duration_s=target_duration_s,
            strip_tags_for_words=True,
        )

        return AudioArtifact(
            path=full_path,
            duration_s=duration,
            word_timings=words,
            engine=self.name,
        )


__all__ = ["EdgeEngine"]
