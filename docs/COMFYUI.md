# ComfyUI Integration

`contenido` integra **ComfyUI** como provider visual de primera clase para tres casos donde APIs templated (Veo / Higgsfield DoP) NO alcanzan:

1. **Brand identity vía LoRA**: 30-50 referencias entrenadas hacen que todos los reels parezcan tuyos
2. **ControlNet (pose/depth/canny)**: layout strict (logo en esquina fija, sujeto centrado)
3. **IPAdapter style transfer**: reference image guía estilo sin describirlo en prompt
4. **AnimateDiff + LoRA**: video t2v con motion control y brand identity simultáneos
5. **Multi-tenant**: cada cliente trae su propia LoRA + workflow (escalable horizontalmente)

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│  contenido pipeline (DAG 18 reasoners + render)                  │
└─────────────────────────┬────────────────────────────────────────┘
                          │ beat → BeatVisual
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  core/visual/generation/orchestrator.py — 3-tier fallback        │
│                                                                  │
│  Tier 1 first frame:  ComfyUI → Soul → Gemini → placeholder      │
│  Tier 2 motion:       DoP → Veo → ken-burns                      │
│  Tier 3 effects:      Higgsfield Effects (post-step opcional)    │
└─────────────────────────┬────────────────────────────────────────┘
                          │ ComfyUIGenerator.generate()
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  core/visual/generation/comfy.py                                 │
│  1. Resolver tenant (BeatVisual.soul_id → cfg.default_tenant_id) │
│  2. Editorial registry > config TOML > defaults                  │
│  3. parameterize_workflow(spec, params)                          │
│  4. ComfyClient.execute_workflow() (REST + WS o polling)         │
│  5. Descarga via GET /view, normaliza PNG→JPG                    │
└─────────────────────────┬────────────────────────────────────────┘
                          │ HTTP + WebSocket
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  ComfyUI server (local 127.0.0.1:8188 o managed)                 │
│  POST /prompt → workflow JSON completo (no presets)              │
│  WS /ws?clientId=<UUID> → executing/progress/executed events     │
│  GET /history/{id} → outputs (filenames)                         │
│  GET /view?filename → bytes binarios                             │
└──────────────────────────────────────────────────────────────────┘
```

## Setup

### Opción A: Self-hosted local (GPU 24GB+)

```bash
# 1. Instala comfy-cli + ComfyUI (15-30 min)
pip install comfy-cli
uv run python -m apps.cli.main comfy install

# 2. Arranca el server en background
uv run python -m apps.cli.main comfy launch --background

# 3. Habilita el provider
echo "COMFYUI_ENABLED=true" >> .env

