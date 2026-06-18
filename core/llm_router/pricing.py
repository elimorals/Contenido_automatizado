"""Tabla de pricing por modelo (USD por token) — portado de corredor-content.

Patrón `priceOf()` de TypeScript adaptado a Python. Cada provider lookea por
`model_name`; si no está en la tabla, usa el fallback genérico (conservador).

Fuentes (actualizar cuando cambien):
- OpenAI: https://openai.com/pricing
- Anthropic: https://www.anthropic.com/pricing
- Gemini: https://ai.google.dev/pricing
- DeepSeek: https://platform.deepseek.com/api-docs/pricing
- OpenRouter: https://openrouter.ai/models (pricing per model)
- Groq: https://groq.com/pricing

Última actualización: 2026-06-15.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """Precio por millón de tokens (input + output)."""

    input_per_mtok: float
    output_per_mtok: float


# Tabla principal. Match exacto por model_name (lowercase normalize).
# Si tu modelo no está, agrégalo aquí o ajusta el fallback abajo.
PRICING: dict[str, Price] = {
    # === OpenAI ===
    "gpt-4o-mini": Price(0.15, 0.60),
    "gpt-4o": Price(2.50, 10.00),
    "gpt-4.1-mini": Price(0.40, 1.60),
    "gpt-4.1-nano": Price(0.10, 0.40),
    "gpt-4.1": Price(2.00, 8.00),
    "gpt-5-nano": Price(0.05, 0.40),
    "gpt-5-mini": Price(0.25, 2.00),
    "gpt-5": Price(1.25, 10.00),
    "o1-mini": Price(3.00, 12.00),
    "o1-preview": Price(15.00, 60.00),

    # === Anthropic Claude (modelos 4.x — 2026 family) ===
    "claude-haiku-4-5": Price(0.80, 4.00),
    "claude-haiku-4-5-20251001": Price(0.80, 4.00),
    "claude-sonnet-4-6": Price(3.00, 15.00),
    "claude-opus-4-7": Price(15.00, 75.00),
    "claude-opus-4-7[1m]": Price(15.00, 75.00),
    # Modelos 3.x legacy
    "claude-3-5-sonnet-20241022": Price(3.00, 15.00),
    "claude-3-5-haiku-20241022": Price(0.80, 4.00),

    # === Google Gemini ===
    "gemini-2.5-flash": Price(0.075, 0.30),
    "gemini-2.5-pro": Price(1.25, 5.00),
    "gemini-2.5-flash-lite": Price(0.025, 0.10),
    "gemini-3.0-pro": Price(2.50, 10.00),

    # === DeepSeek ===
    "deepseek-chat": Price(0.27, 1.10),
    "deepseek-v4-pro": Price(0.27, 1.10),
    "deepseek-v3": Price(0.27, 1.10),
    "deepseek-r1": Price(0.55, 2.19),

    # === OpenRouter (con prefijo openrouter/) ===
    "openrouter/deepseek/deepseek-v4-pro": Price(0.27, 1.10),
    "openrouter/anthropic/claude-sonnet-4-6": Price(3.00, 15.00),
    "openrouter/openai/gpt-4o-mini": Price(0.15, 0.60),
    "openrouter/google/gemini-2.5-flash": Price(0.075, 0.30),

    # === Groq (ultra-fast) ===
    "llama-3.3-70b-versatile": Price(0.59, 0.79),
    "llama-3.1-8b-instant": Price(0.05, 0.08),
    "mixtral-8x7b-32768": Price(0.24, 0.24),

    # === Moonshot Kimi ===
    "moonshot-v1-8k": Price(0.15, 0.15),
    "moonshot-v1-32k": Price(0.30, 0.30),
    "moonshot-v1-128k": Price(0.60, 0.60),

    # === Qwen ===
    "qwen-max": Price(1.60, 4.80),
    "qwen-plus": Price(0.40, 1.20),
    "qwen-turbo": Price(0.05, 0.15),

    # === Xiaomi MiMo ===
    "mimo-7b-instruct": Price(0.10, 0.20),
}


# Fallback conservador: si no encontramos el modelo, asumimos pricing medio
# (DeepSeek-tier). Mejor sobre-estimar costo que sub-reportar.
_FALLBACK = Price(input_per_mtok=1.00, output_per_mtok=3.00)


def price_of(model_name: str) -> Price:
    """Devuelve el Price para `model_name`. Match case-insensitive.

    Si no está en la tabla, devuelve `_FALLBACK` (no lanza error — el costo
    se reportará como aproximado en logs).
    """
    if not model_name:
        return _FALLBACK
    norm = model_name.strip().lower()
    if norm in PRICING:
        return PRICING[norm]
    # Match parcial: a veces vienen sufijos de fecha/version
    for key, price in PRICING.items():
        if norm.startswith(key.lower()):
            return price
    return _FALLBACK


def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Costo en USD = (input_tokens/1M × $in) + (output_tokens/1M × $out)."""
    p = price_of(model_name)
    return (
        (input_tokens / 1_000_000) * p.input_per_mtok
        + (output_tokens / 1_000_000) * p.output_per_mtok
    )


def is_priced(model_name: str) -> bool:
    """True si tenemos pricing exacto (no fallback)."""
    if not model_name:
        return False
    return model_name.strip().lower() in PRICING


# =============================================================================
# Video provider pricing (cost-per-second, no per-token) — ADR-013
# =============================================================================

# Generadores que producen video tarifan por segundo de output, no por tokens.
# Se exponen aquí para que el cost dashboard tenga una fuente única.
# Los valores son DEFAULTS conservadores — override con
# ``cfg.visual.live_avatar.cost_per_video_second_usd`` (ADR-016) etc.
VIDEO_PRICING_USD_PER_SECOND: dict[str, float] = {
    # Higgsfield (ver core/visual/generation/higgsfield_client.py — DoP 5s)
    "higgsfield_dop_turbo": 0.060,   # ~$0.30 / 5s clip
    "higgsfield_dop_preview": 0.160, # ~$0.80 / 5s clip
    # Veo (Google) 3.1 Lite — ~$1.10 / clip 4-8s → mid ~$0.18/s
    "veo_3_1_lite": 0.180,
    # LiveAvatar (ADR-016) — variable según backend:
    #   - RunPod H100 serverless ~$3.39/hr, real ≈ 2× tiempo real → ~$0.0019/s GPU,
    #     pero el provider típicamente cobra ~$0.05/s al usuario final.
    #   - Self-hosted: solo costo GPU eléctrico.
    "live_avatar_remote": 0.050,
    "live_avatar_local": 0.005,      # solo electricidad/amortización
}


def video_cost_per_second(provider_key: str) -> float:
    """Devuelve USD/segundo de video. Fallback 0.0 (no error)."""
    return VIDEO_PRICING_USD_PER_SECOND.get(provider_key.strip().lower(), 0.0)


def calculate_video_cost(provider_key: str, duration_s: float) -> float:
    """Costo en USD = duration_s × USD/s del provider."""
    return max(0.0, duration_s) * video_cost_per_second(provider_key)
