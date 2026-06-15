"""Tests del sample-accurate timing universal."""
from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from core.tts.timing import (
    DEFAULT_SAMPLE_RATE,
    apply_atempo,
    concat_wavs,
    distribute_word_timings,
    measure_audio_duration,
    split_into_sentences,
    strip_tts_tags,
    synthesize_with_sample_accurate_timing,
    wrap_pcm16_as_wav,
)


# pyproject configura `asyncio_mode = "auto"` → tests async se detectan
# automáticamente; los sync no necesitan marker.

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
_requires_ffmpeg = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe no disponible en PATH"
)


# =====================================================================
# split_into_sentences
# =====================================================================


def test_split_into_sentences_basic():
    text = "This is one. And another! Maybe three?"
    sentences = split_into_sentences(text)
    assert sentences == ["This is one.", "And another!", "Maybe three?"]


def test_split_into_sentences_preserves_tags():
    """Los tags inline NO deben perderse en el split — viajan con la frase."""
    text = "[curious] What if we did this? [emphasis] Then look at that."
    sentences = split_into_sentences(text)
    assert len(sentences) == 2
    # El tag [curious] queda con la primera frase, [emphasis] con la segunda.
    assert "[curious]" in sentences[0]
    assert "What if we did this?" in sentences[0]
    assert "[emphasis]" in sentences[1]
    assert "Then look at that." in sentences[1]


def test_split_into_sentences_no_punctuation():
    """Texto sin punctuation termina como una sola "frase"."""
    sentences = split_into_sentences("hello world no end")
    assert sentences == ["hello world no end"]


def test_split_into_sentences_em_dash_does_not_split():
    """Em-dashes no son sentence boundaries."""
    text = "First part — second part. Final."
    assert len(split_into_sentences(text)) == 2


def test_strip_tts_tags():
    text = "[curious] What is [emphasis] going on?"
    assert strip_tts_tags(text) == "What is going on?"


# =====================================================================
# distribute_word_timings
# =====================================================================


def test_distribute_word_timings_basic():
    words = ["hello", "world"]
    timings = distribute_word_timings(words, 0.0, 1.0)
    assert len(timings) == 2
    assert timings[0].start_s == 0.0
    # Última palabra siempre cierra en end_s exacto (sample-accurate boundary)
    assert timings[-1].end_s == 1.0
    # Continuidad: end de una = start de la siguiente
    for i in range(len(timings) - 1):
        assert timings[i].end_s == timings[i + 1].start_s


def test_distribute_word_timings_syllable_weighted():
    """Palabras con más sílabas reciben más tiempo."""
    timings = distribute_word_timings(["a", "extraordinary"], 0.0, 10.0)
    a_duration = timings[0].end_s - timings[0].start_s
    extra_duration = timings[1].end_s - timings[1].start_s
    assert extra_duration > a_duration


def test_distribute_word_timings_empty():
    assert distribute_word_timings([], 0.0, 1.0) == []


def test_distribute_word_timings_degenerate_span():
    """start == end colapsa todas las palabras en ese instante."""
    timings = distribute_word_timings(["a", "b", "c"], 5.0, 5.0)
    assert len(timings) == 3
    for t in timings:
        assert t.start_s == 5.0
        assert t.end_s == 5.0


# =====================================================================
# measure_audio_duration / wrap_pcm16_as_wav / concat_wavs / apply_atempo
# =====================================================================


def _make_silent_wav(out: Path, duration_s: float, sr: int = DEFAULT_SAMPLE_RATE) -> None:
    """Helper: crea un WAV de silencio sin depender de ffmpeg."""
    out.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_s * sr)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"\x00\x00" * n_frames)


def test_wrap_pcm16_as_wav_roundtrip(tmp_path: Path):
    """wrap_pcm16_as_wav produce un WAV parseable."""
    pcm = b"\x00\x00" * 24_000  # 1 segundo @ 24kHz mono
    wav_bytes = wrap_pcm16_as_wav(pcm, sample_rate=24_000, channels=1)
    out = tmp_path / "x.wav"
    out.write_bytes(wav_bytes)
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24_000
        assert wf.getnframes() == 24_000


@_requires_ffmpeg
def test_measure_audio_duration_uses_ffprobe(tmp_path: Path):
    """measure_audio_duration devuelve la duración correcta vía ffprobe."""
    wav = tmp_path / "sine.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
            "-ac", "1", "-ar", str(DEFAULT_SAMPLE_RATE),
            "-c:a", "pcm_s16le", str(wav),
        ],
        check=True,
    )
    duration = measure_audio_duration(wav)
    assert 1.4 < duration < 1.6


