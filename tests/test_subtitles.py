"""Tests para core/subtitles — word_burst, srt, whisper, accents."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.subtitles import (
    build_reel_ass,
    build_reel_ass_with_accents,
    canonical_position,
    is_valid_for_role,
    normalize_overlay,
    render_text,
    subtitles_from_word_timings,
    whisper_available,
    write_reel_ass,
)
from shared.schemas import (
    AccentOverlay,
    AccentPattern,
    AccentPosition,
    Beat,
    BeatRole,
    Card,
    WordTiming,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _make_cards_simple() -> list[Card]:
    """Una sola card con 3 palabras."""
    words = [
        WordTiming(word="Quantum", start_s=0.0, end_s=0.5),
        WordTiming(word="is", start_s=0.5, end_s=0.7),
        WordTiming(word="weird.", start_s=0.7, end_s=1.3),
    ]
    return [
        Card(
            text="Quantum is weird.",
            words=words,
            start_s=0.0,
            end_s=1.3,
            line_count=1,
        )
    ]


def _make_beats_and_accents() -> tuple[list[Beat], list[AccentOverlay | None]]:
    """3 beats: hook (con title card upper), mechanism (con número lower),
    payoff (sin accent)."""
    beats = [
        Beat(
            idx=0,
            role=BeatRole.HOOK,
            text="hook line",
            target_duration_s=2.0,
            veo_duration=6,
        ),
        Beat(
            idx=1,
            role=BeatRole.MECHANISM,
            text="mech line with 47 percent",
            target_duration_s=4.0,
            veo_duration=6,
        ),
        Beat(
            idx=2,
            role=BeatRole.PAYOFF,
            text="payoff line",
            target_duration_s=2.0,
            veo_duration=4,
        ),
    ]
    accents: list[AccentOverlay | None] = [
        AccentOverlay(
            text="Quantum Is Real",
            pattern=AccentPattern.HOOK_TITLE_CARD,
            position=AccentPosition.UPPER_THIRD,
        ),
        AccentOverlay(
            text="47 percent",
            pattern=AccentPattern.NUMBER,
            position=AccentPosition.LOWER_THIRD,
        ),
        None,
    ]
    return beats, accents


# ─────────────────────────────────────────────────────────────────────────────
# 1. word_burst — ASS válido y bien formado
# ─────────────────────────────────────────────────────────────────────────────


def test_word_burst_ass_valid(tmp_path: Path) -> None:
    """build_reel_ass produce un SSAFile con styles + N events por palabra."""
    cards = _make_cards_simple()
    ssa = build_reel_ass(cards, font_name="Montserrat-Bold")

    # Style 'Sub' presente y configurado para bottom-center
    assert "Sub" in ssa.styles
    sub_style = ssa.styles["Sub"]
    assert sub_style.bold is True
    # Alignment 2 == BOTTOM_CENTER en libass / pysubs2
    assert int(sub_style.alignment) == 2

    # Un event por word (3) y todos con layer 0, style "Sub"
    assert len(ssa.events) == 3
    for ev in ssa.events:
        assert ev.layer == 0
        assert ev.style == "Sub"
        # Sin punctuation terminal en el texto burst (se strip-ea)
        assert ev.text and ev.text[-1] not in ".!?"

    # Round-trip write/read: el archivo debe ser parseable por pysubs2
    out = tmp_path / "burst.ass"
    write_reel_ass(cards, out, font_name="Montserrat-Bold")
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content


# ─────────────────────────────────────────────────────────────────────────────
# 2. SRT — formato standard parseable
# ─────────────────────────────────────────────────────────────────────────────


def test_srt_standard_format(tmp_path: Path) -> None:
    """subtitles_from_word_timings produce un SRT con bloques HH:MM:SS,mmm."""
    word_timings = [
        WordTiming(word="Hello", start_s=0.0, end_s=0.5),
        WordTiming(word="world.", start_s=0.5, end_s=1.2),
        WordTiming(word="Second", start_s=1.5, end_s=2.0),
        WordTiming(word="sentence.", start_s=2.0, end_s=2.8),
    ]
    out = tmp_path / "test.srt"
    subtitles_from_word_timings(word_timings, out)
    content = out.read_text(encoding="utf-8")

    # Bloque 1
    assert "1\n" in content
    assert "00:00:00,000 --> 00:00:01,200" in content
    # Bloque 2 (segunda oración, separada por puntuación terminal "world.")
    assert "2\n" in content
    assert "00:00:01,500 --> 00:00:02,800" in content
    # Texto presente
    assert "Hello world." in content
    assert "Second sentence." in content
    # Re-parsing con file_to_subtitles
    from core.subtitles import file_to_subtitles

    parsed = file_to_subtitles(out)
    assert len(parsed) == 2
    assert parsed[0][2] == "Hello world."
    assert parsed[1][2] == "Second sentence."


# ─────────────────────────────────────────────────────────────────────────────
# 3. Accent placement: upper_third (hook) vs lower_third (number)
# ─────────────────────────────────────────────────────────────────────────────


def test_accent_placement_upper_vs_lower(tmp_path: Path) -> None:
    """build_reel_ass_with_accents respeta posición canónica por patrón."""
    cards = _make_cards_simple()
    beats, accents = _make_beats_and_accents()
    ssa = build_reel_ass_with_accents(cards, beats, accents)

    # Ambos styles registrados
    assert "AccentUpper" in ssa.styles
    assert "AccentLower" in ssa.styles
    # Upper anclado a TOP_CENTER (alignment 8); lower a BOTTOM_CENTER (2)
    assert int(ssa.styles["AccentUpper"].alignment) == 8
    assert int(ssa.styles["AccentLower"].alignment) == 2

    # Eventos de accent: deben tener layer 2 y texto UPPERCASE
    accent_events = [ev for ev in ssa.events if ev.layer == 2]
    # 2 accents emitidos (el 3º es None)
    assert len(accent_events) == 2
    # El primero es hook_title_card (upper); el segundo number (lower)
    by_style = {ev.style: ev for ev in accent_events}
    assert "AccentUpper" in by_style
    assert "AccentLower" in by_style
    assert by_style["AccentUpper"].text == "QUANTUM IS REAL"
    assert by_style["AccentLower"].text == "47 PERCENT"

    # Timing: el hook empieza en 0, el number en 2000 ms (cumsum = 2.0s)
    assert by_style["AccentUpper"].start == 0
    assert by_style["AccentUpper"].end == 2000
    assert by_style["AccentLower"].start == 2000
    assert by_style["AccentLower"].end == 6000  # 2.0 + 4.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. CJK detection — usa cjk_font del config cuando hay caracteres CJK
# ─────────────────────────────────────────────────────────────────────────────


def test_cjk_font_fallback() -> None:
    """build_reel_ass detecta CJK y cambia la familia del style 'Sub'."""
    cjk_words = [
        WordTiming(word="量子", start_s=0.0, end_s=0.5),
        WordTiming(word="は", start_s=0.5, end_s=0.7),
        WordTiming(word="奇妙", start_s=0.7, end_s=1.3),
    ]
    cards = [
        Card(
            text="量子は奇妙",
            words=cjk_words,
            start_s=0.0,
            end_s=1.3,
            line_count=1,
        )
    ]
    ssa = build_reel_ass(cards, font_name="Montserrat-Bold")
    sub_font = ssa.styles["Sub"].fontname
    # No es Montserrat — debe haber caído al cjk_font del config (default
    # STHeitiMedium.ttc → "STHeitiMedium" post strip_ext)
    assert "Montserrat" not in sub_font
    # Ascii cards usan la familia pedida
    ascii_cards = _make_cards_simple()
    ssa_ascii = build_reel_ass(ascii_cards, font_name="Montserrat-Bold")
    ascii_font = ssa_ascii.styles["Sub"].fontname
    # Aceptamos Montserrat O el fallback DejaVu Sans (según si Montserrat
    # está instalado en el sistema de tests)
    assert ascii_font in {"Montserrat", "DejaVu Sans", "Montserrat-Bold"}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Whisper lazy import — importar core.subtitles no debe explotar si
#    faster_whisper no está instalado
# ─────────────────────────────────────────────────────────────────────────────


def test_whisper_lazy_no_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aunque faster_whisper no exista, `import core.subtitles` y los helpers
    NO relacionados a transcribe() deben seguir funcionando."""
    # Simulamos que faster_whisper no está disponible. El import del módulo
    # `core.subtitles.whisper` no debe hacer `from faster_whisper import …`
    # al top-level.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    # Reimport directo del módulo: no debe lanzar
    import importlib

    import core.subtitles.whisper as wmod

    importlib.reload(wmod)
    # `is_available` debe devolver False bajo este monkeypatch
    assert wmod.is_available() is False

    # `transcribe()` con fallback graceful: devuelve [] en vez de raise
    fake_audio = Path("/tmp/does_not_exist.mp3")
    result = wmod.transcribe(fake_audio)
    assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. WordTiming → SRT — agrupamiento correcto (max_words_per_line)
