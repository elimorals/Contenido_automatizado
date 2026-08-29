"""Lectura de métricas de posts publicados (ADR-031).

Cierra el lazo: `plan → produce → publish → **medir** → replanificar`. Hasta
aquí el planner generaba ideas desde pilares, audiencias y eventos del
calendario — nunca desde qué había funcionado. Eso no es un sistema que
aprende, es un generador que repite.

La clave que lo hizo posible ya la guardaba ADR-030: `ReelIdea.upload_request_id`
es exactamente lo que pide `GET /api/uploadposts/post-analytics/{request_id}`.

Cada plataforma expone métricas distintas y con nombres distintos (YouTube da
`views`, Instagram da `reach`/`views`/`impressions`, Bluesky ni siquiera tiene
vistas). `PostMetrics` las normaliza a un núcleo comparable y conserva el crudo
en `raw` para no perder lo que sea específico de una plataforma.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Orden de preferencia por métrica: se toma el PRIMER campo presente.
# `reach` va antes que `impressions` porque cuenta personas, no exposiciones —
# comparar reach de una plataforma contra impressions de otra infla la segunda.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "views": ("views", "video_views", "play_count", "reach", "impressions"),
    "likes": ("likes", "like_count", "reactions", "favorites"),
    "comments": ("comments", "comment_count", "replies"),
    "shares": ("shares", "share_count", "reposts", "retweets"),
    "saves": ("saves", "saved", "bookmarks"),
}


def _first_present(metrics: dict[str, Any], names: tuple[str, ...]) -> int:
    for name in names:
        value = metrics.get(name)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return 0


@dataclass(frozen=True)
class PostMetrics:
    """Métricas de UN post en UNA plataforma, normalizadas."""

    platform: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    post_url: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def interactions(self) -> int:
        return self.likes + self.comments + self.shares + self.saves

    @property
    def engagement_rate(self) -> float:
        """Interacciones por vista. 0.0 si no hay vistas medidas todavía.

        Es la única cifra comparable entre plataformas: los valores absolutos
        dicen más del tamaño de la cuenta en cada red que de la pieza.
        """
        return round(self.interactions / self.views, 4) if self.views else 0.0

    @property
    def measured(self) -> bool:
        """¿Hay algo que leer? Un post recién publicado devuelve todo en cero."""
        return self.views > 0 or self.interactions > 0

    @classmethod
    def from_raw(cls, platform: str, metrics: dict[str, Any], post_url: str = "") -> PostMetrics:
        metrics = metrics or {}
        return cls(
            platform=platform,
            views=_first_present(metrics, _FIELD_CANDIDATES["views"]),
            likes=_first_present(metrics, _FIELD_CANDIDATES["likes"]),
            comments=_first_present(metrics, _FIELD_CANDIDATES["comments"]),
            shares=_first_present(metrics, _FIELD_CANDIDATES["shares"]),
            saves=_first_present(metrics, _FIELD_CANDIDATES["saves"]),
            post_url=post_url,
            raw=dict(metrics),
        )


def parse_post_analytics(payload: dict[str, Any]) -> dict[str, PostMetrics]:
    """`{platform: PostMetrics}` desde la respuesta de post-analytics.

    Defensivo a propósito: una plataforma que falló su lectura (`success:
    false`) se omite en vez de contarse como cero, porque un cero medido y un
    cero por error de lectura significan cosas opuestas para el planner.
    """
    out: dict[str, PostMetrics] = {}
    platforms = payload.get("platforms")
    if not isinstance(platforms, dict):
        return out

    for platform, info in platforms.items():
        if not isinstance(info, dict):
            continue
        if info.get("success") is False:
            logger.debug(
                f"[analytics] {platform} sin métricas: "
                f"{info.get('post_metrics_error') or 'success=false'}"
            )
            continue
        metrics = info.get("post_metrics")
        if not isinstance(metrics, dict):
            continue
        out[str(platform).lower()] = PostMetrics.from_raw(
            str(platform).lower(), metrics, str(info.get("post_url") or "")
        )
    return out


__all__ = ["PostMetrics", "parse_post_analytics"]
