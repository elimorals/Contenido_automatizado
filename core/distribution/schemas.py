"""Modelos locales para distribución social.

Estos schemas son locales al subsistema de distribution. Para persistir el
resultado dentro de `TaskInfo.cross_post_results` (que es `list[dict]`), llamar
`UploadResult.model_dump()` y appendear el dict resultante.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Plataformas soportadas hoy. Roadmap: youtube_shorts, linkedin.
SUPPORTED_PLATFORMS: tuple[str, ...] = ("tiktok", "instagram")

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
