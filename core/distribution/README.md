# core/distribution

Publicación automática a redes sociales (portado de MPT).

## Plataformas (Fase 5)

| Plataforma | Aspect | Duración | Estado |
|---|---|---|---|
| TikTok | 9:16 | 9s-10min | Vía Upload-Post |
| Instagram Reels | 9:16 | 3s-90s | Vía Upload-Post |
| Instagram Feed | 1:1 | hasta 60s | Vía Upload-Post |
| YouTube Shorts | 9:16 | hasta 60s | Roadmap (Fase 6+) |
| LinkedIn | 16:9 | hasta 10min | Roadmap |

## Upload-Post integration

```python
from core.distribution import UploadPostService

service = UploadPostService(api_key=cfg.upload_post.api_key)
result = await service.upload_video(
    video_path="./output/task-xyz/reel.mp4",
    platforms=["tiktok", "instagram"],
    caption="...",
    hashtags=["#ai", "#reel"],
)
# result.success, result.urls, result.errors
```

## Trigger automático

Si `params.auto_upload=True` y `cfg.upload_post.auto_upload=True`, el pipeline publica al terminar `stitch`. Resultado en `TaskInfo.cross_post_results`.
