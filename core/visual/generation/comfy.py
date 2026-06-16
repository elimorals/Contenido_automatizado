"""ComfyUIGenerator — provider que ejecuta workflows ComfyUI.

Implementa `VisualGenerator` (genera `BeatArtifact.first_frame_path` o
`video_path` según `ComfyOutputType` del workflow).

Multi-tenant:
- Cada `BeatVisual.soul_id` puede usarse también como tenant_id si está set,
  o sino se usa `cfg.comfyui.default_tenant_id`.
- Cada tenant tiene su workflow + LoRA en `cfg.comfyui.tenants[tenant_id]`.
- Override programático: pasar `brand_config=BrandVisualConfig(...)` al ctor.

Soft fail policy:
- Si server no responde → VisualGenerationError → orchestrator usa fallback
- Si workflow falla → VisualGenerationError
- Si genera 0 outputs → VisualGenerationError
- NUNCA crashea el pipeline
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger
from PIL import Image

from core.visual.generation.base import VisualGenerationError, VisualGenerator
from core.visual.generation.comfy_client import (
    ComfyClient,
    ComfyConnectionError,
    ComfyError,
    ComfyExecutionError,
    ComfyTimeoutError,
    ComfyValidationError,
)
from core.visual.generation.comfy_workflows import (
    WorkflowParameterError,
    WorkflowParams,
    get_workflow_spec,
    load_registry,
    load_workflow_json,
    parameterize_workflow,
    params_from_brand,
)
from shared.config import ComfyUIConfig, load_config
from shared.schemas import (
    Beat,
    BeatArtifact,
    BeatVisual,
    BrandVisualConfig,
    ComfyJob,
    ComfyOutputType,
    ComfyWorkflowSpec,
    VideoSource,
)


def _brand_from_tenant(cfg: ComfyUIConfig, tenant_id: str) -> BrandVisualConfig:
    """Construye BrandVisualConfig desde la tabla [visual.comfyui.tenants.*]."""
    entry = cfg.tenants.get(tenant_id)
    if entry is None:
        # No registrado → defaults vacíos (no LoRA, sin style suffix)
        return BrandVisualConfig(tenant_id=tenant_id)
    return BrandVisualConfig(
        tenant_id=tenant_id,
        primary_workflow_id=entry.primary_workflow_id or cfg.default_workflow_id,
        lora_name=entry.lora_name or None,
        lora_strength=entry.lora_strength,
        style_suffix=entry.style_suffix,
    )


def _resolve_tenant_id(visual: BeatVisual, cfg: ComfyUIConfig) -> str:
    """Tenant resolution: BeatVisual.soul_id > config default."""
    # NOTA: reutilizamos soul_id como tenant_id por convención. Si tu setup
    # necesita ambos por separado, agrega un campo dedicado a BeatVisual.
    if visual.soul_id:
        return visual.soul_id
    return cfg.default_tenant_id


class ComfyUIGenerator(VisualGenerator):
    """Provider que genera first frames (y opcionalmente videos) via ComfyUI."""

    name = "comfyui"

    def __init__(
        self,
        cfg: ComfyUIConfig | None = None,
        *,
        brand: BrandVisualConfig | None = None,
        workflow_id: str | None = None,
    ) -> None:
        self.cfg = cfg or load_config().visual.comfyui
        # Override programático (útil para tests o request-scoped brand)
        self._fixed_brand = brand
        self._fixed_workflow_id = workflow_id

    def _resolve_brand(self, visual: BeatVisual) -> BrandVisualConfig:
        if self._fixed_brand is not None:
            return self._fixed_brand
        tenant = _resolve_tenant_id(visual, self.cfg)
        # Prioridad 1: editorial/brand-visual.json (más rico, controlado por usuario)
        try:
            from core.editorial import load_editorial
            registry = load_editorial()
            from_editorial = registry.get_visual_for_tenant(tenant)
            if from_editorial is not None:
                return from_editorial
        except Exception:  # noqa: BLE001
            pass  # capa editorial no disponible — sigue al config TOML
        # Prioridad 2: config TOML [visual.comfyui.tenants.*]
        return _brand_from_tenant(self.cfg, tenant)

    def _resolve_workflow_id(self, brand: BrandVisualConfig) -> str:
        if self._fixed_workflow_id:
            return self._fixed_workflow_id
        if brand.primary_workflow_id:
            return brand.primary_workflow_id
        if self.cfg.default_workflow_id:
            return self.cfg.default_workflow_id
        raise VisualGenerationError(
            "ComfyUI: no workflow_id configurado (ni en brand, ni en config)"
        )

    async def generate(
        self,
        beat: Beat,
        visual: BeatVisual,
        content_mode: str,
        out_dir: Path,
    ) -> BeatArtifact:
        """Resuelve tenant → brand → workflow → ejecuta → descarga primer output.

        Devuelve BeatArtifact con `first_frame_path` (si output_type=image)
        o `video_path` (si output_type=video).
        """
        if not self.cfg.enabled:
            raise VisualGenerationError("ComfyUI: provider deshabilitado en config")

        brand = self._resolve_brand(visual)
        workflow_id = self._resolve_workflow_id(brand)

        try:
            spec = get_workflow_spec(workflow_id)
        except KeyError as e:
            raise VisualGenerationError(str(e)) from e

        # Construir params semánticos desde brand + image_prompt
        # Width/height vienen del config visual canvas, NO del brand
        # (porque comfyui canvas debe matchear el canvas del DAG)
        canvas_cfg = load_config().visual
        params = params_from_brand(
            brand,
            image_prompt=visual.image_prompt,
            width=canvas_cfg.canvas_w,
            height=canvas_cfg.canvas_h,
        )

        try:
            workflow_json = parameterize_workflow(spec, params)
        except WorkflowParameterError as e:
            raise VisualGenerationError(
                f"ComfyUI: parametrize falló para '{workflow_id}': {e}"
            ) from e

        # Tenant_id como client_id da correlación en logs del server
        out_dir.mkdir(parents=True, exist_ok=True)
        comfy_dir = out_dir / self.cfg.output_subdir
        comfy_dir.mkdir(parents=True, exist_ok=True)

        client_id = f"contenido-{brand.tenant_id}-{beat.idx}"

        job = await self._run_workflow(workflow_json, client_id=client_id)

        # Recuperar outputs
        return await self._materialize_artifact(
            job, spec, beat, visual, comfy_dir, brand,
        )

    async def _run_workflow(
        self, workflow_json: dict, *, client_id: str,
    ) -> ComfyJob:
        try:
            async with ComfyClient(self.cfg, client_id=client_id) as cli:
                if not await cli.is_alive():
                    raise VisualGenerationError(
                        f"ComfyUI server no responde en {self.cfg.server_url}"
                    )
                return await cli.execute_workflow(workflow_json, use_ws=True)
        except ComfyConnectionError as e:
            raise VisualGenerationError(f"ComfyUI connect: {e}") from e
        except ComfyValidationError as e:
            err = f"ComfyUI validation: {e}"
            if e.node_errors:
                err += f" (node_errors: {list(e.node_errors.keys())[:3]})"
            raise VisualGenerationError(err) from e
        except ComfyTimeoutError as e:
            raise VisualGenerationError(f"ComfyUI timeout: {e}") from e
        except ComfyExecutionError as e:
            raise VisualGenerationError(
                f"ComfyUI execution: {e} (node={e.node_id})"
            ) from e
        except ComfyError as e:
            raise VisualGenerationError(f"ComfyUI: {e}") from e

    async def _materialize_artifact(
        self,
        job: ComfyJob,
        spec: ComfyWorkflowSpec,
        beat: Beat,
        visual: BeatVisual,
        comfy_dir: Path,
        brand: BrandVisualConfig,
    ) -> BeatArtifact:
        """Descarga el primer output relevante y arma el BeatArtifact."""
        # Decidir qué descargar según output_type del spec
        if spec.output_type == ComfyOutputType.VIDEO:
            sources = job.videos or job.gifs
            extension = ".mp4"
            field = "video"
        else:
            sources = job.images
            extension = ".png"
            field = "image"

        if not sources:
            raise VisualGenerationError(
                f"ComfyUI: workflow '{spec.id}' completó pero produjo 0 {field}s"
            )

        # Filtrar por output_nodes si están declarados (sino tomar el primero)
        first = sources[0]
        filename = first.get("filename", "")
        if not filename:
            raise VisualGenerationError(
                f"ComfyUI: output sin filename ({first})"
            )

        # Descargar via /view
        async with ComfyClient(self.cfg, client_id=job.client_id) as cli:
            data = await cli.download_view(
                filename,
                subfolder=first.get("subfolder", ""),
                type=first.get("type", "output"),
            )

        out_path = comfy_dir / f"beat-{beat.idx:02d}-{spec.id}{extension}"
        out_path.write_bytes(data)

        logger.info(
            f"[comfy] beat {beat.idx} workflow={spec.id} tenant={brand.tenant_id} "
            f"→ {out_path.name} ({len(data)} bytes, {job.duration_s:.1f}s)"
        )

        # Si es imagen, validar y normalizar a JPG para downstream (Veo/DoP)
        if spec.output_type == ComfyOutputType.IMAGE:
            normalized = self._ensure_jpg(out_path, comfy_dir, beat.idx, spec.id)
            return BeatArtifact(
                idx=beat.idx,
                first_frame_path=normalized,
                video_path=None,
                source=VideoSource.COMFYUI,
                duration_s=0.0,
            )

        # Video: lo dejamos como está
        return BeatArtifact(
            idx=beat.idx,
            first_frame_path=None,
            video_path=out_path,
            source=VideoSource.COMFYUI,
            duration_s=float(beat.veo_duration),
        )

    @staticmethod
    def _ensure_jpg(src: Path, comfy_dir: Path, idx: int, workflow_id: str) -> Path:
        """Si el output es PNG, lo convierte a JPG para downstream consumers."""
        if src.suffix.lower() in (".jpg", ".jpeg"):
            return src
        try:
            img = Image.open(src).convert("RGB")
            dst = comfy_dir / f"frame-{idx:02d}-{workflow_id}.jpg"
            img.save(str(dst), format="JPEG", quality=92)
            return dst
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[comfy] convert PNG→JPG falló: {e}; uso PNG raw")
            return src


# =============================================================================
# Helper directo (paralelo a generate_higgsfield_clip / generate_veo_clip)
# =============================================================================


async def generate_comfy_frame(
    beat: Beat,
    visual: BeatVisual,
    out_dir: Path,
    *,
    brand: BrandVisualConfig | None = None,
    workflow_id: str | None = None,
) -> Path | None:
    """Devuelve path al first frame generado por ComfyUI o None si falla.

    Útil cuando el orchestrator quiere intentar ComfyUI antes que Gemini Image.
    """
    try:
        gen = ComfyUIGenerator(brand=brand, workflow_id=workflow_id)
        artifact = await gen.generate(
            beat=beat, visual=visual, content_mode="general", out_dir=out_dir,
        )
        return artifact.first_frame_path
    except VisualGenerationError:
        return None
    except asyncio.TimeoutError:
        return None


async def is_comfyui_available() -> bool:
    """Quick health-check (≤2s timeout) — útil al inicio para registrar provider."""
    cfg = load_config().visual.comfyui
    if not cfg.enabled:
        return False
    try:
        async with ComfyClient(cfg) as cli:
            return await asyncio.wait_for(cli.is_alive(), timeout=2.0)
    except (ComfyError, asyncio.TimeoutError):
        return False
