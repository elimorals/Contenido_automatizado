"""Prompts canónicos + catálogo de modelos extraídos del repo oficial de skills.

Fuente: `.claude/skills/higgsfield-skills/higgsfield-{generate,soul-id}/references/`
Estos templates espejan las reglas que el equipo de Higgsfield recomienda en
su documentación oficial — bakearlos aquí mejora motion fidelity y calidad.

NO importar el submódulo en runtime — los strings de aquí son la versión congelada.
Cuando upstream cambia, este archivo se regenera con `scripts/sync_higgsfield_prompts.py`.

Última sincronización: skills v0.3.0
"""
from __future__ import annotations

from typing import Literal


# =============================================================================
# CATÁLOGO DE MODELOS — IDs reales del CLI (`higgsfield model list --json`)
# =============================================================================
# Estos son los `job_set_type` que la Platform API entiende. La variante
# 'dop-turbo' que asumimos antes es un wrapper de WaveSpeed; el modelo CANÓNICO
# de Higgsfield para video premium es `seedance_2_0` (SOTA según las skills).

HIGGSFIELD_VIDEO_MODELS: dict[str, str] = {
    "seedance_2_0": "seedance_2_0",            # SOTA all-purpose serious video
    "kling3_0": "kling3_0",                    # cheaper substitute, single-plane
    "seedance_1_5_pro": "seedance_1_5_pro",    # budget, clean single-take
    "cinema_studio_3_0": "cinema_studio_video_3_0",  # cinema-grade premium
    "veo3_1_lite": "veo3_1_lite",              # batch/volume
    "grok_video_v15": "grok_video_v15",        # stylized i2v
    "marketing_studio_video": "marketing_studio_video",  # ads
}

HIGGSFIELD_IMAGE_MODELS: dict[str, str] = {
    "soul_v2": "text2image_soul_v2",           # aesthetic UGC, editorial
    "soul_cinematic": "soul_cinematic",        # cinematic still frame
    "soul_cast": "soul_cast",                  # distinctive character persona
    "soul_location": "soul_location",          # environments, no-people scenes
    "nano_banana_2": "nano_banana_2",          # character/cartoon default
    "nano_banana_pro": "nano_banana_pro",      # high-tier character
    "gpt_image_2": "gpt_image_2",              # high-fidelity general + text
    "seedream_4_5": "seedream_4_5",            # complex face+scene edits
    "z_image": "z_image",                      # fast/cheap iteration
    "recraft_v4_1": "recraft_v4_1",            # vector/graphic design
}

# Wrappers WaveSpeed (3rd-party reseller, mantenidos por compat)
HIGGSFIELD_DOP_VARIANTS: dict[str, str] = {
    "dop-lite": "dop-lite",
    "dop-turbo": "dop-turbo",
    "dop-preview": "dop-preview",
}


# =============================================================================
# CANONICAL STYLE BLOCKS — del prompt-engineering.md de las skills
# =============================================================================
# Regla #1: "Higgsfield models reward concrete, sensory prompts."
# Regla #2: Keep under ~200 tokens — models distort con prompts muy largos.
# Regla #3: "Don't redescribe the static frame" — el modelo ya tiene la imagen.
# Regla #4: Negative phrasing es positivo: "tack sharp" en vez de "no blur".

_VIDEO_GENERAL_STYLE = (
    "cinematic motion, smooth camera dynamics, natural environmental movement, "
    "realistic depth and parallax, 24fps film cadence"
)

_VIDEO_SCIENTIFIC_STYLE = (
    "documentary cinematography, locked-off shots with subtle camera presence, "
    "neutral color grade, tack sharp focus throughout, realistic motion, "
    "no stylized effects"
)

_SOUL_AESTHETIC_STYLE = (
    "aesthetic UGC editorial style, magazine-cover composition, "
    "fashion-editorial lighting, soft natural color grade, "
    "tack sharp focus, single subject occupies upper two-thirds, "
    "vertical 9:16 framing, no text or letters in frame"
)

_SOUL_CINEMATIC_STYLE = (
    "cinematic still frame, film-grade lighting, dramatic mood, "
    "shallow depth of field, 35mm anamorphic look, "
    "vertical 9:16 framing with subject occupying upper two-thirds, "
    "no text or letters in frame"
)


