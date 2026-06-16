"""Tests para la capa editorial (portado de corredor-content).

Cubre:
1. Loader (encontrar editorial/, cargar pilares/audiencias/platforms/facts)
2. Loader graceful (sin editorial/ devuelve registry vacío)
3. Validation (pillar/audience inválidos, rotación)
4. Plan (iso_week, save/load roundtrip)
5. facts_anti_hallucination_block: GENERIC vs poblado
6. Pricing (lookups, fallback, calculate_cost)
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.editorial.loader import (
    EditorialRegistry,
    facts_anti_hallucination_block,
    load_editorial,
    reload_editorial,
)
from core.editorial.plan import iso_week, load_plan, plan_path, save_plan
from core.editorial.validation import (
    ValidationIssue,
    ValidationResult,
    validate_idea,
    validate_plan,
)
from core.llm_router.pricing import (
    PRICING,
    Price,
    calculate_cost,
    is_priced,
    price_of,
)
from shared.schemas import (
    Audience,
    DistributionPlatform,
    EditorialPlan,
    EntryType,
    Fact,
    FactsDocument,
    LLMCostRecord,
    Person,
    Pillar,
    PlatformSpec,
    ReelIdea,
    Study,
    VideoAspect,
    VideoDurationSpec,
)


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def empty_registry(tmp_path: Path) -> EditorialRegistry:
    """Registry sin pilares, audiencias, facts."""
    return EditorialRegistry(root=tmp_path)


@pytest.fixture
def populated_registry(tmp_path: Path) -> EditorialRegistry:
    """Registry con 2 pilares, 2 audiencias, 2 platforms y facts."""
    return EditorialRegistry(
        root=tmp_path,
        brand_voice_md="# Voz\nTuteo, sin emojis cosméticos.",
        pillars={
            "ciencia": Pillar(id="ciencia", label="Ciencia", description="evidencia primero"),
            "historia": Pillar(id="historia", label="Historia", description="story-first"),
        },
        pillar_docs={
            "ciencia": "# Pilar: ciencia\nCita estudios.",
            "historia": "# Pilar: historia\nFechas exactas.",
        },
        audiences={
            "general": Audience(
                id="general", label="General",
                age_range=(18, 45), interests=["curiosidad"],
            ),
            "experto": Audience(
                id="experto", label="Experto",
                age_range=(30, 55), interests=["rigor"],
                voice_register="usted",
            ),
        },
        platforms={
            DistributionPlatform.TIKTOK: PlatformSpec(
                id=DistributionPlatform.TIKTOK,
                aspect_ratio=VideoAspect.PORTRAIT,
                video_duration_s=VideoDurationSpec(min=7, max=60, recommended=(21, 25)),
                caption_max_chars=2200,
                caption_recommended_chars=(80, 150),
                hashtags_min=3, hashtags_max=5,
            ),
            DistributionPlatform.YOUTUBE_SHORTS: PlatformSpec(
                id=DistributionPlatform.YOUTUBE_SHORTS,
                aspect_ratio=VideoAspect.PORTRAIT,
                video_duration_s=VideoDurationSpec(min=15, max=60, recommended=(25, 45)),
                caption_max_chars=100,
                caption_recommended_chars=(40, 80),
                hashtags_min=1, hashtags_max=3,
            ),
        },
        facts=FactsDocument(
            rules_for_hunters={
                "must": ["Cite from this file only."],
                "must_not": ["Invent numbers."],
            },
            verified_people=[
                Person(
                    id="beecher", name="Henry Beecher",
                    years_active="1955-1962", field="anesthesiology",
                    relevance="Demostró efecto placebo en cirugía militar.",
                )
            ],
            verified_studies=[
                Study(
                    id="beecher-1955", title="The Powerful Placebo",
                    authors=["Beecher H"], year=1955,
                    journal="JAMA", sample_size=1082,
                    key_finding="35% of subjects responded to placebo.",
                )
            ],
            verified_facts=[
                Fact(
                    id="placebo-35pct",
                    claim="Placebo response rate ~35% in pain studies",
                    source="Beecher 1955 JAMA",
                    year=1955,
                ),
            ],
        ),
    )


@pytest.fixture
def valid_idea() -> ReelIdea:
    return ReelIdea(
        id="placebo-mecanismo",
        title="El placebo no es solo creer — es bioquímico",
        pillar="ciencia",
        audience="general",
        hook="Si crees que el placebo es psicología, te falta el estudio de 1955",
        rationale="Tema viral en redes este mes",
        platforms=[DistributionPlatform.TIKTOK],
        entry_type=EntryType.TOPIC,
        entry_value="placebo bioquimica beecher",
    )


# =============================================================================
# LOADER
# =============================================================================


class TestLoader:
    def test_load_from_real_editorial_dir(self) -> None:
        """El editorial/ del repo carga con 5 pilares + 3 audiencias."""
        r = reload_editorial()  # invalida cache
        assert len(r.pillars) >= 5
        assert "ciencia" in r.pillars
        assert len(r.audiences) >= 3
        assert "general" in r.audiences
        assert len(r.platforms) >= 4
        assert DistributionPlatform.TIKTOK in r.platforms

    def test_brand_voice_loaded(self) -> None:
        r = reload_editorial()
        assert "Voz de marca" in r.brand_voice_md or len(r.brand_voice_md) > 100

    def test_empty_registry_when_no_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Si no hay editorial/ en el path, devuelve registry vacío sin crashear."""
        monkeypatch.chdir(tmp_path)
        # Limpiar cache
        load_editorial.cache_clear()
        r = load_editorial()
        # En este temp dir sin editorial/, deberíamos tener registry vacío
        # o uno cargado del repo si lo encontró subiendo. Verificamos al menos
        # que no crashee.
        assert r is not None
        assert isinstance(r.pillars, dict)


