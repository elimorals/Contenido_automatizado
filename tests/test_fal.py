"""Tests para core/visual/generation/fal.py — gateway i2v fal.ai (ADR-023).

fal.ai unifica Kling/Runway/MiniMax tras una sola API. Helpers puros (mapeo de
modelo, request, parseo de respuesta) testeados offline; la llamada HTTP es wrapper fino.
"""
from __future__ import annotations

import pytest

from core.visual.generation.fal import _build_request, _model_id, _parse_response


def test_model_id_known_variants():
    assert "kling" in _model_id("kling")
    assert "runway" in _model_id("runway")
    assert "minimax" in _model_id("minimax")


def test_model_id_passthrough_full_id():
    assert _model_id("fal-ai/custom/model") == "fal-ai/custom/model"


def test_model_id_unknown_raises():
    with pytest.raises(ValueError):
        _model_id("sora")


def test_build_request_has_image_and_prompt():
    req = _build_request(prompt="a wave", image_data_url="data:image/jpeg;base64,xxx", duration_s=5)
    assert req["image_url"] == "data:image/jpeg;base64,xxx"
    assert req["prompt"] == "a wave"
    assert "5" in str(req.get("duration"))


def test_build_request_omits_duration_when_none():
    req = _build_request(prompt="x", image_data_url="d", duration_s=None)
    assert "duration" not in req


def test_parse_response_video_url():
    assert _parse_response({"video": {"url": "https://fal/out.mp4"}}) == "https://fal/out.mp4"


def test_parse_response_missing_returns_none():
    assert _parse_response({}) is None
    assert _parse_response({"video": {}}) is None
