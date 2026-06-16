# workflows/

Workflows ComfyUI en **formato API** (no GUI). Cada `.json` aquí es un grafo
completo listo para `POST /prompt`.

## Cómo exportar desde la ComfyUI GUI

1. Abre tu workflow en `http://127.0.0.1:8188`
2. **Settings → Enable Dev mode Options**
3. **Save (API Format)** en el menú lateral
4. El JSON descargado es el que va aquí

## Registro

Cada workflow se registra en `index.json`. El registry NO descubre automáticamente
archivos sueltos — necesitas declararlos:

```json
{
  "workflows": [
    {
      "id": "flux_lora_brand",
      "name": "Flux + Brand LoRA (vertical 9:16)",
      "kind": "lora_t2i",
      "json_path": "flux_lora_brand.json",
      "output_type": "image",
      "parameters": {
        "prompt": "6-inputs-text",
        "seed": "3-inputs-seed",
        "lora_name": "10-inputs-lora_name",
        "lora_strength": "10-inputs-strength_model"
      },
      "output_nodes": ["9"],
      "required_loras": ["ruteo_brand_v1.safetensors"],
      "estimated_seconds": 20.0,
      "estimated_vram_gb": 24.0
    }
  ]
}
```

## Workflows incluidos

| ID | Tipo | Uso |
|---|---|---|
| `flux_basic_9x16` | basic_t2i | Flux txt2img vertical (sin LoRA) |
| `flux_lora_brand` | lora_t2i | Flux + brand LoRA (multi-tenant) |
| `sdxl_ipadapter_style` | ipadapter_reference | SDXL + IPAdapter (style transfer) |

## Convenciones

- **Resolución vertical default**: 720×1280 (consistente con `canvas_w/h` del config)
- **Output node = SaveImage**: id `"9"` por convención
- **LoRA node**: id `"10"` por convención
- **Sampler node**: id `"3"` por convención

Estos node_ids son arbitrarios — el spec en `index.json` los mapea a nombres semánticos.

## Custom nodes requeridos

| Workflow | Custom nodes |
|---|---|
| `flux_lora_brand` | ninguno (built-in) |
| `sdxl_ipadapter_style` | `ComfyUI_IPAdapter_plus` |
| `animatediff_lora` | `ComfyUI-AnimateDiff-Evolved`, `ComfyUI-VideoHelperSuite` |
| `flux_controlnet_pose` | `comfyui_controlnet_aux` |

Instala via:
```bash
contenido comfy node install ComfyUI_IPAdapter_plus
```
