"""Word-burst ASS builder — port de reels-af/render/subtitles.py.

Construye un archivo `.ass` (libass) con DOS layers:

    Layer 0 (Sub):           UNA palabra a la vez, ~170 px, bottom-center.
                             Cada palabra ocupa exactamente su [start_s, end_s]
                             del audio. Feels like a fast teleprompter pulse.
    Layer 2 (AccentUpper/AccentLower):
                             Overlay editorial UPPERCASE en el third opuesto
                             del frame, timed al beat.

Por qué ASS + libass (en vez de N ffmpeg drawtext):
  • libass usa métricas reales de la fuente → sin alignment bugs.
  • Renderiza idéntico en VLC / mpv / players de fansub.
  • Un solo archivo `.ass` reemplaza cadenas drawtext gigantes; fácil de
    extender (fades, glow, karaoke) y de auditar.

El archivo se quema más tarde con el filtro `ass=` de ffmpeg desde
`core/editor`. Aquí NO se hace burn; solo se emite el `.ass`.
"""

from __future__ import annotations

from pathlib import Path

import pysubs2

from shared.config import SubtitleConfig, load_config
from shared.schemas import AccentOverlay, AccentPosition, Beat, Card

# ─────────────────────────────────────────────────────────────────────────────
# Constantes de canvas/posición — tomadas de reels-af/planning/safe_zone.py.
# 9:16 reel estándar; la mitad inferior de la pantalla está obstruida por
# UI nativa (caption / handle / CTA / action bar) → los textos se anclan a
# zonas seguras.
# ─────────────────────────────────────────────────────────────────────────────

CANVAS_W = 1080
CANVAS_H = 1920

_BURST_Y_PCT = 0.74         # well into bottom-third, separado del accent (0.62)
_BURST_STROKE_PX = 10       # stroke grueso para sobrevivir cualquier background

_ACCENT_LOWER_Y_PCT = 0.62  # ~1190 px desde top — seguro encima de UI
_ACCENT_UPPER_Y_PCT = 0.22  # ~423 px — para hook title cards
_ACCENT_FONT_PX = 110       # 1.4× burst → accent debe ser más loud
_ACCENT_STROKE_PX = 6

# Map de nombres simbólicos → BGR hex (ASS usa BGR, no RGB).
_NAMED_BGR: dict[str, str] = {
    "white":  "FFFFFF",
    "black":  "000000",
    "yellow": "00FFFF",
    "green":  "00FF00",
    "red":    "0000FF",
    "blue":   "FF0000",
}


def _hex_to_pysubs2_color(value: str) -> pysubs2.Color:
    """Acepta "#RRGGBB", "RRGGBB" o un nombre conocido y devuelve pysubs2.Color.

    pysubs2 internamente almacena RGB. La conversión a BGR para el formato
    ASS la hace pysubs2 al serializar.
    """
    if not value:
        return pysubs2.Color(255, 255, 255)
    raw = value.strip().lower()
    if raw in _NAMED_BGR:
        bgr = _NAMED_BGR[raw]
        b, g, r = int(bgr[0:2], 16), int(bgr[2:4], 16), int(bgr[4:6], 16)
        return pysubs2.Color(r, g, b)
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) != 6:
        return pysubs2.Color(255, 255, 255)
    try:
        r = int(raw[0:2], 16)
        g = int(raw[2:4], 16)
        b = int(raw[4:6], 16)
        return pysubs2.Color(r, g, b)
    except ValueError:
        return pysubs2.Color(255, 255, 255)


# ─────────────────────────────────────────────────────────────────────────────
# Font resolution — Montserrat es el default editorial; si no está disponible
# en el sistema (Linux CI, contenedor minimal) caemos a DejaVu Sans Bold, que
# es prácticamente universal.
# ─────────────────────────────────────────────────────────────────────────────

# Pistas de búsqueda. Mantener corto: libass es robusto resolviendo nombres
# vía fontconfig, así que el "fallback" es realmente para nuestro mensaje
# del log + para casos extremos donde el config trae un .ttf inválido.
_MONTSERRAT_HINTS = (
    "/Library/Fonts/Montserrat-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Montserrat-Bold.ttf",
    "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
    "/usr/local/share/fonts/Montserrat-Bold.ttf",
)
_DEJAVU_HINTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/DejaVuSans-Bold.ttf",
    "/usr/local/share/fonts/DejaVuSans-Bold.ttf",
)

# Rango CJK simplificado (Chino/Japonés/Coreano). Si el texto contiene
# algún codepoint en este rango usamos `cjk_font` del config en lugar del
# default — Montserrat no contiene glyphs CJK.
_CJK_RANGES = (
    (0x3040, 0x30FF),   # Hiragana + Katakana
    (0x3400, 0x4DBF),   # CJK Ext A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul Syllables
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),   # Half/Fullwidth
)


