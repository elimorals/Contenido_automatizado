"""Fixtures compartidos."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.config import Config, load_config
from shared.schemas import GenerationMode, VideoAspect, VideoParams


@pytest.fixture(autouse=True)
def _isolate_local_env(monkeypatch: pytest.MonkeyPatch):
    """Neutraliza las env vars del `.env` local que cambian comportamiento.

    `shared.config` carga el `.env` al importarse, así que sin esto la suite
    dependería de cómo tenga configurada su máquina quien la corre: un
    `FFMPEG_BINARY` apuntando a otro build haría que los tests del editor
    resuelvan un binario real en vez del mock.

    Las API keys se borran también, y no por higiene: los generadores se
    auto-instancian cuando detectan una key en config, así que con un `.env`
    poblado un test de fallback llegaba a llamar a la API de imágenes **de
    verdad** — segundos de latencia y dinero real en cada corrida de la suite.
    Sin keys, un test que toque red falla ruidosamente en vez de gastar.
    """
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)
    # Flags que cambian DECISIONES, no solo disponibilidad: con
    # HIGGSFIELD_ENABLED=true en el .env local, `_decide_source` rutea el hook
    # a IA en vez de stock y el test correspondiente falla en la máquina de
    # quien tenga la integración encendida.
    for flag in ("HIGGSFIELD_ENABLED", "SORA_ENABLED", "LONG_FORM_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    for key in (
        "OPENAI_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "UPLOAD_POST_API_KEY", "FAL_KEY",
        "HIGGSFIELD_CREDENTIALS", "PEXELS_API_KEYS", "PIXABAY_API_KEYS",
    ):
        monkeypatch.delenv(key, raising=False)
    load_config.cache_clear()  # el config cacheado ya vio las keys del .env
    yield
    load_config.cache_clear()


@pytest.fixture
def default_config() -> Config:
    return load_config()


@pytest.fixture
def sample_topic_params() -> VideoParams:
    return VideoParams(
        topic="the placebo effect",
        mode=GenerationMode.PREMIUM,
        aspect=VideoAspect.PORTRAIT,
    )


@pytest.fixture
def sample_article_params() -> VideoParams:
    return VideoParams(
        url="https://arxiv.org/abs/2509.25541",
        mode=GenerationMode.PREMIUM,
    )


@pytest.fixture
def sample_subject_params() -> VideoParams:
    return VideoParams(
        subject="Spring flowers in Tokyo",
        mode=GenerationMode.EXPRESS,
        voice_name="en-US-AvaNeural-Female",
    )


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out