# 4. Verifica
uv run python -m apps.cli.main comfy status
```

### Opción B: Managed (ViewComfy / RunComfy / propio)

```bash
# .env
COMFYUI_ENABLED=true
COMFYUI_SERVER_URL=https://api.viewcomfy.com/v1
COMFYUI_AUTH_HEADER="Bearer xyz123"
```

No requiere comfy-cli. Solo el cliente HTTP/WS.

### Opción C: Solo lectura (no quieres ComfyUI todavía)

`COMFYUI_ENABLED=false` (default). El orchestrator salta el tier ComfyUI completamente, fallback inmediato a Soul/Gemini. Ningún breaking change.

## Workflows

Los workflows viven en `workflows/*.json` en **formato API** (no GUI). El registry está en `workflows/index.json`.

### Workflows incluidos por default

| ID | Kind | Uso |
|---|---|---|
| `flux_basic_9x16` | basic_t2i | Flux dev txt2img vertical (sin LoRA) |
| `flux_lora_brand` | lora_t2i | Flux + brand LoRA (multi-tenant) |
| `sdxl_ipadapter_style` | ipadapter_reference | SDXL + IPAdapter (style transfer) |

### Inspeccionar

```bash
uv run python -m apps.cli.main comfy workflow list
uv run python -m apps.cli.main comfy workflow show flux_lora_brand
```

### Agregar un workflow nuevo

1. Exporta desde la GUI: **Settings → Enable Dev mode** → **Save (API Format)**
2. Copia el JSON a `workflows/mi_workflow.json`
3. Agrega entry en `workflows/index.json` con el ComfyParameterMap:

```json
{
  "id": "mi_workflow",
  "name": "Mi workflow custom",
  "kind": "lora_t2i",
  "output_type": "image",
  "json_path": "mi_workflow.json",
  "parameters": {
    "prompt": "6-inputs-text",
    "seed": "3-inputs-seed",
    "lora_name": "10-inputs-lora_name",
    "lora_strength": "10-inputs-strength_model"
  },
  "output_nodes": ["9"],
  "required_loras": ["mi_lora.safetensors"],
  "estimated_seconds": 22.0
}
```

El loader detecta el nuevo entry sin reiniciar nada (cache invalidable con `reload_registry()`).

## Multi-tenant

Cada **tenant** mapea a su propio workflow + LoRA + style.

### Declarar tenants en `editorial/brand-visual.json` (preferido)

```json
{
  "tenants": [
    {
      "tenant_id": "ruteo",
      "label": "Ruteo (corredor industrial Veracruz)",
      "primary_workflow_id": "flux_lora_brand",
      "lora_name": "ruteo_brand_v1.safetensors",
      "lora_strength": 0.88,
      "style_suffix": "cinematic still, warm natural light, 35mm film, central Veracruz streets, real Mexican people, golden hour"
    },
    {
      "tenant_id": "tu_canal",
      "label": "Tu canal de ciencia",
      "primary_workflow_id": "sdxl_ipadapter_style",
      "lora_name": "ciencia_brand_v2.safetensors",
      "lora_strength": 0.9,
      "style_suffix": "documentary photograph, research lab aesthetic, sharp focus, neutral lighting"
    }
  ]
}
```

### O en `config.toml` (alternativo)

```toml
[visual.comfyui.tenants.ruteo]
primary_workflow_id = "flux_lora_brand"
lora_name = "ruteo_brand_v1.safetensors"
lora_strength = 0.88
style_suffix = "cinematic still, warm natural light"
```

**Precedencia**: `editorial/brand-visual.json` > `config.toml` > defaults.

### Asignar tenant a un beat

El `BeatVisual.soul_id` se reutiliza como `tenant_id` por convención. En cualquier punto donde construyas un `BeatVisual`:

```python
visual = BeatVisual(
    image_prompt="a person at the bus stop",
    motion_hint=MotionHint.STATIC,
    visual_anchor="bus stop",
    soul_id="ruteo",  # ← este es el tenant que usará ComfyUI
)
```

Si `soul_id` está vacío, usa `cfg.visual.comfyui.default_tenant_id`.

## LoRA training (fuera de este repo)

Este pipeline NO entrena LoRAs — eso requiere infraestructura distinta. Recomendaciones:

| Tool | Cuándo | Notas |
|---|---|---|
| **kohya_ss** | LoRA de estilo o personaje, GUI amigable | https://github.com/bmaltais/kohya_ss |
| **ai-toolkit** | Flux LoRA training optimizado | https://github.com/ostris/ai-toolkit |
| **Replicate trainer** | Sin GPU local, pay-per-train (~$5-15) | https://replicate.com/lucataco/ai-toolkit |
| **CivitAI online trainer** | Mismo que arriba, UI simple | https://civitai.com/training |

### Dataset mínimo

- **30-50 imágenes** de tu marca/estilo/persona
- Resolución 1024×1024 mínimo
- Variedad: ángulos, iluminación, ropa, fondos
- Captioning automático con BLIP o GPT-4V (lo hace kohya)

### Cuando tengas el `.safetensors`

```bash
# Descargar al server ComfyUI
uv run python -m apps.cli.main comfy lora download \
    --url https://huggingface.co/usuario/mi_lora/resolve/main/lora.safetensors \
    --filename mi_brand_v1.safetensors

# Verificar
uv run python -m apps.cli.main comfy lora list

# Setear en editorial/brand-visual.json:
#   "lora_name": "mi_brand_v1.safetensors"
```

## Cost & Performance

| Setup | Costo mensual | Calidad | Producción real |
|---|---|---|---|
| Self-hosted RTX 4090 | $0 (post-$2k hardware) | Top-tier | Sí, single-user |
| RunPod A100 on-demand | $0.40-0.80/h | Top-tier | Sí, batch (orquesta lifecycle) |
| ViewComfy managed | $50-200/mes baseline + per-gen | Top-tier | Sí, listo para prod |
| Modal serverless | Por-segundo billing | Top-tier | Sí, idle = $0 |

### Latencia esperada (cold start excluido)

| Workflow | Hardware | Latencia |
|---|---|---|
| flux_basic_9x16 (Flux dev fp16, 25 steps) | RTX 4090 | 18-22s |
| flux_lora_brand (idem + LoRA) | RTX 4090 | 22-28s |
| sdxl_ipadapter_style (SDXL 30 steps) | RTX 4090 | 28-35s |
| AnimateDiff 16 frames | A100 80GB | 60-90s |

### VRAM mínimo

| Workflow | VRAM |
|---|---|
| flux_basic / flux_lora | 24 GB (Flux dev requires) |
| sdxl_ipadapter | 12 GB |
| AnimateDiff | 16-24 GB |

## Selector behavior

`core/visual/selector.py:_ia_source()` decide qué provider usar para first frames:

1. **ComfyUI** si:
   - `cfg.visual.comfyui.enabled = true`
   - `cfg.visual.comfyui.prefer_for_brand_frames = true`
   - El tenant default tiene `lora_name` configurada
2. **Higgsfield Soul** si está completamente configurado (credentials + soul_default_reference_id)
3. **Gemini Image** como fallback genérico

El orchestrator también respeta el orden por beat — si ComfyUI falla (server down, OOM), cae automáticamente a Soul/Gemini sin abortar el reel.

## Soft fail policy

- Server no responde → `VisualGenerationError` → orchestrator usa fallback
- Workflow falla (node_errors, OOM) → `ComfyValidationError` → fallback
- Timeout → `ComfyTimeoutError` → fallback
- Output vacío → `VisualGenerationError` → fallback

**Nunca crashea el pipeline**. El reel se genera con calidad menor (Gemini Image en vez de tu LoRA) en vez de fallar.

## Comandos CLI

```bash
# Server lifecycle
contenido comfy status                 # health check
contenido comfy install                # primera vez
contenido comfy launch --background    # arranca server

# Workflows
contenido comfy workflow list          # lista registrados
contenido comfy workflow show <id>     # detalle + parámetros mapeados
contenido comfy test <workflow_id> --prompt "test"   # E2E test con un beat

# Modelos
contenido comfy models checkpoints     # vía API (no cli)
contenido comfy models loras
contenido comfy lora list              # vía comfy-cli
contenido comfy lora download --url ... --filename ...
```

## Trade-offs honestos

### Cuándo usar ComfyUI

- **SÍ**: tienes (o vas a entrenar) una LoRA de marca → ComfyUI es la única forma de aplicarla
- **SÍ**: necesitas ControlNet/IPAdapter (layout strict, style transfer fiel)
- **SÍ**: vas multi-tenant (cada cliente con su LoRA)
- **SÍ**: workflows compuestos (upscale + face restore en chain)

### Cuándo NO usar ComfyUI

- **NO**: generación genérica "una imagen bonita" — Gemini Image cubre con menos overhead
- **NO**: no tienes GPU y no quieres pagar managed
- **NO**: solo necesitas i2v simple — Veo/DoP cubren sin LoRA

## Próximos pasos sugeridos

- Workflows adicionales: `flux_controlnet_pose`, `animatediff_lora`, `inpaint_brand`
- Auto-fallback OOM: cuando el server devuelve OOM, llamar `POST /free` y reintentar
- Workflow versioning: incluir hash del JSON en el cost_breakdown para tracking de qué versión generó qué reel
- Multi-server load balancing: cuando tengas N tenants concurrentes, distribuir entre múltiples GPUs
