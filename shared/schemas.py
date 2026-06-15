"""Pydantic 2 schemas unificados — merge de MoneyPrinterTurbo + reels-af.

Convención: nombres en inglés (match con código de referencia), descripciones en español.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# ENUMS
# =============================================================================


class GenerationMode(str, Enum):
    """Modo express (MPT clásico) vs premium (reels-af DAG profundo)."""

    EXPRESS = "express"
    PREMIUM = "premium"


class VideoAspect(str, Enum):
    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"

    def dimensions(self) -> tuple[int, int]:
        return {
            VideoAspect.LANDSCAPE: (1920, 1080),
            VideoAspect.PORTRAIT: (1080, 1920),
            VideoAspect.SQUARE: (1080, 1080),
        }[self]


class VideoSource(str, Enum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    COVERR = "coverr"
    LOCAL = "local"
    GEMINI_IMAGE = "gemini_image"
    VEO = "veo"
    HIGGSFIELD_DOP = "higgsfield_dop"
    HIGGSFIELD_SOUL = "higgsfield_soul"
    HIGGSFIELD_EFFECT = "higgsfield_effect"


class HiggsfieldModel(str, Enum):
    """Variantes del modelo DoP (i2v) y Soul (i2i) de Higgsfield."""

    DOP_LITE = "dop-lite"
    DOP_TURBO = "dop-turbo"
    DOP_PREVIEW = "dop-preview"
    SOUL_V2 = "text2image_soul_v2"
    SOUL_CINEMATIC = "soul_cinematic"


class HiggsfieldPreset(str, Enum):
    """50+ camera presets que entiende DoP por nombre semántico.

    Se mapean a motion IDs reales vía `core.visual.generation.higgsfield_motions`
    (catálogo cacheado de `getMotions()`).
    """

    STATIC = "static"
    DOLLY_IN = "dolly_in"
    DOLLY_OUT = "dolly_out"
    DOLLY_LEFT = "dolly_left"
    DOLLY_RIGHT = "dolly_right"
    SUPER_DOLLY_IN = "super_dolly_in"
    SUPER_DOLLY_OUT = "super_dolly_out"
    DOUBLE_DOLLY = "double_dolly"
    DOLLY_ZOOM_IN = "dolly_zoom_in"
    DOLLY_ZOOM_OUT = "dolly_zoom_out"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    RAPID_ZOOM_IN = "rapid_zoom_in"
    RAPID_ZOOM_OUT = "rapid_zoom_out"
    CRASH_ZOOM_OUT = "crash_zoom_out"
    YOYO_ZOOM = "yoyo_zoom"
    EATING_ZOOM = "eating_zoom"
    MOUTH_IN = "mouth_in"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    WHIP_PAN = "whip_pan"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    DUTCH_ANGLE = "dutch_angle"
    OVERHEAD = "overhead"
    JIB_UP = "jib_up"
    JIB_DOWN = "jib_down"
    HERO_CAM = "hero_cam"
    LAZY_SUSAN = "lazy_susan"
    ORBIT_360 = "360_orbit"
    ARC_RIGHT = "arc_right"
    ROBO_ARM = "robo_arm"
    SNORRICAM = "snorricam"
    FPV_DRONE = "fpv_drone"
    FLYING_CAM = "flying_cam_transition"
    HANDHELD = "handheld"
    HEAD_TRACKING = "head_tracking"
    OBJECT_POV = "object_pov"
    ROAD_RUSH = "road_rush"
    FISHEYE = "fisheye"
    FOCUS_CHANGE = "focus_change"
    LOW_SHUTTER = "low_shutter"
    INCLINE = "incline"
    WIGGLE = "wiggle"
    GLAM = "glam"
    TIMELAPSE_GLAM = "timelapse_glam"
    TIMELAPSE_HUMAN = "timelapse_human"
    TIMELAPSE_LANDSCAPE = "timelapse_landscape"
    HYPERLAPSE = "hyperlapse"
    THROUGH_OBJECT_IN = "through_object_in"
    THROUGH_OBJECT_OUT = "through_object_out"
    ROTATION_3D = "3d_rotation"


class HiggsfieldEffect(str, Enum):
    """Action effects VFX que se aplican como overlay del clip.

    Lista representativa; el catálogo real se descarga vía API y se cachea.
    """

    EXPLOSION = "explosion"
    TRANSFORMATION = "transformation"
    FIRE = "fire"
    LIGHTNING = "lightning"
    SHATTER = "shatter"
    DISSOLVE = "dissolve"
    FREEZE = "freeze"
    SMOKE = "smoke"
    PORTAL = "portal"
    TIME_WARP = "time_warp"


class VisualStrategy(str, Enum):
    STOCK = "stock"
    IA = "ia"
    HYBRID = "hybrid"


class VideoConcatMode(str, Enum):
    RANDOM = "random"
    SEQUENTIAL = "sequential"


class VideoTransitionMode(str, Enum):
    NONE = "none"
    SHUFFLE = "Shuffle"
    FADE_IN = "FadeIn"
    FADE_OUT = "FadeOut"
    SLIDE_IN = "SlideIn"
    SLIDE_OUT = "SlideOut"


class SubtitleStyle(str, Enum):
    """Word-burst (reels-af) o SRT clásico (MPT)."""

    WORD_BURST = "word_burst"
    SRT = "srt"


class HookVariant(str, Enum):
    SHOCK_STAT = "shock_stat"
    CONTRARIAN = "contrarian"
    AUTHORITY = "authority"
    CURIOSITY_GAP = "curiosity_gap"
    LISTICLE = "listicle"


class OpenStyle(str, Enum):
    QUESTION = "question"
    SETUP_FLIP = "setup_flip"
    CRYPTIC_SETUP = "cryptic_setup"
    TOPIC_TEASE = "topic_tease"
    PERSONAL_STAKE = "personal_stake"


class BeatRole(str, Enum):
    HOOK = "hook"
    MECHANISM = "mechanism"
    PAYOFF = "payoff"


class MotionHint(str, Enum):
    STATIC = "static"
    SLOW_ZOOM_IN = "slow_zoom_in"
    SLOW_ZOOM_OUT = "slow_zoom_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    KEN_BURNS = "ken_burns"


class AccentPattern(str, Enum):
    NUMBER = "number"
    NAMED_ENTITY = "named_entity"
    JARGON_TRANSLATION = "jargon_translation"
    HOOK_TITLE_CARD = "hook_title_card"
    REACTION = "reaction"
    LIST_MARKER = "list_marker"


class AccentPosition(str, Enum):
    LOWER_THIRD = "lower_third"
    UPPER_THIRD = "upper_third"


class TaskState(int, Enum):
    PROCESSING = 4
    COMPLETE = 1
    FAILED = -1
    QUEUED = 0


# =============================================================================
# NARRATIVE MODELS (de reels-af)
# =============================================================================


class Essence(BaseModel):
    """Extraída de una URL — un solo claim sorprendente con evidencia."""

    core_claim: str = Field(..., max_length=200)
    mechanism: str = Field(..., description="Por qué/cómo, 1-2 frases")
    evidence: list[str] = Field(..., min_length=1, max_length=3)
    content_mode: Literal["scientific", "general"] = "general"
    domain: str = Field(..., max_length=30)


class EssenceCandidate(Essence):
    """Candidato producido por un hunter (con ángulo y pitch de novedad)."""

    angle: str = Field(..., description="specific_figure | reversal | temporal | cross_domain")
    novelty_pitch: str = Field(..., max_length=120)


class CriticRanking(BaseModel):
    candidate_idx: int
    novelty: int = Field(..., ge=1, le=10)
    specificity: int = Field(..., ge=1, le=10)
    hookability: int = Field(..., ge=1, le=10)
    narratability: int = Field(..., ge=1, le=10)
    composite: float = Field(..., ge=1.0, le=10.0)
    why: str


class CriticOutput(BaseModel):
    top_3_indices: list[int] = Field(..., min_length=3, max_length=3)
    rankings: list[CriticRanking]


class ConversationalScript(BaseModel):
    """Script delayed-reveal (de narrator)."""

    tease: str = Field(..., description="5-15 words, NO answer/name/year")
    common_belief: str | None = None
    reveal: str = Field(..., description="Body con named entities, years, evidence")
    payoff: str = Field(..., description="Callback al tease")
    open_style: OpenStyle = OpenStyle.QUESTION
    target_wpm: int = Field(180, ge=160, le=200)
    narration: str = Field(..., description="Full script con [tags] inline")


class PairwiseVerdict(BaseModel):
    winner_idx: int
    composite_score: float = Field(..., ge=1.0, le=10.0)
    why: str


class ScriptDraft(BaseModel):
    """Unificado: usado por article path y como adaptación del topic path."""

    hook: str = Field(..., description="6-10 spoken words")
    hook_variant: HookVariant = HookVariant.CURIOSITY_GAP
    mechanism_lines: list[str] = Field(..., min_length=2, max_length=4)
    payoff_line: str
    target_wpm: int = Field(180, ge=120, le=200)
    narration: str = Field(..., description="hook + mechanism + payoff + [tags] inline")

    @model_validator(mode="after")
    def check_loop_back(self) -> ScriptDraft:
        """Última frase debe ecoar keyword del hook (curiosity loop).

        Busca el keyword del hook en `payoff_line` específicamente (no en hook
        ni en mechanism_lines). Esto fuerza que el cierre eche callback explícito.
        """
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "to", "of", "in", "on", "at", "for", "with", "by", "as", "and", "or",
            "but", "if", "then", "so", "that", "this", "it", "its", "we", "you",
            "they", "he", "she", "i", "why", "what", "how", "when", "where",
            "do", "does", "did", "have", "has", "had",
        }
        hook_words = [w.lower().strip(".,!?;:\"'") for w in self.hook.split() if w]
        keywords = [w for w in hook_words if w not in stopwords and len(w) > 2]
        if not keywords:
            return self
        longest = max(keywords, key=len)
        payoff_lower = self.payoff_line.lower()
        if longest not in payoff_lower:
            raise ValueError(
                f"Loop-back validator falló: hook keyword '{longest}' no aparece en "
                f"payoff_line ('{self.payoff_line[:80]}...'). Curiosity loop roto."
            )
        return self


# =============================================================================
# AUDIO / TIMING (sample-accurate)
# =============================================================================


class WordTiming(BaseModel):
    word: str
    start_s: float = Field(..., ge=0)
    end_s: float = Field(..., ge=0)


class AudioArtifact(BaseModel):
    path: Path
    duration_s: float
    word_timings: list[WordTiming]
    engine: str = Field(..., description="edge | gemini_flash | azure | mimo | siliconflow | silent")


# =============================================================================
# PLANNING (determinístico, sin LLM)
# =============================================================================


class Beat(BaseModel):
    """Unidad mínima de video con bucket Veo asignado."""

    idx: int = Field(..., ge=0)
    role: BeatRole
    text: str
    target_duration_s: float = Field(..., gt=0)
    veo_duration: Literal[4, 6, 8] = Field(
        ..., description="Bucket fijo de Veo: hook≥6, payoff≤4, mechanism el menor ≥target+0.3"
    )


class Card(BaseModel):
    """Layout de subtítulos (word-burst pack)."""

    text: str
    words: list[WordTiming]
    start_s: float
    end_s: float
    line_count: Literal[1, 2] = 1


# =============================================================================
# VISUAL (per-beat)
# =============================================================================


class BeatVisual(BaseModel):
    image_prompt: str = Field(..., description="Específico, grounded en evidence")
    motion_hint: MotionHint = MotionHint.SLOW_ZOOM_IN
    visual_anchor: str = Field(..., description="Qué item de evidence ancla el visual")

    # === Higgsfield extensions (opcionales) ===
    soul_id: str | None = Field(
        None,
        description="SoulId de Higgsfield para character consistency. Si se setea, el first frame se genera con HiggsfieldSoulGenerator en vez de Gemini Image.",
    )
    higgsfield_preset: HiggsfieldPreset | None = Field(
        None,
        description="Camera preset específico de Higgsfield DoP (override de motion_hint). Si None, se mapea desde motion_hint.",
    )
    higgsfield_motion_id: str | None = Field(
        None,
        description="Motion ID raw (UUID) cuando el preset no está en el enum. Override más bajo nivel todavía.",
    )
    effect: HiggsfieldEffect | None = Field(
        None,
        description="VFX effect a aplicar sobre el clip post-generación. None = sin efecto (default).",
    )
    effect_strength: float = Field(
        0.8,
        ge=0.0,
        le=1.0,
        description="Intensidad del effect (0=ausente, 1=máxima). Ignorado si effect=None.",
    )


class AccentOverlay(BaseModel):
    """Overlay editorial opcional (biased a None)."""

    text: str = Field(..., min_length=1)
    pattern: AccentPattern
    position: AccentPosition = AccentPosition.LOWER_THIRD

    @field_validator("text")
    @classmethod
    def check_word_count(cls, v: str) -> str:
        wc = len(v.split())
        if not (2 <= wc <= 6):
            raise ValueError(f"Accent text debe tener 2-6 palabras, tiene {wc}")
        return v


class BeatArtifact(BaseModel):
    """Output de la fase de media generation."""

    idx: int
    first_frame_path: Path | None = None
    video_path: Path | None = None
    source: VideoSource = VideoSource.GEMINI_IMAGE
    duration_s: float = 0.0


# =============================================================================
# STOCK MATERIAL (de MPT)
# =============================================================================


class MaterialInfo(BaseModel):
    provider: VideoSource = VideoSource.PEXELS
    url: str = ""
    local_path: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0


# =============================================================================
# REQUEST / RESPONSE (entry points)
# =============================================================================


class VideoParams(BaseModel):
    """Request unificado para los 3 entry points (url, topic, subject)."""

    # === Entry (uno de los tres requerido) ===
    url: str | None = None
    topic: str | None = None
    subject: str | None = Field(None, alias="video_subject")

    # === Modo de generación ===
    mode: GenerationMode = GenerationMode.PREMIUM

    # === Script (opcional, override) ===
    script: str | None = Field(None, alias="video_script")
    terms: list[str] | None = Field(None, alias="video_terms")
    language: str = "auto"
    paragraph_number: int = Field(1, ge=1, le=10)

    # === Video ===
    aspect: VideoAspect = VideoAspect.PORTRAIT
    video_source: VideoSource = VideoSource.PEXELS
    visual_strategy: VisualStrategy = VisualStrategy.HYBRID
    video_count: int = Field(1, ge=1, le=5)
    video_clip_duration: int = Field(5, ge=2, le=15)
    video_concat_mode: VideoConcatMode = VideoConcatMode.RANDOM
    video_transition_mode: VideoTransitionMode = VideoTransitionMode.NONE
    match_materials_to_script: bool = False
    video_materials: list[MaterialInfo] | None = None

    # === Voz ===
    voice_name: str = ""
    voice_volume: float = Field(1.0, ge=0.0, le=2.0)
    voice_rate: float = Field(1.0, ge=0.5, le=2.0)
    custom_audio_file: str | None = None

    # === BGM ===
    bgm_type: Literal["random", "none", "custom"] = "random"
    bgm_file: str = ""
    bgm_volume: float = Field(0.2, ge=0.0, le=1.0)

    # === Subtítulos ===
    subtitle_enabled: bool = True
    subtitle_style: SubtitleStyle = SubtitleStyle.WORD_BURST
    subtitle_position: Literal["bottom", "top", "center", "custom"] = "bottom"
    custom_position: float = 70.0
    font_name: str = "Montserrat-Bold.ttf"
    text_fore_color: str = "#FFFFFF"
    text_background_color: bool | str = False
    rounded_subtitle_background: bool = False
    font_size: int = Field(170, ge=20, le=300)
    stroke_color: str = "#000000"
    stroke_width: float = Field(1.5, ge=0.0, le=10.0)

    # === Generación premium ===
    use_veo: bool = False
    custom_system_prompt: str = Field("", max_length=8000)
    video_script_prompt: str = Field("", max_length=2000)

    # === Distribución ===
    auto_upload: bool = False
    upload_platforms: list[str] = Field(default_factory=list)

    # === Performance ===
    n_threads: int = Field(2, ge=1, le=16)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def check_entry_point(self) -> VideoParams:
        if not any([self.url, self.topic, self.subject]):
            raise ValueError("Se requiere uno de: url, topic, subject")
        return self


class TaskInfo(BaseModel):
    """Estado de una task en el sistema."""

    task_id: str
    state: TaskState
    mode: GenerationMode
    progress: int = Field(0, ge=0, le=100)
    request_id: str | None = None
    params: VideoParams | None = None

    # Artefactos
    script: str | None = None
    terms: list[str] | None = None
    audio_path: str | None = None
    audio_duration_s: float | None = None
    subtitle_path: str | None = None
    materials: list[str] = Field(default_factory=list)
    combined_videos: list[str] = Field(default_factory=list)
    videos: list[str] = Field(default_factory=list)

    # Premium-only
    essence: Essence | None = None
    chosen_candidate: EssenceCandidate | None = None
    winner_composite: float | None = None
    timings_s: dict[str, float] = Field(default_factory=dict)
    cost_breakdown: dict[str, float] = Field(default_factory=dict)

    # Distribución
    cross_post_results: list[dict] = Field(default_factory=list)


class BaseResponse(BaseModel):
    status: int = 200
    message: str = "success"
    data: dict | None = None
