"""Tests del loader de config."""
from __future__ import annotations

import os
from pathlib import Path

from shared.config import Config, load_config, reload_config


def test_load_defaults_when_no_toml(tmp_path: Path, monkeypatch) -> None:
    # cwd sin config.toml — debería devolver defaults
    monkeypatch.chdir(tmp_path)
    reload_config.cache_clear() if hasattr(reload_config, "cache_clear") else None
    cfg = reload_config()
    assert isinstance(cfg, Config)
    assert cfg.app.env in {"dev", "staging", "prod"}


def test_env_override_priority(monkeypatch) -> None:
    monkeypatch.setenv("CONTENIDO_ENV", "test")
    monkeypatch.setenv("MAX_CONCURRENT_TASKS", "7")
    cfg = reload_config()
    assert cfg.app.env == "test"
    assert cfg.app.max_concurrent_tasks == 7


def test_load_real_example() -> None:
    cfg = load_config()
    assert isinstance(cfg, Config)
    # config.example.toml debería existir y cargar sin error
    assert cfg.app.env in {"dev", "staging", "prod", "test"}
