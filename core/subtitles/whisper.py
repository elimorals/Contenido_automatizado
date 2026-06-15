"""Whisper transcription fallback — port de MoneyPrinterTurbo/services/subtitle.py.

Usa `faster-whisper` con word_timestamps + VAD para extraer `WordTiming[]`
desde un archivo de audio. Solo se usa cuando NO tenemos sample-accurate
timings del TTS (Edge / Gemini Flash sí los devuelven nativamente).

Lazy import (faster_whisper es ~heavy deps con CTranslate2 + cuDNN/CUDA):
nunca importamos al top del módulo, solo dentro de `transcribe()`. Esto
permite que `core.subtitles` cargue en entornos donde faster-whisper no
está instalado (CI mínimo, web-only deploy).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from shared.config import load_config
from shared.schemas import WordTiming


@lru_cache(maxsize=2)
def _load_model(
    model_size: str,
    device: str,
    compute_type: str,
) -> Any:
    """Carga el WhisperModel una sola vez por (size, device, compute_type).

    El `lru_cache` evita re-cargar el modelo cada `transcribe()` — clave
    cuando procesamos batches de varios audios sin reiniciar el worker.
    """
    # Lazy import: solo aquí, NUNCA al top-level. Falla con ImportError clara
    # si faster-whisper no está instalado.
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    logger.info(
        f"loading whisper model: size={model_size}, device={device}, "
        f"compute_type={compute_type}"
    )
    return WhisperModel(
        model_size_or_path=model_size,
        device=device,
        compute_type=compute_type,
    )


def transcribe(
    audio_path: Path,
    model_size: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
) -> list[WordTiming]:
    """Transcribe `audio_path` y devuelve `WordTiming[]` palabra-a-palabra.

    Parámetros (defaults vienen de `cfg.whisper`):
      * model_size: "large-v3" (default), "medium", "small", "tiny"…
      * device: "cpu" | "cuda" | "auto"
      * compute_type: "int8" (CPU), "float16" (GPU), "int8_float16"…

    Si faster-whisper no está instalado registramos warning y devolvemos
    lista vacía (caller decide qué hacer — típicamente, omitir subs).
    """
    cfg = load_config().whisper
    size = model_size or cfg.model_size
    dev = device or cfg.device
    ctype = compute_type or cfg.compute_type

    try:
        model = _load_model(size, dev, ctype)
    except ImportError:
        logger.warning(
            "faster_whisper no instalado — saltando transcripción Whisper. "
            "Instala con `uv pip install faster-whisper` para habilitarla."
        )
        return []
    except Exception as e:  # noqa: BLE001 — log + degrade graceful
        logger.error(f"failed to load whisper model: {e}")
        return []

    logger.info(f"transcribing: {audio_path}")
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=cfg.beam_size,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": cfg.vad_threshold_ms},
    )
    logger.info(
        f"detected language: '{info.language}' "
        f"(prob={info.language_probability:.2f})"
    )

    word_timings: list[WordTiming] = []
    for segment in segments:
        if not segment.words:
            continue
        for w in segment.words:
            # faster_whisper Word: .word, .start, .end, .probability
            txt = (w.word or "").strip()
            if not txt:
                continue
            word_timings.append(
                WordTiming(
                    word=txt,
                    start_s=float(max(0.0, w.start or 0.0)),
                    end_s=float(max(w.start or 0.0, w.end or 0.0)),
                )
            )

    logger.info(f"whisper produced {len(word_timings)} word timings")
    return word_timings


def is_available() -> bool:
    """¿Está instalado faster-whisper? Útil para health-checks / UI gating."""
    try:
        import importlib

        importlib.import_module("faster_whisper")
        return True
    except ImportError:
        return False


# Re-export `correct` desde srt para mantener la API de MPT (los usuarios de
# `subtitle.correct(srt, script)` esperan encontrarlo aquí o en un punto
# central).
from core.subtitles.srt import correct  # noqa: E402, F401  — re-export


__all__ = [
    "correct",
    "is_available",
    "transcribe",
]
