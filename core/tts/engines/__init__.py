"""Engines TTS concretos (drivers de la interfaz común).

Cada engine vive en su propio módulo para que los imports pesados (SDK
Azure, google-generativeai, httpx) sean lazy via `core.tts.registry`.
"""

__all__ = [
    "AzureEngine",
    "EdgeEngine",
    "GeminiFlashEngine",
    "MimoEngine",
    "SilentEngine",
    "SiliconFlowEngine",
]


def __getattr__(name: str):
    """Lazy re-exports — evita cargar SDKs hasta usarlos."""
    if name == "EdgeEngine":
        from core.tts.engines.edge import EdgeEngine

        return EdgeEngine
    if name == "GeminiFlashEngine":
        from core.tts.engines.gemini_flash import GeminiFlashEngine

        return GeminiFlashEngine
    if name == "AzureEngine":
        from core.tts.engines.azure import AzureEngine

        return AzureEngine
    if name == "MimoEngine":
        from core.tts.engines.mimo import MimoEngine

        return MimoEngine
    if name == "SiliconFlowEngine":
        from core.tts.engines.siliconflow import SiliconFlowEngine

        return SiliconFlowEngine
    if name == "SilentEngine":
        from core.tts.engines.silent import SilentEngine

        return SilentEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
