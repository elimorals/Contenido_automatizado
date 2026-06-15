"""Gemini Flash TTS — el único engine que soporta tags inline.

Combina:
  - El flujo SDK de MoneyPrinterTurbo `gemini_tts()` (gemini-2.5-flash-preview-tts).
  - El pipeline sample-accurate de reels-af `render/tts.py`.

Gemini Flash interpreta `[curious]`, `[emphasis]`, `[confident]` y ~200
audio tags más como stage directions — modulan delivery sin meter el
texto del tag en el audio. Por eso `supports_inline_tags = True` y el
pipeline pasa la frase CON tags al synth_sentence_fn (los tags entran
al modelo) pero el word_timing se calcula con tags STRIPED (no aparecen
en el audio).

Output: raw PCM @ 24kHz mono 16-bit → envolvemos en WAV header.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from shared.config import load_config
from shared.schemas import AudioArtifact

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    synthesize_with_sample_accurate_timing,
    wrap_pcm16_as_wav,
)

DEFAULT_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"


async def _gemini_synth_sentence_pcm(
    sentence: str, voice: str, api_key: str, model_name: str
) -> bytes:
    """Llama a Gemini TTS y devuelve los bytes PCM crudos.

    El SDK `google.generativeai` es sync — lo lanzamos en `to_thread`.
    """
    import google.generativeai as genai

    def _sync_call() -> bytes:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        generation_config = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": voice}
                }
            },
        }
        response = model.generate_content(
            contents=sentence,
            generation_config=generation_config,
        )
        if not response.candidates or not response.candidates[0].content:
            raise RuntimeError("gemini_flash: empty response from API")
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                data = part.inline_data.data
                if isinstance(data, str):
                    import base64
                    return base64.b64decode(data)
                return bytes(data)
        raise RuntimeError("gemini_flash: no audio data in response parts")

    return await asyncio.to_thread(_sync_call)


class GeminiFlashEngine(TTSEngine):
    """Engine basado en `gemini-2.5-flash-preview-tts`.

    Único engine con inline tag support — pasa la frase con tags
    al modelo sin stripear, pero los tags NO aparecen en el word_timing.
    """

    name = "gemini_flash"
    supports_inline_tags = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        cfg = load_config().get_tts_engine(self.name)
        self._api_key = api_key or cfg.api_key
        self._model = model or cfg.model or DEFAULT_GEMINI_TTS_MODEL
        if not self._api_key:
            # Permitimos instanciar sin key para tests/registry — falla al
            # llamar synthesize() si efectivamente se necesita.
            self._api_key = ""

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        if not self._api_key:
            raise RuntimeError(
                "gemini_flash: missing API key. Set GEMINI_API_KEY or "
                "configure tts.gemini_flash.api_key in config.toml"
            )

        out_dir.mkdir(parents=True, exist_ok=True)

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            # NOTA: sentence aquí incluye los tags inline. NO stripear —
            # Gemini los interpreta como stage directions.
            pcm = await _gemini_synth_sentence_pcm(
                sentence=sentence,
                voice=voice,
                api_key=self._api_key,
                model_name=self._model,
            )
            if not pcm:
                raise RuntimeError(
                    f"gemini_flash: synth returned empty pcm for sentence {idx}"
                )
            wav_bytes = wrap_pcm16_as_wav(pcm, sample_rate=DEFAULT_SAMPLE_RATE)
            wav_path = sub_dir / "raw.wav"
            wav_path.write_bytes(wav_bytes)
            return wav_path

        full_path, duration, words = await synthesize_with_sample_accurate_timing(
            text=text,
            out_dir=out_dir,
            synth_sentence_fn=_synth_sentence_fn,
            target_duration_s=target_duration_s,
            # strip_tags_for_words=True → tags NO van al word_timing.
            # El audio tampoco contiene el texto del tag (Gemini los
            # interpreta como stage directions, no los lee).
            strip_tags_for_words=True,
        )

        return AudioArtifact(
            path=full_path,
            duration_s=duration,
            word_timings=words,
            engine=self.name,
        )


__all__ = ["DEFAULT_GEMINI_TTS_MODEL", "GeminiFlashEngine"]
