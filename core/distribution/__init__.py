"""Distribución a redes sociales (Upload-Post).

Exports:
    DistributionService    - ABC para implementaciones
    UploadPostService      - Cliente TikTok+Instagram vía Upload-Post
    UploadResult           - Modelo de resultado (model_dump() -> dict en TaskInfo)
    UploadStatus           - Modelo de estado para check_status()
    SUPPORTED_PLATFORMS    - Tupla de plataformas soportadas
    get_distribution_service(cfg) -> DistributionService | None
"""
from core.distribution.base import DistributionService
from core.distribution.factory import get_distribution_service
from core.distribution.schemas import (
    SUPPORTED_PLATFORMS,
    UploadResult,
    UploadStatus,
)
from core.distribution.upload_post import UploadPostService

__all__ = [
    "SUPPORTED_PLATFORMS",
    "DistributionService",
    "UploadPostService",
    "UploadResult",
    "UploadStatus",
    "get_distribution_service",
]
