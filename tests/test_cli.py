"""Tests del CLI Typer (apps.cli.main).

Mockea `apps.api.pipeline.run_pipeline` para evitar invocaciones reales al
DAG / TTS / ffmpeg. Verifica:
  - Comandos topic / article / subject construyen VideoParams correctos.
  - Validación de URL.
  - config-check no falla.
  - Pipeline failure → exit code 1.
"""
from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from apps.cli.main import app
from shared.schemas import (
    GenerationMode,
    TaskInfo,
    TaskState,
    VideoAspect,
    VideoParams,
    VisualStrategy,
)

runner = CliRunner()


# =============================================================================
# Helpers
# =============================================================================


def _fake_complete(task_id: str, params: VideoParams) -> TaskInfo:
    """TaskInfo COMPLETE simulado."""
    return TaskInfo(
        task_id=task_id,
        state=TaskState.COMPLETE,
        mode=params.mode,
        progress=100,
        videos=[f"/tmp/output/{task_id}/final.mp4"],
        timings_s={"total": 12.3, "tts": 1.0, "stitch": 2.0},
    )


def _make_run_pipeline_mock(capture: dict):
    """Devuelve un AsyncMock que captura params y persiste un TaskInfo COMPLETE."""

    async def _fake(params, task_id, state_manager):
        capture["params"] = params
        capture["task_id"] = task_id
        # Inicializar TaskInfo en el state así `state.get(task_id)` del CLI lo encuentra.
        info = TaskInfo(
            task_id=task_id,
            state=TaskState.PROCESSING,
            mode=params.mode,
            progress=0,
            params=params,
        )
        await state_manager.set(task_id, info)
        final = _fake_complete(task_id, params)
        await state_manager.update(
            task_id,
            state=TaskState.COMPLETE,
            progress=100,
            videos=final.videos,
            timings_s=final.timings_s,
        )
        return final

    return _fake


def _make_run_pipeline_fail():
    """Mock que falla durante la ejecución."""

    async def _fake(params, task_id, state_manager):
        info = TaskInfo(
            task_id=task_id,
            state=TaskState.PROCESSING,
            mode=params.mode,
            progress=0,
            params=params,
        )
        await state_manager.set(task_id, info)
        await state_manager.update(
            task_id,
            state=TaskState.FAILED,
            timings_s={"error": "tts: boom"},
        )
        raise RuntimeError("tts: boom")

    return _fake


# =============================================================================
# topic
# =============================================================================


def test_topic_invokes_pipeline_with_correct_params(tmp_path):
    capture: dict = {}
    with patch(
        "apps.cli.main.run_pipeline", side_effect=_make_run_pipeline_mock(capture)
    ):
        result = runner.invoke(
            app,
            [
                "topic",
                "the placebo effect",
                "--mode",
                "premium",
                "--aspect",
                "9:16",
                "--strategy",
                "hybrid",
                "--output",
                str(tmp_path / "out"),
                "--quiet",
            ],
        )

    assert result.exit_code == 0, result.output
    params: VideoParams = capture["params"]
    assert params.topic == "the placebo effect"
    assert params.mode == GenerationMode.PREMIUM
    assert params.aspect == VideoAspect.PORTRAIT
    assert params.visual_strategy == VisualStrategy.HYBRID
    assert params.url is None
    assert params.subject is None


# =============================================================================
# article
# =============================================================================


def test_article_invokes_pipeline_with_url(tmp_path):
    capture: dict = {}
    with patch(
        "apps.cli.main.run_pipeline", side_effect=_make_run_pipeline_mock(capture)
    ):
        result = runner.invoke(
            app,
            [
                "article",
                "https://example.com/post",
                "--mode",
                "premium",
                "--output",
                str(tmp_path / "out"),
                "--quiet",
            ],
        )

    assert result.exit_code == 0, result.output
    params: VideoParams = capture["params"]
    assert params.url == "https://example.com/post"
    assert params.topic is None
    assert params.subject is None


def test_article_rejects_invalid_url():
    result = runner.invoke(
        app,
        [
            "article",
            "not-a-url",
            "--quiet",
        ],
    )
    assert result.exit_code == 2
    # El stderr captured va a output con mix_stderr=True por default
    assert "URL inválida" in result.output or "URL invalida" in result.output


# =============================================================================
# subject
# =============================================================================


def test_subject_uses_express_mode_by_default(tmp_path):
    capture: dict = {}
    with patch(
        "apps.cli.main.run_pipeline", side_effect=_make_run_pipeline_mock(capture)
    ):
        result = runner.invoke(
            app,
            [
                "subject",
                "Tokyo spring flowers",
                "--output",
                str(tmp_path / "out"),
                "--quiet",
            ],
        )

    assert result.exit_code == 0, result.output
    params: VideoParams = capture["params"]
    assert params.subject == "Tokyo spring flowers"
    assert params.mode == GenerationMode.EXPRESS
    # default voice_name del comando subject
    assert "AvaNeural" in params.voice_name


# =============================================================================
# config-check
# =============================================================================


def test_config_check_runs_without_error():
    result = runner.invoke(app, ["config-check"])
    assert result.exit_code == 0, result.output
    # Estructura esperada del output
    assert "Configuración" in result.output or "Configuracion" in result.output
    assert "LLM Providers" in result.output
    assert "TTS Engines" in result.output
    assert "Stock Providers" in result.output
    assert "Visual" in result.output


# =============================================================================
# list-voices
# =============================================================================


def test_list_voices_default_engine():
    result = runner.invoke(app, ["list-voices"])
    assert result.exit_code == 0, result.output
    assert "edge" in result.output.lower()
    # debería mostrar al menos una voz Edge
    assert "Neural" in result.output


def test_list_voices_invalid_engine():
    result = runner.invoke(app, ["list-voices", "--engine", "no-such-engine"])
    assert result.exit_code == 2


# =============================================================================
# Pipeline failure → exit code 1
# =============================================================================


def test_pipeline_failure_exits_with_code_1(tmp_path):
    with patch("apps.cli.main.run_pipeline", side_effect=_make_run_pipeline_fail()):
        result = runner.invoke(
            app,
            [
                "topic",
                "anything",
                "--quiet",
                "--output",
                str(tmp_path / "out"),
            ],
        )
    assert result.exit_code == 1
    # Debe mostrar mensaje de fallo (no traceback raw obligatorio)
    assert "fail" in result.output.lower() or "boom" in result.output.lower()


# =============================================================================
# task command (sin Redis → error claro)
# =============================================================================


def test_task_without_redis_errors_cleanly():
    # load_config con cache: aseguramos que el flag enable_redis sea False (default)
    result = runner.invoke(app, ["task", "some-id"])
    # Debe salir con code 1 y mensaje claro
    assert result.exit_code == 1
    assert "Redis" in result.output
