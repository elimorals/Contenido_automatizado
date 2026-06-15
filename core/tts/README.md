# core/tts

Sistema TTS unificado con sample-accurate timing aplicado a TODOS los engines.

## Engines soportados (Fase 1)

| Engine | Costo | Tags inline | Calidad | Default para |
|---|---|---|---|---|
| `edge` | Gratis | No | Buena | Express, CJK |
| `gemini_flash` | ~$0.003/min | **Sí** | Excelente | Premium |
| `azure` | ~$16/M chars | No | Excelente | Voces premium |
| `mimo` | ~$0.002/min | No | Muy buena | Chino |
| `siliconflow` | ~$0.001/min | No | Buena | CosyVoice2 |
| `silent` | Gratis | N/A | N/A | No-voice |

## El upgrade clave: sample-accurate timing universal

`timing.py` aplica el método de reels-af (`render/tts.py`) a CUALQUIER engine:

1. **Split por sentence** (mantiene tags inline pegados al texto)
2. **Síntesis paralela** por frase
3. **ffprobe measure** de cada WAV (boundaries sample-accurate)
4. **atempo=1.35** (preserve pitch, target 18-25s reels)
5. **Native WAV concat** (sin drift)
6. **Word distribution** por syllable count sobre measured span

Esto **elimina el drift acumulativo del SubMaker de Edge TTS** que tenía MPT y permite word-burst karaoke con cualquier voz.

## Interfaz

```python
from core.tts import TTSEngine, get_engine

engine = get_engine("edge")  # auto-config
audio = await engine.synthesize(
    text="Hello [emphasis] world",
    voice="en-US-AvaNeural-Female",
    out_dir=Path("./output/task-xyz"),
    target_duration_s=25.0,  # atempo se calcula automático
)
# audio.path, audio.duration_s, audio.word_timings (sample-accurate)
```
