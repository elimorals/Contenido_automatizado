"""Registry de engines TTS.

Patrón factory simple:

    engine = get_engine("edge")
    artifact = await engine.synthesize(text, voice, out_dir)

Lazy-import los engines pesados (Azure SDK, google-generativeai) sólo
cuando se piden — así un import de `core.tts` no fuerza tener todas las
deps instaladas si solo vas a usar Edge o Silent.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.tts.base import TTSEngine


# Lista canónica de engines soportados (sirve también para validación
# de config y para get_engines() del UI).
SUPPORTED_ENGINES = (
    "edge",
    "gemini_flash",
    "azure",
    "mimo",
    "siliconflow",
    "silent",
)


def get_engine(name: str, **kwargs) -> "TTSEngine":
    """Instancia un engine por nombre.

    Args:
        name: Uno de `SUPPORTED_ENGINES`.
        **kwargs: Forward a la constructora del engine (api_key,
            voice_rate, timeout_s, etc.). Para overrides on-the-fly.

    Raises:
        ValueError: si ``name`` no está soportado.

    Examples:
        >>> engine = get_engine("silent")
        >>> engine.name
        'silent'
    """
    name = (name or "").strip().lower()

    if name == "silent":
        from core.tts.engines.silent import SilentEngine

        return SilentEngine(**kwargs)

    if name == "edge":
        from core.tts.engines.edge import EdgeEngine

        return EdgeEngine(**kwargs)

    if name == "gemini_flash":
        from core.tts.engines.gemini_flash import GeminiFlashEngine

        return GeminiFlashEngine(**kwargs)

    if name == "azure":
        from core.tts.engines.azure import AzureEngine

        return AzureEngine(**kwargs)

    if name == "mimo":
        from core.tts.engines.mimo import MimoEngine

        return MimoEngine(**kwargs)

    if name == "siliconflow":
        from core.tts.engines.siliconflow import SiliconFlowEngine

        return SiliconFlowEngine(**kwargs)

    raise ValueError(
        f"Unknown TTS engine '{name}'. Supported: {SUPPORTED_ENGINES}"
    )


__all__ = ["SUPPORTED_ENGINES", "get_engine"]
