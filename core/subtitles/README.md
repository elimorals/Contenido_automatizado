# core/subtitles

Subtítulos con dos estilos: word-burst (default, viral) + SRT (clásico, compatible MPT).

## Módulos (Fase 4)

### `word_burst.py` — portado de reels-af
Layer 1: UNA palabra a la vez, 170px, bottom-center, sample-accurate timing.
Layer 2: Accents editoriales UPPERCASE en posición opuesta.

Implementado con **pysubs2 → ASS file → libass burn en ffmpeg**.

### `srt.py` — portado de MPT
SRT clásico broadcasting, líneas multi-word, posición configurable.

### `whisper.py` — portado de MPT (fallback)
faster-whisper para subtítulos desde audio personalizado (cuando no hay sample-accurate word_timings).

### `accents.py` — portado de reels-af
6 patrones canónicos de overlay editorial:
1. `number` — "$47,000", "85%"
2. `named_entity` — "DR. CHEN, STANFORD"
3. `jargon_translation` — "ENTANGLEMENT = SPOOKY LINK"
4. `hook_title_card` — ONLY en beat.role==hook
5. `reaction` — "WAIT WHAT", "BIG IF TRUE"
6. `list_marker` — "STEP 2 OF 3"

Bias a **None**: sobreuso > falta de accents.

## Por qué libass > drawtext

- **Métricas de fuente reales** (no alignment bugs)
- **Estándar industrial** (VLC, mpv, fansubs)
- **Extensible**: fades, glow, animations
- **Mejor rendering CJK** (importante para mercado asiático)

## Compatibilidad MPT

Usuarios que vienen de MPT pueden seguir usando SRT con `subtitle_style=srt`. La integración mantiene compatibilidad bidireccional.