def _style_for_mode(content_mode: str, *, cinematic: bool = False) -> str:
    """Selecciona el style block según content_mode."""
    if cinematic:
        return _SOUL_CINEMATIC_STYLE if content_mode != "scientific" else _VIDEO_SCIENTIFIC_STYLE
    if content_mode == "scientific":
        return _VIDEO_SCIENTIFIC_STYLE
    return _SOUL_AESTHETIC_STYLE


# =============================================================================
# AUGMENTERS — usados por los providers
# =============================================================================

# Regla skills: para image-to-video, el prompt describe LA MOTION, no redescribe
# el first frame. Verbos clave: zooms, dollies, sweeping pan, slow push, whip,
# the subject spins, smoke rises slowly.

_MOTION_VERBS_BY_PRESET: dict[str, str] = {
    "static": "camera locked off, no movement, subject motion only",
    "dolly_in": "camera dollies in toward the subject with smooth tracking",
    "dolly_out": "camera dollies out, slowly revealing more of the scene",
    "super_dolly_in": "camera aggressively dollies forward, fast and decisive",
    "super_dolly_out": "camera aggressively dollies back, wide reveal",
    "dolly_zoom_in": "vertigo effect, dolly in while zoom out, Hitchcock zoom",
    "dolly_zoom_out": "reverse vertigo, dolly out while zoom in",
    "zoom_in": "slow steady zoom into the subject",
    "zoom_out": "slow steady zoom out from the subject",
    "rapid_zoom_in": "fast aggressive zoom in",
    "rapid_zoom_out": "fast aggressive zoom out",
    "crash_zoom_out": "abrupt crash zoom outward, kinetic energy",
    "pan_left": "smooth pan from right to left",
    "pan_right": "smooth pan from left to right",
    "whip_pan": "fast whip pan with motion blur",
    "tilt_up": "smooth tilt upward",
    "tilt_down": "smooth tilt downward",
    "dutch_angle": "subtle dutch angle tilt for unease",
    "overhead": "overhead bird's-eye perspective",
    "360_orbit": "smooth 360 degree orbital camera around the subject",
    "arc_right": "camera arcs around to the right while maintaining subject focus",
    "hero_cam": "low hero shot looking up at the subject",
    "fpv_drone": "fast FPV drone shot, kinetic immersive flight",
    "flying_cam_transition": "flying camera transition into the scene",
    "handheld": "natural handheld camera with subtle organic motion",
    "head_tracking": "tight tracking on subject's head movement",
    "robo_arm": "precise robotic camera arm move, mechanical smoothness",
    "snorricam": "snorricam rig, subject locked to frame with environment moving",
    "lazy_susan": "slow rotating lazy susan turntable motion",
}


def augment_dop_prompt(
    image_prompt: str,
    motion_preset: str,
    content_mode: str = "general",
) -> str:
    """Construye el prompt final para DoP / Seedance i2v siguiendo las skills.

    Estructura recomendada por prompt-engineering.md:
        <motion clause> + <subject motion if any> + <style block>

    NO redescribe el first frame — el modelo ya lo tiene.

    Args:
        image_prompt: Prompt original del beat (puede ser descriptivo de la escena).
        motion_preset: Valor de `HiggsfieldPreset` (ej "dolly_in", "360_orbit").
        content_mode: "scientific" | "general".

    Returns:
        Prompt enriquecido, capped en ~200 tokens equivalente (~1200 chars).
    """
    motion_clause = _MOTION_VERBS_BY_PRESET.get(
        motion_preset, f"camera {motion_preset.replace('_', ' ')}"
    )
    # No redescribimos el frame estático — usamos el image_prompt como contexto
    # de SUBJECT motion (ej "the dancer spins"), no como redescripción visual.
    style_block = _style_for_mode(content_mode)
    parts = [motion_clause]
    if image_prompt.strip():
        # Tomamos solo el verbo/acción principal del image_prompt si existe.
        parts.append(image_prompt.strip().rstrip("."))
    parts.append(style_block)
    composed = ". ".join(parts) + "."
    # Cap en ~1200 chars (≈200 tokens)
    if len(composed) > 1200:
        composed = composed[:1197] + "..."
    return composed


