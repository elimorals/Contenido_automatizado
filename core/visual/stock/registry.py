"""Registro de providers de stock footage.

Lee config de `shared.config.load_config().stock` y construye instancias
on-demand. `get_all_providers()` respeta `provider_order` y omite los
providers sin API keys configuradas.
"""
from __future__ import annotations

from shared.config import load_config

from .base import StockProvider
from .coverr import CoverrProvider
from .pexels import PexelsProvider
from .pixabay import PixabayProvider

_PROVIDER_CLASSES: dict[str, type[StockProvider]] = {
    "pexels": PexelsProvider,
    "pixabay": PixabayProvider,
    "coverr": CoverrProvider,
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
    """Devuelve todos los providers con keys configuradas, en `provider_order`."""
    cfg = load_config()
    order = cfg.stock.provider_order or available_providers()

    keys_by_provider: dict[str, list[str]] = {
        "pexels": list(cfg.stock.pexels_api_keys),
        "pixabay": list(cfg.stock.pixabay_api_keys),
        "coverr": list(cfg.stock.coverr_api_keys),
    }

    out: list[StockProvider] = []
    for name in order:
        key = name.strip().lower()
        if key not in _PROVIDER_CLASSES:
            continue
        if not keys_by_provider.get(key):
            continue
        out.append(_PROVIDER_CLASSES[key]())
    return out
