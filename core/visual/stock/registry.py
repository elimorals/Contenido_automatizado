"""Registro de providers de stock footage.

Lee config de `shared.config.load_config().stock` y construye instancias
on-demand. `get_all_providers()` respeta `provider_order` e incluye:
- providers con API key (pexels/pixabay/coverr/unsplash) si hay key configurada,
- providers libres keyless (archive_org/wikimedia/nasa) si su flag `*_enabled` es true.
"""
from __future__ import annotations

from shared.config import load_config

from .archive_org import ArchiveOrgProvider
from .base import StockProvider
from .coverr import CoverrProvider
from .nasa import NasaProvider
from .pexels import PexelsProvider
from .pixabay import PixabayProvider
from .unsplash import UnsplashProvider
from .wikimedia import WikimediaProvider

_PROVIDER_CLASSES: dict[str, type[StockProvider]] = {
    "pexels": PexelsProvider,
    "pixabay": PixabayProvider,
    "coverr": CoverrProvider,
    "archive_org": ArchiveOrgProvider,
    "wikimedia": WikimediaProvider,
    "nasa": NasaProvider,
    "unsplash": UnsplashProvider,
}

# Providers libres keyless → se activan por flag `stock.<flag>`, no por API key.
_KEYLESS_ENABLED_FLAG: dict[str, str] = {
    "archive_org": "archive_enabled",
    "wikimedia": "wikimedia_enabled",
    "nasa": "nasa_enabled",
}


def available_providers() -> list[str]:
    """Lista los nombres de provider conocidos."""
    return list(_PROVIDER_CLASSES.keys())


def get_provider(name: str) -> StockProvider:
    """Construye una instancia del provider pedido.

    Raises:
        KeyError: si `name` no es un provider conocido.
    """
    key = name.strip().lower()
    if key not in _PROVIDER_CLASSES:
        raise KeyError(
            f"Provider desconocido: '{name}'. Disponibles: {available_providers()}"
        )
    return _PROVIDER_CLASSES[key]()


def get_all_providers() -> list[StockProvider]:
    """Providers activos en orden: `provider_order` primero, libres al final."""
    cfg = load_config()
    order = list(cfg.stock.provider_order or available_providers())
    # Asegura que todo provider conocido sea considerado (los libres no suelen
    # estar en provider_order por default → se anexan tras los de pago).
    for name in available_providers():
        if name not in order:
            order.append(name)

    keys_by_provider: dict[str, list[str]] = {
        "pexels": list(cfg.stock.pexels_api_keys),
        "pixabay": list(cfg.stock.pixabay_api_keys),
        "coverr": list(cfg.stock.coverr_api_keys),
        "unsplash": list(cfg.stock.unsplash_api_keys),
    }

    out: list[StockProvider] = []
    seen: set[str] = set()
    for name in order:
        key = name.strip().lower()
        if key in seen or key not in _PROVIDER_CLASSES:
            continue
        seen.add(key)
        if key in _KEYLESS_ENABLED_FLAG:
            if getattr(cfg.stock, _KEYLESS_ENABLED_FLAG[key], False):
                out.append(_PROVIDER_CLASSES[key]())
        elif keys_by_provider.get(key):
            out.append(_PROVIDER_CLASSES[key]())
    return out
