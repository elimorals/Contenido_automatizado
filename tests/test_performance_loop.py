"""Tests del bucle de retroalimentación — Fase 3 / ADR-031.

`publish → medir → replanificar`. Cubre:

1. Normalización de métricas entre plataformas (cada una las llama distinto)
2. Parseo defensivo: "sin datos" NUNCA se convierte en "cero"
3. Lectura de planes publicados desde disco
4. El resumen que entra al prompt del planner
5. `generate_plan` inyectándolo — y siguiendo sin él cuando no hay nada
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.distribution.analytics import PostMetrics, parse_post_analytics
from core.distribution.upload_post import UploadPostService
from core.editorial.performance import (
    MIN_SAMPLES_PER_PILLAR,
    IdeaPerformance,
    aggregate_by_pillar,
    available_weeks,
    collect_performance,
    published_ideas,
    recent_weeks,
    summarize_for_planner,
)
from shared.schemas import DistributionPlatform, EditorialPlan, EntryType, ReelIdea

# =============================================================================
# Fixtures
# =============================================================================


def _idea(idea_id: str, pillar: str, **kw: Any) -> ReelIdea:
    base: dict[str, Any] = {
        "id": idea_id,
        "title": f"Titulo de {idea_id}",
        "pillar": pillar,
        "audience": "general",
        "hook": f"Hook de {idea_id} que engancha",
        "rationale": "Razon suficientemente larga",
        "platforms": [DistributionPlatform.TIKTOK],
        "entry_type": EntryType.TOPIC,
        "entry_value": f"tema {idea_id}",
        "approved": True,
    }
    base.update(kw)
    return ReelIdea(**base)


def _published(idea_id: str, pillar: str, **kw: Any) -> ReelIdea:
    return _idea(
        idea_id, pillar,
        task_id=f"t-{idea_id}",
        published_at="2026-08-20T10:00:00+00:00",
        upload_request_id=f"req-{idea_id}",
        **kw,
    )


@pytest.fixture
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plans"
    d.mkdir()
    plan = EditorialPlan(
        week="2026-W34",
        generated_at="2026-08-17T00:00:00+00:00",
        ideas=[
            _published("ganadora", "ciencia", cost_usd=0.40),
            _published("perdedora", "historia", cost_usd=0.35),
            _idea("sin-producir", "ciencia"),                      # nunca se produjo
            _idea("producida-no-publicada", "arte", task_id="t-x"),  # sin publicar
        ],
    )
    (d / "plan-2026-W34.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return d


class _FakeService:
    """Servicio con `get_post_analytics`, como espera `collect_performance`."""

    def __init__(self, table: dict[str, dict[str, PostMetrics]]) -> None:
        self.table = table
        self.asked: list[str] = []

    async def get_post_analytics(self, request_id: str) -> dict[str, PostMetrics]:
        self.asked.append(request_id)
        return self.table.get(request_id, {})


# =============================================================================
# 1 + 2. Normalización y parseo defensivo
# =============================================================================


class TestPostMetrics:
    def test_youtube_and_tiktok_use_views(self) -> None:
        m = PostMetrics.from_raw("youtube", {"views": 1200, "likes": 90, "comments": 8})
        assert (m.views, m.likes, m.comments) == (1200, 90, 8)

    def test_instagram_reach_maps_to_views(self) -> None:
        m = PostMetrics.from_raw("instagram", {"reach": 800, "saves": 12, "shares": 4})
        assert m.views == 800
        assert m.saves == 12

    def test_reach_wins_over_impressions(self) -> None:
        """Reach cuenta personas; impressions cuenta exposiciones e infla."""
        m = PostMetrics.from_raw("instagram", {"reach": 500, "impressions": 900})
        assert m.views == 500

    def test_facebook_reactions_count_as_likes(self) -> None:
        assert PostMetrics.from_raw("facebook", {"reactions": 33}).likes == 33

    def test_engagement_rate_is_interactions_over_views(self) -> None:
        m = PostMetrics.from_raw("tiktok", {"views": 1000, "likes": 80, "comments": 20})
        assert m.interactions == 100
        assert m.engagement_rate == 0.1

    def test_no_views_yields_zero_rate_not_division_error(self) -> None:
        assert PostMetrics.from_raw("tiktok", {"likes": 5}).engagement_rate == 0.0

    def test_measured_distinguishes_empty_from_zero(self) -> None:
        assert PostMetrics.from_raw("tiktok", {}).measured is False
        assert PostMetrics.from_raw("tiktok", {"likes": 1}).measured is True

    def test_raw_is_preserved(self) -> None:
        m = PostMetrics.from_raw("youtube", {"views": 5, "watch_time_minutes": 42})
        assert m.raw["watch_time_minutes"] == 42

    def test_negative_or_non_numeric_values_ignored(self) -> None:
        m = PostMetrics.from_raw("tiktok", {"views": -1, "likes": "muchos"})
        assert m.views == 0
        assert m.likes == 0


class TestParseAnalytics:
    def test_parses_platforms(self) -> None:
        out = parse_post_analytics({
            "success": True,
            "platforms": {
                "TIKTOK": {"success": True, "post_metrics": {"views": 10}, "post_url": "u"},
            },
        })
        assert out["tiktok"].views == 10
        assert out["tiktok"].post_url == "u"

    def test_failed_platform_is_omitted_not_zeroed(self) -> None:
        """Un cero medido y un cero por error significan cosas opuestas."""
        out = parse_post_analytics({
            "platforms": {
                "tiktok": {"success": False, "post_metrics_error": "token expired"},
                "instagram": {"success": True, "post_metrics": {"reach": 5}},
            }
        })
        assert "tiktok" not in out
        assert out["instagram"].views == 5

    @pytest.mark.parametrize("payload", [{}, {"platforms": None}, {"platforms": []}])
    def test_malformed_payloads_return_empty(self, payload: dict) -> None:
        assert parse_post_analytics(payload) == {}


class TestServiceAnalytics:
    @pytest.mark.asyncio
    async def test_hits_the_request_id_endpoint(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url.path))
            return httpx.Response(200, json={
                "success": True,
                "platforms": {"tiktok": {"success": True, "post_metrics": {"views": 7}}},
            })

        real = httpx.AsyncClient
        with patch(
            "core.distribution.upload_post.httpx.AsyncClient",
            side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        ):
            svc = UploadPostService(api_key="k")
            out = await svc.get_post_analytics("req-abc")
        assert seen == ["/api/uploadposts/post-analytics/req-abc"]
        assert out["tiktok"].views == 7

    @pytest.mark.asyncio
    async def test_empty_request_id_short_circuits(self) -> None:
        svc = UploadPostService(api_key="k")
        assert await svc.get_post_analytics("") == {}

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_not_raise(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        real = httpx.AsyncClient
        with patch(
            "core.distribution.upload_post.httpx.AsyncClient",
            side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}),
        ):
            svc = UploadPostService(api_key="k")
            assert await svc.get_post_analytics("req") == {}

    @pytest.mark.asyncio
    async def test_default_impl_returns_empty(self) -> None:
        """Un proveedor puede publicar sin ofrecer analytics."""
        from core.distribution.base import DistributionService

        class _Bare(DistributionService):
            async def upload_video(self, *a, **k):  # noqa: ANN001
                ...

            async def check_status(self, request_id):  # noqa: ANN001
                ...

        assert await _Bare().get_post_analytics("r") == {}


# =============================================================================
# 3. Lectura de planes
# =============================================================================


class TestPlanReading:
    def test_recent_weeks_excludes_current(self) -> None:
        import datetime as dt

        weeks = recent_weeks(3, before=dt.datetime(2026, 8, 29, tzinfo=dt.UTC))
        assert len(weeks) == 3
        assert "2026-W35" not in weeks  # la semana en curso todavía no rindió

    def test_available_weeks_sorted_desc(self, plans_dir: Path) -> None:
        (plans_dir / "plan-2026-W30.json").write_text("{}", encoding="utf-8")
        assert available_weeks(plans_dir)[:2] == ["2026-W34", "2026-W30"]

    def test_ignores_non_plan_files(self, plans_dir: Path) -> None:
        (plans_dir / "plan-notaweek.json").write_text("{}", encoding="utf-8")
        assert available_weeks(plans_dir) == ["2026-W34"]

    def test_only_published_ideas_are_returned(self, plans_dir: Path) -> None:
        out = published_ideas(["2026-W34"], plans_dir)
        assert {i.id for _, i in out} == {"ganadora", "perdedora"}

    def test_missing_week_is_skipped(self, plans_dir: Path) -> None:
        assert published_ideas(["2026-W01"], plans_dir) == []

    def test_corrupt_plan_is_skipped_not_fatal(self, plans_dir: Path) -> None:
        (plans_dir / "plan-2026-W33.json").write_text("{ no json", encoding="utf-8")
        out = published_ideas(["2026-W33", "2026-W34"], plans_dir)
        assert len(out) == 2  # las de W34 siguen llegando


class TestCollectPerformance:
    @pytest.mark.asyncio
    async def test_joins_plan_with_metrics(self, plans_dir: Path) -> None:
        svc = _FakeService({
            "req-ganadora": {"tiktok": PostMetrics.from_raw("tiktok", {"views": 10000, "likes": 900})},
            "req-perdedora": {"tiktok": PostMetrics.from_raw("tiktok", {"views": 400, "likes": 4})},
        })
        perfs = await collect_performance(["2026-W34"], service=svc, plans_dir=plans_dir)
        by_id = {p.idea_id: p for p in perfs}
        assert set(by_id) == {"ganadora", "perdedora"}
        assert by_id["ganadora"].engagement_rate > by_id["perdedora"].engagement_rate
        assert svc.asked == ["req-ganadora", "req-perdedora"]

    @pytest.mark.asyncio
    async def test_no_service_returns_empty(self, plans_dir: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "core.distribution.get_distribution_service", lambda cfg: None
        )
        assert await collect_performance(["2026-W34"], plans_dir=plans_dir) == []

    @pytest.mark.asyncio
    async def test_cost_per_1k_views(self, plans_dir: Path) -> None:
        svc = _FakeService({
            "req-ganadora": {"tiktok": PostMetrics.from_raw("tiktok", {"views": 20000})},
        })
        perfs = await collect_performance(["2026-W34"], service=svc, plans_dir=plans_dir)
        winner = next(p for p in perfs if p.idea_id == "ganadora")
        assert winner.cost_per_1k_views == 0.02  # $0.40 / 20 mil-vistas

    @pytest.mark.asyncio
    async def test_analytics_failure_skips_that_idea_only(self, plans_dir: Path) -> None:
        class _Flaky:
            async def get_post_analytics(self, request_id: str):  # noqa: ANN201
                if request_id == "req-ganadora":
                    raise RuntimeError("502")
                return {"tiktok": PostMetrics.from_raw("tiktok", {"views": 100})}

        perfs = await collect_performance(["2026-W34"], service=_Flaky(), plans_dir=plans_dir)
        assert [p.idea_id for p in perfs] == ["perdedora"]


# =============================================================================
# 4. Resumen para el planner
# =============================================================================


def _perf(idea_id: str, pillar: str, views: int, likes: int) -> IdeaPerformance:
    return IdeaPerformance(
        idea_id=idea_id, week="2026-W34", pillar=pillar, audience="general",
        title=f"Titulo {idea_id}", hook=f"Hook {idea_id}",
        published_at="2026-08-20T10:00:00+00:00",
        metrics={"tiktok": PostMetrics.from_raw("tiktok", {"views": views, "likes": likes})},
    )


class TestSummary:
    def test_empty_when_nothing_measured(self) -> None:
        """Un encabezado vacío el LLM lo leería como 'todo rinde mal'."""
        assert summarize_for_planner([]) == ""
        assert summarize_for_planner([_perf("a", "ciencia", 0, 0)]) == ""

    def test_lists_winners_first(self) -> None:
        block = summarize_for_planner([
            _perf("floja", "historia", 1000, 10),
            _perf("fuerte", "ciencia", 1000, 300),
        ])
        assert block.index("Titulo fuerte") < block.index("Titulo floja")

    def test_includes_the_hook_not_just_the_title(self) -> None:
        # El ángulo del hook es lo transferible a temas nuevos.
        assert "Hook fuerte" in summarize_for_planner([_perf("fuerte", "ciencia", 100, 50)])

    def test_warns_against_copying_the_topic(self) -> None:
        block = summarize_for_planner([_perf("a", "ciencia", 100, 50)])
        assert "SEÑAL" in block
        assert "rotando pilares" in block

    def test_unmeasured_pieces_excluded_from_ranking(self) -> None:
        block = summarize_for_planner([
            _perf("medida", "ciencia", 100, 50),
            _perf("sin-datos", "historia", 0, 0),
        ])
        assert "Titulo sin-datos" not in block


class TestAggregation:
    def test_needs_minimum_samples(self) -> None:
        assert MIN_SAMPLES_PER_PILLAR >= 2
        # Con una sola pieza, el "promedio" del pilar es esa pieza.
        assert aggregate_by_pillar([_perf("a", "ciencia", 100, 50)]) == {}

    def test_averages_over_measured_only(self) -> None:
        out = aggregate_by_pillar([
            _perf("a", "ciencia", 1000, 100),
            _perf("b", "ciencia", 3000, 300),
            _perf("c", "ciencia", 0, 0),  # sin datos: no debe bajar el promedio
        ])
        assert out["ciencia"]["n"] == 2
        assert out["ciencia"]["avg_views"] == 2000.0
        assert out["ciencia"]["avg_engagement"] == 0.1


# =============================================================================
# 5. Inyección en el planner
# =============================================================================


class TestPlannerInjection:
    def test_system_prompt_carries_the_block(self) -> None:
        from core.editorial.loader import EditorialRegistry
        from core.editorial.plan import _build_system_prompt

        reg = EditorialRegistry(root=Path("."))
        block = "\n## RENDIMIENTO REAL DE SEMANAS ANTERIORES\n- algo"
        prompt = _build_system_prompt(reg, 7, block)
        assert "RENDIMIENTO REAL" in prompt
        # Contexto para decidir, no una regla que obedecer: va antes de REGLAS.
        assert prompt.index("RENDIMIENTO REAL") < prompt.index("## REGLAS")

    def test_prompt_unchanged_without_performance(self) -> None:
        from core.editorial.loader import EditorialRegistry
        from core.editorial.plan import _build_system_prompt

        reg = EditorialRegistry(root=Path("."))
        assert _build_system_prompt(reg, 7) == _build_system_prompt(reg, 7, "")

    @pytest.mark.asyncio
    async def test_generate_plan_survives_analytics_failure(self, monkeypatch) -> None:
        """Medir nunca debe bloquear planificar."""
        from core.editorial import plan as plan_mod

        monkeypatch.setattr(
            "core.editorial.performance.collect_performance",
            AsyncMock(side_effect=RuntimeError("analytics caído")),
        )
        captured: dict[str, Any] = {}

        async def _fake_structured(**kwargs: Any):  # noqa: ANN202
            captured.update(kwargs)
            return plan_mod._PlanLLMResponse(ideas=[_idea("x", "ciencia")])

        monkeypatch.setattr(plan_mod, "complete_structured", _fake_structured)
        monkeypatch.setattr(plan_mod, "save_plan", lambda p: Path("/tmp/x.json"))

        the_plan, _, _ = await plan_mod.generate_plan(n_ideas=1)
        assert len(the_plan.ideas) == 1
        assert "RENDIMIENTO REAL" not in captured["system"]

    @pytest.mark.asyncio
    async def test_generate_plan_injects_when_data_exists(self, monkeypatch) -> None:
        from core.editorial import plan as plan_mod

        monkeypatch.setattr(
            "core.editorial.performance.collect_performance",
            AsyncMock(return_value=[_perf("fuerte", "ciencia", 5000, 500)]),
        )
        captured: dict[str, Any] = {}

        async def _fake_structured(**kwargs: Any):  # noqa: ANN202
            captured.update(kwargs)
            return plan_mod._PlanLLMResponse(ideas=[_idea("x", "ciencia")])

        monkeypatch.setattr(plan_mod, "complete_structured", _fake_structured)
        monkeypatch.setattr(plan_mod, "save_plan", lambda p: Path("/tmp/x.json"))

        await plan_mod.generate_plan(n_ideas=1)
        assert "RENDIMIENTO REAL" in captured["system"]
        assert "Hook fuerte" in captured["system"]

    @pytest.mark.asyncio
    async def test_use_performance_false_skips_the_lookup(self, monkeypatch) -> None:
        from core.editorial import plan as plan_mod

        spy = AsyncMock(return_value=[])
        monkeypatch.setattr("core.editorial.performance.collect_performance", spy)

        async def _fake_structured(**kwargs: Any):  # noqa: ANN202
            return plan_mod._PlanLLMResponse(ideas=[_idea("x", "ciencia")])

        monkeypatch.setattr(plan_mod, "complete_structured", _fake_structured)
        monkeypatch.setattr(plan_mod, "save_plan", lambda p: Path("/tmp/x.json"))

        await plan_mod.generate_plan(n_ideas=1, use_performance=False)
        spy.assert_not_awaited()


# =============================================================================
# Round-trip del plan con métricas
# =============================================================================


def test_plan_with_publication_data_round_trips(tmp_path: Path) -> None:
    """Lo que no serializa, no existe entre corridas."""
    plan = EditorialPlan(
        week="2026-W34",
        generated_at="2026-08-17T00:00:00+00:00",
        ideas=[_published("a", "ciencia", cost_usd=0.5)],
    )
    p = tmp_path / "plan.json"
    p.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    restored = EditorialPlan(**json.loads(p.read_text(encoding="utf-8")))
    assert restored.ideas[0].upload_request_id == "req-a"
    assert restored.ideas[0].cost_usd == 0.5
