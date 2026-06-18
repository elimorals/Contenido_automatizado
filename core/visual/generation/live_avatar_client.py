"""Cliente dual-backend para LiveAvatar (Alibaba-Quark) — audio-driven talking head.

LiveAvatar produce video con lip-sync sincronizado al audio sobre una imagen
de referencia. Es un LoRA Distillation Matching Distillation (DMD) sobre
Wan2.2-S2V-14B (14B parámetros, diffusion).

Este módulo abstrae los dos modos de despliegue (ADR-016):

1. ``LocalCliBackend`` — invoca ``torchrun minimal_inference/s2v_streaming_interact.py``
   como subprocess. Mismo patrón que ``higgsfield_cli.py``. Requiere repo clonado
   en ``cli_repo_path``, checkpoints descargados en ``cli_ckpt_dir``, y un Python
   con torch 2.8+ / CUDA 12.4 / flash-attn instalados.

2. ``RemoteHttpBackend`` — POST multipart a un endpoint propio (RunPod serverless,
   Lambda Labs HTTP worker, vLLM-style). El endpoint encapsula la GPU heavy
   inference y devuelve ``{video_url, duration_s, cost_usd}``. Recomendado para
   producción — evita acoplar el host del pipeline a una H100.

NO sabe nada de schemas del pipeline (Beat/BeatVisual). El bridge lo hace
``live_avatar.py`` (el generator).
"""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from shared.config import LiveAvatarConfig


# =============================================================================
# Errores
# =============================================================================


class LiveAvatarError(RuntimeError):
    """Base de errores LiveAvatar."""


class LiveAvatarAuthError(LiveAvatarError):
    """Token inválido o ausente (remote_http)."""


class LiveAvatarBadInputError(LiveAvatarError):
    """Input rechazado: imagen ilegible, audio sin formato, prompt vacío."""


class LiveAvatarBackendUnavailableError(LiveAvatarError):
    """Backend no instalado (local: repo/ckpt ausente) o endpoint caído (remote)."""


class LiveAvatarTimeoutError(LiveAvatarError):
    """Inferencia excedió el timeout configurado."""


class LiveAvatarAPIError(LiveAvatarError):
    """5xx u otro error genérico del backend remoto."""


# =============================================================================
# Resultado
# =============================================================================


@dataclass
class LiveAvatarResult:
    """Resultado de una inferencia completa."""

    video_path: Path
    duration_s: float
    backend: str  # "local_cli" | "remote_http"
    cost_usd: float = 0.0
    raw: dict[str, Any] | None = None


# =============================================================================
# Backend abstracto
# =============================================================================


class LiveAvatarBackend(ABC):
    """Contrato común para los dos modos de despliegue."""

    name: str = ""

    @abstractmethod
    async def generate(
        self,
        *,
        image_path: Path,
        audio_path: Path,
        prompt: str,
        out_path: Path,
        seed: int | None = None,
        num_clip: int | None = None,
    ) -> LiveAvatarResult:
        """Genera un MP4 con lip-sync. ``out_path`` es destino sugerido (el
        backend puede ignorarlo y devolver path real en ``result.video_path``).
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Sanity check rápido (no debe lanzar; True si listo, False si no)."""


# =============================================================================
# LocalCliBackend — subprocess torchrun
# =============================================================================