def _contains_cjk(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def _strip_ext(name: str) -> str:
    """Pysubs2 espera FAMILY name, no nombre de archivo."""
    for ext in (".ttf", ".otf", ".ttc", ".TTF", ".OTF", ".TTC"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _resolve_font_name(requested: str) -> str:
    """Resuelve el nombre lógico de fuente, con fallback Montserrat → DejaVu Sans.

    No es estrictamente necesario tocar el filesystem (libass usa fontconfig),
    pero comprobamos pistas para emitir un nombre con más probabilidad de
    resolver. Quitamos extensión .ttf si vino del config.
    """
    family = _strip_ext(requested or "Montserrat-Bold")
    # Si tenemos hint de Montserrat, prefiérelo
    for hint in _MONTSERRAT_HINTS:
        if Path(hint).exists() and family.lower().startswith("montserrat"):
            return "Montserrat"
    # Si el usuario pidió Montserrat pero no está, caer a DejaVu Sans
    if family.lower().startswith("montserrat"):
        for hint in _DEJAVU_HINTS:
            if Path(hint).exists():
                return "DejaVu Sans"
    return family


def _make_sub_style(
    font_name: str,
    cfg: SubtitleConfig,
) -> pysubs2.SSAStyle:
    """Single-word burst style: bold sans, big, bottom-center."""
    margin_bottom = int((1.0 - _BURST_Y_PCT) * CANVAS_H)
    return pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=cfg.font_size_burst,
        primarycolor=_hex_to_pysubs2_color(cfg.fore_color),
        outlinecolor=_hex_to_pysubs2_color(cfg.stroke_color),
        backcolor=_hex_to_pysubs2_color("black"),
        bold=True,
        outline=max(_BURST_STROKE_PX, int(cfg.stroke_width * 4)),
        shadow=0,
        alignment=pysubs2.Alignment.BOTTOM_CENTER,  # \an2
        marginl=40,
        marginr=40,
        marginv=margin_bottom,
    )


def _make_accent_style(
    font_name: str,
    position: AccentPosition | str,
    cfg: SubtitleConfig,
) -> pysubs2.SSAStyle:
    """Accent style: louder, opposite third of frame."""
    pos = position.value if isinstance(position, AccentPosition) else position
    if pos == AccentPosition.UPPER_THIRD.value:
        margin_v = int(_ACCENT_UPPER_Y_PCT * CANVAS_H)
        alignment = pysubs2.Alignment.TOP_CENTER
    else:
        margin_v = int((1.0 - _ACCENT_LOWER_Y_PCT) * CANVAS_H)
        alignment = pysubs2.Alignment.BOTTOM_CENTER
    return pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=_ACCENT_FONT_PX,
        primarycolor=_hex_to_pysubs2_color(cfg.fore_color),
        outlinecolor=_hex_to_pysubs2_color(cfg.stroke_color),
        backcolor=_hex_to_pysubs2_color("black"),
        bold=True,
        outline=_ACCENT_STROKE_PX,
        shadow=0,
        alignment=alignment,
        marginl=20,
        marginr=20,
        marginv=margin_v,
    )


def _emit_card_events(
    ssa: pysubs2.SSAFile,
    cards: list[Card],
) -> None:
    """UN event por WORD — word-burst.

    Cada palabra se muestra sola a bottom-center por exactamente su window
    [start_s, end_s]. Al avanzar el audio la siguiente reemplaza a la anterior.
    """
    for card in cards:
        for word in card.words:
            w_start_ms = int(max(0.0, word.start_s) * 1000)
            w_end_ms = int(max(word.start_s, word.end_s) * 1000)
            if w_end_ms <= w_start_ms:
                continue
            # Quitar puntuación terminal — en display de una sola palabra
            # se lee como noise.
            txt = word.word
            while txt and txt[-1] in ".!?":
                txt = txt[:-1]
            if not txt:
                continue
            ssa.events.append(
                pysubs2.SSAEvent(
                    start=w_start_ms,
                    end=w_end_ms,
                    style="Sub",
                    text=txt,
                    layer=0,
                )
            )


