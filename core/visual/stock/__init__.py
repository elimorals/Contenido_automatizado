"""Stock footage providers (Pexels, Pixabay, Coverr).

Port async de `MoneyPrinterTurbo/app/services/material.py`:
- httpx async en lugar de requests sync.
- Validación de cache vía ffprobe (no MoviePy).
- Rotación thread-safe / async-safe de API keys.
"""
from __future__ import annotations

from .base import (
    AllKeysExhaustedError,
    APIKeyRotator,
    NoAPIKeyError,
    StockProvider,
)
from .cache import ProbeResult, fetch_and_cache, probe_video
from .coverr import CoverrProvider
from .pexels import PexelsProvider
from .pixabay import PixabayProvider
from .registry import available_providers, get_all_providers, get_provider

__all__ = [
    "AllKeysExhaustedError",
    "APIKeyRotator",
    "CoverrProvider",
    "NoAPIKeyError",
    "PexelsProvider",
    "PixabayProvider",
    "ProbeResult",
    "StockProvider",
    "available_providers",
    "fetch_and_cache",
    "get_all_providers",
    "get_provider",
    "probe_video",
]
