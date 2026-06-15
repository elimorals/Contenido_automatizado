"""Loader unificado de configuración: config.toml + override por env vars.

Precedencia:
  1. Variables de entorno (más prioridad)
  2. config.toml
  3. config.example.toml (fallback)
  4. Defaults hardcoded en este módulo (último recurso)
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python < 3.11 fallback
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CONFIG_FILENAME = "config.toml"
EXAMPLE_FILENAME = "config.example.toml"


class AppConfig(BaseModel):
    env: str = "dev"
    default_mode: str = "premium"
    log_level: str = "INFO"
    tls_verify: bool = True
    max_concurrent_tasks: int = 3
    max_queued_tasks: int = 50
    enable_redis: bool = False
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    agentfield_server: str = "http://localhost:8080"
    agentfield_node_id: str = "contenido"
    agentfield_llm_call_timeout: int = 120


class LLMProviderConfig(BaseModel):
    api_key: str = ""
    model_name: str = ""
    base_url: str = ""
    api_version: str = ""
    default_model: str = ""


class TTSEngineConfig(BaseModel):
    api_key: str = ""
    model: str = ""
    default_voice: str = ""
    timeout: int = 30
    voice_tone: str = "wonder"
    region: str = ""


class HiggsfieldConfig(BaseModel):
    """Higgsfield Platform API — DoP (i2v), Soul (i2i char consistency), Effects.

    Auth: `Authorization: Key KEY_ID:KEY_SECRET` (V2). Acepta el par separado
    o la cadena combinada `credentials`. Env override siempre gana.
    """

    enabled: bool = False
    credentials: str = ""  # "KEY_ID:KEY_SECRET" — set vía HIGGSFIELD_CREDENTIALS
    key_id: str = ""       # set vía HIGGSFIELD_KEY_ID
    key_secret: str = ""   # set vía HIGGSFIELD_KEY_SECRET
    base_url: str = "https://platform.higgsfield.ai"
    timeout_s: float = 300.0
    poll_interval_s: float = 2.0
    max_poll_time_s: float = 600.0

    # DoP (image-to-video)
    dop_model: str = "dop-turbo"  # dop-lite | dop-turbo | dop-preview
    dop_endpoint: str = "/v1/image2video/dop"
    dop_clip_duration_s: int = 5  # DoP es fijo a 5s
    dop_motion_strength: float = 0.85  # 0.0-1.0

    # Soul (character consistency)
    soul_enabled: bool = False
    soul_model: str = "soul_cinematic"  # text2image_soul_v2 | soul_cinematic
    soul_endpoint: str = "/v1/text2image/soul"
    soul_default_style_id: str = ""  # si vacío, no se pasa al payload
    soul_default_reference_id: str = ""  # default SoulId si BeatVisual.soul_id está vacío
    soul_reference_strength: float = 0.75
    soul_width: int = 720
    soul_height: int = 1280

    # Effects (VFX overlay)
    effects_enabled: bool = False
    effects_endpoint: str = "/v1/effects/apply"

    # Selector behaviour
    prefer_over_veo: bool = True  # cuando ambos habilitados, Higgsfield gana
    motion_catalog_cache_path: str = "./storage/higgsfield_motions.json"

    # CLI fallback (subprocess) — opt-in. Cuando la REST API falla
    # (rate limit, 5xx, timeout), invoca el binario `higgsfield` como
    # último intento antes de devolver el control al orchestrator.
    cli_fallback_enabled: bool = False
    cli_binary_path: str = "higgsfield"  # asume en PATH; usar full path en prod
    cli_default_video_model: str = "seedance_2_0"  # modelo canónico Higgsfield
    cli_timeout_s: float = 900.0  # 15min — Seedance puede tardar


class VisualConfig(BaseModel):
    default_strategy: str = "hybrid"
    gemini_image_model: str = "openrouter/google/gemini-2.5-flash-image"
    canvas_w: int = 720
    canvas_h: int = 1280
    veo_enabled: bool = False
    veo_model: str = "openrouter/google/veo-3.1-lite"
    ken_burns_zoom: float = 1.15
    higgsfield: HiggsfieldConfig = Field(default_factory=HiggsfieldConfig)


class StockConfig(BaseModel):
    pexels_api_keys: list[str] = Field(default_factory=list)
    pixabay_api_keys: list[str] = Field(default_factory=list)
    coverr_api_keys: list[str] = Field(default_factory=list)
    provider_order: list[str] = Field(default_factory=lambda: ["pexels", "pixabay", "coverr"])
    cache_dir: str = "./storage/materials_cache"
    min_duration_s: float = 3.0
    max_duration_s: float = 30.0


class WhisperConfig(BaseModel):
    model_size: str = "large-v3"
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5
    vad_threshold_ms: int = 500


class SubtitleConfig(BaseModel):
    default_style: str = "word_burst"
    font_name: str = "Montserrat-Bold.ttf"
    font_size_burst: int = 170
    font_size_srt: int = 60
    fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 1.5
    background: bool = False
    position: str = "bottom"
    custom_position: float = 70.0
    cjk_font: str = "STHeitiMedium.ttc"


class EditorConfig(BaseModel):
    single_pass: bool = True
    codec: str = "libx264"
    crf: int = 23
    preset: str = "fast"
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    fps: int = 30
    hw_encoder_fallback: list[str] = Field(
        default_factory=lambda: [
            "h264_videotoolbox",
            "h264_nvenc",
            "h264_amf",
            "h264_qsv",
            "h264_mf",
            "libx264",
        ]
    )


class BGMConfig(BaseModel):
    default_type: str = "random"
    default_file: str = ""
    default_volume: float = 0.2
    library_dir: str = "./resource/songs"


class UIConfig(BaseModel):
    hide_log: bool = False
    show_cost_tracker: bool = True
    show_timings: bool = True
    default_language: str = "es-MX"
    supported_languages: list[str] = Field(default_factory=list)


class UploadPostConfig(BaseModel):
    api_key: str = ""
    auto_upload: bool = False
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "instagram"])


class Config(BaseModel):
    """Configuración raíz de contenido."""

    app: AppConfig = Field(default_factory=AppConfig)
    llm: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    llm_default_provider_premium: str = "openrouter"
    llm_default_provider_express: str = "edge_tts_companion"
    tts: dict[str, TTSEngineConfig] = Field(default_factory=dict)
    tts_default_premium: str = "gemini_flash"
    tts_default_express: str = "edge"
    tts_sample_accurate_timing: bool = True
    visual: VisualConfig = Field(default_factory=VisualConfig)
    stock: StockConfig = Field(default_factory=StockConfig)
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    subtitles: SubtitleConfig = Field(default_factory=SubtitleConfig)
    editor: EditorConfig = Field(default_factory=EditorConfig)
    bgm: BGMConfig = Field(default_factory=BGMConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    upload_post: UploadPostConfig = Field(default_factory=UploadPostConfig)

    # Aspect ratios (no es BaseModel anidado por simplicidad TOML)
    aspect_ratios: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "9:16": {"width": 1080, "height": 1920},
            "16:9": {"width": 1920, "height": 1080},
            "1:1": {"width": 1080, "height": 1080},
        }
    )

    def get_llm_provider(self, name: str) -> LLMProviderConfig:
        """Devuelve config de provider, creando vacía si no existe."""
        if name not in self.llm:
            self.llm[name] = LLMProviderConfig()
        return self.llm[name]

    def get_tts_engine(self, name: str) -> TTSEngineConfig:
        if name not in self.tts:
            self.tts[name] = TTSEngineConfig()
        return self.tts[name]


def _find_config_path(start: Path | None = None) -> Path | None:
    """Busca config.toml subiendo desde CWD hasta la raíz."""
    start = start or Path.cwd()
    for parent in [start, *start.parents]:
        candidate = parent / CONFIG_FILENAME
        if candidate.exists():
            return candidate
        example = parent / EXAMPLE_FILENAME
        if example.exists():
            return example
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def _apply_env_overrides(cfg: Config) -> Config:
    """Aplica overrides de env vars (12-factor compliance)."""
    # App
    cfg.app.env = os.getenv("CONTENIDO_ENV", cfg.app.env)
    cfg.app.default_mode = os.getenv("DEFAULT_MODE", cfg.app.default_mode)
    cfg.app.log_level = os.getenv("LOG_LEVEL", cfg.app.log_level)
    cfg.app.enable_redis = os.getenv("ENABLE_REDIS", str(cfg.app.enable_redis)).lower() == "true"
    cfg.app.redis_host = os.getenv("REDIS_HOST", cfg.app.redis_host)
    cfg.app.redis_port = int(os.getenv("REDIS_PORT", cfg.app.redis_port))
    cfg.app.max_concurrent_tasks = int(
        os.getenv("MAX_CONCURRENT_TASKS", cfg.app.max_concurrent_tasks)
    )
    cfg.app.max_queued_tasks = int(os.getenv("MAX_QUEUED_TASKS", cfg.app.max_queued_tasks))
    cfg.app.agentfield_server = os.getenv("AGENTFIELD_SERVER", cfg.app.agentfield_server)
    cfg.app.agentfield_node_id = os.getenv("AGENT_NODE_ID", cfg.app.agentfield_node_id)

    # LLM providers (mapeo común)
    env_llm_map = {
        "openai": "OPENAI_API_KEY",
        "azure": "AZURE_OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "qwen": "QWEN_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "mimo": "MIMO_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    for provider, env_var in env_llm_map.items():
        if os.getenv(env_var):
            cfg.get_llm_provider(provider).api_key = os.getenv(env_var, "")

    # Modelos override
    if os.getenv("REEL_AF_MODEL"):
        cfg.get_llm_provider("openrouter").default_model = os.getenv("REEL_AF_MODEL", "")

    # TTS
    if os.getenv("AZURE_TTS_API_KEY"):
        cfg.get_tts_engine("azure").api_key = os.getenv("AZURE_TTS_API_KEY", "")
        cfg.get_tts_engine("azure").region = os.getenv("AZURE_TTS_REGION", "eastus")
    if os.getenv("SILICONFLOW_API_KEY"):
        cfg.get_tts_engine("siliconflow").api_key = os.getenv("SILICONFLOW_API_KEY", "")

    # Visual
    cfg.visual.veo_enabled = os.getenv("REEL_AF_USE_VEO", str(cfg.visual.veo_enabled)).lower() == "true"
    if os.getenv("REEL_AF_IMAGE_MODEL"):
        cfg.visual.gemini_image_model = os.getenv("REEL_AF_IMAGE_MODEL", "")
    if os.getenv("REEL_AF_VIDEO_MODEL"):
        cfg.visual.veo_model = os.getenv("REEL_AF_VIDEO_MODEL", "")

    # Higgsfield (DoP + Soul + Effects)
    hf = cfg.visual.higgsfield
    if os.getenv("HIGGSFIELD_CREDENTIALS"):
        hf.credentials = os.getenv("HIGGSFIELD_CREDENTIALS", "")
    if os.getenv("HIGGSFIELD_KEY_ID"):
        hf.key_id = os.getenv("HIGGSFIELD_KEY_ID", "")
    if os.getenv("HIGGSFIELD_KEY_SECRET"):
        hf.key_secret = os.getenv("HIGGSFIELD_KEY_SECRET", "")
    # Soporte legacy HF_* (matching SDK oficial)
    if os.getenv("HF_CREDENTIALS"):
        hf.credentials = os.getenv("HF_CREDENTIALS", "")
    if os.getenv("HF_API_KEY"):
        hf.key_id = os.getenv("HF_API_KEY", "")
    if os.getenv("HF_API_SECRET"):
        hf.key_secret = os.getenv("HF_API_SECRET", "")
    # Composición credentials si vienen separados
    if not hf.credentials and hf.key_id and hf.key_secret:
        hf.credentials = f"{hf.key_id}:{hf.key_secret}"
    # Enable flags
    hf.enabled = os.getenv("HIGGSFIELD_ENABLED", str(hf.enabled)).lower() == "true"
    hf.soul_enabled = os.getenv("HIGGSFIELD_SOUL_ENABLED", str(hf.soul_enabled)).lower() == "true"
    hf.effects_enabled = os.getenv("HIGGSFIELD_EFFECTS_ENABLED", str(hf.effects_enabled)).lower() == "true"
    # Model overrides
    if os.getenv("HIGGSFIELD_DOP_MODEL"):
        hf.dop_model = os.getenv("HIGGSFIELD_DOP_MODEL", "")
    if os.getenv("HIGGSFIELD_SOUL_MODEL"):
        hf.soul_model = os.getenv("HIGGSFIELD_SOUL_MODEL", "")
    if os.getenv("HIGGSFIELD_BASE_URL"):
        hf.base_url = os.getenv("HIGGSFIELD_BASE_URL", "")
    if os.getenv("HIGGSFIELD_SOUL_REFERENCE_ID"):
        hf.soul_default_reference_id = os.getenv("HIGGSFIELD_SOUL_REFERENCE_ID", "")
    # CLI fallback
    hf.cli_fallback_enabled = os.getenv(
        "HIGGSFIELD_CLI_FALLBACK", str(hf.cli_fallback_enabled)
    ).lower() == "true"
    if os.getenv("HIGGSFIELD_CLI_BINARY"):
        hf.cli_binary_path = os.getenv("HIGGSFIELD_CLI_BINARY", "")
    if os.getenv("HIGGSFIELD_CLI_MODEL"):
        hf.cli_default_video_model = os.getenv("HIGGSFIELD_CLI_MODEL", "")

    # Stock
    if os.getenv("PEXELS_API_KEYS"):
        cfg.stock.pexels_api_keys = [k.strip() for k in os.getenv("PEXELS_API_KEYS", "").split(",") if k.strip()]
    if os.getenv("PIXABAY_API_KEYS"):
        cfg.stock.pixabay_api_keys = [k.strip() for k in os.getenv("PIXABAY_API_KEYS", "").split(",") if k.strip()]
    if os.getenv("COVERR_API_KEYS"):
        cfg.stock.coverr_api_keys = [k.strip() for k in os.getenv("COVERR_API_KEYS", "").split(",") if k.strip()]

    # Whisper
    cfg.whisper.model_size = os.getenv("WHISPER_MODEL_SIZE", cfg.whisper.model_size)
    cfg.whisper.device = os.getenv("WHISPER_DEVICE", cfg.whisper.device)
    cfg.whisper.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", cfg.whisper.compute_type)

    # Upload-Post
    if os.getenv("UPLOAD_POST_API_KEY"):
        cfg.upload_post.api_key = os.getenv("UPLOAD_POST_API_KEY", "")
    cfg.upload_post.auto_upload = os.getenv("UPLOAD_POST_AUTO_UPLOAD", str(cfg.upload_post.auto_upload)).lower() == "true"
    if os.getenv("UPLOAD_POST_PLATFORMS"):
        cfg.upload_post.platforms = [p.strip() for p in os.getenv("UPLOAD_POST_PLATFORMS", "").split(",") if p.strip()]

    return cfg


def _build_visual_config(raw_visual: dict[str, Any]) -> VisualConfig:
    """Construye VisualConfig respetando la sub-sección [visual.higgsfield].

    Los demás sub-dicts ([visual.gemini_image], [visual.veo], [visual.ken_burns])
    se mapean a campos flat por compatibilidad con el patrón previo.
    """
    flat = {k: v for k, v in raw_visual.items() if not isinstance(v, dict)}
    # Higgsfield sub-section
    hf_raw = raw_visual.get("higgsfield", {})
    if isinstance(hf_raw, dict) and hf_raw:
        flat["higgsfield"] = HiggsfieldConfig(**hf_raw)
    # Flatten legacy [visual.gemini_image] / [visual.veo] / [visual.ken_burns]
    if isinstance(raw_visual.get("gemini_image"), dict):
        gi = raw_visual["gemini_image"]
        if "model" in gi:
            flat.setdefault("gemini_image_model", gi["model"])
        if "canvas_w" in gi:
            flat.setdefault("canvas_w", gi["canvas_w"])
        if "canvas_h" in gi:
            flat.setdefault("canvas_h", gi["canvas_h"])
    if isinstance(raw_visual.get("veo"), dict):
        v = raw_visual["veo"]
        if "enabled" in v:
            flat.setdefault("veo_enabled", v["enabled"])
        if "model" in v:
            flat.setdefault("veo_model", v["model"])
    if isinstance(raw_visual.get("ken_burns"), dict):
        kb = raw_visual["ken_burns"]
        if "zoom_factor" in kb:
            flat.setdefault("ken_burns_zoom", kb["zoom_factor"])
    return VisualConfig(**flat)


def _coerce_dict_to_config(raw: dict[str, Any]) -> Config:
    """Convierte el dict raw de TOML a Config (mapeando secciones llm.* y tts.*)."""
    # Extraer secciones llm.* y tts.* explícitamente (TOML las anida)
    llm_section = raw.get("llm", {})
    llm_providers: dict[str, LLMProviderConfig] = {}
    llm_default_premium = "openrouter"
    llm_default_express = "edge_tts_companion"
    for k, v in llm_section.items():
        if isinstance(v, dict):
            llm_providers[k] = LLMProviderConfig(**v)
        elif k == "default_provider_premium":
            llm_default_premium = v
        elif k == "default_provider_express":
            llm_default_express = v

    tts_section = raw.get("tts", {})
    tts_engines: dict[str, TTSEngineConfig] = {}
    tts_default_premium = "gemini_flash"
    tts_default_express = "edge"
    tts_sample_acc = True
    for k, v in tts_section.items():
        if isinstance(v, dict):
            tts_engines[k] = TTSEngineConfig(**v)
        elif k == "default_premium":
            tts_default_premium = v
        elif k == "default_express":
            tts_default_express = v
        elif k == "sample_accurate_timing":
            tts_sample_acc = v

    config_kwargs: dict[str, Any] = {
        "app": AppConfig(**raw.get("app", {})),
        "llm": llm_providers,
        "llm_default_provider_premium": llm_default_premium,
        "llm_default_provider_express": llm_default_express,
        "tts": tts_engines,
        "tts_default_premium": tts_default_premium,
        "tts_default_express": tts_default_express,
        "tts_sample_accurate_timing": tts_sample_acc,
        "visual": _build_visual_config(raw.get("visual", {})),
        "stock": StockConfig(**raw.get("stock", {})),
        "whisper": WhisperConfig(**raw.get("whisper", {})),
        "subtitles": SubtitleConfig(**raw.get("subtitles", {})),
        "editor": EditorConfig(**{k: v for k, v in raw.get("editor", {}).items() if not isinstance(v, dict)}),
        "bgm": BGMConfig(**raw.get("bgm", {})),
        "ui": UIConfig(**raw.get("ui", {})),
        "upload_post": UploadPostConfig(**raw.get("upload_post", {})),
    }

    # Aspect ratios (sección anidada en [editor.aspect])
    editor_section = raw.get("editor", {})
    if "aspect" in editor_section and isinstance(editor_section["aspect"], dict):
        aspect_data = {
            k: v for k, v in editor_section["aspect"].items() if isinstance(v, dict)
        }
        if aspect_data:
            config_kwargs["aspect_ratios"] = aspect_data

    return Config(**config_kwargs)


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> Config:
    """Carga config con cache. Llamar al inicio de la app."""
    target = Path(path) if path else _find_config_path()
    if target is None:
        # Sin archivo → defaults + env vars
        cfg = Config()
    else:
        raw = _load_toml(target)
        cfg = _coerce_dict_to_config(raw)

    cfg = _apply_env_overrides(cfg)
    return cfg


def reload_config(path: str | Path | None = None) -> Config:
    """Fuerza recarga (invalida cache)."""
    load_config.cache_clear()
    return load_config(path)


if __name__ == "__main__":
    cfg = load_config()
    print(f"Env: {cfg.app.env}", file=sys.stderr)
    print(f"Mode: {cfg.app.default_mode}", file=sys.stderr)
    print(f"LLM providers: {list(cfg.llm.keys())}", file=sys.stderr)
    print(f"TTS engines: {list(cfg.tts.keys())}", file=sys.stderr)
