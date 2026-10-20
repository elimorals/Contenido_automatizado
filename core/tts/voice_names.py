"""Parser unificado de voice names — portado de MoneyPrinterTurbo.

Heurística de detección de engine por nombre:

  - `azure:` / `azure-…-V2-Female`           → Azure Cognitive Services V2
  - `mimo:…-Female`                          → Xiaomi MiMo
  - `gemini:…-Female`                        → Gemini Flash (interno reels-af)
  - `siliconflow:model:voice-Gender`         → SiliconFlow CosyVoice2
  - `openai:coral`                           → OpenAI gpt-4o-mini-tts
  - `no-voice` / `none`                      → Silent (no-voice)
  - `xx-XX-…Neural-Gender`                   → Edge TTS (default fallback)

Para llamarse desde el registry / pipeline:

    engine, parsed_voice = detect_engine_and_voice(name)
    artifact = await get_engine(engine).synthesize(text, parsed_voice, …)
"""
from __future__ import annotations

NO_VOICE_NAME = "no-voice"
# Voz cuando el usuario no eligió ninguna. Los prompts del DAG escriben en
# inglés, así que una voz es-MX leería mal; se cambia en `[tts.edge].default_voice`.
FALLBACK_VOICE = "en-US-AvaNeural-Female"
_NO_VOICE_ALIASES = {NO_VOICE_NAME, "none", ""}


def strip_gender_suffix(name: str) -> str:
    """Elimina los sufijos ``-Female`` / ``-Male`` que MPT usa en el UI."""
    return name.replace("-Female", "").replace("-Male", "").strip()


def is_no_voice(voice_name: str | None) -> bool:
    """¿El usuario eligió explícitamente "sin voz"?"""
    return str(voice_name or "").strip().lower() in _NO_VOICE_ALIASES


def resolve_voice_name(voice_name: str | None) -> str:
    """Vacío → voz por defecto real (antes vacío = reel mudo).

    Solo ``no-voice``/``none`` explícitos piden silencio.
    """
    name = str(voice_name or "").strip()
    if name:
        return name
    from shared.config import load_config

    return load_config().get_tts_engine("edge").default_voice or FALLBACK_VOICE


def is_azure_v2(voice_name: str) -> str:
    """Devuelve el voice name (sin sufijo) si es Azure V2, else ``""``."""
    base = strip_gender_suffix(voice_name)
    if base.endswith("-V2"):
        return base.replace("-V2", "").strip()
    return ""


def is_siliconflow(voice_name: str) -> bool:
    return voice_name.startswith("siliconflow:")


def is_gemini(voice_name: str) -> bool:
    return voice_name.startswith("gemini:")


def is_mimo(voice_name: str) -> bool:
    return voice_name.startswith("mimo:")


def is_azure_explicit(voice_name: str) -> bool:
    """Prefijo explícito ``azure:`` (alternativa a la heurística V2)."""
    return voice_name.startswith("azure:")


def detect_engine_and_voice(voice_name: str) -> tuple[str, str]:
    """Identifica engine y normaliza el voice name a lo que el engine espera.

    Returns:
        ``(engine_name, voice_arg)``. ``engine_name`` ∈
        {edge, gemini_flash, azure, mimo, siliconflow, silent}.
        ``voice_arg`` es el string final que se le pasa a
        ``engine.synthesize(voice=...)``.

    Examples:
        >>> detect_engine_and_voice("en-US-AvaNeural-Female")
        ('edge', 'en-US-AvaNeural')
        >>> detect_engine_and_voice("gemini:Zephyr-Female")
        ('gemini_flash', 'Zephyr')
        >>> detect_engine_and_voice("mimo:冰糖-Female")
        ('mimo', '冰糖')
        >>> detect_engine_and_voice("siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex-Male")
        ('siliconflow', 'FunAudioLLM/CosyVoice2-0.5B:alex')
        >>> detect_engine_and_voice("zh-CN-XiaoxiaoMultilingualNeural-V2-Female")
        ('azure', 'zh-CN-XiaoxiaoMultilingualNeural')
        >>> detect_engine_and_voice("no-voice")
        ('silent', '')
    """
    if is_no_voice(voice_name):
        return ("silent", "")

    if is_gemini(voice_name):
        # gemini:Zephyr-Female → "Zephyr"
        parts = voice_name.split(":", 1)
        if len(parts) >= 2:
            voice = strip_gender_suffix(parts[1])
            return ("gemini_flash", voice)
        return ("gemini_flash", strip_gender_suffix(voice_name))

    if is_mimo(voice_name):
        # mimo:冰糖-Female → "冰糖"
        parts = voice_name.split(":", 1)
        if len(parts) >= 2:
            voice = strip_gender_suffix(parts[1])
            return ("mimo", voice)
        return ("mimo", strip_gender_suffix(voice_name))

    if is_siliconflow(voice_name):
        # siliconflow:model:voice-Gender → "model:voice"
        parts = voice_name.split(":")
        if len(parts) >= 3:
            model = parts[1]
            voice = strip_gender_suffix(parts[2])
            # voice puede incluir slash en model (FunAudioLLM/CosyVoice2-0.5B)
            return ("siliconflow", f"{model}:{voice}")
        return ("siliconflow", voice_name)

    if voice_name.startswith("openai:"):
        return ("openai", strip_gender_suffix(voice_name.split(":", 1)[1]))

    if is_azure_explicit(voice_name):
        # azure:Name → "Name"
        parts = voice_name.split(":", 1)
        if len(parts) >= 2:
            return ("azure", strip_gender_suffix(parts[1]))
        return ("azure", strip_gender_suffix(voice_name))

    v2 = is_azure_v2(voice_name)
    if v2:
        return ("azure", v2)

    # Fallback: nombre Edge-style (xx-XX-NameNeural-Gender)
    return ("edge", strip_gender_suffix(voice_name))


__all__ = [
    "FALLBACK_VOICE",
    "NO_VOICE_NAME",
    "detect_engine_and_voice",
    "is_azure_explicit",
    "is_azure_v2",
    "is_gemini",
    "is_mimo",
    "is_no_voice",
    "is_siliconflow",
    "resolve_voice_name",
    "strip_gender_suffix",
]
