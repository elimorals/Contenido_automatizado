"""Tests para core/visual/generation: Gemini Image + Veo + ken-burns.

Cobertura mínima:
1. ken_burns: cmd ffmpeg correcto
2. placeholder cuando todo falla
3. two-tier fallback (image fail → placeholder + ken-burns)
4. style block per content_mode
5. center-crop dimensions
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from core.visual.generation import gemini_image as gi_mod
from core.visual.generation import orchestrator as orch_mod
from core.visual.generation import veo as veo_mod
from core.visual.generation.gemini_image import (
    GeminiImageGenerator,
    _augment,
    _crop_to_9x16,
    _style_note,
)
from core.visual.generation.ken_burns import (
    KenBurnsGenerator,
    _build_ffmpeg_cmd,
    _placeholder_frame,
    render_ken_burns,
)
from core.visual.generation.orchestrator import generate_beat_videos
from core.visual.generation.veo import VeoGenerator, _motion_clause
from shared.schemas import (
    Beat,
    BeatRole,
    BeatVisual,
    MotionHint,
    VideoSource,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def sample_beat() -> Beat:
    return Beat(
        idx=0,
        role=BeatRole.HOOK,
        text="opening line",
        target_duration_s=2.5,
        veo_duration=4,
    )


@pytest.fixture
def sample_visual() -> BeatVisual:
    return BeatVisual(
        image_prompt="a scientist staring at a glowing screen",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor="glowing screen",
    )


@pytest.fixture
def fake_jpeg(tmp_path: Path) -> Path:
    """Crea un JPEG fake 720×1280 para pasarle a ken-burns."""
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (720, 1280), color=(50, 60, 70)).save(p, "JPEG", quality=85)
    return p


@pytest.fixture
def fake_square_png(tmp_path: Path) -> Path:
    """Imagen cuadrada 1024×1024 (lo que devuelve Gemini Image raw)."""
    p = tmp_path / "raw.png"
    Image.new("RGB", (1024, 1024), color=(120, 130, 140)).save(p, "PNG")
    return p


# =============================================================================
# TEST 1: ken_burns construye comando ffmpeg correcto
# =============================================================================


def test_ken_burns_builds_correct_ffmpeg_command(tmp_path: Path) -> None:
    """`_build_ffmpeg_cmd` debe producir comando con zoompan + tamaño 720×1280."""
    frame = tmp_path / "in.jpg"
    out = tmp_path / "out.mp4"
    cmd = _build_ffmpeg_cmd(frame, out, duration_s=4.0, zoom_target=1.06)

    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-loop" in cmd and "1" in cmd
    assert "-t" in cmd
    # zoompan filter present con dimensiones nativas Veo
    vf_idx = cmd.index("-vf")
    vf = cmd[vf_idx + 1]
    assert vf.startswith("zoompan=")
    assert "720x1280" in vf
    assert "fps=30" in vf
    # codec y pix_fmt esperados
    assert "libx264" in cmd
    assert "yuv420p" in cmd
    # input y output paths
    assert str(frame) in cmd
    assert str(out) in cmd


@pytest.mark.asyncio
async def test_render_ken_burns_invokes_subprocess(
    fake_jpeg: Path, tmp_path: Path
) -> None:
    """`render_ken_burns` debe llamar create_subprocess_exec con cmd ffmpeg."""
    out = tmp_path / "clip.mp4"

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch(
        "core.visual.generation.ken_burns.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=mock_proc),
    ) as mock_spawn:
        result = await render_ken_burns(
            frame_path=fake_jpeg, duration_s=4.0, out_path=out, zoom_target=1.06
        )

    assert result == out
    args = mock_spawn.call_args[0]
    assert args[0] == "ffmpeg"
    assert "-vf" in args
    # zoompan en el siguiente arg después de -vf
    vf_arg = args[args.index("-vf") + 1]
    assert "zoompan" in vf_arg


# =============================================================================
# TEST 2: placeholder cuando todo falla
# =============================================================================


def test_placeholder_generates_valid_image(tmp_path: Path) -> None:
    """Placeholder debe generar un JPEG válido 720×1280 cuando todo falla."""
    out = tmp_path / "ph.jpg"
    result = _placeholder_frame(out, idx=3)
    assert result.exists()
    img = Image.open(result)
    assert img.size == (720, 1280)
    assert img.mode == "RGB"


def test_placeholder_color_varies_by_idx(tmp_path: Path) -> None:
    """Distintos idx producen distintos placeholders (palette rotativa)."""
    p0 = _placeholder_frame(tmp_path / "p0.jpg", idx=0)
    p1 = _placeholder_frame(tmp_path / "p1.jpg", idx=1)
    img0 = Image.open(p0).convert("RGB")
    img1 = Image.open(p1).convert("RGB")
    # Pixel central del bloque inferior (debajo del rectangulo top)
    px0 = img0.getpixel((10, 1200))
    px1 = img1.getpixel((10, 1200))
    assert px0 != px1


# =============================================================================
# TEST 3: two-tier fallback (image fail → ken-burns sobre placeholder)
# =============================================================================


@pytest.mark.asyncio
async def test_two_tier_fallback_image_fails_then_ken_burns(
    sample_beat: Beat, sample_visual: BeatVisual, tmp_path: Path
) -> None:
    """Si Gemini Image falla → placeholder. Si Veo deshabilitado → ken-burns."""
    # Mock image gen que falla siempre
    failing_image = MagicMock(spec=GeminiImageGenerator)
    failing_image.generate = AsyncMock(side_effect=RuntimeError("api down"))

    # Mock ken-burns que escribe un fake MP4
    async def fake_ken_burns_generate(beat, visual, content_mode, out_dir, first_frame_path=None):
        # Verifica que se pasó un first_frame_path (placeholder)
        assert first_frame_path is not None
        assert first_frame_path.exists()
        clip = out_dir / f"clip-{beat.idx:02d}.mp4"
        clip.write_bytes(b"fake mp4")
        from shared.schemas import BeatArtifact
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=clip,
            source=VideoSource.GEMINI_IMAGE,
            duration_s=float(beat.veo_duration),
        )

    kb_gen = MagicMock(spec=KenBurnsGenerator)
    kb_gen.generate = AsyncMock(side_effect=fake_ken_burns_generate)

    artifacts = await generate_beat_videos(
        beats=[sample_beat],
        visuals=[sample_visual],
        out_dir=tmp_path,
        content_mode="general",
        use_veo=False,
        image_gen=failing_image,
        ken_burns_gen=kb_gen,
    )

    assert len(artifacts) == 1
    a = artifacts[0]
    assert a.video_path is not None
    assert a.video_path.exists()
    # Source debe marcarse como LOCAL porque el frame era placeholder
    assert a.source == VideoSource.LOCAL
    # Ken-burns fue invocado
    kb_gen.generate.assert_called_once()


@pytest.mark.asyncio
async def test_two_tier_fallback_veo_fails_uses_ken_burns(
    sample_beat: Beat, sample_visual: BeatVisual, tmp_path: Path, fake_jpeg: Path
) -> None:
    """Image OK → Veo falla → ken-burns sobre frame real."""
    from shared.schemas import BeatArtifact

    # Image gen que devuelve un frame real
    img_gen = MagicMock(spec=GeminiImageGenerator)

    async def fake_img(beat, visual, content_mode, out_dir):
        target = out_dir / f"frame-{beat.idx:02d}.jpg"
        target.write_bytes(fake_jpeg.read_bytes())
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=target,
            video_path=None,
            source=VideoSource.GEMINI_IMAGE,
        )

    img_gen.generate = AsyncMock(side_effect=fake_img)

    # Veo que falla
    veo_gen = MagicMock(spec=VeoGenerator)
    veo_gen.generate = AsyncMock(side_effect=RuntimeError("veo timeout"))

    # Ken-burns OK
    async def fake_kb(beat, visual, content_mode, out_dir, first_frame_path=None):
        clip = out_dir / f"clip-{beat.idx:02d}.mp4"
        clip.write_bytes(b"fake mp4")
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=clip,
            source=VideoSource.GEMINI_IMAGE,
            duration_s=float(beat.veo_duration),
        )

    kb_gen = MagicMock(spec=KenBurnsGenerator)
    kb_gen.generate = AsyncMock(side_effect=fake_kb)

    artifacts = await generate_beat_videos(
        beats=[sample_beat],
        visuals=[sample_visual],
        out_dir=tmp_path,
        content_mode="general",
        use_veo=True,
        image_gen=img_gen,
        veo_gen=veo_gen,
        ken_burns_gen=kb_gen,
    )

    assert len(artifacts) == 1
    a = artifacts[0]
    assert a.video_path is not None
    # Source mantiene GEMINI_IMAGE porque el frame fue real
    assert a.source == VideoSource.GEMINI_IMAGE
    veo_gen.generate.assert_called_once()
    kb_gen.generate.assert_called_once()


# =============================================================================
# TEST 4: style block per content_mode
# =============================================================================


def test_style_note_general_vs_scientific() -> None:
    """`_style_note` debe variar por content_mode con strings EXACTOS."""
    general = _style_note("general")
    scientific = _style_note("scientific")
    assert "cinematic documentary still" in general
    assert "warm natural light" in general
    assert "35mm film grain" in general
    assert "VERTICAL portrait" in general

    assert "documentary photograph" in scientific
    assert "research lab" in scientific
    assert "no film grain" in scientific  # explicit
    assert "VERTICAL portrait" in scientific

    # Modos distintos → bloques distintos
    assert general != scientific
    # Default fallback
    assert _style_note("unknown_mode") == general


def test_augment_prefixes_prompt_and_appends_style() -> None:
    """`_augment` debe limpiar trailing dot y appendear style note."""
    out = _augment("a glowing screen.", content_mode="general")
    assert out.startswith("a glowing screen.")
    assert "cinematic documentary still" in out

    out_sci = _augment("a microscope", content_mode="scientific")
    assert "documentary photograph" in out_sci
    assert "no film grain" in out_sci


# =============================================================================
# TEST 5: center-crop dimensions
# =============================================================================


def test_crop_to_9x16_produces_720x1280(
    fake_square_png: Path, tmp_path: Path
) -> None:
    """Un PNG 1024×1024 debe quedar exactamente 720×1280 tras crop+resize."""
    dest = tmp_path / "cropped.jpg"
    result = _crop_to_9x16(fake_square_png, dest, target_w=720)
    img = Image.open(result)
    assert img.size == (720, 1280)
    assert img.mode == "RGB"


def test_crop_handles_already_vertical(tmp_path: Path) -> None:
    """Una imagen 9:16 ya correcta debe seguir saliendo 720×1280."""
    src = tmp_path / "vertical.png"
    Image.new("RGB", (1080, 1920), color=(80, 80, 80)).save(src, "PNG")
    dest = tmp_path / "out.jpg"
    result = _crop_to_9x16(src, dest, target_w=720)
    img = Image.open(result)
    assert img.size == (720, 1280)


# =============================================================================
# EXTRA: motion clause mapping
# =============================================================================


def test_motion_clause_static_explicit() -> None:
    """`static` debe emitir phrasing explícito anti-drift."""
    assert _motion_clause(MotionHint.STATIC) == "Camera: static, no movement"


def test_motion_clause_other_hints() -> None:
    """Otros hints se appendean como 'Camera: <hint>' con _ → espacio."""
    assert _motion_clause(MotionHint.SLOW_ZOOM_IN) == "Camera: slow zoom in"
    assert _motion_clause(MotionHint.PAN_LEFT) == "Camera: pan left"
    assert _motion_clause(MotionHint.KEN_BURNS) == "Camera: ken burns"
