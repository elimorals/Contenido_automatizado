"""Tests del E2E de publicación — Fases 1 y 2 de ADR-030.

Cubre los seis huecos que cortaban el lazo entre "MP4 en disco" y "publicado":

  A. `produce-week` no pasaba `auto_upload`/`upload_platforms`
  B. `_run()` devolvía None, así que el plan nunca se cerraba
  C. `PlatformSpec` era código muerto: mismo caption para todas, cero hashtags
  D. El scheduling se enviaba como `schedule_at` (la API espera `scheduled_date`)
  E. Re-correr `produce-week` re-publicaba todo
  F. La portada nunca se adjuntaba

Cero red: el transporte httpx va contra `MockTransport`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from core.distribution.captions import (
    PlatformPost,
    build_hashtag_pool,
    build_post,
    build_posts,
    normalize_hashtag,
    truncate_words,
)
from core.distribution.schemas import PLATFORM_TO_PROVIDER, SUPPORTED_PLATFORMS, to_provider
from core.distribution.upload_post import UploadPostService
from shared.schemas import (
    DistributionPlatform,
    EntryType,
    PlatformSpec,
    ReelIdea,
    VideoParams,
)

# =============================================================================
# Fixtures
# =============================================================================


def _spec(
    platform: DistributionPlatform,
    *,
    caption_max: int,
    recommended: tuple[int, int],
    tags: tuple[int, int],
    aspect: str = "9:16",
) -> PlatformSpec:
    return PlatformSpec(
        id=platform,
        aspect_ratio=aspect,
        video_duration_s={"min": 7, "max": 90, "recommended": (20, 30)},
        caption_max_chars=caption_max,
        caption_recommended_chars=recommended,
        hashtags_min=tags[0],
        hashtags_max=tags[1],
    )


@pytest.fixture
def specs() -> dict[DistributionPlatform, PlatformSpec]:
    """Subconjunto realista de `editorial/platforms.json`."""
    return {
        DistributionPlatform.TIKTOK: _spec(
            DistributionPlatform.TIKTOK,
            caption_max=2200, recommended=(80, 150), tags=(3, 5),
        ),
        DistributionPlatform.YOUTUBE_SHORTS: _spec(
            DistributionPlatform.YOUTUBE_SHORTS,
            caption_max=100, recommended=(40, 80), tags=(1, 3),
        ),
        DistributionPlatform.LINKEDIN_VIDEO: _spec(
            DistributionPlatform.LINKEDIN_VIDEO,
            caption_max=3000, recommended=(800, 2000), tags=(3, 5), aspect="16:9",
        ),
    }


@pytest.fixture
def fake_video(tmp_path: Path) -> Path:
    p = tmp_path / "reel.mp4"
    p.write_bytes(b"FAKE_MP4" * 50)
    return p


@pytest.fixture
def fake_cover(tmp_path: Path) -> Path:
    p = tmp_path / "hook_frame.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"JPEG" * 20)
    return p


class _Recorder:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.file_fields: list[str] = []
        self.calls = 0


def _patched_client(recorder: _Recorder, body: dict | None = None) -> Any:
    """Captura el multipart y devuelve una respuesta de éxito."""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.calls += 1
        raw = request.content.decode("latin-1")
        # Parseo mínimo del multipart: nombre de campo → valor (o marca de archivo).
        for chunk in raw.split("--" + request.headers["content-type"].split("boundary=")[-1]):
            if 'name="' not in chunk:
                continue
            name = chunk.split('name="', 1)[1].split('"', 1)[0]
            if "filename=" in chunk:
                recorder.file_fields.append(name)
            else:
                value = chunk.split("\r\n\r\n", 1)[-1].rsplit("\r\n", 1)[0]
                recorder.data[name] = value
        return httpx.Response(200, json=body or {"success": True, "request_id": "r1"})

    real = httpx.AsyncClient

    def fake(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch("core.distribution.upload_post.httpx.AsyncClient", side_effect=fake)


# =============================================================================
# Mapeo editorial ↔ proveedor
# =============================================================================


class TestPlatformMapping:
    def test_every_editorial_platform_maps(self) -> None:
        for platform in DistributionPlatform:
            assert platform in PLATFORM_TO_PROVIDER

    def test_every_provider_is_supported(self) -> None:
        for provider in PLATFORM_TO_PROVIDER.values():
            assert provider in SUPPORTED_PLATFORMS

    def test_shorts_and_long_collapse_to_youtube(self) -> None:
        # La capa editorial los distingue (aspect, duración, caption); el
        # proveedor no.
        assert to_provider(DistributionPlatform.YOUTUBE_SHORTS) == "youtube"
        assert to_provider(DistributionPlatform.YOUTUBE_LONG) == "youtube"

    def test_accepts_strings_from_either_side(self) -> None:
        assert to_provider("instagram_reels") == "instagram"
        assert to_provider("instagram") == "instagram"  # idempotente

    def test_unknown_string_passes_through(self) -> None:
        # No inventamos: que lo rechace `_validate_platforms` con su mensaje.
        assert to_provider("mastodon") == "mastodon"


# =============================================================================
# C — captions y hashtags por plataforma
# =============================================================================


class TestHashtags:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("El efecto placebo", "efectoplacebo"),
            ("#YaTieneHash", "yatienehash"),
            ("Neuro-ciencia!", "neurociencia"),
            ("de la", None),      # solo stopwords
            ("a", None),          # demasiado corto
            ("2026", None),       # solo dígitos
            ("", None),
        ],
    )
    def test_normalize(self, raw: str, expected: str | None) -> None:
        assert normalize_hashtag(raw) == expected

    def test_accents_are_stripped(self) -> None:
        # Un hashtag con tilde se fragmenta distinto según la plataforma.
        assert normalize_hashtag("Depresión") == "depresion"

    def test_pool_preserves_order_and_dedupes(self) -> None:
        pool = build_hashtag_pool(["ciencia"], "Ciencia", ["memoria", "ciencia"])
        assert pool == ["ciencia", "memoria"]

    def test_pool_accepts_mixed_and_none_sources(self) -> None:
        assert build_hashtag_pool(None, "placebo", []) == ["placebo"]

    def test_pool_order_matters_for_narrow_platforms(self) -> None:
        # YouTube Shorts solo acepta 3: los primeros del pool deben ser los
        # más específicos que pasó el caller.
        pool = build_hashtag_pool(["marca"], "ciencia", ["cerebro", "memoria", "sueño"])
        assert pool[:3] == ["marca", "ciencia", "cerebro"]


class TestTruncate:
    def test_short_text_untouched(self) -> None:
        assert truncate_words("hola mundo", 50) == "hola mundo"

    def test_cuts_on_word_boundary(self) -> None:
        out = truncate_words("una frase bastante larga que hay que cortar", 20)
        assert out.endswith("…")
        assert len(out) <= 20
        assert "bastant…" not in out  # no corta a media palabra

    def test_collapses_whitespace(self) -> None:
        assert truncate_words("hola   \n  mundo", 50) == "hola mundo"

    def test_zero_limit(self) -> None:
        assert truncate_words("hola", 0) == ""


class TestBuildPost:
    def test_respects_hashtag_max(self, specs) -> None:
        pool = ["a1", "b2", "c3", "d4", "e5", "f6", "g7"]
        post = build_post(
            specs[DistributionPlatform.YOUTUBE_SHORTS], hook="Hook", hashtag_pool=pool
        )
        assert len(post.hashtags) == 3  # hashtags_max de Shorts

    def test_short_pool_publishes_with_fewer_not_invented(self, specs) -> None:
        post = build_post(
            specs[DistributionPlatform.TIKTOK], hook="Hook", hashtag_pool=["solo"]
        )
        assert post.hashtags == ["solo"]  # min es 3, pero no inventamos

    def test_caption_fits_platform_budget(self, specs) -> None:
        hook = "Por qué el kerning arruina un logo"
        body = "palabra " * 400
        pool = ["tipografia", "diseno", "kerning"]
        for platform, spec in specs.items():
            post = build_post(spec, hook=hook, body=body, hashtag_pool=pool)
            assert len(post.caption) <= spec.caption_max_chars, platform

    def test_hashtags_compete_for_the_budget(self, specs) -> None:
        """El bloque de hashtags va DENTRO del caption, no además de él."""
        spec = specs[DistributionPlatform.YOUTUBE_SHORTS]  # max 100
        long_body = "palabra " * 100
        with_tags = build_post(
            spec, hook="Hook", body=long_body, hashtag_pool=["uno", "dos", "tres"]
        )
        without = build_post(spec, hook="Hook", body=long_body, hashtag_pool=[])
        assert len(with_tags.description) < len(without.description)
        assert len(with_tags.caption) <= spec.caption_max_chars

    def test_title_capped_at_youtube_limit(self, specs) -> None:
        post = build_post(
            specs[DistributionPlatform.LINKEDIN_VIDEO], hook="palabra " * 60
        )
        assert len(post.title) <= 100

    def test_uses_recommended_not_max_length(self, specs) -> None:
        """Facebook PERMITE 63k chars; eso no significa que convenga."""
        spec = specs[DistributionPlatform.LINKEDIN_VIDEO]  # max 3000, rec hi 2000
        post = build_post(spec, hook="Hook", body="palabra " * 900)
        assert len(post.caption) <= 2000

    def test_different_platforms_get_different_captions(self, specs) -> None:
        """El corazón del hueco C: antes las tres recibían lo mismo."""
        posts = build_posts(
            list(specs), specs,
            hook="Por qué el kerning arruina un logo",
            body="palabra " * 300,
            hashtag_pool=["tipografia", "diseno", "kerning", "branding", "ux"],
        )
        captions = {p.caption for p in posts}
        assert len(captions) == 3
        counts = {p.platform: len(p.hashtags) for p in posts}
        assert counts[DistributionPlatform.YOUTUBE_SHORTS] == 3
        assert counts[DistributionPlatform.TIKTOK] == 5

    def test_platform_without_spec_is_skipped(self, specs) -> None:
        posts = build_posts(
            [DistributionPlatform.TIKTOK, DistributionPlatform.FACEBOOK_REELS],
            specs, hook="Hook",
        )
        assert [p.platform for p in posts] == [DistributionPlatform.TIKTOK]

    def test_caption_joins_description_and_tags(self) -> None:
        post = PlatformPost(
            platform=DistributionPlatform.TIKTOK, provider="tiktok",
            title="T", description="cuerpo", hashtags=["uno", "dos"],
        )
        assert post.caption == "cuerpo\n\n#uno #dos"


# =============================================================================
# D + F — scheduling y portada llegan al multipart
# =============================================================================


class TestUploadWireFormat:
    @pytest.mark.asyncio
    async def test_scheduling_uses_scheduled_date_not_schedule_at(
        self, fake_video: Path
    ) -> None:
        """Regresión del hueco D: `schedule_at` la API lo ignora en silencio."""
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video,
                platforms=["tiktok"],
                caption="hola",
                metadata={"scheduled_date": "2026-09-01T18:00:00", "timezone": "America/Mexico_City"},
            )
        assert rec.data["scheduled_date"] == "2026-09-01T18:00:00"
        assert rec.data["timezone"] == "America/Mexico_City"
        assert "schedule_at" not in rec.data

    @pytest.mark.asyncio
    async def test_timezone_defaults_to_utc(self, fake_video: Path) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video, platforms=["tiktok"],
                metadata={"scheduled_date": "2026-09-01T18:00:00"},
            )
        assert rec.data["timezone"] == "UTC"

    @pytest.mark.asyncio
    async def test_no_schedule_sends_no_scheduling_fields(self, fake_video: Path) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(video_path=fake_video, platforms=["tiktok"], metadata={})
        assert "scheduled_date" not in rec.data
        assert "timezone" not in rec.data

    @pytest.mark.asyncio
    async def test_per_platform_titles_and_descriptions(self, fake_video: Path) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video,
                platforms=["tiktok", "youtube"],
                metadata={
                    "posts": [
                        {"provider": "tiktok", "title": "T corto", "caption": "cap tiktok"},
                        {"provider": "youtube", "title": "T youtube", "caption": "cap youtube"},
                    ]
                },
            )
        # `youtube_title` es obligatorio para publicar en YouTube.
        assert rec.data["youtube_title"] == "T youtube"
        assert rec.data["youtube_description"] == "cap youtube"
        assert rec.data["tiktok_title"] == "T corto"

    @pytest.mark.asyncio
    async def test_posts_for_unrequested_platforms_are_ignored(
        self, fake_video: Path
    ) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video,
                platforms=["tiktok"],
                metadata={"posts": [{"provider": "linkedin", "title": "X", "caption": "Y"}]},
            )
        assert "linkedin_title" not in rec.data

    @pytest.mark.asyncio
    async def test_cover_attached_under_per_platform_field(
        self, fake_video: Path, fake_cover: Path
    ) -> None:
        """Hueco F: cada plataforma llama distinto al campo de portada."""
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video,
                platforms=["instagram", "youtube"],
                metadata={"cover_path": str(fake_cover)},
            )
        assert "cover_image" in rec.file_fields   # instagram
        assert "thumbnail" in rec.file_fields     # youtube
        assert "video" in rec.file_fields

    @pytest.mark.asyncio
    async def test_missing_cover_uploads_without_it(
        self, fake_video: Path, tmp_path: Path
    ) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            result = await svc.upload_video(
                video_path=fake_video,
                platforms=["instagram"],
                metadata={"cover_path": str(tmp_path / "no-existe.jpg")},
            )
        assert result.success is True
        assert "cover_image" not in rec.file_fields

    @pytest.mark.asyncio
    async def test_platform_without_cover_field_skips_it(
        self, fake_video: Path, fake_cover: Path
    ) -> None:
        # TikTok usa `cover_timestamp` (ms), no un archivo.
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            await svc.upload_video(
                video_path=fake_video, platforms=["tiktok"],
                metadata={"cover_path": str(fake_cover)},
            )
        assert rec.file_fields == ["video"]

    @pytest.mark.asyncio
    async def test_newly_supported_platforms_reach_the_api(
        self, fake_video: Path
    ) -> None:
        rec = _Recorder()
        with _patched_client(rec):
            svc = UploadPostService(api_key="k", max_retries=1)
            result = await svc.upload_video(
                video_path=fake_video, platforms=["linkedin", "youtube", "facebook"]
            )
        assert result.success is True
        assert {rec.data["platform[0]"], rec.data["platform[1]"], rec.data["platform[2]"]} == {
            "linkedin", "youtube", "facebook",
        }


# =============================================================================
# A + B + E — el plan como registro de lo publicado
# =============================================================================


class TestReelIdeaLifecycle:
    def _idea(self, **kw: Any) -> ReelIdea:
        base: dict[str, Any] = {
            "id": "kerning-101",
            "title": "Por qué el kerning arruina un logo",
            "pillar": "diseno",
            "audience": "disenadores",
            "hook": "Un logo puede morir por dos píxeles",
            "rationale": "Tema evergreen con demanda constante",
            "platforms": [DistributionPlatform.TIKTOK, DistributionPlatform.LINKEDIN_VIDEO],
            "entry_type": EntryType.TOPIC,
            "entry_value": "kerning en logos",
            "approved": True,
        }
        base.update(kw)
        return ReelIdea(**base)

    def test_starts_unproduced_and_unpublished(self) -> None:
        idea = self._idea()
        assert idea.task_id is None
        assert idea.published_at is None
        assert idea.upload_urls == {}

    def test_publish_at_is_optional(self) -> None:
        assert self._idea().publish_at is None
        assert self._idea(publish_at="2026-09-01T18:00:00").publish_at is not None

    def test_published_idea_round_trips_through_json(self) -> None:
        """El plan vive en disco: lo que no serializa, no existe entre corridas."""
        idea = self._idea(
            task_id="t1", output_path="/out/r.mp4", cost_usd=0.42,
            published_at="2026-08-29T18:00:00+00:00",
            upload_request_id="req-1",
            upload_urls={"tiktok": "https://tiktok.com/x"},
        )
        restored = ReelIdea.model_validate_json(idea.model_dump_json())
        assert restored.published_at == idea.published_at
        assert restored.upload_urls == {"tiktok": "https://tiktok.com/x"}
        assert restored.cost_usd == 0.42

    def test_platforms_translate_to_provider_names(self) -> None:
        """Hueco A: el plan habla editorial, el proveedor habla lo suyo."""
        idea = self._idea()
        params = VideoParams(
            topic=idea.entry_value,
            auto_upload=True,
            upload_platforms=[p.value for p in idea.platforms],
        )
        assert [to_provider(p) for p in params.upload_platforms] == ["tiktok", "linkedin"]

    def test_video_params_carries_scheduling_and_hashtags(self) -> None:
        params = VideoParams(
            topic="x", hashtags=["marca"], publish_at="2026-09-01T18:00:00",
            timezone="America/Mexico_City",
        )
        assert params.hashtags == ["marca"]
        assert params.timezone == "America/Mexico_City"

    def test_defaults_keep_publishing_off(self) -> None:
        """Publicar es irreversible: nunca por accidente."""
        params = VideoParams(topic="x")
        assert params.auto_upload is False
        assert params.publish_at is None
