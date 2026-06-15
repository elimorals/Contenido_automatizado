# core/editor

Editor final ffmpeg, sin MoviePy. Single-pass de reels-af + multi-aspect/multi-encoder de MPT.

## Módulos (Fase 3-4)

### `ffmpeg_stitch.py` — portado de reels-af (`render/stitch.py`)
Single-pass ffmpeg que combina en UNA invocación:
- **Concat filter** — beat clips end-to-end (sample-accurate)
- **libass burn** — global ASS file (word-burst + accents)
- **AAC mux** — full TTS WAV (primes UNA sola vez)
- **BGM mix** — música de fondo (volumen ajustable)
- **Codec** — libx264 default, fallback a hardware

### `aspect.py` — portado de MPT
Lógica de canvas para 9:16, 16:9, 1:1:
- Resize con preserve aspect (no stretch)
- Center crop / letterbox según `padding_mode`
- Coordenadas de safe-zone por aspect

### `encoders.py` — portado de MPT
Detección automática y fallback de hardware encoder:
```
libx264 → h264_nvenc (NVIDIA) → h264_amf (AMD)
       → h264_qsv (Intel) → h264_mf (Windows)
       → h264_videotoolbox (macOS)
```

### `bgm.py` — nuevo (mezcla MPT + ffmpeg directo)
- Biblioteca de songs en `resource/songs/` (heredado de MPT)
- Mezcla en el single-pass ffmpeg (no en step separado)
- Volume sidechain con ducking opcional

## Por qué ffmpeg directo > MoviePy

1. **Single-pass** = sin drift sub-frame en boundaries
2. **Velocidad** — 3-5× más rápido en reels de 25s
3. **Hardware encoders** — moviepy no soporta nvenc/qsv/videotoolbox bien
4. **libass burn** — moviepy usa drawtext (alignment bugs conocidos)
5. **Menor footprint** — sin numpy/Pillow en hot path

## Trade-off

MoviePy es más legible. Aquí lo cambiamos por performance + correctness.
