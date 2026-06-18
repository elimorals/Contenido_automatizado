# LiveAvatar — talking-head con lip-sync (ADR-016)

LiveAvatar es el quinto backend visual de `contenido`, junto a Gemini Image,
Veo, Higgsfield (DoP/Soul/Effects) y ComfyUI. Se especializa en **avatares
parlantes audio-driven**: input image + WAV → MP4 con boca sincronizada.

Es **opt-in**, NO se activa por default. Se diseñó específicamente para el
intent editorial `LongFormIntent.TALKING_HEAD` (explainer videos, cursos,
news anchors, lectures). Para todo lo demás el pipeline existente sigue
siendo la mejor opción.

---

## ¿Cuándo usar LiveAvatar?

**SÍ**:

- Cursos online con presentador virtual
- Explainer videos (long-form, 5–30 min)
- News anchors / reportes corporativos
- Lectures / tutoriales pedagógicos
- Onboarding videos repetibles

**NO**:

- Reels cortos de montaje (25s, stock + IA + i2v ya cubre)
- Cinematografía abstracta / paisajes
- Brand identity con LoRA custom (training code de LiveAvatar aún no liberado)
- Si tu narración no incluye un presentador on-screen

---

## Arquitectura

```
┌────────────────────────────────────────────────────────────────┐
│  Director long_form (intent=TALKING_HEAD)                       │
│                                                                  │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│   │ TTS (Edge)   │──▶│ LiveAvatar   │──▶│ ffmpeg concat    │   │
│   │ shot WAV     │   │ portrait+wav │   │ single-pass     │   │
│   │ sample-acc.  │   │  → mp4       │   │ ADR-001         │   │
│   └──────────────┘   └──────┬───────┘   └──────────────────┘   │
│                              │                                   │
│                   ┌──────────┴──────────┐                       │
│                   ▼                     ▼                       │
│           LocalCliBackend       RemoteHttpBackend               │
│           subprocess torchrun   POST multipart                  │
└────────────────────────────────────────────────────────────────┘
```

**Contratos**:

- `LiveAvatarBackend` (ABC) en `core/visual/generation/live_avatar_client.py`
- `LiveAvatarGenerator(VisualGenerator)` en `core/visual/generation/live_avatar.py`
- Short-circuit en `core/visual/generation/orchestrator.py:_should_use_live_avatar`

---

## Setup

### Opción A — `remote_http` (recomendado para producción)

Despliega un worker HTTP que envuelve LiveAvatar. Ejemplos:

