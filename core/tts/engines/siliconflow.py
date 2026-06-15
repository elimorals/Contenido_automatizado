"""SiliconFlow CosyVoice2 — TTS chino/inglés muy barato.

Portado de MoneyPrinterTurbo `siliconflow_tts()`. La API es REST HTTP
con endpoint `/v1/audio/speech`. Voice format: `model:voice` (e.g.
`FunAudioLLM/CosyVoice2-0.5B:alex`).

Output: MP3 → transcodificar a WAV canónico.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from shared.config import load_config
from shared.schemas import AudioArtifact

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
    transcode_to_canonical_wav,
)

_SF_ENDPOINT = "https://api.siliconflow.cn/v1/audio/speech"


async def _http_siliconflow_synth(
    text: str,
    model: str,
    voice: str,
    api_key: str,
    out_mp3: Path,
    voice_rate: float,
    voice_volume: float,
    timeout_s: float,
) -> None:
    """POST a SiliconFlow y guarda MP3 en disco."""
    gain = max(-10.0, min(10.0, voice_volume - 1.0))
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "mp3",
        "sample_rate": 32000,
        "stream": False,
        "speed": voice_rate,
        "gain": gain,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(_SF_ENDPOINT, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(
                f"siliconflow: HTTP {resp.status_code}: {resp.text[:300]}"
            )
        out_mp3.write_bytes(resp.content)


class SiliconFlowEngine(TTSEngine):
    """SiliconFlow CosyVoice2 engine."""

    name = "siliconflow"
    supports_inline_tags = False

    def __init__(
        self,
        api_key: str | None = None,
        voice_rate: float = 1.0,
        voice_volume: float = 1.0,
        timeout_s: float = 60.0,
    ):
        cfg = load_config().get_tts_engine(self.name)
        self._api_key = api_key or cfg.api_key
        self._voice_rate = voice_rate
        self._voice_volume = voice_volume
        self._timeout_s = timeout_s

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        if not self._api_key:
            raise RuntimeError(
                "siliconflow: missing API key. Set SILICONFLOW_API_KEY or "
                "configure tts.siliconflow.api_key in config.toml"
            )

        # voice format: "model:voice" (e.g. "FunAudioLLM/CosyVoice2-0.5B:alex")
        # El model puede contener "/" — split por el último ":".
        if ":" not in voice:
            raise ValueError(
                f"siliconflow: voice debe tener formato 'model:voice', got '{voice}'"
            )
        model, voice_id = voice.rsplit(":", 1)

        out_dir.mkdir(parents=True, exist_ok=True)

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            clean = strip_tts_tags(sentence)
            if not clean:
                raise ValueError(
                    f"siliconflow: empty sentence {idx} after stripping tags"
                )

            mp3_path = sub_dir / "raw.mp3"
            await _http_siliconflow_synth(
                text=clean,
                model=model,
                voice=f"{model}:{voice_id}",
                api_key=self._api_key,
                out_mp3=mp3_path,
                voice_rate=self._voice_rate,
                voice_volume=self._voice_volume,
                timeout_s=self._timeout_s,
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


# Avoid unused-import shadow warning by ensuring asyncio is referenced if
# httpx ever exposes a sync fallback in the future.
_ = asyncio


__all__ = ["SiliconFlowEngine"]
