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
    COMFYUI = "comfyui"  # Local ComfyUI: LoRA + ControlNet + grafos custom
    LIVE_AVATAR = "live_avatar"  # Alibaba-Quark talking-head (audio-driven lip-sync)


class ComfyOutputType(str, Enum):
    """¿Qué produce este workflow? Importa para wiring downstream."""

    IMAGE = "image"  # first frame para Veo/DoP
    VIDEO = "video"  # AnimateDiff / SVD / WAN-Video direct output


class ComfyWorkflowKind(str, Enum):
    """Categoría semántica del workflow — usada por el selector."""

    BASIC_T2I = "basic_t2i"          # Flux/SDXL puro txt2img
    LORA_T2I = "lora_t2i"            # Brand LoRA + txt2img
    LORA_CONTROLNET = "lora_controlnet"  # LoRA + ControlNet (pose/depth/canny)
    IPADAPTER_REFERENCE = "ipadapter_reference"  # Style transfer desde reference image
    ANIMATEDIFF_LORA = "animatediff_lora"  # video t2v con LoRA
    INPAINT = "inpaint"              # producto cambia, fondo persiste
    UPSCALE_RESTORE = "upscale_restore"  # post-step: subir res + face restore


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
# EDITORIAL LAYER (portado de corredor-content)
# =============================================================================


class DistributionPlatform(str, Enum):
    """Plataforma destino del reel (distinto de VideoSource, que es stock origin)."""

    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE_LONG = "youtube_long"
    FACEBOOK_REELS = "facebook_reels"
    LINKEDIN_VIDEO = "linkedin_video"


