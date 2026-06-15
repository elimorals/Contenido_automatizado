"""Tests de engines TTS (con mocks, sin red).

Estrategia:
  - `silent` se testea end-to-end (sólo necesita ffmpeg, no red).
  - `edge`, `gemini_flash`, `azure`, `mimo`, `siliconflow` se testean
    monkeypatcheando el `synth_sentence_fn` / la llamada API para
    devolver WAV pregenerados localmente. Verificamos contrato: nombre,
    word_timings, engine field.
  - `voice_names.detect_engine_and_voice` se testea para todos los
    formatos comunes.
  - `registry.get_engine` resuelve nombres correctos y rechaza inválidos.
"""
from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from core.tts import (
    SUPPORTED_ENGINES,
    TTSEngine,
    detect_engine_and_voice,
    get_engine,
)
from core.tts.timing import DEFAULT_SAMPLE_RATE

# pyproject configura `asyncio_mode = "auto"` → tests async se detectan
# automáticamente; los sync no necesitan marker.

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_requires_ffmpeg = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe no disponible en PATH"
)


def _make_silent_wav(out: Path, duration_s: float, sr: int = DEFAULT_SAMPLE_RATE) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = int(duration_s * sr)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n)


# =====================================================================
# voice_names — detect_engine_and_voice
# =====================================================================


@pytest.mark.parametrize("name,expected_engine,expected_voice", [
    ("en-US-AvaNeural-Female", "edge", "en-US-AvaNeural"),
    ("zh-CN-XiaoxiaoNeural-Female", "edge", "zh-CN-XiaoxiaoNeural"),
    ("gemini:Zephyr-Female", "gemini_flash", "Zephyr"),
    ("gemini:Puck-Male", "gemini_flash", "Puck"),
    ("mimo:冰糖-Female", "mimo", "冰糖"),
    ("mimo:Mia-Female", "mimo", "Mia"),
    ("siliconflow:FunAudioLLM/CosyVoice2-0.5B:alex-Male", "siliconflow",
        "FunAudioLLM/CosyVoice2-0.5B:alex"),
    ("zh-CN-XiaoxiaoMultilingualNeural-V2-Female", "azure",
        "zh-CN-XiaoxiaoMultilingualNeural"),
    ("azure:CustomVoice-Female", "azure", "CustomVoice"),
    ("no-voice", "silent", ""),
    ("none", "silent", ""),
    ("", "silent", ""),
])
def test_detect_engine_and_voice(name, expected_engine, expected_voice):
    engine, voice = detect_engine_and_voice(name)
    assert engine == expected_engine
    assert voice == expected_voice


# =====================================================================
# registry — get_engine
# =====================================================================


def test_supported_engines_set():
    assert set(SUPPORTED_ENGINES) == {
        "edge", "gemini_flash", "azure", "mimo", "siliconflow", "silent"
    }


def test_get_engine_silent_instantiates():
    engine = get_engine("silent")
    assert isinstance(engine, TTSEngine)
    assert engine.name == "silent"
    assert engine.supports_inline_tags is False


def test_get_engine_unknown_raises():
    with pytest.raises(ValueError, match="Unknown TTS engine"):
        get_engine("nope")


def test_get_engine_case_insensitive():
    engine = get_engine("SILENT")
    assert engine.name == "silent"


# =====================================================================
# Silent engine end-to-end
# =====================================================================


@_requires_ffmpeg
async def test_silent_engine_e2e(tmp_path: Path):
    engine = get_engine("silent")
    text = "This is the first sentence. And this is the second."
    artifact = await engine.synthesize(text, voice="", out_dir=tmp_path)

    assert artifact.engine == "silent"
    assert artifact.path.exists()
    assert artifact.duration_s > 0
    assert len(artifact.word_timings) > 0
    # Continuidad: last word.end_s == duración total (sample-accurate boundary)
    assert abs(artifact.word_timings[-1].end_s - artifact.duration_s) < 0.05


@_requires_ffmpeg
async def test_silent_engine_respects_target_duration(tmp_path: Path):
    """Si se pasa target_duration_s, la salida lo respeta."""
    engine = get_engine("silent")
    artifact = await engine.synthesize(
        "Short text.", voice="", out_dir=tmp_path, target_duration_s=10.0,
    )
    # Permitimos un poco de slack porque ffmpeg redondea al frame.
    assert 9.5 < artifact.duration_s < 10.5