def build_reel_ass(
    cards: list[Card],
    font_name: str | None = None,
    cfg: SubtitleConfig | None = None,
) -> pysubs2.SSAFile:
    """Construye el SSA file in-memory desde cards (sin accents)."""
    cfg = cfg or load_config().subtitles
    requested = font_name or cfg.font_name
    # CJK detection: si cualquier card contiene CJK, cambiar a cjk_font.
    if any(_contains_cjk(c.text) for c in cards):
        requested = cfg.cjk_font
    family = _resolve_font_name(requested)

    ssa = pysubs2.SSAFile()
    ssa.info["PlayResX"] = str(CANVAS_W)
    ssa.info["PlayResY"] = str(CANVAS_H)
    # WrapStyle=0 → libass auto-wraps cuando una card excede el safe stage.
    ssa.info["WrapStyle"] = "0"
    ssa.styles["Sub"] = _make_sub_style(family, cfg)
    _emit_card_events(ssa, cards)
    return ssa


def build_reel_ass_with_accents(
    cards: list[Card],
    beats: list[Beat],
    accents: list[AccentOverlay | None],
    font_name: str | None = None,
    cfg: SubtitleConfig | None = None,
) -> pysubs2.SSAFile:
    """Como build_reel_ass pero embebe Layer 2 con accent events.

    Cada accent ocupa el window CUMULATIVO del beat (sum de target_duration_s).
    Estos timings son best-effort — ±200 ms es imperceptible para el viewer.
    """
    if len(beats) != len(accents):
        raise ValueError(
            f"build_reel_ass_with_accents: beats ({len(beats)}) y "
            f"accents ({len(accents)}) length mismatch"
        )
    cfg = cfg or load_config().subtitles
    ssa = build_reel_ass(cards, font_name=font_name, cfg=cfg)

    # Resolver familia de fuente igual que en build_reel_ass para los styles
    # de accent (que también pueden necesitar CJK).
    requested = font_name or cfg.font_name
    accent_text_join = " ".join(a.text for a in accents if a is not None)
    if _contains_cjk(accent_text_join):
        requested = cfg.cjk_font
    family = _resolve_font_name(requested)

    ssa.styles["AccentLower"] = _make_accent_style(family, AccentPosition.LOWER_THIRD, cfg)
    ssa.styles["AccentUpper"] = _make_accent_style(family, AccentPosition.UPPER_THIRD, cfg)

    cursor = 0.0
    beat_starts: list[float] = []
    for beat in beats:
        beat_starts.append(cursor)
        cursor += beat.target_duration_s

    for beat, accent, start_s in zip(beats, accents, beat_starts, strict=True):
        if accent is None:
            continue
        end_s = start_s + beat.target_duration_s
        start_ms = int(start_s * 1000)
        end_ms = int(end_s * 1000)
        if end_ms <= start_ms:
            continue
        pos = (
            accent.position.value
            if isinstance(accent.position, AccentPosition)
            else accent.position
        )
        style_name = (
            "AccentUpper" if pos == AccentPosition.UPPER_THIRD.value else "AccentLower"
        )
        ssa.events.append(
            pysubs2.SSAEvent(
                start=start_ms,
                end=end_ms,
                style=style_name,
                text=accent.text.upper(),
                layer=2,
            )
        )

    return ssa


def write_reel_ass(
    cards: list[Card],
    out_path: Path,
    font_name: str | None = None,
    cfg: SubtitleConfig | None = None,
) -> Path:
    """Escribe ASS a disco. Retorna su path."""
    ssa = build_reel_ass(cards, font_name=font_name, cfg=cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ssa.save(str(out_path), format_="ass")
    return out_path


def write_reel_ass_with_accents(
    cards: list[Card],
    accents: list[AccentOverlay | None],
    font_name: str,
    out_path: Path,
    beats: list[Beat] | None = None,
    cfg: SubtitleConfig | None = None,
) -> Path:
    """Escribe ASS con accents a disco. Retorna su path.

    Signature alineada con el spec del task: cards + accents + font_name + out.
    `beats` es requerido por la lógica de timing del overlay (cumulative
    beat windows); si no se pasa se infiere uno-a-uno con `cards` (cada card
    = un beat con duración igual a su span audio).
    """
    if beats is None:
        # Inferir beats desde cards (1:1) con duración = end_s - start_s.
        # No tenemos role real; usamos MECHANISM como neutro. Solo afecta
        # casos donde no se pasan beats; el render no usa role.
        from shared.schemas import BeatRole  # local to avoid cycle

        beats = [
            Beat(
                idx=i,
                role=BeatRole.MECHANISM,
                text=c.text,
                target_duration_s=max(0.1, c.end_s - c.start_s),
                veo_duration=6,
            )
            for i, c in enumerate(cards)
        ]
    ssa = build_reel_ass_with_accents(
        cards, beats, accents, font_name=font_name, cfg=cfg,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ssa.save(str(out_path), format_="ass")
    return out_path


__all__ = [
    "CANVAS_H",
    "CANVAS_W",
    "build_reel_ass",
    "build_reel_ass_with_accents",
    "write_reel_ass",
    "write_reel_ass_with_accents",
]