def augment_soul_prompt(
    image_prompt: str,
    content_mode: str = "general",
    *,
    cinematic: bool = False,
) -> str:
    """Construye prompt para Soul (text2image_soul_v2 o soul_cinematic).

    Soul es text-to-image con identity reference. La regla de skills es
    mantenerlo concreto y sensorial: subject + setting + style + lighting.

    Args:
        image_prompt: Prompt original.
        content_mode: "scientific" | "general".
        cinematic: True si se usa soul_cinematic (style block más dramático).
    """
    base = image_prompt.strip().rstrip(".")
    style_block = _style_for_mode(content_mode, cinematic=cinematic)
    composed = f"{base}. {style_block}."
    if len(composed) > 1200:
        composed = composed[:1197] + "..."
    return composed


# =============================================================================
# SOUL TRAINING — validation por photo-guide.md
# =============================================================================

SOUL_PHOTO_MIN = 5
SOUL_PHOTO_MAX = 20
SOUL_PHOTO_SWEET_SPOT = (8, 12)

SOUL_PHOTO_GUIDE = """
Requisitos para entrenar un Soul Character (de skill v0.3.0):

CANTIDAD: 5-20 fotos (8-12 sweet spot).

CONTENIDO:
- Rostro claro, ojos visibles
- Una sola persona por foto
- Sin filtros pesados, sin lentes oscuros

VARIEDAD (clave para mejor identity capture):
- Múltiples ángulos: front, 3/4 left, 3/4 right, slight up/down
- Distintas iluminaciones: indoor, outdoor, soft, harsh
- Distintas expresiones: neutral, smiling, talking
- Distintas distancias: head shot, head-and-shoulders, full body

CALIDAD:
- Sharp, in-focus
- Resolución ≥ 1024×1024 ideal
- JPEG o PNG

EVITAR:
- Group photos
- Heavy makeup not normally worn
- Costumes / cosplay
- Hats covering the face
- Same pose repeated
"""


class SoulTrainingValidationError(ValueError):
    """Lanzado cuando las reference images no cumplen el photo guide."""


def validate_soul_training_set(image_paths: list, *, strict: bool = False) -> list[str]:
    """Valida un set de imágenes contra el photo guide oficial.

    Devuelve lista de warnings (vacía si todo OK).
    Si `strict=True`, lanza `SoulTrainingValidationError` ante cualquier violación.
    """
    warnings: list[str] = []
    n = len(image_paths)
    if n < SOUL_PHOTO_MIN:
        msg = f"Solo {n} fotos provistas; mínimo es {SOUL_PHOTO_MIN}."
        if strict:
            raise SoulTrainingValidationError(msg)
        warnings.append(msg)
    if n > SOUL_PHOTO_MAX:
        msg = f"{n} fotos provistas; máximo es {SOUL_PHOTO_MAX} (extras serán descartadas)."
        warnings.append(msg)
    sweet_lo, sweet_hi = SOUL_PHOTO_SWEET_SPOT
    if not (sweet_lo <= n <= sweet_hi):
        warnings.append(
            f"{n} fotos está fuera del sweet spot {sweet_lo}-{sweet_hi}; "
            "calidad de captura puede ser subóptima."
        )
    return warnings


# =============================================================================
# ASPECT RATIO recommendations
# =============================================================================

ASPECT_GUIDANCE: dict[str, str] = {
    "9:16": "vertical, social — TikTok, Reels, Shorts",
    "16:9": "landscape, cinematic — YouTube, web",
    "1:1": "square, profile / icon — Instagram Feed",
    "4:3": "classic, model-dependent (check `higgsfield model get`)",
    "3:4": "portrait, model-dependent",
    "21:9": "ultra-wide cinema, model-dependent",
}


# =============================================================================
# SAFETY hints
# =============================================================================

UNSAFE_PROMPT_PATTERNS: tuple[str, ...] = (
    # Models reject with `nsfw` o `ip_detected` terminal status.
    # Estas son señales que disparan el filtro server-side.
    "nude", "naked", "explicit",
    # Trademarks comunes — el modelo bloquea por ip_detected.
    "mickey mouse", "darth vader", "harry potter", "pokemon", "marvel",
)


def quick_safety_check(prompt: str) -> tuple[bool, str | None]:
    """Heurística local: True si el prompt parece safe.

    No reemplaza el filtro server-side, solo detecta patrones obvios
    antes de gastar el call.
    """
    low = prompt.lower()
    for pattern in UNSAFE_PROMPT_PATTERNS:
        if pattern in low:
            return False, f"prompt contiene patrón potencialmente bloqueado: '{pattern}'"
    return True, None