class LocalCliBackend(LiveAvatarBackend):
    """Invoca el script CLI de LiveAvatar como subprocess.

    Args asumen el script ``minimal_inference/s2v_streaming_interact.py`` del
    repo de Alibaba-Quark. Compatible con el patrón de ``higgsfield_cli.py``.
    """

    name = "local_cli"

    def __init__(self, cfg: LiveAvatarConfig):
        self.cfg = cfg
        self.repo = Path(cfg.cli_repo_path).resolve()
        self.ckpt = Path(cfg.cli_ckpt_dir).resolve()

    async def health_check(self) -> bool:
        if not self.repo.exists():
            logger.warning(f"[live_avatar.cli] repo no existe: {self.repo}")
            return False
        script = self.repo / "minimal_inference" / "s2v_streaming_interact.py"
        if not script.exists():
            logger.warning(f"[live_avatar.cli] entrypoint ausente: {script}")
            return False
        if not self.ckpt.exists():
            logger.warning(
                f"[live_avatar.cli] checkpoints ausentes: {self.ckpt} "
                "— descarga con `huggingface-cli download Wan-AI/Wan2.2-S2V-14B`"
            )
            return False
        return True

    def _build_cmd(
        self,
        *,
        image_path: Path,
        audio_path: Path,
        prompt: str,
        save_file: Path,
        seed: int,
        num_clip: int,
    ) -> tuple[list[str], dict[str, str]]:
        cfg = self.cfg
        cmd = [
            "torchrun",
            f"--nproc_per_node={cfg.cli_num_gpus_dit + (0 if cfg.cli_num_gpus_dit == 1 else 1)}",
            f"--master_port={cfg.cli_master_port}",
            "minimal_inference/s2v_streaming_interact.py",
            "--ulysses_size", "1",
            "--task", "s2v-14B",
            "--size", cfg.size,
            "--base_seed", str(seed),
            "--training_config", cfg.cli_training_config,
            "--offload_model", "True" if cfg.offload_model else "False",
            "--convert_model_dtype",
            "--prompt", prompt,
            "--image", str(image_path),
            "--audio", str(audio_path),
            "--infer_frames", str(cfg.infer_frames),
            "--load_lora",
            "--lora_path_dmd", cfg.cli_lora_path,
            "--sample_steps", str(cfg.sample_steps),
            "--sample_guide_scale", str(cfg.sample_guide_scale),
            "--num_clip", str(num_clip),
            "--num_gpus_dit", str(cfg.cli_num_gpus_dit),
            "--sample_solver", cfg.sample_solver,
            "--ckpt_dir", str(self.ckpt),
            "--save_file", str(save_file),
        ]
        if cfg.cli_num_gpus_dit == 1:
            cmd.append("--single_gpu")
        else:
            cmd.append("--enable_vae_parallel")
        if cfg.fp8:
            cmd.append("--fp8")
        if cfg.enable_online_decode:
            cmd.append("--enable_online_decode")

        env = {
            "CUDA_VISIBLE_DEVICES": cfg.cli_cuda_visible_devices,
            "ENABLE_COMPILE": "true" if cfg.enable_compile else "false",
            "ENABLE_FP8": "true" if cfg.fp8 else "false",
            "NCCL_DEBUG": "WARN",
            "NCCL_DEBUG_SUBSYS": "OFF",
        }
        return cmd, env

    async def generate(
        self,
        *,
        image_path: Path,
        audio_path: Path,
        prompt: str,
        out_path: Path,
        seed: int | None = None,
        num_clip: int | None = None,
    ) -> LiveAvatarResult:
        if not await self.health_check():
            raise LiveAvatarBackendUnavailableError(
                f"local_cli backend no listo (repo={self.repo}, ckpt={self.ckpt})"
            )
        if not image_path.exists():
            raise LiveAvatarBadInputError(f"image_path no existe: {image_path}")
        if not audio_path.exists():
            raise LiveAvatarBadInputError(f"audio_path no existe: {audio_path}")
        if not prompt.strip():
            raise LiveAvatarBadInputError("prompt vacío — LiveAvatar lo requiere")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        seed_val = seed if seed is not None else self.cfg.base_seed
        nclip = num_clip if num_clip is not None else 10000  # bound real es length del audio

        cmd, env_extra = self._build_cmd(
            image_path=image_path,
            audio_path=audio_path,
            prompt=prompt,
            save_file=out_path,
            seed=seed_val,
            num_clip=nclip,
        )
        logger.info(
            f"[live_avatar.cli] launch: {shlex.join(cmd[:6])} ... "
            f"(image={image_path.name}, audio={audio_path.name})"
        )
        t0 = time.time()
        import os as _os
        merged_env = {**_os.environ, **env_extra}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.repo),
                env=merged_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.cfg.cli_timeout_s
            )
        except asyncio.TimeoutError as e:
            raise LiveAvatarTimeoutError(
                f"local_cli excedió timeout {self.cfg.cli_timeout_s}s"
            ) from e

        elapsed = time.time() - t0
        if proc.returncode != 0:
            tail = (stderr or b"").decode(errors="replace")[-2000:]
            raise LiveAvatarAPIError(
                f"local_cli exit={proc.returncode} ({elapsed:.1f}s). stderr tail:\n{tail}"
            )

        if not out_path.exists():
            raise LiveAvatarAPIError(
                f"local_cli terminó pero output no existe: {out_path}"
            )

        duration_s = _probe_video_duration(out_path)
        cost = duration_s * self.cfg.cost_per_video_second_usd
        logger.info(
            f"[live_avatar.cli] ✓ {duration_s:.2f}s video en {elapsed:.1f}s "
            f"wall (~${cost:.4f})"
        )
        return LiveAvatarResult(
            video_path=out_path,
            duration_s=duration_s,
            backend=self.name,
            cost_usd=cost,
        )


# =============================================================================
# RemoteHttpBackend — POST multipart a worker propio
# =============================================================================


