"""Single-pass ffmpeg stitch — concat + libass + BGM mix + AAC mux.

Portado de `reels-af/render/stitch.py` (~302 LOC) y extendido con:
  • Multi-aspect (de MPT).
  • Hardware encoder detection + fallback (de MPT).
  • BGM mix opcional (de MPT, sin MoviePy).

REGLA CRÍTICA: UNA sola invocación de ffmpeg. El filter_complex hace
concat + ass burn + audio mix juntos. Razones (heredadas de reels-af):
  • Concat filter es sample-accurate; sin drift sub-frame entre beats.
  • AAC encoder priming ocurre UNA vez para todo el reel.
  • Subtítulos burn con libass a resolución de canvas (no upscale).
  • BGM mix en el mismo grafo evita re-encode innecesario.

Si quieres facilitar la legibilidad del comando enorme, usar
`FFmpegCommandBuilder` (al final del archivo) — es un builder pattern
que ensambla inputs, filter graph y output args paso a paso.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from shared.config import Config, load_config
from shared.schemas import BeatArtifact, VideoAspect

from .aspect import build_scale_filter, get_dimensions
from .bgm import build_bgm_mix_filter
from .encoders import detect_hw_encoder, get_encoder_args, mark_encoder_failed

logger = logging.getLogger(__name__)


# ───── ffmpeg path escaping ──────────────────────────────────────────


def _ffmpeg_path_arg(path: Path | str) -> str:
    """Escapa un path para uso en filter-graph args.

    Filter-graph trata `:` como separador KV y `\\` como escape. Paths
    con dos puntos (raros, pero pasan en macOS Time Machine) necesitan
    ambos escapes.
    """
    return str(path).replace("\\", "\\\\").replace(":", r"\:")


# ───── Builder pattern para mantener la legibilidad ─────────────────


@dataclass
class FFmpegCommandBuilder:
    """Construye el comando ffmpeg single-pass paso a paso.

    Uso típico:

        builder = FFmpegCommandBuilder(out_path=out)
        builder.add_concat_list_input(filelist)
        builder.add_input(audio_path)
        if bgm: builder.add_input(bgm)
        builder.add_filter_complex(...)
        builder.set_video_encoder("h264_videotoolbox", crf=23)
        cmd = builder.build()
    """

    out_path: Path
    inputs: list[list[str]] = field(default_factory=list)  # cada input son sus args ffmpeg
    filter_complex: str = ""
    video_map: str = "[v]"
    audio_map: str = "[a]"
    video_codec_args: list[str] = field(default_factory=list)
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    fps: int = 30
    extra_args: list[str] = field(default_factory=list)

    def add_input(self, path: Path | str, *pre_args: str) -> int:
        """Añade un input file. `pre_args` van antes de `-i` (ej: -ss, -t)."""
        args = list(pre_args) + ["-i", str(path)]
        self.inputs.append(args)
        return len(self.inputs) - 1

    def add_concat_list_input(self, list_path: Path | str) -> int:
        """Añade input vía concat demuxer (filelist `file 'X'`)."""
        return self.add_input(list_path, "-f", "concat", "-safe", "0")

    def add_filter_complex(self, graph: str) -> None:
        self.filter_complex = graph

    def set_video_encoder(self, codec: str, *, crf: int, preset: str) -> None:
        self.video_codec_args = get_encoder_args(codec, crf=crf, preset=preset)

    def build(self) -> list[str]:
        cmd: list[str] = ["ffmpeg", "-y", "-loglevel", "error", "-nostdin"]
        for in_args in self.inputs:
            cmd.extend(in_args)
        if self.filter_complex:
            cmd += ["-filter_complex", self.filter_complex]
        cmd += ["-map", self.video_map, "-map", self.audio_map]
        cmd += self.video_codec_args
        cmd += [
            "-c:a", self.audio_codec,
            "-b:a", self.audio_bitrate,
            "-ar", "44100",
            "-r", str(self.fps),
            "-shortest",
            "-movflags", "+faststart",
        ]
        cmd += self.extra_args
        cmd.append(str(self.out_path))
        return cmd


# ───── Subprocess driver con retry de encoder ───────────────────────


async def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """Ejecuta ffmpeg async, captura stderr (último 1.5KB)."""
    logger.debug("ffmpeg: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    stderr_text = stderr.decode(errors="replace")[-1500:] if stderr else ""
    return proc.returncode or 0, stderr_text


# ───── Public single-pass entry point ───────────────────────────────


async def stitch_video(
    artifacts: list[BeatArtifact],
    audio_path: Path,
    subtitle_ass_path: Path | None,
    out_dir: Path,
    aspect: VideoAspect = VideoAspect.PORTRAIT,
    *,
    bgm_path: Path | None = None,
    bgm_volume: float = 0.2,
    voice_volume: float = 1.0,
    out_name: str = "reel.mp4",
    config: Config | None = None,
) -> Path:
    """Single-pass stitch — concat + ASS burn + voice + BGM en UN comando.

    Args:
        artifacts: lista de BeatArtifact con video_path resuelto.
        audio_path: full TTS WAV (voz narrada completa).
        subtitle_ass_path: ASS file global (opcional; None = sin burn).
        out_dir: directorio de salida.
        aspect: VideoAspect target (afecta dimensiones del filter).
        bgm_path: archivo BGM opcional (de `bgm.pick_bgm`).
        bgm_volume: peso de BGM en el amix (0.0-1.0).
        voice_volume: peso de la voz en el amix.
        out_name: nombre del archivo final.
        config: override de config (None → load_config()).

    Returns:
        Path al video final.

    Raises:
        RuntimeError si ffmpeg no está en PATH o si todos los encoders fallan.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg no encontrado en PATH.")

    cfg = config or load_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / out_name

    # Validar artifacts.
    missing = [a.idx for a in artifacts if a.video_path is None]
    if missing:
        raise RuntimeError(f"BeatArtifacts sin video_path: idx={missing}")
    if len(artifacts) == 0:
        raise RuntimeError("stitch: no hay artifacts para ensamblar.")

    # 1. Filelist concat demuxer (más robusto que `concat` filter para N inputs).
    filelist_path = out_dir / "_concat_list.txt"
    with filelist_path.open("w", encoding="utf-8") as fp:
        for art in artifacts:
            # ffmpeg concat demuxer: single quotes around path; escape internas.
            assert art.video_path is not None
            abs_p = str(Path(art.video_path).resolve()).replace("'", "'\\''")
            fp.write(f"file '{abs_p}'\n")

    # 2. Filter complex — scale → (subtitles) → labeled video out.
    scale = build_scale_filter(aspect, fps=cfg.editor.fps, config=cfg)
    if subtitle_ass_path is not None:
        ass_arg = _ffmpeg_path_arg(subtitle_ass_path)
        font_dir_arg = _ffmpeg_path_arg(Path(cfg.subtitles.font_name).parent or ".")
        video_chain = f"[0:v]{scale}[vs];[vs]subtitles={ass_arg}:fontsdir={font_dir_arg}[v]"
    else:
        video_chain = f"[0:v]{scale}[v]"

    # 3. Audio chain — voice solo, o voice + BGM amix.
    if bgm_path is not None:
        # Voice input idx = 1, BGM input idx = 2.
        audio_chain = build_bgm_mix_filter(
            voice_input_label="1:a",
            bgm_input_label="2:a",
            voice_weight=voice_volume,
            bgm_weight=bgm_volume,
            out_label="a",
        )
    else:
        audio_chain = f"[1:a]volume={voice_volume}[a]"

    filter_complex = f"{video_chain};{audio_chain}"

    # 4. Encoder loop (retry con fallback si falla en runtime).
    crf = cfg.editor.crf
    preset = cfg.editor.preset
    audio_codec = cfg.editor.audio_codec
    audio_bitrate = cfg.editor.audio_bitrate
    fps = cfg.editor.fps

    last_err = ""
    tried: list[str] = []
    while True:
        codec = detect_hw_encoder(config=cfg)
        if codec in tried:
            # Ya probamos este encoder y libx264 también (es el bottom).
            raise RuntimeError(
                f"stitch: todos los encoders fallaron. Tried={tried}. "
                f"Último stderr:\n{last_err}"
            )
        tried.append(codec)

        builder = FFmpegCommandBuilder(out_path=out_path)
        builder.add_concat_list_input(filelist_path)
        builder.add_input(audio_path)
        if bgm_path is not None:
            builder.add_input(bgm_path)
        builder.add_filter_complex(filter_complex)
        builder.set_video_encoder(codec, crf=crf, preset=preset)
        builder.audio_codec = audio_codec
        builder.audio_bitrate = audio_bitrate
        builder.fps = fps
        cmd = builder.build()

        rc, stderr_text = await _run_ffmpeg(cmd)
        if rc == 0:
            logger.info("Stitch OK con %s → %s", codec, out_path)
            return out_path

        last_err = stderr_text
        logger.warning(
            "ffmpeg falló con encoder %s (rc=%d). Stderr (tail):\n%s",
            codec,
            rc,
            stderr_text[-600:],
        )
        # Mark failed → próxima detect_hw_encoder devolverá el siguiente.
        mark_encoder_failed(codec, reason=stderr_text[-200:])


__all__ = [
    "stitch_video",
    "FFmpegCommandBuilder",
]
