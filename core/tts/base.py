"""Interfaz común para engines TTS.

Todos los engines exponen `synthesize(text, voice, out_dir, target_duration_s)`
y devuelven un `AudioArtifact` con `word_timings` sample-accurate. El sample
accuracy lo provee `core.tts.timing` — los engines individuales solo se
encargan de la síntesis raw de cada frase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from shared.schemas import AudioArtifact


class TTSEngine(ABC):
    """Driver TTS abstracto.

    Cada subclase representa un proveedor (Edge, Gemini Flash, Azure, MiMo,
    SiliconFlow, Silent). El framework de timing es responsabilidad de
    `core.tts.timing` y se aplica desde la propia implementación de
    `synthesize` o vía los helpers comunes.
    """

    #: Identificador estable usado en logs y en `AudioArtifact.engine`.
    name: str = "base"

    #: Indica si el engine entiende tags inline (`[curious]`, `[emphasis]`).
    #: Solo Gemini Flash los honra; los demás deben stripearlos antes de
    #: sintetizar pero mantener las palabras en el word_timing.
    supports_inline_tags: bool = False

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str,
        out_dir: Path,
        target_duration_s: float | None = None,
    ) -> AudioArtifact:
        """Sintetiza ``text`` con la voz indicada.

        Args:
            text: Script con o sin tags inline `[curious]`. Si el engine no
                soporta tags, deben stripearse para la síntesis raw pero
                las palabras correspondientes a los tags NO deben aparecer
                en el word_timing.
            voice: Nombre de voz (formato específico de engine).
            out_dir: Directorio donde colocar `full.wav` y artefactos
                intermedios (`sentences/`).
            target_duration_s: Si se especifica, se aplica `atempo` para
                acercar la duración al objetivo (preserve pitch). `None`
                deja la voz a tempo natural.

        Returns:
            AudioArtifact con `path` (full.wav), `duration_s` medida
            con ffprobe sobre el resultado final, `word_timings`
            sample-accurate sentence-by-sentence, y `engine=self.name`.
        """
        ...


__all__ = ["TTSEngine"]
