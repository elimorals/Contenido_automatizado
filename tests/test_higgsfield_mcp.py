"""Tests de `core/visual/generation/higgsfield_mcp.py` (ADR-037).

El MCP está diseñado para un asistente conversacional, no para un pipeline:
responde **en prosa** donde uno esperaría JSON, pregunta cosas a mitad de una
generación, y manda keepalives dentro del SSE. Cada test de aquí fija una de
esas rarezas, todas observadas contra el servidor real.
"""
from __future__ import annotations

from typing import Any

import pytest

from core.visual.generation.base import VisualGenerationError
from core.visual.generation.higgsfield_mcp import (
    HiggsfieldMCPClient,
    _declined_preset_id,
    _extract_video_url,
    _job_id_from,
    _job_state,
)


class _Resp:
    """Respuesta httpx mínima para probar el parser SSE."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200


class TestSSEParsing:
    def test_plain_json(self) -> None:
        assert HiggsfieldMCPClient._parse(_Resp('{"result": {"ok": 1}}')) == {"result": {"ok": 1}}

    def test_single_sse_event(self) -> None:
        out = HiggsfieldMCPClient._parse(_Resp('event: message\ndata: {"result": {"a": 2}}'))
        assert out["result"]["a"] == 2

    def test_keepalive_before_payload(self) -> None:
        """Las llamadas largas mandan `: keepalive` antes del data real.

        Cortar por el primer `data: ` fallaba aquí — era el bug que hacía
        ilegible una respuesta de `job_status` que sí había llegado bien.
        """
        raw = ': keepalive\n\nevent: message\ndata: {"result": {"done": true}}'
        assert HiggsfieldMCPClient._parse(_Resp(raw))["result"]["done"] is True

    def test_takes_last_payload(self) -> None:
        raw = 'data: {"result": {"n": 1}}\n\ndata: {"result": {"n": 2}}'
        assert HiggsfieldMCPClient._parse(_Resp(raw))["result"]["n"] == 2

    def test_no_json_raises(self) -> None:
        with pytest.raises(VisualGenerationError, match="ilegible"):
            HiggsfieldMCPClient._parse(_Resp("no soy json"))


class TestProseParsers:
    """El MCP contesta en prosa; hay que extraer los datos del texto."""

    def test_job_id_from_structured(self) -> None:
        assert _job_id_from({"job_id": "abc"}) == "abc"

    def test_job_id_from_prose(self) -> None:
        text = (
            "Note: adjustments applied — duration: 12.\n\nSubmitted 1 job.\n"
            '- c5426fee-2f07-4d6a-b6e1-288acc67ec3d  "un prompt"'
        )
        assert _job_id_from({"text": text}) == "c5426fee-2f07-4d6a-b6e1-288acc67ec3d"

    def test_job_id_absent(self) -> None:
        assert _job_id_from({"text": "algo sin uuid"}) is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Job abc — completed\nhttps://x/y.mp4", "completed"),
            ("Job abc — in_progress", "in_progress"),
            ("Job abc — failed", "failed"),
        ],
    )
    def test_job_state_from_prose(self, text: str, expected: str) -> None:
        """`job_status` no trae campo `status`.

        Asumir la clave hacía que un job COMPLETADO se sondeara hasta agotar el
        timeout de 600s — el vídeo estaba listo y el cliente no se enteraba.
        """
        assert _job_state({"text": text}) == expected

    def test_structured_status_wins(self) -> None:
        assert _job_state({"status": "COMPLETED", "text": "Job x — failed"}) == "completed"

    def test_video_url_from_prose(self) -> None:
        payload = {"text": "Job abc — completed\nhttps://cdn.example/user/hf_123.mp4"}
        assert _extract_video_url(payload) == "https://cdn.example/user/hf_123.mp4"

    def test_video_url_from_nested_json(self) -> None:
        payload: dict[str, Any] = {"generation": {"outputs": [{"url": "https://c/x.mp4"}]}}
        assert _extract_video_url(payload) == "https://c/x.mp4"

    def test_video_url_missing_raises(self) -> None:
        with pytest.raises(VisualGenerationError, match="sin URL"):
            _extract_video_url({"text": "Job abc — completed"})


class TestPresetInterception:
    """Si el prompt se parece a un preset, el MCP pregunta en vez de generar."""

    def test_detects_and_extracts_preset_id(self) -> None:
        text = (
            'Notice: This prompt looks like the Higgsfield preset "Earth zoom in". '
            "Ask the user whether to use that preset or generate literally.\n\n"
            "Preset id: f7561a2d-7556-4aed-b21d-2ff07a28326a\n"
            'To generate literally, retry with declined_preset_id: "f7561a2d-7556-4aed-b21d-2ff07a28326a".'
        )
        assert _declined_preset_id({"text": text}) == "f7561a2d-7556-4aed-b21d-2ff07a28326a"

    def test_normal_response_is_not_a_preset_prompt(self) -> None:
        assert _declined_preset_id({"text": "Submitted 1 job.\n- abc"}) is None

    def test_structured_response_is_not_a_preset_prompt(self) -> None:
        assert _declined_preset_id({"job_id": "abc"}) is None


class TestTokenSource:
    def test_missing_server_raises_actionable_error(self, monkeypatch) -> None:
        import json as _json

        class _Done:
            stdout = _json.dumps({"mcpOAuth": {"otro|hash": {"accessToken": "x"}}})

        monkeypatch.setattr(
            "core.visual.generation.higgsfield_mcp.subprocess.run",
            lambda *a, **k: _Done(),
        )
        from core.visual.generation.higgsfield_mcp import _read_oauth_entry

        with pytest.raises(VisualGenerationError, match="/mcp"):
            _read_oauth_entry("higgsfield")

    def test_finds_entry_despite_hash_suffix(self, monkeypatch) -> None:
        import json as _json

        class _Done:
            stdout = _json.dumps(
                {"mcpOAuth": {"higgsfield|eaa560e1": {"accessToken": "t", "serverUrl": "u"}}}
            )

        monkeypatch.setattr(
            "core.visual.generation.higgsfield_mcp.subprocess.run",
            lambda *a, **k: _Done(),
        )
        from core.visual.generation.higgsfield_mcp import _read_oauth_entry

        assert _read_oauth_entry("higgsfield")["accessToken"] == "t"


class TestGeneratorGuards:
    @pytest.mark.asyncio
    async def test_requires_first_frame(self, tmp_path) -> None:
        from core.visual.generation.higgsfield_mcp import HiggsfieldMCPGenerator
        from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint

        beat = Beat(idx=0, role=BeatRole.HOOK, text="t", target_duration_s=4.0, veo_duration=4)
        visual = BeatVisual(image_prompt="p", motion_hint=MotionHint.STATIC, visual_anchor="a")
        with pytest.raises(VisualGenerationError, match="first_frame_path"):
            await HiggsfieldMCPGenerator().generate(
                beat, visual, "general", tmp_path, first_frame_path=None
            )
