"""Fetcher real de referencias: yt-dlp (descarga+metadata) + whisper (transcript)
+ ffmpeg (scene detection).

Implementa el protocolo `ReferenceFetcher`. Todas las deps pesadas se importan
perezosamente para que `core.reference` sea importable sin ellas (los tests usan
fetchers fake). Requiere el extra `reference` (`pip install -e ".[reference]"`)
para yt-dlp; faster-whisper ya está en deps base; ffmpeg/ffprobe deben estar en PATH.

NO se ejercita en la suite unitaria (red + binarios) — ver ADR-017.
"""
from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from loguru import logger

from core.reference.analyzer import RawReference

# Umbral de cambio de escena para ffmpeg `select='gt(scene,X)'` (0..1).
SCENE_THRESHOLD: float = 0.4


class YtDlpFetcher:
    """Descarga el video, transcribe con whisper y detecta cortes con ffmpeg."""

    def __init__(
        self,
        *,
        whisper_model: str = "base",
        scene_threshold: float = SCENE_THRESHOLD,
        keep_files: bool = False,
    ) -> None:
        self.whisper_model = whisper_model
        self.scene_threshold = scene_threshold
        self.keep_files = keep_files

    async def fetch(self, url: str) -> RawReference:
        tmpdir = Path(tempfile.mkdtemp(prefix="reference_"))
        try:
            video_path, title, duration = await asyncio.to_thread(
                self._download, url, tmpdir
            )
            segments, shot_cuts = await asyncio.gather(
                asyncio.to_thread(self._transcribe, video_path),
                asyncio.to_thread(self._detect_scenes, video_path),
            )
            return RawReference(
                title=title,
                duration_s=duration,
                segments=segments,
                shot_cuts=shot_cuts,
            )
        finally:
            if not self.keep_files:
                import shutil

                shutil.rmtree(tmpdir, ignore_errors=True)

    # ── descarga ──────────────────────────────────────────────────────────────
    def _download(self, url: str, tmpdir: Path) -> tuple[Path, str, float]:
        try:
            import yt_dlp  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "yt-dlp no instalado. Instala el extra: pip install -e '.[reference]'"
            ) from e

        out_tmpl = str(tmpdir / "ref.%(ext)s")
        opts = {
            "outtmpl": out_tmpl,
            "format": "mp4/best",
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        files = list(tmpdir.glob("ref.*"))
        if not files:
            raise RuntimeError("yt-dlp no produjo archivo")
        title = info.get("title", "") if isinstance(info, dict) else ""
        duration = float(info.get("duration") or 0.0) if isinstance(info, dict) else 0.0
        return files[0], title, duration

    # ── transcript ──────────────────────────────────────────────────────────────
    def _transcribe(self, video_path: Path) -> list[tuple[float, float, str]]:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:  # pragma: no cover
            logger.warning(f"faster-whisper no disponible: {e}; transcript vacío")
            return []
        model = WhisperModel(self.whisper_model, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(video_path))
        return [(float(s.start), float(s.end), s.text.strip()) for s in segments]

    # ── scene detection ───────────────────────────────────────────────────────────
    def _detect_scenes(self, video_path: Path) -> list[float]:
        """Usa ffmpeg showinfo + select scene para extraer timestamps de cortes."""
        import subprocess

        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-filter:v", f"select='gt(scene,{self.scene_threshold})',showinfo",
            "-f", "null", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:  # pragma: no cover
            logger.warning(f"ffmpeg scene-detect falló: {e}; sin cortes")
            return []
        cuts: list[float] = []
        for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
            cuts.append(float(m.group(1)))
        return sorted(cuts)


__all__ = ["YtDlpFetcher", "SCENE_THRESHOLD"]
