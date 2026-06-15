"""Xiaomi MiMo V2.5 TTS.

Portado de MoneyPrinterTurbo `mimo_tts()`. La API es OpenAI Chat
Completions compatible pero con dos quirks:
  1. El texto a sintetizar va en un mensaje `assistant`, no `user`.
  2. El audio viene como base64 en `choices[0].message.audio.data`.

El SDK `openai` es sync → usamos `asyncio.to_thread` por frase.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from shared.config import load_config
from shared.schemas import AudioArtifact

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
    transcode_to_canonical_wav,
)

_MIMO_DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
_MIMO_DEFAULT_TTS_MODEL = "mimo-v2.5-tts"
_MIMO_DEFAULT_STYLE_PROMPT = (
    "请用自然、清晰、适合短视频旁白的语气朗读。"
    # "Read in a natural, clear tone suitable for short-video narration."
)


def _sync_mimo_synth(
    text: str,
    voice: str,
    api_key: str,
    base_url: str,
    model: str,
    style_prompt: str,
) -> bytes:
    """Llama MiMo (sync) y devuelve WAV bytes."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": style_prompt},
            {"role": "assistant", "content": text},
        ],
        audio={"format": "wav", "voice": voice},
    )
    if not completion or not getattr(completion, "choices", None):
        raise RuntimeError("mimo: empty response from API")
    message = completion.choices[0].message
    audio = getattr(message, "audio", None)
    data = None
    if isinstance(audio, dict):
        data = audio.get("data")
    elif audio is not None:
        data = getattr(audio, "data", None)
    if not data:
        raise RuntimeError("mimo: no audio data in response")
    return base64.b64decode(data)


class MimoEngine(TTSEngine):
    """Xiaomi MiMo V2.5 TTS engine."""

    name = "mimo"
    supports_inline_tags = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        style_prompt: str | None = None,
    ):
        cfg = load_config().get_tts_engine(self.name)
        self._api_key = api_key or cfg.api_key
        self._base_url = base_url or _MIMO_DEFAULT_BASE_URL
        self._model = model or cfg.model or _MIMO_DEFAULT_TTS_MODEL
        self._style_prompt = style_prompt or _MIMO_DEFAULT_STYLE_PROMPT

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        if not self._api_key:
            raise RuntimeError(
                "mimo: missing API key. Set MIMO_API_KEY or configure "
                "tts.mimo.api_key in config.toml"
            )

        out_dir.mkdir(parents=True, exist_ok=True)

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            clean = strip_tts_tags(sentence)
            if not clean:
                raise ValueError(f"mimo: empty sentence {idx} after stripping tags")

            raw_wav_bytes = await asyncio.to_thread(
                _sync_mimo_synth,
                clean,
                voice,
                self._api_key,
                self._base_url,
                self._model,
                self._style_prompt,
            )
            raw_path = sub_dir / "raw_api.wav"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw_wav_bytes)

            # Normalizar a WAV canónico (sample rate, mono).
            canonical = sub_dir / "raw.wav"
            await transcode_to_canonical_wav(
                raw_path, canonical, sample_rate=DEFAULT_SAMPLE_RATE
            )
            return canonical

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


__all__ = ["MimoEngine"]
