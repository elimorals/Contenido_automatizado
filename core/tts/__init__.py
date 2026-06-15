"""TTS engines unificados + sample-accurate timing universal.

Top-level API:

    from core.tts import get_engine, detect_engine_and_voice, TTSEngine
    from core.tts import synthesize_with_sample_accurate_timing

    name, voice = detect_engine_and_voice("en-US-AvaNeural-Female")
    artifact = await get_engine(name).synthesize(text, voice, out_dir)

Módulos:
  - `base`        : ABC `TTSEngine`.
  - `timing`      : helpers universales (split, ffprobe, atempo, concat,
                    distribute_word_timings, synthesize_with_sample_accurate_timing).
  - `registry`    : `get_engine(name)` lazy factory.
  - `voice_names` : parser unificado de voice strings.
  - `engines/`    : drivers Edge, Gemini Flash, Azure, MiMo, SiliconFlow, Silent.
"""
from __future__ import annotations

from core.tts.base import TTSEngine
from core.tts.registry import SUPPORTED_ENGINES, get_engine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    apply_atempo,
    concat_wavs,
    distribute_word_timings,
    measure_audio_duration,
    measure_audio_duration_async,
    split_into_sentences,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
    transcode_to_canonical_wav,
    wrap_pcm16_as_wav,
)
from core.tts.voice_names import (
    NO_VOICE_NAME,
    detect_engine_and_voice,
    is_no_voice,
)

__all__ = [
    "DEFAULT_SAMPLE_RATE",
    "NO_VOICE_NAME",
    "SUPPORTED_ENGINES",
    "TTSEngine",
    "apply_atempo",
    "concat_wavs",
    "detect_engine_and_voice",
    "distribute_word_timings",
    "get_engine",
    "is_no_voice",
    "measure_audio_duration",
    "measure_audio_duration_async",
    "split_into_sentences",
    "strip_tts_tags",
    "synthesize_with_sample_accurate_timing",
    "transcode_to_canonical_wav",
    "wrap_pcm16_as_wav",
]
