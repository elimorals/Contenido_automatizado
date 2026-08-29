"""Modelos locales para distribución social.

Estos schemas son locales al subsistema de distribution. Para persistir el
resultado dentro de `TaskInfo.cross_post_results` (que es `list[dict]`), llamar
`UploadResult.model_dump()` y appendear el dict resultante.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from shared.schemas import DistributionPlatform

# Identificadores que acepta el parámetro `platform[]` de Upload-Post.
# Verificado contra https://docs.upload-post.com/api/upload-video/ (2026-08-29).
# OJO: son los nombres del PROVEEDOR, no los de la capa editorial —
# `youtube_shorts` es nuestro, `youtube` es el suyo. Ver `PLATFORM_TO_PROVIDER`.
SUPPORTED_PLATFORMS: tuple[str, ...] = (
    "tiktok",
    "instagram",
    "linkedin",
    "youtube",
    "facebook",
    "twitter",
    "threads",
    "pinterest",
)

# Traducción capa editorial → proveedor. La capa editorial distingue formatos
# que el proveedor no (shorts vs long son ambos "youtube"), porque a nosotros sí
# nos cambian el aspect, la duración y el largo del caption.
PLATFORM_TO_PROVIDER: dict[DistributionPlatform, str] = {
    DistributionPlatform.TIKTOK: "tiktok",
    DistributionPlatform.INSTAGRAM_REELS: "instagram",
    DistributionPlatform.YOUTUBE_SHORTS: "youtube",
    DistributionPlatform.YOUTUBE_LONG: "youtube",
    DistributionPlatform.FACEBOOK_REELS: "facebook",
    DistributionPlatform.LINKEDIN_VIDEO: "linkedin",
}


def to_provider(platform: DistributionPlatform | str) -> str:
    """Nombre de proveedor para una plataforma editorial.

    Acepta ya el nombre del proveedor (idempotente) para que los callers que
    trabajan con strings sueltos no tengan que saber de qué lado están.
    """
    if isinstance(platform, DistributionPlatform):
        return PLATFORM_TO_PROVIDER[platform]
    raw = str(platform).strip().lower()
    try:
        return PLATFORM_TO_PROVIDER[DistributionPlatform(raw)]
    except ValueError:
        return raw

UploadState = Literal["pending", "uploading", "complete", "failed"]


class UploadResult(BaseModel):
    """Resultado de un upload (posiblemente multi-plataforma)."""

    success: bool
    request_id: str | None = None
    urls: dict[str, str] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    platforms: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict, description="Respuesta cruda del proveedor")


class UploadStatus(BaseModel):
    """Estado actual de un upload async."""

    request_id: str
    state: UploadState
    urls: dict[str, str] = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)
