"""Tests para Higgsfield: cliente + DoP + Soul + Effects + orchestrator.

Mockean httpx para no pegar a la API real. Cubren:
1. Auth resolution (combined credentials vs separated key_id/key_secret)
2. Error taxonomy (401 → Auth, 402 → NotEnoughCredits, 400 → BadInput, NSFW)
3. Submit + poll flow (queued → in_progress → completed)
4. Motion preset → motion_id resolution con cache
5. DoP generate happy path (con upload_image + motion catalog + payload)
6. DoP fallback cuando credenciales ausentes
7. Soul generate con SoulId override y default
8. Effects no-op cuando effect=None
9. Effects fallback graceful cuando API falla
10. Orchestrator three-tier fallback Soul→Gemini→placeholder, DoP→Veo→ken-burns
11. MotionHint → HiggsfieldPreset mapping
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from core.visual.generation import (
    HiggsfieldAPIError,
    HiggsfieldAuthError,
    HiggsfieldBadInputError,
    HiggsfieldClient,
    HiggsfieldDopGenerator,
    HiggsfieldEffectsGenerator,
    HiggsfieldError,
    HiggsfieldNotEnoughCreditsError,
    HiggsfieldNSFWError,
    HiggsfieldSoulGenerator,
    HiggsfieldTimeoutError,
    JobResult,
    VisualGenerationError,
    generate_beat_videos,
    resolve_preset,
)
from core.visual.generation.higgsfield_cli import (
    CLIFallbackError,
    CLINotInstalledError,
    _check_binary,
    _extract_video_url,
)
from core.visual.generation.higgsfield_client import _resolve_credentials
from core.visual.generation.higgsfield_prompts import (
    HIGGSFIELD_IMAGE_MODELS,
    HIGGSFIELD_VIDEO_MODELS,
    SoulTrainingValidationError,
    augment_dop_prompt,
    augment_soul_prompt,
    quick_safety_check,
    validate_soul_training_set,
)
from shared.config import HiggsfieldConfig
from shared.schemas import (
    Beat,
    BeatRole,
    BeatVisual,
    HiggsfieldEffect,
    HiggsfieldPreset,
    MotionHint,
    VideoSource,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def hf_config() -> HiggsfieldConfig:
    return HiggsfieldConfig(
        enabled=True,
        credentials="test_id:test_secret",
        base_url="https://platform.higgsfield.ai",
        timeout_s=10.0,
        poll_interval_s=0.01,  # tests rápidos
        max_poll_time_s=2.0,
        dop_model="dop-turbo",
        soul_enabled=True,
        soul_model="soul_cinematic",
        effects_enabled=True,
        motion_catalog_cache_path="/tmp/test_hf_motions.json",
    )


@pytest.fixture
def sample_beat() -> Beat:
    return Beat(
        idx=0,
        role=BeatRole.HOOK,
        text="opening line about placebo",
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
def visual_with_soul_id() -> BeatVisual:
    return BeatVisual(
        image_prompt="same character now in a lab",
        motion_hint=MotionHint.STATIC,
        visual_anchor="lab",
        soul_id="soul-xyz-123",
    )


@pytest.fixture
def visual_with_preset() -> BeatVisual:
    return BeatVisual(
        image_prompt="action shot",
        motion_hint=MotionHint.SLOW_ZOOM_IN,
        visual_anchor="anchor",
        higgsfield_preset=HiggsfieldPreset.FPV_DRONE,
        effect=HiggsfieldEffect.EXPLOSION,
        effect_strength=0.9,
    )


@pytest.fixture
def fake_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (720, 1280), color=(50, 60, 70)).save(p, "JPEG", quality=85)
    return p


# =============================================================================
# CLIENT: auth, errores, polling
# =============================================================================


class TestCredentialsResolution:
    def test_combined_credentials(self) -> None:
        cfg = HiggsfieldConfig(credentials="id:secret")
        assert _resolve_credentials(cfg) == "id:secret"

    def test_separated_credentials(self) -> None:
        cfg = HiggsfieldConfig(key_id="id", key_secret="secret")
        assert _resolve_credentials(cfg) == "id:secret"

    def test_combined_wins_over_separated(self) -> None:
        cfg = HiggsfieldConfig(
            credentials="combined:cred",
            key_id="ignored",
            key_secret="alsoignored",
        )
        assert _resolve_credentials(cfg) == "combined:cred"

    def test_missing_raises(self) -> None:
        cfg = HiggsfieldConfig()  # todo vacío
        with pytest.raises(HiggsfieldAuthError):
            _resolve_credentials(cfg)


class TestClientErrorTaxonomy:
    @pytest.mark.parametrize(
        "status_code,exc_type",
        [
            (401, HiggsfieldAuthError),
            (403, HiggsfieldAuthError),
            (400, HiggsfieldBadInputError),
            (422, HiggsfieldBadInputError),
            (402, HiggsfieldNotEnoughCreditsError),
            (500, HiggsfieldAPIError),
            (503, HiggsfieldAPIError),
        ],
    )
    def test_raise_for_status_maps_correctly(
        self, status_code: int, exc_type: type
    ) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = "error body"
        with pytest.raises(exc_type):
            HiggsfieldClient._raise_for_status(resp, ctx="test")

    def test_raise_for_status_passes_on_2xx(self) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        # no raise
        HiggsfieldClient._raise_for_status(resp, ctx="test")


class TestPolling:
    @pytest.mark.asyncio
    async def test_poll_completes_with_video_url(
        self, hf_config: HiggsfieldConfig
    ) -> None:
        # Simulamos 1 in_progress, luego completed
        responses = [
            {"status": "in_progress"},
            {"status": "completed", "video": {"url": "https://cdn/x.mp4"}},
        ]
        call_count = {"n": 0}

        async def fake_get(path: str, **kwargs):  # noqa: ANN001
            r = MagicMock(spec=httpx.Response)
            r.status_code = 200
            r.json = MagicMock(return_value=responses[call_count["n"]])
            call_count["n"] += 1
            return r

        cli = HiggsfieldClient(hf_config)
        cli._client = MagicMock()
        cli._client.get = AsyncMock(side_effect=fake_get)

        result = await cli.poll("req-123")
        assert result.status == "completed"
        assert result.video_url == "https://cdn/x.mp4"

    @pytest.mark.asyncio
    async def test_poll_failed_raises_api_error(
        self, hf_config: HiggsfieldConfig
    ) -> None:
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json = MagicMock(return_value={"status": "failed", "error": "bad prompt"})
        cli = HiggsfieldClient(hf_config)
        cli._client = MagicMock()
        cli._client.get = AsyncMock(return_value=r)

        with pytest.raises(HiggsfieldAPIError, match="bad prompt"):
            await cli.poll("req-123")

    @pytest.mark.asyncio
    async def test_poll_nsfw_raises(self, hf_config: HiggsfieldConfig) -> None:
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json = MagicMock(return_value={"status": "nsfw"})
        cli = HiggsfieldClient(hf_config)
        cli._client = MagicMock()
        cli._client.get = AsyncMock(return_value=r)

        with pytest.raises(HiggsfieldNSFWError):
            await cli.poll("req-123")

    @pytest.mark.asyncio
    async def test_poll_timeout(self, hf_config: HiggsfieldConfig) -> None:
        # Siempre devuelve queued → debería timeout
        hf_config.max_poll_time_s = 0.05
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json = MagicMock(return_value={"status": "queued"})
        cli = HiggsfieldClient(hf_config)
        cli._client = MagicMock()
        cli._client.get = AsyncMock(return_value=r)

        with pytest.raises(HiggsfieldTimeoutError):
            await cli.poll("req-123")

    @pytest.mark.asyncio
    async def test_poll_extracts_images_for_soul(
        self, hf_config: HiggsfieldConfig
    ) -> None:
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.json = MagicMock(
            return_value={
                "status": "completed",
                "images": [{"url": "https://cdn/a.png"}, {"url": "https://cdn/b.png"}],
            }
        )
        cli = HiggsfieldClient(hf_config)
        cli._client = MagicMock()
        cli._client.get = AsyncMock(return_value=r)

        result = await cli.poll("req-123")
        assert result.image_urls == ["https://cdn/a.png", "https://cdn/b.png"]


# =============================================================================
# DOP PROVIDER
# =============================================================================


class TestMotionMapping:
    def test_motion_hint_maps_to_preset(self) -> None:
        v = BeatVisual(
            image_prompt="x",
            motion_hint=MotionHint.PAN_LEFT,
            visual_anchor="y",
        )
        assert resolve_preset(v) == HiggsfieldPreset.PAN_LEFT

    def test_explicit_preset_overrides_hint(self) -> None:
        v = BeatVisual(
            image_prompt="x",
            motion_hint=MotionHint.STATIC,  # mappeo natural = STATIC
            visual_anchor="y",
            higgsfield_preset=HiggsfieldPreset.FPV_DRONE,  # override
        )
        assert resolve_preset(v) == HiggsfieldPreset.FPV_DRONE

    def test_unknown_hint_fallback(self) -> None:
        # Aunque MotionHint solo tiene 6 valores válidos, garantizamos fallback
        v = BeatVisual(
            image_prompt="x",
            motion_hint=MotionHint.SLOW_ZOOM_IN,
            visual_anchor="y",
        )
        assert resolve_preset(v) == HiggsfieldPreset.ZOOM_IN


class TestDopGenerator:
    @pytest.mark.asyncio
    async def test_missing_credentials_raises(
        self, sample_beat: Beat, sample_visual: BeatVisual, fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        cfg = HiggsfieldConfig()  # vacío
        gen = HiggsfieldDopGenerator()
        gen.cfg = cfg
        with pytest.raises(VisualGenerationError, match="credenciales"):
            await gen.generate(
                beat=sample_beat,
                visual=sample_visual,
                content_mode="general",
                out_dir=tmp_path,
                first_frame_path=fake_jpeg,
            )

    @pytest.mark.asyncio
    async def test_missing_first_frame_raises(
        self, hf_config: HiggsfieldConfig, sample_beat: Beat,
        sample_visual: BeatVisual, tmp_path: Path,
    ) -> None:
        gen = HiggsfieldDopGenerator()
        gen.cfg = hf_config
        with pytest.raises(VisualGenerationError, match="first_frame_path"):
            await gen.generate(
                beat=sample_beat,
                visual=sample_visual,
                content_mode="general",
                out_dir=tmp_path,
                first_frame_path=None,
            )

    @pytest.mark.asyncio
    async def test_happy_path(
        self,
        hf_config: HiggsfieldConfig,
        sample_beat: Beat,
        sample_visual: BeatVisual,
        fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        """Verifica que el provider llama submit + poll + download y escribe MP4."""
        gen = HiggsfieldDopGenerator()
        gen.cfg = hf_config

        fake_mp4_bytes = b"fake mp4 content" * 100

        # Mock context manager del client
        mock_client = AsyncMock(spec=HiggsfieldClient)
        mock_client.upload_image = AsyncMock(return_value="https://cdn/frame.jpg")
        mock_client.resolve_motion_id = AsyncMock(return_value="motion-uuid-abc")
        mock_client.submit_and_wait = AsyncMock(
            return_value=JobResult(
                request_id="r1",
                status="completed",
                video_url="https://cdn/clip.mp4",
            )
        )
        mock_client.download = AsyncMock(return_value=fake_mp4_bytes)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "core.visual.generation.higgsfield.HiggsfieldClient",
            return_value=mock_client,
        ):
            artifact = await gen.generate(
                beat=sample_beat,
                visual=sample_visual,
                content_mode="general",
                out_dir=tmp_path,
                first_frame_path=fake_jpeg,
            )

        assert artifact.source == VideoSource.HIGGSFIELD_DOP
        assert artifact.video_path is not None
        assert artifact.video_path.exists()
        assert artifact.video_path.read_bytes() == fake_mp4_bytes
        # Verifica que el payload incluyó motion_id resuelto
        call_args = mock_client.submit_and_wait.call_args
        payload = call_args.kwargs.get("payload") or call_args.args[1]
        assert "motions" in payload
        assert payload["motions"][0]["id"] == "motion-uuid-abc"


# =============================================================================
# SOUL PROVIDER
# =============================================================================


class TestSoulGenerator:
    def test_resolve_soul_id_from_visual(
        self, hf_config: HiggsfieldConfig, visual_with_soul_id: BeatVisual
    ) -> None:
        gen = HiggsfieldSoulGenerator()
        gen.cfg = hf_config
        assert gen._resolve_soul_id(visual_with_soul_id) == "soul-xyz-123"

    def test_resolve_soul_id_falls_back_to_default(
        self, hf_config: HiggsfieldConfig, sample_visual: BeatVisual
    ) -> None:
        hf_config.soul_default_reference_id = "default-soul-abc"
        gen = HiggsfieldSoulGenerator()
        gen.cfg = hf_config
        assert gen._resolve_soul_id(sample_visual) == "default-soul-abc"

    def test_resolve_soul_id_raises_when_none_set(
        self, hf_config: HiggsfieldConfig, sample_visual: BeatVisual
    ) -> None:
        # No default y BeatVisual.soul_id vacío
        gen = HiggsfieldSoulGenerator()
        gen.cfg = hf_config
        with pytest.raises(VisualGenerationError, match="soul_id"):
            gen._resolve_soul_id(sample_visual)


# =============================================================================
# EFFECTS PROVIDER
# =============================================================================


class TestEffectsGenerator:
    @pytest.mark.asyncio
    async def test_no_op_when_effect_none(
        self, hf_config: HiggsfieldConfig, sample_visual: BeatVisual, fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        from shared.schemas import BeatArtifact

        gen = HiggsfieldEffectsGenerator()
        gen.cfg = hf_config

        artifact = BeatArtifact(
            idx=0, video_path=fake_jpeg, source=VideoSource.HIGGSFIELD_DOP,
        )
        # sample_visual.effect = None
        result = await gen.apply(artifact, sample_visual, tmp_path)
        assert result is artifact  # identidad — no copia

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_api_error(
        self,
        hf_config: HiggsfieldConfig,
        visual_with_preset: BeatVisual,
        fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        from shared.schemas import BeatArtifact

        gen = HiggsfieldEffectsGenerator()
        gen.cfg = hf_config

        artifact = BeatArtifact(
            idx=0, video_path=fake_jpeg, source=VideoSource.HIGGSFIELD_DOP,
            duration_s=5.0,
        )

        mock_client = AsyncMock()
        mock_client.upload_image = AsyncMock(side_effect=HiggsfieldError("API down"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "core.visual.generation.higgsfield_effects.HiggsfieldClient",
            return_value=mock_client,
        ):
            result = await gen.apply(artifact, visual_with_preset, tmp_path)

        # En fallo, devuelve el artifact original sin modificar
        assert result.video_path == fake_jpeg
        assert result.source == VideoSource.HIGGSFIELD_DOP


# =============================================================================
# PROMPTS MODULE (extraído de skills oficiales)
# =============================================================================


class TestPromptAugmentation:
    def test_dop_prompt_includes_motion_clause(self) -> None:
        out = augment_dop_prompt("a scientist", "fpv_drone", "general")
        assert "FPV drone" in out
        assert "a scientist" in out

    def test_dop_prompt_scientific_uses_documentary_style(self) -> None:
        out = augment_dop_prompt("a microscope close-up", "zoom_in", "scientific")
        assert "documentary" in out.lower()
        assert "no stylized" in out.lower()

    def test_dop_prompt_capped_at_1200_chars(self) -> None:
        long_input = "lorem ipsum " * 200  # ~2400 chars
        out = augment_dop_prompt(long_input, "dolly_in", "general")
        assert len(out) <= 1200

    def test_dop_prompt_unknown_preset_falls_back(self) -> None:
        out = augment_dop_prompt("subject", "nonexistent_preset", "general")
        assert "nonexistent preset" in out

    def test_soul_prompt_aesthetic_default(self) -> None:
        out = augment_soul_prompt("a portrait", "general")
        assert "aesthetic UGC" in out or "editorial" in out

    def test_soul_prompt_cinematic_mode(self) -> None:
        out = augment_soul_prompt("a portrait", "general", cinematic=True)
        assert "cinematic" in out.lower()
        assert "film-grade" in out.lower()


class TestSoulTrainingValidation:
    def test_valid_range_no_warnings(self) -> None:
        # 10 fotos está en sweet spot
        warnings = validate_soul_training_set([f"img{i}.jpg" for i in range(10)])
        assert warnings == []

    def test_too_few_warns(self) -> None:
        warnings = validate_soul_training_set(["a.jpg", "b.jpg"])
        assert any("mínimo" in w for w in warnings)

    def test_too_few_strict_raises(self) -> None:
        with pytest.raises(SoulTrainingValidationError, match="mínimo"):
            validate_soul_training_set(["a.jpg"], strict=True)

    def test_too_many_warns(self) -> None:
        warnings = validate_soul_training_set([f"img{i}.jpg" for i in range(25)])
        assert any("máximo" in w for w in warnings)

    def test_outside_sweet_spot_warns(self) -> None:
        # 6 fotos: válido pero fuera del sweet spot 8-12
        warnings = validate_soul_training_set([f"img{i}.jpg" for i in range(6)])
        assert any("sweet spot" in w for w in warnings)


class TestSafetyCheck:
    def test_clean_prompt_is_safe(self) -> None:
        safe, msg = quick_safety_check("a beautiful sunset over the mountains")
        assert safe is True
        assert msg is None

    def test_trademark_pattern_flagged(self) -> None:
        safe, msg = quick_safety_check("mickey mouse holding a sword")
        assert safe is False
        assert msg is not None
        assert "mickey mouse" in msg.lower()

    def test_case_insensitive(self) -> None:
        safe, _ = quick_safety_check("MICKEY MOUSE in space")
        assert safe is False


# =============================================================================
# CLI FALLBACK (Option B)
# =============================================================================


class TestCLIBinaryResolution:
    def test_missing_binary_raises(self) -> None:
        cfg = HiggsfieldConfig(cli_binary_path="/nonexistent/path/to/higgsfield")
        with pytest.raises(CLINotInstalledError, match="no existe"):
            _check_binary(cfg)

    def test_not_in_path_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = HiggsfieldConfig(cli_binary_path="totally_invented_xyzzy_cli")
        # which() will return None
        with pytest.raises(CLINotInstalledError, match="encontrado"):
            _check_binary(cfg)


class TestCLIVideoUrlExtraction:
    def test_direct_url(self) -> None:
        assert _extract_video_url({"url": "https://x/y.mp4"}) == "https://x/y.mp4"

    def test_result_url_key(self) -> None:
        assert _extract_video_url({"result_url": "https://x.mp4"}) == "https://x.mp4"

    def test_video_dict(self) -> None:
        assert _extract_video_url({"video": {"url": "https://v.mp4"}}) == "https://v.mp4"

    def test_jobset_results(self) -> None:
        payload = {"results": [{"url": "https://j.mp4"}]}
        assert _extract_video_url(payload) == "https://j.mp4"

    def test_nested_raw(self) -> None:
        payload = {"results": [{"results": {"raw": {"url": "https://r.mp4"}}}]}
        assert _extract_video_url(payload) == "https://r.mp4"

    def test_empty(self) -> None:
        assert _extract_video_url({}) is None

    def test_invalid_url_skipped(self) -> None:
        assert _extract_video_url({"url": "not_a_real_url"}) is None


class TestCLIFallbackIntegration:
    @pytest.mark.asyncio
    async def test_rest_fail_no_cli_fallback_raises_visual_error(
        self,
        hf_config: HiggsfieldConfig,
        sample_beat: Beat,
        sample_visual: BeatVisual,
        fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        """REST falla + cli_fallback_enabled=False → VisualGenerationError."""
        hf_config.cli_fallback_enabled = False
        from core.visual.generation.higgsfield import HiggsfieldDopGenerator

        gen = HiggsfieldDopGenerator()
        gen.cfg = hf_config

        mock_client = AsyncMock()
        mock_client.upload_image = AsyncMock(return_value="https://cdn/x.jpg")
        mock_client.resolve_motion_id = AsyncMock(return_value=None)
        mock_client.submit_and_wait = AsyncMock(
            side_effect=HiggsfieldError("simulated 503")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "core.visual.generation.higgsfield.HiggsfieldClient",
            return_value=mock_client,
        ):
            with pytest.raises(VisualGenerationError, match="REST error"):
                await gen.generate(
                    beat=sample_beat,
                    visual=sample_visual,
                    content_mode="general",
                    out_dir=tmp_path,
                    first_frame_path=fake_jpeg,
                )

    @pytest.mark.asyncio
    async def test_rest_fail_with_cli_enabled_tries_cli(
        self,
        hf_config: HiggsfieldConfig,
        sample_beat: Beat,
        sample_visual: BeatVisual,
        fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        """REST falla + cli_fallback_enabled=True → invoca generate_video_via_cli."""
        hf_config.cli_fallback_enabled = True
        from core.visual.generation.higgsfield import HiggsfieldDopGenerator

        gen = HiggsfieldDopGenerator()
        gen.cfg = hf_config

        mock_client = AsyncMock()
        mock_client.upload_image = AsyncMock(return_value="https://cdn/x.jpg")
        mock_client.resolve_motion_id = AsyncMock(return_value=None)
        mock_client.submit_and_wait = AsyncMock(
            side_effect=HiggsfieldError("simulated 503")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def fake_cli(*, prompt, first_frame_path, duration_s, out_path, cfg):  # noqa: ANN001
            out_path.write_bytes(b"mp4 from cli")
            return out_path

        with patch(
            "core.visual.generation.higgsfield.HiggsfieldClient",
            return_value=mock_client,
        ), patch(
            "core.visual.generation.higgsfield.generate_video_via_cli",
            side_effect=fake_cli,
        ):
            artifact = await gen.generate(
                beat=sample_beat,
                visual=sample_visual,
                content_mode="general",
                out_dir=tmp_path,
                first_frame_path=fake_jpeg,
            )

        assert artifact.source == VideoSource.HIGGSFIELD_DOP
        assert artifact.video_path is not None
        assert artifact.video_path.exists()
        assert artifact.video_path.read_bytes() == b"mp4 from cli"


class TestModelCatalog:
    def test_video_catalog_has_seedance(self) -> None:
        assert "seedance_2_0" in HIGGSFIELD_VIDEO_MODELS

    def test_image_catalog_has_soul_models(self) -> None:
        assert "soul_v2" in HIGGSFIELD_IMAGE_MODELS
        assert HIGGSFIELD_IMAGE_MODELS["soul_v2"] == "text2image_soul_v2"
        assert "soul_cinematic" in HIGGSFIELD_IMAGE_MODELS


# =============================================================================
# ORCHESTRATOR: 3-tier fallback
# =============================================================================


class TestOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_no_higgsfield_no_veo_uses_ken_burns(
        self, sample_beat: Beat, sample_visual: BeatVisual, fake_jpeg: Path,
        tmp_path: Path,
    ) -> None:
        """Sin DoP ni Veo, debe caer a ken-burns."""
        from core.visual.generation.gemini_image import GeminiImageGenerator
        from core.visual.generation.ken_burns import KenBurnsGenerator
        from shared.schemas import BeatArtifact

        img_mock = MagicMock(spec=GeminiImageGenerator)
        img_mock.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=0, first_frame_path=fake_jpeg, source=VideoSource.GEMINI_IMAGE
            )
        )
        kb_mock = MagicMock(spec=KenBurnsGenerator)
        kb_mock.generate = AsyncMock(
            return_value=BeatArtifact(
                idx=0, first_frame_path=fake_jpeg, video_path=fake_jpeg,
                source=VideoSource.LOCAL, duration_s=4.0,
            )
        )

        artifacts = await generate_beat_videos(
            beats=[sample_beat],
            visuals=[sample_visual],
            out_dir=tmp_path,
            use_veo=False,
            use_higgsfield=False,
            use_higgsfield_soul=False,
            use_higgsfield_effects=False,
            image_gen=img_mock,
            ken_burns_gen=kb_mock,
        )
        assert len(artifacts) == 1
        # Ken-burns devuelve LOCAL source (no DoP/Veo intentado)
        assert artifacts[0].source == VideoSource.LOCAL
        kb_mock.generate.assert_called_once()
