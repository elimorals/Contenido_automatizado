# core/visual

Pipeline visual híbrido: stock footage + generación IA + selector inteligente.

## Submódulos (Fase 3)

### `stock/` — portado de MPT
- `pexels.py` — orientation portrait/landscape, 20 vids/page, rotación de keys
- `pixabay.py` — 50 vids/page, flexible width matching
- `coverr.py` — JWT signed URLs, principalmente 16:9
- `cache.py` — local hash MD5 + validación con ffprobe (no MoviePy)

### `generation/` — portado de reels-af
- `gemini_image.py` — first frames per-beat, 720×1280 + center-crop a 9:16
- `veo.py` — i2v opcional (Veo 3.1 Lite), buckets 4/6/8s
- `ken_burns.py` — fallback gratuito con `ffmpeg zoompan`

### `selector.py` — el cerebro
Decide stock vs IA vs mixto por beat según:
- `mode` (express/premium)
- `beat.role` (hook → IA en premium; payoff → stock en express)
- `essence.evidence` (si menciona persona/año/número → IA específica)
- Disponibilidad (stock falla → fallback a IA)

## Heurísticas del selector

```python
# pseudo-código
def select(beat, essence, mode, config):
    if mode == EXPRESS:
        return stock_provider()
    if beat.role == HOOK and mode == PREMIUM:
        return gemini_image()
    if any(num_or_name in beat.text for evidence in essence.evidence):
        return gemini_image()  # específico
    if beat.role == PAYOFF:
        return stock_provider()  # callback visual genérico OK
    return mixed()  # IA primer plano + stock cortes
```

## Trade-offs

| Estrategia | Costo/reel | Tiempo | Especificidad |
|---|---|---|---|
| Solo stock | $0 | 30s descarga | Baja (genérico) |
| Solo IA (Gemini) | ~$0.05 | 25s gen | Alta |
| Solo IA (Gemini + Veo) | ~$1.20 | 40s gen | Muy alta (motion) |
| Híbrido | ~$0.02 | 28s | Media-Alta |
