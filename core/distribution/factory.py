"""Factory para servicios de distribución.

Devuelve None si la publicación está deshabilitada (api_key vacía). El caller
debe interpretar `None` como "no publicar; seguir adelante".
"""
from __future__ import annotations

from loguru import logger

from core.distribution.base import DistributionService
from core.distribution.upload_post import UploadPostService
from shared.config import Config


def get_distribution_service(config: Config) -> DistributionService | None:
    """Devuelve un servicio de distribución listo o None si está deshabilitado.

    Reglas:
    - Si `config.upload_post.api_key` está vacía -> None (publicación off).
    - Caso contrario -> instancia `UploadPostService`.
    """
    cfg = config.upload_post
    api_key = (cfg.api_key or "").strip()
    if not api_key:
        logger.info("Distribution disabled: upload_post.api_key vacía.")
        return None

    return UploadPostService(api_key=api_key)
