"""Helpers de rendering para los 6 patrones de accent overlay.

Esto NO es generación con LLM — esa lógica vive en
`core/narrative/accent.py`. Aquí solo:

  • Validación de los 6 patrones canónicos.
  • Helpers para resolver la posición correcta según el patrón
    (hook_title_card → upper, resto → lower).
  • Wrapping de texto a UPPERCASE para el render.

Los 6 patrones (canónicos, alineados con `shared.schemas.AccentPattern`):
  1. number              — "$47,000", "85%", "3 STEPS"
  2. named_entity        — "DR. CHEN, STANFORD"
  3. jargon_translation  — "ENTANGLEMENT = SPOOKY LINK"
  4. hook_title_card     — ONLY beat.role == "hook"
  5. reaction            — "WAIT WHAT", "BIG IF TRUE"
  6. list_marker         — "STEP 2 OF 3"
"""

from __future__ import annotations

from shared.schemas import AccentOverlay, AccentPattern, AccentPosition, BeatRole

# Mapeo patrón → posición canónica.
# hook_title_card sale arriba para no chocar con los burst subtitles.
# Todos los demás van abajo (lower_third) — opuesto al burst.
_PATTERN_POSITION: dict[AccentPattern, AccentPosition] = {
    AccentPattern.NUMBER: AccentPosition.LOWER_THIRD,
    AccentPattern.NAMED_ENTITY: AccentPosition.LOWER_THIRD,
    AccentPattern.JARGON_TRANSLATION: AccentPosition.LOWER_THIRD,
    AccentPattern.HOOK_TITLE_CARD: AccentPosition.UPPER_THIRD,
    AccentPattern.REACTION: AccentPosition.LOWER_THIRD,
    AccentPattern.LIST_MARKER: AccentPosition.LOWER_THIRD,
}


def canonical_position(pattern: AccentPattern) -> AccentPosition:
    """Devuelve la posición canónica para un patrón.

    En el LLM-generated overlay puede llegar cualquier `position`; este
    helper sirve para CORREGIRLO antes de renderizar.
    """
    return _PATTERN_POSITION[pattern]


def is_valid_for_role(pattern: AccentPattern, role: BeatRole | str) -> bool:
    """¿Es válido emitir este patrón en este `beat.role`?

    Solo `hook_title_card` está restringido a beat.role == HOOK.
    """
    role_val = role.value if isinstance(role, BeatRole) else role
    if pattern == AccentPattern.HOOK_TITLE_CARD:
        return role_val == BeatRole.HOOK.value
    return True


def render_text(accent: AccentOverlay) -> str:
    """Devuelve el texto listo para emitir al .ass (siempre UPPERCASE).

    Se aplica a render-time, no a schema-time: el operador puede escribir
    el accent en cualquier case sin preocuparse.
    """
    return accent.text.upper()


def normalize_overlay(
    overlay: AccentOverlay,
    beat_role: BeatRole | str,
) -> AccentOverlay | None:
    """Normaliza un overlay: corrige posición, descarta si role inválido.

    Retorna None si el overlay no debe emitirse (e.g. hook_title_card en
    un beat que no es hook). Si es válido, devuelve un overlay corregido
    con la posición canónica.

    Espejo defensivo de la lógica en `core/narrative/accent.py`, útil para
    casos donde se invoca el renderer con overlays no producidos por la
    pipeline canónica (e.g. test fixtures, edición manual, importación).
    """
    if not is_valid_for_role(overlay.pattern, beat_role):
        return None
    desired = canonical_position(overlay.pattern)
    if overlay.position == desired:
        return overlay
    return overlay.model_copy(update={"position": desired})


def count_words(text: str) -> int:
    """Helper para validar el rango 2-6 palabras del schema."""
    return len(text.strip().split())


__all__ = [
    "canonical_position",
    "count_words",
    "is_valid_for_role",
    "normalize_overlay",
    "render_text",
]
