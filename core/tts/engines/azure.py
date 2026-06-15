"""Azure Cognitive Services Speech — premium voices (incluyendo V2).

Portado de MoneyPrinterTurbo `azure_tts_v2()`. Azure SDK es sync, así que
encapsulamos el `speak_text_async().get()` en `asyncio.to_thread` y por
frase. El SDK devuelve un MP3 directamente al `voice_file`; transcodificamos
a WAV canónico para el concat nativo del pipeline de timing.

Tags inline NO soportados (Azure puede aceptar SSML pero por ahora
stripeamos para mantener interfaz simple).
"""
from __future__ import annotations

import asyncio
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


def _sync_azure_synth(
    text: str,
    voice: str,
    out_path: Path,
    speech_key: str,
    region: str,
) -> None:
    """Llama al SDK Azure (sync) y escribe MP3 en out_path."""
    import azure.cognitiveservices.speech as speechsdk  # type: ignore[import-not-found]

    audio_config = speechsdk.audio.AudioOutputConfig(
        filename=str(out_path), use_default_speaker=False
    )
    speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=region)
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio48Khz192KBitRateMonoMp3
    )
    synthesizer = speechsdk.SpeechSynthesizer(
        audio_config=audio_config, speech_config=speech_config
    )
    result = synthesizer.speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        details = getattr(result, "cancellation_details", None)
        reason = getattr(details, "error_details", None) or result.reason
        raise RuntimeError(f"azure: synth failed: {reason}")


class AzureEngine(TTSEngine):
    """Azure Cognitive Services Speech."""

    name = "azure"
    supports_inline_tags = False

    def __init__(self, api_key: str | None = None, region: str | None = None):
        cfg = load_config().get_tts_engine(self.name)
        self._api_key = api_key or cfg.api_key
        self._region = region or cfg.region or "eastus"

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        if not self._api_key:
            raise RuntimeError(
                "azure: missing API key. Set AZURE_TTS_API_KEY or "
                "configure tts.azure.api_key in config.toml"
            )

        out_dir.mkdir(parents=True, exist_ok=True)

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            clean = strip_tts_tags(sentence)
            if not clean:
                raise ValueError(f"azure: empty sentence {idx} after stripping tags")

            mp3_path = sub_dir / "raw.mp3"
            mp3_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                _sync_azure_synth,
                clean,
                voice,
                mp3_path,
                self._api_key,
                self._region,
            )
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


__all__ = ["AzureEngine"]
