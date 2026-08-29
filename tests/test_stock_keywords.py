"""Tests de `core/visual/stock/keywords.py` (ADR-034).

El selector mandaba `BeatVisual.visual_anchor` —una cita académica verbatim— a
los buscadores de stock, que devolvían cero siempre. De ahí el
`source_diversity: 1` del guard anti-slideshow: no es que el stock fuera malo,
es que nunca llegó a buscarse nada buscable.
"""
from __future__ import annotations

import pytest

from core.visual.stock.keywords import (
    MAX_TERMS,
    build_stock_query,
    extract_terms,
)

# Anchors reales del job `3a3b12bd` — los que devolvieron 0 resultados en los
# seis proveedores de stock.
ANCHOR_DREAMS = (
    "Dement and Kleitman, 1957: systematic awakenings established that dream "
    "reports could be obtained from both REM and non-REM sleep."
)
ANCHOR_PLACEBO = (
    'Leonard Cobb and colleagues published "An Evaluation of '
    'Internal-Mammary-Artery Ligation by a Double-Blind Technic" in the New '
    "England Journal of Medicine in 1959."
)
PROMPT_DREAMS = (
    "A sleep laboratory at night, EEG machine with a red indicator light, a "
    "man sleeping with electrodes on his head. Cinematic, dim lighting, "
    "vertical 9:16 frame, negative space upper-center."
)


class TestExtraction:
    def test_academic_citation_becomes_searchable(self) -> None:
        """El caso exacto que rompía: cita entera → términos de escena."""
        assert build_stock_query(PROMPT_DREAMS, ANCHOR_DREAMS) == "sleep laboratory night"

    def test_caps_at_max_terms(self) -> None:
        q = build_stock_query(PROMPT_DREAMS, ANCHOR_DREAMS)
        assert len(q.split()) <= MAX_TERMS

    def test_prompt_wins_over_anchor(self) -> None:
        """El image_prompt describe una escena; el anchor cita una fuente."""
        q = build_stock_query("ocean waves crashing on rocks", ANCHOR_PLACEBO)
        assert "ocean" in q
        assert "ligation" not in q

    def test_anchor_used_when_no_prompt(self) -> None:
        q = build_stock_query("", ANCHOR_PLACEBO)
        assert q  # algo saca; el anchor es el último recurso útil
        assert "1959" not in q  # …pero sin el año de la cita

    def test_fallback_when_everything_else_is_style(self) -> None:
        q = build_stock_query("Cinematic wide shot, dramatic lighting, 4k", "", "coffee shop")
        assert q == "coffee shop"

    def test_empty_input_returns_empty(self) -> None:
        """Vacío es un resultado válido: significa 'no hay nada que buscar'."""
        assert build_stock_query("", "", "") == ""
        assert build_stock_query("the and of", "", "") == ""


class TestNoise:
    @pytest.mark.parametrize(
        "noisy",
        ["Smith et al. 2019 reported", "vol. 12 pp. 3", "1957 study of sleep"],
    )
    def test_citation_noise_stripped(self, noisy: str) -> None:
        terms = extract_terms(noisy)
        assert not any(t.isdigit() for t in terms)
        assert "et" not in terms
        assert "study" not in terms

    def test_style_vocabulary_stripped(self) -> None:
        """'cinematic' describe CÓMO se filma; trae de todo en una búsqueda."""
        terms = extract_terms("cinematic dramatic lighting wide angle bokeh")
        assert terms == []

    def test_abstract_words_stripped(self) -> None:
        # Un buscador de stock indexa cosas que se ven, no ideas.
        assert extract_terms("the concept of evidence and results") == []

    def test_accents_normalized(self) -> None:
        # 'investigación' es ruido de cita igual que 'research'.
        assert "investigacion" not in extract_terms("una investigación sobre océanos")

    def test_spanish_stopwords(self) -> None:
        assert extract_terms("el laboratorio de los sueños") == ["laboratorio", "suenos"]

    def test_short_tokens_dropped(self) -> None:
        assert extract_terms("a b cd ocean") == ["ocean"]

    def test_period_survives_as_useful_context(self) -> None:
        """'1950s' sí acota una búsqueda de stock; '1957' suelto es una cita."""
        assert "1950s" in extract_terms("A 1950s operating theatre")


class TestOrderAndDedup:
    def test_preserves_order_of_appearance(self) -> None:
        """Los prompts nombran el sujeto primero y el ambiente después."""
        assert extract_terms("surgeon operating theatre lamp") == [
            "surgeon", "operating", "theatre",
        ]

    def test_dedupes_across_sources(self) -> None:
        terms = extract_terms("ocean waves", "ocean storm", max_terms=5)
        assert terms.count("ocean") == 1
        assert terms == ["ocean", "waves", "storm"]

    def test_handles_none_sources(self) -> None:
        assert extract_terms(None, "ocean", None) == ["ocean"]


class TestSelectorIntegration:
    @pytest.mark.asyncio
    async def test_unsearchable_beat_skips_providers_entirely(self, tmp_path) -> None:
        """Sin términos, no se gastan 6 peticiones para recibir 6 ceros."""
        from unittest.mock import MagicMock, patch

        from core.visual.selector import _try_stock
        from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint

        beat = Beat(idx=0, role=BeatRole.HOOK, text="the and of", target_duration_s=4.0, veo_duration=4)
        visual = BeatVisual(image_prompt="cinematic dramatic", motion_hint=MotionHint.STATIC, visual_anchor="")

        provider = MagicMock()
        with patch("core.visual.selector.get_all_providers", return_value=[provider]):
            result = await _try_stock(beat, visual, "9:16", tmp_path)

        assert result is None
        provider.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_searchable_beat_queries_with_short_terms(self, tmp_path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        from core.visual.selector import _try_stock
        from shared.schemas import Beat, BeatRole, BeatVisual, MotionHint

        beat = Beat(idx=1, role=BeatRole.MECHANISM, text="body", target_duration_s=4.0, veo_duration=4)
        visual = BeatVisual(
            image_prompt=PROMPT_DREAMS, motion_hint=MotionHint.STATIC, visual_anchor=ANCHOR_DREAMS
        )

        provider = MagicMock()
        provider.name = "fake"
        provider.search = AsyncMock(return_value=[])
        with patch("core.visual.selector.get_all_providers", return_value=[provider]):
            await _try_stock(beat, visual, "9:16", tmp_path)

        sent = provider.search.await_args.kwargs["query"]
        assert sent == "sleep laboratory night"
        assert len(sent) < 40  # antes viajaban ~140 chars de cita