@_requires_ffmpeg
async def test_silent_engine_strips_tags_in_word_timings(tmp_path: Path):
    """Los tags inline NO deben aparecer en word_timings (silent no los habla)."""
    engine = get_engine("silent")
    text = "[curious] Hello there. [emphasis] World."
    artifact = await engine.synthesize(text, voice="", out_dir=tmp_path)

    spoken = [w.word for w in artifact.word_timings]
    assert "[curious]" not in spoken
    assert "[emphasis]" not in spoken
    assert "Hello" in spoken
    assert "World." in spoken or "World" in spoken


def test_silent_estimate_duration_min_3s():
    """Texto vacío estima al menos 3 segundos."""
    from core.tts.engines.silent import estimate_speech_duration

    assert estimate_speech_duration("") == 3.0
    assert estimate_speech_duration("   ") == 3.0


def test_silent_estimate_duration_ascii_scales():
    """Texto más largo → estima más duración."""
    from core.tts.engines.silent import estimate_speech_duration

    short = estimate_speech_duration("hello")
    long = estimate_speech_duration(
        "this is a much longer paragraph with several words "
        "and should take noticeably more time to read aloud"
    )
    assert long > short


# =====================================================================
# Edge engine (mocked at sentence level)
# =====================================================================


@_requires_ffmpeg
async def test_edge_engine_uses_sample_accurate_timing(tmp_path: Path, monkeypatch):
    """Edge engine se compone con synthesize_with_sample_accurate_timing.

    Reemplazamos `_synth_sentence_to_mp3` y `transcode_to_canonical_wav`
    para que cada "frase" produzca un WAV canónico de silencio.
    """
    from core.tts.engines import edge as edge_mod

    async def fake_mp3(sentence, voice, out_mp3, rate_str, timeout_s):
        out_mp3.parent.mkdir(parents=True, exist_ok=True)
        # Truco: escribimos un WAV silente con extensión .mp3 — luego el
        # transcode mock lo sobreescribe como WAV canónico.
        _make_silent_wav(out_mp3, 0.5)

    async def fake_transcode(in_path, out_path, sample_rate=DEFAULT_SAMPLE_RATE):
        # En tests basta con copiar el WAV (ya está canónico).
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(in_path.read_bytes())

    monkeypatch.setattr(edge_mod, "_synth_sentence_to_mp3", fake_mp3)
    monkeypatch.setattr(edge_mod, "transcode_to_canonical_wav", fake_transcode)

    engine = get_engine("edge")
    text = "One sentence. Two sentence."
    artifact = await engine.synthesize(text, "en-US-AvaNeural", tmp_path)

    assert artifact.engine == "edge"
    assert artifact.path.exists()
    assert artifact.duration_s > 0
    spoken = [w.word for w in artifact.word_timings]
    assert "One" in spoken
    assert "Two" in spoken


# =====================================================================
# Azure, MiMo, SiliconFlow, Gemini — sanidad sin red
# =====================================================================


async def test_gemini_engine_requires_api_key(tmp_path: Path, monkeypatch):
    """Sin GEMINI_API_KEY el engine falla con mensaje claro."""
    from core.tts.engines.gemini_flash import GeminiFlashEngine

    engine = GeminiFlashEngine(api_key="")
    with pytest.raises(RuntimeError, match="missing API key"):
        await engine.synthesize("hello.", "Zephyr", tmp_path)


async def test_azure_engine_requires_api_key(tmp_path: Path):
    from core.tts.engines.azure import AzureEngine

    engine = AzureEngine(api_key="", region="eastus")
    with pytest.raises(RuntimeError, match="missing API key"):
        await engine.synthesize("hello.", "en-US-AvaNeural", tmp_path)


async def test_mimo_engine_requires_api_key(tmp_path: Path):
    from core.tts.engines.mimo import MimoEngine

    engine = MimoEngine(api_key="")
    with pytest.raises(RuntimeError, match="missing API key"):
        await engine.synthesize("hello.", "Mia", tmp_path)


async def test_siliconflow_engine_requires_api_key(tmp_path: Path):
    from core.tts.engines.siliconflow import SiliconFlowEngine

    engine = SiliconFlowEngine(api_key="")
    with pytest.raises(RuntimeError, match="missing API key"):
        await engine.synthesize(
            "hello.", "FunAudioLLM/CosyVoice2-0.5B:alex", tmp_path,
        )


async def test_siliconflow_engine_rejects_bad_voice_format(tmp_path: Path):
    """Voice sin ':' debe lanzar ValueError descriptivo."""
    from core.tts.engines.siliconflow import SiliconFlowEngine

    engine = SiliconFlowEngine(api_key="dummy-key")
    with pytest.raises(ValueError, match="model:voice"):
        await engine.synthesize("hello.", "no-colons", tmp_path)