# ─────────────────────────────────────────────────────────────────────────────


def test_word_timings_to_srt_grouping(tmp_path: Path) -> None:
    """16 palabras sin puntuación se parten en 2 líneas (default 14/línea)."""
    word_timings = [
        WordTiming(word=f"w{i}", start_s=float(i), end_s=float(i + 1))
        for i in range(16)
    ]
    out = tmp_path / "g.srt"
    subtitles_from_word_timings(word_timings, out)
    parsed_text = out.read_text(encoding="utf-8")
    # Dos bloques numerados (1 y 2)
    assert "\n1\n" in "\n" + parsed_text
    assert "\n2\n" in "\n" + parsed_text
    # No debe haber bloque 3
    assert "\n3\n" not in "\n" + parsed_text
    # Timestamps coherentes: el bloque 1 termina en max-th word (14)
    assert "00:00:00,000 --> 00:00:14,000" in parsed_text


# ─────────────────────────────────────────────────────────────────────────────
# 7. Accent helpers — patrones canónicos y posición
# ─────────────────────────────────────────────────────────────────────────────


def test_accent_canonical_position_and_role_validity() -> None:
    """canonical_position y is_valid_for_role implementan las 6 reglas."""
    # hook_title_card → upper
    assert canonical_position(AccentPattern.HOOK_TITLE_CARD) == AccentPosition.UPPER_THIRD
    # Los otros 5 → lower
    for p in (
        AccentPattern.NUMBER,
        AccentPattern.NAMED_ENTITY,
        AccentPattern.JARGON_TRANSLATION,
        AccentPattern.REACTION,
        AccentPattern.LIST_MARKER,
    ):
        assert canonical_position(p) == AccentPosition.LOWER_THIRD

    # hook_title_card SOLO válido en role=hook
    assert is_valid_for_role(AccentPattern.HOOK_TITLE_CARD, BeatRole.HOOK) is True
    assert is_valid_for_role(AccentPattern.HOOK_TITLE_CARD, BeatRole.MECHANISM) is False
    # Los demás son válidos en cualquier role
    assert is_valid_for_role(AccentPattern.NUMBER, BeatRole.PAYOFF) is True

    # normalize_overlay corrige posición y descarta el inválido
    bad_position = AccentOverlay(
        text="quantum is real",
        pattern=AccentPattern.HOOK_TITLE_CARD,
        position=AccentPosition.LOWER_THIRD,  # ← incorrecto
    )
    fixed = normalize_overlay(bad_position, BeatRole.HOOK)
    assert fixed is not None
    assert fixed.position == AccentPosition.UPPER_THIRD
    # hook_title_card en role no-hook → None
    assert normalize_overlay(bad_position, BeatRole.MECHANISM) is None

    # render_text uppercase
    overlay = AccentOverlay(
        text="forty seven percent",
        pattern=AccentPattern.NUMBER,
        position=AccentPosition.LOWER_THIRD,
    )
    assert render_text(overlay) == "FORTY SEVEN PERCENT"


# ─────────────────────────────────────────────────────────────────────────────
# Sanity: `whisper_available` reporta state sin importar
# ─────────────────────────────────────────────────────────────────────────────


def test_whisper_available_is_bool() -> None:
    assert isinstance(whisper_available(), bool)
