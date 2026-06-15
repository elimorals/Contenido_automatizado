# Troubleshooting

Errores comunes ordenados por **fase del pipeline donde aparecen**.

## 🔍 Diagnóstico rápido

```bash
# 1. Verificar config
uv run contenido config-check

# 2. Verificar API levanta
curl http://localhost:8000/health

# 3. Logs de la API
tail -f /tmp/contenido-api.log

# 4. Test individual de cada fase
curl -X POST http://localhost:8000/scripts -H 'Content-Type: application/json' \
    -d '{"subject":"test","mode":"express"}'

# 5. Test TTS
curl -X POST http://localhost:8000/audio -H 'Content-Type: application/json' \
    -d '{"subject":"hello","voice_name":"en-US-AvaNeural-Female"}'
```

---

## 🚨 Errores de setup

### `ModuleNotFoundError: No module named 'shared'`

**Causa**: estás ejecutando desde fuera del directorio del proyecto, o el venv no está activado.

**Fix**:
```bash
cd /ruta/a/contenido
source .venv/bin/activate    # o uv run <comando>
```

### `ffmpeg: command not found`

**Causa**: ffmpeg no está instalado en el sistema.

**Fix**:
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows
winget install ffmpeg
```

### `OSError: [Errno 8] Exec format error`

**Causa**: ffmpeg binario no compatible con tu arquitectura (típico en Apple Silicon con binarios x86).

**Fix**:
```bash
# Reinstalar con arquitectura nativa
brew uninstall ffmpeg && brew install ffmpeg
```

### `ImportError: cannot import name 'tomllib'`

**Causa**: Python < 3.11.

**Fix**: actualizar a Python 3.11+:
```bash
brew install python@3.11
# o pyenv install 3.11.10
```

---

## 🔑 Errores de API keys

### `LLMProviderError: No API key configured for provider 'openrouter'`

**Causa**: `OPENROUTER_API_KEY` no está seteada o tiene typo.

**Fix**:
1. Verifica que `.env` existe en la raíz del proyecto
2. Verifica formato: `OPENROUTER_API_KEY=sk-or-v1-...` (sin espacios, sin comillas)
3. Recarga venv: `source .venv/bin/activate`

```bash
# Verificar que está cargada
uv run python -c "import os; print(os.getenv('OPENROUTER_API_KEY', 'MISSING'))"
```

### `401 Unauthorized` desde Pexels/Pixabay/OpenAI

**Causa**: key incorrecta o expirada.

**Fix**:
1. Regenera la key desde el dashboard del provider
2. Si tienes varias en `PEXELS_API_KEYS`, una puede estar muerta — quita la mala
3. Verifica saldo (OpenAI/Anthropic requieren balance positivo)

### `429 Too Many Requests`

**Causa**: rate limit excedido.

**Fix**:
- Agrega keys adicionales para rotación: `PEXELS_API_KEYS=key1,key2,key3`
- Reduce concurrencia: `MAX_CONCURRENT_TASKS=2`
- Espera unos minutos

### `Provider 'X' not available`

**Causa**: provider configurado pero módulo no implementado.

**Fix**: ver [API_KEYS.md](./API_KEYS.md) — usa uno de los providers soportados (openrouter, openai, anthropic, gemini, qwen, azure, deepseek, groq, moonshot, ollama).

---

## 📝 Errores de la fase Script/Narrative

### `ValueError: Loop-back validator falló`

**Causa**: el LLM generó un ScriptDraft donde el `payoff_line` no incluye keyword del hook. El validator de `shared/schemas.py:ScriptDraft` lo bloquea.

**Síntoma**:
```
ValueError: Loop-back validator falló: hook keyword 'placebo' no aparece en
payoff_line ('And that's the answer...'). Curiosity loop roto.
```

**Fix**:
- Es esperado ~10% del tiempo con modelos pequeños. El pipeline NO reintenta automáticamente todavía.
- Workaround: re-ejecutar la task (`uv run contenido topic "X"` de nuevo).
- Fix permanente: agregar retry en `core/narrative/compose.py` (TODO).

### `Pydantic ValidationError: text must have 2-6 words`

**Causa**: el modelo generó un accent overlay con menos de 2 o más de 6 palabras.

**Fix**:
- El validator lo descarta automáticamente y el beat queda sin accent — no es bloqueante.
- Si quieres ver por qué: `LOG_LEVEL=DEBUG uv run contenido topic "X"`

### Hunters devuelven candidates idénticos

**Causa**: temperature demasiado baja o LLM con poca diversidad.

**Fix**:
- Aumentar temperature: editar `core/narrative/hunters.py` (default 1.1, prueba 1.3)
- Usar otro modelo: `REEL_AF_MODEL=openrouter/anthropic/claude-opus-4-7`

---

## 🔊 Errores de TTS

### `EdgeTTSError: No audio was received`

**Causa**: Edge TTS endpoint público está caído o tu IP bloqueada.

**Fix**:
- Espera 5-10 min y reintenta
- Aumenta timeout: `EDGE_TTS_TIMEOUT=60` en `.env`
- Cambia engine: `--voice-name "gemini:Kore-Female"` (requiere `OPENROUTER_API_KEY`)

### Sample-accurate timing devuelve word_timings vacíos

**Causa**: ffprobe falla midiendo el WAV.

**Síntoma**:
```
AudioArtifact(word_timings=[])
```

**Fix**:
1. Verifica ffprobe instalado: `ffprobe -version`
2. Verifica el WAV temporal:
   ```bash
   ls -la /tmp/contenido-*/
   ffprobe -v error -show_entries stream=duration /tmp/contenido-*/sentence_0.wav
   ```
3. Si el WAV está corrupto: el engine TTS falló silenciosamente. Cambia engine.

### `atempo factor out of range`

**Causa**: `target_duration_s` muy distinto a la duración natural del audio (típicamente > 2× o < 0.5×).

**Fix**:
- ffmpeg atempo solo acepta 0.5-2.0. Para factores fuera de rango, el código en `core/tts/timing.py` cascadea atempo (2.0 × 1.5 = 3.0). Si ves este error, hay un bug en el cascading — abre issue.

---

## 🎬 Errores de Visual / Stock / Generación

### `No stock results for query 'X'`

**Causa**: el `visual_anchor` es demasiado específico para Pexels.

**Síntoma**: el beat se queda con placeholder (color sólido).

**Fix**:
- El fallback automático debería usar IA (Gemini Image). Verifica:
  ```bash
  grep "fallback" /tmp/contenido-api.log
  ```
- Si Gemini Image también falla: verifica `OPENROUTER_API_KEY`.

### `Veo i2v timeout (300s)`

**Causa**: Veo es lento (~30-60s por clip). Con 5 beats × 60s = 300s mínimo.

**Fix**:
- Aumenta timeout: `AGENTFIELD_LLM_CALL_TIMEOUT=600` en `.env`
- O desactiva Veo: `REEL_AF_USE_VEO=false` (usa ken-burns gratis)

### Gemini Image devuelve imágenes con texto

**Causa**: el prompt no incluyó "no text" o el modelo lo ignoró.

**Fix**:
- Verifica que los style blocks en `core/visual/generation/gemini_image.py` incluyen "no text"
- Reintentar la task (el modelo es estocástico)

### Ken-burns produce video estático

**Causa**: el comando ffmpeg `zoompan` está mal formado o `zoom_factor` es muy bajo.

**Fix**:
- En `config.toml`: subir `[visual.ken_burns] zoom_factor = 1.25` (default 1.15)
- Verificar el comando real: agregar `LOG_LEVEL=DEBUG`

---

## 🎞 Errores del editor ffmpeg

### `ffmpeg error: Invalid argument`

**Causa más común**: paths con espacios o caracteres especiales.

**Fix**:
- Los outputs van a `./output/<task_id>/` (sin espacios). Si moviste outputs manualmente a path con espacios, vuelve atrás.
- Si tu `bgm_file` tiene espacios: renombra el archivo.

### `Filter complex parse failed`

**Causa**: el builder generó filter_complex malformado.

**Fix**:
- Abre issue con el comando completo (logs en `/tmp/contenido-api.log`).
- Workaround temporal: desactivar BGM (`bgm_type=none`).

### Hardware encoder fallback no funciona

**Causa**: ffmpeg detecta el encoder pero falla en runtime.

**Síntoma**:
```
[h264_nvenc] No NVENC capable devices found
```

**Fix**:
- En `config.toml`: forzar `libx264`:
  ```toml
  [editor]
  hw_encoder_fallback = ["libx264"]
  ```

### Output video sin audio

**Causa**: el filter_complex no mapeó correctamente la pista de audio.

**Fix**:
- Verifica que `audio_path` existe antes del stitch:
  ```bash
  ls -la ./output/<task_id>/audio.*
  ```
- Re-ejecuta la task — bug intermitente conocido.

---

## 📋 Errores de Subtítulos

### Word-burst .ass renderiza vacío

**Causa**: libass no encuentra la fuente Montserrat Bold.

**Fix**:
1. Verifica fonts:
   ```bash
   fc-list | grep -i montserrat
   ```
2. Si no aparece, descarga e instala:
   ```bash
   # macOS
   curl -L https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf \
       -o ~/Library/Fonts/Montserrat-Bold.ttf
   ```
3. O fallback automático: DejaVu Sans (debería estar siempre instalado).

### Subtítulos CJK aparecen como □□□

**Causa**: fuente CJK no instalada.

**Fix**:
```bash
# macOS
brew install --cask font-noto-sans-cjk

# Ubuntu
sudo apt install fonts-noto-cjk

# Verifica el config
# config.toml
[subtitles]
cjk_font = "Noto Sans CJK SC"   # o el nombre exacto de tu sistema
```

### Whisper falla al cargar modelo

**Causa**: primera vez requiere descargar ~2 GB.

**Fix**:
- Espera (la descarga puede tomar varios minutos)
- Cambia a modelo más chico: `WHISPER_MODEL_SIZE=base` (~150 MB)
- Si la descarga falla por red: descarga manual de https://huggingface.co/Systran

---

## 🚦 Errores del API / Worker

### `429 Queue full, retry later`

**Causa**: `max_queued_tasks` alcanzado.

**Fix**:
- Espera a que workers procesen
- Aumenta capacidad: `MAX_QUEUED_TASKS=200` en `.env`
- Agrega workers: en docker-compose `replicas: 5`

### Worker se queda colgado en `PROCESSING`

**Causa**: el pipeline crasheó pero el worker no marcó FAILED (bug).

**Fix manual**:
```bash
# Borrar la task
curl -X DELETE http://localhost:8000/tasks/<task_id>

# O en Redis directo
redis-cli DEL "contenido:task:<task_id>"
```

### `Cannot connect to Redis`

**Causa**: Redis no está corriendo.

**Fix**:
```bash
# Levantar Redis
docker run -d -p 6379:6379 redis:7-alpine

# O en macOS
brew services start redis

# O desactivar Redis (usar memory)
# .env
ENABLE_REDIS=false
```

### API tarda mucho en arrancar

**Causa**: imports pesados (Whisper, modelos LLM).

**Fix**: imports lazy — primer request será lento, los siguientes rápidos. Aceptable.

---

## 🌐 Errores de WebUI

### "No se pudo conectar con la API en http://localhost:8000"

**Causa**: la API no está corriendo.

**Fix**:
```bash
# Verifica que la API responda
curl http://localhost:8000/health

# Si no responde, arráncala
uv run uvicorn apps.api.main:app --port 8000
```

### Polling se queda eterno en "Esperando..."

**Causa**: el worker no está procesando o la task se perdió.

**Fix**:
1. Verifica el worker:
   ```bash
   ps aux | grep "apps.api.worker"
   ```
2. Si no hay worker: ejecútalo manualmente:
   ```bash
   uv run python -m apps.api.worker
   ```
3. O cambia a modo sin queue (CLI directo): `uv run contenido topic "X"`

---

## 🐳 Errores de Docker

### `docker compose up` falla con permission denied

**Fix**:
```bash
sudo chown -R $USER:$USER ./output ./storage
```

### `control-plane` container restart loop

**Causa**: AgentField control-plane no es público todavía.

**Fix temporal**: removerlo del compose:
```yaml
# docker-compose.yml
# Comentar el servicio control-plane
# El runtime adapter usará core.llm_router directo como fallback
```

### Out of memory al construir imagen

**Causa**: ffmpeg + Whisper + dependencies pesadas.

**Fix**:
```bash
# Aumentar memoria de Docker Desktop (Settings → Resources → 8GB)
# O usar multi-stage build (ya configurado en Dockerfile)
```

---

## 💸 Costos inesperados

### Factura de OpenRouter/OpenAI más alta de lo esperado

**Causa común**: tests E2E con mode=premium ejecutados muchas veces.

**Fix**:
- Activar cost tracking en `config.toml`:
  ```toml
  [ui]
  show_cost_tracker = true
  ```
- Monitorear desde dashboards de cada provider
- Set hard limit en OpenRouter: https://openrouter.ai/settings/credits

### Veo más caro de lo esperado

**Causa**: cada reel premium con Veo cuesta ~$1.10 (5 clips × $0.20-0.30 c/u).

**Fix**: por default `REEL_AF_USE_VEO=false`. Solo activar para casos premium reales.

---

## 🆘 Debug avanzado

### Ver el comando ffmpeg generado

```python
# Inyecta logging en core/editor/ffmpeg_stitch.py
import shlex
logger.debug("ffmpeg command: " + shlex.join(cmd))
```

### Inspeccionar TaskInfo completo

```bash
curl http://localhost:8000/tasks/<task_id> | jq .
```

### Ver timings de cada phase

```bash
curl http://localhost:8000/timings/<task_id> | jq .
```

### Logs detallados del pipeline

```bash
LOG_LEVEL=DEBUG uv run contenido subject "test"
```

### Estado de una task en Redis

```bash
redis-cli GET "contenido:task:<task_id>"
```

## ¿Tu error no está aquí?

1. Busca en los logs: `/tmp/contenido-api.log`, `/tmp/contenido-webui.log`
2. Activa DEBUG: `LOG_LEVEL=DEBUG`
3. Abre issue con:
   - Comando ejecutado
   - Output completo del error
   - `uv run contenido config-check`
   - OS + Python version
