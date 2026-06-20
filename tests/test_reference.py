"""Tests para core/reference: analizador de video de referencia (input-by-reference).

Idea reimplementada (NO copiada) de OpenMontage (video_analyzer + scene_detect +
transcript_fetcher): dado un video de referencia (TikTok/Reel/YouTube), extraer su
pacing, estructura, hook y transcript para informar la composición del guion
("haz un reel con el ritmo de este video"). Ver ADR-017.

La descarga/transcripción/scene-detect reales viven detrás de un `ReferenceFetcher`
inyectable; la LÓGICA de análisis (`build_brief`) es pura y determinista → se testea
offline con un fetcher fake. Código propio bajo Apache-2.0.

Cobertura:
1. build_brief: transcript, wpm, segment_count.
2. build_brief: shot_count = cuts+1, avg_shot_s.
3. hook_style: pregunta / shock_stat / contrarian / listicle / statement.
4. suggested_beats refleja pacing y se acota a [3,8].
5. duration 0 → no crashea (wpm 0, avg_shot_s 0).
6. target_wpm acotado a [120,200].
7. analyze_reference con fetcher fake → brief con url.
8. analyze_reference con fetcher que falla → ReferenceAnalysisError.
"""
from __future__ import annotations

import pytest

from core.reference import (
    RawReference,
    ReferenceAnalysisError,
    analyze_reference,
    build_brief,
    mechanism_target,
    reference_style_hint,
)
from shared.schemas import EssenceCandidate, ReferenceBrief


def _raw(
    *,
    title: str = "Demo",
    duration_s: float = 30.0,
    segments: list[tuple[float, float, str]] | None = None,
    shot_cuts: list[float] | None = None,
) -> RawReference:
    return RawReference(
        title=title,
        duration_s=duration_s,
        segments=segments if segments is not None else [(0.0, 30.0, "hello world " * 30)],
        shot_cuts=shot_cuts if shot_cuts is not None else [],
    )


# =============================================================================
# build_brief
# =============================================================================


def test_build_brief_transcript_and_wpm():
    raw = _raw(
        duration_s=60.0,
        segments=[(0.0, 30.0, "one two three four five"), (30.0, 60.0, "six seven eight nine ten")],
    )
    brief = build_brief("https://x/v", raw)
    assert isinstance(brief, ReferenceBrief)
    assert "one two three" in brief.transcript
    assert brief.segment_count == 2
    assert brief.wpm == 10  # 10 palabras / 1 min


def test_build_brief_shot_count_and_avg():
    raw = _raw(duration_s=30.0, shot_cuts=[10.0, 20.0])  # 2 cortes → 3 shots
    brief = build_brief("https://x/v", raw)
    assert brief.shot_count == 3
    assert abs(brief.avg_shot_s - 10.0) < 1e-9


def test_hook_style_question():
    raw = _raw(segments=[(0.0, 5.0, "Did you know this trick changes everything?")])
    assert build_brief("u", raw).hook_style == "question"


def test_hook_style_shock_stat():
    raw = _raw(segments=[(0.0, 5.0, "97 percent of people get this completely wrong")])
    # Contiene dígito → shock_stat tiene prioridad sobre contrarian.
    assert build_brief("u", raw).hook_style == "shock_stat"


def test_hook_style_listicle():
    raw = _raw(segments=[(0.0, 5.0, "Five ways to improve your focus today")])
    assert build_brief("u", raw).hook_style == "listicle"


def test_hook_style_statement_default():
    raw = _raw(segments=[(0.0, 5.0, "The ocean is very deep and blue")])
    assert build_brief("u", raw).hook_style == "statement"


def test_suggested_beats_fast_cuts_clamped_high():
    # Cortes cada ~1.5s → muchos shots → suggested_beats topado en 8.
    cuts = [1.5 * i for i in range(1, 20)]
    raw = _raw(duration_s=30.0, shot_cuts=cuts)
    brief = build_brief("u", raw)
    assert brief.suggested_beats == 8


def test_suggested_beats_slow_cuts_low():
    # Un solo shot de 30s → pacing lento → suggested_beats al piso (3).
    raw = _raw(duration_s=30.0, shot_cuts=[])
    brief = build_brief("u", raw)
    assert brief.suggested_beats == 3


def test_zero_duration_no_crash():
    raw = _raw(duration_s=0.0, segments=[(0.0, 0.0, "x")], shot_cuts=[])
    brief = build_brief("u", raw)
    assert brief.wpm == 0
    assert brief.avg_shot_s == 0.0


def test_target_wpm_clamped():
    # 600 palabras en 1 min = 600 wpm → target_wpm acotado a 200.
    raw = _raw(duration_s=60.0, segments=[(0.0, 60.0, " ".join(["w"] * 600))])
    brief = build_brief("u", raw)
    assert brief.wpm == 600
    assert brief.target_wpm == 200


