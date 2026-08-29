"""Tests de `core/visual/generation/sora.py` (ADR-033).

Sora cierra el hueco de motion: con solo key de OpenAI los beats se quedaban en
ken-burns sobre stills y el guard anti-slideshow (ADR-019) lo cantaba:

    [ERROR] slideshow: 6/6 beats son estáticos (ratio 100% > umbral 60%)

Cero red: el transporte httpx va contra `MockTransport`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.visual.generation.base import VisualGenerationError
from core.visual.generation.sora import (
    VALID_SECONDS,
    SoraGenerator,
    _build_prompt,
    _clamp_seconds,
)
from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint, VideoSource

JOB_ID = "video_abc123"


@pytest.fixture
def beat() -> Beat:
    return Beat(
        idx=2, role=BeatRole.MECHANISM, text="body",
        target_duration_s=4.0, veo_duration=4,
    )


@pytest.fixture
def visual() -> BeatVisual:
    return BeatVisual(
        image_prompt="A sleep laboratory at night, EEG machine",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor="lab",
    )


@pytest.fixture
def frame(tmp_path: Path) -> Path:
    p = tmp_path / "frame-02.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"JPEG" * 30)
    return p


class _Rec:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []   # (método, path)
        self.multipart: bytes = b""
        self.polls = 0


def _api(rec: _Rec, *, statuses: list[str], video: bytes = b"MP4DATA") -> Any:
    """Simula el ciclo create → poll(N) → download."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        rec.calls.append((request.method, path))

        if request.method == "POST":
            rec.multipart = request.content
            return httpx.Response(200, json={"id": JOB_ID, "status": "queued"})

        if path.endswith("/content"):
            return httpx.Response(200, content=video)

        idx = min(rec.polls, len(statuses) - 1)
        rec.polls += 1
        status = statuses[idx]
        body: dict[str, Any] = {"id": JOB_ID, "status": status, "progress": 100 if status == "completed" else 40}
        if status == "failed":
            body["error"] = {"message": "content policy"}
        return httpx.Response(200, json=body)

    real = httpx.AsyncClient

    def fake(*a: Any, **k: Any) -> httpx.AsyncClient:
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)

    return patch("core.visual.generation.sora.httpx.AsyncClient", side_effect=fake)


def _gen(**kw: Any) -> SoraGenerator:
    kw.setdefault("api_key", "sk-test")
    kw.setdefault("poll_interval_s", 0.001)
    kw.setdefault("max_poll_time_s", 5.0)
    return SoraGenerator(**kw)


# =============================================================================
# Buckets de duración
# =============================================================================


class TestClampSeconds:
    @pytest.mark.parametrize(
        ("target", "expected"),
        [(1.0, 4), (4.0, 4), (4.1, 8), (6.0, 8), (8.0, 8), (9.0, 12), (60.0, 12)],
    )
    def test_rounds_up_to_valid_bucket(self, target: float, expected: int) -> None:
        """Hacia arriba: un clip corto deja negro; uno largo se recorta."""
        assert _clamp_seconds(target) == expected

    def test_only_api_accepted_values(self) -> None:
        # Verificado contra la API: `seconds=5` → HTTP 400 Invalid value.
        assert VALID_SECONDS == (4, 8, 12)
        assert all(_clamp_seconds(t) in VALID_SECONDS for t in (0.5, 3, 5, 7, 11, 99))


class TestPrompt:
    def test_includes_scene_and_motion(self, visual: BeatVisual) -> None:
        p = _build_prompt(visual)
        assert "sleep laboratory" in p
        assert "slow zoom in" in p  # de `_motion_clause`, reusado de Veo

    def test_static_is_explicit(self) -> None:
        v = BeatVisual(image_prompt="x", motion_hint=MotionHint.STATIC, visual_anchor="a")
        assert "static, no movement" in _build_prompt(v)

    def test_asks_for_restraint_and_no_text(self, visual: BeatVisual) -> None:
        p = _build_prompt(visual)
        assert "restrained" in p.lower()
        assert "no text" in p.lower()  # los subtítulos se queman después


# =============================================================================
# Ciclo del job
# =============================================================================


class TestGenerate:
    @pytest.mark.asyncio
    async def test_happy_path(self, beat, visual, frame, tmp_path) -> None:
        rec = _Rec()
        with _api(rec, statuses=["in_progress", "completed"]):
            art = await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)

        assert art.source == VideoSource.SORA
        assert art.video_path is not None and art.video_path.exists()
        assert art.video_path.read_bytes() == b"MP4DATA"
        assert art.first_frame_path == frame   # se propaga el frame de origen
        assert art.duration_s == 4.0

    @pytest.mark.asyncio
    async def test_sends_frame_as_multipart_reference(self, beat, visual, frame, tmp_path) -> None:
        """`input_reference` va como archivo, no como data-URL (a diferencia de Veo)."""
        rec = _Rec()
        with _api(rec, statuses=["completed"]):
            await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)

        body = rec.multipart
        assert b'name="input_reference"' in body
        assert b'name="model"' in body
        assert b"720x1280" in body   # tamaño nativo: sin recorte

    @pytest.mark.asyncio
    async def test_full_lifecycle_order(self, beat, visual, frame, tmp_path) -> None:
        rec = _Rec()
        with _api(rec, statuses=["queued", "in_progress", "completed"]):
            await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)

        methods = [m for m, _ in rec.calls]
        assert methods[0] == "POST"                     # create
        assert methods.count("GET") >= 2                # poll(s) + download
        assert rec.calls[-1][1].endswith("/content")    # descarga al final

    @pytest.mark.asyncio
    async def test_downloads_immediately(self, beat, visual, frame, tmp_path) -> None:
        """El enlace expira a 48h, así que no se guarda el id para después."""
        rec = _Rec()
        with _api(rec, statuses=["completed"]):
            art = await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)
        assert art.video_path.stat().st_size > 0


