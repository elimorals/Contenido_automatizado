"""ComfyUIGenerator — provider que ejecuta workflows ComfyUI.

Implementa `VisualGenerator` (genera `BeatArtifact.first_frame_path` o
`video_path` según `ComfyOutputType` del workflow).

Multi-tenant:
- Cada `BeatVisual.soul_id` puede usarse también como tenant_id si está set,
  o sino se usa `cfg.comfyui.default_tenant_id`.
- Cada tenant tiene su workflow + LoRA en `cfg.comfyui.tenants[tenant_id]`.
- Override programático: pasar `brand_config=BrandVisualConfig(...)` al ctor.

Reference images: si `BrandVisualConfig.reference_images[]` apunta a paths
locales, los subimos al server con `upload_image()` antes de submit para que
los nodos `LoadImage` los encuentren. Si ya son nombres remotos (sin '/'),
asumimos que están en `ComfyUI/input/` y los usamos tal cual.

OOM retry: si ejecución falla con OOM, llamamos `free_memory()` y reintentamos
UNA vez. Si el segundo intento también falla, soft-fail al orchestrator.

Observability: cada `generate()` produce un `ComfyJob` con timings + outputs +
workflow_version (SHA256 del JSON + spec) accesible vía `last_job` para que
el caller agregue a `TaskInfo.cost_breakdown`.

Soft fail policy:
- Server no responde → VisualGenerationError → orchestrator usa fallback
- Workflow falla → VisualGenerationError
- Genera 0 outputs → VisualGenerationError
- NUNCA crashea el pipeline.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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
    ComfyJobStatus,
    ComfyOutputType,
    ComfyWorkflowSpec,
    VideoSource,
)


# Heurísticas para detectar OOM en mensajes de error del server
_OOM_PATTERNS = (
    "out of memory",
    "cuda out of memory",
    "torch.cuda.outofmemoryerror",
    "outofmemoryerror",
    "not enough memory",
)


def _is_oom_error(msg: str) -> bool:
    low = msg.lower()
    return any(p in low for p in _OOM_PATTERNS)


def _brand_from_tenant(cfg: ComfyUIConfig, tenant_id: str) -> BrandVisualConfig:
    """Construye BrandVisualConfig desde la tabla [visual.comfyui.tenants.*]."""
    entry = cfg.tenants.get(tenant_id)
    if entry is None:
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
    if visual.soul_id:
        return visual.soul_id
    return cfg.default_tenant_id


def _workflow_version(spec: ComfyWorkflowSpec, workflow_json: dict) -> str:
    """SHA256 corto del JSON post-parametrización + spec id.

    Permite trazabilidad: dado un BeatArtifact, sabemos qué workflow
    exacto (con qué parámetros) lo generó. Útil cuando un cliente
    multi-tenant reporta cambio de estilo y necesitas saber qué versión
    estaba vigente.
    """
    h = hashlib.sha256()
    h.update(spec.id.encode("utf-8"))
    h.update(b":")
    h.update(json.dumps(workflow_json, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:12]


def _is_local_path(s: str) -> bool:
    """¿Es path local (necesita upload) o nombre remoto (ya en input/)?"""
    if not s:
        return False
    # Heurística: si tiene separator o existe como archivo, es local
    return ("/" in s) or ("\\" in s) or Path(s).exists()


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
        self._fixed_brand = brand
        self._fixed_workflow_id = workflow_id
        # Última ejecución (para observability + tests)
        self.last_job: ComfyJob | None = None
        self.last_workflow_version: str = ""

    def _resolve_brand(self, visual: BeatVisual) -> BrandVisualConfig:
        if self._fixed_brand is not None:
            return self._fixed_brand
        tenant = _resolve_tenant_id(visual, self.cfg)
        try:
            from core.editorial import load_editorial
            registry = load_editorial()
            from_editorial = registry.get_visual_for_tenant(tenant)
            if from_editorial is not None:
                return from_editorial
        except Exception:  # noqa: BLE001
            pass
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

    async def _upload_reference_if_local(
        self,
        client: ComfyClient,
        ref: str | None,
    ) -> str | None:
        """Si `ref` es path local, lo sube y devuelve el filename remoto.

        Si es nombre remoto (sin separator), lo devuelve sin tocar.
        Si es None/empty, devuelve None.
        """
        if not ref:
            return None
        if not _is_local_path(ref):
            return ref  # ya está en input/
        p = Path(ref)
        if not p.exists():
            logger.warning(f"[comfy] reference image no existe localmente: {ref}")
            return ref  # último intento: que el server lo encuentre
        try:
            uploaded = await client.upload_image(p, type="input", overwrite=True)
            return uploaded.get("name", p.name)
        except ComfyError as e:
            logger.warning(f"[comfy] upload de {p.name} falló ({e}); uso nombre raw")
            return p.name

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

        canvas_cfg = load_config().visual
        params = params_from_brand(
            brand,
            image_prompt=visual.image_prompt,
            width=canvas_cfg.canvas_w,
            height=canvas_cfg.canvas_h,
        )

        out_dir.mkdir(parents=True, exist_ok=True)
        comfy_dir = out_dir / self.cfg.output_subdir
        comfy_dir.mkdir(parents=True, exist_ok=True)

        client_id = f"contenido-{brand.tenant_id}-{beat.idx}"

        # Ejecución con upload de reference + OOM retry
        job, workflow_json = await self._execute_with_retry(
            spec=spec,
            params=params,
            client_id=client_id,
        )
        self.last_job = job
        self.last_workflow_version = _workflow_version(spec, workflow_json)
        job.workflow_id = spec.id

        artifact = await self._materialize_artifact(
            job, spec, beat, visual, comfy_dir, brand,
        )
        # Observability: log siempre, sin importar éxito de download
        logger.info(
            f"[comfy] beat {beat.idx} workflow={spec.id} version={self.last_workflow_version} "
            f"tenant={brand.tenant_id} duration={job.duration_s:.1f}s status={job.status.value}"
        )
        return artifact

    async def _execute_with_retry(
        self,
        *,
        spec: ComfyWorkflowSpec,
        params: WorkflowParams,
        client_id: str,
    ) -> tuple[ComfyJob, dict]:
        """Workflow execute + upload references + OOM auto-retry."""
        try:
            template = load_workflow_json(spec)
            workflow_json = parameterize_workflow(spec, params, workflow_template=template)
        except WorkflowParameterError as e:
            raise VisualGenerationError(
                f"ComfyUI: parametrize falló para '{spec.id}': {e}"
            ) from e

        async def _run_once(retry: bool) -> ComfyJob:
            async with ComfyClient(self.cfg, client_id=client_id) as cli:
                if not await cli.is_alive():
                    raise VisualGenerationError(
                        f"ComfyUI server no responde en {self.cfg.server_url}"
                    )

                # Upload reference image si es local path
                if params.reference_image and spec.parameters.reference_image:
                    uploaded = await self._upload_reference_if_local(
                        cli, params.reference_image,
                    )
                    if uploaded and uploaded != params.reference_image:
                        # Re-parameterize con el nombre del archivo subido
                        params.reference_image = uploaded
                        nonlocal_template = parameterize_workflow(
                            spec, params, workflow_template=template,
                        )
                        workflow_json.clear()
                        workflow_json.update(nonlocal_template)

                if retry:
                    logger.info(f"[comfy] OOM retry: free_memory + retry workflow={spec.id}")
                    try:
                        await cli.free_memory(unload_models=True, free_memory=True)
                    except ComfyError as e:
                        logger.warning(f"[comfy] free_memory falló: {e} (sigo retry igual)")

                return await cli.execute_workflow(workflow_json, use_ws=True)

        try:
            job = await _run_once(retry=False)
            return job, workflow_json
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
            if _is_oom_error(str(e)):
                logger.warning(f"[comfy] OOM detectado: {e}; reintentando con free_memory")
                try:
                    job = await _run_once(retry=True)
                    return job, workflow_json
                except ComfyError as e2:
                    raise VisualGenerationError(
                        f"ComfyUI OOM retry también falló: {e2}"
                    ) from e2
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

        first = sources[0]
        filename = first.get("filename", "")
        if not filename:
            raise VisualGenerationError(
                f"ComfyUI: output sin filename ({first})"
            )

        async with ComfyClient(self.cfg, client_id=job.client_id) as cli:
            data = await cli.download_view(
                filename,
                subfolder=first.get("subfolder", ""),
                type=first.get("type", "output"),
            )

        out_path = comfy_dir / f"beat-{beat.idx:02d}-{spec.id}{extension}"
        out_path.write_bytes(data)

        if spec.output_type == ComfyOutputType.IMAGE:
            normalized = self._ensure_jpg(out_path, comfy_dir, beat.idx, spec.id)
            return BeatArtifact(
                idx=beat.idx,
                first_frame_path=normalized,
                video_path=None,
                source=VideoSource.COMFYUI,
                duration_s=0.0,
            )

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

    def cost_record(self, *, phase: str = "visual") -> dict:
        """Devuelve dict agregable a `TaskInfo.cost_breakdown` desde el último job.

        Estructura:
            {
                "comfyui_<workflow_id>": cost_estimate_usd,
                "comfyui_<workflow_id>_duration_s": duration,
                "comfyui_<workflow_id>_version": hash,
            }
        """
        if self.last_job is None:
            return {}
        wid = self.last_job.workflow_id or "unknown"
        return {
            f"comfyui_{wid}": self._cost_estimate(),
            f"comfyui_{wid}_duration_s": round(self.last_job.duration_s, 2),
            f"comfyui_{wid}_version": self.last_workflow_version,
        }

    def _cost_estimate(self) -> float:
        """Costo estimado del último run según output_type del spec."""
        if self.last_job is None or not self.last_job.workflow_id:
            return 0.0
        try:
            spec = get_workflow_spec(self.last_job.workflow_id)
            if spec.estimated_cost_usd > 0:
                return spec.estimated_cost_usd
        except KeyError:
            pass
        # Fallback: usar cost_estimate_per_image/video del cfg
        try:
            spec = get_workflow_spec(self.last_job.workflow_id)
            if spec.output_type == ComfyOutputType.VIDEO:
                return self.cfg.cost_estimate_per_video_usd
            return self.cfg.cost_estimate_per_image_usd
        except KeyError:
            return 0.0


# =============================================================================
# Helpers directos
# =============================================================================


async def generate_comfy_frame(
    beat: Beat,
    visual: BeatVisual,
    out_dir: Path,
    *,
    brand: BrandVisualConfig | None = None,
    workflow_id: str | None = None,
) -> Path | None:
    """Devuelve path al first frame generado por ComfyUI o None si falla."""
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
    """Quick health-check (≤2s timeout)."""
    cfg = load_config().visual.comfyui
    if not cfg.enabled:
        return False
    try:
        async with ComfyClient(cfg) as cli:
            return await asyncio.wait_for(cli.is_alive(), timeout=2.0)
    except (ComfyError, asyncio.TimeoutError):
        return False