class RemoteHttpBackend(LiveAvatarBackend):
    """Cliente HTTP async para un worker LiveAvatar self-hosted.

    Convención de API (documentada en docs/LIVE_AVATAR.md):

    .. code-block:: text

        POST {remote_endpoint}
        Authorization: Bearer {remote_api_key}
        Content-Type: multipart/form-data

        fields:
          image: <binary jpg/png>
          audio: <binary wav>
          prompt: str
          seed: int
          num_clip: int
          size: str           # "704*384"
          sample_steps: int   # 4
          fp8: bool

        Response 200:
          {"video_url": "https://.../job-<id>.mp4",
           "duration_s": 12.34,
           "cost_usd": 0.61,
           "job_id": "..."}
    """

    name = "remote_http"

    def __init__(self, cfg: LiveAvatarConfig):
        self.cfg = cfg
        if not cfg.remote_endpoint:
            raise LiveAvatarBackendUnavailableError(
                "remote_endpoint vacío — set [visual.live_avatar].remote_endpoint "
                "o LIVE_AVATAR_REMOTE_ENDPOINT env var"
            )

    async def health_check(self) -> bool:
        if not self.cfg.remote_endpoint:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # GET base URL (assume /health o /); 2xx/3xx/404 todos son OK
                # — solo queremos saber que responde.
                base = self.cfg.remote_endpoint.rsplit("/", 1)[0] or self.cfg.remote_endpoint
                r = await client.get(base)
                return r.status_code < 500
        except Exception as e:
            logger.warning(f"[live_avatar.remote] health check falló: {e}")
            return False

    async def generate(
        self,
        *,
        image_path: Path,
        audio_path: Path,
        prompt: str,
        out_path: Path,
        seed: int | None = None,
        num_clip: int | None = None,
    ) -> LiveAvatarResult:
        if not image_path.exists():
            raise LiveAvatarBadInputError(f"image_path no existe: {image_path}")
        if not audio_path.exists():
            raise LiveAvatarBadInputError(f"audio_path no existe: {audio_path}")
        if not prompt.strip():
            raise LiveAvatarBadInputError("prompt vacío — LiveAvatar lo requiere")

        cfg = self.cfg
        headers = {}
        if cfg.remote_api_key:
            headers["Authorization"] = f"Bearer {cfg.remote_api_key}"

        seed_val = seed if seed is not None else cfg.base_seed
        nclip = num_clip if num_clip is not None else 10000

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=cfg.remote_timeout_s) as client:
                with open(image_path, "rb") as fi, open(audio_path, "rb") as fa:
                    files = {
                        "image": (image_path.name, fi.read(), "application/octet-stream"),
                        "audio": (audio_path.name, fa.read(), "audio/wav"),
                    }
                data = {
                    "prompt": prompt,
                    "seed": str(seed_val),
                    "num_clip": str(nclip),
                    "size": cfg.size,
                    "sample_steps": str(cfg.sample_steps),
                    "sample_guide_scale": str(cfg.sample_guide_scale),
                    "sample_solver": cfg.sample_solver,
                    "infer_frames": str(cfg.infer_frames),
                    "fp8": "true" if cfg.fp8 else "false",
                }
                resp = await client.post(
                    cfg.remote_endpoint, headers=headers, files=files, data=data
                )
        except httpx.TimeoutException as e:
            raise LiveAvatarTimeoutError(
                f"remote_http excedió timeout {cfg.remote_timeout_s}s"
            ) from e

        if resp.status_code == 401 or resp.status_code == 403:
            raise LiveAvatarAuthError(f"auth falló: {resp.status_code} {resp.text[:200]}")
        if resp.status_code == 400:
            raise LiveAvatarBadInputError(f"400: {resp.text[:300]}")
        if resp.status_code >= 500:
            raise LiveAvatarAPIError(f"{resp.status_code}: {resp.text[:300]}")
        if resp.status_code != 200:
            raise LiveAvatarAPIError(f"unexpected {resp.status_code}: {resp.text[:300]}")

        try:
            body = resp.json()
        except Exception as e:
            raise LiveAvatarAPIError(f"respuesta no es JSON: {resp.text[:200]}") from e

        video_url = body.get("video_url")
        if not video_url:
            raise LiveAvatarAPIError(f"respuesta sin video_url: {body}")

        # Descarga el MP4 al destino local
        out_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=cfg.remote_timeout_s) as client:
            async with client.stream("GET", video_url) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1 << 20):
                        f.write(chunk)

        duration_s = float(body.get("duration_s", 0.0)) or _probe_video_duration(out_path)
        cost = float(body.get("cost_usd", duration_s * cfg.cost_per_video_second_usd))
        elapsed = time.time() - t0
        logger.info(
            f"[live_avatar.remote] ✓ {duration_s:.2f}s video en {elapsed:.1f}s "
            f"wall (~${cost:.4f}) job={body.get('job_id', '?')}"
        )
        return LiveAvatarResult(
            video_path=out_path,
            duration_s=duration_s,
            backend=self.name,
            cost_usd=cost,
            raw=body,
        )


# =============================================================================
# Factory
# =============================================================================


def make_backend(cfg: LiveAvatarConfig) -> LiveAvatarBackend:
    """Construye el backend según ``cfg.backend``. Pure factory — no I/O."""
    if cfg.backend == "local_cli":
        return LocalCliBackend(cfg)
    if cfg.backend == "remote_http":
        return RemoteHttpBackend(cfg)
    raise LiveAvatarBackendUnavailableError(f"backend desconocido: {cfg.backend}")


# =============================================================================
# Helpers
# =============================================================================


def _probe_video_duration(path: Path) -> float:
    """Lee duración de un MP4 con ffprobe. Devuelve 0.0 si falla.

    Sigue el patrón de ``core/editor`` (ffmpeg-only, no MoviePy — ADR-001).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"[live_avatar] ffprobe falló para {path}: {e}")
    return 0.0