def test_concat_wavs_native(tmp_path: Path):
    """concat_wavs preserva la duración total."""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    out = tmp_path / "out.wav"
    _make_silent_wav(a, 0.5)
    _make_silent_wav(b, 0.3)
    concat_wavs([a, b], out)
    with wave.open(str(out), "rb") as wf:
        assert wf.getnframes() == int(0.8 * DEFAULT_SAMPLE_RATE)


def test_concat_wavs_param_mismatch_raises(tmp_path: Path):
    """concat_wavs lanza RuntimeError si los PCM params no concuerdan."""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    out = tmp_path / "out.wav"
    _make_silent_wav(a, 0.5, sr=24_000)
    _make_silent_wav(b, 0.5, sr=44_100)
    with pytest.raises(RuntimeError, match="param mismatch"):
        concat_wavs([a, b], out)


def test_concat_wavs_empty_raises():
    with pytest.raises(ValueError):
        concat_wavs([], Path("/tmp/x.wav"))


@_requires_ffmpeg
async def test_apply_atempo_speed_up(tmp_path: Path):
    """apply_atempo con factor>1 reduce la duración."""
    src = tmp_path / "src.wav"
    out = tmp_path / "out.wav"
    _make_silent_wav(src, 2.0)
    await apply_atempo(src, out, 2.0, sample_rate=DEFAULT_SAMPLE_RATE)
    out_dur = measure_audio_duration(out)
    # 2.0 / 2.0 = ~1.0s
    assert 0.9 < out_dur < 1.1


@_requires_ffmpeg
async def test_apply_atempo_extreme_factor_cascades(tmp_path: Path):
    """Factor > 2.0 debe cascadear filtros sin romperse."""
    src = tmp_path / "src.wav"
    out = tmp_path / "out.wav"
    _make_silent_wav(src, 4.0)
    await apply_atempo(src, out, 3.5, sample_rate=DEFAULT_SAMPLE_RATE)
    out_dur = measure_audio_duration(out)
    # 4.0 / 3.5 ≈ 1.14s
    assert 1.0 < out_dur < 1.3


@_requires_ffmpeg
async def test_apply_atempo_near_unity_skips_filter(tmp_path: Path):
    """Factor en ±5% de 1.0 evita atempo (pero aún normaliza PCM)."""
    src = tmp_path / "src.wav"
    out = tmp_path / "out.wav"
    _make_silent_wav(src, 1.0)
    await apply_atempo(src, out, 1.01, sample_rate=DEFAULT_SAMPLE_RATE)
    out_dur = measure_audio_duration(out)
    # Sin atempo: duración inalterada (~1.0s)
    assert 0.95 < out_dur < 1.05


# =====================================================================
# Pipeline universal end-to-end
# =====================================================================


@_requires_ffmpeg
async def test_synthesize_with_sample_accurate_timing_e2e(tmp_path: Path):
    """Pipeline end-to-end con un synth_sentence_fn mock (silencio)."""

    async def fake_synth(idx: int, sentence: str, sub_dir: Path) -> Path:
        # Cada frase produce un WAV de duración proporcional al char count.
        target_duration = 0.5 + 0.05 * len(sentence)
        out = sub_dir / "raw.wav"
        _make_silent_wav(out, target_duration)
        return out

    text = "Hello world. This is the second one. And the third!"
    full_path, total_duration, words = await synthesize_with_sample_accurate_timing(
        text=text,
        out_dir=tmp_path,
        synth_sentence_fn=fake_synth,
        target_duration_s=None,
    )

    assert full_path.exists()
    assert total_duration > 0
    assert words, "El pipeline debe producir word_timings"
    # Continuidad sample-accurate: cada palabra encadena con la siguiente.
    for a, b in zip(words[:-1], words[1:], strict=True):
        assert a.end_s == b.start_s or abs(a.end_s - b.start_s) < 1e-6
    # La última palabra cierra dentro de ±50 ms del total medido.
    assert abs(words[-1].end_s - total_duration) < 0.05


@_requires_ffmpeg
async def test_synthesize_with_sample_accurate_timing_strips_tags(tmp_path: Path):
    """Los tags inline NO aparecen en el word_timing."""

    async def fake_synth(idx: int, sentence: str, sub_dir: Path) -> Path:
        out = sub_dir / "raw.wav"
        _make_silent_wav(out, 1.0)
        return out

    text = "[curious] Hello world. [emphasis] Bye now."
    _, _, words = await synthesize_with_sample_accurate_timing(
        text=text,
        out_dir=tmp_path,
        synth_sentence_fn=fake_synth,
    )

    spoken = [w.word for w in words]
    assert "[curious]" not in spoken
    assert "[emphasis]" not in spoken
    assert "Hello" in spoken
    assert "Bye" in spoken
