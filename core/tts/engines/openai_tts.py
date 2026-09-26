"""TTS de OpenAI con dirección actoral.

Por qué existe
--------------
Edge es gratis y sirve para narración informativa, pero es **plano**: dice el
texto sin interpretarlo. En un reel científico da igual; en un melodrama, donde
la mitad de la información está en cómo se dice la línea, se nota en el primer
segundo — la protagonista grita "¡tu familia destruyó a mi papá!" con el mismo
tono con el que leería el pronóstico del tiempo.

`gpt-4o-mini-tts` acepta un campo `instructions` con dirección de doblaje en
lenguaje natural: *"rabia contenida, al borde del llanto, acento chilango"*. Eso
convierte al TTS en algo dirigible, que es lo que un melodrama necesita.

Encaja sin fricción
-------------------
Devuelve WAV `pcm_s16le` 24 kHz mono — exactamente el formato canónico que
`concat_wavs` exige, así que no hay transcodificación intermedia. Es el único
engine del repo que sale nativo en el formato correcto.

Formato de voz: ``openai:coral`` — el prefijo lo rutea aquí y `coral` es una de
las voces del catálogo.
"""
from __future__ import annotations

from pathlib import Path

import httpx
from loguru import logger

from core.tts.base import TTSEngine
from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
)
from shared.schemas import AudioArtifact

API_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "coral"

#: Voces del catálogo. `gpt-4o-mini-tts` las soporta todas; los `tts-1`
#: antiguos solo las seis primeras.
VOICES: tuple[str, ...] = (
    "alloy", "echo", "fable", "onyx", "nova", "shimmer",
    "ash", "ballad", "coral", "sage", "verse",
)

#: Reparto por defecto para castellano. El orden importa por la misma razón que
#: en `core.long_form.voices`: personajes distintos necesitan timbres distintos,
#: y estas están ordenadas para que dos consecutivas no se confundan.
FEMININE: tuple[str, ...] = ("coral", "nova", "shimmer", "sage", "ballad")
MASCULINE: tuple[str, ...] = ("onyx", "echo", "ash", "verse", "fable")


class OpenAITTSEngine(TTSEngine):
    """Engine dirigible: la misma voz cambia de registro según `instructions`."""

    name = "openai"
    #: No interpreta tags `[curious]` como Gemini: la dirección va aparte, en
    #: `instructions`, así que los tags deben stripearse del texto hablado.
    supports_inline_tags = False

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        instructions: str = "",
        timeout_s: float = 120.0,
    ) -> None:
        from shared.config import load_config

        cfg = load_config()
        self.api_key = api_key or cfg.get_llm_provider("openai").api_key
        self.model = model or DEFAULT_MODEL
        #: Dirección de doblaje. La inyecta el caller (el director del episodio,
        #: desde el personaje), no el texto.
        self.instructions = instructions
        self.timeout_s = timeout_s

    async def _synth_one(self, sentence: str, out_path: Path, voice: str) -> Path:
        payload: dict[str, object] = {
            "model": self.model,
            "voice": voice or DEFAULT_VOICE,
            "input": strip_tts_tags(sentence),  # si no, lee "[curious]" en voz alta
            "response_format": "wav",
        }
        # `instructions` solo existe en los modelos gpt-4o*-tts; mandarlo a
        # `tts-1` da 400.
        if self.instructions and "gpt-4o" in self.model:
            payload["instructions"] = self.instructions

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"OpenAI TTS HTTP {resp.status_code}: {resp.text[:250]}"
                )
            if not resp.content:
                raise RuntimeError("OpenAI TTS: respuesta vacía")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return out_path

    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        if not self.api_key:
            raise RuntimeError("OpenAITTSEngine: OPENAI_API_KEY no configurada")

        resolved = (voice or DEFAULT_VOICE).strip().lower()
        if resolved not in VOICES:
            logger.warning(
                f"[openai_tts] voz '{resolved}' no está en el catálogo; uso "
                f"'{DEFAULT_VOICE}'"
            )
            resolved = DEFAULT_VOICE

        async def _synth_sentence_fn(idx: int, sentence: str, sub_dir: Path) -> Path:
            return await self._synth_one(sentence, sub_dir / "raw.wav", resolved)

        full_path, duration, words = await synthesize_with_sample_accurate_timing(
            text=text,
            out_dir=out_dir,
            synth_sentence_fn=_synth_sentence_fn,
            target_duration_s=target_duration_s,
            sample_rate=DEFAULT_SAMPLE_RATE,
        )
        return AudioArtifact(
            path=full_path,
            duration_s=duration,
            word_timings=words,
            engine=self.name,
        )


__all__ = ["FEMININE", "MASCULINE", "VOICES", "OpenAITTSEngine"]