class EntryType(str, Enum):
    """Entry point del pipeline DAG (mismas 3 puertas que /videos)."""

    TOPIC = "topic"
    URL = "url"
    SUBJECT = "subject"


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

    # === LiveAvatar (talking-head, audio-driven lip-sync) ===
    audio_path: Path | None = Field(
        None,
        description=(
            "Path al WAV de TTS sample-accurate para este beat. Cuando se setea "
            "Y existe una reference image (first_frame_path resuelto upstream), "
            "el orchestrator enruta a LiveAvatarGenerator en vez de DoP/Veo. "
            "El audio determina la duración del clip (lip-sync). Si None, se usa "
            "el pipeline visual estándar (stock/gen + i2v motion)."
        ),
    )
    reference_image_path: Path | None = Field(
        None,
        description=(
            "Override explícito de la imagen-referencia para LiveAvatar. Si None, "
            "el generator usa first_frame_path del BeatArtifact previo (Soul/Comfy/Gemini). "
            "Útil cuando el presentador es fijo (anchor brand) y no se regenera por beat."
        ),
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
    """Request unificado para los 4 entry points (url, topic, subject, long_form_input)."""

    # === Entry (uno de los cuatro requerido) ===
    url: str | None = None
    topic: str | None = None
    subject: str | None = Field(None, alias="video_subject")

    # Long-form entry point: contenido de un libro/script/podcast (5-60 min video)
    long_form_input: str | None = Field(
        None,
        description="Texto crudo (idea/script/novel) — dispara pipeline long_form",
    )
    long_form_source_kind: Literal["idea", "script", "novel", "article", "podcast_transcript"] = "idea"
    long_form_target_minutes: float = Field(10.0, gt=0.5, le=120)

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
        if not any([self.url, self.topic, self.subject, self.long_form_input]):
            raise ValueError(
                "Se requiere uno de: url, topic, subject, long_form_input"
            )
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


# =============================================================================
# EDITORIAL SCHEMAS (Pillar, Audience, PlatformSpec, ReelIdea, EditorialPlan)
# =============================================================================


class Pillar(BaseModel):
    """Pilar editorial. id kebab-case = nombre del .md en editorial/pillars/."""

    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$", max_length=40)
    label: str = Field(..., min_length=2, max_length=80)
    description: str = Field("", max_length=400)


class Audience(BaseModel):
    """Perfil de audiencia (de editorial/audiences.json)."""

    id: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$", max_length=40)
    label: str = Field(..., min_length=2, max_length=80)
    age_range: tuple[int, int] = Field(..., description="[min, max]")
    interests: list[str] = Field(default_factory=list)
    voice_register: Literal["tuteo", "usted"] = Field("tuteo", alias="register")
    language: str = "es-MX"
    notes: str = ""

    model_config = {"populate_by_name": True}

    @field_validator("age_range")
    @classmethod
    def check_age_range(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 0 or v[1] > 120 or v[0] >= v[1]:
            raise ValueError(f"age_range inválido: {v}")
        return v


class VideoDurationSpec(BaseModel):
    """Duración válida por plataforma (segundos)."""

    min_s: int = Field(..., ge=1, le=3600, alias="min")
    max_s: int = Field(..., ge=1, le=3600, alias="max")
    recommended: tuple[int, int] = Field(...)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def check_range(self) -> VideoDurationSpec:
        if self.min_s >= self.max_s:
            raise ValueError(f"min_s ({self.min_s}) >= max_s ({self.max_s})")
        lo, hi = self.recommended
        if lo < self.min_s or hi > self.max_s or lo >= hi:
            raise ValueError(
                f"recommended {self.recommended} fuera de [min={self.min_s}, max={self.max_s}]"
            )
        return self


class PlatformSpec(BaseModel):
    """Specs editoriales y técnicas de una plataforma destino."""

    id: DistributionPlatform
    aspect_ratio: VideoAspect
    video_duration_s: VideoDurationSpec
    caption_max_chars: int = Field(..., ge=1, le=100_000)
    caption_recommended_chars: tuple[int, int]
    hashtags_min: int = Field(0, ge=0, le=50)
    hashtags_max: int = Field(0, ge=0, le=50)
    notes: str = ""

    @model_validator(mode="after")
    def check_chars(self) -> PlatformSpec:
        lo, hi = self.caption_recommended_chars
        if lo > hi or hi > self.caption_max_chars:
            raise ValueError(
                f"caption_recommended_chars {self.caption_recommended_chars} fuera de max {self.caption_max_chars}"
            )
        if self.hashtags_min > self.hashtags_max:
            raise ValueError(
                f"hashtags_min ({self.hashtags_min}) > max ({self.hashtags_max})"
            )
        return self


class ReelIdea(BaseModel):
    """Una idea de reel del plan editorial — gate humano antes de producir."""

    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(..., min_length=8, max_length=120)
    pillar: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    audience: str = Field(..., pattern=r"^[a-z][a-z0-9-]*$")
    hook: str = Field(..., min_length=10, max_length=280)
    rationale: str = Field(..., min_length=10, max_length=400)
    platforms: list[DistributionPlatform] = Field(..., min_length=1)

    # Cómo entra al DAG existente (uno de los 3 entry points)
    entry_type: EntryType = EntryType.TOPIC
    entry_value: str = Field(..., min_length=3)

    # Gate humano
    approved: bool = False

    # Producción (se llena post-produce)
    task_id: str | None = None
    output_path: str | None = None
    cost_usd: float | None = None


class EditorialPlan(BaseModel):
    """Plan editorial semanal (uno por semana ISO)."""

    week: str = Field(..., pattern=r"^\d{4}-W\d{2}$")
    generated_at: str
    ideas: list[ReelIdea] = Field(..., min_length=1, max_length=20)

    def approved_ideas(self) -> list[ReelIdea]:
        return [i for i in self.ideas if i.approved]

    def by_pillar(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for idea in self.ideas:
            counts[idea.pillar] = counts.get(idea.pillar, 0) + 1
        return counts


class Fact(BaseModel):
    """Un hecho verificable referenciable por evidence[]."""

    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    claim: str = Field(..., min_length=10, max_length=400)
    source: str = Field("", description="URL, paper, libro")
    year: int | None = None
    tags: list[str] = Field(default_factory=list)


class Person(BaseModel):
    """Persona referenciable por hunters (specific_figure)."""

    id: str
    name: str
    years_active: str | None = None  # "1957-1972" o "1967"
    field: str
    relevance: str = Field(..., min_length=10, max_length=300)


class Study(BaseModel):
    """Estudio/paper referenciable por hunters (temporal)."""

    id: str
    title: str
    authors: list[str]
    year: int
    journal: str = ""
    sample_size: int | None = None
    key_finding: str = Field(..., min_length=10, max_length=400)


class FactsDocument(BaseModel):
    """Contenido completo de editorial/facts.json."""

    brand: dict = Field(default_factory=dict)
    rules_for_hunters: dict = Field(default_factory=dict)
    verified_facts: list[Fact] = Field(default_factory=list)
    verified_people: list[Person] = Field(default_factory=list)
    verified_studies: list[Study] = Field(default_factory=list)


class LocalEvent(BaseModel):
    """Evento del calendario para seedeo del plan editorial."""

    name: str
    location: str = ""
    start_month: int = Field(..., ge=1, le=12)
    end_month: int = Field(..., ge=1, le=12)
    yearly: bool = True
    angles: list[str] = Field(default_factory=list)


# =============================================================================
# COST TRACKING (priceOf pattern de corredor-content)
# =============================================================================


class LLMCostRecord(BaseModel):
    """Costo de UN LLM call — agregable a TaskInfo.cost_breakdown."""

    provider: str
    model: str
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cost_usd: float = Field(..., ge=0)
    phase: str = Field("", description="hunt|critic|narrate|judge|extract|compose|visual|accent|other")


# =============================================================================
# COMFYUI SCHEMAS (workflows custom + multi-tenant LoRA)
# =============================================================================


class ComfyParameterMap(BaseModel):
    """Mapeo de parámetros customizables de un workflow → nodos concretos.

    Permite que el caller use nombres semánticos (`prompt`, `negative_prompt`,
    `seed`, `width`, `height`, `lora_name`, `lora_strength`, `reference_image`)
    sin saber qué node_id los implementa en ESTE workflow específico.

    El loader (`comfy_workflows.py`) traduce a las llaves planas
    `{node_id}-inputs-{param_name}` que usa el JSON.
    """

    prompt: str | None = None              # ej: "6-inputs-text"
    negative_prompt: str | None = None     # ej: "7-inputs-text"
    seed: str | None = None                # ej: "3-inputs-seed"
    width: str | None = None               # ej: "5-inputs-width"
    height: str | None = None              # ej: "5-inputs-height"
    steps: str | None = None               # ej: "3-inputs-steps"
    cfg: str | None = None                 # ej: "3-inputs-cfg"
    checkpoint: str | None = None          # ej: "4-inputs-ckpt_name"
    lora_name: str | None = None           # ej: "10-inputs-lora_name"
    lora_strength: str | None = None       # ej: "10-inputs-strength_model"
    reference_image: str | None = None     # ej: "52-inputs-image"
    controlnet_image: str | None = None    # ej: "30-inputs-image"
    frames: str | None = None              # animatediff: "20-inputs-frame_count"
    fps: str | None = None                 # animatediff: "20-inputs-fps"

    # Custom overrides (escape hatch): {"42-inputs-custom_value": "..."}
    custom: dict[str, str] = Field(default_factory=dict)


class ComfyWorkflowSpec(BaseModel):
    """Spec de un workflow ComfyUI registrado en `workflows/`.

    Un workflow es un JSON en formato API (no GUI) + metadata que dice
    qué hace y cómo parametrizarlo.
    """

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_-]*$", max_length=60)
    name: str = Field(..., min_length=3, max_length=120)
    kind: ComfyWorkflowKind
    output_type: ComfyOutputType = ComfyOutputType.IMAGE

    # Path al JSON template (relativo a workflows/ o absoluto)
    json_path: str

    # Mapping de parámetros lógicos → node-input keys del JSON específico
    parameters: ComfyParameterMap = Field(default_factory=ComfyParameterMap)

    # Nodos cuya `images`/`gifs`/`videos` output capturamos
    output_nodes: list[str] = Field(
        default_factory=lambda: ["9"],
        description="Lista de node_ids de SaveImage/VHS_VideoCombine cuyos outputs descargar",
    )

    # Requisitos: modelos, custom nodes, RAM
    required_checkpoints: list[str] = Field(default_factory=list)
    required_loras: list[str] = Field(default_factory=list)
    required_custom_nodes: list[str] = Field(default_factory=list)
    estimated_seconds: float = 30.0  # cost estimate por run
    estimated_vram_gb: float = 12.0

    # Cost estimate USD (compute, no providers managed)
    estimated_cost_usd: float = 0.0


class BrandVisualConfig(BaseModel):
    """Configuración visual por tenant/marca: qué LoRA + workflow usar.

    Se carga desde `editorial/brand-visual.json` o se pasa per-request.
    Multi-tenant: cada `tenant_id` apunta a su propia LoRA + workflow.
    """

    tenant_id: str = Field("default", pattern=r"^[a-z0-9][a-z0-9_-]*$")
    label: str = "Default tenant"

    # Workflow primario para first frames con identidad de marca
    primary_workflow_id: str | None = None  # ej: "flux_lora_brand"
    # Workflow secundario para casos especiales (inpaint, controlnet)
    fallback_workflow_ids: list[str] = Field(default_factory=list)

    # LoRA principal de marca (path o nombre relativo a ComfyUI/models/loras/)
    lora_name: str | None = None
    lora_strength: float = Field(0.85, ge=0.0, le=2.0)

    # Style block que se appendea al image_prompt antes de mandar
    style_suffix: str = ""

    # Negative prompt baseline
    negative_prompt: str = (
        "blurry, low quality, deformed, text, watermark, logo, signature"
    )

    # Defaults técnicos
    default_width: int = 720
    default_height: int = 1280
    default_steps: int = 25
    default_cfg: float = 7.0

    # Reference images (IPAdapter) — paths absolutos o nombres en ComfyUI/input/
    reference_images: list[str] = Field(default_factory=list)


class ComfyJobStatus(str, Enum):
    """Estado interno de un job ComfyUI tracked por el cliente."""

    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    TIMEOUT = "timeout"


class ComfyJob(BaseModel):
    """Tracking de UN job de ComfyUI desde submit hasta retrieval."""

    prompt_id: str
    client_id: str
    workflow_id: str
    status: ComfyJobStatus = ComfyJobStatus.QUEUED

    # Progreso WebSocket
    current_node: str | None = None
    progress_value: int = 0
    progress_max: int = 0

    # Outputs (filename + subfolder + type) recuperados de /history
    images: list[dict[str, str]] = Field(default_factory=list)
    videos: list[dict[str, str]] = Field(default_factory=list)
    gifs: list[dict[str, str]] = Field(default_factory=list)

    # Error info si status=failed
    error_message: str = ""
    error_node: str | None = None

    # Timings
    submitted_at_unix: float = 0.0
    completed_at_unix: float = 0.0

    @property
    def duration_s(self) -> float:
        if self.completed_at_unix and self.submitted_at_unix:
            return self.completed_at_unix - self.submitted_at_unix
        return 0.0


# =============================================================================
# LONG-FORM SCHEMAS (ViMax-inspired — para video largo 5-60 min)
# =============================================================================


class LongFormIntent(str, Enum):
    """Routing del Script Planner: distinto tono por intent."""

    NARRATIVE = "narrative"       # personajes + diálogos + arc
    MOTION = "motion"             # acción + cinematografía técnica
    MONTAGE = "montage"           # arco emocional vía imágenes + pacing
    TALKING_HEAD = "talking_head"  # presentador on-screen con lip-sync (LiveAvatar)


class CharacterProfile(BaseModel):
    """Personaje recurrente con apariencia + persona para consistency cross-scene."""

    name: str = Field(..., min_length=1, max_length=80)
    appearance: str = Field(..., description="Rasgos físicos: género, edad, etnia, ropa, pelo, etc")
    persona: str = Field("", description="Personalidad, motivación, arc")
    portrait_path: str | None = Field(
        None, description="Path a imagen-portrait generada (front view) — referencia base"
    )
    portrait_alt_views: list[str] = Field(
        default_factory=list,
        description="Paths a vistas alternativas (perfil, espalda, full body)",
    )


class Shot(BaseModel):
    """Una toma — equivalente a un Beat pero a nivel long-form."""

    idx: int = Field(..., ge=0)
    scene_idx: int = Field(..., ge=0)
    visual_description: str = Field(..., min_length=10)

    # Decomposition (ViMax-inspired): first/last frame + motion
    first_frame_desc: str = ""
    last_frame_desc: str = ""
    motion_desc: str = ""

    # Cinematic language
    shot_type: str = Field(
        "medium",
        description="close_up | medium | wide | extreme_wide | over_shoulder | pov | aerial",
    )
    camera_angle: str = Field(
        "eye_level",
        description="eye_level | high | low | dutch | overhead",
    )
    camera_movement: str = Field(
        "static",
        description="static | dolly_in | dolly_out | pan_left | pan_right | tilt | zoom | track",
    )

    # Dialogue (al menos uno por shot si narrative)
    speaker: str | None = None
    dialogue: str | None = None

    # Duration estimate (seconds)
    target_duration_s: float = Field(4.0, gt=0, le=60)

    # Characters present in this shot (refs to CharacterProfile.name)
    characters_present: list[str] = Field(default_factory=list)

    # Provenance for VLM consistency
    reference_frame_paths: list[str] = Field(
        default_factory=list,
        description="Paths a frames previos usados como reference image (IPAdapter)",
    )


class Scene(BaseModel):
    """Una escena — N shots con setting + characters consistentes."""

    idx: int = Field(..., ge=0)
    title: str = Field(..., min_length=3, max_length=200)
    setting: str = Field(..., description="Lugar + tiempo + atmósfera")
    summary: str = Field(..., description="Qué pasa narrativamente en esta escena")
    characters_in_scene: list[str] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)

    # Continuity con la escena previa
    continuation_from_prev: str = Field(
        "",
        description="Qué hereda de la escena anterior (props, location, character state)",
    )


class NarrativeArc(BaseModel):
    """Arco narrativo de tres actos + plot points clave."""

    title: str
    logline: str = Field(..., max_length=400, description="Una frase con la premisa")
    act1_setup: str = Field(..., description="Mundo + protagonista + inciting incident")
    act2_confrontation: str = Field(..., description="Obstáculos + escalation + midpoint")
    act3_resolution: str = Field(..., description="Climax + payoff + denouement")
    themes: list[str] = Field(default_factory=list, max_length=5)
    target_minutes: float = Field(10.0, gt=0.5, le=120)


class LongFormScript(BaseModel):
    """Script completo para video largo — listo para shoot."""

    arc: NarrativeArc
    intent: LongFormIntent = LongFormIntent.NARRATIVE
    characters: list[CharacterProfile] = Field(default_factory=list, max_length=20)
    scenes: list[Scene] = Field(..., min_length=1)

    # Provenance del input original
    source_kind: Literal["novel", "article", "idea", "script", "podcast_transcript"] = "idea"
    source_text_hash: str = ""  # SHA256 corto del input para cache

    @property
    def total_shots(self) -> int:
        return sum(len(s.shots) for s in self.scenes)

    @property
    def estimated_duration_s(self) -> float:
        return sum(sh.target_duration_s for sc in self.scenes for sh in sc.shots)


class ConsistencyAnchor(BaseModel):
    """Snapshot del estado visual de un shot — usado como reference para los próximos.

    El selector elige cuáles anchors mostrar al image gen del siguiente shot
    (ej. los 3 más recientes + portraits de los characters presentes).
    """

    shot_idx: int
    scene_idx: int
    frame_path: str
    description: str = Field(..., description="Texto + composición + characters present")
    character_names: list[str] = Field(default_factory=list)
    camera_id: str = Field(
        "cam0", description="ID del 'camera position' — shots desde mismo cam comparten estilo"
    )
    created_at_unix: float = 0.0


class LongFormJob(BaseModel):
    """Tracking de UN job long-form de principio a fin."""

    job_id: str
    source_kind: Literal["novel", "article", "idea", "script"] = "idea"
    target_minutes: float = 10.0
    intent: LongFormIntent = LongFormIntent.NARRATIVE
    status: Literal["pending", "planning", "shooting", "stitching", "completed", "failed"] = "pending"

    # Artifacts
    script_path: str | None = None      # LongFormScript serialized JSON
    chunks_dir: str | None = None        # /chunks/*.txt (RAG store)
    compressed_dir: str | None = None    # /compressed/*.txt
    portraits_dir: str | None = None     # /portraits/*.jpg
    shots_dir: str | None = None         # /shots/scene_X_shot_Y.{jpg,mp4}
    final_video_path: str | None = None

    # Metrics
    total_chunks: int = 0
    total_scenes: int = 0
    total_shots: int = 0
    actual_duration_s: float = 0.0

    # Cost tracking
    cost_breakdown: dict[str, float] = Field(default_factory=dict)
    timings_s: dict[str, float] = Field(default_factory=dict)

    error_message: str = ""