- [RunPod Serverless](https://www.runpod.io/serverless) con GPU H100/A100 on-demand
- [Lambda Labs](https://lambdalabs.com/) instance long-lived con FastAPI wrapper
- Self-host en tu propio servidor con 1×H100 + Nginx + auth

El worker debe exponer:

```
POST {remote_endpoint}
Authorization: Bearer {remote_api_key}
Content-Type: multipart/form-data

fields:
  image: <binary jpg/png>
  audio: <binary wav>
  prompt: str
  seed: int
  num_clip: int
  size: str           # "704*384"
  sample_steps: int   # 4
  sample_guide_scale: float
  sample_solver: str  # "euler"
  infer_frames: int   # 48
  fp8: bool

Response 200:
  {
    "video_url": "https://.../job-<id>.mp4",
    "duration_s": 12.34,
    "cost_usd": 0.61,
    "job_id": "..."
  }
```

`contenido` ya hace la descarga del MP4 desde `video_url`.

**Config**:

```toml
[visual.live_avatar]
enabled = true
backend = "remote_http"
remote_endpoint = "https://my-worker.runpod.io/generate"
remote_api_key = ""  # via env LIVE_AVATAR_REMOTE_API_KEY
cost_per_video_second_usd = 0.05
```

O via env:

```bash
export LIVE_AVATAR_ENABLED=true
export LIVE_AVATAR_BACKEND=remote_http
export LIVE_AVATAR_REMOTE_ENDPOINT=https://my-worker.runpod.io/generate
export LIVE_AVATAR_REMOTE_API_KEY=sk-...
```

### Opción B — `local_cli` (dev / batch interno)

Clona el repo y descarga los checkpoints. El submodule `.external/LiveAvatar`
ya está clonado en tu working tree.

```bash
cd .external/LiveAvatar

# Crear env conda
conda create -n liveavatar python=3.10 -y
conda activate liveavatar

# CUDA + torch + flash-attn
conda install nvidia/label/cuda-12.4.1::cuda -y
pip install torch==2.8.0 torchvision==0.23.0 \
    --index-url https://download.pytorch.org/whl/cu128
pip install flash-attn==2.8.3 --no-build-isolation  # o flash_attn_3 si tienes Hopper

# Resto
pip install -r requirements.txt
apt-get install -y ffmpeg

# Descargar weights (~30 GB)
pip install "huggingface_hub[cli]"
huggingface-cli download Wan-AI/Wan2.2-S2V-14B \
    --local-dir ./ckpt/Wan2.2-S2V-14B
huggingface-cli download Quark-Vision/Live-Avatar \
    --local-dir ./ckpt/LiveAvatar
```

**Config**:

```toml
[visual.live_avatar]
enabled = true
backend = "local_cli"
cli_repo_path = "./.external/LiveAvatar"
cli_ckpt_dir = "./.external/LiveAvatar/ckpt/Wan2.2-S2V-14B"
cli_lora_path = "Quark-Vision/Live-Avatar"
cli_num_gpus_dit = 1
fp8 = true                   # 48GB VRAM (con FP8); 80GB sin FP8
enable_compile = true        # primer run lento, runs siguientes 2-3× más rápido
```

> **Importante**: `contenido` invoca `torchrun` con el conda env esperado en PATH.
> Asegúrate de activar el env conda en el shell donde corres el pipeline, o setea
> `cli_python = "/path/to/conda/envs/liveavatar/bin/python"` y `torchrun` análogo.

---

## Uso desde el pipeline long-form

```python
from core.long_form.director import Director
from shared.schemas import LongFormIntent

director = Director()

# 1. Plan (cheap, ~$1-3)
script, job = await director.plan(
    source_text="A 10-minute course on the basics of curiosity-driven learning.",
    intent=LongFormIntent.TALKING_HEAD,  # explícito o lo detecta el router
    target_minutes=10,
)

# Revisar/editar script.json a mano si se quiere

# 2. Produce (costoso, ~$15-30 con remote_http)
job = await director.produce(
    job,
    script,
    portrait_path=Path("editorial/anchors/maria.jpg"),
    tts_engine="edge",     # gratis
    tts_voice="es-MX-DaliaNeural",
)

print(job.final_video_path)  # /storage/long_form/.../<job_id>_talking_head.mp4
```

### Detección automática de intent

El intent router LLM detecta TALKING_HEAD a partir del texto base:

```python
from core.long_form.script_planner import detect_intent

intent = await detect_intent(
    "Quiero un video donde un profesor explica directo a cámara cómo funciona la fotosíntesis"
)
assert intent is LongFormIntent.TALKING_HEAD
```

Keywords que disparan TALKING_HEAD en el router:

- "explica", "explainer", "course", "lecture", "tutorial"
- "anchor", "presenter", "teacher", "host"
- "direct address", "to camera", "first-person"
- "vlog", "talking head", "podcast video"

---

## Modelo de costos

Por defecto el cost tracker stampa `last_cost_usd` con:

| Backend | USD/segundo video output | Caso típico 10 min |
|---|---|---|
| `remote_http` (RunPod H100 serverless) | $0.050 | $30 |
| `local_cli` (server propio) | $0.005 amortizado | $3 (electricidad) |

Override en config (`cost_per_video_second_usd`) o por env
(`LIVE_AVATAR_COST_PER_VIDEO_SECOND_USD`).

Tu provider real puede variar — checa pricing en RunPod / Lambda Labs según
deal del momento.

---

## Hardware requirements

| Modo | GPU | VRAM | Wall-clock |
|---|---|---|---|
| `local_cli` single-GPU FP8 | 1× A6000 Ada / RTX 6000 Ada / H100 / H200 / A100 | 48 GB | ~2× tiempo real (10 min video → ~20 min compute) |
| `local_cli` single-GPU sin FP8 | 1× H100 / A100 | 80 GB | ~2× tiempo real |
| `local_cli` multi-GPU TPP | 5× H800 | 5× 80 GB | real-time (45 FPS streaming) |

> **Nota**: el primer run con `enable_compile=true` tarda 5-10 min adicionales
> compilando kernels. Los runs subsiguientes son 2-3× más rápidos. Para batch
> jobs largos vale la pena el costo inicial.

---

## Limitaciones conocidas

1. **No hay training code todavía** (en TODO list del repo upstream) — no se puede entrenar un LoRA custom por brand. Todos los outputs usan el LoRA Quark base.
2. **Aspect ratio fijado por la imagen input** — para 9:16 (Reels/Shorts) pasar imagen 9:16; para 16:9 (YouTube long), imagen 16:9.
3. **No hay control de cinematografía** (plano fijo, sin DoP presets). Es el trade-off por tener lip-sync.
4. **El prompt textual es contexto visual, NO diálogo** — el diálogo viene del audio. El prompt describe atmósfera (lighting, background, mood).
5. **Sin TTS integrado upstream**: `contenido` pasa el WAV de Edge/Gemini/Azure como input (input externo a LiveAvatar).

---

## Troubleshooting

### `LiveAvatarBackendUnavailableError: local_cli backend no listo`

- Verifica que `cli_repo_path` apunta al repo clonado
- Verifica que `cli_ckpt_dir` contiene los safetensors descargados de `Wan-AI/Wan2.2-S2V-14B`
- Verifica que `torchrun` y el conda env están en PATH del proceso que corre `contenido`

### OOM en single-GPU 80GB

- Setea `fp8 = true` (baja a 48GB)
- Setea `enable_online_decode = false` (`true` para videos extra-largos pero costa más memoria)
- Baja el `size` (p.ej. `"512*288"` en lugar de `"704*384"`)

### Inferencia muy lenta

- Setea `enable_compile = true` (lento la primera, rápido las siguientes)
- Considera multi-GPU (5×H800 → real-time 45 FPS)
- Para batch jobs, mantén el proceso vivo entre jobs (no spin-up por job)

### Audio out of sync

- Verifica que el WAV tiene sample rate 16 kHz (resampling se hace en el backend; si no, conviértelo manualmente con `ffmpeg -i in.wav -ar 16000 out.wav`)
- Verifica que `infer_frames=48` (default; otros valores pueden romper el alineamiento)

### `core.tts no disponible`

- Talking-head requiere TTS para lip-sync. `pip install -e .` o `uv sync` para asegurar deps base.

---

## Roadmap

- [ ] **POC validation con GPU rentado** — confirmar costo/calidad sobre 1 video de 30s antes de comprometer producción
- [ ] **Wrapper RunPod serverless oficial** — Dockerfile + handler.py listo para deploy
- [ ] **Multi-character por scene** — LiveAvatar soporta nativo; wirearlo cuando `script.scenes[*].characters` tiene 2+ entries
- [ ] **TTS+LiveAvatar pipeline batched** — actualmente generamos audio paralelo y video serial; eventualmente paralelizar audio→video cuando la GPU soporte
- [ ] **Voice cloning** — combinar con SiliconFlow CosyVoice2 (en el roadmap de `core/tts/`)
- [ ] **Reentrenar LoRA propio** — esperar a que upstream libere training code (TODO list LiveAvatar)
