"""Interfaz abstracta para servicios de distribución (cross-posting)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.distribution.analytics import PostMetrics
from core.distribution.schemas import UploadResult, UploadStatus


class DistributionService(ABC):
    """Servicio de publicación a redes sociales.

    Implementaciones concretas: `UploadPostService` (TikTok+Instagram).
    Roadmap: YouTubeShortsService, LinkedInService.
    """

    @abstractmethod
    async def upload_video(
        self,
        video_path: Path,
        platforms: list[str],
        caption: str = "",
        hashtags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> UploadResult:
        """Sube un video a una o varias plataformas.

        Args:
            video_path: Path al archivo de video (debe existir).
            platforms: Lista de plataformas, ej. ["tiktok", "instagram"].
            caption: Caption/título. Cada plataforma trunca a su propio límite.
            hashtags: Lista de hashtags sin el `#`. Se agregan al final del caption.
            metadata: Metadata adicional específica del proveedor (privacy, etc).

        Returns:
            UploadResult con success, request_id, urls y errores por plataforma.
        """
        ...

    @abstractmethod
    async def check_status(self, request_id: str) -> UploadStatus:
        """Consulta el estado de un upload async."""
        ...

    async def get_post_analytics(self, request_id: str) -> dict[str, PostMetrics]:
        """Métricas del post publicado, por plataforma. `{}` si no hay o falla.

        NO es abstracto: un proveedor puede publicar sin ofrecer analytics, y
        obligarlo a implementar un stub sería ruido. El default devuelve vacío,
        que el planner interpreta como "sin datos" — nunca como "cero vistas".
        """
        return {}
