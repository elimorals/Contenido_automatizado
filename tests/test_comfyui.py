"""Tests para integración ComfyUI: client, workflows, provider, CLI wrapper.

Cubre:
1. Error taxonomy (Auth/Validation/Timeout/Execution/Connection)
2. _ws_url_from_http (http/https → ws/wss)
3. Workflow registry load + parameterizer
4. Provider tenant resolution + soft fail
5. comfy-cli wrapper binary check
6. Schemas (ComfyJob, BrandVisualConfig)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from core.comfy.training import (
    TRAINING_MAX_IMAGES,
    TRAINING_MIN_IMAGES,
    TrainingValidationError,
    kohya_training_command,
    plan_lora_training,
    replicate_training_command,
    validate_training_dataset,
)
from core.comfy.wrapper import (
    ComfyCLIError,
    ComfyCLINotInstalled,
    check_binary,
)
from core.visual.generation.comfy import (
    _is_local_path,
    _is_oom_error,
    _workflow_version,
)
from core.visual.generation.comfy import (
    ComfyUIGenerator,
    _brand_from_tenant,
    _resolve_tenant_id,
    is_comfyui_available,
)
from core.visual.generation.comfy_client import (
    ComfyAuthError,
    ComfyClient,
    ComfyConnectionError,
    ComfyError,
    ComfyExecutionError,
    ComfyTimeoutError,
    ComfyValidationError,
    _ws_url_from_http,
)
from core.visual.generation.comfy_workflows import (
    WorkflowParameterError,
    WorkflowParams,
    _apply_path,
    get_workflow_spec,
    load_registry,
    parameterize_workflow,
    params_from_brand,
    randomize_seed,
)
from shared.config import ComfyTenantEntry, ComfyUIConfig
from shared.schemas import (
    Beat,
    BeatRole,
    BeatVisual,
    BrandVisualConfig,
    ComfyJob,
    ComfyJobStatus,
    ComfyOutputType,
    ComfyParameterMap,
    ComfyWorkflowKind,
    ComfyWorkflowSpec,
    MotionHint,
    VideoSource,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def cfg_enabled() -> ComfyUIConfig:
    return ComfyUIConfig(
        enabled=True,
        server_url="http://127.0.0.1:8188",
        workflows_dir="./workflows",
        default_workflow_id="flux_lora_brand",
        submit_timeout_s=5.0,
        poll_timeout_s=5.0,
        poll_interval_s=0.05,
        tenants={
            "ruteo": ComfyTenantEntry(
                primary_workflow_id="flux_lora_brand",
                lora_name="ruteo_v1.safetensors",
                lora_strength=0.85,
                style_suffix="cinematic still",
            )
        },
        prefer_for_brand_frames=True,
    )


@pytest.fixture
def sample_workflow_spec() -> ComfyWorkflowSpec:
    return ComfyWorkflowSpec(
        id="test_workflow",
        name="Test",
        kind=ComfyWorkflowKind.LORA_T2I,
        json_path="test.json",
        parameters=ComfyParameterMap(
            prompt="6-inputs-text",
            seed="3-inputs-seed",
            width="5-inputs-width",
            height="5-inputs-height",
            lora_name="10-inputs-lora_name",
            lora_strength="10-inputs-strength_model",
        ),
        output_nodes=["9"],
    )


@pytest.fixture
def sample_workflow_template() -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "cfg": 8}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "default prompt"}},
        "10": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "none.safetensors", "strength_model": 0.5},
        },
    }


@pytest.fixture
def sample_beat() -> Beat:
    return Beat(
        idx=0, role=BeatRole.HOOK, text="test",
        target_duration_s=4.0, veo_duration=4,
    )


@pytest.fixture
def sample_visual() -> BeatVisual:
    return BeatVisual(
        image_prompt="a person holding coffee",
        motion_hint=MotionHint.STATIC,
        visual_anchor="coffee",
    )


# =============================================================================
# URL helpers
# =============================================================================


class TestWsUrlFromHttp:
    def test_http_local(self) -> None:
        assert _ws_url_from_http("http://127.0.0.1:8188") == "ws://127.0.0.1:8188/ws"

    def test_https_remote(self) -> None:
        assert _ws_url_from_http("https://api.example.com") == "wss://api.example.com/ws"

    def test_strips_trailing_slash(self) -> None:
        assert _ws_url_from_http("http://x:8188/") == "ws://x:8188/ws"

    def test_no_scheme_assumes_http(self) -> None:
        assert _ws_url_from_http("127.0.0.1:8188") == "ws://127.0.0.1:8188/ws"


# =============================================================================
# Error taxonomy
# =============================================================================


class TestErrorTaxonomy:
    @pytest.mark.parametrize(
        "status,exc_type",
        [
            (401, ComfyAuthError),
            (403, ComfyAuthError),
            (400, ComfyValidationError),
            (500, ComfyError),
        ],
    )
    def test_raise_for_status_maps(self, status: int, exc_type: type) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status
        resp.text = "{}"
        with pytest.raises(exc_type):
            ComfyClient._raise_for_status(resp, ctx="test")

    def test_raise_for_status_passes_2xx(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        # No raise
        ComfyClient._raise_for_status(resp, ctx="test")

    def test_validation_error_extracts_node_errors(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 400
        resp.text = '{"node_errors": {"3": {"errors": ["bad"]}}}'
        resp.json = MagicMock(
            return_value={"node_errors": {"3": {"errors": ["bad"]}}}
        )
        with pytest.raises(ComfyValidationError) as exc_info:
            ComfyClient._raise_for_status(resp, ctx="submit")
        assert "3" in exc_info.value.node_errors


# =============================================================================
# Workflow registry + parameterizer
# =============================================================================


class TestWorkflowRegistry:
    def test_load_registry_finds_workflows(self) -> None:
        reg = load_registry()
        # El repo trae 3 workflows registrados por default
        assert "flux_basic_9x16" in reg
        assert "flux_lora_brand" in reg
        assert "sdxl_ipadapter_style" in reg

    def test_get_workflow_spec_valid(self) -> None:
        spec = get_workflow_spec("flux_lora_brand")
        assert spec.kind == ComfyWorkflowKind.LORA_T2I
        assert spec.output_type == ComfyOutputType.IMAGE

    def test_get_workflow_spec_invalid_raises(self) -> None:
        with pytest.raises(KeyError, match="no encontrado"):
            get_workflow_spec("no_existe")


class TestParameterizeWorkflow:
    def test_substitutes_prompt(self, sample_workflow_spec, sample_workflow_template) -> None:
        params = WorkflowParams(prompt="a cat in space")
        result = parameterize_workflow(
            sample_workflow_spec, params,
            workflow_template=sample_workflow_template,
        )
        assert result["6"]["inputs"]["text"] == "a cat in space"

    def test_substitutes_lora(self, sample_workflow_spec, sample_workflow_template) -> None:
        params = WorkflowParams(
            prompt="x", lora_name="brand_v2.safetensors", lora_strength=0.9,
        )
        result = parameterize_workflow(
            sample_workflow_spec, params,
            workflow_template=sample_workflow_template,
        )
        assert result["10"]["inputs"]["lora_name"] == "brand_v2.safetensors"
        assert result["10"]["inputs"]["strength_model"] == 0.9

    def test_template_not_mutated(self, sample_workflow_spec, sample_workflow_template) -> None:
        """Deep copy — el template original no se modifica."""
        original_prompt = sample_workflow_template["6"]["inputs"]["text"]
        params = WorkflowParams(prompt="MUTATED")
        parameterize_workflow(
            sample_workflow_spec, params,
            workflow_template=sample_workflow_template,
        )
        assert sample_workflow_template["6"]["inputs"]["text"] == original_prompt

    def test_none_values_skip(self, sample_workflow_spec, sample_workflow_template) -> None:
        """Params=None preserva el default del template."""
        params = WorkflowParams(prompt="x")  # lora_name=None
        result = parameterize_workflow(
            sample_workflow_spec, params,
            workflow_template=sample_workflow_template,
        )
        # node 10 lora_name no fue sustituido (None preserva template default)
        assert result["10"]["inputs"]["lora_name"] == "none.safetensors"

    def test_custom_overrides(self, sample_workflow_spec, sample_workflow_template) -> None:
        params = WorkflowParams(
            prompt="x", custom={"3-inputs-cfg": 42},
        )
        result = parameterize_workflow(
            sample_workflow_spec, params,
            workflow_template=sample_workflow_template,
        )
        assert result["3"]["inputs"]["cfg"] == 42

    def test_invalid_path_raises(self) -> None:
        wf = {"3": {"class_type": "X", "inputs": {}}}
        with pytest.raises(WorkflowParameterError, match="inválida"):
            _apply_path(wf, "no_dash_pattern", "value")

    def test_unknown_node_raises(self) -> None:
        wf = {"3": {"class_type": "X", "inputs": {}}}
        with pytest.raises(WorkflowParameterError, match="no existe en workflow"):
            _apply_path(wf, "99-inputs-text", "value")


class TestParamsFromBrand:
    def test_applies_style_suffix(self) -> None:
        brand = BrandVisualConfig(
            tenant_id="r",
            style_suffix="cinematic still, golden hour",
        )
        params = params_from_brand(brand, image_prompt="a fox")
        assert params.prompt == "a fox, cinematic still, golden hour"

    def test_no_style_suffix(self) -> None:
        brand = BrandVisualConfig(tenant_id="r", style_suffix="")
        params = params_from_brand(brand, image_prompt="a fox")
        assert params.prompt == "a fox"

    def test_uses_brand_dimensions(self) -> None:
        brand = BrandVisualConfig(
            tenant_id="r", default_width=1080, default_height=1920,
        )
        params = params_from_brand(brand, image_prompt="x")
        assert params.width == 1080
        assert params.height == 1920

    def test_random_seed_default(self) -> None:
        brand = BrandVisualConfig(tenant_id="r")
        params = params_from_brand(brand, image_prompt="x")
        assert params.seed is not None
        assert 0 <= params.seed < 2**32

    def test_explicit_seed_overrides(self) -> None:
        brand = BrandVisualConfig(tenant_id="r")
        params = params_from_brand(brand, image_prompt="x", seed=42)
        assert params.seed == 42


class TestRandomizeSeed:
    def test_in_range(self) -> None:
        seed = randomize_seed()
        assert 0 <= seed < 2**32

    def test_varies(self) -> None:
        seeds = {randomize_seed() for _ in range(20)}
        # Probabilísticamente al menos algunos seeds son distintos
        assert len(seeds) > 5


# =============================================================================
# Tenant resolution + brand
# =============================================================================


class TestTenantResolution:
    def test_resolves_from_visual_soul_id(
        self, cfg_enabled: ComfyUIConfig, sample_visual: BeatVisual,
    ) -> None:
        visual = sample_visual.model_copy(update={"soul_id": "ruteo"})
        assert _resolve_tenant_id(visual, cfg_enabled) == "ruteo"

    def test_falls_back_to_default(
        self, cfg_enabled: ComfyUIConfig, sample_visual: BeatVisual,
    ) -> None:
        # sample_visual.soul_id es None
        assert _resolve_tenant_id(sample_visual, cfg_enabled) == cfg_enabled.default_tenant_id

    def test_brand_from_known_tenant(self, cfg_enabled: ComfyUIConfig) -> None:
        brand = _brand_from_tenant(cfg_enabled, "ruteo")
        assert brand.lora_name == "ruteo_v1.safetensors"
        assert brand.lora_strength == 0.85
        assert "cinematic still" in brand.style_suffix

    def test_brand_from_unknown_tenant_uses_defaults(
        self, cfg_enabled: ComfyUIConfig,
    ) -> None:
        brand = _brand_from_tenant(cfg_enabled, "unknown_tenant")
        assert brand.tenant_id == "unknown_tenant"
        assert brand.lora_name is None  # default vacío


# =============================================================================
# Provider soft fail + tenant override
# =============================================================================


class TestComfyUIGeneratorSoftFail:
    @pytest.mark.asyncio
    async def test_disabled_raises_visual_error(
        self, sample_beat, sample_visual, tmp_path,
    ) -> None:
        from core.visual.generation.base import VisualGenerationError

        gen = ComfyUIGenerator()
        gen.cfg = ComfyUIConfig(enabled=False)
        with pytest.raises(VisualGenerationError, match="deshabilitado"):
            await gen.generate(
                beat=sample_beat, visual=sample_visual,
                content_mode="general", out_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_no_workflow_id_raises(
        self, sample_beat, sample_visual, tmp_path,
    ) -> None:
        from core.visual.generation.base import VisualGenerationError

        cfg = ComfyUIConfig(enabled=True, default_workflow_id="")
        gen = ComfyUIGenerator(cfg=cfg)
        # Brand sin workflow tampoco
        gen._fixed_brand = BrandVisualConfig(tenant_id="x")
        with pytest.raises(VisualGenerationError, match="no workflow_id"):
            await gen.generate(
                beat=sample_beat, visual=sample_visual,
                content_mode="general", out_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_unknown_workflow_raises(
        self, sample_beat, sample_visual, tmp_path,
    ) -> None:
        from core.visual.generation.base import VisualGenerationError

        cfg = ComfyUIConfig(enabled=True, default_workflow_id="no_existe_xyz")
        gen = ComfyUIGenerator(cfg=cfg)
        # Brand fijo sin workflow → fuerza usar cfg.default_workflow_id
        # (que apunta a un id inexistente)
        gen._fixed_brand = BrandVisualConfig(
            tenant_id="ghost", primary_workflow_id="no_existe_xyz",
        )
        with pytest.raises(VisualGenerationError, match="no encontrado"):
            await gen.generate(
                beat=sample_beat, visual=sample_visual,
                content_mode="general", out_dir=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_server_down_raises_visual_error(
        self, cfg_enabled, sample_beat, sample_visual, tmp_path,
    ) -> None:
        from core.visual.generation.base import VisualGenerationError

        gen = ComfyUIGenerator(cfg=cfg_enabled)
        mock_client = AsyncMock()
        mock_client.is_alive = AsyncMock(return_value=False)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        with patch(
            "core.visual.generation.comfy.ComfyClient",
            return_value=mock_client,
        ):
            with pytest.raises(VisualGenerationError, match="no responde"):
                await gen.generate(
                    beat=sample_beat, visual=sample_visual,
                    content_mode="general", out_dir=tmp_path,
                )


# =============================================================================
# Schema validations
# =============================================================================


class TestComfyJobSchema:
    def test_default_status_queued(self) -> None:
        job = ComfyJob(prompt_id="x", client_id="c", workflow_id="w")
        assert job.status == ComfyJobStatus.QUEUED

    def test_duration_zero_when_not_completed(self) -> None:
        job = ComfyJob(prompt_id="x", client_id="c", workflow_id="w")
        assert job.duration_s == 0.0

    def test_duration_computes_after_completion(self) -> None:
        job = ComfyJob(
            prompt_id="x", client_id="c", workflow_id="w",
            submitted_at_unix=1000.0, completed_at_unix=1023.5,
        )
        assert job.duration_s == 23.5


class TestBrandVisualConfigSchema:
    def test_default_values(self) -> None:
        brand = BrandVisualConfig(tenant_id="default")
        assert brand.lora_strength == 0.85
        assert brand.default_width == 720
        assert brand.default_height == 1280

    def test_tenant_id_pattern(self) -> None:
        # Permitido
        BrandVisualConfig(tenant_id="my_brand")
        BrandVisualConfig(tenant_id="brand-v1")
        # No permitido
        with pytest.raises(Exception):
            BrandVisualConfig(tenant_id="UPPERCASE")

    def test_lora_strength_range(self) -> None:
        with pytest.raises(Exception):
            BrandVisualConfig(tenant_id="x", lora_strength=2.5)


# =============================================================================
# CLI wrapper binary check
# =============================================================================


class TestCLIBinaryCheck:
    def test_absolute_path_missing_raises(self) -> None:
        with pytest.raises(ComfyCLINotInstalled, match="no existe"):
            check_binary("/nonexistent/path/comfy")

    def test_not_in_path_raises(self) -> None:
        with pytest.raises(ComfyCLINotInstalled, match="no encontrado"):
            check_binary("totally_invented_xyzzy_cli")


# =============================================================================
# Helpers nuevos del provider: OOM detection, workflow versioning, local path
# =============================================================================


class TestIsOomError:
    @pytest.mark.parametrize("msg", [
        "RuntimeError: CUDA out of memory",
        "torch.cuda.OutOfMemoryError: Tried to allocate 24GiB",
        "Not enough memory to load model",
    ])
    def test_detects_oom_messages(self, msg: str) -> None:
        assert _is_oom_error(msg) is True

    @pytest.mark.parametrize("msg", [
        "Invalid prompt",
        "Connection refused",
        "Workflow validation failed: missing node",
    ])
    def test_ignores_non_oom(self, msg: str) -> None:
        assert _is_oom_error(msg) is False


class TestIsLocalPath:
    def test_with_slash_is_local(self) -> None:
        assert _is_local_path("/tmp/img.png") is True

    def test_with_backslash_is_local(self) -> None:
        assert _is_local_path(r"C:\images\img.png") is True

    def test_bare_filename_is_remote(self) -> None:
        # Sin separator + no existe localmente
        assert _is_local_path("just_filename.png") is False

    def test_empty_is_false(self) -> None:
        assert _is_local_path("") is False
        assert _is_local_path(None) is False  # type: ignore[arg-type]


class TestWorkflowVersion:
    def test_deterministic(self, sample_workflow_spec, sample_workflow_template) -> None:
        v1 = _workflow_version(sample_workflow_spec, sample_workflow_template)
        v2 = _workflow_version(sample_workflow_spec, sample_workflow_template)
        assert v1 == v2

    def test_changes_with_workflow(self, sample_workflow_spec, sample_workflow_template) -> None:
        v1 = _workflow_version(sample_workflow_spec, sample_workflow_template)
        modified = {**sample_workflow_template}
        modified["6"] = {**modified["6"], "inputs": {"text": "OTHER"}}
        v2 = _workflow_version(sample_workflow_spec, modified)
        assert v1 != v2

    def test_short_hex_format(self, sample_workflow_spec, sample_workflow_template) -> None:
        v = _workflow_version(sample_workflow_spec, sample_workflow_template)
        assert len(v) == 12
        assert all(c in "0123456789abcdef" for c in v)


# =============================================================================
# Training wizard
# =============================================================================


class TestTrainingValidation:
    def test_missing_dir_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "no_existe"
        with pytest.raises(TrainingValidationError, match="no existe"):
            validate_training_dataset(missing)

    def test_empty_dir_warns(self, tmp_path: Path) -> None:
        d = tmp_path / "images"
        d.mkdir()
        valid, warnings = validate_training_dataset(d)
        assert valid == []
        assert any("mínimo" in w for w in warnings)

    def test_strict_raises_on_too_few(self, tmp_path: Path) -> None:
        d = tmp_path / "images"
        d.mkdir()
        with pytest.raises(TrainingValidationError, match="mínimo"):
            validate_training_dataset(d, strict=True)

    def test_low_res_skipped(self, tmp_path: Path) -> None:
        d = tmp_path / "images"
        d.mkdir()
        # Crear 5 imágenes pequeñas (< 1024)
        for i in range(5):
            Image.new("RGB", (512, 512), (i*40, 0, 0)).save(d / f"img{i}.jpg")
        valid, warnings = validate_training_dataset(d)
        assert valid == []
        assert any("1024 mínimo" in w for w in warnings)

    def test_valid_images_pass(self, tmp_path: Path) -> None:
        d = tmp_path / "images"
        d.mkdir()
        for i in range(10):
            Image.new("RGB", (1024, 1024), (i*20, 0, 0)).save(d / f"img{i}.jpg")
        valid, warnings = validate_training_dataset(d)
        assert len(valid) == 10
        # 10 está fuera del sweet spot 20-30
        assert any("sweet spot" in w for w in warnings)


class TestReplicateTraining:
    def test_command_includes_dataset_path(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        result = replicate_training_command(
            name="test_brand", image_dir=d, trigger_word="tb",
        )
        assert "test_brand" in result["cli_command"]
        assert "tb" in result["cli_command"]
        assert result["plan"].steps == 1000
        assert result["plan"].estimated_cost_usd > 0

    def test_includes_instructions(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        result = replicate_training_command(name="test", image_dir=d)
        assert len(result["instructions"]) >= 5
        assert any("API token" in i for i in result["instructions"])


class TestKohyaTraining:
    def test_flux_uses_correct_script(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        result = kohya_training_command(name="b", image_dir=d, base_model="flux")
        assert "flux_train_network.py" in result["cli_command"]
        assert "flux1-dev" in result["cli_command"]

    def test_sdxl_uses_correct_script(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        result = kohya_training_command(name="b", image_dir=d, base_model="sdxl")
        assert "sdxl_train_network.py" in result["cli_command"]


class TestPlanLoraTraining:
    def test_invalid_dataset_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(TrainingValidationError, match="0 imágenes"):
            plan_lora_training(name="b", image_dir=d)

    def test_replicate_returns_plan(self, tmp_path: Path) -> None:
        d = tmp_path / "imgs"
        d.mkdir()
        for i in range(8):
            Image.new("RGB", (1024, 1024), (i*30, 0, 0)).save(d / f"img{i}.jpg")
        result = plan_lora_training(
            name="b", image_dir=d, backend="replicate",
        )
        assert result["backend"] == "replicate"
        assert len(result["valid_images"]) == 8
        assert "cli_command" in result["plan"]


# =============================================================================
# Brand-visual editorial integration
# =============================================================================


class TestEditorialBrandVisual:
    def test_loader_finds_default(self) -> None:
        from core.editorial import reload_editorial

        r = reload_editorial()
        # editorial/brand-visual.json del repo trae "default" tenant
        assert "default" in r.brand_visual

    def test_loader_finds_real_tenants(self) -> None:
        from core.editorial import reload_editorial

        r = reload_editorial()
        # Ahora tenemos también ruteo y ciencia
        assert "ruteo" in r.brand_visual
        assert "ciencia" in r.brand_visual

    def test_ruteo_has_lora_configured(self) -> None:
        from core.editorial import reload_editorial

        r = reload_editorial()
        bv = r.get_visual_for_tenant("ruteo")
        assert bv is not None
        assert bv.lora_name == "ruteo_brand_v1.safetensors"
        assert bv.primary_workflow_id == "flux_lora_brand"
        assert "Veracruz" in bv.style_suffix

    def test_get_visual_for_tenant_known(self) -> None:
        from core.editorial import reload_editorial

        r = reload_editorial()
        bv = r.get_visual_for_tenant("default")
        assert bv is not None
        assert bv.tenant_id == "default"

    def test_get_visual_for_tenant_unknown_falls_back_to_default(self) -> None:
        from core.editorial import reload_editorial

        r = reload_editorial()
        bv = r.get_visual_for_tenant("does_not_exist")
        # default existe, así que devuelve default
        assert bv is not None
        assert bv.tenant_id == "default"


# =============================================================================
# Selector integration
# =============================================================================


class TestSelectorComfyUIPriority:
    def test_comfyui_chosen_when_enabled_and_tenant_has_lora(self) -> None:
        from core.visual.selector import _ia_source

        # Configurar mock con ComfyUI enabled + tenant con lora
        fake = MagicMock()
        fake.visual.higgsfield.soul_enabled = False
        fake.visual.higgsfield.credentials = ""
        fake.visual.higgsfield.key_id = ""
        fake.visual.higgsfield.key_secret = ""
        fake.visual.higgsfield.soul_default_reference_id = ""
        fake.visual.comfyui.enabled = True
        fake.visual.comfyui.prefer_for_brand_frames = True
        fake.visual.comfyui.default_tenant_id = "ruteo"
        fake.visual.comfyui.tenants = {
            "ruteo": ComfyTenantEntry(
                primary_workflow_id="flux_lora_brand",
                lora_name="ruteo_v1.safetensors",
            )
        }
        with patch("core.visual.selector.load_config", return_value=fake):
            result = _ia_source()
        assert result == VideoSource.COMFYUI

    def test_falls_back_to_gemini_when_no_lora(self) -> None:
        from core.visual.selector import _ia_source

        fake = MagicMock()
        fake.visual.higgsfield.soul_enabled = False
        fake.visual.higgsfield.credentials = ""
        fake.visual.higgsfield.key_id = ""
        fake.visual.higgsfield.key_secret = ""
        fake.visual.higgsfield.soul_default_reference_id = ""
        fake.visual.comfyui.enabled = True
        fake.visual.comfyui.prefer_for_brand_frames = True
        fake.visual.comfyui.default_tenant_id = "default"
        # Tenant default sin LoRA
        fake.visual.comfyui.tenants = {"default": ComfyTenantEntry(lora_name="")}
        with patch("core.visual.selector.load_config", return_value=fake):
            result = _ia_source()
        assert result == VideoSource.GEMINI_IMAGE

    def test_disabled_falls_back_to_gemini(self) -> None:
        from core.visual.selector import _ia_source

        fake = MagicMock()
        fake.visual.higgsfield.soul_enabled = False
        fake.visual.higgsfield.credentials = ""
        fake.visual.higgsfield.key_id = ""
        fake.visual.higgsfield.key_secret = ""
        fake.visual.higgsfield.soul_default_reference_id = ""
        fake.visual.comfyui.enabled = False
        fake.visual.comfyui.prefer_for_brand_frames = True
        fake.visual.comfyui.default_tenant_id = "default"
        fake.visual.comfyui.tenants = {}
        with patch("core.visual.selector.load_config", return_value=fake):
            result = _ia_source()
        assert result == VideoSource.GEMINI_IMAGE