# =============================================================================
# analyze_reference (orquestación con fetcher inyectable)
# =============================================================================


@pytest.mark.asyncio
async def test_analyze_reference_with_fake_fetcher():
    class FakeFetcher:
        async def fetch(self, url: str) -> RawReference:
            return _raw(title="Fake", duration_s=20.0)

    brief = await analyze_reference("https://tiktok.com/x", fetcher=FakeFetcher())
    assert brief.url == "https://tiktok.com/x"
    assert brief.title == "Fake"
    assert brief.duration_s == 20.0


@pytest.mark.asyncio
async def test_analyze_reference_fetcher_failure_raises():
    class BrokenFetcher:
        async def fetch(self, url: str) -> RawReference:
            raise RuntimeError("network down")

    with pytest.raises(ReferenceAnalysisError):
        await analyze_reference("https://x/v", fetcher=BrokenFetcher())


# =============================================================================
# reference_style_hint (guía de estilo para el prompt de composición, ADR-017)
# =============================================================================


def test_style_hint_includes_pacing_and_hook():
    brief = build_brief(
        "u",
        _raw(duration_s=30.0, shot_cuts=[10.0, 20.0], segments=[(0.0, 30.0, "97 facts here")]),
    )
    hint = reference_style_hint(brief)
    assert brief.hook_style in hint
    assert str(brief.target_wpm) in hint
    assert str(brief.suggested_beats) in hint


def test_style_hint_never_empty():
    brief = build_brief("u", _raw(duration_s=0.0, segments=[(0.0, 0.0, "x")], shot_cuts=[]))
    assert reference_style_hint(brief).strip() != ""


# =============================================================================
# Inyección opcional en los prompts de composición (NO afecta cuando es None)
# =============================================================================


def _essence_candidate() -> EssenceCandidate:
    return EssenceCandidate(
        core_claim="X causes Y",
        mechanism="because Z",
        evidence=["fact one"],
        domain="science",
        angle="reversal",
        novelty_pitch="counterintuitive",
    )


def test_compose_user_prompt_unchanged_without_brief():
    from core.narrative.compose import _user_prompt

    essence = _essence_candidate()
    assert _user_prompt(essence) == _user_prompt(essence, None)


def test_compose_user_prompt_injects_brief():
    from core.narrative.compose import _user_prompt

    brief = build_brief("u", _raw(segments=[(0.0, 5.0, "Five ways to win today")]))
    out = _user_prompt(_essence_candidate(), brief)
    assert brief.hook_style in out
    assert "STYLE REFERENCE" in out


def test_narrator_user_prompt_unchanged_without_brief():
    from core.narrative.narrator import _user_prompt

    essence = _essence_candidate()
    assert _user_prompt(essence) == _user_prompt(essence, None)


def test_narrator_user_prompt_injects_brief():
    from core.narrative.narrator import _user_prompt

    brief = build_brief("u", _raw(segments=[(0.0, 5.0, "Did you know this?")]))
    out = _user_prompt(_essence_candidate(), brief)
    assert "STYLE REFERENCE" in out


# =============================================================================
# mechanism_target: modula el nº de mechanism_lines según pacing (ADR-017)
# =============================================================================


def test_mechanism_target_subtracts_hook_and_payoff():
    # suggested_beats cuenta hook + mechanism + payoff → mechanism = beats - 2.
    assert mechanism_target(ReferenceBrief(url="u", suggested_beats=5)) == 3


def test_mechanism_target_clamps_to_schema_max():
    # ScriptDraft.mechanism_lines tope = 4.
    assert mechanism_target(ReferenceBrief(url="u", suggested_beats=8)) == 4


def test_mechanism_target_clamps_to_schema_min():
    # ScriptDraft.mechanism_lines piso = 2.
    assert mechanism_target(ReferenceBrief(url="u", suggested_beats=3)) == 2


def test_mechanism_target_degenerate_zero_beats():
    assert mechanism_target(ReferenceBrief(url="u", suggested_beats=0)) == 2


# =============================================================================
# _system_prompt: pide nº exacto sólo con target (sin target = comportamiento previo)
# =============================================================================


def test_compose_system_prompt_default_range_without_target():
    from core.narrative.compose import _system_prompt

    out = _system_prompt("general")
    assert "2-4 sentences" in out
    assert _system_prompt("general") == _system_prompt("general", None)


def test_compose_system_prompt_exact_count_with_target():
    from core.narrative.compose import _system_prompt

    out = _system_prompt("general", 3)
    assert "exactly 3 sentences" in out
    assert "2-4 sentences" not in out