class TestFailureModes:
    @pytest.mark.asyncio
    async def test_no_key_raises_before_request(self, beat, visual, frame, tmp_path) -> None:
        rec = _Rec()
        with _api(rec, statuses=["completed"]):
            with pytest.raises(VisualGenerationError, match="OPENAI_API_KEY"):
                await _gen(api_key="").generate(beat, visual, "general", tmp_path, first_frame_path=frame)
        assert rec.calls == []

    @pytest.mark.asyncio
    async def test_missing_frame_raises(self, beat, visual, tmp_path) -> None:
        """Es i2v: sin frame no hay nada que animar."""
        rec = _Rec()
        with _api(rec, statuses=["completed"]):
            with pytest.raises(VisualGenerationError, match="first_frame_path"):
                await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=None)
        assert rec.calls == []

    @pytest.mark.asyncio
    async def test_nonexistent_frame_raises(self, beat, visual, tmp_path) -> None:
        with pytest.raises(VisualGenerationError, match="first_frame_path"):
            await _gen().generate(
                beat, visual, "general", tmp_path, first_frame_path=tmp_path / "nope.jpg"
            )

    @pytest.mark.asyncio
    async def test_failed_job_surfaces_api_message(self, beat, visual, frame, tmp_path) -> None:
        rec = _Rec()
        with _api(rec, statuses=["failed"]):
            with pytest.raises(VisualGenerationError, match="content policy"):
                await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)

    @pytest.mark.asyncio
    async def test_timeout_raises_instead_of_hanging(self, beat, visual, frame, tmp_path) -> None:
        rec = _Rec()
        with _api(rec, statuses=["in_progress"]):   # nunca termina
            with pytest.raises(VisualGenerationError, match="no terminó"):
                await _gen(max_poll_time_s=0.01).generate(
                    beat, visual, "general", tmp_path, first_frame_path=frame
                )

    @pytest.mark.asyncio
    async def test_create_http_error_raises(self, beat, visual, frame, tmp_path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        real = httpx.AsyncClient
        with patch(
            "core.visual.generation.sora.httpx.AsyncClient",
            side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        ):
            with pytest.raises(VisualGenerationError, match="429"):
                await _gen().generate(beat, visual, "general", tmp_path, first_frame_path=frame)


# =============================================================================
# Cadena de motion del orchestrator
# =============================================================================


class TestOrchestratorChain:
    @pytest.mark.asyncio
    async def test_sora_animates_when_veo_absent(self, beat, visual, frame, tmp_path) -> None:
        """El caso del ADR: sin Veo/DoP/fal, Sora evita el slideshow."""
        from core.visual.generation.ken_burns import KenBurnsGenerator
        from core.visual.generation.orchestrator import _generate_one
        from shared.schemas import BeatArtifact

        clip = tmp_path / "clip-02.mp4"
        clip.write_bytes(b"mp4")
        sora = MagicMock(spec=SoraGenerator)
        sora.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=beat.idx, first_frame_path=frame, video_path=clip,
                source=VideoSource.SORA, duration_s=4.0,
            )
        )
        kb = MagicMock(spec=KenBurnsGenerator)
        kb.generate = AsyncMock(side_effect=AssertionError("ken-burns no debió correr"))

        img = MagicMock()
        img.generate = AsyncMock(
            return_value=BeatArtifact(idx=beat.idx, first_frame_path=frame, source=VideoSource.GEMINI_IMAGE)
        )

        art = await _generate_one(
            beat, visual, tmp_path, "general",
            comfy_gen=None, soul_gen=None, image_gen=img, openai_image_gen=None,
            hf_dop_gen=None, veo_gen=None, sora_gen=sora,
            ken_burns_gen=kb, effects_gen=None,
        )
        assert art.source == VideoSource.SORA
        kb.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_ken_burns_when_sora_fails(self, beat, visual, frame, tmp_path) -> None:
        from core.visual.generation.ken_burns import KenBurnsGenerator
        from core.visual.generation.orchestrator import _generate_one
        from shared.schemas import BeatArtifact

        clip = tmp_path / "kb.mp4"
        clip.write_bytes(b"mp4")
        sora = MagicMock(spec=SoraGenerator)
        sora.generate = AsyncMock(side_effect=VisualGenerationError("job falló"))
        kb = MagicMock(spec=KenBurnsGenerator)
        kb.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=beat.idx, first_frame_path=frame, video_path=clip,
                source=VideoSource.LOCAL, duration_s=4.0,
            )
        )
        img = MagicMock()
        img.generate = AsyncMock(
            return_value=BeatArtifact(idx=beat.idx, first_frame_path=frame, source=VideoSource.GEMINI_IMAGE)
        )

        art = await _generate_one(
            beat, visual, tmp_path, "general",
            comfy_gen=None, soul_gen=None, image_gen=img, openai_image_gen=None,
            hf_dop_gen=None, veo_gen=None, sora_gen=sora,
            ken_burns_gen=kb, effects_gen=None,
        )
        assert art.video_path == clip   # degradó sin romper
        kb.generate.assert_awaited_once()
