"""Ken-burns: still image → video con zoompan via ffmpeg.

Fallback gratis cuando Veo falla o `visual.veo_enabled=False`. Acepta
un first frame + duración y produce un MP4 720×1280 con slow zoom-in.
Implementación: subprocess ffmpeg async (sin MoviePy).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from shared.config import load_config
from shared.schemas import Beat, BeatArtifact, BeatVisual, MotionHint, VideoSource

# Veo Lite native vertical res; mantenemos parity.
_CANVAS_W = 720
_CANVAS_H = 1280
_FPS = 30


def _placeholder_frame(out_path: Path, idx: int) -> Path:
    """Solid muted color con beat idx, como fallback final.

    Usado cuando image gen falla. El renderer aplica ken-burns sobre
    esto para que el reel siga teniendo algo que mostrar.

    Color basado en `idx` para distinguir visualmente entre beats.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Palette muted que rota por idx — siempre legible.
    palette = [
        (28, 28, 32),
        (40, 48, 56),
        (56, 40, 48),
        (48, 56, 40),
        (32, 48, 56),
        (56, 48, 32),
    ]
    base_color = palette[idx % len(palette)]
    accent = tuple(min(255, c + 12) for c in base_color)
    img = Image.new("RGB", (_CANVAS_W, _CANVAS_H), color=base_color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, _CANVAS_W, _CANVAS_H // 2), fill=accent)
    draw.text((40, _CANVAS_H - 80), f"beat {idx}", fill=(200, 200, 200))
    img.save(str(out_path), format="JPEG", quality=88)
    return out_path


def _build_ffmpeg_cmd(
    frame_path: Path,
    out_path: Path,
    duration_s: float,
    zoom_target: float,
) -> list[str]:
    """Construye comando ffmpeg para ken-burns.

    Aislado en función pura para testear sin ejecutar el subprocess.
    """
    dur_int = max(1, int(round(duration_s)))
    n_frames = dur_int * _FPS
    # Slow zoom-in de 1.0 a `zoom_target` durante el clip.
    # Step = (target - 1) / n_frames para llegar exacto al final.
    zoom_step = max(0.0001, (zoom_target - 1.0) / max(1, n_frames))
    zp = (
        f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom_target:.3f})':"
        f"d={n_frames}:s={_CANVAS_W}x{_CANVAS_H}:fps={_FPS}"
    )
    return [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-loop",
        "1",
        "-t",
        str(dur_int),
        "-i",
        str(frame_path),
        "-vf",
        zp,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(_FPS),
        str(out_path),
    ]


async def render_ken_burns(
    frame_path: Path,
    duration_s: float,
    out_path: Path,
    zoom_target: float | None = None,
) -> Path:
    """Renderiza frame como ken-burns video.

    El scale+crop chain produce output canvas-size aun desde un first
    frame 720×1280, y un slow zoom 1.0→`zoom_target` añade movimiento
    suficiente para que no parezca una imagen congelada.

    Lanza `VisualGenerationError` si ffmpeg falla — el caller debe
    capturar y degradar a placeholder si esto pasa.
    """
    cfg = load_config()
    z = zoom_target if zoom_target is not None else cfg.visual.ken_burns_zoom
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_ffmpeg_cmd(frame_path, out_path, duration_s, z)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-400:] if stderr else ""
        raise VisualGenerationError(
            f"ken_burns: ffmpeg failed ({proc.returncode}): {tail}"
        )
    return out_path


class KenBurnsGenerator(VisualGenerator):
    """Generador de video tipo ken-burns (zoompan) sobre un still."""

    name = "ken_burns"

    def __init__(self, zoom_target: float | None = None) -> None:
        cfg = load_config()
        self.zoom_target = (
            zoom_target if zoom_target is not None else cfg.visual.ken_burns_zoom
        )

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
        first_frame_path: Path | None = None,
    ) -> BeatArtifact:
        """Renderiza ken-burns sobre un first frame.

        Si `first_frame_path` no se provee o no existe, genera
        placeholder y aplica ken-burns sobre eso — nunca crashea.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        if first_frame_path is None or not first_frame_path.exists():
            first_frame_path = _placeholder_frame(
                out_dir / f"frame-{beat.idx:02d}-placeholder.jpg",
                beat.idx,
            )

        # Damos un poco de buffer sobre veo_duration para que el editor
        # tenga margen al cortar.
        duration = float(beat.veo_duration) + 0.5
        out_path = out_dir / f"clip-{beat.idx:02d}.mp4"

        # Slight motion hint adjustment: zoom_out usa zoom inverso.
        z = self.zoom_target
        if visual.motion_hint == MotionHint.SLOW_ZOOM_OUT:
            # zoompan no soporta zoom-out directo limpio; mantenemos zoom-in suave
            # con target menor para minimizar movimiento percibido.
            z = max(1.0, 1.0 + (self.zoom_target - 1.0) * 0.5)

        await render_ken_burns(
            frame_path=first_frame_path,
            duration_s=duration,
            out_path=out_path,
            zoom_target=z,
        )
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=first_frame_path,
            video_path=out_path,
            source=VideoSource.GEMINI_IMAGE,
            duration_s=duration,
        )