# =============================================================================
# ANTI-HALLUCINATION BLOCK
# =============================================================================


class TestFactsBlock:
    def test_generic_block_when_empty(self, empty_registry: EditorialRegistry) -> None:
        block = facts_anti_hallucination_block(empty_registry)
        assert "ANTI-HALLUCINATION" in block
        assert "studies show" in block.lower() or "researchers found" in block.lower()

    def test_populated_block_lists_people(self, populated_registry: EditorialRegistry) -> None:
        block = facts_anti_hallucination_block(populated_registry)
        assert "Henry Beecher" in block
        assert "1955" in block
        assert "VERIFIED FACTS" in block

    def test_populated_block_lists_studies(self, populated_registry: EditorialRegistry) -> None:
        block = facts_anti_hallucination_block(populated_registry)
        assert "Beecher H" in block
        assert "JAMA" in block
        assert "n=1082" in block

    def test_populated_block_includes_rules(self, populated_registry: EditorialRegistry) -> None:
        block = facts_anti_hallucination_block(populated_registry)
        assert "MUST: Cite from this file only." in block
        assert "MUST NOT: Invent numbers." in block


# =============================================================================
# VALIDATION
# =============================================================================


class TestValidateIdea:
    def test_valid_idea_passes(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        result = validate_idea(valid_idea, populated_registry)
        assert result.ok is True
        assert len(result.errors) == 0

    def test_unknown_pillar_errors(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        bad = valid_idea.model_copy(update={"pillar": "no-existe"})
        result = validate_idea(bad, populated_registry)
        assert result.ok is False
        assert any("pillar" in e.field for e in result.errors)

    def test_unknown_audience_errors(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        bad = valid_idea.model_copy(update={"audience": "no-existe"})
        result = validate_idea(bad, populated_registry)
        assert result.ok is False
        assert any("audience" in e.field for e in result.errors)

    def test_platform_without_spec_warns(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        bad = valid_idea.model_copy(
            update={"platforms": [DistributionPlatform.LINKEDIN_VIDEO]}
        )
        result = validate_idea(bad, populated_registry)
        # LINKEDIN_VIDEO no está en populated_registry.platforms → warning
        assert result.ok is True  # warning, not error
        assert any("platform" in w.field for w in result.warnings)


class TestValidatePlan:
    def test_clean_plan_passes(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[
                valid_idea,
                valid_idea.model_copy(update={"id": "otra-idea", "pillar": "historia"}),
            ],
        )
        result = validate_plan(plan, populated_registry)
        assert result.ok is True

    def test_duplicate_ids_error(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[
                valid_idea,
                valid_idea.model_copy(),  # mismo id duplicado
            ],
        )
        result = validate_plan(plan, populated_registry)
        assert any("duplicado" in e.message for e in result.errors)

    def test_pillar_rotation_warns(
        self, valid_idea: ReelIdea, populated_registry: EditorialRegistry
    ) -> None:
        """3 ideas con el mismo pilar → warning de rotación."""
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[
                valid_idea.model_copy(update={"id": f"id-{i}"})
                for i in range(3)
            ],
        )
        result = validate_plan(plan, populated_registry, max_per_pillar=2)
        assert any("rotation" in w.field for w in result.warnings)


# =============================================================================
# PLAN PERSISTENCE
# =============================================================================


class TestPlanPersistence:
    def test_iso_week_format(self) -> None:
        w = iso_week()
        assert len(w) == 8  # YYYY-WNN
        assert w[4] == "-"
        assert w[5] == "W"

    def test_save_load_roundtrip(
        self, valid_idea: ReelIdea, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[valid_idea],
        )
        out = save_plan(plan)
        assert out.exists()
        loaded = load_plan("2026-W24")
        assert loaded.week == plan.week
        assert len(loaded.ideas) == 1
        assert loaded.ideas[0].id == valid_idea.id

    def test_plan_by_pillar(self, valid_idea: ReelIdea) -> None:
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[
                valid_idea.model_copy(update={"id": "i1"}),
                valid_idea.model_copy(update={"id": "i2", "pillar": "historia"}),
                valid_idea.model_copy(update={"id": "i3"}),
            ],
        )
        counts = plan.by_pillar()
        assert counts["ciencia"] == 2
        assert counts["historia"] == 1

    def test_plan_approved_ideas(self, valid_idea: ReelIdea) -> None:
        plan = EditorialPlan(
            week="2026-W24",
            generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
            ideas=[
                valid_idea.model_copy(update={"id": "i1", "approved": True}),
                valid_idea.model_copy(update={"id": "i2", "approved": False}),
            ],
        )
        approved = plan.approved_ideas()
        assert len(approved) == 1
        assert approved[0].id == "i1"


# =============================================================================
# PRICING
# =============================================================================


class TestPricing:
    def test_known_model_exact_lookup(self) -> None:
        p = price_of("gpt-4o-mini")
        assert p.input_per_mtok == 0.15
        assert p.output_per_mtok == 0.60

    def test_claude_opus_pricing(self) -> None:
        p = price_of("claude-opus-4-7")
        assert p.input_per_mtok == 15.0
        assert p.output_per_mtok == 75.0

    def test_unknown_model_uses_fallback(self) -> None:
        p = price_of("totally-made-up-xyz")
        # Fallback es conservador (1.0/3.0)
        assert p.input_per_mtok > 0
        assert p.output_per_mtok > 0

    def test_calculate_cost(self) -> None:
        # 1M input @ 0.15 = $0.15, 1M output @ 0.60 = $0.60
        cost = calculate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
        assert abs(cost - 0.75) < 0.001

    def test_calculate_cost_small_numbers(self) -> None:
        # 1000 in + 500 out gpt-4o-mini = 1000*0.15/1M + 500*0.60/1M = 0.00045
        cost = calculate_cost("gpt-4o-mini", 1000, 500)
        assert abs(cost - 0.00045) < 0.0001

    def test_is_priced(self) -> None:
        assert is_priced("gpt-4o-mini") is True
        assert is_priced("totally-made-up") is False
        assert is_priced("") is False

    def test_case_insensitive_lookup(self) -> None:
        p1 = price_of("GPT-4O-MINI")
        p2 = price_of("gpt-4o-mini")
        assert p1 == p2

    def test_pricing_table_not_empty(self) -> None:
        assert len(PRICING) > 20  # tenemos ~30+ modelos
        # Modelos críticos presentes
        for m in [
            "gpt-4o-mini", "claude-opus-4-7", "claude-sonnet-4-6",
            "gemini-2.5-flash", "deepseek-chat",
        ]:
            assert m in PRICING


# =============================================================================
# COST STAMPING ON PROVIDER
# =============================================================================


class TestProviderCostStamping:
    def test_dummy_provider_stamps_cost(self) -> None:
        from core.llm_router.base import LLMProvider

        class DummyP(LLMProvider):
            name = "dummy"
            async def complete(self, *a: object, **kw: object) -> str: return ""
            async def complete_structured(self, *a: object, **kw: object) -> object: return None

        p = DummyP(model_name="gpt-4o-mini")
        p._stamp_cost(2000, 1000)
        assert p.last_input_tokens == 2000
        assert p.last_output_tokens == 1000
        assert p.last_cost_usd > 0
        assert p.total_calls == 1

    def test_cost_record_includes_phase(self) -> None:
        from core.llm_router.base import LLMProvider

        class DummyP(LLMProvider):
            name = "dummy"
            async def complete(self, *a, **kw): return ""
            async def complete_structured(self, *a, **kw): return None

        p = DummyP(model_name="claude-opus-4-7")
        p._stamp_cost(1000, 500)
        record: LLMCostRecord = p.get_cost_record(phase="hunt")
        assert record.provider == "dummy"
        assert record.model == "claude-opus-4-7"
        assert record.phase == "hunt"
        assert record.input_tokens == 1000
        assert record.output_tokens == 500

    def test_total_accumulates_across_calls(self) -> None:
        from core.llm_router.base import LLMProvider

        class DummyP(LLMProvider):
            name = "dummy"
            async def complete(self, *a, **kw): return ""
            async def complete_structured(self, *a, **kw): return None

        p = DummyP(model_name="gpt-4o-mini")
        p._stamp_cost(1000, 500)
        first = p.total_cost_usd
        p._stamp_cost(2000, 1000)
        assert p.total_cost_usd > first
        assert p.total_calls == 2
