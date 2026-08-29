"""Fixtures compartidos."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.config import Config, load_config
from shared.schemas import GenerationMode, VideoAspect, VideoParams


@pytest.fixture(autouse=True)
def _isolate_local_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutraliza las env vars del `.env` local que cambian comportamiento.

    `shared.config` carga el `.env` al importarse, así que sin esto la suite
    dependería de cómo tenga configurada su máquina quien la corre: un
    `FFMPEG_BINARY` apuntando a otro build haría que los tests del editor
    resuelvan un binario real en vez del mock. Las API keys no se tocan — los
    tests no llaman a red, y borrarlas escondería un test que sí lo hiciera.
    """
    monkeypatch.delenv("FFMPEG_BINARY", raising=False)


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
